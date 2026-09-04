"""
新视觉 provider（Q3/Q4）与文本优先决策链（Q1）单元测试。

Mock 掉网络调用（OpenAI 兼容 client），只验证：
- siliconflow JSON 解析、OCR 位置标记剔除、API_KEY 缺失守卫
- modelscope JSON 解析、API_KEY 缺失守卫
- 决策链：DeepSeek 文本优先；DeepSeek 失败→视觉兜底；页面文字过少→RapidOCR 补字
"""
from __future__ import annotations

import pytest

from src.agent import decision as decision_mod
from src.agent.decision import decide_next_action
from src.llm import modelscope, siliconflow


# ---------- 假 OpenAI 客户端 ----------

class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content

    async def create(self, **kwargs):
        _Msg = type("_Msg", (), {"content": self.content})
        _Choice = type("_Choice", (), {"message": _Msg()})
        return type("_Resp", (), {"choices": [_Choice()]})()


class _FakeChat:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    """可注入返回内容的假 AsyncOpenAI 客户端。"""
    content = "{}"

    def __init__(self, *args, **kwargs):
        self.chat = _FakeChat(self.content)


def _ok_response(content: str):
    _Msg = type("_Msg", (), {"content": content})
    _Choice = type("_Choice", (), {"message": _Msg()})
    return type("_Resp", (), {"choices": [_Choice()]})()


# ---------- SiliconFlow ----------

class TestSiliconFlowParse:
    def test_markdown_fence_stripped(self):
        raw = '```json\n[{"title": "气象工程师", "company": "成都气象局"}]\n```'
        assert siliconflow._parse_json_array(raw) == [{"title": "气象工程师", "company": "成都气象局"}]

    def test_plain_array(self):
        assert siliconflow._parse_json_array('[{"title": "a"}]') == [{"title": "a"}]

    def test_invalid_json_returns_empty(self):
        assert siliconflow._parse_json_array("这不是 JSON") == []

    def test_non_list_returns_empty(self):
        assert siliconflow._parse_json_array('{"title": "a"}') == []


class TestSiliconFlowOcr:
    @pytest.mark.asyncio
    async def test_loc_markers_stripped(self, monkeypatch):
        # 设上 key，否则 ocr_screenshot_text 会在 API_KEY 守卫处直接返回空，走不到 mock client
        monkeypatch.setattr(siliconflow, "SILICONFLOW_API_KEY", "sk-test")
        monkeypatch.setattr(siliconflow, "_create_client", lambda timeout: _FakeClient())
        _FakeClient.content = '岗位：气象工程师\n薪资：15-25K<|LOC_1|>\n经验：3-5年<|LOC_2|>'
        text = await siliconflow.ocr_screenshot_text(b"\x89PNG fake")
        # 位置标记剔除，换行保留（喂 DeepSeek 结构化时行分隔有用）
        assert text == "岗位：气象工程师\n薪资：15-25K\n经验：3-5年"
        assert "<|LOC" not in text

    @pytest.mark.asyncio
    async def test_api_key_unset_returns_empty(self, monkeypatch):
        monkeypatch.setattr(siliconflow, "SILICONFLOW_API_KEY", "")
        assert await siliconflow.ocr_screenshot_text(b"fake") == ""


# ---------- ModelScope ----------

