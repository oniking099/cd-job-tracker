"""
拉勾 DOM 提取器 单元测试（plan B1 增量，参照智联双层模式）。

- _extract_lagou_from_dom：SSR JSON 优先、DOM 锚点扫描回退、双空/异常兜底
- 集成：extract_jobs_from_page 对拉勾走 DOM 优先（不经 OCR/标题匹配回填）

⚠️ 提取器本身未经实页验证（拉勾滑动验证风控拦截 MCP 浏览器，2026-08-09）；
SSR 字段路径沿用旧 scrapers/lagou.py 已验证结果。这里 mock evaluate 返回值，
验证 Python 侧的分派/兜底/集成逻辑。
"""
from __future__ import annotations

import pytest

from src.agent.extract import _extract_lagou_from_dom, extract_jobs_from_page


class _FakeLagouPage:
    """模拟拉勾页面：按 evaluate 的 JS 内容返回 SSR JSON 结果或 DOM 结果（或抛异常）。"""

    def __init__(self, ssr_out=None, dom_out=None, raises: bool = False):
        self.ssr_out = ssr_out
        self.dom_out = dom_out
        self.raises = raises

    async def evaluate(self, expr, arg=None):
        if self.raises:
            raise RuntimeError("mock evaluate boom")
        if "__INITIAL_STATE__" in expr:
            return self.ssr_out if self.ssr_out is not None else []
        return self.dom_out if self.dom_out is not None else []


# 拉勾实测痛点（旧 scraper 时代）：同标题多公司并排，SSR 字段 positionId/companyFullName 成组
LAGOU_SSR = [
    {
        "title": "AI算法工程师", "company": "甲智能科技有限公司",
        "url": "https://www.lagou.com/jobs/1001.html", "salary_text": "20-40k", "location": "成都高新区",
    },
    {
        "title": "AI算法工程师", "company": "乙数据科技有限公司",
        "url": "https://www.lagou.com/jobs/1002.html", "salary_text": "15-25k", "location": "成都武侯区",
    },
]

LAGOU_DOM = [
    {
        "title": "气象分析师", "company": "某气象科技公司",
        "url": "https://www.lagou.com/jobs/2001.html", "salary_text": "10-15k", "location": "成都",
    },
]


class TestLagouDomExtract:
    @pytest.mark.asyncio
    async def test_ssr_json_primary_same_title_multi_company(self):
        """SSR JSON 有数据时直接返回；同标题多公司各取各的 URL（根治错配核心效果）。"""
        out = await _extract_lagou_from_dom(_FakeLagouPage(ssr_out=LAGOU_SSR))
        assert len(out) == 2
        assert out[0]["title"] == "AI算法工程师"
        assert out[0]["company"] == "甲智能科技有限公司"
        assert out[0]["url"] == "https://www.lagou.com/jobs/1001.html"
        assert out[1]["company"] == "乙数据科技有限公司"
        assert out[1]["url"] == "https://www.lagou.com/jobs/1002.html"

    @pytest.mark.asyncio
    async def test_dom_fallback_when_ssr_empty(self):
        """SSR JSON 为空（页面改版无 __INITIAL_STATE__）时回退 DOM 锚点扫描。"""
        out = await _extract_lagou_from_dom(_FakeLagouPage(ssr_out=[], dom_out=LAGOU_DOM))
        assert len(out) == 1
        assert out[0]["title"] == "气象分析师"
        assert out[0]["url"] == "https://www.lagou.com/jobs/2001.html"

    @pytest.mark.asyncio
    async def test_both_empty_returns_empty(self):
        """SSR + DOM 都空 -> 返回空（上游回退 OCR，安全不丢数据）。"""
        out = await _extract_lagou_from_dom(_FakeLagouPage(ssr_out=[], dom_out=[]))
        assert out == []

    @pytest.mark.asyncio
    async def test_ssr_missing_position_id_kept(self):
        """SSR 缺 positionId（构造不出详情 URL）时仍保留该岗位，url 置空不丢岗位。"""
        ssr = [{"title": "环保工程师", "company": "某环保公司", "url": "",
                "salary_text": "面议", "location": "成都"}]
        out = await _extract_lagou_from_dom(_FakeLagouPage(ssr_out=ssr))
        assert len(out) == 1
        assert out[0]["url"] == ""
        assert out[0]["company"] == "某环保公司"

    @pytest.mark.asyncio
    async def test_evaluate_exception_returns_empty(self):
        """page.evaluate 抛异常（风控/改版）-> 返回空，上游安全回退 OCR。"""
        out = await _extract_lagou_from_dom(_FakeLagouPage(raises=True))
        assert out == []

    @pytest.mark.asyncio
    async def test_extract_jobs_from_page_lagou_dom_first(self):
        """集成：拉勾走 DOM 优先，直接产出 Job（不经 OCR/标题匹配回填）。"""
        jobs = await extract_jobs_from_page(_FakeLagouPage(ssr_out=LAGOU_SSR), "拉勾", "12")
        assert len(jobs) == 2
        assert all(j.platform == "拉勾" for j in jobs)
        assert jobs[0].company == "甲智能科技有限公司"
        assert jobs[0].url == "https://www.lagou.com/jobs/1001.html"
        assert jobs[1].url == "https://www.lagou.com/jobs/1002.html"
