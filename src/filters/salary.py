"""
薪资过滤器：正则提取月薪并按企业类型判断最低薪资阈值。

薪资格式覆盖：
- "10K-15K" / "10k-15k"
- "10-15K" / "10-15k"
- "8千-1.2万" / "4.5-9千" / "6千-1.2万"（千字格式，2026-09-04 补）
- "10000-15000元/月" / "10000-15000/月"
- "1万-1.5万" / "1.0万-2万"
- "12-15万/年" / "年薪12-15万"（年薪按 ÷12 换算月薪，2026-09-04 补）
- "10000-15000" (fallback)
- 面议 / 薪资open → 跳过（不排除）

用户拍板（2026-09-04，起因：低薪岗大量漏网）：
- 有薪资文本但解析不出数字（日结/时薪/按件等零工格式）→ 不再放行，按不达标排除；
  仅"面议"类标记文本放行（ UNKNOWN_SALARY_KEYWORDS）。
- 无薪资文本（信息缺失）→ 仍放行，沿用"缺信息不排除"原则。
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

# 千字格式（2026-09-04）：高低位单位不同，无法用统一乘数，单独处理
# "8千-1.2万" → (8000, 12000)；"6千-1.2万" 同理
_QIAN_WAN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*千\s*[-~至]\s*(\d+(?:\.\d+)?)\s*万"
)
# "4.5-9千" / "6.5-8千" / "3千-6千" → 双位千
_QIAN_QIAN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:千\s*)?[-~至]\s*(\d+(?:\.\d+)?)\s*千"
)
# "8千-12K" 混合
_QIAN_K_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*千\s*[-~至]\s*(\d+(?:\.\d+)?)\s*[kK]"
)

# 年薪标记：命中则数值按 ÷12 换算月薪（"12-15万/年"实测曾被当 12-15万/月 直接达标）
_YEARLY_RE = re.compile(r"/\s*年|元\s*/\s*年|年薪")

# 特殊标记
UNKNOWN_SALARY_KEYWORDS = ["面议", "薪资open", "待遇从优", "薪资优厚", "薪资丰厚"]

# 月薪合理性区间（低于 1000 的匹配视为误提取，继续尝试下一模式）
_SALARY_MIN = 1000.0
_SALARY_MAX_LOW = 200000.0
_SALARY_MAX_HIGH = 500000.0


def _is_reasonable(low: float, high: float) -> bool:
    return (
        low >= _SALARY_MIN
        and high >= _SALARY_MIN
        and low <= _SALARY_MAX_LOW
        and high <= _SALARY_MAX_HIGH
    )


def extract_salary(salary_text: str) -> tuple[float, float]:
    """
    从薪资文本中提取最低月薪和最高月薪（年薪文本自动 ÷12）。
    返回 (min_salary, max_salary)，无法提取返回 (0.0, 0.0)。
    """
    if not salary_text:
        return 0.0, 0.0

    # 检查面议等标记
    for kw in UNKNOWN_SALARY_KEYWORDS:
        if kw in salary_text:
            return 0.0, 0.0

    text = salary_text.strip()
    is_yearly = bool(_YEARLY_RE.search(text))

    # 千字格式优先（更具体），避免落入通用万/K 模式的误提取
    m = _QIAN_WAN_RE.search(text)
    if m:
        low, high = float(m.group(1)) * 1000, float(m.group(2)) * 10000
        if _is_reasonable(*_yearly_div(low, high, is_yearly)):
            return _yearly_div(low, high, is_yearly)

    m = _QIAN_QIAN_RE.search(text)
    if m:
        low, high = float(m.group(1)) * 1000, float(m.group(2)) * 1000
        if _is_reasonable(*_yearly_div(low, high, is_yearly)):
            return _yearly_div(low, high, is_yearly)

    m = _QIAN_K_RE.search(text)
    if m:
        low, high = float(m.group(1)) * 1000, float(m.group(2)) * 1000
        if _is_reasonable(*_yearly_div(low, high, is_yearly)):
            return _yearly_div(low, high, is_yearly)

    for pattern, multiplier in SALARY_PATTERNS:
        m = re.search(pattern, text)
        if not m:
            continue
        try:
            low = float(m.group(1)) * multiplier
            high = float(m.group(2)) * multiplier
            low, high = _yearly_div(low, high, is_yearly)
            if _is_reasonable(low, high):
                return low, high
        except (ValueError, IndexError):
            continue

    return 0.0, 0.0


def _yearly_div(low: float, high: float, is_yearly: bool) -> tuple[float, float]:
    """年薪文本按 ÷12 换算为月薪。"""
    if is_yearly:
        return low / 12, high / 12
    return low, high


def meets_salary_threshold(job: Job) -> bool:
    """
    检查岗位是否满足薪资门槛：
    - 国企/央企/外资/合资: >= 10000 元/月
    - 其他: >= 16000 元/月
    - 企业类型未知: >= 16000 元/月（严格侧）
    - 薪资面议: 放行（不因薪资排除）
    - 无薪资文本（信息缺失）: 放行
    - 有薪资文本但解析失败（日结/时薪等零工格式）: 排除（用户 2026-09-04）
    """
    salary_text = (job.salary_text or "").strip()

    # 无薪资文本 → 信息缺失，放行
    if not salary_text:
        return True

    # 有文本但提取不出数字：仅面议类标记放行，其余视为不达标
    if job.salary_min <= 0 and job.salary_max <= 0:
        return any(kw in salary_text for kw in UNKNOWN_SALARY_KEYWORDS)

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