class TestModelScope:
    @pytest.mark.asyncio
    async def test_extract_code_block_json(self, monkeypatch):
        monkeypatch.setattr(modelscope, "MODELSCOPE_API_KEY", "sk-test")
        monkeypatch.setattr(modelscope, "_create_client", lambda timeout: _FakeClient())
        _FakeClient.content = '```\n[{"title": "环境工程师", "company": "成都环境集团"}]\n```'
        jobs = await modelscope.extract_jobs_from_screenshot(b"\x89PNG fake", "智联招聘")
        assert jobs == [{"title": "环境工程师", "company": "成都环境集团"}]

    @pytest.mark.asyncio
    async def test_extract_invalid_json_empty(self, monkeypatch):
        monkeypatch.setattr(modelscope, "MODELSCOPE_API_KEY", "sk-test")
        monkeypatch.setattr(modelscope, "_create_client", lambda timeout: _FakeClient())
        _FakeClient.content = "服务器繁忙"
        assert await modelscope.extract_jobs_from_screenshot(b"fake", "智联招聘") == []

    @pytest.mark.asyncio
    async def test_api_key_unset_raises(self, monkeypatch):
        monkeypatch.setattr(modelscope, "MODELSCOPE_API_KEY", "")
        with pytest.raises(ValueError):
            await modelscope.extract_jobs_from_screenshot(b"fake", "智联招聘")

    def test_priority_list_vl_default(self):
        # 未配置时默认能力优先、非思考在前（实测 Qwen3-VL 五模型可用）
        models = modelscope.model_priority_list("vl")
        assert models[0] == "Qwen/Qwen3-VL-235B-A22B-Instruct"
        assert models[1] == "Qwen/Qwen3-VL-30B-A3B-Instruct"
        assert "Qwen/Qwen3-VL-30B-A3B-Thinking" in models
        assert "Qwen/Qwen3-VL-8B-Instruct" in models
        assert "Qwen/Qwen3-VL-8B-Thinking" in models

    def test_priority_list_text_default(self):
        # 决策兜底链：匹配度×能力排序，能力顶级在前，flash 收尾兜底
        models = modelscope.model_priority_list("text")
        assert models[0] == "deepseek-ai/DeepSeek-V4-Pro"
        assert "Qwen/Qwen3-Next-80B-A3B-Instruct" in models
        assert "Qwen/Qwen3-235B-A22B-Thinking-2507" in models
        assert models[-1] == "deepseek-ai/DeepSeek-V4-Flash-0731"

    def test_priority_list_parses_comma_env(self, monkeypatch):
        monkeypatch.setattr(modelscope, "MODELSCOPE_VL_MODELS", "A/Model1, B/Model2 ,C/Model3")
        assert modelscope.model_priority_list("vl") == ["A/Model1", "B/Model2", "C/Model3"]

    def test_priority_list_falls_back_to_legacy_single(self, monkeypatch):
        monkeypatch.setattr(modelscope, "MODELSCOPE_VL_MODELS", "")
        monkeypatch.setattr(modelscope, "MODELSCOPE_VL_MODEL", "Old/VL-Model")
        assert modelscope.model_priority_list("vl") == ["Old/VL-Model"]

    @pytest.mark.asyncio
    async def test_chat_switches_model_on_429(self, monkeypatch):
        """429/额度不足自动切下一个模型，先进优先。"""
        monkeypatch.setattr(modelscope, "MODELSCOPE_API_KEY", "sk-test")
        assert len(modelscope.model_priority_list("vl")) >= 2
        first = modelscope.model_priority_list("vl")[0]

        class _SelectiveCompletions:
            async def create(self, **kwargs):
                if kwargs.get("model") == first:
                    raise RuntimeError("429 Too Many Requests")
                return _ok_response('{"from": "fallback"}')

        class _SelectiveClient:
            def __init__(self, *a, **k):
                self.chat = type("_C", (), {"completions": _SelectiveCompletions()})()

        monkeypatch.setattr(modelscope, "_create_client", lambda timeout: _SelectiveClient())
        raw = await modelscope.chat([{"role": "user", "content": "hi"}], kind="vl")
        assert '"from": "fallback"' in raw

    @pytest.mark.asyncio
    async def test_chat_all_models_fail_raises(self, monkeypatch):
        monkeypatch.setattr(modelscope, "MODELSCOPE_API_KEY", "sk-test")

        class _FailCompletions:
            async def create(self, **kwargs):
                raise RuntimeError("429 Too Many Requests")

        class _FailClient:
            def __init__(self, *a, **k):
                self.chat = type("_C", (), {"completions": _FailCompletions()})()

        monkeypatch.setattr(modelscope, "_create_client", lambda timeout: _FailClient())
        with pytest.raises(RuntimeError, match="全部模型失败"):
            await modelscope.chat([{"role": "user", "content": "hi"}], kind="vl")

    @pytest.mark.asyncio
    async def test_chat_switches_model_on_empty_content(self, monkeypatch):
        """200 但空内容（实测 DeepSeek-V4-Pro 偶发）视同失败，切下一个模型。"""
        monkeypatch.setattr(modelscope, "MODELSCOPE_API_KEY", "sk-test")
        first = modelscope.model_priority_list("vl")[0]

        class _EmptyCompletions:
            async def create(self, **kwargs):
                if kwargs.get("model") == first:
                    return _ok_response("")  # 空内容
                return _ok_response('{"from": "fallback"}')

        class _EmptyClient:
            def __init__(self, *a, **k):
                self.chat = type("_C", (), {"completions": _EmptyCompletions()})()

        monkeypatch.setattr(modelscope, "_create_client", lambda timeout: _EmptyClient())
        raw = await modelscope.chat([{"role": "user", "content": "hi"}], kind="vl")
        assert '"from": "fallback"' in raw


