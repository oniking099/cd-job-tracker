"""
决策层：从页面状态决定下一步动作。

文本优先（用户约束 + 视觉模型欠费/额度问题的根治）：
1. DeepSeek 文本决策：DOM 可交互元素清单 + 页面 innerText 摘要（页面文字过少时补
   RapidOCR 读字）→ 文本 LLM 输出动作 JSON。零视觉 API 调用，成本分毫。
2. ModelScope 文本兜底：DeepSeek 失败时用 Qwen3.5 文本链（397B→35B→27B，
   429 自动降级），仍不占视觉模型额度。
3. 视觉兜底链：ModelScope-VL（235B→72B 自动降级）→ SiliconFlow → Gemini → Qwen-VL。
   仅在文本决策全失败时才动用多模态。

输出用 prompt 约束的纯 JSON，宽容解析 + Pydantic 校验，失败重试并注入上次错误。
"""
from __future__ import annotations

import base64
import logging

from openai import AsyncOpenAI

from src.config import (
    AGENT_LLM_TIMEOUT,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    QWENVL_API_KEY,
    QWENVL_BASE_URL,
    QWENVL_MODEL,
    SILICONFLOW_API_KEY,
    SILICONFLOW_BASE_URL,
    SILICONFLOW_VL_MODEL,
)
from src.agent.actions import (
    AgentAction,
    SYSTEM_PROMPT,
    build_decision_user_message,
    parse_action,
)

logger = logging.getLogger(__name__)

_MAX_ACTION_TOKENS = 400
_TEXT_EXCERPT_MIN = 40  # 页面 innerText 少于该字符数时补 RapidOCR 读字


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
    主决策入口。DeepSeek 文本优先 → 视觉链逐 provider，整体重试 1 次。
    全部失败抛 RuntimeError。
    """
    last_error: str | None = None
    for attempt in range(2):
        providers: list[tuple[str, object]] = [
            ("DeepSeek-文本", _call_deepseek_text),
            ("ModelScope-文本", _call_modelscope_text),
            ("ModelScope-视觉", _call_model_scope),
            ("SiliconFlow", _call_siliconflow),
            ("Gemini", _call_gemini),
            ("Qwen-VL", _call_qwen_vl),
        ]
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


def _build_vision_content(
    task: str,
    state_signal: str,
    last_result: str,
    history_lines: list[str],
    inventory_text: str,
    page_text: str,
    screenshot: bytes,
    error_hint: str | None,
) -> list[dict]:
    """视觉决策内容：文本 + 截图。"""
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


async def _build_text_prompt(
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
    """文本决策 prompt：DOM 元素清单 + 页面文本（过短时补 RapidOCR）。

    零视觉 API。页面 innerText 足够时直接决策；文字过少（JS 渲染页）才补
    一次本地 RapidOCR 读字，仍不依赖视觉模型。DeepSeek / ModelScope 文本共用。
    """
    excerpt = page_text or ""
    if len(excerpt.strip()) < _TEXT_EXCERPT_MIN and screenshot:
        try:
            from src.ocr.rapid import extract_text_from_screenshot
            ocr_text = await extract_text_from_screenshot(screenshot)
            if ocr_text:
                excerpt = ocr_text
        except Exception as e:
            logger.warning(f"[agent] 决策用 RapidOCR 读字失败: {e}")

    text = build_decision_user_message(
        task=task,
        state_signal=state_signal,
        last_result=last_result,
        history_lines=history_lines,
        inventory_text=inventory_text,
        page_text_excerpt=excerpt[:1200],
    )
    if error_hint:
        text += (
            f"\n\n（警告：上次解析你的输出失败：{error_hint}。"
            "请严格只输出一个合法 JSON 对象，不要代码块、不要多余文字。）"
        )
    return text


async def _call_deepseek_text(
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
    """文本决策首选：DeepSeek（零视觉 API）。"""
    from src.llm.deepseek import chat

    text = await _build_text_prompt(
        task=task, state_signal=state_signal, last_result=last_result,
        history_lines=history_lines, inventory_text=inventory_text,
        page_text=page_text, screenshot=screenshot, error_hint=error_hint,
    )
    raw = await chat(
        text,
        system=SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=_MAX_ACTION_TOKENS,
    )
    return raw or ""


async def _call_modelscope_text(
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
    """ModelScope 文本决策兜底（DeepSeek 失败/不可用时）。

    文本模型链 Qwen3.5-397B→35B→27B 自动降级，先进优先、429 切下一个。
    仍在文本域内，不占视觉模型额度。
    """
    from src.llm.modelscope import chat as modelscope_chat

    text = await _build_text_prompt(
        task=task, state_signal=state_signal, last_result=last_result,
        history_lines=history_lines, inventory_text=inventory_text,
        page_text=page_text, screenshot=screenshot, error_hint=error_hint,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    raw = await modelscope_chat(
        messages,
        temperature=0.1,
        max_tokens=_MAX_ACTION_TOKENS,
        timeout=AGENT_LLM_TIMEOUT,
        kind="text",
    )
    return raw


async def _call_model_scope(
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
    """ModelScope 视觉决策兜底：VL 模型链（235B→72B）自动降级。"""
    from src.llm.modelscope import chat as modelscope_chat

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_vision_content(
                task, state_signal, last_result, history_lines,
                inventory_text, page_text, screenshot, error_hint,
            ),
        },
    ]
    raw = await modelscope_chat(
        messages,
        temperature=0.1,
        max_tokens=_MAX_ACTION_TOKENS,
        timeout=AGENT_LLM_TIMEOUT,
        kind="vl",
    )
    return raw


async def _call_siliconflow(
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
    if not SILICONFLOW_API_KEY:
        raise ValueError("SILICONFLOW_API_KEY 未设置")
    client = AsyncOpenAI(
        api_key=SILICONFLOW_API_KEY,
        base_url=SILICONFLOW_BASE_URL,
        timeout=AGENT_LLM_TIMEOUT,
    )
    response = await client.chat.completions.create(
        model=SILICONFLOW_VL_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_vision_content(
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
                "content": _build_vision_content(
                    task, state_signal, last_result, history_lines,
                    inventory_text, page_text, screenshot, error_hint,
                ),
            },
        ],
        temperature=0.1,
        max_tokens=_MAX_ACTION_TOKENS,
    )
    return response.choices[0].message.content or ""


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
                "content": _build_vision_content(
                    task, state_signal, last_result, history_lines,
                    inventory_text, page_text, screenshot, error_hint,
                ),
            },
        ],
        temperature=0.1,
        max_tokens=_MAX_ACTION_TOKENS,
    )
    return response.choices[0].message.content or ""
