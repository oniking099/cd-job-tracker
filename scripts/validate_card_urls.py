"""
点4 验证：51Job 实页跑新提取链，确认 _collect_card_urls 返回的是真实 DOM href。
用法: python -u scripts/validate_card_urls.py
"""
import asyncio
import logging

from src.scrapers.agent_scraper import Job51AgentScraper
from src.agent.extract import extract_jobs_from_page, _collect_card_urls

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")


async def main() -> None:
    async with Job51AgentScraper() as scraper:
        scraper.context = await scraper._new_context()
        page = await scraper.context.new_page()

        start_url = scraper.build_start_url("成都 气象工程师")
        print("start:", start_url)
        await scraper._prewarm(page, start_url)
        # 等 SPA 结果渲染
        await page.wait_for_timeout(4000)

        # 1) 先看 DOM 原始链接（证明页面有真实岗位锚点）
        links = await _collect_card_urls(page, "51Job")
        print(f"\n[DOM] 采集到锚点 {len(links)} 个，前 8 个:")
        for l in links[:8]:
            print(f"    {l['text'][:22]!r:28} -> {l['href']}")

        # 2) 完整提取链（OCR → DeepSeek → 规则 → 视觉兜底 + URL 回填）
        jobs = await extract_jobs_from_page(page, "51Job", "url-val")
        print(f"\n[提取] 共 {len(jobs)} 条岗位")
        with_url = [j for j in jobs if j.url]
        print(f"[URL] 带真实URL: {len(with_url)} 条")
        for j in with_url[:12]:
            print(f"    {j.title[:18]:20} | {j.url}")
        no_url = [j for j in jobs if not j.url]
        if no_url:
            print(f"[无URL] {len(no_url)} 条: {[j.title[:14] for j in no_url[:6]]}")

        await page.close()


if __name__ == "__main__":
    asyncio.run(main())