# ---------- 决策链：文本优先 ----------

class TestDecisionTextFirst:
    @pytest.mark.asyncio
    async def test_deepseek_text_returns_action_without_vision(self, monkeypatch):
        called = {"vision": False}

        async def fake_chat(prompt, **kwargs):
            return '{"action":"extract","reason":"已找到岗位列表"}'

        async def fail_vision(*args, **kwargs):
            called["vision"] = True
            raise AssertionError("视觉模型不应被调用")

        monkeypatch.setattr("src.llm.deepseek.chat", fake_chat)
        monkeypatch.setattr(decision_mod, "_call_model_scope", fail_vision)
        monkeypatch.setattr(decision_mod, "_call_glm", fail_vision)

        action = await decide_next_action(
            task="在智联招聘搜索「气象」岗位",
            state_signal="页面状态已发生变化",
            last_result="wait 成功",
            history_lines=["click:搜索(成功)"],
            inventory_text="0:输入框「关键词」\n1:按钮「搜索」",
            page_text="气象预报工程师 成都 15-20K",
            screenshot=b"",
        )
        assert action.action == "extract"
        assert called["vision"] is False

    @pytest.mark.asyncio
    async def test_deepseek_fail_falls_back_to_modelscope_text(self, monkeypatch):
        async def fail_deepseek(prompt, **kwargs):
            raise RuntimeError("DeepSeek API 欠费")

        captured: dict = {}

        async def fake_modelscope_chat(messages, **kwargs):
            captured["kind"] = kwargs.get("kind")
            return '{"action":"scroll","reason":"向下查看更多岗位"}'

        monkeypatch.setattr("src.llm.deepseek.chat", fail_deepseek)
        monkeypatch.setattr("src.llm.modelscope.chat", fake_modelscope_chat)

        action = await decide_next_action(
            task="在智联招聘搜索「气象」岗位",
            state_signal="页面状态已发生变化",
            last_result="wait 成功",
            history_lines=[],
            inventory_text="0:按钮「搜索」",
            page_text="加载中，请稍候，页面正在渲染搜索结果列表，稍后自动出现",
            screenshot=b"\x89PNG fake",
        )
        # DeepSeek 失败 → ModelScope 文本链（先进优先）兜底，仍在文本域不占视觉额度
        assert action.action == "scroll"
        assert captured["kind"] == "text"

    @pytest.mark.asyncio
    async def test_sparse_page_text_triggers_rapidocr(self, monkeypatch):
        captured: dict = {}

        async def fake_chat(prompt, **kwargs):
            captured["prompt"] = prompt
            return '{"action":"done","reason":"登录墙"}'

        async def fake_ocr(screenshot):
            assert screenshot == b"\x89PNG fake"
            return "请扫码登录后查看 手机号验证码"

        monkeypatch.setattr("src.llm.deepseek.chat", fake_chat)
        monkeypatch.setattr("src.ocr.rapid.extract_text_from_screenshot", fake_ocr)

        await decide_next_action(
            task="在BOSS直聘搜索「环境」岗位",
            state_signal="页面状态已发生变化",
            last_result="navigate 成功",
            history_lines=["navigate:目标URL(成功)"],
            inventory_text="0:按钮「扫码登录」",
            page_text="",  # 空页面文字 → 应触发 RapidOCR
            screenshot=b"\x89PNG fake",
        )
        assert "请扫码登录后查看" in captured["prompt"]

    @pytest.mark.asyncio
    async def test_rich_page_text_skips_rapidocr(self, monkeypatch):
        captured: dict = {}

        async def fake_chat(prompt, **kwargs):
            captured["prompt"] = prompt
            return '{"action":"done","reason":"登录墙"}'

        async def fail_ocr(screenshot):
            raise AssertionError("页面文字充足时不应调用 OCR")

        monkeypatch.setattr("src.llm.deepseek.chat", fake_chat)
        monkeypatch.setattr("src.ocr.rapid.extract_text_from_screenshot", fail_ocr)

        await decide_next_action(
            task="在BOSS直聘搜索「环境」岗位",
            state_signal="页面状态已发生变化",
            last_result="navigate 成功",
            history_lines=[],
            inventory_text="0:按钮「扫码登录」",
            page_text="扫码登录后查看完整职位信息 请用 App 扫码 登录后享受更多功能" * 3,
            screenshot=b"\x89PNG fake",
        )
        assert "扫码登录后查看完整职位信息" in captured["prompt"]
