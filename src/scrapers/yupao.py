"""
鱼泡直聘爬虫。
搜索页：https://www.yupao.com/jobs?city=成都&keyword=keyword
"""
from __future__ import annotations

from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


class YupaoScraper(BaseScraper):
    platform_name = "鱼泡直聘"
    base_url = "https://www.yupao.com"
    search_url = "https://www.yupao.com/jobs?city=%E6%88%90%E9%83%BD&keyword={keyword}&page={page}"

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

                await self._human_scroll(page, times=2)
                await self._random_delay()

                content = await page.content()
                batch = self._parse_html(content, round_label)
                jobs.extend(batch)

                if len(batch) < 20:
                    break

            except Exception:
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析鱼泡直聘搜索结果"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("div.job-item") or tree.css("li.job-card")

        for item in items:
            try:
                title_el = item.css_first("a.job-title, h3.title a, span.job-name")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""
                if url and not url.startswith("http"):
                    url = self.base_url + url

                company_el = item.css_first("a.company-name, span.company, div.company")
                company = company_el.text(strip=True) if company_el else ""

                salary_el = item.css_first("span.salary, span.money, span.red")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                loc_el = item.css_first("span.location, span.address, span.area")
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

            except Exception:
                continue

        return jobs
