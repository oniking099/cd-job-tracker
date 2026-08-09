"""
智联 DOM 提取器 + 同标题多公司消歧 单元测试。

- _extract_zhilian_from_dom：SSR JSON 优先、DOM 回退、双空兜底
- _merge_card_urls 同标题多公司：按 company 消歧各回填自己的 URL（深度重构核心效果）
"""
from __future__ import annotations

import pytest

from src.agent.extract import _extract_zhilian_from_dom, _merge_card_urls, extract_jobs_from_page


class _FakeZhilianPage:
    """模拟智联页面：按 evaluate 的 JS 内容返回 SSR JSON 结果或 DOM 结果。

    SSR 字段映射（name->title, company.name->company 等）沿用 zhilian.py._parse_json
    验证过的路径；这里 mock 直接返回 JS 处理后的 out 列表，验证 Python 侧分派/返回逻辑。
    """

    def __init__(self, ssr_out=None, dom_out=None):
        self.ssr_out = ssr_out
        self.dom_out = dom_out

    async def evaluate(self, expr, arg=None):
        if "__INITIAL_STATE__" in expr:
            return self.ssr_out if self.ssr_out is not None else []
        return self.dom_out if self.dom_out is not None else []


class TestZhilianDomExtract:
    @pytest.mark.asyncio
    async def test_ssr_json_primary(self):
        """SSR JSON 有数据时直接返回（title/company/url 成组，根治错配）。"""
        ssr = [{
            "title": "AI工程师", "company": "某AI公司",
            "url": "https://jobs.zhaopin.com/p1", "salary_text": "15-25K", "location": "成都",
        }]
        out = await _extract_zhilian_from_dom(_FakeZhilianPage(ssr_out=ssr))
        assert len(out) == 1
        assert out[0]["title"] == "AI工程师"
        assert out[0]["company"] == "某AI公司"
        assert out[0]["url"] == "https://jobs.zhaopin.com/p1"

    @pytest.mark.asyncio
    async def test_dom_fallback_when_ssr_empty(self):
        """SSR JSON 为空时回退 DOM 卡片选择器。"""
        dom = [{
            "title": "气象工程师", "company": "气象局",
            "url": "https://jobs.zhaopin.com/p2", "salary_text": "", "location": "成都",
        }]
        out = await _extract_zhilian_from_dom(_FakeZhilianPage(ssr_out=[], dom_out=dom))
        assert len(out) == 1
        assert out[0]["title"] == "气象工程师"

    @pytest.mark.asyncio
    async def test_both_empty_returns_empty(self):
        """SSR + DOM 都空 -> 返回空（上游回退 OCR，安全）。"""
        out = await _extract_zhilian_from_dom(_FakeZhilianPage(ssr_out=[], dom_out=[]))
        assert out == []

    @pytest.mark.asyncio
    async def test_ssr_missing_company_kept(self):
        """SSR 缺 company 字段时仍返回该岗位（company 为空，不丢岗位）。"""
        ssr = [{"title": "环保工程师", "company": "", "url": "https://jobs.zhaopin.com/p3"}]
        out = await _extract_zhilian_from_dom(_FakeZhilianPage(ssr_out=ssr))
        assert len(out) == 1
        assert out[0]["company"] == ""

    @pytest.mark.asyncio
    async def test_extract_jobs_from_page_zhilian_dom_first(self):
        """集成：智联走 DOM 优先，直接产出 Job（不经 OCR/标题匹配回填）。"""
        ssr = [{
            "title": "AI工程师", "company": "某AI公司",
            "url": "https://jobs.zhaopin.com/p1", "salary_text": "15-25K", "location": "成都",
        }]
        jobs = await extract_jobs_from_page(_FakeZhilianPage(ssr_out=ssr), "智联招聘", "12")
        assert len(jobs) == 1
        assert jobs[0].title == "AI工程师"
        assert jobs[0].company == "某AI公司"
        assert jobs[0].platform == "智联招聘"

    # DOM 空时回退 OCR 链路需真实 Playwright page（viewport_size/screenshot），
    # 非简单 mock 可覆盖；该路径由 _extract_zhilian_from_dom 双空返回 [] 保证安全回退。


class TestSameTitleMultiCompanyDisambiguation:
    """止血 A3 + 深度重构 company 采集：同标题多公司按 company 各回填自己的 URL。"""

    def test_disambiguate_by_company(self):
        # 51Job/职友集 同标题多公司：collector 现带 company，按公司精确匹配各回填
        jobs = [
            {"title": "AI工程师", "company": "甲公司"},
            {"title": "AI工程师", "company": "乙公司"},
        ]
        links = [
            {"text": "AI工程师", "href": "https://jobs.51job.com/c/1.html", "company": "甲公司"},
            {"text": "AI工程师", "href": "https://jobs.51job.com/c/2.html", "company": "乙公司"},
        ]
        out = _merge_card_urls(jobs, links)
        assert out[0]["url"] == "https://jobs.51job.com/c/1.html"
        assert out[1]["url"] == "https://jobs.51job.com/c/2.html"

    def test_no_company_match_no_backfill(self):
        # 公司匹配不上任一候选 -> 宁缺毋滥不回填（卡片显示"暂无链接"而非错配别家）
        jobs = [{"title": "AI工程师", "company": "丙公司"}]
        links = [
            {"text": "AI工程师", "href": "https://jobs.51job.com/c/1.html", "company": "甲公司"},
            {"text": "AI工程师", "href": "https://jobs.51job.com/c/2.html", "company": "乙公司"},
        ]
        out = _merge_card_urls(jobs, links)
        assert "url" not in out[0]

    def test_company_contains_match(self):
        # 公司名一方完整包含另一方（如"XX科技有限公司" vs "XX科技"）且长度接近 -> 匹配
        jobs = [{"title": "算法工程师", "company": "兴蓉环境科技有限公司"}]
        links = [
            {"text": "算法工程师", "href": "https://jobs.51job.com/c/9.html", "company": "兴蓉环境科技"},
        ]
        out = _merge_card_urls(jobs, links)
        assert out[0]["url"] == "https://jobs.51job.com/c/9.html"
