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

# 证书规则（用户 2026-08-10）：明确要求证书时，只接受环保工程师证；
# 注册类证书（注册安全/造价/建造/会计…师证等）一律排除。
# 注意「优先」属于软性偏好，不作为硬性资格排除；未提证书也绝不因信息缺失排除。
_ENVIRONMENTAL_ENGINEER_CERT_RE = re.compile(
    r"(?:注册)?\s*(?:环境保护|环保)\s*工程师\s*(?:职业)?(?:资格)?(?:证(?:书)?)?"
)
_CERTIFICATE_TOKEN_RE = re.compile(
    r"(?:"
    r"注册\s*[^\s，。；、]{0,12}?(?:职业资格|执业资格|从业资格|资格证(?:书)?|执业证(?:书)?|上岗证(?:书)?|从业证(?:书)?|操作证(?:书)?|证(?:书)?|资格)"
    r"|[一二三四五六七八九十\d]*级?\s*[^\s，。；、]{0,12}?(?:资格证书?|执业证书?|上岗证书?|从业证书?|操作证书?|职业资格(?:证书)?|执业资格|从业资格|证书)"
    r")"
)
# 学历/学位/结业类证书不属于职业资格，绝不作为证书硬性要求排除。
_ACADEMIC_CERT_RE = re.compile(r"(?:毕业|学位|学历|结业)\s*证(?:书)?")
_CERTIFICATE_REQUIRED_RE = re.compile(
    r"(?:须|需|必须|要求|应当|应|持有|具备|拥有|取得|获得|提供|需持|须持)"
)
_CLAUSE_SPLIT_RE = re.compile(r"[，。；;\n\r]")


def _has_disallowed_certificate_requirement(text: str) -> bool:
    """仅识别写明的硬性证书要求；环保工程师证是唯一允许项。"""
    for clause in _CLAUSE_SPLIT_RE.split(text or ""):
        if not _CERTIFICATE_REQUIRED_RE.search(clause):
            continue
        certificate_matches = list(_CERTIFICATE_TOKEN_RE.finditer(clause))
        if not certificate_matches:
            continue
        # 「持有一级建造师证书者优先」并非硬条件，沿用全局软偏好原则保留。
        if all(is_preference(clause, m.start(), m.end()) for m in certificate_matches):
            continue
        remaining = _ENVIRONMENTAL_ENGINEER_CERT_RE.sub("", clause)
        remaining = _ACADEMIC_CERT_RE.sub("", remaining)
        if _CERTIFICATE_TOKEN_RE.search(remaining):
            return True
    return False


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

        if not excluded and _has_disallowed_certificate_requirement(full_text):
            excluded = True
            exclude_reason = "certificate_non_environmental"

        if excluded:
            job.excluded = True
            job.exclude_reason = f"资格排除: {exclude_reason}"

        result.append(job)

    return result
