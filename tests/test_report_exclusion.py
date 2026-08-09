"""
报告层排除规则单元测试（用户要求 2026-08-09）：
1. 领域排除：医学/法律/游戏/证券/电商/餐饮（制造+服务两侧，匹配 标题+公司）
2. 高薪排除：月薪下限 ≥ 2.9万（29K）一律剔除；年薪折月；面议/乱码保留
   （阈值 2026-08-09 由 3万 下调为 2.9万）
"""
from __future__ import annotations

import pytest

from src.models import Job
from src.filters.report_exclusion import (
    excluded_domain,
    is_high_salary,
    report_exclusion_reason,
    salary_floor_monthly_k,
)


def _job(title: str, company: str = "", salary_text: str = "") -> Job:
    return Job(
        platform="test", job_id="x", url="",
        title=title, company=company, salary_text=salary_text,
    )


class TestExcludedDomain:
    @pytest.mark.parametrize("title", [
        "临床医生", "执业律师", "游戏策划", "证券分析师", "电商运营工程师", "餐厅店长",
    ])
    def test_title_hit_each_domain(self, title):
        assert excluded_domain(_job(title)) != ""

    def test_company_hit_manufacturing_side(self):
        # 制造侧：医药制造公司的环保工程师也剔除（用户：制造/服务都不要）
        assert excluded_domain(_job("环保工程师", "成都某医药制造有限公司")) == "医学"

    def test_company_hit_service_side(self):
        # 服务侧：餐饮服务公司的新媒体运营也剔除
        assert excluded_domain(_job("新媒体运营", "某餐饮管理服务部")) == "餐饮"

    def test_company_hit_securities(self):
        assert excluded_domain(_job("客户经理", "某证券股份有限公司")) == "证券"

    def test_company_hit_game(self):
        assert excluded_domain(_job("AI算法工程师", "某游戏科技有限公司")) == "游戏"

    def test_target_domain_jobs_kept(self):
        # 目标领域（气象/环保/AI）不受影响
        assert excluded_domain(_job("气象预报工程师", "某气象科技公司")) == ""
        assert excluded_domain(_job("环境监测工程师", "某环保集团")) == ""
        assert excluded_domain(_job("AI工程师", "某智能科技公司")) == ""

    def test_quality_inspector_not_medical(self):
        # 防误伤：「检验」未进词表（质量检验是制造业通用岗，不是医学检验）
        assert excluded_domain(_job("质量检验工程师", "某机械制造公司")) == ""


class TestSalaryFloorParsing:
    @pytest.mark.parametrize("text,expected", [
        ("15-25K", 15.0),
        ("5k-10k元/月", 5.0),
        ("3-6万", 30.0),            # 首数字无单位向后继承「万」
        ("2.5-3万·15薪", 25.0),
        ("5-7千", 5.0),
        ("8千-1.5万", 8.0),
        ("18-22万/年", 15.0),        # 年薪折月：18*10/12
        ("36-60万/年", 30.0),        # 36万/年 = 3万/月
        ("7000-10000", 7.0),         # 裸大数字按元
        ("15-25k·14薪", 15.0),
    ])
    def test_parse_formats(self, text, expected):
        assert salary_floor_monthly_k(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["", "面议", "薪资面议", "*大-**元", None])
    def test_unparseable_returns_none(self, text):
        assert salary_floor_monthly_k(text) is None


class TestHighSalaryExclusion:
    @pytest.mark.parametrize("text", [
        "3-6万", "30-50K", "3万-5万/月", "36-60万/年", "4-8万",
        "2.9-4万",          # 新阈值边界：下限恰 2.9万 -> 剔
        "29-40K",
        "35-50万/年",       # 年薪折月 29.2K >= 29K -> 剔
    ])
    def test_floor_29k_or_above_excluded(self, text):
        assert is_high_salary(_job("AI工程师", salary_text=text)) is True

    @pytest.mark.parametrize("text", [
        "2.8-3.5万",        # 下限 2.8万 < 2.9万 -> 保留（规则看下限）
        "2.5-3万·15薪",
        "1.8-3万",
        "25-35K",
        "28-38K",
        "34-50万/年",       # 年薪折月 28.3K < 29K -> 保留
        "18-22万/年",
        "面议", "",
    ])
    def test_below_or_unknown_kept(self, text):
        assert is_high_salary(_job("AI工程师", salary_text=text)) is False


class TestReportExclusionReason:
    def test_domain_priority_over_salary(self):
        j = _job("电商运营工程师", salary_text="3-6万")
        assert report_exclusion_reason(j) == "排除领域:电商"

    def test_high_salary_reason(self):
        j = _job("AI工程师", salary_text="3-6万")
        assert "月薪下限≥2.9万" in report_exclusion_reason(j)

    def test_normal_job_kept(self):
        j = _job("气象工程师", "某气象科技公司", "8千-1.5万")
        assert report_exclusion_reason(j) == ""


class TestGenerateReportIntegration:
    def test_excluded_jobs_absent_from_both_reports(self, monkeypatch):
        """集成：排除规则在拆分前生效，两份 HTML 都不含被剔岗位。"""
        import shutil
        import tempfile
        from pathlib import Path

        import src.report.generator as gen

        # 不用 pytest tmp_path：本机 Temp\\pytest-of-pc 有 ACL 限制（WinError 5）
        out_dir = Path(tempfile.mkdtemp(prefix="report-exclusion-test-"))
        self._cleanup = lambda: shutil.rmtree(out_dir, ignore_errors=True)
        monkeypatch.setattr(gen, "OUTPUT_DIR", out_dir)
        jobs = [
            _job("气象预报工程师", "某气象科技公司", "8千-1.5万"),       # 保留 -> 行业
            _job("AI工程师", "某智能科技公司", "1.5-2.5万"),           # 保留 -> 专业
            _job("电商运营工程师", "某贸易公司", "1-1.8万"),           # 领域剔除
            _job("医药代表", "某医药公司", "6-9千"),                   # 领域剔除
            _job("高级算法工程师", "某科技公司", "3-6万"),             # 高薪剔除
        ]
        paths = gen.generate_report(jobs, target_date="2099-01-01")
        industry = (out_dir / "2099-01-01" / "report-industry.html").read_text(encoding="utf-8")
        professional = (out_dir / "2099-01-01" / "report-professional.html").read_text(encoding="utf-8")
        self._cleanup()
        both = industry + professional

        assert "气象预报工程师" in industry
        assert "AI工程师" in professional
        assert "电商运营工程师" not in both
        assert "医药代表" not in both
        assert "高级算法工程师" not in both
        assert paths["industry"].endswith("report-industry.html")
        assert paths["professional"].endswith("report-professional.html")
