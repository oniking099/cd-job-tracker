"""
体制内公告型爬虫（2026-09-05 用户要求补强事业单位/国企央企社招渠道）。

- ScptaScraper：四川省人社厅人事考试专栏·事业单位公开招聘
  （scpta.com.cn/front/News/List/67，列表 JS 渲染，playwright 渲染后解析）
- Rc5Scraper：王才网成都站（5rc.com，成都本地招聘公告流）

公告型数据源设计（与岗位搜索型平台不同）：
- 与关键词无关（抓公告流），同轮仅真实抓取一次（模块级去重），后续调用返回空
- 粒度是"公告"而非"岗位"：title=公告标题、company=标题解析的发布单位、
  url=公告原文页；公告正文由 detail.py 详情富集抓取，专业/资格过滤自然作用
  （正文含"专业不限"放行、"35周岁以下/应届"拦截——与用户规则一致）
- 流程类公示（拟聘公示/总成绩/体检安排）不是招聘公告，直接丢弃
- location 从标题解析：含"成都"→成都；省级单位（四川省XX厅/局）默认成都
  （省属单位驻地绝大多数在成都）；解析不出成都信号的丢弃（宁缺毋滥）
"""
from __future__ import annotations

import hashlib
import logging
import re

from selectolax.parser import HTMLParser

from src.config import bjt_today
from src.models import Job
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# 同轮已抓标记：platform -> "date|round_label"（公告流与关键词无关，
# 每轮 12 个关键词会重复调 search，只放第一次真实抓取）
_NOTICE_FETCHED: dict[str, str] = {}


# 公告标题清洗：去"316人丨"式人数前缀
_HEAD_NOISE_RE = re.compile(r"^\s*[\d,，~·]+\s*(?:人|名|个岗?位?)?\s*[丨|:：\-—]\s*")
# 流程类公示（非招聘公告）：拟聘公示/成绩排名/体检安排/考察/递补
_PROCEDURE_RE = re.compile(r"公示|成绩|体检|考察|递补|排名|准考证|延期|取消")
# 招聘类关键词（公告必须命中其一）
_RECRUIT_RE = re.compile(r"招聘|选调|引进|遴选|招募|招考|招聘会")
# 发布单位解析（标题里第一个机构名）
_ORG_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,18}?(?:厅|局|委员会|管理局|国资委|集团(?:公司)?|有限公司|"
    r"研究中心|研究所|研究院|学院|大学|学校|医院|电视台|广播电视台|报社|出版社|"
    r"气象局|生态环境厅|发展和改革委员会|人力资源和社会保障局))"
)
_DATE_RE = re.compile(r"(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})")


def _clean_title(raw: str) -> str:
    return _HEAD_NOISE_RE.sub("", (raw or "").strip())


def _is_recruit_notice(title: str) -> bool:
    """招聘公告判定：命中招聘词 且 非流程公示。"""
    if _PROCEDURE_RE.search(title):
        return False
    return bool(_RECRUIT_RE.search(title))


def parse_notice_title(title: str) -> dict:
    """公告标题 → {title, company, location}。解析不出成都信号返回 location=''（上层丢弃）。"""
    t = _clean_title(title)
    org = _ORG_RE.search(t)
    company = org.group(1) if org else ""
    if "成都" in t:
        location = "成都"
    elif re.search(r"四川省?[\u4e00-\u9fa5]{0,10}(厅|局|委员会)", t) or company.startswith("四川"):
        # 省属单位驻地绝大多数在成都（省气象局/生态环境厅等）
        location = "成都（省属）"
    else:
        location = ""
    return {"title": t, "company": company or "四川省属单位", "location": location}


