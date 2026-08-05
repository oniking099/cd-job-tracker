"""
专业匹配过滤器：判断岗位是否属于目标专业领域。
覆盖气象/大气科学/环境/生态/遥感/GIS/碳中和等相关专业。
"""
from __future__ import annotations

import re
from src.models import Job

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


def match_major(jobs: list[Job]) -> list[Job]:
    """
    专业匹配主函数。
    检查岗位描述和要求中是否包含目标专业关键词。
    不匹配的标记为排除。

    区分两种排除原因：
    - "JD文本未抓取"：标题+职责+要求全空（详情页未抓到/被登录墙挡）。这不是专业不匹配，
      而是抓取覆盖缺失，原因单独标记后可量化富集覆盖率（配合 pipeline stats）。
    - "专业不匹配"：有文本但确实不含目标专业关键词。
    """
    result: list[Job] = []
    for job in jobs:
        full_text = f"{job.title} {job.requirements} {job.responsibilities}".strip()

        if not full_text:
            job.excluded = True
            job.exclude_reason = "JD文本未抓取"
            result.append(job)
            continue

        passed, matched = check_major_match(full_text)

        if passed:
            result.append(job)
        else:
            job.excluded = True
            job.exclude_reason = "专业不匹配"
            result.append(job)

    return result
