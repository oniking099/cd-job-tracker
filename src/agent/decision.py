"""
决策层：调用多模态 LLM，从截图 + 元素清单决定下一步动作。

Qwen-VL-Max 主力，Gemini Flash-Lite 兜底（与提取层同一降级模式）。
输出用 prompt 约束的纯 JSON（Qwen-VL 不支持 response_format:json_object，联网确认），
宽容解析 + Pydantic 校验，失败重试并在重试时注入上次错误。
"""
from __future__ import annotations

import base64
import logging

from openai import AsyncOpenAI

from src.config import (
    QWENVL_API_KEY,
    QWENVL_BASE_URL,
    QWENVL_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    AGENT_LLM_TIMEOUT,
)
from src.agent.actions import (
    AgentAction,
    SYSTEM_PROMPT,
    build_decision_user_message,
    parse_action,
)

logger = logging.getLogger(__name__)

_MAX_ACTION_TOKENS = 400


async def decide_next_action(
    task: str,
    state_signal: str,
    last_result: str,
    history_lines: list[str],
    inventory_text: str,
    page_text: str,
    screenshot: bytes,
) -> AgentAction:
    """
    主决策入口。Qwen-VL → Gemini 逐 provider 尝试，整体重试 1 次。
    全部失败抛 RuntimeError。
    """
    last_error: str | None = None
    for attempt in range(2):
        providers = (
            ("Qwen-VL", _call_qwen_vl),
            ("Gemini", _call_gemini),
        )
        for name, call in providers:
            try:
                raw = await call(
                    task=task,
                    state_signal=state_signal,
                    last_result=last_result,
                    history_lines=history_lines,
                    inventory_text=inventory_text,
                    page_text=page_text,
                    screenshot=screenshot,
                    error_hint=last_error,
                )
                return parse_action(raw)
            except Exception as e:
                logger.warning(f"[agent] {name} 决策失败(第{attempt+1}次): {e}")
                last_error = str(e)

    raise RuntimeError(f"决策模型全部失败: {last_error}")


def _build_user_content(
    task: str,
    state_signal: str,
    last_result: str,
    history_lines: list[str],
    inventory_text: str,
    page_text: str,
    screenshot: bytes,
    error_hint: str | None,
) -> list[dict]:
    text = build_decision_user_message(
        task=task,
        state_signal=state_signal,
        last_result=last_result,
        history_lines=history_lines,
        inventory_text=inventory_text,
        page_text_excerpt=page_text,
    )
    if error_hint:
        text += (
            f"\n\n（警告：上次解析你的输出失败：{error_hint}。"
            "请严格只输出一个合法 JSON 对象，不要代码块、不要多余文字。）"
        )

    content: list[dict] = [{"type": "text", "text": text}]
    if screenshot:
        image_b64 = base64.b64encode(screenshot).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        })
    return content


async def _call_qwen_vl(
    *,
    task: str,
    state_signal: str,
    last_result: str,
    history_lines: list[str],
    inventory_text: str,
    page_text: str,
    screenshot: bytes,
    error_hint: str | None,
) -> str:
    if not QWENVL_API_KEY:
        raise ValueError("QWENVL_API_KEY 未设置")
    client = AsyncOpenAI(
        api_key=QWENVL_API_KEY,
        base_url=QWENVL_BASE_URL,
        timeout=AGENT_LLM_TIMEOUT,
    )
    response = await client.chat.completions.create(
        model=QWENVL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_content(
                    task, state_signal, last_result, history_lines,
                    inventory_text, page_text, screenshot, error_hint,
                ),
            },
        ],
        temperature=0.1,
        max_tokens=_MAX_ACTION_TOKENS,
    )
    return response.choices[0].message.content or ""


async def _call_gemini(
    *,
    task: str,
    state_signal: str,
    last_result: str,
    history_lines: list[str],
    inventory_text: str,
    page_text: str,
    screenshot: bytes,
    error_hint: str | None,
) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 未设置")
    client = AsyncOpenAI(
        api_key=GEMINI_API_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        timeout=AGENT_LLM_TIMEOUT,
    )
    response = await client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_content(
                    task, state_signal, last_result, history_lines,
                    inventory_text, page_text, screenshot, error_hint,
                ),
            },
        ],
        temperature=0.1,
        max_tokens=_MAX_ACTION_TOKENS,
    )
    return response.choices[0].message.content or ""
