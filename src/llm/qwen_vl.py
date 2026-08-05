"""
Qwen-VL-Max 视觉提取客户端（主力）
通过阿里云百炼平台 API，使用多模态能力从截图中提取结构化招聘数据。
API 兼容 OpenAI 格式。
"""
from __future__ import annotations

import base64
import logging
from openai import AsyncOpenAI

from src.config import (
    AGENT_EXTRACT_TIMEOUT,
    AGENT_LLM_TIMEOUT,
    QWENVL_API_KEY,
    QWENVL_BASE_URL,
    QWENVL_MODEL,
)

logger = logging.getLogger(__name__)


def _create_client(timeout: float = AGENT_LLM_TIMEOUT) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=QWENVL_API_KEY,
        base_url=QWENVL_BASE_URL,
        timeout=timeout,
    )


async def extract_jobs_from_screenshot(
    screenshot_bytes: bytes | list[bytes],
    platform: str,
) -> list[dict]:
    """
    从招聘网站截图（PNG bytes）中提取结构化岗位信息。
    screenshot_bytes 支持单图（bytes）或多图（list[bytes]，滚动后的多个视口截图）。
    将图片转为 base64，通过 Qwen-VL-Max 视觉提取。
    """
    if not QWENVL_API_KEY:
        raise ValueError("QWENVL_API_KEY 未设置")

    # 统一为多图列表
    screenshots = [screenshot_bytes] if isinstance(screenshot_bytes, bytes) else list(screenshot_bytes)
    if not screenshots:
        return []

    client = _create_client(timeout=AGENT_EXTRACT_TIMEOUT)

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

    response = await client.chat.completions.create(
        model=QWENVL_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=4000,
    )

    import json
    raw = response.choices[0].message.content or "[]"
    # 清理可能的 markdown 包裹
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.warning(f"Qwen-VL JSON解析失败: {e}, raw={raw[:200]}")
        return []
