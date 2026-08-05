"""
JD 质量修复 + CI 重构的配套单测。

覆盖：
- bjt_today() 跨 UTC 日边界（凌晨 01:00 BJT 启动时数据不落错天）
- detail.py 详情富集纯函数（拆分/清洗/URL去重）+ 登录墙降级
- confirm.py 轮次确认（数量门槛 + LLM 评审 + 降级从宽）

全部为离线测试：mock datetime / mock page / mock LLM，不碰浏览器与网络。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone, timedelta

import pytest

from src.config import DETAIL_JD_TEXT_LIMIT
from src.models import Job, SearchRound
from src.scrapers import detail
from src.scrapers.detail import (
    _clean_text,
    _collect_urls,
    _looks_like_detail_page,
    _merge_text,
    _split_job_text,
    _extract_detail_text,
)


def _job(url: str = "", title: str = "岗位", platform: str = "智联招聘") -> Job:
    return Job(platform=platform, job_id="x", url=url, title=title, company="测试公司")


# ---------- bjt_today() 跨 UTC 日边界 ----------

class TestBjtToday:
    def test_format(self):
        from src.config import bjt_today
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", bjt_today())

    def test_crosses_utc_date(self, monkeypatch):
        """01:00 BJT = 前一天 17:00 UTC。模拟 BJT 8/6 01:00，
        bjt_today() 必须返回 8/6（而非 UTC 日 8/5），否则凌晨轮次落错天。"""
        import src.config as config
        BJT = timezone(timedelta(hours=8))

        class _FakeDateTime:
            @classmethod
            def now(cls, tz):
                return datetime(2026, 8, 6, 1, 0, 0, tzinfo=BJT)

        monkeypatch.setattr(config, "datetime", _FakeDateTime)
        assert config.bjt_today() == "2026-08-06"
        assert config.bjt_now().date().isoformat() == "2026-08-06"

    def test_afternoon_same_day(self, monkeypatch):
        """21:00 BJT 当天日期与 UTC 相同，不误判。"""
        import src.config as config
        BJT = timezone(timedelta(hours=8))

        class _FakeDateTime:
            @classmethod
            def now(cls, tz):
                return datetime(2026, 8, 5, 21, 0, 0, tzinfo=BJT)

        monkeypatch.setattr(config, "datetime", _FakeDateTime)
        assert config.bjt_today() == "2026-08-05"


# ---------- detail.py 详情富集纯函数 ----------

class TestSplitJobText:
    def test_requirement_marker_splits(self):
        text = "岗位职责：负责气象数据分析。\n任职要求：大气科学本科以上，3年经验。"
        resp, req = _split_job_text(text)
        assert resp == text
        assert req == "任职要求：大气科学本科以上，3年经验。"

    def test_no_marker_keeps_resp_only(self):
        text = "岗位职责：负责环境监测数据分析。"
        resp, req = _split_job_text(text)
        assert resp == text
        assert req == ""

    def test_empty(self):
        assert _split_job_text("") == ("", "")


class TestCleanText:
    def test_collapses_whitespace(self):
        out = _clean_text("  第一行\n\n\n  第二行\t\t尾  ")
        assert "第一行" in out and "第二行" in out
        assert "\n\n\n" not in out

    def test_truncates_to_limit(self):
        out = _clean_text("字" * (DETAIL_JD_TEXT_LIMIT + 500))
        assert len(out) == DETAIL_JD_TEXT_LIMIT

    def test_empty(self):
        assert _clean_text("") == ""


class TestCollectUrls:
    def test_dedup_by_url(self):
        jobs = [
            _job(url="https://jobs.testjobs.cn/job/a", title="岗A"),
            _job(url="https://jobs.testjobs.cn/job/a", title="岗A2"),  # 同 URL 合并
            _job(url="https://jobs.testjobs.cn/job/b", title="岗B"),
            _job(url="", title="无URL"),                       # 无 URL 跳过
            _job(url="https://xxxxxxx.html", title="幻觉URL"),  # 占位符净化
        ]
        by_url, urls = _collect_urls(jobs, max_jobs=10)
        assert by_url["https://jobs.testjobs.cn/job/a"] == [jobs[0], jobs[1]]
        assert by_url["https://jobs.testjobs.cn/job/b"] == [jobs[2]]
        assert urls == ["https://jobs.testjobs.cn/job/a", "https://jobs.testjobs.cn/job/b"]

    def test_respects_max_jobs(self):
        jobs = [_job(url=f"https://jobs.testjobs.cn/job/{i}") for i in range(10)]
        by_url, urls = _collect_urls(jobs, max_jobs=3)
        assert len(urls) == 3

    def test_empty(self):
        assert _collect_urls([], 5) == ({}, [])


class TestMergeText:
    def test_merges_and_dedupes(self):
        merged = _merge_text("职责A", "职责B")
        assert "职责A" in merged and "职责B" in merged
        # 重复正文不叠加
        merged2 = _merge_text("职责A", "职责A")
        assert merged2.count("职责A") == 1

    def test_keeps_existing_if_new_empty(self):
        assert _merge_text("职责A", "") == "职责A"
        assert _merge_text("", "职责B") == "职责B"


class TestLooksLikeDetailPage:
    """详情页正文判定：防止把 404/登录墙/落地页噪音当 JD 写入（冒烟暴露的缺陷）。"""

    def test_real_jd_with_section_marker(self):
        assert _looks_like_detail_page("岗位职责：负责气象数据分析。任职要求：大气科学本科。薪资15-25K")

    def test_real_jd_section_marker_even_with_login_nav(self):
        # 真实详情页头部也常带"登录/注册"，但有 JD 区块标记 → 仍判为详情页
        assert _looks_like_detail_page("登录/注册 首页 职位描述 任职要求 岗位职责 薪资面议")

    def test_404_error_page_rejected(self):
        assert not _looks_like_detail_page("找不到该页 File not found 您要查看的页已删除 请尝试以下操作")

    def test_expired_job_rejected(self):
        assert not _looks_like_detail_page("该职位已下线 感谢您的关注 首页 职位搜索")

    def test_boss_landing_nav_rejected(self):
        # BOSS 未登录落地页：有"登录/招聘/搜索"导航但无 JD 区块 → 拒
        assert not _looks_like_detail_page(
            "全国[切换] 首页 职位 公司 校园 海归 我要招聘 我要找工作 登录/注册 职位类型 地图 搜索 热门职位: Java 产品经理 前端开发工程师"
        )

    def test_short_login_wall_rejected(self):
        assert not _looks_like_detail_page("请扫码登录后查看完整职位信息")

    def test_generic_list_page_rejected(self):
        # 列表页：一堆岗位标题但无 JD 区块、且信号词 <2
        assert not _looks_like_detail_page("热门职位 公司 城市 行业 更多 欢迎加入我们")

    def test_fallback_signals_at_least_two(self):
        # 无 JD 区块但含 ≥2 个岗位信号（部分平台详情页措辞特殊）→ 放行
        assert _looks_like_detail_page("薪资15-25K 经验3-5年 学历本科 工作地点成都")

    def test_empty_rejected(self):
        assert not _looks_like_detail_page("")


class _FakeDetailPage:
    """mock 详情页：返回预设正文，记录 wait_for_selector / goto 调用。"""
    def __init__(self, body: str = "", selector_text: str | None = None,
                 selector_found: bool = True):
        self.body = body
        self.selector_text = selector_text
        self.selector_found = selector_found
        self.calls: list[str] = []

    async def wait_for_selector(self, sel, state=None, timeout=None):
        self.calls.append(f"wait:{sel}")
        if not self.selector_found:
            raise Exception("selector timeout")
        return True

    async def wait_for_timeout(self, ms):
        self.calls.append(f"timeout:{ms}")

    async def evaluate(self, expr, arg=None):
        self.calls.append(f"eval:{expr[:30]}")
        if "querySelector(sel)" in expr or "querySelector(" in expr:
            return self.selector_text
        return self.body


class TestExtractDetailText:
    @pytest.mark.asyncio
    async def test_selector_priority(self):
        """有 detail_selector 时优先取容器文本。"""
        page = _FakeDetailPage(body="body全文", selector_text="容器正文：任职要求 本科")
        text = await _extract_detail_text(page, ".job_msg")
        assert text == "容器正文：任职要求 本科"

    @pytest.mark.asyncio
    async def test_fallback_to_body_when_selector_timeout(self):
        """selector 失效（站点改版）时回退 body innerText。"""
        page = _FakeDetailPage(body="body全文岗位职责", selector_found=False)
        text = await _extract_detail_text(page, ".job_msg")
        assert text == "body全文岗位职责"

    @pytest.mark.asyncio
    async def test_generic_fallback_without_selector(self):
        """无 selector 的平台走通用回退（等 2s 后取 body）。"""
        page = _FakeDetailPage(body="通用回退正文")
        text = await _extract_detail_text(page, None)
        assert text == "通用回退正文"
        assert any(c.startswith("timeout:") for c in page.calls)


class TestEnrichLoginWallDegradation:
    @pytest.mark.asyncio
    async def test_login_wall_not_counted_as_enriched(self, monkeypatch):
        """登录墙页面 → 错误记录、不记入富集数、不中断（降级保留列表数据）。"""
        # 直接测 _enrich_with_page：登录墙文本 < LOGIN_WALL_MIN_TEXT 时跳过
        jobs = [_job(url="https://jobs.testjobs.cn/job/login-walled")]
        by_url, urls = _collect_urls(jobs, 1)

        class _WallPage:
            async def goto(self, url, wait_until=None, timeout=None):
                pass

            async def wait_for_timeout(self, ms):
                pass

            async def wait_for_selector(self, sel, state=None, timeout=None):
                raise Exception("timeout")

        # 覆盖 _extract_detail_text 返回登录墙文本
        async def fake_extract(page, selector):
            return "请扫码登录后查看完整职位信息"

        monkeypatch.setattr(detail, "_extract_detail_text", fake_extract)
        enriched, errors = await detail._enrich_with_page(
            _WallPage(), by_url, urls, budget_seconds=30,
        )
        assert enriched == 0
        assert any("登录墙" in e for e in errors)


# ---------- confirm.py 轮次确认 ----------

def _make_round(valid: int, excluded: int = 0) -> SearchRound:
    jobs = []
    for i in range(valid):
        jobs.append(Job(platform="智联招聘", job_id=f"v{i}", url="", title=f"气象工程师{i}",
                        company="测试公司", responsibilities="负责气象数据分析与预报。"))
    for i in range(excluded):
        j = Job(platform="智联招聘", job_id=f"e{i}", url="", title=f"销售{i}", company="测试公司",
                responsibilities="")
        j.excluded = True
        j.exclude_reason = "专业不匹配"
        jobs.append(j)
    return SearchRound(round_label="1", jobs=jobs)


class TestConfirmRound:
    @pytest.mark.asyncio
    async def test_quantity_gate_fail(self, monkeypatch):
        """有效 JD 3 < 门槛 5 → FAIL，且 stats 写回。"""
        from src.confirm import confirm_round

        rd = _make_round(valid=3)
        saved: list[SearchRound] = []
        monkeypatch.setattr("src.confirm.save_round", lambda r: saved.append(r))

        async def fake_review(r):
            return {"reviewed": 3, "relevant": True, "jd_complete": True, "reason": "ok"}

        monkeypatch.setattr("src.confirm._llm_review", fake_review)

        conf = await confirm_round(rd)
        assert conf["quantity_pass"] is False
        assert conf["result"] == "FAIL"
        assert "低于门槛" in conf["reason"]
        assert rd.stats["confirmation"]["valid_count"] == 3
        assert saved and saved[-1] is rd  # 确认结果已重新落盘

    @pytest.mark.asyncio
    async def test_quantity_pass_and_llm_relevant(self, monkeypatch):
        from src.confirm import confirm_round

        rd = _make_round(valid=8, excluded=2)
        monkeypatch.setattr("src.confirm.save_round", lambda r: None)

        async def fake_review(r):
            return {"reviewed": 8, "relevant": True, "jd_complete": True, "reason": "气象相关岗位，JD 完整"}

        monkeypatch.setattr("src.confirm._llm_review", fake_review)

        conf = await confirm_round(rd)
        assert conf["quantity_pass"] is True
        assert conf["result"] == "PASS"
        assert "完整" in conf["reason"]

    @pytest.mark.asyncio
    async def test_llm_not_relevant_fails(self, monkeypatch):
        from src.confirm import confirm_round

        rd = _make_round(valid=8)
        monkeypatch.setattr("src.confirm.save_round", lambda r: None)

        async def fake_review(r):
            return {"reviewed": 8, "relevant": False, "jd_complete": True, "reason": "全是销售岗"}

        monkeypatch.setattr("src.confirm._llm_review", fake_review)

        conf = await confirm_round(rd)
        assert conf["quantity_pass"] is True
        assert conf["result"] == "FAIL"
        assert "相关性不足" in conf["reason"]

    @pytest.mark.asyncio
    async def test_llm_failure_degrades_to_pass(self, monkeypatch):
        """LLM 评审抛异常 → confirm_round 兜底从宽放行，数量达标仍 PASS。"""
        from src.confirm import confirm_round

        rd = _make_round(valid=8)
        monkeypatch.setattr("src.confirm.save_round", lambda r: None)

        async def fake_review(r):
            raise RuntimeError("DeepSeek 超时")

        monkeypatch.setattr("src.confirm._llm_review", fake_review)

        conf = await confirm_round(rd)
        assert conf["quantity_pass"] is True
        assert conf["result"] == "PASS"
        assert conf["review"]["relevant"] is True
        assert "LLM 评审异常" in conf["review"]["reason"]

    @pytest.mark.asyncio
    async def test_llm_review_internal_guard(self, monkeypatch):
        """DeepSeek 调用失败 → _llm_review 内部降级返回（relevant 从宽 True），不抛异常。"""
        from src.confirm import _llm_review

        rd = _make_round(valid=8)

        async def fake_chat(prompt, **kwargs):
            raise RuntimeError("DeepSeek 超时")

        monkeypatch.setattr("src.llm.deepseek.chat", fake_chat)

        review = await _llm_review(rd)
        assert review["reviewed"] == 8
        assert review["relevant"] is True
        assert "LLM 评审失败" in review["reason"]

    @pytest.mark.asyncio
    async def test_llm_review_no_valid_jobs(self):
        from src.confirm import _llm_review

        rd = _make_round(valid=0)
        review = await _llm_review(rd)
        assert review["reviewed"] == 0
        assert review["relevant"] is True

    def test_count_valid(self):
        from src.confirm import _count_valid
        rd = _make_round(valid=6, excluded=3)
        assert _count_valid(rd) == 6
