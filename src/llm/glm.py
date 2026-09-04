"""
GLM 视觉客户端（智谱 GLM Coding Plan，OpenAI 兼容）。

GLM-5.3-Flash 是 GLM-5 系列首个原生多模态模型（可读截图），Coding Plan 内
额度为 GLM-5.3 的 3 倍，非高峰时段（含周末全天）积分减半——视觉链末位兜底
（ModelScope 免费额度在前，GLM 收尾）。

⚠️ base_url 必须用 Coding Plan 专属端点 /api/coding/paas/v4；
   普通平台端点 /api/paas/v4 扣的是余额而非套餐额度。
思考模型参数遵循官方推荐 temperature=1 / top_p=0.95（低温易复读）。
"""
from __future__ import annotations

import base64
import json
import logging

from openai import AsyncOpenAI

from src.config import (
    AGENT_EXTRACT_TIMEOUT,
    GLM_API_KEY,
    GLM_BASE_URL,
    GLM_VL_MODEL,
)

logger = logging.getLogger(__name__)


def _create_client(timeout: float) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=GLM_API_KEY,
        base_url=GLM_BASE_URL,
        timeout=timeout,
    )


def _build_image_content(screenshots: list[bytes], prompt: str) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for sb in screenshots:
        image_b64 = base64.b64encode(sb).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        })
    return content


async def extract_jobs_from_screenshot(
    screenshot_bytes: bytes | list[bytes],
    platform: str,
) -> list[dict]:
    """
    从招聘网站截图（PNG bytes）中提取结构化岗位信息（GLM 视觉通道）。
    与 qwen_vl/siliconflow/modelscope 同签名，接入提取视觉兜底链。
    """
    if not GLM_API_KEY:
        raise ValueError("GLM_API_KEY 未设置")

    screenshots = [screenshot_bytes] if isinstance(screenshot_bytes, bytes) else list(screenshot_bytes)
    if not screenshots:
        return []

    client = _create_client(timeout=AGENT_EXTRACT_TIMEOUT)

    prompt = (
        f"这是 {platform} 的搜索结果页面截图（共 {len(screenshots)} 张，按页面从上到下排列，可能有重叠）。\n"
        "请从中提取所有成都地区招聘岗位的信息，多张截图里的同一岗位只保留一次。\n\n"
        "对每个岗位，提取以下字段：\n"
        "- title: 岗位名称\n"
        "- company: 企业名称\n"
        "- salary_text: 原始薪资文本（完整保留）\n"
        "- location: 工作地点\n"
        "- requirements: 岗位要求（完整原文，不要概括，不要省略）\n"
        "- responsibilities: 岗位职责（完整原文，不要概括，不要省略）\n"
        "- url: 如果有详情页链接，提取完整 URL\n"
        "- hr_active: HR是否活跃，根据页面上的\"活跃\"\"刚刚在线\"\"今日回复\"等标记判断，有则true\n"
        "- posted_date: 发布日期\n\n"
        "注意：若截图中没有岗位职责/要求正文，对应字段返回空字符串，绝对禁止编造。\n"
        "返回纯 JSON 数组格式，不要markdown代码块："
    )

    response = await client.chat.completions.create(
        model=GLM_VL_MODEL,
        messages=[{"role": "user", "content": _build_image_content(screenshots, prompt)}],
        temperature=1.0,
        top_p=0.95,
        max_tokens=4000,
    )

    raw = response.choices[0].message.content or "[]"
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
        logger.warning(f"GLM JSON解析失败: {e}, raw={raw[:200]}")
        return []
