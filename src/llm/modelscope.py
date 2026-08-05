"""
ModelScope 魔搭 api-inference 客户端（阿里云百炼托管，OpenAI 兼容）。

配额规则（用户实测确认，2026-08-05）：
- 每日总配额 2000 次（所有模型加和）；单模型最高 200 次/天，先进模型实际更少。
- 因此**不做单选**：把可用模型全部列进优先列表，429/额度不足自动切下一个。

两套模型链（都用同一 fallback 机制）：
- `MODELSCOPE_VL_MODELS`   视觉链（VL 模型才能读图）：Qwen3-VL-235B-A22B-Instruct 优先，
                           429/空内容降级到 30B-A3B-Instruct → 30B-A3B-Thinking → 8B-Instruct
                           → 8B-Thinking。（实测 Qwen2.5-VL 全系与 235B-Thinking 报 no provider；
                           中文「千问/」前缀全部 400 Invalid，英文 Qwen/ 是唯一正确写法）
- `MODELSCOPE_TEXT_MODELS` 文本链（决策兜底，DeepSeek 失败时）：DeepSeek-V4-Pro 优先，
                           429/空内容降级到 Qwen3-Next-80B-A3B-Instruct → Qwen3-235B-A22B-Thinking-2507
                           → Qwen3-30B-A3B-Thinking-2507 → Qwen3.5-397B-A17B → Qwen3.5-35B-A3B
                           → DeepSeek-V4-Flash-0731（flash 兜底收尾）。
"""
from __future__ import annotations

import base64
import json
import logging

from openai import AsyncOpenAI

from src.config import (
    AGENT_EXTRACT_TIMEOUT,
    AGENT_LLM_TIMEOUT,
    MODELSCOPE_API_KEY,
    MODELSCOPE_BASE_URL,
    MODELSCOPE_TEXT_MODELS,
    MODELSCOPE_VL_MODEL,
    MODELSCOPE_VL_MODELS,
)

logger = logging.getLogger(__name__)


def _create_client(timeout: float = AGENT_LLM_TIMEOUT) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=MODELSCOPE_API_KEY,
        base_url=MODELSCOPE_BASE_URL,
        timeout=timeout,
    )


def model_priority_list(kind: str = "vl") -> list[str]:
    """返回指定链的模型优先列表（先进优先）。

    kind="vl" 取 MODELSCOPE_VL_MODELS；kind="text" 取 MODELSCOPE_TEXT_MODELS。
    兼容旧版单模型变量 MODELSCOPE_VL_MODEL 作兜底；全空则给默认值。
    """
    cfg = MODELSCOPE_VL_MODELS if kind == "vl" else MODELSCOPE_TEXT_MODELS
    models = [m.strip() for m in (cfg or "").split(",") if m.strip()]
    if not models and kind == "vl":
        models = [m for m in (MODELSCOPE_VL_MODEL or "").split(",") if m.strip()]
    if not models:
        models = (
            [
                "Qwen/Qwen3-VL-235B-A22B-Instruct",
                "Qwen/Qwen3-VL-30B-A3B-Instruct",
                "Qwen/Qwen3-VL-30B-A3B-Thinking",
                "Qwen/Qwen3-VL-8B-Instruct",
                "Qwen/Qwen3-VL-8B-Thinking",
            ]
            if kind == "vl"
            else [
                "deepseek-ai/DeepSeek-V4-Pro",
                "Qwen/Qwen3-Next-80B-A3B-Instruct",
                "Qwen/Qwen3-235B-A22B-Thinking-2507",
                "Qwen/Qwen3-30B-A3B-Thinking-2507",
                "Qwen/Qwen3.5-397B-A17B",
                "Qwen/Qwen3.5-35B-A3B",
                "deepseek-ai/DeepSeek-V4-Flash-0731",
            ]
        )
    return models


async def chat(
    messages: list[dict],
    *,
    temperature: float = 0.1,
    max_tokens: int = 4000,
    timeout: float = AGENT_EXTRACT_TIMEOUT,
    kind: str = "vl",
) -> str:
    """按模型优先级逐模型调用，429/额度不足/异常自动切下一个模型。

    任一模型成功即返回内容；全部失败抛 RuntimeError（错误信息带最后一条失败原因，
    供上层重试/降级判断）。这是 ModelScope 侧唯一的调用入口。
    """
    if not MODELSCOPE_API_KEY:
        raise ValueError("MODELSCOPE_API_KEY 未设置")
    models = model_priority_list(kind)
    last_error = ""
    for model in models:
        try:
            client = _create_client(timeout=timeout)
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                # 200 但空内容（实测 DeepSeek-V4-Pro 偶发）：视同失败切下一个模型。
                # 否则空串会让上层 parse 失败、整条链白跑而跳过其余模型。
                raise RuntimeError("返回空内容")
            logger.info(f"[modelscope] {kind} 链模型 {model} 调用成功")
            return content
        except Exception as e:
            last_error = f"{model}: {e}"
            logger.warning(f"[modelscope] {kind} 链模型 {model} 失败: {e}，切换下一个")

    raise RuntimeError(f"ModelScope {kind} 链全部模型失败: {last_error}")


async def extract_jobs_from_screenshot(
    screenshot_bytes: bytes | list[bytes],
    platform: str,
) -> list[dict]:
    """
    从招聘网站截图（PNG bytes）中提取结构化岗位信息（ModelScope 免费通道）。
    支持单图（bytes）或多图（list[bytes]）。内部按 VL 模型优先级自动降级。
    """
    screenshots = [screenshot_bytes] if isinstance(screenshot_bytes, bytes) else list(screenshot_bytes)
    if not screenshots:
        return []

    content: list[dict] = [{
        "type": "text",
        "text": f"""这是 {platform} 的搜索结果页面截图（共 {len(screenshots)} 张，按页面从上到下排列，可能有重叠）。
请从中提取所有成都地区招聘岗位的信息，多张截图里的同一岗位只保留一次。

对每个岗位，提取以下字段：
- title: 岗位名称
- company: 企业名称
- salary_text: 原始薪资文本（完整保留）
- location: 工作地点
- requirements: 岗位要求（完整原文，不要概括，不要省略）
- responsibilities: 岗位职责（完整原文，不要概括，不要省略）
- url: 如果有详情页链接，提取完整 URL
- hr_active: HR是否活跃，根据页面上的"活跃""刚刚在线""今日回复"等标记判断，有则true
- posted_date: 发布日期

注意：若截图中没有岗位职责/要求正文，对应字段返回空字符串，绝对禁止编造。
返回纯 JSON 数组格式，不要markdown代码块：""",
    }]
    for sb in screenshots:
        image_b64 = base64.b64encode(sb).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        })

    raw = await chat(
        [{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=4000,
        timeout=AGENT_EXTRACT_TIMEOUT,
        kind="vl",
    )
    return _parse_json_array(raw)


def _parse_json_array(raw: str) -> list[dict]:
    """清理 markdown 包裹后解析 JSON 数组，失败返回空列表。"""
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw.strip())
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as e:
        logger.warning(f"ModelScope JSON解析失败: {e}, raw={raw[:200]}")
        return []
