"""
Gemini Flash-Lite 视觉提取客户端（备用）
当 Qwen-VL 额度耗尽时自动切换。
"""
from __future__ import annotations

import base64
import logging
from openai import AsyncOpenAI

from src.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)


def _create_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=GEMINI_API_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )


async def extract_jobs_from_screenshot(
    screenshot_bytes: bytes,
    platform: str,
) -> list[dict]:
    """
    从招聘网站截图中提取结构化岗位信息（Gemini 备用通道）。
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 未设置")

    image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
    image_url = f"data:image/png;base64,{image_b64}"

    client = _create_client()

    response = await client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""Extract all job listings from this {platform} search results screenshot (Chengdu area).
For each job return JSON with: title, company, salary_text, location, requirements, responsibilities, url, hr_active (boolean), posted_date.
Return ONLY a JSON array, no markdown formatting.""",
                },
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }],
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
