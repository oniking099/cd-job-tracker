"""
猎聘 / 中华英才 DOM 提取器 单元测试（崩溃恢复后补齐 plan B3 的测试要求）。

- _extract_liepin_from_dom：DOM 直取 title/company/url 三元组、异常安全回退
- _extract_chinahr_from_dom：同上（官方类名 compony 拼写、info 第 3 项为地点）
- 集成：extract_jobs_from_page 对两平台走 DOM 优先（不经 OCR/标题匹配回填）

两提取器均只有一次 page.evaluate（纯 DOM，无 SSR 分支），
mock 直接返回 JS 处理后的 out 列表，验证 Python 侧返回/异常兜底逻辑。
"""
from __future__ import annotations

import pytest

from src.agent.extract import (
    _extract_chinahr_from_dom,
    _extract_liepin_from_dom,
    extract_jobs_from_page,
)


class _FakeStaticPage:
    """模拟页面：evaluate 固定返回预设 DOM 提取结果（或抛异常）。"""

    def __init__(self, dom_out=None, raises: bool = False):
        self.dom_out = dom_out if dom_out is not None else []
        self.raises = raises

    async def evaluate(self, expr, arg=None):
        if self.raises:
            raise RuntimeError("mock evaluate boom")
        return self.dom_out


# 猎聘实测痛点：同标题多公司并排（"AI人工智能算法工程师"3 家），OCR+标题回填会错配 URL
LIEPIN_CARDS = [
    {
        "title": "AI人工智能算法工程师", "company": "甲科技公司",
        "url": "https://www.liepin.com/job/1001", "salary_text": "15-25k", "location": "成都-高新区",
    },
    {
        "title": "AI人工智能算法工程师", "company": "乙智能公司",
        "url": "https://www.liepin.com/job/1002", "salary_text": "薪资面议", "location": "成都-武侯区",
    },
]

CHINAHR_CARDS = [
    {
        "title": "气象预报工程师", "company": "某气象科技公司",
        "url": "https://www.chinahr.com/detail/2001.html", "salary_text": "5k-10k元/月", "location": "成都",
    },
    {
        "title": "环境监测工程师", "company": "某环保集团",
        "url": "https://www.chinahr.com/detail/2002.html", "salary_text": "面议", "location": "成都",
    },
]


class TestLiepinDomExtract:
    @pytest.mark.asyncio
    async def test_same_title_multi_company_aligned(self):
        """核心效果：同标题多公司各取各的 URL，三元组不错配。"""
        out = await _extract_liepin_from_dom(_FakeStaticPage(LIEPIN_CARDS))
        assert len(out) == 2
        assert out[0]["title"] == "AI人工智能算法工程师"
        assert out[0]["company"] == "甲科技公司"
        assert out[0]["url"] == "https://www.liepin.com/job/1001"
        assert out[1]["company"] == "乙智能公司"
        assert out[1]["url"] == "https://www.liepin.com/job/1002"

    @pytest.mark.asyncio
    async def test_evaluate_exception_returns_empty(self):
        """page.evaluate 抛异常（风控/改版）-> 返回空，上游安全回退 OCR。"""
        out = await _extract_liepin_from_dom(_FakeStaticPage(raises=True))
        assert out == []

    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        out = await _extract_liepin_from_dom(_FakeStaticPage([]))
        assert out == []

    @pytest.mark.asyncio
    async def test_extract_jobs_from_page_liepin_dom_first(self):
        """集成：猎聘走 DOM 优先，直接产出 Job（不经 OCR/标题匹配回填）。"""
        jobs = await extract_jobs_from_page(_FakeStaticPage(LIEPIN_CARDS), "猎聘", "12")
        assert len(jobs) == 2
        assert all(j.platform == "猎聘" for j in jobs)
        assert jobs[0].company == "甲科技公司"
        assert jobs[0].url == "https://www.liepin.com/job/1001"
        assert jobs[1].company == "乙智能公司"
        assert jobs[1].url == "https://www.liepin.com/job/1002"


class TestChinahrDomExtract:
    @pytest.mark.asyncio
    async def test_dom_extract_basic(self):
        out = await _extract_chinahr_from_dom(_FakeStaticPage(CHINAHR_CARDS))
        assert len(out) == 2
        assert out[0]["title"] == "气象预报工程师"
        assert out[0]["company"] == "某气象科技公司"
        assert out[0]["url"] == "https://www.chinahr.com/detail/2001.html"
        assert out[0]["location"] == "成都"

    @pytest.mark.asyncio
    async def test_same_company_same_title_diff_location_not_collapsed(self):
        """同公司同标题不同地点：集成层也保留各自 URL（不被 _dedup_jobs 压成 1 条），
        且 job_id 必须不同（否则下游 dedup_key=md5(platform:job_id) 会再压一遍）。"""
        cards = [
            {**CHINAHR_CARDS[0], "url": "https://www.chinahr.com/detail/3001.html", "location": "成都"},
            {**CHINAHR_CARDS[0], "url": "https://www.chinahr.com/detail/3002.html", "location": "绵阳"},
        ]
        jobs = await extract_jobs_from_page(_FakeStaticPage(cards), "中华英才网", "12")
        assert len(jobs) == 2
        assert jobs[0].url != jobs[1].url
        assert jobs[0].job_id != jobs[1].job_id

    @pytest.mark.asyncio
    async def test_same_company_same_title_diff_bianzhi_not_collapsed(self):
        """同公司同标题同地点不同编制（不同详情 URL）：集成层保留各自 URL。"""
        cards = [
            {**CHINAHR_CARDS[0], "url": "https://www.chinahr.com/detail/4001.html"},
            {**CHINAHR_CARDS[0], "url": "https://www.chinahr.com/detail/4002.html"},
        ]
        jobs = await extract_jobs_from_page(_FakeStaticPage(cards), "中华英才网", "12")
        assert len(jobs) == 2
        assert {j.url for j in jobs} == {
            "https://www.chinahr.com/detail/4001.html",
            "https://www.chinahr.com/detail/4002.html",
        }

    @pytest.mark.asyncio
    async def test_identical_cards_deduped(self):
        """四元组全同（滚动重叠采到同一卡片）仍然去重成 1 条。"""
        cards = [CHINAHR_CARDS[0], dict(CHINAHR_CARDS[0])]
        jobs = await extract_jobs_from_page(_FakeStaticPage(cards), "中华英才网", "12")
        assert len(jobs) == 1

    @pytest.mark.asyncio
    async def test_evaluate_exception_returns_empty(self):
        out = await _extract_chinahr_from_dom(_FakeStaticPage(raises=True))
        assert out == []

    @pytest.mark.asyncio
    async def test_extract_jobs_from_page_chinahr_dom_first(self):
        """集成：中华英才走 DOM 优先，直接产出 Job。"""
        jobs = await extract_jobs_from_page(_FakeStaticPage(CHINAHR_CARDS), "中华英才网", "12")
        assert len(jobs) == 2
        assert all(j.platform == "中华英才网" for j in jobs)
        assert jobs[0].title == "气象预报工程师"
        assert jobs[0].company == "某气象科技公司"
        assert jobs[0].salary_text == "5k-10k元/月"
