#!/usr/bin/env python3
"""临时验证脚本：鱼泡 URL 回填 + 提取噪音 + 城市过滤。

跳过 agent LLM loop，直接复用 YupaoAgentScraper 的 Camoufox persistent context，
打开搜索页后直接调用 extract_jobs_from_page，打印每个岗位的 url 字段，
确认 URL 回填是真实 /zhaogong/{id}/{slug}.html 而非占位符/空。

用法：
  python scripts/verify_yupao.py --keyword 气象
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.extract import extract_jobs_from_page
from src.scrapers import AGENT_SCRAPERS


async def main() -> int:
    # Windows 终端编码不一致 → 强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    keyword = sys.argv[sys.argv.index("--keyword") + 1] if "--keyword" in sys.argv else "气象"

    # 鱼泡频率风控：脚本内冷却，避免短时多次访问触发降级
    import time
    time.sleep(10)

    scraper_cls = AGENT_SCRAPERS["鱼泡直聘"]
    print(f"平台: {scraper_cls.platform_name}   关键词: {keyword}")

    async with scraper_cls() as scraper:
        page = await scraper.context.new_page()
        start_url = scraper.build_start_url(keyword)
        print(f"起始 URL: {start_url}")
        await scraper._prewarm(page, start_url)

        # 风控检查：鱼泡频率风控跳 /safe/verify/，多次访问升级为强制登录墙 /web/login/
        low = page.url.lower()
        body = (await page.evaluate("() => document.body?.innerText || ''"))[:200]
        if "/web/login/" in low or "safe/verify" in low or "安全访问" in body or "点击进行验证" in body:
            reason = "强制登录墙" if "/web/login/" in low else "安全访问验证（风控）"
            print(f"❌ 触发{reason}，headless 无法通过")
            print(f"   URL: {page.url}")
            print(f"   body: {body[:120]}")
            print("   处理：重跑 capture_session 扫码登录（改进后的检测会等导航栏「登录丨注册」消失）")
            return 1

        # 诊断：等岗位卡加载（SPA 渲染可能慢于 _prewarm 的 2.5s）
        for i in range(10):
            n = await page.evaluate("() => document.querySelectorAll('a[href*=\"/zhaogong/\"]').length")
            if n > 0:
                break
            await page.wait_for_timeout(1000)
        naccba = await page.evaluate("() => document.querySelectorAll('div.accba').length")
        print(f"[诊断] 最终 URL: {page.url}")
        print(f"[诊断] title: {await page.title()}")
        print(f"[诊断] body 前 300 字: {body!r}")
        print(f"[诊断] 等 {i + 1}s 后 /zhaogong/ 锚点数: {n} | div.accba 卡片数: {naccba}")

        # ① 先看 DOM 锚点通道能采到多少卡片链接（URL 回填的前提）
        anchors = await page.evaluate("""() => {
            const out = [];
            for (const a of document.querySelectorAll('a[href]')) {
                const text = (a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!text || text.length > 40) continue;
                out.push({ text: text.slice(0, 25), href: a.href.slice(0, 90) });
            }
            return out.slice(0, 15);
        }""")
        print(f"\n[DOM] 前 {len(anchors)} 个短文本锚点（检查 /zhaogong/ 链接是否可采）:")
        for a in anchors:
            print(f"  - {a['text']!r}  ->  {a['href']}")
        zhaogong = await page.evaluate("() => document.querySelectorAll('a[href*=\"/zhaogong/\"]').length")
        print(f"[DOM] /zhaogong/ 详情锚点数: {zhaogong}")

        # 诊断异常卡片：标题文本 > 12 字的 .accba 卡（职责描述被当成标题的疑似置顶卡）
        card_html = await page.evaluate("""() => {
            const out = [];
            const cards = document.querySelectorAll('div.accba');
            for (const c of cards) {
                const a = c.querySelector('a.accjh[href*="/zhaogong/"][href*=".html"]');
                const title = c.querySelector('div.accbc');
                if (!a || !title) continue;
                const t = (title.innerText || title.textContent || '').trim();
                if (t.length > 12) {
                    out.push({ href: a.href.slice(0, 90), title: t.slice(0, 40),
                               html: c.outerHTML.slice(0, 2200) });
                }
            }
            return out;
        }""")
        print(f"\n[诊断] 标题>12字的异常卡 {len(card_html)} 张:")
        for cd in card_html[:3]:
            print("  href:", cd["href"])
            print("  title:", cd["title"])
            print("  outerHTML:", cd["html"][:1200], "\n")

        # 完整 dump href=330062844 那张职责描述被当标题的卡
        weird = await page.evaluate("""() => {
            const c = document.querySelector('a[href*="330062844"]');
            if (!c) return 'CARD GONE';
            let el = c;
            for (let i = 0; i < 5 && el; i++) {
                if (el.classList && el.classList.contains('accba')) break;
                el = el.parentElement;
            }
            return el.outerHTML.slice(0, 3000);
        }""")
        print("\n[诊断] 职责描述卡完整 outerHTML:\n", weird)

        # ② 完整提取链路（OCR → DeepSeek 结构化 → URL 回填）
        print("\n提取中（OCR + 结构化）...")
        jobs = await extract_jobs_from_page(page, scraper_cls.platform_name)
        await page.close()

    print(f"\n提取到 {len(jobs)} 条岗位：")
    real = empty = placeholder = 0
    for j in jobs:
        u = j.url or ""
        if u.startswith("http") and "/zhaogong/" in u:
            real += 1
        elif not u:
            empty += 1
        else:
            placeholder += 1
        print(f"  - {j.title!r} | {j.company} | {j.salary_text} | {j.location} | {u}")

    print(f"\n统计: 共 {len(jobs)} 条 | 真实 /zhaogong/ URL {real} | 空 URL {empty} | 其他 {placeholder}")
    chengdu = sum(1 for j in jobs if "成都" in (j.location or ""))
    print(f"城市过滤: 含成都 {chengdu}/{len(jobs)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
