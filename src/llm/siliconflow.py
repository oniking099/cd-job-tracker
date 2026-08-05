"""
SiliconFlow 硅基流动视觉客户端（OpenAI 兼容）。

双角色：
1. `extract_jobs_from_screenshot` — 视觉提取兜底（Qwen3-VL-8B，走新用户免费 tokens，
   与 qwen_vl/gemini 同签名，可无缝接入决策/提取降级链）。
2. `ocr_screenshot_text` — 云端 OCR 兜底（PaddlePaddle/PaddleOCR-VL-1.5 永久免费，
   本地 RapidOCR 识别为空/失败时使用；返回含位置标记的原始文本，喂 DeepSeek 结构化）。
"""
from __future__ import annotations

import base64
import json
import logging
import re

from openai import AsyncOpenAI

from src.config import (
    AGENT_EXTRACT_TIMEOUT,
    AGENT_LLM_TIMEOUT,
    SILICONFLOW_API_KEY,
    SILICONFLOW_BASE_URL,
    SILICONFLOW_OCR_MODEL,
    SILICONFLOW_VL_MODEL,
)

logger = logging.getLogger(__name__)

# PaddleOCR-VL-1.5 返回文本中夹带的位置标记，如 <|LOC_1|>，提取正文时剔除
_LOC_MARKER_RE = re.compile(r"<\|LOC_\d+\|>")


def _create_client(timeout: float) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=SILICONFLOW_API_KEY,
        base_url=SILICONFLOW_BASE_URL,
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
    从招聘网站截图（PNG bytes）中提取结构化岗位信息（SiliconFlow 兜底通道）。
    支持单图（bytes）或多图（list[bytes]）。
    """
    if not SILICONFLOW_API_KEY:
        raise ValueError("SILICONFLOW_API_KEY 未设置")

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
        model=SILICONFLOW_VL_MODEL,
        messages=[{"role": "user", "content": _build_image_content(screenshots, prompt)}],
        temperature=0.1,
        max_tokens=4000,
    )

    raw = response.choices[0].message.content or "[]"
    return _parse_json_array(raw)


async def ocr_screenshot_text(screenshot_bytes: bytes | list[bytes]) -> str:
    """云端 OCR 兜底：PaddleOCR-VL-1.5（免费）读截图 → 拼接文本。

    本地 RapidOCR 识别为空/失败时调用。冷启动可能较慢，超时给足。
    """
    if not SILICONFLOW_API_KEY:
        logger.warning("[ocr] SILICONFLOW_API_KEY 未设置，跳过云端 OCR 兜底")
        return ""

    shots = [screenshot_bytes] if isinstance(screenshot_bytes, bytes) else list(screenshot_bytes)
    if not shots:
        return ""

    try:
        client = _create_client(timeout=max(AGENT_EXTRACT_TIMEOUT, 150))
        response = await client.chat.completions.create(
            model=SILICONFLOW_OCR_MODEL,
            messages=[{
                "role": "user",
                "content": _build_image_content(
                    shots,
                    f"请逐张识别图片中的全部文字，共 {len(shots)} 张，按图片顺序输出，保留原文顺序与换行。",
                ),
            }],
            temperature=0.0,
            max_tokens=8192,
        )
        raw = response.choices[0].message.content or ""
        text = _LOC_MARKER_RE.sub("", raw)
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not text:
            logger.warning("[ocr] SiliconFlow 云端 OCR 未识别出文本")
            return ""
        logger.info("[ocr] SiliconFlow 云端 OCR 识别成功")
        return text
    except Exception as e:
        logger.warning(f"[ocr] SiliconFlow 云端 OCR 失败: {e}")
        return ""


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
        logger.warning(f"SiliconFlow JSON解析失败: {e}, raw={raw[:200]}")
        return []
