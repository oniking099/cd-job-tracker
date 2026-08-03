"""
51Job/前程无忧爬虫。
搜索 API 端点：https://search.51job.com/list/000000,000000,0000,00,9,99,keyword,2,1.html
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


class Job51Scraper(BaseScraper):
    platform_name = "51Job"
    base_url = "https://www.51job.com"
    search_url = "https://search.51job.com/list/090200,000000,0000,00,9,99,{keyword},2,{page}.html"

    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        jobs: list[Job] = []
        self.context = await self._new_context()
        page = await self.context.new_page()

        for page_num in range(1, 4):  # 最多 3 页
            try:
                url = self.search_url.format(keyword=keyword, page=page_num)
                ok = await self._retry_get(page, url)

                if not ok:
                    continue

                await self._human_scroll(page, times=2)
                await self._random_delay()

                content = await page.content()
                batch = self._parse_list_html(content, round_label)
                if not batch:
                    # 尝试 JSON 数据（51Job 的 API 模式）
                    batch = await self._parse_api_json(page, keyword, page_num, round_label)

                jobs.extend(batch)

                if len(batch) < 20:
                    break  # 最后一页

            except Exception as e:
                # 单页失败不阻塞
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_list_html(self, html: str, round_label: str) -> list[Job]:
        """解析 51Job 搜索结果 HTML"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("div.joblist-item") or tree.css("div.e")

        for item in items:
            try:
                # 岗位名称和链接
                title_el = item.css_first("a[title], span.jname, p.t span a")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""
                if url and not url.startswith("http"):
                    url = self.base_url + url

                # 企业名称
                company_el = item.css_first("a.cname, span.cname, p.t a.cname")
                company = company_el.text(strip=True) if company_el else ""

                # 薪资
                salary_el = item.css_first("span.sal, span.salary")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                # 地点
                loc_el = item.css_first("span.d, p.t span.d")
                location = loc_el.text(strip=True) if loc_el else ""

                # 从 item 文本中提取更多信息
                full_text = item.text(strip=True) if hasattr(item, 'text') else ""

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

            except Exception:
                continue

        return jobs

    async def _parse_api_json(self, page, keyword: str, page_num: int, round_label: str) -> list[Job]:
        """尝试通过 51Job 内部 API 获取 JSON 数据"""
        jobs: list[Job] = []
        try:
            result = await page.evaluate("""() => {
                return window.__SEARCH_RESULT__ || window.__INITIAL_STATE__ || null;
            }""")

            if result and isinstance(result, dict):
                engine_data = result.get("engine_search_result") or result.get("result", [])
                for item in engine_data:
                    job = Job(
                        platform=self.platform_name,
                        job_id=str(item.get("jobid", "")),
                        url=item.get("job_href", ""),
                        title=item.get("job_name", ""),
                        company=item.get("company_name", ""),
                        salary_text=item.get("providesalary_text", ""),
                        location=item.get("workarea_text", ""),
                        requirements="",
                        responsibilities="",
                        scraped_at=datetime.now().isoformat(),
                        search_round=round_label,
                    )
                    jobs.append(job)
        except Exception:
            pass

        return jobs
