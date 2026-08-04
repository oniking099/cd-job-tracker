"""
OCR 兜底解析单元测试：parse_jobs_from_text 对两种卡片结构的提取、登录噪音排除、城市过滤。
纯函数测试，不加载 OCR 引擎（避免触发模型下载）。
"""
from __future__ import annotations

import pytest

from src.ocr.parse_jobs import parse_jobs_from_text, _split_title_salary
from src.scrapers.agent_scraper import AgentScraperBase


# 模拟智联搜索卡片（A 结构：title+salary 同行）
MOCK_A = """气象仪器销售经理3000-6000元
长春气象仪器有限公司
民营
20-99人
仪器仪表
王莉·人事经理 5日内活跃 立即沟通
长春·南关·鸿城3-5年 大专
气象数据分析工程师 8000-16000元
天津华云天仪特种气象探测技术有限公司
天津·西青·张家窝
经验不限本科"""

# 模拟智联搜索卡片（B 结构：title/company/salary 分行）
MOCK_B = """气象雷达软件设计师（二级）
航天新气象科技有限公司
1.5-2.5万
国企
立即投递
300-499人
专用设备制造
无锡·滨湖·雪浪3-5年 本科"""

# 模拟登录弹窗噪音 + 空内容
MOCK_NOISE = """我要招人>
验证码登录/注册
手机号
+ 86
获取验证码
已阅读并同意《用户服务协议》和
《隐私政策》"""


class TestSplitTitleSalary:
    def test_inline_title_salary(self):
        title, salary = _split_title_salary("气象仪器销售经理3000-6000元")
        assert title == "气象仪器销售经理"
        assert salary == "3000-6000元"

    def test_pure_salary_line(self):
        title, salary = _split_title_salary("1.5-2.5万")
        assert title == ""
        assert salary == "1.5-2.5万"

    def test_no_salary(self):
        assert _split_title_salary("航天新气象科技有限公司") == ("", "")


class TestParseA:
    def test_extracts_inline_cards(self):
        jobs = parse_jobs_from_text(MOCK_A)
        assert len(jobs) == 2
        j0 = jobs[0]
        assert j0["title"] == "气象仪器销售经理"
        assert j0["salary_text"] == "3000-6000元"
        assert j0["company"] == "长春气象仪器有限公司"

    def test_location_not_hr_line(self):
        jobs = parse_jobs_from_text(MOCK_A)
        assert "人事经理" not in jobs[0]["location"]
        assert "长春" in jobs[0]["location"]


class TestParseB:
    def test_extracts_split_cards(self):
        jobs = parse_jobs_from_text(MOCK_B)
        assert len(jobs) == 1
        j0 = jobs[0]
        assert j0["title"] == "气象雷达软件设计师（二级）"
        assert j0["salary_text"] == "1.5-2.5万"
        assert j0["company"] == "航天新气象科技有限公司"
        assert "无锡" in j0["location"]


class TestParseNoise:
    def test_login_noise_not_extracted(self):
        jobs = parse_jobs_from_text(MOCK_NOISE)
        assert jobs == []

    def test_empty_text(self):
        assert parse_jobs_from_text("") == []


class TestCityFilter:
    def test_only_chengdu_kept(self):
        from src.models import Job
        jobs = [
            Job(platform="智联招聘", job_id="1", url="", title="成都岗位", company="A", location="成都·武侯·红牌楼"),
            Job(platform="智联招聘", job_id="2", url="", title="外地岗位", company="B", location="无锡·滨湖·雪浪"),
            Job(platform="智联招聘", job_id="3", url="", title="无地点岗位", company="C", location=""),
        ]
        filtered = AgentScraperBase._filter_city(AgentScraperBase, jobs)
        assert len(filtered) == 1
        assert filtered[0].title == "成都岗位"

    def test_empty_location_dropped(self):
        from src.models import Job
        jobs = [Job(platform="p", job_id="1", url="", title="t", company="c", location="")]
        assert AgentScraperBase._filter_city(AgentScraperBase, jobs) == []
