"""
Agent 智能体单元测试：动作 JSON 解析边界、重复守卫签名、登录墙检测、清单格式化、提取去重。

全部为纯函数测试，不需要浏览器 / LLM / API。
"""
from __future__ import annotations

import pytest

from src.agent.actions import AgentAction, parse_action
from src.agent.agent import _looks_like_job_page
from src.agent.extract import _dedup_jobs, _dict_to_job
from src.agent.perceive import detect_login_wall, inventory_to_text
from src.models import Job


# ---------- parse_action：JSON 解析边界 ----------

class TestParseAction:
    def test_plain_json(self):
        raw = '{"thought":"看页面","action":"click","target_id":0,"reason":"点搜索"}'
        a = parse_action(raw)
        assert a.action == "click"
        assert a.target_id == 0
        assert a.thought == "看页面"

    def test_markdown_fence(self):
        raw = '```json\n{"action":"type","target":"搜索框","text":"成都 气象"}\n```'
        a = parse_action(raw)
        assert a.action == "type"
        assert a.target == "搜索框"
        assert a.text == "成都 气象"

    def test_trailing_text_after_object(self):
        raw = '{"action":"scroll"} 好的，我决定向下滚动查看更多岗位。'
        a = parse_action(raw)
        assert a.action == "scroll"

    def test_coord_array(self):
        raw = '{"action":"click","coord":[400,300]}'
        a = parse_action(raw)
        assert a.coord == (400, 300)

    def test_empty_fields_default(self):
        a = parse_action('{"action":"wait"}')
        assert a.action == "wait"
        assert a.target_id is None
        assert a.target == ""
        assert a.text == ""
        assert a.coord is None
        assert a.reason == ""

    def test_invalid_no_json_object_raises(self):
        with pytest.raises(ValueError):
            parse_action("页面正在加载，请稍候...")

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            parse_action('{"action": "click", "target_id": }')

    def test_non_object_json_raises(self):
        with pytest.raises(ValueError):
            parse_action("[1, 2, 3]")

    def test_invalid_action_value_raises(self):
        # Pydantic 校验失败（非法 action 枚举）也应抛 ValueError
        with pytest.raises(ValueError):
            parse_action('{"action":"hack_the_planet"}')

    def test_reason_with_login_wall_keyword(self):
        a = parse_action('{"action":"done","reason":"登录墙，跳过"}')
        assert a.is_terminal()


# ---------- AgentAction：终止判断 + 重复守卫签名 ----------

class TestAgentAction:
    def test_is_terminal_extract(self):
        assert AgentAction(action="extract").is_terminal()

    def test_is_terminal_done(self):
        assert AgentAction(action="done").is_terminal()

    def test_is_terminal_click_false(self):
        assert not AgentAction(action="click").is_terminal()

    def test_signature_prefers_target_id(self):
        a1 = AgentAction(action="click", target_id=3)
        a2 = AgentAction(action="click", target="搜索按钮")
        assert a1.signature() != a2.signature()

    def test_signature_same_action_same_target_same_text(self):
        a1 = AgentAction(action="type", target="搜索框", text="成都")
        a2 = AgentAction(action="type", target="搜索框", text="成都")
        assert a1.signature() == a2.signature()

    def test_signature_differs_when_text_changes(self):
        a1 = AgentAction(action="type", target="搜索框", text="成都")
        a2 = AgentAction(action="type", target="搜索框", text="北京")
        assert a1.signature() != a2.signature()

    def test_signature_uses_target_when_no_id(self):
        a = AgentAction(action="click", target="岗位卡片")
        assert a.signature() == "click:岗位卡片:"


# ---------- detect_login_wall ----------

class TestDetectLoginWall:
    def test_login_keyword(self):
        assert detect_login_wall("请先登录后再查看完整职位信息")

    def test_captcha_keyword(self):
        assert detect_login_wall("拖动滑块完成安全验证")

    def test_normal_job_page_false(self):
        assert not detect_login_wall("气象工程师 薪资15-25K 经验3-5年 本科")

    def test_empty_false(self):
        assert not detect_login_wall("")


# ---------- _looks_like_job_page ----------

class TestLooksLikeJobPage:
    def test_job_signal_words(self):
        assert _looks_like_job_page("招聘 气象工程师 学历不限 月薪")

    def test_pure_login_text_false(self):
        assert not _looks_like_job_page("登录 扫码 验证码")


# ---------- inventory_to_text ----------

class TestInventoryToText:
    def test_empty(self):
        assert inventory_to_text([]) == ""

    def test_formats_elements(self):
        inv = [
            {"id": 0, "tag": "input", "placeholder": "搜索职位", "x": 300, "y": 40},
            {"id": 1, "tag": "button", "text": "搜索", "x": 500, "y": 40},
        ]
        text = inventory_to_text(inv)
        assert "[0] input" in text and '"搜索职位"' in text and "@300,40" in text
        assert "[1] button \"搜索\" @500,40" in text

    def test_limit_truncation(self):
        inv = [{"id": i, "tag": "button", "text": f"b{i}", "x": i, "y": 0} for i in range(50)]
        text = inventory_to_text(inv, limit=10)
        assert "共 50 个元素" in text
        assert len([l for l in text.splitlines() if l.startswith("[")]) == 10


# ---------- _dict_to_job / _dedup_jobs ----------

class TestDictToJob:
    def test_basic_mapping(self):
        j = _dict_to_job(
            {"title": "气象工程师", "company": "成都气象局", "salary_text": "15-25K", "location": "成都"},
            platform="智联招聘",
            round_label="12",
        )
        assert j.title == "气象工程师"
        assert j.company == "成都气象局"
        assert j.platform == "智联招聘"
        assert j.search_round == "12"
        assert j.job_id.startswith("agent-")
        assert len(j.job_id) == len("agent-") + 12

    def test_strips_whitespace_and_handles_missing(self):
        j = _dict_to_job({"title": "  气象  ", "company": None}, "51Job", "")
        assert j.title == "气象"
        assert j.company == ""
        assert j.salary_text == ""
        assert j.requirements == ""
        assert j.hr_active is False

    def test_requirements_truncated(self):
        j = _dict_to_job({"title": "t", "company": "c", "requirements": "x" * 800}, "51Job", "")
        assert len(j.requirements) == 500


class TestDedupJobs:
    def _job(self, title, company):
        return Job(
            platform="智联招聘",
            job_id="x",
            url="",
            title=title,
            company=company,
        )

    def test_dedup_case_insensitive(self):
        jobs = [
            self._job("气象工程师", "成都气象局"),
            self._job("气象工程师", "成都气象局"),  # 完全相同
            self._job("气象工程师", "成都气象局"),  # 大小写不同（英文公司名场景）
        ]
        result = _dedup_jobs(jobs)
        assert len(result) == 1

    def test_filters_empty_jobs(self):
        jobs = [
            self._job("", ""),
            self._job("", ""),
            self._job("气象工程师", "成都气象局"),
        ]
        result = _dedup_jobs(jobs)
        assert len(result) == 1
        assert result[0].title == "气象工程师"
