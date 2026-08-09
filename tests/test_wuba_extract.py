"""
58同城 DOM 提取器 单元测试（plan B1 增量，参照猎聘/中华英才单层模式）。

- _extract_wuba_from_dom：DOM 直取 title/company/url 三元组、异常安全回退
- 集成：extract_jobs_from_page 对 58同城走 DOM 优先（不经 OCR/标题匹配回填）

⚠️ 提取器本身未经实页验证（cd.58.com 搜索页对 MCP 浏览器直接跳登录墙，
2026-08-09 实测）；选择器沿用旧 scrapers/wuba.py 已验证结果。这里 mock
evaluate 返回值，验证 Python 侧的返回/异常兜底/集成逻辑。
"""
from __future__ import annotations

import pytest

from src.agent.extract import _extract_wuba_from_dom, extract_jobs_from_page


class _FakeStaticPage:
    """模拟页面：evaluate 固定返回预设 DOM 提取结果（或抛异常）。"""

    def __init__(self, dom_out=None, raises: bool = False):
        self.dom_out = dom_out if dom_out is not None else []
        self.raises = raises

    async def evaluate(self, expr, arg=None):
        if self.raises:
            raise RuntimeError("mock evaluate boom")
        return self.dom_out


# 同标题多公司并排：OCR+标题回填会错配 URL，DOM 直取各归各
WUBA_CARDS = [
    {
        "title": "环境监测员", "company": "甲环境科技公司",
        "url": "https://cd.58.com/huanjing/1001.shtml", "salary_text": "4-6千", "location": "成都-锦江区",
    },
    {
        "title": "环境监测员", "company": "乙检测有限公司",
        "url": "https://cd.58.com/huanjing/1002.shtml", "salary_text": "5-8千", "location": "成都-双流区",
    },
]


class TestWubaDomExtract:
    @pytest.mark.asyncio
    async def test_same_title_multi_company_aligned(self):
        """核心效果：同标题多公司各取各的 URL，三元组不错配。"""
        out = await _extract_wuba_from_dom(_FakeStaticPage(WUBA_CARDS))
        assert len(out) == 2
        assert out[0]["title"] == "环境监测员"
        assert out[0]["company"] == "甲环境科技公司"
        assert out[0]["url"] == "https://cd.58.com/huanjing/1001.shtml"
        assert out[1]["company"] == "乙检测有限公司"
        assert out[1]["url"] == "https://cd.58.com/huanjing/1002.shtml"

    @pytest.mark.asyncio
    async def test_evaluate_exception_returns_empty(self):
        """page.evaluate 抛异常（风控/改版）-> 返回空，上游安全回退 OCR。"""
        out = await _extract_wuba_from_dom(_FakeStaticPage(raises=True))
        assert out == []

    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        out = await _extract_wuba_from_dom(_FakeStaticPage([]))
        assert out == []

    @pytest.mark.asyncio
    async def test_extract_jobs_from_page_wuba_dom_first(self):
        """集成：58同城走 DOM 优先，直接产出 Job（不经 OCR/标题匹配回填）。"""
        jobs = await extract_jobs_from_page(_FakeStaticPage(WUBA_CARDS), "58同城", "12")
        assert len(jobs) == 2
        assert all(j.platform == "58同城" for j in jobs)
        assert jobs[0].company == "甲环境科技公司"
        assert jobs[0].url == "https://cd.58.com/huanjing/1001.shtml"
        assert jobs[1].location == "成都-双流区"
