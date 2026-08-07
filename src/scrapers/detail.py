"""
详情页正文富集：JD 质量修复的根因层。

背景（2026-08-05 代码级确认）：所有 agent 平台只在搜索结果「列表页」截图+OCR+LLM 提取，
而岗位职责/要求正文只存在于「详情页」，从未被访问 → responsibilities/requirements 90%+ 为空
→ match_major 只能拿标题（普遍不含专业词）判专业 → 95%+ 判"专业不匹配"。

本模块在轮级收尾时启动一个浏览器，逐个访问岗位详情页：
  goto(url) → 等详情容器渲染（有 detail_selector 用 selector，否则等正文长文本出现）
  → 取 document.body.innerText 全文 → 写入 responsibilities，启发式切出 requirements。

设计取舍：
- 轮级（而非平台级）执行：平台级会被 PLATFORM_BUDGET_AGENT=300s 预算挤爆（agent 循环+提取
  本身已逼近预算），轮级只启动一个浏览器、按 URL 去重富集，时间可控（60 条 × ~3s ≈ 3 分钟）。
- 登录墙/超时/正文为空 → 保留列表数据、记录错误，绝不中断整轮。
- 已有 URL 回填基础设施（src/agent/extract.py `_collect_card_urls`/`_merge_card_urls`/`sanitize_url`），
  列表提取时已尽量回填真实详情 URL；这里只富集带真实 URL 的岗位。
"""
from __future__ import annotations

import logging
import random
import re
import time

from playwright.async_api import async_playwright

from src.agent.perceive import detect_login_wall, page_full_text
from src.agent.extract import sanitize_url
from src.config import (
    DETAIL_BUDGET_SECONDS,
    DETAIL_JD_TEXT_LIMIT,
    DETAIL_MAX_JOBS,
)
from src.models import Job
from src.scrapers.base import UA_POOL, VIEWPORT_POOL, apply_stealth

logger = logging.getLogger(__name__)

# 各平台详情页 JD 容器选择器（web 调研常用容器；缺失走通用"正文长文本"回退）。
# 站点改版会让 selector 失效，但通用回退兜底，不影响整体。
DETAIL_SELECTORS: dict[str, str] = {
    "51Job": ".job_msg",
    "智联招聘": ".describtion",
    "BOSS直聘": ".job-sec-text",
    "猎聘": ".job-intro",
}

# 任职要求/岗位要求等段落标记（用于从全文切出 requirements）
_REQUIREMENT_MARKERS = (
    "任职要求", "岗位要求", "职位要求", "任职资格",
    "岗位任职资格", "招聘要求", "应聘要求", "任职条件",
)

# JD 区块标记：真实岗位详情页一般至少出现一个（用于区分正文与落地页/列表页噪音）。
# 注意不含"职位信息"——它太泛，登录墙文本（"请扫码登录后查看完整职位信息"）也命中，
# 会放行登录墙；真实 JD 页几乎必有 任职要求/岗位职责/职位描述 等更特异的标记。
_JD_SECTION_MARKERS = (
    "任职要求", "岗位要求", "职位要求", "任职资格", "岗位职责",
    "职位描述", "工作内容", "岗位描述", "职位亮点",
    "岗位概述", "职业发展", "工作职责",
)

# 明确错误页/失效页标记：命中任一即视为非详情页（404/下线/被删）
_ERROR_PAGE_MARKERS = (
    "找不到该页", "File not found", "页面不存在", "您要查看的页已删除",
    "该职位已下线", "该职位已过期", "该职位已失效", "职位已删除",
    "该职位不存在", "职位已失效", "自动跳转首页", "抱歉，您访问的页面",
    "很抱歉，您访问的页面", "此职位已停止", "招聘已结束", "职位关闭",
)

# 岗位信号词：真实 JD 正文通常含薪资/经验/学历等（无 JD 区块标记时的兜底判据）
_JOB_SIGNALS = ("薪资", "月薪", "经验", "学历", "岗位", "招聘", "投递", "简历", "职责")


