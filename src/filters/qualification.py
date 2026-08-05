"""
资格排除过滤器：
- 年龄：排除要求 35 岁及以下/更年轻的岗位（用户 35 岁以上，不满足就过滤）
- 学历：排除明确要求博士的岗位（用户非博士）
- 政治：排除要求党员的岗位（用户非党员）
- 招聘类型：排除校招/应届/实习/兼职/管培生（用户非应届/在校生）

用户拍板原则（2026-08-05）：
- 不管任何招聘来源，只要用户不满足条件，都过滤——不设学校/研究所来源例外。
- 没写的信息保留：没写年龄/学历/政治/招聘类型 → 不因缺信息排除。
"""
from __future__ import annotations

import re
from src.models import Job
from src.filters.common import is_preference

# 排除规则（任一命中即排除；只按"写了明确要求"排除，没写就不排）
EXCLUDE_RULES: dict[str, str] = {
    # 年龄限制（用户 35 岁以上；凡要求 <=35 岁的都不满足）
    # "35-45岁"这类区间不命中（用户可能落在区间内，不排除）
    "age_35": r"35\s*(?:周?岁)?(?:及?以下|及?以内)|年龄[^\d]*不(?:超过|高于|得超)\s*35|不超过\s*35",
    "age_younger": (
        r"(?:[12]\d|3[0-4])\s*岁(?:及?以下|及?以内)"
        r"|(?:[12]\d|3[0-4])\s*周岁(?:及?以下|及?以内)"
        r"|不超过\s*(?:[12]\d|3[0-4])\s*岁"
    ),
    # 学历要求（用户非博士；只排明确要求博士的，不排"硕士或博士"等含硕士的情形）
    "degree_phd": r"博士(?:学位|学历|研究生|毕业)|(?:要求|需)\s*[：:]*\s*博士|学历[：:]*\s*博士|Ph\.?\s*D",
    # 政治面貌（用户非党员）
    "party": r"(?:中共)?党员|预备党员|入党积极分子|政治面貌[：:]\s*党员",
    # 招聘类型（用户非应届/在校生；对学校/研究所等任何来源一视同仁）
    "campus1": r"校[园]?\s*招[聘生]|校园招聘|面向\s*(?:应届|2\d{3}\s*届)",
    "campus2": r"应届[毕业生]?|应届生[专招]?",
    "part_time": r"兼\s*职|实习[生岗]?|暑期实习|寒假实习|日常实习",
    "trainee": r"管培生|培训生|实习生|见习生",
}


def filter_qualification(jobs: list[Job]) -> list[Job]:
    """
    资格排除主函数。

    对所有招聘来源一视同仁：命中任一排除规则即排除（用户明确"只要我不满足，都过滤"）。
    没写相关信息（年龄/学历/政治/招聘类型）的岗位保留。
    软性偏好（如"35岁以下优先"）非刚性要求 → 保留。
    """
    result: list[Job] = []

    for job in jobs:
        full_text = f"{job.title} {job.requirements} {job.responsibilities} {job.company}"
        excluded = False
        exclude_reason = ""

        for rule_name, pattern in EXCLUDE_RULES.items():
            m = re.search(pattern, full_text)
            if not m:
                continue
            # 软性偏好（"X优先"）→ 非刚性，保留
            if is_preference(full_text, m.start(), m.end()):
                continue
            excluded = True
            exclude_reason = rule_name
            break

        if excluded:
            job.excluded = True
            job.exclude_reason = f"资格排除: {exclude_reason}"

        result.append(job)

    return result
