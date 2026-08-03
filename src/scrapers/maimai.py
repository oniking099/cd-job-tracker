"""
脉脉爬虫。
搜索页：https://maimai.cn/jobs?q=keyword&city=成都
脉脉需要登录才能查看完整信息，反爬较严。
"""
from __future__ import annotations

import logging
from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


logger = logging.getLogger(__name__)
class MaimaiScraper(BaseScraper):
    platform_name = "脉脉"
    base_url = "https://maimai.cn"
    search_url = "https://maimai.cn/jobs?q={keyword}&city=%E6%88%90%E9%83%BD&page={page}"

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

                await self._human_scroll(page, times=2)
                await self._random_delay()

                content = await page.content()
                batch = self._parse_html(content, round_label)

                # 脉脉大量 JS 渲染，HTML 解析可能失败
                if not batch:
                    batch = await self._parse_json(page, round_label)

                jobs.extend(batch)

                if len(batch) < 10:
                    break

            except Exception as e:
                logger.warning(f"[maimai] 失败: {e}")
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析脉脉搜索结果 HTML"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("div.job-card") or tree.css("li.job-item")

        for item in items:
            try:
                title_el = item.css_first("a.job-title, span.title, h3")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""
                if url and not url.startswith("http"):
                    url = self.base_url + url

                company_el = item.css_first("span.company-name, a.company")
                company = company_el.text(strip=True) if company_el else ""

                salary_el = item.css_first("span.salary, span.salary-range")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                location = "成都"  # 已限定城市

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
                logger.warning(f"[maimai] 失败: {e}")
                continue

        return jobs

    async def _parse_json(self, page, round_label: str) -> list[Job]:
        """从脉脉 JS 数据提取"""
        jobs: list[Job] = []
        try:
            result = await page.evaluate("""() => {
                return window.__INITIAL_STATE__ || null;
            }""")
            if result:
                items = result.get("jobList", {}).get("list", []) or []
                for item in items:
                    job = Job(
                        platform=self.platform_name,
                        job_id=str(item.get("id", "")),
                        url=f"https://maimai.cn/job/{item.get('id', '')}",
                        title=item.get("title", ""),
                        company=item.get("companyName", ""),
                        salary_text=item.get("salary", ""),
                        location="成都",
                        scraped_at=datetime.now().isoformat(),
                        search_round=round_label,
                    )
                    jobs.append(job)
        except Exception as e:
            logger.warning(f"[maimai] JSON提取失败: {e}")
        return jobs
