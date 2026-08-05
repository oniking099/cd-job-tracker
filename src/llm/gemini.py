"""
Gemini Flash-Lite 视觉提取客户端（备用）
当 Qwen-VL 额度耗尽时自动切换。
"""
from __future__ import annotations

import base64
import logging
from openai import AsyncOpenAI

from src.config import (
    AGENT_EXTRACT_TIMEOUT,
    AGENT_LLM_TIMEOUT,
    GEMINI_API_KEY,
    GEMINI_MODEL,
)

logger = logging.getLogger(__name__)


def _create_client(timeout: float = AGENT_LLM_TIMEOUT) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=GEMINI_API_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        timeout=timeout,
    )


async def extract_jobs_from_screenshot(
    screenshot_bytes: bytes | list[bytes],
    platform: str,
) -> list[dict]:
    """
    从招聘网站截图（PNG bytes）中提取结构化岗位信息（Gemini 备用通道）。
    支持单图（bytes）或多图（list[bytes]）。
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 未设置")

    screenshots = [screenshot_bytes] if isinstance(screenshot_bytes, bytes) else list(screenshot_bytes)
    if not screenshots:
        return []

    client = _create_client(timeout=AGENT_EXTRACT_TIMEOUT)

    content: list[dict] = [{
        "type": "text",
        "text": f"""Extract all job listings from these {platform} search results screenshots (Chengdu area, {len(screenshots)} images top-to-bottom, may overlap; dedupe).
For each job return JSON with: title, company, salary_text, location, requirements (full original text, do not summarize or omit), responsibilities (full original text, do not summarize or omit), url, hr_active (boolean), posted_date.
If the screenshots contain no job description/requirements body, return empty string for those fields. Never fabricate content.
Return ONLY a JSON array, no markdown formatting.""",
    }]
    for sb in screenshots:
        image_b64 = base64.b64encode(sb).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        })

    response = await client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=4000,
    )

    import json
    raw = response.choices[0].message.content or "[]"
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.warning(f"Gemini JSON解析失败: {e}, raw={raw[:200]}")
        return []