def _looks_like_detail_page(text: str) -> bool:
    """判定抓到的正文是否真是一个岗位详情页（排除 404/登录墙/落地页/列表页噪音）。

    冒烟测试（2026-08-05）发现：占位假 URL / 过期职位 / 登录墙会把错误页正文
    （"找不到该页"、BOSS 落地页导航）当 JD 文本写入，污染数据。这里在写入前设防：
    1. 命中错误页/失效标记 → 拒
    2. 命中登录/验证关键词且无 JD 区块标记 → 拒（登录墙正文可能很长，不再只看短文本）
    3. 有 JD 区块标记 → 放行；否则要求 ≥2 个岗位信号词（列表页/导航页通常不满足）
    """
    if not text:
        return False
    if any(m in text for m in _ERROR_PAGE_MARKERS):
        return False
    if detect_login_wall(text) and not any(m in text for m in _JD_SECTION_MARKERS):
        return False
    if any(m in text for m in _JD_SECTION_MARKERS):
        return True
    return sum(1 for k in _JOB_SIGNALS if k in text) >= 2


def _collect_urls(jobs: list[Job], max_jobs: int) -> tuple[dict[str, list[Job]], list[str]]:
    """按真实 URL 去重组织岗位，返回 {url: [jobs...]} 与待访问 url 列表。"""
    jobs_by_url: dict[str, list[Job]] = {}
    for j in jobs:
        u = sanitize_url(j.url)
        if not u:
            continue
        jobs_by_url.setdefault(u, []).append(j)
    urls = list(jobs_by_url.keys())[:max_jobs]
    return jobs_by_url, urls


def _clean_text(text: str) -> str:
    """压缩空白、去掉多余空行、按长度截断。"""
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    joined = "\n".join(lines)
    # 中文站点标题/公司/薪资等噪声行很常见，但整段保留比删错好——match_major 只做关键词命中。
    return joined[:DETAIL_JD_TEXT_LIMIT]


def _split_job_text(text: str) -> tuple[str, str]:
    """启发式拆分正文为 (responsibilities, requirements)。

    规则：全文始终写入 responsibilities（match_major 扫描两个字段任一命中即放行）；
    若命中"要求"类段落标记，把该标记往后的文本作为 requirements。
    标记缺失时 requirements 留空，不猜测。
    """
    if not text:
        return "", ""
    req_start = -1
    for marker in _REQUIREMENT_MARKERS:
        idx = text.find(marker)
        if idx != -1 and (req_start == -1 or idx < req_start):
            req_start = idx
    if req_start > 0:
        requirements = text[req_start:]
    else:
        requirements = ""
    return text, requirements


async def _extract_detail_text(page, detail_selector: str | None) -> str:
    """访问详情页后等待正文渲染并取全文。

    有 selector：等该容器可见再取容器 innerText（最准）。
    无 selector：等 2s 让 SPA 渲染，取 body innerText（通用回退）。
    等待失败也尝试读 body 文本（页面可能不满足 selector 但仍有内容）。
    """
    if detail_selector:
        try:
            await page.wait_for_selector(detail_selector, state="visible", timeout=8000)
            text = await page.evaluate(
                "(sel) => { const el = document.querySelector(sel); return el ? el.innerText : ''; }",
                detail_selector,
            )
            if text and text.strip():
                return text
        except Exception:
            pass
    else:
        try:
            await page.wait_for_timeout(2000)
        except Exception:
            pass
    try:
        return await page_full_text(page)
    except Exception:
        return ""


