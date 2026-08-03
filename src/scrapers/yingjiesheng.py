"""
应届生求职网爬虫。
搜索页：https://search.yingjiesheng.com/search?keyword=keyword&city=成都
注意：应届生求职网以校招为主，需重点过滤。
"""
from __future__ import annotations

import logging
from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


logger = logging.getLogger(__name__)
class YingjieshengScraper(BaseScraper):
    platform_name = "应届生求职网"
    base_url = "https://www.yingjiesheng.com"
    search_url = "https://search.yingjiesheng.com/search?keyword={keyword}&city=%E6%88%90%E9%83%BD&page={page}"

    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        jobs: list[Job] = []
        self.context = await self._new_context()
        page = await self.context.new_page()

        for page_num in range(1, 3):
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
                logger.warning(f"[yingjiesheng] 失败: {e}")
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析应届生求职网搜索结果"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("div.job-item") or tree.css("li.job-info") or tree.css("div.result-item")

        for item in items:
            try:
                title_el = item.css_first("a.job-name, a.title, h3 a")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""
                if url and not url.startswith("http"):
                    url = self.base_url + url

                company_el = item.css_first("a.company, span.company-name")
                company = company_el.text(strip=True) if company_el else ""

                salary_el = item.css_first("span.salary, span.pay")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                loc_el = item.css_first("span.city, span.location")
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
                logger.warning(f"[yingjiesheng] 失败: {e}")
                continue

        return jobs