def _notice_jobs(items: list[dict], platform: str, round_label: str) -> list[Job]:
    """公告条目列表 → Job 列表（过滤流程公示 + 非成都 + 同 URL 去重）。"""
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    for it in items:
        info = parse_notice_title(it.get("title", ""))
        url = (it.get("url") or "").strip()
        if not url.startswith("http") or url in seen_urls:
            continue
        if not _is_recruit_notice(info["title"]):
            continue
        if not info["location"]:
            continue
        seen_urls.add(url)
        job_id = f"notice-{hashlib.md5(url.encode()).hexdigest()[:12]}"
        jobs.append(Job(
            platform=platform,
            job_id=job_id,
            url=url,
            title=info["title"][:120],
            company=info["company"],
            location=info["location"],
            salary_text="",           # 公告无薪资 → 薪资过滤按"无文本"放行
            posted_date=it.get("date", ""),
            scraped_at=bjt_today(),
            search_round=round_label,
        ))
    return jobs


class _NoticeScraperBase(BaseScraper):
    """公告流爬虫基类：同轮去重 + 渲染抓取 + 列表解析。"""

    notice_url: str = ""
    # 列表链接选择器（子类给正则：匹配 href 的公告详情链接）
    link_href_re: re.Pattern = re.compile(r"$^")

    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        key = f"{bjt_today()}|{round_label}"
        if _NOTICE_FETCHED.get(self.platform_name) == key:
            return []  # 公告流与关键词无关，同轮只抓一次
        _NOTICE_FETCHED[self.platform_name] = key

        self.context = await self._new_context()
        page = await self.context.new_page()
        try:
            await self._retry_get(page, self.notice_url)
            # 列表 JS 渲染（scpta）/SSR（5rc）统一等一拍
            await page.wait_for_timeout(3500)
            content = await page.content()
            items = self._parse_list(content, page.url)
            jobs = _notice_jobs(items, self.platform_name, round_label)
            print(f"  [{self.platform_name}] 公告流: {len(jobs)} 条招聘公告（关键词无关，本轮仅此一次）")
            return jobs
        except Exception as e:
            logger.warning(f"[{self.platform_name}] 公告流抓取失败: {e}")
            return []
        finally:
            try:
                await page.close()
                await self.context.close()
            except Exception:
                pass

    def _parse_list(self, html: str, base_url: str) -> list[dict]:
        """解析公告列表：a[href 匹配详情链接] → {title, url, date}。"""
        tree = HTMLParser(html)
        out: list[dict] = []
        seen: set[str] = set()
        for a in tree.css("a[href]"):
            href = a.attributes.get("href", "") or ""
            if not self.link_href_re.search(href):
                continue
            # 绝对化
            if href.startswith("/"):
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            title = (a.text() or "").strip().replace("\n", " ")
            title = re.sub(r"\s+", " ", title)
            if len(title) < 8 or href in seen:
                continue
            seen.add(href)
            # 日期取链接周边文本（列表项常见 yyyy-mm-dd）
            parent_text = a.parent.text() if a.parent else ""
            dm = _DATE_RE.search(parent_text or "")
            out.append({"title": title, "url": href, "date": dm.group(1) if dm else ""})
        return out


class ScptaScraper(_NoticeScraperBase):
    """四川省人社厅人事考试专栏·事业单位公开招聘（官方源头，省属事业编公告）。

    实测（2026-09-05 Playwright）：专栏列表 /front/News/List/67 渲染后约 30 条，
    详情链接形态 /front/News/info/{hash}；列表混有流程公示，由 _is_recruit_notice 过滤。
    """
    platform_name = "四川人事考试网"
    notice_url = "https://www.scpta.com.cn/front/News/List/67"
    link_href_re = re.compile(r"/front/News/info/")


class Rc5Scraper(_NoticeScraperBase):
    """王才网成都站（5rc.com）：成都本地招聘公告流（事业单位/国企/社区公告聚合）。

    实测（2026-09-05）：首页公告流为成都本地（司法局/社区卫生服务中心/区属国企等），
    详情链接形态 /zhaokao/{id}/；tag 页（事业单位/国企）是全国混排，不用。
    """
    platform_name = "王才网成都站"
    notice_url = "https://www.5rc.com/"
    link_href_re = re.compile(r"^/zhaokao/\d+/?$|^https?://[^/]*5rc\.com/zhaokao/\d+/?$")
