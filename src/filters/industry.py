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
    # 医药/临床行业排除（用户 2026-08-07）：医学临床类岗位不属于目标领域。
    # 只用强信号词（岗位职能/明确专业要求），不用"医疗"这种易误伤词
    # （"医疗废物处理"是环保岗正文常见词）。
    "医学临床": [
        "临床监察", "临床协调", "临床研究", "临床试验", "临床数据管理",
        "CRA", "CRC", "药物警戒", "药品注册", "药品注册专员",
        "医药代表", "医药销售", "医学联络官", "医学经理",
        "药剂师", "药师", "临床医学专业", "医药学相关专业",
        "药学专业", "护理专业", "医学影像", "医学检验",
    ],
    # 测绘/GIS/遥感排除（用户 2026-08-07）：明确要求这三个专业的不要。
    # 仅匹配明确专业/岗位词，避免误伤"气象遥感"等用到遥感技术的目标岗
    # （由 _is_surveying_context 做气象/大气/环境豁免）。
    "测绘GIS遥感": [
        "测绘工程", "测绘技术", "测绘员", "测量工程", "测量员",
        "GIS开发", "GIS工程师", "地理信息系统",
        "遥感工程", "遥感技术员", "遥感科学与技术",
    ],
}

# 这些词单独出现不一定排除，需要上下文验证
AMBIENT_KEYWORDS = ["游戏", "驾驶"]

# 测绘/GIS/遥感排除的目标领域豁免词：命中测绘类关键词但文本同时含这些
# 目标领域词时，视为"用到了遥感/GIS 技术的目标岗"（如气象遥感、环境遥感），
# 不排除。避免误伤用户真正想要的目标专业岗位。
_SURVEYING_EXEMPT_KW = [
    "气象", "大气", "天气", "气候", "数值预报",
    "环境", "环保", "生态", "污染", "监测", "碳排放", "碳中和",
]


def _is_surveying_context(text: str) -> bool:
    """测绘/GIS/遥感命中后，检查是否属于豁免的目标领域上下文。"""
    return any(kw in text for kw in _SURVEYING_EXEMPT_KW)


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
            # 测绘/GIS/遥感命中 + 目标领域上下文（气象遥感/环境遥感）-> 豁免，保留
            if industry == "测绘GIS遥感" and _is_surveying_context(text):
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
