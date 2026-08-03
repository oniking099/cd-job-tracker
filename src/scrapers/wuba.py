"""
58同城招聘爬虫。
搜索页：https://cd.58.com/job/?key=keyword
"""
from __future__ import annotations

import logging
from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


logger = logging.getLogger(__name__)
class WubaScraper(BaseScraper):
    platform_name = "58同城"
    base_url = "https://cd.58.com"
    search_url = "https://cd.58.com/job/?key={keyword}&pn={page}&final=1&jump=1"

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

                await self._random_delay()
                content = await page.content()
                batch = self._parse_html(content, round_label)
                jobs.extend(batch)

                if len(batch) < 15:
                    break

            except Exception as e:
                logger.warning(f"[wuba] 失败: {e}")
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析58同城搜索结果"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("li.job_item") or tree.css("div.job-list-item")

        for item in items:
            try:
                title_el = item.css_first("a.t, span.name a, a.job-name")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""

                company_el = item.css_first("a.fl, a.comp_name, span.company-name")
                company = company_el.text(strip=True) if company_el else ""

                salary_el = item.css_first("span.salary, b.price, p.salary")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                loc_el = item.css_first("span.address, span.area")
                location = loc_el.text(strip=True) if loc_el else ""

                job = Job(
                    platform=self.platform_name,
                    job_id=str(hash(url))[:12] if url else str(hash(title + company))[:12],
                    url=url,
                    title=title,
                    company=company,
                    salary_text=salary_text,
                    location=location,
                    scraped_at=datetime.now().isoformat(),
                    search_round=round_label,
                )
                jobs.append(job)

            except Exception as e:
                logger.warning(f"[wuba] 失败: {e}")
                continue

        return jobs
