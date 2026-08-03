"""
资格排除过滤器：
- 年龄：排除 35 岁以下要求的岗位
- 学历：排除要求博士的岗位
- 政治：排除要求党员的岗位
- 招聘类型：排除校招/应届/实习/兼职/管培生
- 特殊放行：学校/研究所发布的社招岗位
"""
from __future__ import annotations

import re
from src.models import Job

# 排除规则（任一命中即排除）
EXCLUDE_RULES: dict[str, str] = {
    # 年龄限制
    "age_35": r"35岁[以之]下|35周岁[以之]下|年龄[^\d]*35|不超过\s*35",
    "age_limit": r"年龄\s*(?:要求|限制|不[得超]).*?(?:3[0-5]|[23][0-9])\s*岁",
    # 学历要求
    "degree_phd": r"博士[及和或]?\s*(?:学位|学历|研究生)?|博士学位|博士学历|Ph\.?\s*D",
    "degree_phd2": r"(?:要求|学历)\s*[：:]*\s*博士",
    # 政治面貌
    "party": r"(?:中共)?党员|预备党员|入党积极分子|政治面貌[：:]\s*党员",
    # 招聘类型
    "campus1": r"校[园]?\s*招[聘生]|校园招聘|面向\s*(?:应届|2\d{3}\s*届)",
    "campus2": r"应届[毕业生]?|应届生[专招]?",
    "part_time": r"兼\s*职|实习[生岗]?|暑期实习|寒假实习|日常实习",
    "trainee": r"管培生|培训生|实习生|见习生",
}

# 学校/研究所发布方关键词
SCHOOL_RESEARCH_PUBLISHER: list[str] = [
    "大学", "学院", "研究所", "研究院", "科学院",
    "设计院", "实验室", "中心（科研", "国家重点",
    "气象局", "环保局", "环境监测站", "水文站",
    "中国科学院", "中国工程院", "农科院", "林科院",
    "环科院", "规划院", "勘察院",
]

# 社招信号词（学校/研究所发布方包含这些才放行）
SHEHUI_SIGNALS: list[str] = [
    "社招", "社会招聘", "社会人员", "有工作经验",
    "硕士及以上", "博士及以上", "研究生及以上",
    "高级工程师", "资深", "负责人", "主管", "经理",
    "总工", "主任", "专家",
    "具有.*年以上", "工作经验.*年以上",
]


def _is_school_or_research(company: str) -> bool:
    """判断发布方是否为学校/研究所"""
    for kw in SCHOOL_RESEARCH_PUBLISHER:
        if kw in company:
            return True
    return False


def _has_shehui_signal(text: str) -> bool:
    """判断文本是否包含社招信号"""
    for kw in SHEHUI_SIGNALS:
        if re.search(kw, text):
            return True
    return False


def filter_qualification(jobs: list[Job]) -> list[Job]:
    """
    资格排除主函数。
    检查每条招聘信息是否命中排除规则。
    学校/研究所发布的岗位如果有社招信号，放行。
    """
    result: list[Job] = []

    for job in jobs:
        full_text = f"{job.title} {job.requirements} {job.responsibilities} {job.company}"
        excluded = False
        exclude_reason = ""

        # 逐规则检查
        for rule_name, pattern in EXCLUDE_RULES.items():
            if re.search(pattern, full_text):
                # 特殊处理：如果是学校/研究所发布，且有社招信号，放行
                if rule_name in ("campus1", "campus2", "trainee"):
                    if _is_school_or_research(job.company) and _has_shehui_signal(full_text):
                        continue  # 不放排除，继续检查其他规则
                excluded = True
                exclude_reason = rule_name
                break

        if excluded:
            job.excluded = True
            job.exclude_reason = f"资格排除: {exclude_reason}"

        result.append(job)

    return result
