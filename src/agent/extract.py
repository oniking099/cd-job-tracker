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
    return u


async def extract_jobs_from_page(
    page,
    platform: str,
    round_label: str = "",
    max_viewports: int = MAX_VIEWPORTS,
) -> list[Job]:
    """滚动采集多视口截图，多图一次提取，去重后映射为 Job 列表。"""
    # 鱼泡卡片结构稳定且 .accbj 标签会污染 OCR 链路 → 专属 DOM 提取优先
    if platform == "鱼泡直聘":
        dom_jobs = await _extract_yupao_from_dom(page)
        if dom_jobs:
            jobs = [_dict_to_job(d, platform, round_label) for d in dom_jobs]
            return _dedup_jobs(jobs)
        # 登录墙/风控页：不回退 OCR（会把"微信扫码 快捷登录"解析成垃圾岗位）
        try:
            low = (page.url or "").lower()
            if "/web/login/" in low or "/safe/verify/" in low:
                logger.warning("[agent] 鱼泡登录墙/风控页，跳过提取")
                return []
        except Exception:
            pass
        logger.warning("[agent] 鱼泡 DOM 提取为空，回退通用 OCR 链路")

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
    - 视觉兜底：ModelScope（免费 2000/天）→ SiliconFlow → Gemini → Qwen-VL(DashScope)
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
        ("SiliconFlow", "src.llm.siliconflow"),
        ("Gemini", "src.llm.gemini"),
        ("Qwen-VL", "src.llm.qwen_vl"),
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

    try:
        data = await page.evaluate(f"""() => {{
            const out = [];
            const anchors = document.querySelectorAll('a[href]');
            for (const a of anchors) {{
                const href = a.href || '';
                if (!href || href.startsWith('javascript') || href === '#') continue;
                const text = (a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!text || text.length > 40) continue;
                out.push({{ text, href }});
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
                out.push({{ text: title, href: 'https://jobs.51job.com/chengdu/' + jobId + '.html' }});
                if (out.length >= {max_cards}) break;
            }}
            return out;
        }}""")
    except Exception as e:
        logger.warning(f"[agent] 51Job 卡片链接采集失败: {e}")
        return []
    return data or []


def _merge_card_urls(raw_jobs: list[dict], card_links: list[dict]) -> list[dict]:
    """把 DOM 采集到的岗位链接回填到提取结果。

    匹配规则：锚点文本与岗位标题相等（多数平台如此），或标题完整包含于锚点文本
    （标题带后缀如"【急聘】"）。匹配到公司名/无关链接则跳过，避免父子页误配。
    """
    if not card_links:
        return raw_jobs

    def norm(s: str) -> str:
        # 去空白 + 统一全半角括号 + 去标点，仅留中英文数字，让近似标题能匹配上
        s = re.sub(r"\s+", "", s or "").lower()
        s = s.replace("（", "(").replace("）", ")")
        return re.sub(r"[^a-z0-9一-鿿]", "", s)

    # 先建索引：锚点文本 → 链接（同文本取第一个，丢弃占位符链接）
    by_text: dict[str, str] = {}
    for link in card_links:
        t = norm(link.get("text"))
        href = sanitize_url(link.get("href", ""))
        if t and href and t not in by_text:
            by_text[t] = href

    for d in raw_jobs:
        if d.get("url"):
            continue
        title = norm(d.get("title"))
        company = norm(d.get("company"))
        if not title:
            continue

        # 精确匹配优先（多数平台岗位标题即锚点文本）
        href = by_text.get(title, "")

        # 兜底：标题是锚点文本子串且长度接近，排除宽泛导航（首页/更多/热门）
        if not href:
            for t, h in by_text.items():
                if title in t and 0 <= len(t) - len(title) <= 6:
                    href = h
                    break

        # 若匹配到的是公司主页链接（公司名=锚点文本），丢弃
        if company and by_text.get(company, "") == href and href:
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
    job_id = f"agent-{hashlib.md5(f'{platform}:{title}:{company}'.encode()).hexdigest()[:12]}"
    return Job(
        platform=platform,
        job_id=job_id,
        url=url,
        title=title,
        company=company,
        salary_text=str(d.get("salary_text", "") or "").strip(),
        location=str(d.get("location", "") or "").strip(),
        # 详情富集会追加更完整正文；这里放宽到 JD 全文上限（原 500 太短，导致 match_major 判专业时文本不足）
        requirements=str(d.get("requirements", "") or "")[:DETAIL_JD_TEXT_LIMIT],
        responsibilities=str(d.get("responsibilities", "") or "")[:DETAIL_JD_TEXT_LIMIT],
        hr_active=bool(d.get("hr_active", False)),
        posted_date=str(d.get("posted_date", "") or "").strip(),
        scraped_at=datetime.now().isoformat(),
        search_round=round_label,
    )


def _dedup_jobs(jobs: list[Job]) -> list[Job]:
    """按 公司+岗位 去重，过滤空岗位。"""
    seen: set[str] = set()
    result: list[Job] = []
    for j in jobs:
        if not j.title and not j.company:
            continue
        key = f"{j.company}|{j.title}".lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(j)
    return result
