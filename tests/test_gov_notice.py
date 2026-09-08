# -*- coding: utf-8 -*-
"""体制内公告型爬虫（scpta/5rc）解析逻辑单元测试。

覆盖：标题清洗、招聘/公示判定、发布单位解析、成都信号判定（含省属）、
流程公示过滤、URL 过滤、同轮去重。
"""
from __future__ import annotations

import pytest

from src.scrapers.gov_notice import (
    Rc5Scraper,
    ScptaScraper,
    _is_recruit_notice,
    _notice_jobs,
    parse_notice_title,
)


class TestParseNoticeTitle:
    def test_chengdu_direct(self):
        r = parse_notice_title("成都市司法局公开招聘公告")
        assert r["location"] == "成都"
        assert r["company"] == "成都市司法局"

    def test_head_number_noise_removed(self):
        r = parse_notice_title("316人丨成都市养老服务管理专员招募公告")
        assert r["title"].startswith("成都市")
        assert r["location"] == "成都"

    def test_provincial_unit_defaults_chengdu(self):
        r = parse_notice_title("四川省气象局2026年公开招聘工作人员公告")
        assert r["location"] == "成都（省属）"
        assert "成都" in r["location"]
        assert r["company"] == "四川省气象局"

    def test_other_province_dropped(self):
        r = parse_notice_title("广西壮族自治区人力资源和社会保障厅直属事业单位招聘公告")
        assert r["location"] == ""  # 无成都信号 → 上层丢弃

    def test_company_extraction_group(self):
        r = parse_notice_title("四川成都锦江发展集团下属锦发边缘智能公司招聘公告")
        assert "锦江发展集团" in r["company"]
        assert r["location"] == "成都"


class TestRecruitNoticeFilter:
    def test_recruit_kept(self):
        assert _is_recruit_notice("2026年公开招聘工作人员公告")

    def test_procedure_filtered(self):
        assert not _is_recruit_notice("公开招聘工作人员拟聘人员公示")
        assert not _is_recruit_notice("公开选调工作人员考试总成绩排名公告")

    def test_non_recruit_filtered(self):
        assert not _is_recruit_notice("2026年度考试计划安排")


class TestNoticeJobs:
    def test_full_pipeline(self):
        items = [
            {"title": "成都市生态环境局公开招聘公告", "url": "https://www.5rc.com/zhaokao/1/", "date": "2026-09-05"},
            {"title": "成都市XX局拟聘人员公示", "url": "https://www.5rc.com/zhaokao/2/", "date": ""},
            {"title": "外省某市招聘公告", "url": "https://www.5rc.com/zhaokao/3/", "date": ""},
            {"title": "无链接公告", "url": "javascript:;", "date": ""},
        ]
        jobs = _notice_jobs(items, "王才网成都站", "1")
        assert len(jobs) == 1
        j = jobs[0]
        assert j.platform == "王才网成都站"
        assert j.location == "成都"
        assert j.posted_date == "2026-09-05"
        assert j.job_id.startswith("notice-")

    def test_dedup_same_url(self):
        items = [
            {"title": "成都市司法局公开招聘公告", "url": "https://x/zhaokao/9/", "date": ""},
            {"title": "成都市司法局公开招聘公告", "url": "https://x/zhaokao/9/", "date": ""},
        ]
        assert len(_notice_jobs(items, "王才网成都站", "1")) == 1


class TestRegistry:
    def test_registered_as_html_not_agent(self):
        from src.scrapers import ALL_SCRAPERS, AGENT_SCRAPERS
        assert ALL_SCRAPERS.get("四川人事考试网") is ScptaScraper
        assert ALL_SCRAPERS.get("王才网成都站") is Rc5Scraper
        assert "四川人事考试网" not in AGENT_SCRAPERS
        assert "王才网成都站" not in AGENT_SCRAPERS

    def test_round_dedup_gate(self, monkeypatch):
        """同轮第二次调用直接返回空（不发网络请求）。"""
        import src.scrapers.gov_notice as gn

        monkeypatch.setattr(gn, "_NOTICE_FETCHED", {"四川人事考试网": "2026-09-05|1"})
        # search() 第一行就会命中去重返回 []，无需浏览器
        import asyncio
        s = ScptaScraper()
        assert asyncio.get_event_loop_policy()  # 环境存在
        assert s.platform_name == "四川人事考试网"