async def _enrich_with_page(
    page,
    jobs_by_url: dict[str, list[Job]],
    urls: list[str],
    budget_seconds: int,
) -> tuple[int, list[str]]:
    """逐个访问详情页取正文，写回对应岗位。返回 (富集成功数, 错误列表)。"""
    enriched = 0
    errors: list[str] = []
    start = time.monotonic()

    for url in urls:
        if time.monotonic() - start > budget_seconds:
            errors.append("详情富集达到时间预算，提前停止")
            break
        jobs = jobs_by_url[url]
        platform = jobs[0].platform if jobs else ""
        selector = DETAIL_SELECTORS.get(platform)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            text = await _extract_detail_text(page, selector)
            text = _clean_text(text)

            if not text:
                errors.append(f"[{platform}] {url}: 详情页正文为空")
                # 数据层修复（用户 2026-08-06）：仅当岗位缺 JD 正文（本次访问正是为了补全它）
                # 且详情页打不开/拿不到正文时，才标记"无有效 JD 页面"剔除。
                # 列表页已有完整职责的岗位不依赖详情页，详情页失败不影响它。
                for j in jobs:
                    if not (j.responsibilities or j.requirements).strip():
                        j.excluded = True
                        j.exclude_reason = "无有效JD页面: 详情页正文为空"
                continue
            if not _looks_like_detail_page(text):
                errors.append(f"[{platform}] {url}: 非岗位详情页（404/登录墙/落地页），跳过")
                # 同上：访问到的不是真岗位详情页（404/登录墙/落地页）。
                # 详情页无效时，列表残留的少量正文（常是 OCR 噪音）不足以做专业/行业筛选，
                # 正文 <50 字的一并剔除，避免风控重定向没抓到正文的岗混入报告。
                for j in jobs:
                    body = (j.responsibilities or j.requirements).strip()
                    if len(body) < 50:
                        j.excluded = True
                        j.exclude_reason = "无有效JD页面: 非岗位详情页（404/登录墙/落地页）"
                continue

            responsibilities, requirements = _split_job_text(text)
            for j in jobs:
                # 列表提取若已产出部分职责，保留并追加详情正文（避免丢信息）
                j.responsibilities = _merge_text(j.responsibilities, responsibilities)
                j.requirements = _merge_text(j.requirements, requirements)
            enriched += 1
        except Exception as e:
            errors.append(f"[{platform}] {url}: {e}")
            continue
        finally:
            # 限流：避免短时间高频访问触发风控
            try:
                await page.wait_for_timeout(random.uniform(1000, 2000))
            except Exception:
                pass

    return enriched, errors


def _merge_text(existing: str, new: str) -> str:
    """合并已有文本与详情正文，去重、截断。"""
    if existing and new and new not in existing:
        merged = f"{existing}\n{new}"
    else:
        merged = existing or new
    return merged[:DETAIL_JD_TEXT_LIMIT]


async def _launch_browser(playwright):
    """启动浏览器：优先系统真实 Chrome（指纹更真），CI 无则回退内置 Chromium。"""
    launch_kwargs = dict(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
        ignore_default_args=["--enable-automation"],
    )
    try:
        return await playwright.chromium.launch(channel="chrome", **launch_kwargs)
    except Exception:
        return await playwright.chromium.launch(**launch_kwargs)


async def enrich_job_details(
    jobs: list[Job],
    *,
    max_jobs: int = DETAIL_MAX_JOBS,
    budget_seconds: int = DETAIL_BUDGET_SECONDS,
) -> tuple[int, list[str]]:
    """入口：为岗位列表补齐详情页 JD 正文。

    返回 (富集成功数, 错误列表)。无真实 URL / 岗位为空时返回 (0, [])，
    不抛异常——富集是增强项，失败不影响已有列表数据。
    """
    if not jobs:
        return 0, []
    jobs_by_url, urls = _collect_urls(jobs, max_jobs)
    if not urls:
        logger.info("[detail] 无岗位带真实 URL，跳过详情富集")
        return 0, []

    enriched, errors = 0, []
    try:
        async with async_playwright() as p:
            browser = await _launch_browser(p)
            try:
                context = await browser.new_context(
                    user_agent=random.choice(UA_POOL),
                    viewport=random.choice(VIEWPORT_POOL),
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                await apply_stealth(context)
                page = await context.new_page()
                enriched, errors = await _enrich_with_page(
                    page, jobs_by_url, urls, budget_seconds,
                )
            finally:
                await browser.close()
    except Exception as e:
        logger.warning(f"[detail] 浏览器启动失败，跳过详情富集: {e}")
        return 0, [f"详情富集浏览器启动失败: {e}"]

    if enriched:
        logger.info(f"[detail] 详情富集成功 {enriched}/{len(urls)} 条")
    if errors:
        logger.info(f"[detail] 详情富集记录 {len(errors)} 条错误")
    return enriched, errors
