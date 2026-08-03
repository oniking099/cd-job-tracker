"""
猎聘爬虫。
搜索页：https://www.liepin.com/zhaopin/?city=040&key=keyword
"""
from __future__ import annotations

from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


class LiepinScraper(BaseScraper):
    platform_name = "猎聘"
    base_url = "https://www.liepin.com"
    search_url = "https://www.liepin.com/zhaopin/?city=040&dq=040&pubTime=&currentPage={page}&pageSize=40&key={keyword}"

    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        jobs: list[Job] = []
        self.context = await self._new_context()
        page = await self.context.new_page()

        for page_num in range(0, 3):  # 猎聘从 page 0 开始
            try:
                url = self.search_url.format(keyword=keyword, page=page_num)
                ok = await self._retry_get(page, url)

                if not ok:
                    continue

                await self._human_scroll(page, times=3)
                await self._random_delay()

                content = await page.content()
                batch = self._parse_html(content, round_label)

                if not batch:
                    batch = await self._parse_json(page, round_label)

                jobs.extend(batch)

                if len(batch) < 25:
                    break

            except Exception:
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析猎聘搜索结果 HTML"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("div.job-list-item") or tree.css("div.job-card")

        for item in items:
            try:
                # 岗位名
                title_el = item.css_first("h3 a, span.job-name a, a[data-promid]")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""
                if url and not url.startswith("http"):
                    url = self.base_url + url

                # 企业名
                company_el = item.css_first("p.company-name a, div.company-info a")
                company = company_el.text(strip=True) if company_el else ""

                # 薪资
                salary_el = item.css_first("span.salary-text, p.job-salary")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                # 地点
                loc_el = item.css_first("span.area, p.job-dq")
                location = loc_el.text(strip=True) if loc_el else ""

                # 猎聘通常有更多描述信息
                desc_el = item.css_first("div.job-detail-box, p.job-desc")
                desc = desc_el.text(strip=True) if desc_el else ""

                job = Job(
                    platform=self.platform_name,
                    job_id=str(hash(url))[:12] if url else str(hash(title + company))[:12],
                    url=url,
                    title=title,
                    company=company,
                    salary_text=salary_text,
                    location=location,
                    requirements=desc[:200] if desc else "",
                    responsibilities=desc[:200] if desc else "",
                    scraped_at=datetime.now().isoformat(),
                    search_round=round_label,
                )
                jobs.append(job)

            except Exception:
                continue

        return jobs

    async def _parse_json(self, page, round_label: str) -> list[Job]:
        """尝试从猎聘 JSON 数据提取"""
        jobs: list[Job] = []
        try:
            result = await page.evaluate("""() => {
                return window.__INITIAL_STATE__ || null;
            }""")
            if result:
                for item in (result.get("jobList", {}).get("list", []) or []):
                    job = Job(
                        platform=self.platform_name,
                        job_id=str(item.get("jobId", "")),
                        url=item.get("jobUrl", ""),
                        title=item.get("title", ""),
                        company=item.get("companyName", ""),
                        salary_text=item.get("salary", ""),
                        location=item.get("city", ""),
                        requirements="",
                        responsibilities="",
                        scraped_at=datetime.now().isoformat(),
                        search_round=round_label,
                    )
                    jobs.append(job)
        except Exception:
            pass
        return jobs
