"""
智联招聘爬虫。
搜索页：https://sou.zhaopin.com/?jl=489&kw=keyword&p=1
"""
from __future__ import annotations

import logging
import json
import re
from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


logger = logging.getLogger(__name__)
class ZhilianScraper(BaseScraper):
    platform_name = "智联招聘"
    base_url = "https://www.zhaopin.com"
    search_url = "https://sou.zhaopin.com/?jl=489&kw={keyword}&p={page}"

    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        jobs: list[Job] = []
        self.context = await self._new_context()
        page = await self.context.new_page()

        for page_num in range(1, 4):
            try:
                url = self.search_url.format(keyword=keyword, page=page_num)
                ok = await self._retry_get(page, url)

                if not ok:
                    continue

                await self._human_scroll(page, times=3)
                await self._random_delay()

                content = await page.content()

                # 先用 HTML 解析
                batch = self._parse_html(content, round_label)

                # 如果 HTML 解析失败，尝试 JSON
                if not batch:
                    batch = await self._parse_json(page, round_label)

                jobs.extend(batch)

                if len(batch) < 15:
                    break

            except Exception as e:
                logger.warning(f"[zhilian] 失败: {e}")
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析智联搜索结果 HTML"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("div.joblist-box__item") or tree.css("div.content_details_div")

        for item in items:
            try:
                title_el = item.css_first("a.joblist-box__item-title, span.jobName a, a.jobName")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""

                # 企业名
                company_el = item.css_first("a.company_title, span.company__title, a.companyName")
                company = company_el.text(strip=True) if company_el else ""

                # 薪资
                salary_el = item.css_first("span.salary, p.salary, span.salaryText")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                # 地点
                loc_el = item.css_first("span.work__city, span.joblist-box__item-desc span:first-child, p.info span:first-child")
                location = loc_el.text(strip=True) if loc_el else ""

                job = Job(
                    platform=self.platform_name,
                    job_id=str(hash(url))[:12] if url else str(hash(title + company))[:12],
                    url=url,
                    title=title,
                    company=company,
                    salary_text=salary_text,
                    location=location,
                    requirements="",
                    responsibilities="",
                    scraped_at=datetime.now().isoformat(),
                    search_round=round_label,
                )
                jobs.append(job)

            except Exception as e:
                logger.warning(f"[zhilian] 失败: {e}")
                continue

        return jobs

    async def _parse_json(self, page, round_label: str) -> list[Job]:
        """尝试从智联的 JSON 数据中提取"""
        jobs: list[Job] = []
        try:
            result = await page.evaluate("""() => {
                return window.__INITIAL_STATE__ || null;
            }""")
            if result:
                items = (
                    result.get("search", {}).get("list", []) or
                    result.get("positionList", {}).get("result", []) or
                    []
                )
                for item in items:
                    job = Job(
                        platform=self.platform_name,
                        job_id=str(item.get("number", item.get("positionId", ""))),
                        url=item.get("positionURL", item.get("shareUrl", "")),
                        title=item.get("name", item.get("jobName", "")),
                        company=item.get("company", {}).get("name", item.get("companyName", "")),
                        salary_text=item.get("salary60", item.get("salary", "")),
                        location=item.get("city", {}).get("display", item.get("workCity", "")),
                        requirements="",
                        responsibilities="",
                        scraped_at=datetime.now().isoformat(),
                        search_round=round_label,
                    )
                    jobs.append(job)
        except Exception as e:
            logger.warning(f"[zhilian] JSON提取失败: {e}")
        return jobs
