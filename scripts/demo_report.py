"""
点4 演示：51Job 实页抓取 → 真实 URL → 新格式报告（output/<today>/demo-report.html）。
用法: python -u scripts/demo_report.py
"""
import asyncio
import logging
from pathlib import Path

from src.scrapers.agent_scraper import Job51AgentScraper
from src.agent.extract import extract_jobs_from_page
from src.models import Job
from src.report.generator import generate_report
from src.config import OUTPUT_DIR

logging.basicConfig(level=logging.WARNING)


async def main() -> None:
    async with Job51AgentScraper() as scraper:
        scraper.context = await scraper._new_context()
        page = await scraper.context.new_page()

        jobs: list[Job] = []
        for kw in ["成都 气象工程师", "成都 环境工程师"]:
            await scraper._prewarm(page, scraper.build_start_url(kw))
            await page.wait_for_timeout(4000)
            batch = await extract_jobs_from_page(page, "51Job", "demo")
            jobs.extend(batch)
            print(f"[{kw}] 提取 {len(batch)} 条")

        await page.close()

    jobs = scraper._filter_city(jobs)
    jobs = [j for j in jobs if "成都" in (j.location or "")]
    # 按公司去重（demo 只留唯一公司名）
    seen = set()
    uniq = []
    for j in jobs:
        k = f"{j.company}|{j.title}"
        if k in seen:
            continue
        seen.add(k)
        uniq.append(j)
    jobs = uniq

    print(f"\n共 {len(jobs)} 条（含真实URL {sum(1 for j in jobs if j.url)} 条）")
    p = generate_report(jobs, target_date=None)
    # 输出到独立演示文件，不覆盖正式 report.html
    demo = Path(p).parent / "demo-report.html"
    Path(p).rename(demo)
    print("演示报告:", demo)


if __name__ == "__main__":
    asyncio.run(main())
