"""
DeepSeek 客户端：文本推理、职位匹配、企业分类辅助。
使用 OpenAI 兼容 SDK，模型 deepseek-chat（映射到 v4-flash）。
"""
from __future__ import annotations

import logging
from openai import AsyncOpenAI

from src.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)


def create_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )


async def chat(
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2000,
) -> str:
    """单次 LLM 调用"""
    client = create_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


async def suggest_search_terms(
    theme: str,
    found_count: int,
    searched: list[str],
    target_delta: int,
) -> list[str]:
    """LLM 根据当前搜索结果，建议补充搜索关键词"""
    prompt = f"""你是招聘搜索引擎专家。当前搜索主题是"{theme}"，已搜索过的关键词：
{chr(10).join(f'- {s}' for s in searched)}

找到了 {found_count} 条结果，希望再找到约 {target_delta} 条新结果。

请建议 3-5 个新的搜索关键词（仅关键词，每行一个，不要序号和解释），用于在成都地区招聘网站上搜索。
要求：与主题相关但不与已搜索关键词重复，能覆盖被遗漏的岗位表述。"""

    raw = await chat(prompt, system="你是一个精确的搜索引擎专家。", temperature=0.7)
    keywords = [kw.strip().lstrip("- ").strip() for kw in raw.strip().split("\n") if kw.strip()]
    # 去重、去空
    return [kw for kw in keywords if kw and kw not in searched][:5]


async def parse_job_from_screenshot(
    screenshot_markdown: str,
    platform: str,
) -> list[dict]:
    """
    从页面截图（多模态模型的输出文本）中提取结构化招聘信息。
    配合 Qwen-VL / Gemini 的多模态输出使用。
    """
    prompt = f"""从以下 {platform} 搜索结果页面内容中，提取所有成都地区的招聘岗位信息。
对每个岗位返回 JSON 数组，字段：
- title: 岗位名称
- company: 企业名称
- salary_text: 原始薪资文本
- location: 工作地点
- requirements: 岗位要求（完整原文，不要概括，不要省略）
- responsibilities: 岗位职责（完整原文，不要概括，不要省略）
- url: 详情页链接（如果页面中有）
- hr_active: HR是否活跃（true/false）

注意：若页面内容中没有岗位职责/要求正文，对应字段返回空字符串，绝对禁止编造。
只返回 JSON 数组，不要其他内容。"""

    raw = await chat(prompt + "\n\n页面内容：\n" + screenshot_markdown, temperature=0.1)
    import json
    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


async def cross_platform_dedup(
    jobs: list[dict],
) -> list[dict]:
    """LLM 辅助的跨平台去重"""
    if len(jobs) <= 1:
        return jobs

    prompt = f"""以下是从多个招聘平台收集的成都岗位，可能包含重复（同一公司同一岗位在不同平台发布）。
请找出重复的条目，返回去重后的索引列表。

岗位列表：
{chr(10).join(f'{i}. [{j.get("platform","")}] {j.get("company","")} - {j.get("title","")}' for i, j in enumerate(jobs))}

请用 JSON 数组格式返回应保留的索引（0-based），如 [0, 2, 5, 7]。"""

    raw = await chat(prompt, temperature=0.1, max_tokens=500)
    import json
    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        indices = json.loads(raw)
        return [jobs[i] for i in indices if 0 <= i < len(jobs)]
    except Exception as e:
        logger.warning(f"LLM跨平台去重失败，返回原始列表: {e}")
        return jobs
