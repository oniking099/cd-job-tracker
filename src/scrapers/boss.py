"""
BOSS直聘爬虫。
搜索页：https://www.zhipin.com/web/geek/job?city=101270100&query=keyword
BOSS直聘反爬最严格，需要特别处理。
"""
from __future__ import annotations

from datetime import datetime
from selectolax.parser import HTMLParser

from src.scrapers.base import BaseScraper
from src.models import Job


class BossScraper(BaseScraper):
    platform_name = "BOSS直聘"
    base_url = "https://www.zhipin.com"
    search_url = "https://www.zhipin.com/web/geek/job?city=101270100&query={keyword}&page={page}"

    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        jobs: list[Job] = []
        self.context = await self._new_context()
        page = await self.context.new_page()

        # BOSS直聘需要先访问首页建立会话
        try:
            await self._retry_get(page, self.base_url)
            await self._random_delay()
        except Exception:
            pass

        for page_num in range(1, 4):
            try:
                url = self.search_url.format(keyword=keyword, page=page_num)
                ok = await self._retry_get(page, url)

                if not ok:
                    continue

                # BOSS直聘是 SPA，等待 JS 渲染
                await page.wait_for_timeout(3000)
                await self._human_scroll(page, times=4)
                await self._random_delay()

                content = await page.content()
                batch = self._parse_html(content, round_label)

                # BOSS直聘大量数据在 JS 中
                if not batch or len(batch) < 5:
                    batch = await self._parse_json(page, round_label)

                jobs.extend(batch)

                if len(batch) < 15:
                    break

            except Exception:
                # BOSS直聘反爬严格，单页失败就降级
                if page_num == 1:
                    batch = await self._parse_with_fallback(page)
                    jobs.extend(batch)
                continue

        await page.close()
        await self.context.close()
        return jobs

    def _parse_html(self, html: str, round_label: str) -> list[Job]:
        """解析 BOSS 搜索结果 HTML"""
        jobs: list[Job] = []
        tree = HTMLParser(html)
        items = tree.css("li.job-card-wrapper") or tree.css("div.job-card-body") or tree.css("div.search-job-result li")

        for item in items:
            try:
                title_el = item.css_first("span.job-name, a.job-title, h3.name")
                if not title_el:
                    continue
                title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""

                # 企业名
                company_el = item.css_first("h3.company-name a, a.company-name, span.company-text")
                company = company_el.text(strip=True) if company_el else ""

                # 薪资
                salary_el = item.css_first("span.salary, span.red")
                salary_text = salary_el.text(strip=True) if salary_el else ""

                # 地点
                loc_el = item.css_first("span.job-area, p.job-area")
                location = loc_el.text(strip=True) if loc_el else ""

                # BOSS直聘岗位卡片可能包含 HR 状态
                hr_el = item.css_first("span.boss-online-tag")
                hr_active = bool(hr_el)

                # 要求标签
                tags = item.css("li.tag-item, span.tag-item")
                req_text = " ".join([t.text(strip=True) for t in tags if hasattr(t, 'text')]) if tags else ""

                # 详情链接 - BOSS直聘的 ID 通常在其他属性中
                link_el = item.css_first("a.job-card-left, a[ka^='search_list']")
                url = ""
                if link_el and hasattr(link_el, 'attrs'):
                    url = link_el.attrs.get("href", "")
                    if url and not url.startswith("http"):
                        url = self.base_url + url

                job_id = str(hash(url))[:12] if url else str(hash(title + company))[:12]

                job = Job(
                    platform=self.platform_name,
                    job_id=job_id,
                    url=url,
                    title=title,
                    company=company,
                    salary_text=salary_text,
                    location=location,
                    requirements=req_text[:200] if req_text else "",
                    hr_active=hr_active,
                    scraped_at=datetime.now().isoformat(),
                    search_round=round_label,
                )
                jobs.append(job)

            except Exception:
                continue

        return jobs

    async def _parse_json(self, page, round_label: str) -> list[Job]:
        """从 BOSS 直聘的 JS 数据中提取"""
        jobs: list[Job] = []
        try:
            result = await page.evaluate("""() => {
                return window.__NEXT_DATA__ || window.__INITIAL_STATE__ || null;
            }""")

            if result and isinstance(result, dict):
                props = result.get("props", {}) or result
                page_props = props.get("pageProps", {}) or props
                job_list = (
                    page_props.get("jobList", []) or
                    page_props.get("searchResult", {}).get("jobList", []) or
                    []
                )

                for item in job_list:
                    boss_info = item.get("bossInfo", {}) or {}
                    job = Job(
                        platform=self.platform_name,
                        job_id=str(item.get("jobId", item.get("encryptJobId", ""))),
                        url=f"https://www.zhipin.com/job_detail/{item.get('encryptJobId', '')}.html",
                        title=item.get("jobName", ""),
                        company=item.get("brandName", ""),
                        salary_text=item.get("salaryDesc", ""),
                        location=item.get("cityName", "") + (item.get("areaDistrict", "") or ""),
                        requirements=", ".join(item.get("jobLabels", []) or []),
                        hr_active=boss_info.get("online", False),
                        scraped_at=datetime.now().isoformat(),
                        search_round=round_label,
                    )
                    jobs.append(job)
        except Exception:
            pass
        return jobs
