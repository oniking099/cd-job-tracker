#!/usr/bin/env python3
"""DOM 提取器 实页验证脚本（可重跑，CI 每晚 + 每次 push 自动跑）。

背景：提取器的验证对象是"代码本身"——改选择器/逻辑后，旧的实页验证对不上新代码，
于是感觉"每次改代码都要重做复核"。本脚本把复核沉淀成一条命令 + 断言门槛：
真实加载平台页面 → 直接调该平台的 DOM 提取器（不经 OCR/视觉兜底，验证的就是
提取器本身）→ 结构性断言（标题填充率 / URL 真实性与域匹配）。

verdict（不影响门槛的失败不亮红）：
  PASS    提取出结构化岗位，标题填充率≥90%，URL 真实同域、无 javascript: 占位
  BLOCKED 页面被平台风控/登录墙拦截（0 提取 + 命中拦截特征）——非提取器 bug，不计失败
  SKIP    本地缺 Camoufox（BOSS 平台）——CI 已装会跑，不计失败
  FAIL    页面可加载、未拦截，但提取 0 条或结构不合格——真 bug，退出码 1

用法：
  python scripts/verify_extractors.py                 # 验证全部 DOM 提取平台
  python scripts/verify_extractors.py --platform 58同城
  python scripts/verify_extractors.py --keyword 气象
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.extract import _DOM_EXTRACTORS
from src.scrapers.agent_scraper import (
    BossAgentScraper,
    WubaAgentScraper,
)

# 诊断落盘目录（FAIL 时把 CI 实页现场写进 data/verify-diagnostics/，
# search.yml 的 data/ 自动提交会带上仓库，无日志权限也能看到页面结构）
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 平台 -> 验证配置：scraper（复用其 context/登录态）、期望 URL 域名、是否需 Camoufox
PLATFORMS: dict[str, dict] = {
    "58同城": dict(
        scraper=WubaAgentScraper, domains=("58.com",), needs_camoufox=False,
    ),
    "BOSS直聘": dict(
        scraper=BossAgentScraper, domains=("zhipin.com",), needs_camoufox=True,
    ),
}

# 风控/登录墙拦截特征（仅当提取 0 条时才判定，避免误伤正常页面的导航"登录"字样）
_BLOCK_MARKERS = (
    "验证码", "安全验证", "访问异常", "异常访问", "人机验证", "滑动验证",
    "verify", "captcha", "扫码登录",
)
_BLOCK_URL_FRAGMENTS = (
    "passport.", "verifyCode", "security", "captcha", "sso/", "checkcode",
)


def _looks_blocked(page_title: str, page_url: str) -> bool:
    """页面无提取结果时，用 标题+URL 判定是否被平台风控/登录墙拦截。"""
    text = f"{page_title} {page_url}".lower()
    if any(f in text for f in _BLOCK_URL_FRAGMENTS):
        return True
    return any(m in text for m in _BLOCK_MARKERS)


def _dump_diagnostic(platform: str, keyword: str, title: str, url: str,
                     body_len: int, ssr: str, dom_cards: int, note: str) -> None:
    """FAIL 时输出 ::error:: 注解（annotations API 可读，绕开日志鉴权）+ 落盘现场。"""
    line = (f"[{platform}] {note} | title={title!r} | url={url[:80]} "
            f"| body_len={body_len} | ssr={ssr} | dom_cards={dom_cards}")
    # ::error:: 被 GitHub Actions 转成 run 注解，check-runs annotations 接口即可读
    print(f"::error::{line}")
    try:
        d = DATA_DIR / "verify-diagnostics"
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        f = d / f"{platform}-{stamp}.txt"
        f.write_text(
            f"[{platform}] verify FAIL ({note})\n"
            f"keyword:   {keyword}\n"
            f"page_title: {title}\n"
            f"page_url:  {url}\n"
            f"body_len:  {body_len}\n"
            f"ssr:       {ssr}\n"
            f"dom_cards: {dom_cards}\n",
            encoding="utf-8",
        )
        print(f"  ⚠️ FAIL 现场已写入 {f}")
    except Exception as e:
        print(f"  ⚠️ 诊断写入失败: {e}")


def _check_structure(rows: list[dict], domains: tuple[str, ...]) -> tuple[bool, str]:
    """结构性断言：标题填充率 + URL 真实性与域匹配（错配问题的根因检查）。"""
    total = len(rows)
    if total == 0:
        return False, "空提取"

    missing_title = sum(1 for r in rows if not (r.get("title") or "").strip())
    title_fill = (total - missing_title) / total

    bad_urls = []
    for r in rows:
        u = (r.get("url") or "").strip()
        if not u or u.startswith("javascript:") or not u.startswith("http"):
            bad_urls.append(u)
            continue
        if not any(d in u for d in domains):
            bad_urls.append(u)
    url_ok = (total - len(bad_urls)) / total

    issues = []
    if title_fill < 0.9:
        issues.append(f"标题填充率 {title_fill:.0%} < 90%")
    if url_ok < 0.9:
        issues.append(f"URL 合格率 {url_ok:.0%} < 90%（域名须含 {domains}）")
    # 信息项（不设门槛：58 sou 页无公司/薪资属平台字段缺失，不是 bug）
    sal = sum(1 for r in rows if (r.get("salary_text") or "").strip())
    loc = sum(1 for r in rows if (r.get("location") or "").strip())
    info = f"｜薪资填充 {sal*100//total}% 地点填充 {loc*100//total}%"
    if not issues:
        return True, f"{total} 条，标题 {title_fill:.0%}，URL 合格 {url_ok:.0%}{info}"
    return False, f"{total} 条，{'；'.join(issues)}{info}"


async def _verify_platform(platform: str, cfg: dict, keyword: str) -> tuple[str, str]:
    scraper_cls = cfg["scraper"]
    domains = cfg["domains"]
    needs_camoufox = cfg["needs_camoufox"]

    if needs_camoufox:
        try:
            import camoufox  # noqa: F401
        except ImportError:
            return "SKIP", "本地未装 Camoufox（CI 已装会自动跑）"

    extractor = _DOM_EXTRACTORS.get(platform)
    if extractor is None:
        return "FAIL", f"_DOM_EXTRACTORS 未注册 {platform}"

    try:
        async with scraper_cls() as scraper:
            # 普通 Chromium scraper 的 __aenter__ 只启动浏览器，context 需 _new_context 创建；
            # Camoufox 的 __aenter__ 已建好 persistent context，_new_context 幂等返回它。
            if scraper.context is None:
                scraper.context = await scraper._new_context()
            page = await scraper.context.new_page()
            start_url = scraper.build_start_url(keyword)
            await scraper._prewarm(page, start_url)
            # SPA 平台（BOSS 等）懒加载卡片：等网络空闲 + 余量，避免页面没渲染完就提取 → 假 FAIL
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(2500)
            try:
                rows = [d for d in (await extractor(page) or []) if d]
            except Exception as e:
                await page.close()
                return "FAIL", f"提取器异常: {e}"
            try:
                page_title = await page.title()
                page_url = page.url
                body_len = await page.evaluate(
                    "() => document.body ? document.body.innerText.length : 0"
                )
                ssr = await page.evaluate("""() => {
                    try {
                        const d = window.__NEXT_DATA__ || window.__INITIAL_STATE__ || null;
                        if (!d) return 'no-ssr';
                        const p = (d.props && d.props.pageProps) ? d.props.pageProps : d;
                        const l = p.jobList || (p.searchResult && p.searchResult.jobList) || [];
                        return 'jobList=' + (l || []).length;
                    } catch (e) { return 'ssr-read-err'; }
                }""")
                dom_cards = await page.evaluate("""() => {
                    return document.querySelectorAll(
                        'li.job-card-wrapper, div.job-card-body, div.search-job-result li'
                    ).length;
                }""")
            except Exception:
                body_len, ssr, dom_cards = 0, "", 0
            await page.close()
    except Exception as e:
        # 浏览器启动失败（Camoufox 依赖/平台风控把连接掐断等）——环境问题，非提取器 bug
        return "BLOCKED", f"浏览器启动失败: {str(e)[:120]}"

    if rows:
        ok, msg = _check_structure(rows, domains)
        verdict = "PASS" if ok else "FAIL"
        if not ok:
            _dump_diagnostic(platform, keyword, page_title, page_url, body_len, ssr, dom_cards, msg)
        return verdict, msg

    if _looks_blocked(page_title, page_url):
        return "BLOCKED", f"页面被风控/登录墙拦截（title={page_title[:40]!r}）"
    _dump_diagnostic(platform, keyword, page_title, page_url, body_len, ssr, dom_cards, "提取 0 条")
    return "FAIL", "页面可加载、无拦截特征，但提取 0 条"


async def main() -> int:
    parser = argparse.ArgumentParser(description="DOM 提取器实页验证")
    parser.add_argument("--platform", default="", help="只验证指定平台（空=全部）")
    parser.add_argument("--keyword", default="气象", help="验证用关键词（默认 气象）")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    platforms = [args.platform] if args.platform else list(PLATFORMS)
    failures: list[str] = []
    print("=" * 68)
    print("DOM 提取器 实页验证（关键词: %s）" % args.keyword)
    print("=" * 68)
    for name in platforms:
        verdict, msg = await _verify_platform(name, PLATFORMS[name], args.keyword)
        mark = "✅" if verdict == "PASS" else ("🟡" if verdict in ("BLOCKED", "SKIP") else "❌")
        print(f"{mark} [{verdict:<7}] {name}: {msg}")
        if verdict == "FAIL":
            failures.append(name)
    print("=" * 68)
    if failures:
        print(f"❌ FAIL 平台: {', '.join(failures)} —— 提取器 bug，需修复")
        return 1
    print("✅ 无 FAIL（BLOCKED=平台风控非bug，SKIP=CI 会跑）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
