"""
职友集爬虫（聚合引擎）。
搜索页：https://www.jobui.com/jobs?cityKw=成都&q=keyword
职友集聚合多个平台的招聘信息，一个顶多个。
"""
from __future__ import annotations

from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


class JobuiScraper(BaseScraper):
    platform_name = "职友集"
    base_url = "https://www.jobui.com"
    search_url = "https://www.jobui.com/jobs?cityKw=%E6%88%90%E9%83%BD&q={keyword}&page={page}"

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

            except Exception:
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析职友集搜索结果"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("div.job-item") or tree.css("div.job-search-result li")

        for item in items:
            try:
                title_el = item.css_first("a.job-name, h3 a, a[href*='jobs']")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""
                if url and not url.startswith("http"):
                    url = self.base_url + url

                company_el = item.css_first("a.company-name, span.company")
                company = company_el.text(strip=True) if company_el else ""

                salary_el = item.css_first("span.salary, span.red")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                loc_el = item.css_first("span.city, span.address")
                location = loc_el.text(strip=True) if loc_el else ""

                # 职友集通常显示来源平台
                source_el = item.css_first("span.source, span.platform")
                source = source_el.text(strip=True) if source_el else ""

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

            except Exception:
                continue

        return jobs
