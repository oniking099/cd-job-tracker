"""
拉勾网爬虫。
搜索页：https://www.lagou.com/wn/jobs?city=成都&kd=keyword
"""
from __future__ import annotations

import logging
from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


logger = logging.getLogger(__name__)
class LagouScraper(BaseScraper):
    platform_name = "拉勾"
    base_url = "https://www.lagou.com"
    search_url = "https://www.lagou.com/wn/jobs?city=%E6%88%90%E9%83%BD&kd={keyword}&pn={page}"

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
                batch = self._parse_html(content, round_label)

                # 拉勾大量使用 JS 渲染，JSON 提取更可靠
                if not batch or len(batch) < 5:
                    batch = await self._parse_json(page, round_label)

                jobs.extend(batch)

                if len(batch) < 15:
                    break

            except Exception as e:
                logger.warning(f"[lagou] 失败: {e}")
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析拉勾搜索结果 HTML"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("li.con_list_item") or tree.css("div.job-item") or tree.css("div.position-list-item")

        for item in items:
            try:
                # 岗位名
                title_el = item.css_first("h3 a, span.position-name, a.position_link")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
                url = title_el.attrs.get("href", "") if hasattr(title_el, 'attrs') else ""

                # 企业名
                company_el = item.css_first("div.company_name a, span.company-name")
                company = company_el.text(strip=True) if company_el else ""

                # 薪资
                salary_el = item.css_first("span.money, span.salary")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                # 地点
                loc_el = item.css_first("div.add em, span.work_addr")
                location = loc_el.text(strip=True) if loc_el else "成都"

                # 要求
                req_el = item.css_first("div.li_b_l, div.require")
                req_text = req_el.text(strip=True) if req_el else ""

                job = Job(
                    platform=self.platform_name,
                    job_id=str(hash(url))[:12] if url else str(hash(title + company))[:12],
                    url=url,
                    title=title,
                    company=company,
                    salary_text=salary_text,
                    location=location,
                    requirements=req_text[:200] if req_text else "",
                    scraped_at=datetime.now().isoformat(),
                    search_round=round_label,
                )
                jobs.append(job)

            except Exception as e:
                logger.warning(f"[lagou] 失败: {e}")
                continue

        return jobs

    async def _parse_json(self, page, round_label: str) -> list[Job]:
        """尝试从拉勾 JSON 数据提取"""
        jobs: list[Job] = []
        try:
            result = await page.evaluate("""() => {
                return window.__INITIAL_STATE__ || null;
            }""")
            if result:
                position_result = result.get("positionResult") or {}
                items = position_result.get("result", []) or []
                for item in items:
                    job = Job(
                        platform=self.platform_name,
                        job_id=str(item.get("positionId", "")),
                        url=f"https://www.lagou.com/jobs/{item.get('positionId', '')}.html",
                        title=item.get("positionName", ""),
                        company=item.get("companyFullName", item.get("companyName", "")),
                        salary_text=item.get("salary", ""),
                        location=item.get("city", "") + (item.get("district", "") or ""),
                        requirements=",".join(item.get("positionLables", []) or []),
                        responsibilities="",
                        scraped_at=datetime.now().isoformat(),
                        search_round=round_label,
                    )
                    jobs.append(job)
        except Exception as e:
            logger.warning(f"[lagou] JSON提取失败: {e}")
        return jobs
