"""
提取层：Agent 循环结束后，滚动采集多视口截图 → 单次多图 Qwen-VL 提取 → 去重映射 Job。

与旧 `_parse_with_fallback` 的区别：这是主路径而非降级，且用多图单次调用覆盖整个结果列表。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from datetime import datetime

from src.agent.perceive import capture_screenshot
from src.config import AGENT_EXTRACT_TIMEOUT, DETAIL_JD_TEXT_LIMIT
from src.models import Job

logger = logging.getLogger(__name__)

MAX_VIEWPORTS = 4              # 最多采集几个视口的截图
SCROLL_VIEWPORT_RATIO = 0.9    # 每次滚动 90% 视口高度，留重叠防漏

# 占位符 URL（LLM 幻觉产物）：如 xxxxx.html / ddddd.html / 123456789.html 的假 ID
_PLACEHOLDER_URL_RE = re.compile(
    r"(x{4,}|y{4,}|z{4,}|a{4,}|b{4,}|c{4,}|d{4,}|tbd|placeholder|example\.com)"
)


def sanitize_url(url: str) -> str:
    """丢弃占位符/幻觉 URL，只保留看起来真实的链接。

    老数据里 DeepSeek 会编造 xxxxx.html、123456789.html 这类假详情链接，
    点进去不是当前岗位的真实页面。这里兜底净化，宁缺毋滥。
    """
    u = (url or "").strip()
    if not u:
        return ""
    if _PLACEHOLDER_URL_RE.search(u):
        return ""
    # 链接必须是 http(s)
    if not u.startswith(("http://", "https://")):
        return ""
    # 职友集搜索页 URL（/jobs?）是搜索列表而非职位详情页，不能当详情回填
    # （DeepSeek 会从 OCR 文本里误提这类搜索链接当 url；与 generator.safe_url 同源兜底）
    if "jobui.com/jobs" in u:
        return ""
    return u


async def extract_jobs_from_page(
    page,
    platform: str,
    round_label: str = "",
    max_viewports: int = MAX_VIEWPORTS,
) -> list[Job]:
    """滚动采集多视口截图，多图一次提取，去重后映射为 Job 列表。"""
    # 有专属 DOM 提取器的平台：结构化直取 title/company/url，避免 OCR 标题匹配错配
    extractor = _DOM_EXTRACTORS.get(platform)
    if extractor:
        dom_jobs = await extractor(page)
        if dom_jobs:
            jobs = [_dict_to_job(d, platform, round_label) for d in dom_jobs]
            return _dedup_jobs(jobs)
        # 鱼泡登录墙/风控页：不回退 OCR（会把"微信扫码 快捷登录"解析成垃圾岗位）
        if platform == "鱼泡直聘":
            try:
                low = (page.url or "").lower()
                if "/web/login/" in low or "/safe/verify/" in low:
                    logger.warning("[agent] 鱼泡登录墙/风控页，跳过提取")
                    return []
            except Exception:
                pass
        logger.warning(f"[agent] {platform} DOM 提取为空，回退通用 OCR 链路")

    screenshots = await _collect_viewport_screenshots(page, max_viewports)
    if not screenshots:
        logger.warning("[agent] 提取阶段未采到截图")
        return []

    raw_jobs = await _extract_with_vision(screenshots, platform)
    if not raw_jobs:
        logger.warning("[agent] 视觉提取返回空")
        return []

    # 回填真实岗位详情 URL（DOM 锚点/数据属性与标题匹配），保证"查看详情"是当前岗位真实页
    card_links = await _collect_card_urls(page, platform)
    if card_links:
        raw_jobs = _merge_card_urls(raw_jobs, card_links)

    jobs = [_dict_to_job(d, platform, round_label) for d in raw_jobs]
    return _dedup_jobs(jobs)


async def _collect_viewport_screenshots(page, max_viewports: int) -> list[bytes]:
    """回到顶部，逐视口滚动并截图。"""
    try:
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.wait_for_timeout(800)
    except Exception:
        pass

    vp = page.viewport_size
    step_y = max(int((vp["height"] if vp else 800) * SCROLL_VIEWPORT_RATIO), 300)

    shots: list[bytes] = []
    for _ in range(max_viewports):
        try:
            shot = await capture_screenshot(page)
        except Exception as e:
            logger.warning(f"[agent] 截图失败: {e}")
            break
        shots.append(shot)
        try:
            await page.evaluate(f"() => window.scrollBy(0, {step_y})")
            await page.wait_for_timeout(random.uniform(400, 800))
        except Exception:
            break

    try:
        await page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass
    return shots


async def _extract_with_vision(screenshots: list[bytes], platform: str) -> list[dict]:
    """OCR 优先提取链：本地 RapidOCR → 云端 OCR 兜底 → DeepSeek 结构化 → 规则解析 → 视觉兜底。

    用户约束：OCR 文字识别准确率极高，截图文字提取应以 OCR 为主，避免占用多模态
    视觉大模型的额度/延时。视觉 API 只做最后兜底。
    - 读字：本地 RapidOCR（离线免费）优先；空/失败 → SiliconFlow PaddleOCR-VL-1.5（免费）
    - 结构化：便宜文本 LLM（DeepSeek）→ 规则解析离线兜底
    - 视觉兜底：ModelScope（免费 2000/天）→ GLM-5.3-Flash（智谱 Coding Plan）。
      （用户 2026-09-04：SiliconFlow-VL/Gemini/Qwen-VL 已退出视觉链，只留两级。）
    """
    # ① 读字：本地 RapidOCR 优先，空/失败补云端 OCR（免费）
    ocr_text = await _ocr_text(screenshots)
    if not ocr_text:
        ocr_text = await _cloud_ocr_text(screenshots)

    if ocr_text:
        # ② DeepSeek 文本结构化（分毫成本，无视觉额度问题）
        try:
            from src.llm.deepseek import parse_job_from_screenshot
            raw = await asyncio.wait_for(
                parse_job_from_screenshot(ocr_text, platform),
                timeout=AGENT_EXTRACT_TIMEOUT,
            )
            if raw:
                logger.info(f"[agent] OCR+DeepSeek 结构化出 {len(raw)} 条")
                return _dedup_raw(raw)
        except Exception as e:
            logger.warning(f"[agent] DeepSeek 结构化失败: {e}")

        # ②' 规则解析离线兜底（DeepSeek 失败/不可用时仍有产出）
        rule_jobs = await _extract_with_ocr_text(ocr_text)
        if rule_jobs:
            return rule_jobs

    # ③ 视觉兜底：OCR 无文本或两级解析全空时才动用多模态 API
    raw: list[dict] = []
    for name, mod in (
        ("ModelScope", "src.llm.modelscope"),
        ("GLM", "src.llm.glm"),
    ):
        if raw:
            break
        try:
            from importlib import import_module
            extractor = getattr(import_module(mod), "extract_jobs_from_screenshot")
            raw = await extractor(screenshots, platform)
            if raw:
                logger.info(f"[agent] {name} 视觉提取出 {len(raw)} 条")
        except Exception as e:
            logger.warning(f"[agent] {name} 提取失败: {e}")

    return raw


async def _ocr_text(screenshots: list[bytes]) -> str:
    """RapidOCR 读字：截图 → 文本（本地离线）。"""
    try:
        from src.ocr.rapid import extract_text_from_screenshot
        text = await extract_text_from_screenshot(screenshots)
        if not text:
            logger.warning("[agent] OCR 未识别出文本")
            return ""
        return text
    except Exception as e:
        logger.warning(f"[agent] OCR 读取失败: {e}")
        return ""


async def _cloud_ocr_text(screenshots: list[bytes]) -> str:
    """云端 OCR 兜底：本地 RapidOCR 空/失败时用 SiliconFlow PaddleOCR-VL-1.5（免费）。"""
    try:
        from src.llm.siliconflow import ocr_screenshot_text
        text = await asyncio.wait_for(
            ocr_screenshot_text(screenshots),
            timeout=max(AGENT_EXTRACT_TIMEOUT, 150),
        )
        if text:
            logger.info("[agent] 云端 OCR 读字成功")
        return text
    except Exception as e:
        logger.warning(f"[agent] 云端 OCR 兜底失败: {e}")
        return ""


async def _extract_with_ocr_text(ocr_text: str) -> list[dict]:
    """规则解析离线兜底：OCR 文本 → 岗位结构。"""
    try:
        from src.ocr.parse_jobs import parse_jobs_from_text
        jobs = parse_jobs_from_text(ocr_text)
        if jobs:
            logger.info(f"[agent] OCR 规则解析出 {len(jobs)} 条岗位")
        return jobs
    except Exception as e:
        logger.warning(f"[agent] OCR 规则解析失败: {e}")
        return []


def _dedup_raw(raw: list[dict]) -> list[dict]:
    """LLM 输出可能含重复/空条目，按 标题+公司 去重过滤（同标题不同公司不吞）。"""
    seen: set[str] = set()
    result: list[dict] = []
    for d in raw:
        title = str(d.get("title", "") or "").strip()
        company = str(d.get("company", "") or "").strip()
        if not title:
            continue
        key = f"{title}|{company}".lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(d)
    return result


async def _collect_card_urls(page, platform: str = "", max_links: int = 600) -> list[dict]:
    """从 DOM 采集岗位卡片链接：{text: 岗位标题, href: 真实详情URL}。

    两种通道：
    1. 平台专属：SPA 岗位卡没有 <a> 链接时，从数据属性提取 jobId 构造详情 URL
       （如 51Job 的 sensorsdata 属性 → jobs.51job.com/chengdu/{jobId}.html）。
    2. 通用锚点：岗位标题直接作为 <a> 文本的平台，锚点文本与标题匹配即得真实详情页。
    """
    if platform == "51Job":
        return await _collect_job51_urls(page)
    if platform == "职友集":
        return await _collect_jobui_urls(page)

    try:
        data = await page.evaluate(f"""() => {{
            const out = [];
            const anchors = document.querySelectorAll('a[href]');
            // 企业后缀词，用于在卡片容器里识别公司名文本
            const corpRe = /(有限公司|股份有限公司|有限责任|集团|科技|技术|实业|控股|研究院|研究所|设计院|大学|学院|事务所|合伙企业|分公司)/;
            for (const a of anchors) {{
                const href = a.href || '';
                if (!href || href.startsWith('javascript') || href === '#') continue;
                const text = (a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!text || text.length > 40) continue;
                // 向上找卡片容器取公司名（同标题多公司时按公司精确匹配各自的详情URL，根治错配）
                let company = '';
                let p = a.parentElement;
                for (let i = 0; i < 6 && p && !company; i++) {{
                    // 优先：容器内指向公司主页的锚点（文本即公司名）
                    const coA = p.querySelector('a[href*="/companydetail"], a[href*="/company/"], a[href*="/qiye"], a[href*="/cmp/"], a[href*="/firm/"]');
                    if (coA) {{
                        const t = (coA.innerText || '').trim();
                        if (t && t.length <= 40) company = t;
                    }}
                    p = p.parentElement;
                }}
                // 兜底：最近祖先里找叶子元素文本含企业后缀且长度合理
                if (!company) {{
                    let q = a.parentElement;
                    for (let i = 0; i < 5 && q && !company; i++) {{
                        for (const el of q.querySelectorAll('*')) {{
                            if (el.children.length > 0) continue;
                            const t = (el.innerText || '').trim();
                            if (t && t.length >= 4 && t.length <= 40 && corpRe.test(t)) {{ company = t; break; }}
                        }}
                        q = q.parentElement;
                    }}
                }}
                out.push({{ text, href, company }});
            }}
            return out.slice(0, {max_links});
        }}""")
    except Exception as e:
        logger.warning(f"[agent] DOM 链接采集失败: {e}")
        return []
    return data or []


async def _extract_yupao_from_dom(page) -> list[dict]:
    """鱼泡专属 DOM 提取：卡片结构稳定，直接读 DOM 而非 OCR。

    为什么必须特化（实测 2026-08-04）：
    1. 标题噪音——卡片里 .accbj 标签（职位类型/经验/学历，如"测试工程师""1-3年"）
       文本形态与标题相同，OCR+DeepSeek 会误判为独立岗位标题；
    2. URL 错配——通用锚点通道把详情页(/zhaogong/{jobId}/{slug}.html)与分类页
       (/zhaogong/a322c{code}/)混在一起，按标题文本匹配会回填分类页链接。
    DOM 结构：
      div.accba                    卡片容器
        a.accjh[href*="/zhaogong/"][href*=".html"]   详情链接
          div.accbc                标题
          span.accbh               薪资
        div.accca
          a[href*="/qiye/"]        公司
          span.accci               地点
    """
    try:
        data = await page.evaluate("""() => {
            const out = [];
            const cards = document.querySelectorAll('div.accba');
            for (const c of cards) {
                const a = c.querySelector('a.accjh[href*="/zhaogong/"][href*=".html"]');
                const title = c.querySelector('div.accbc');
                if (!a || !title) continue;
                const t = (title.innerText || title.textContent || '').trim();
                if (!t) continue;
                const salary = c.querySelector('span.accbh');
                const companyA = c.querySelector('div.accca a[href*="/qiye/"]');
                const location = c.querySelector('span.accci');
                out.push({
                    title: t,
                    salary_text: salary ? (salary.innerText || '').trim() : '',
                    company: companyA ? (companyA.innerText || '').trim() : '',
                    location: location ? (location.innerText || '').trim() : '',
                    url: a.href,
                });
            }
            return out;
        }""")
    except Exception as e:
        logger.warning(f"[agent] 鱼泡 DOM 提取失败: {e}")
        return []
    return data or []


async def _extract_zhilian_from_dom(page) -> list[dict]:
    """智联专属提取：优先 window.__INITIAL_STATE__ SSR JSON（不依赖 DOM 选择器，最稳），
    失败回退 DOM 卡片选择器（旧 sou.zhaopin.com + 新 www.zhaopin.com/sou/ 两套合并）。

    为什么必须特化：智联同标题多公司普遍（如"AI工程师"6 家），OCR+DeepSeek 链路靠
    标题文本回填 URL 会错配到别家；SSR JSON 自带 number/positionURL/company，
    title/company/url 三元组成组直取，根治错配。字段路径沿用旧 zhilian.py._parse_json 验证结果。
    """
    # ① 优先 SSR JSON（最稳，不依赖选择器）
    try:
        data = await page.evaluate("""() => {
            const s = window.__INITIAL_STATE__;
            if (!s) return [];
            const items = (s.search && s.search.list)
                       || (s.positionList && s.positionList.result) || [];
            const out = [];
            for (const it of items) {
                const title = it.name || it.jobName || '';
                if (!title) continue;
                out.push({
                    title,
                    company: (it.company && it.company.name) || it.companyName || '',
                    url: it.positionURL || it.shareUrl || '',
                    salary_text: it.salary60 || it.salary || '',
                    location: (it.city && it.city.display) || it.workCity || '',
                });
            }
            return out;
        }""")
        if data:
            return data
    except Exception as e:
        logger.warning(f"[agent] 智联 SSR JSON 提取失败: {e}")

    # ② 回退 DOM 卡片选择器（旧 sou + 新 www 两套合并）
    try:
        data = await page.evaluate("""() => {
            const out = [];
            const cards = document.querySelectorAll(
                '.joblist-box__item, .content_details_div, .jobinfo, .jobcard'
            );
            for (const c of cards) {
                const titleA = c.querySelector(
                    'a.joblist-box__item-title, span.jobName a, a.jobName, a.jobinfo__name, a.jobname'
                );
                if (!titleA) continue;
                const t = (titleA.innerText || titleA.textContent || '').trim();
                if (!t) continue;
                const companyEl = c.querySelector(
                    'a.company_title, span.company__title, a.companyName, a.jobinfo__company, .company-name'
                );
                const salaryEl = c.querySelector(
                    'span.salary, p.salary, span.salaryText, .jobinfo__salary'
                );
                const locEl = c.querySelector(
                    'span.work__city, span.joblist-box__item-desc span:first-child, p.info span:first-child, .jobinfo__other-info-item'
                );
                out.push({
                    title: t,
                    company: companyEl ? (companyEl.innerText || '').trim() : '',
                    url: titleA.href || '',
                    salary_text: salaryEl ? (salaryEl.innerText || '').trim() : '',
                    location: locEl ? (locEl.innerText || '').trim() : '',
                });
            }
            return out;
        }""")
    except Exception as e:
        logger.warning(f"[agent] 智联 DOM 提取失败: {e}")
        return []
    return data or []


async def _extract_liepin_from_dom(page) -> list[dict]:
    """猎聘专属 DOM 提取：卡片 .job-card-pc-container。

    注意：猎聘类名是 CSS-module 哈希（_40108xxx，每次部署会变），选择器只锚定
    稳定钩子：容器类名 + data-nick 属性 + 结构位置，不碰哈希类名。

    为什么必须特化：猎聘同标题多公司普遍（实测"AI人工智能算法工程师"3 家并排），
    OCR+标题回填会把 A 公司岗位错配 B 公司 URL；DOM 直取 title/company/url 成组根治。
    实测（2026-08-08 Playwright）：42 卡片 company/url/salary 0 缺失、URL 0 重复。
    DOM 结构：
      div.job-card-pc-container               卡片容器
        a[data-nick="job-detail-job-info"]    标题锚点+详情链接（href 带追踪参数，截 ? 取净 URL）
          div.ellipsis-1 第1个                 标题
          div.ellipsis-1 第2个                 地点（成都-高新区）
          span（薪资格式文本）                 薪资（薪资面议/15-25k/15-25k·14薪）
        div[data-nick="job-detail-company-info"]
          span.ellipsis-1 第1个                公司名
    """
    try:
        data = await page.evaluate("""() => {
            const out = [];
            for (const c of document.querySelectorAll('.job-card-pc-container')) {
                const a = c.querySelector('a[data-nick="job-detail-job-info"]');
                if (!a) continue;
                const ellipses = [...a.querySelectorAll('.ellipsis-1')]
                    .map(e => (e.innerText || '').trim()).filter(Boolean);
                const title = ellipses[0] || '';
                if (!title) continue;
                // 薪资：锚点内匹配薪资格式的 span（薪资面议 / 15-25k / 15-25k·14薪 / 10-20万）
                const salarySpan = [...a.querySelectorAll('span')].find(s =>
                    /面议|\\d+\\s*[-~]\\s*\\d+\\s*[kK]|\\d+\\s*[-~]\\s*\\d+\\s*万/.test((s.textContent || '').trim()));
                const coBox = c.querySelector('[data-nick="job-detail-company-info"]');
                const coEl = coBox && coBox.querySelector('span.ellipsis-1');
                out.push({
                    title,
                    company: coEl ? (coEl.innerText || '').trim() : '',
                    url: (a.href || '').split('?')[0],  // 去追踪参数，净详情 URL 利去重
                    salary_text: salarySpan ? (salarySpan.textContent || '').trim() : '',
                    location: ellipses[1] || '',
                });
            }
            return out;
        }""")
    except Exception as e:
        logger.warning(f"[agent] 猎聘 DOM 提取失败: {e}")
        return []
    return data or []


async def _extract_chinahr_from_dom(page) -> list[dict]:
    """中华英才（新华英才）专属 DOM 提取：卡片 .detail-card，类名为语义命名（Vue scoped），稳定。

    为什么特化：同标题多条目普遍（实测"气象学"3 条同公司不同地点/编制），
    OCR+标题回填无法区分；DOM 直取三元组根治。
    实测（2026-08-08 Playwright）：13 卡片 company/url 0 缺失。
    DOM 结构：
      div.detail-card                          卡片容器
        a.detail-card_left[href^="/detail/"]   详情链接（a.href 自动转绝对 URL）
          span.detail-card_left-title-name     标题
          p.detail-card_left-salary            薪资（面议/5k-10k元/月）
          div.detail-card_left-info span       [学历, 经验, 地点, 招N人]（≥4 个时第 3 个是地点）
        div.detail-card_right-compony span.name   公司名（官方类名拼写就是 compony，勿"修正"）
    """
    try:
        data = await page.evaluate("""() => {
            const out = [];
            for (const c of document.querySelectorAll('.detail-card')) {
                const a = c.querySelector('a.detail-card_left');
                if (!a) continue;
                const titleEl = c.querySelector('.detail-card_left-title-name');
                const title = titleEl ? (titleEl.innerText || '').trim() : '';
                if (!title) continue;
                const salaryEl = c.querySelector('.detail-card_left-salary');
                const infoSpans = [...c.querySelectorAll('.detail-card_left-info span')]
                    .map(s => (s.innerText || '').trim());
                const coEl = c.querySelector('.detail-card_right-compony .name');
                out.push({
                    title,
                    company: coEl ? (coEl.innerText || '').trim() : '',
                    url: a.href || '',
                    salary_text: salaryEl ? (salaryEl.innerText || '').trim() : '',
                    location: infoSpans.length >= 4 ? infoSpans[2] : '',
                });
            }
            return out;
        }""")
    except Exception as e:
        logger.warning(f"[agent] 中华英才 DOM 提取失败: {e}")
        return []
    return data or []


async def _extract_wuba_from_dom(page) -> list[dict]:
    """58同城专属 DOM 提取：三套结构按页面实际渲染命中，直取 title/company/url/salary/location。

    为什么必须特化：58 同标题多公司普遍，OCR+标题回填会错配别家 URL；
    卡片容器内三元组成组直取，根治错配。

    ✅ 实页验证（2026-08-09 Playwright MCP，m.58.com/cd/job/）：30 卡片
    title/company/url/salary/location 0 缺失、URL 0 重复/0 非 58.com，详情页真实可达
    （m.58.com/cd/{cat}/{id}.shtml 含完整 JD：岗位职责/任职资格/薪资/公司）。
    三个页面形态（按渲染命中取一种）：
    ① wap 频道页（agent 主入口 m.58.com/cd/job/，无登录墙）：
       a.list-item-a.tcb_list_item_link   整卡链接（href 即详情 URL）
         .info-title     标题      .info-salary     薪资（3000-4500元/月）
         .company        公司      .local_quXianName 地点（青羊-万家湾）
       ⚠️ 地点是"区-商圈"无城市前缀（cd.58.com 即成都站，全站均为成都岗位），
       提取时统一补 "成都" 前缀，否则下游 _filter_city/_keep_chengdu 会把整站岗位误剔。
    ② wap sou 搜索页（m.58.com/cd/sou/?key=X，有关键词但无公司/薪资字段）：
       li > a > dl > dt.tit strong   标题；dd.attr span 末个为地点。
       该页无公司/薪资，字段留空（宁缺毋滥，不伪造），URL 真实可点进详情。
    ③ 桌面版回退（旧 scrapers/wuba.py 已验证选择器；桌面被登录墙拦截时命中不了）：
       li.job_item / div.job-list-item 卡片，a.t / span.name a 标题，等。
    """
    try:
        data = await page.evaluate("""() => {
            const out = [];
            // ① wap 频道页卡片（数据全，主路径）
            for (const c of document.querySelectorAll('a.list-item-a.tcb_list_item_link')) {
                const titleEl = c.querySelector('.info-title');
                if (!titleEl) continue;
                const t = (titleEl.innerText || titleEl.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!t) continue;
                const coEl = c.querySelector('.company');
                const salEl = c.querySelector('.info-salary');
                const locEl = c.querySelector('.local_quXianName');
                const loc = locEl ? (locEl.innerText || '').trim() : '';
                out.push({
                    title: t,
                    company: coEl ? (coEl.innerText || '').trim() : '',
                    url: c.href || '',
                    salary_text: salEl ? (salEl.innerText || '').trim() : '',
                    // cd.58.com 即成都站，全站岗位都在成都；wap 地点只有"区-商圈"无城市前缀，
                    // 补 "成都" 前缀让下游城市过滤能保留（崇州/新都/双流均属成都辖区）
                    location: loc ? '成都' + loc : '成都',
                });
            }
            if (out.length) return out;
            // ② wap sou 搜索页（有关键词，但卡片无公司/薪资字段，留空不伪造）
            for (const li of document.querySelectorAll('li')) {
                const a = li.querySelector('a[href]');
                const dt = li.querySelector('dt.tit strong');
                if (!a || !dt) continue;
                const t = (dt.innerText || '').trim().replace(/\\s+/g, ' ');
                if (!t) continue;
                const href = a.href || '';
                if (!href || href.startsWith('javascript')) continue;
                let location = '';
                for (const dd of li.querySelectorAll('dd.attr')) {
                    const spans = dd.querySelectorAll('span');
                    if (!spans.length) continue;
                    const lt = (spans[spans.length - 1].innerText || '').trim();
                    if (lt && lt.length <= 10) location = lt;
                }
                out.push({ title: t, company: '', url: href, salary_text: '', location });
            }
            if (out.length) return out;
            // ③ 桌面版回退（旧选择器；桌面搜索页对自动化 302 登录墙，正常命中不了）
            for (const c of document.querySelectorAll('li.job_item, div.job-list-item')) {
                const titleA = c.querySelector('a.t, span.name a, a.job-name');
                if (!titleA) continue;
                const t = (titleA.innerText || titleA.textContent || '').trim();
                if (!t) continue;
                const href = titleA.href || '';
                if (!href || href.startsWith('javascript')) continue;
                const coEl = c.querySelector('a.fl, a.comp_name, span.company-name');
                const salEl = c.querySelector('span.salary, b.price, p.salary');
                const locEl = c.querySelector('span.address, span.area');
                out.push({
                    title: t,
                    company: coEl ? (coEl.innerText || '').trim() : '',
                    url: href,
                    salary_text: salEl ? (salEl.innerText || '').trim() : '',
                    location: locEl ? (locEl.innerText || '').trim() : '',
                });
            }
            return out;
        }""")
    except Exception as e:
        logger.warning(f"[agent] 58同城 DOM 提取失败: {e}")
        return []
    return data or []


async def _extract_boss_from_dom(page) -> list[dict]:
    """BOSS直聘专属 DOM 提取：SSR JSON 优先 + DOM 卡片回退，直取 title/company/url。

    为什么必须特化：BOSS 同标题多公司并排，OCR+标题回填会错配 URL；且 BOSS
    反爬最严，越少交互越好——直接从 SSR JSON 拿加密岗位 ID 拼详情 URL，绕开 DOM。

    ⚠️ 未经实页验证（2026-08-09 实测：本机出口 IP 被 BOSS 风控拦截"当前 IP
    地址可能存在异常访问行为"，MCP 浏览器/API 都看不到卡片；需 CI/Camoufox
    环境实跑复核，本机无法验证）。字段路径沿用旧 scrapers/boss.py 已验证结果；
    命中不了时回退 OCR 兜底不丢数据。
    SSR JSON（window.__NEXT_DATA__ / __INITIAL_STATE__）：
      props.pageProps.jobList[]（或 props.pageProps.searchResult.jobList[]）
        jobName / brandName / salaryDesc / cityName+areaDistrict / jobLabels[]
        encryptJobId -> https://www.zhipin.com/job_detail/{encryptJobId}.html
        bossInfo.online -> hr_active
    DOM 回退（li.job-card-wrapper，旧版已验证选择器）：
      span.job-name / a.job-title / h3.name    标题
      h3.company-name a / a.company-name       公司
      span.salary / span.red                   薪资
      span.job-area / p.job-area               地点（"成都·高新区"，自带城市前缀）
      span.boss-online-tag                     HR 在线
      li.tag-item / span.tag-item              要求标签
      a.job-card-left / a[ka^='search_list']   详情链接（浏览器内 a.href 即绝对 URL）
    """
    try:
        data = await page.evaluate("""() => {
            const out = [];
            // ① SSR JSON：拿加密岗位 ID 拼详情 URL，零 DOM 依赖最稳
            const data = window.__NEXT_DATA__ || window.__INITIAL_STATE__ || null;
            if (data && typeof data === 'object') {
                const props = data.props || data;
                const pageProps = props.pageProps || props;
                const list = pageProps.jobList ||
                    (pageProps.searchResult && pageProps.searchResult.jobList) || [];
                for (const it of list) {
                    if (!it.jobName) continue;
                    const boss = it.bossInfo || {};
                    const eid = it.encryptJobId || it.jobId || '';
                    const city = (it.cityName || '').trim();
                    const area = (it.areaDistrict || '').trim();
                    out.push({
                        title: it.jobName.trim(),
                        company: it.brandName || '',
                        url: eid ? 'https://www.zhipin.com/job_detail/' + eid + '.html' : '',
                        salary_text: it.salaryDesc || '',
                        location: city + area,
                        requirements: (it.jobLabels || []).join(', '),
                        hr_active: !!(boss.online),
                    });
                }
                if (out.length) return out;
            }
            // ② DOM 卡片回退（SSR 没数据时）
            for (const c of document.querySelectorAll('li.job-card-wrapper, div.job-card-body, div.search-job-result li')) {
                const titleEl = c.querySelector('span.job-name, a.job-title, h3.name');
                if (!titleEl) continue;
                const t = (titleEl.innerText || titleEl.textContent || '').trim();
                if (!t) continue;
                const a = c.querySelector('a.job-card-left, a[ka^="search_list"]');
                const href = a ? (a.href || '') : '';
                const url = (href && !href.startsWith('javascript')) ? href : '';
                const coEl = c.querySelector('h3.company-name a, a.company-name, span.company-text');
                const salEl = c.querySelector('span.salary, span.red');
                const locEl = c.querySelector('span.job-area, p.job-area');
                const hrEl = c.querySelector('span.boss-online-tag');
                const reqTags = c.querySelectorAll('li.tag-item, span.tag-item');
                const reqText = Array.from(reqTags)
                    .map(x => (x.innerText || '').trim()).filter(Boolean).join(' ');
                out.push({
                    title: t,
                    company: coEl ? (coEl.innerText || '').trim() : '',
                    url,
                    salary_text: salEl ? (salEl.innerText || '').trim() : '',
                    location: locEl ? (locEl.innerText || '').trim() : '',
                    requirements: reqText.slice(0, 200),
                    hr_active: !!hrEl,
                });
            }
            return out;
        }""")
    except Exception as e:
        logger.warning(f"[agent] BOSS直聘 DOM 提取失败: {e}")
        return []
    return data or []


# 平台 -> 专属 DOM 提取器（结构化直取 title/company/url，避免 OCR 标题匹配错配）
# 运行时查找：extract_jobs_from_page 在模块加载完成后才调用，此时下方的提取器已定义
_DOM_EXTRACTORS: dict = {
    "鱼泡直聘": _extract_yupao_from_dom,
    "智联招聘": _extract_zhilian_from_dom,
    "猎聘": _extract_liepin_from_dom,
    "中华英才网": _extract_chinahr_from_dom,
    "58同城": _extract_wuba_from_dom,
    "BOSS直聘": _extract_boss_from_dom,
}


async def _collect_job51_urls(page, max_cards: int = 100) -> list[dict]:
    """51Job 新版 SPA：岗位卡是纯 JS 渲染，无 <a> 详情链接。

    jobId 存在卡片 div 的 sensorsdata 属性（JSON）里。
    详情 URL = https://jobs.51job.com/chengdu/{jobId}.html（已验证真实可达，仅 WAF 滑动验证）。
    jobTitle 与页面标题一致，可直接用于回填匹配。
    """
    try:
        data = await page.evaluate(f"""() => {{
            const out = [];
            const cards = document.querySelectorAll('[sensorsdata]');
            for (const el of cards) {{
                let info = null;
                try {{ info = JSON.parse(el.getAttribute('sensorsdata')); }} catch (e) {{ continue; }}
                const jobId = info && info.jobId;
                const title = info && info.jobTitle;
                if (!jobId || !title) continue;
                // company：sensorsdata 多种可能字段名，取不到向上查卡片 DOM（供 _merge_card_urls 同标题消歧）
                let company = (info && (info.company || info.companyName || info.company_name)) || '';
                if (!company) {{
                    let p = el.parentElement;
                    for (let i = 0; i < 5 && p && !company; i++) {{
                        const co = p.querySelector('.company-name, .company_name, .cname, a[href*="/co/"], a[href*="/corp/"]');
                        if (co) {{ const t = (co.innerText || '').trim(); if (t && t.length <= 40) company = t; }}
                        p = p.parentElement;
                    }}
                }}
                out.push({{ text: title, href: 'https://jobs.51job.com/chengdu/' + jobId + '.html', company }});
                if (out.length >= {max_cards}) break;
            }}
            return out;
        }}""")
    except Exception as e:
        logger.warning(f"[agent] 51Job 卡片链接采集失败: {e}")
        return []
    return data or []


async def _collect_jobui_urls(page, max_links: int = 600) -> list[dict]:
    """职友集专属：只采集真实职位详情页锚点（/job/数字/），过滤搜索页锚点。

    jobui 搜索页含大量"相关搜索"锚点（href=/jobs?jobKw=...），其文本恰好是
    岗位标题，通用通道按文本匹配会把搜索页 URL 当详情回填，产生正文为空的垃圾岗。
    这里只收 /job/数字/ 形态的详情锚点，从源头杜绝。
    """
    try:
        data = await page.evaluate(f"""() => {{
            const out = [];
            const re = /^https?:\\/\\/[^\\/]*jobui\\.com\\/job\\/\\d+\\/?/;
            const corpRe = /(有限公司|股份有限公司|有限责任|集团|科技|技术|实业|控股|研究院|研究所|设计院|大学|学院|事务所|合伙企业|分公司)/;
            const anchors = document.querySelectorAll('a[href*="/job/"]');
            for (const a of anchors) {{
                const href = a.href || '';
                if (!re.test(href)) continue;
                const text = (a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!text || text.length > 40) continue;
                // 向上取卡片容器里的公司名（供 _merge_card_urls 同标题多公司消歧）
                let company = '';
                let p = a.parentElement;
                for (let i = 0; i < 6 && p && !company; i++) {{
                    const coA = p.querySelector('a[href*="/companydetail/"], a[href*="/company/"], a[href*="/qiye/"], a[href*="/cmp/"], a[href*="/firm/"]');
                    if (coA) {{ const t = (coA.innerText || '').trim(); if (t && t.length <= 40) company = t; }}
                    p = p.parentElement;
                }}
                if (!company) {{
                    let q = a.parentElement;
                    for (let i = 0; i < 5 && q && !company; i++) {{
                        for (const el of q.querySelectorAll('*')) {{
                            if (el.children.length > 0) continue;
                            const t = (el.innerText || '').trim();
                            if (t && t.length >= 4 && t.length <= 40 && corpRe.test(t)) {{ company = t; break; }}
                        }}
                        q = q.parentElement;
                    }}
                }}
                out.push({{ text, href, company }});
            }}
            return out.slice(0, {max_links});
        }}""")
    except Exception as e:
        logger.warning(f"[agent] 职友集 DOM 链接采集失败: {e}")
        return []
    return data or []


def _merge_card_urls(raw_jobs: list[dict], card_links: list[dict]) -> list[dict]:
    """把 DOM 采集到的岗位链接回填到提取结果。

    匹配规则（2026-08-08 重构根治同标题错配）：
    - 锚点文本与岗位标题精确相等 -> 候选。
    - 标题完整包含于锚点文本（带后缀如"【急聘】"）-> 子串兜底候选。
    - 单候选 -> 回填（排除公司主页链接）。
    - 多候选（同标题多家公司，如"AI工程师"6 家）-> 按公司名双匹配：
      job.company 与候选 link.company 归一化相等/包含才回填对应 URL；
      匹配不唯一或都匹配不上 -> 不回填（卡片显示"暂无链接"而非错配，宁缺毋滥）。
    """
    if not card_links:
        return raw_jobs

    def norm(s: str) -> str:
        # 去空白 + 统一全半角括号 + 去标点，仅留中英文数字，让近似标题能匹配上
        s = re.sub(r"\s+", "", s or "").lower()
        s = s.replace("（", "(").replace("）", ")")
        return re.sub(r"[^a-z0-9一-鿿]", "", s)

    # 建索引：锚点文本 -> 候选链接列表（同文本可有多条，对应同标题的多家公司）
    by_text: dict[str, list[dict]] = {}
    for link in card_links:
        t = norm(link.get("text"))
        href = sanitize_url(link.get("href", ""))
        if not t or not href:
            continue
        by_text.setdefault(t, []).append({"href": href, "company": norm(link.get("company", ""))})

    def _company_match(job_co: str, link_co: str) -> bool:
        """公司名归一化匹配：相等或一方完整包含另一方（长度接近防短串误匹配）。"""
        if not job_co or not link_co:
            return False
        if job_co == link_co:
            return True
        if job_co in link_co or link_co in job_co:
            return abs(len(job_co) - len(link_co)) <= 6
        return False

    def _pick(candidates: list[dict], job_company: str) -> str:
        """从候选中按公司名选唯一 URL；多候选歧义/无匹配则不回填。"""
        if not candidates:
            return ""
        if len(candidates) == 1:
            return candidates[0]["href"]
        # 多候选：按公司名精确匹配消歧
        job_co = norm(job_company)
        if not job_co:
            return ""  # 无公司名无法消歧，宁缺毋滥
        matched = [c["href"] for c in candidates if _company_match(job_co, c["company"])]
        # 唯一匹配才回填；多个匹配仍有歧义 -> 不回填
        return matched[0] if len(matched) == 1 else ""

    for d in raw_jobs:
        if d.get("url"):
            continue
        title = norm(d.get("title"))
        company = d.get("company", "")
        if not title:
            continue

        # 精确匹配候选
        candidates = list(by_text.get(title, []))
        href = _pick(candidates, company)

        # 兜底：标题是锚点文本子串且长度接近，排除宽泛导航（首页/更多/热门）
        if not href:
            sub_candidates: list[dict] = []
            for t, cs in by_text.items():
                if title in t and 0 <= len(t) - len(title) <= 6:
                    sub_candidates.extend(cs)
            href = _pick(sub_candidates, company)

        # 若匹配到的是公司主页链接（公司名=锚点文本），丢弃
        if href and company:
            co_candidates = by_text.get(norm(company), [])
            if any(c["href"] == href for c in co_candidates):
                href = ""

        if href:
            d["url"] = href
            logger.debug(f"[agent] 回填详情URL: {d.get('title')} -> {href}")
    return raw_jobs


async def _extract_with_ocr(screenshots: list[bytes]) -> list[dict]:
    """RapidOCR 本地兜底：截图 → 文本 → 规则提取岗位。"""
    try:
        from src.ocr.rapid import extract_text_from_screenshot
        from src.ocr.parse_jobs import parse_jobs_from_text

        text = await extract_text_from_screenshot(screenshots)
        if not text:
            logger.warning("[agent] OCR 兜底未识别出文本")
            return []
        jobs = parse_jobs_from_text(text)
        logger.info(f"[agent] OCR 兜底解析出 {len(jobs)} 条岗位")
        return jobs
    except Exception as e:
        logger.warning(f"[agent] OCR 兜底失败: {e}")
        return []


def _dict_to_job(d: dict, platform: str, round_label: str) -> Job:
    title = str(d.get("title", "") or "").strip()
    company = str(d.get("company", "") or "").strip()
    url = sanitize_url(d.get("url", ""))
    location = str(d.get("location", "") or "").strip()
    # 同公司同标题不同地点/编制是不同岗位（中华英才实测"气象学"3 条并存），
    # job_id 必须带 location+url，否则下游 dedup_key=md5(platform:job_id) 会把它们压成 1 条
    job_id = f"agent-{hashlib.md5(f'{platform}:{title}:{company}:{location}:{url}'.encode()).hexdigest()[:12]}"
    return Job(
        platform=platform,
        job_id=job_id,
        url=url,
        title=title,
        company=company,
        salary_text=str(d.get("salary_text", "") or "").strip(),
        location=location,
        # 详情富集会追加更完整正文；这里放宽到 JD 全文上限（原 500 太短，导致 match_major 判专业时文本不足）
        requirements=str(d.get("requirements", "") or "")[:DETAIL_JD_TEXT_LIMIT],
        responsibilities=str(d.get("responsibilities", "") or "")[:DETAIL_JD_TEXT_LIMIT],
        hr_active=bool(d.get("hr_active", False)),
        posted_date=str(d.get("posted_date", "") or "").strip(),
        scraped_at=datetime.now().isoformat(),
        search_round=round_label,
    )


def _dedup_jobs(jobs: list[Job]) -> list[Job]:
    """按 公司+岗位+地点+URL 去重，过滤空岗位。

    同公司同标题但不同地点/编制是不同岗位（各自详情 URL 必须保留），
    只有四元组全同才算重复（滚动截图重叠采到同一卡片）。
    """
    seen: set[str] = set()
    result: list[Job] = []
    for j in jobs:
        if not j.title and not j.company:
            continue
        key = f"{j.company}|{j.title}|{j.location}|{j.url}".lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(j)
    return result
