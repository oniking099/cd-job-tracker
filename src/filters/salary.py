"""
薪资过滤器：正则提取月薪并按企业类型判断最低薪资阈值。

薪资格式覆盖：
- "10K-15K" / "10k-15k"
- "10-15K" / "10-15k"
- "10000-15000元/月" / "10000-15000/月"
- "1万-1.5万" / "1.0万-2万"
- "10000-15000" (fallback)
- 面议 / 薪资open → 跳过（不排除，留待人工/LLM判断）
"""
from __future__ import annotations

import re
from src.models import Job, CompanyType

# 正则模式按优先级排列
SALARY_PATTERNS: list[tuple[str, int]] = [
    # "10K-15K" / "10k-15k" → 10000~15000
    (r"(\d+(?:\.\d+)?)\s*[kK]\s*[-~至]\s*(\d+(?:\.\d+)?)\s*[kK]", 1000),
    # "10-15K" / "10-15k"
    (r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)\s*[kK]", 1000),
    # "10000-15000元/月"
    (r"(\d+)\s*[-~至]\s*(\d+)\s*元\s*/\s*月", 1),
    # "10000-15000/月"
    (r"(\d+)\s*[-~至]\s*(\d+)\s*/\s*月", 1),
    # "1万-1.5万" / "1.0万-2万"
    (r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)\s*万", 10000),
    # "10000-15000" (纯数字 fallback，最后尝试)
    (r"(?:^|\D)([1-9]\d{3,})\s*[-~至]\s*([1-9]\d{3,})(?:\D|$)", 1),
]

# 特殊标记
UNKNOWN_SALARY_KEYWORDS = ["面议", "薪资open", "待遇从优", "薪资优厚", "薪资丰厚"]


def extract_salary(salary_text: str) -> tuple[float, float]:
    """
    从薪资文本中提取最低月薪和最高月薪。
    返回 (min_salary, max_salary)，无法提取返回 (0.0, 0.0)。
    """
    if not salary_text:
        return 0.0, 0.0

    # 检查面议等标记
    for kw in UNKNOWN_SALARY_KEYWORDS:
        if kw in salary_text:
            return 0.0, 0.0

    text = salary_text.strip()

    for pattern, multiplier in SALARY_PATTERNS:
        m = re.search(pattern, text)
        if m:
            try:
                low = float(m.group(1)) * multiplier
                high = float(m.group(2)) * multiplier
                # 合理性检查
                if low < 1000 or high < 1000:
                    continue
                if low > 200000 or high > 500000:
                    continue
                return low, high
            except (ValueError, IndexError):
                continue

    return 0.0, 0.0


def meets_salary_threshold(job: Job) -> bool:
    """
    检查岗位是否满足薪资门槛：
    - 国企/央企/外资/合资: >= 10000 元/月
    - 其他: >= 16000 元/月
    - 企业类型未知: >= 16000 元/月（严格侧）
    - 薪资面议: 放行（不因薪资排除）
    """
    # 薪资面议 → 放行
    if job.salary_min <= 0 and job.salary_max <= 0:
        return True

    ct = job.company_type

    # 四类企业：1万门槛
    if ct in (CompanyType.STATE_OWNED, CompanyType.CENTRAL,
              CompanyType.FOREIGN, CompanyType.JOINT_VENTURE):
        threshold = 10000.0
    else:
        # 其他 或 未知：1.6万门槛
        threshold = 16000.0

    return job.salary_min >= threshold


def filter_salary(jobs: list[Job]) -> list[Job]:
    """
    薪资过滤主函数。
    1. 提取每条的薪资
    2. 按企业类型判定阈值
    3. 排除不达标的，标记原因
    """
    result: list[Job] = []
    for job in jobs:
        # 从原始薪资文本中提取
        if job.salary_text and job.salary_min <= 0:
            low, high = extract_salary(job.salary_text)
            job.salary_min = low
            job.salary_max = high

        if meets_salary_threshold(job):
            result.append(job)
        else:
            job.excluded = True
            job.exclude_reason = f"薪资不达标: {job.salary_display} < 门槛"
            result.append(job)  # 保留但标记，供后续分析

    return result
