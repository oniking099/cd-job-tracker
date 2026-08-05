"""
专业匹配过滤器：判断岗位是否属于目标专业领域。
覆盖气象/大气科学/环境/生态/遥感/GIS/碳中和等相关专业。

用户拍板原则（2026-08-05）："所有没写的信息，都保留；你这里绝对不能说，专业没写气象等，就给我排除。"
故匹配规则为"只排明确冲突，不排信息缺失"：
- "专业不限" → 放行
- 文本命中目标专业关键词 → 放行
- JD 文本为空（详情页未抓到）→ 保留（缺信息不排除）
- 文本明确写了其他专业要求（如"专业要求：计算机"）→ 排除（"专业不匹配"）
- 其余（没写专业要求）→ 保留
"""
from __future__ import annotations

import re
from src.models import Job
from src.filters.common import has_preference, is_preference

# 目标专业关键词（命中任一即放行）
REQUIRED_MAJORS: list[str] = [
    # 气象/大气类
    "气象", "大气科学", "大气物理", "大气探测", "气候",
    "数值预报", "天气预报", "大气环境", "大气",
    # 环境类
    "环境科学", "环境工程", "环境监测", "环境管理", "环境规划",
    "环境影响评价", "环境咨询", "环境评估", "环境治理",
    "生态环境", "环保", "生态学", "环境经济", "环境化学",
    "环境地质", "环境生物", "环境",
    # 水文/水资源
    "水文", "水资源", "水环境", "水处理", "水质",
    "给排水",
    # 遥感/GIS
    "遥感", "地理信息", "GIS", "空间数据", "卫星数据",
    "对地观测", "测绘",
    # 碳中和/可持续发展
    "碳中和", "碳达峰", "碳交易", "碳管理", "ESG",
    "可持续发展", "绿色低碳", "低碳",
    # 交叉方向
    "智慧气象", "气象AI", "环境AI", "智慧环保",
    "数字孪生环境", "环境大数据",
]

# 专业不限的表述（命中任一即放行）
PASS_THROUGH_PATTERNS: list[str] = [
    r"专业不限",
    r"专业要求[：:]*\s*不限",
    r"无专业限制",
    r"专业无要求",
    r"不限专业",
    r"专业[：:]*\s*(不限|无|无要求)",
]

# 明确限定专业的表述模式（只有命中这些"写了其他专业"才可能排除；否则视为没写专业要求）
EXPLICIT_MAJOR_PATTERNS: list[str] = [
    r"专业要求[：:\s]+([^。；;，,、\n]{1,40})",       # 专业要求：计算机 / 专业要求 计算机
    r"(?:仅限|限|限定)([^。；;，,、\n]{1,20}?)专业",  # 限计算机专业
    r"(?:需|需要|要求)([^。；;，,、\n]{1,20}?)专业(?:背景|毕业|学历)",  # 需计算机专业背景
]

# 泛化大类表述：命中即视为"未明确限定具体专业"（不构成冲突）。
# 注意不含"相关/相近/等"——"计算机相关专业"是明确写了具体其他专业，必须排除；
# 仅"相关专业"这种没写具体专业名的，由 _is_vague_major 兜底保留。
GENERIC_MAJOR_RE = re.compile(
    r"不限|无限制|理工|工科|理、工|理科|综合|各类"
)


def _is_vague_major(seg: str) -> bool:
    """判断"专业要求"片段是否属于未限定具体专业的泛化表述。

    - "理工科相关专业" → 含"理工"大类 → 泛化（保留）
    - "相关专业" → 去掉"相关/专业"后无具体专业名 → 泛化（保留）
    - "计算机相关专业" → 去掉"相关/专业"后仍是"计算机" → 具体其他专业（冲突，排除）
    """
    if GENERIC_MAJOR_RE.search(seg):
        return True
    stripped = re.sub(r"相关|相近|等|和|与|及|、|／|/|\s|专业", "", seg)
    return not stripped


def check_major_match(text: str) -> tuple[bool, str]:
    """
    检查文本是否匹配目标专业。
    返回 (是否通过, 匹配到的关键词)。
    """
    if not text:
        return False, ""

    # 先检查"专业不限"类
    for pat in PASS_THROUGH_PATTERNS:
        if re.search(pat, text):
            return True, "专业不限"

    # 再检查目标专业
    for major in REQUIRED_MAJORS:
        if major in text:
            return True, major

    return False, ""


def _has_conflicting_major(text: str) -> bool:
    """
    检测文本是否明确写了与目标专业冲突的专业要求。

    用户原则（2026-08-05）：
    - 软性偏好（"计算机相关专业优先"）→ 非刚性，不冲突
    - 明确写了具体其他专业（"专业要求：计算机"/"只有计算机相关专业"）→ 冲突
    - 泛化大类（理工科等）或"相关专业"没写具体专业名 → 不冲突
    - 含目标专业关键词（"专业要求：大气科学"）→ 不冲突
    - 文本没提专业 → 无任何命中 → 不冲突
    """
    for pat in EXPLICIT_MAJOR_PATTERNS:
        for m in re.finditer(pat, text):
            seg = m.group(1).strip()
            if not seg:
                continue
            # 软性偏好（"X优先"）→ 非刚性，保留。
            # "优先"可能被卷进捕获片段内（"专业要求：计算机相关专业优先"），
            # 也可能紧跟命中处之后，两者都要查。
            if has_preference(seg) or is_preference(text, m.start(), m.end()):
                continue
            # 含目标专业关键词 → 不冲突
            if any(kw in seg for kw in REQUIRED_MAJORS):
                continue
            # 泛化表述（理工大类 / 无具体专业名）→ 未明确限定，不冲突
            if _is_vague_major(seg):
                continue
            # 明确写了其他具体专业（含"只有计算机相关专业"）→ 冲突
            return True
    return False


def match_major(jobs: list[Job]) -> list[Job]:
    """
    专业匹配主函数。

    用户原则：所有没写的信息保留。
    - JD 文本为空 → 保留（缺详情文本不等于专业不匹配，不排除）
    - 命中目标专业关键词 / "专业不限" → 放行
    - 未命中但明确写了其他专业要求 → 排除（"专业不匹配"）
    - 未命中也没写专业要求 → 保留
    """
    result: list[Job] = []
    for job in jobs:
        full_text = f"{job.title} {job.requirements} {job.responsibilities}".strip()

        # JD 文本为空（详情页未抓到）→ 保留，缺信息不排除
        if not full_text:
            result.append(job)
            continue

        passed, _ = check_major_match(full_text)
        if passed:
            result.append(job)
            continue

        # 未命中目标关键词：只有明确写了其他专业要求才排除
        if _has_conflicting_major(full_text):
            job.excluded = True
            job.exclude_reason = "专业不匹配"

        result.append(job)

    return result
