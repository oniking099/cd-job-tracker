"""
行业排除过滤器：
排除特定行业的岗位——游戏、智能驾驶、特定LLM方向等。
注意：需要语义判断，不能简单关键词匹配。
比如"气象局前端开发"不属于"前端LLM"。
"""
from __future__ import annotations

from src.models import Job
from src.filters.common import is_preference

# 行业排除关键词（必须在明确的行业上下文中才算命中）
INDUSTRY_EXCLUDE: dict[str, list[str]] = {
    "游戏": ["游戏", "手游", "页游", "电竞", "游戏开发", "游戏公司", "游戏行业"],
    "智能驾驶": ["智能驾驶", "自动驾驶", "无人驾驶", "ADAS", "智驾"],
    "前端LLM": ["前端LLM", "LLM前端", "大模型前端"],
    "后端LLM": ["后端LLM", "LLM后端", "大模型后端"],
    "大数据LLM": ["大数据LLM", "LLM大数据", "大模型数据"],
}

# 这些词单独出现不一定排除，需要上下文验证
AMBIENT_KEYWORDS = ["游戏", "驾驶"]


def _check_industry_match(text: str) -> tuple[bool, str, str, int]:
    """
    检查文本是否匹配排除行业。
    返回 (是否排除, 匹配到的行业, 命中的关键词, 命中位置)。
    命中为软性偏好（"X优先"）时不算排除。
    """
    for industry, keywords in INDUSTRY_EXCLUDE.items():
        for kw in keywords:
            idx = text.find(kw)
            if idx == -1:
                continue
            # 软性偏好（"游戏行业经验者优先"）→ 非刚性，保留
            if is_preference(text, idx, idx + len(kw)):
                continue
            return True, industry, kw, idx
    return False, "", "", -1


def filter_industry(jobs: list[Job]) -> list[Job]:
    """
    行业排除主函数。
    注意：当前版本使用规则匹配。
    后续可通过 LLM 做更精准的语义判断（区分"气象局前端开发" vs "前端LLM"）。
    """
    result: list[Job] = []

    for job in jobs:
        full_text = f"{job.title} {job.responsibilities} {job.requirements} {job.company}"
        exclude, industry, _kw, _idx = _check_industry_match(full_text)

        if exclude:
            job.excluded = True
            job.exclude_reason = f"行业排除: {industry}"
        result.append(job)

    return result
