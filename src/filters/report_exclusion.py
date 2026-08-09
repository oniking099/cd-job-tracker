"""
报告层排除规则（用户要求 2026-08-09）：进两份 HTML 报告前整岗剔除。

1. **领域排除**：医学 / 法律 / 游戏 / 证券 / 电商 / 餐饮 —— 这些领域内不管
   制造方面还是服务方面都不要（如「医药公司的环保工程师」「餐饮店的新媒体运营」都剔除）。
   匹配文本 = 标题 + 公司名（公司名是领域信号：制造/服务侧都靠公司名兜住）。
   不匹配正文——「有餐饮行业经验优先」这类正文提及噪声太大。
2. **高薪排除**：月薪下限 ≥ 2.9万（29K）的岗位一律删除，不管任何岗位任何领域
   （阈值 2026-08-09 由 3万 下调为 2.9万）。
   年薪格式（「18-22万/年」）按 /12 折月后再比；无法解析/面议/乱码 → 保留
   （只有明确解析出下限才剔除）。
"""
from __future__ import annotations

import re

from src.models import Job

# 排除领域词表：命中 标题或公司名 即剔除
# 注意：不收单字「医」「药」「餐」——避免「中西医结合」类长词切分歧义，复合词已足够覆盖
EXCLUDED_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "医学": [
        "医学", "医疗", "医药", "医生", "医师", "医院", "诊所", "护理", "护士",
        "临床", "药店", "药师", "制药", "口腔", "牙科", "眼科", "中医", "医美", "康复", "康养",
    ],
    "法律": ["法律", "律师", "法务", "律所"],
    "游戏": ["游戏", "电竞"],
    "证券": ["证券", "券商", "股票", "基金", "期货", "投行"],
    "电商": ["电商", "电子商务", "淘宝", "天猫", "拼多多", "网店", "跨境电商"],
    "餐饮": ["餐饮", "餐厅", "饭店", "厨师", "烘焙", "火锅", "茶饮", "奶茶"],
}

# 月薪下限阈值：2.9 万 = 29K（含，用户 2026-08-09 由 3万 下调：「下限2.9万或以上都删除」）
HIGH_SALARY_FLOOR_K: float = 29.0


def excluded_domain(job: Job) -> str:
    """命中排除领域则返回领域名（如「电商」），否则返回空串。"""
    text = f"{job.title or ''} {job.company or ''}"
    for domain, keywords in EXCLUDED_DOMAIN_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return domain
    return ""


def salary_floor_monthly_k(salary_text: str) -> float | None:
    """解析月薪下限（单位 K）。无法解析 / 面议 / 乱码 → None。

    实数据格式（data/2026-08-07 调研）：
    - 「15-25K」「5k-10k元/月」      -> K 月薪
    - 「3-6万」「2.5-3万·15薪」      -> 万 月薪（×10）
    - 「5-7千」「8千-1.5万」          -> 千 月薪（首数字无单位时向后继承单位）
    - 「18-22万/年」                  -> 年薪（/12 折月）；实数据里年薪都显式带「年」
    - 「7000-10000」                  -> 元 月薪（≥1000 的裸数字按元）
    - 「面议」「*大-**元」            -> None（不剔除，规则只针对「明确说明」的下限）
    """
    s = (salary_text or "").lower().replace(" ", "")
    if not s or "面议" in s:
        return None
    m = re.match(r"(\d+(?:\.\d+)?)(万|千|k|元)?", s)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2)
    if unit is None:
        if val >= 1000:
            unit = "元"  # 「7000-10000」裸大数字按元
        else:
            # 「5-7千」「3-6万」首数字无单位 -> 向后继承区间后半的单位
            m2 = re.search(r"(万|千|k)", s[m.end():])
            if not m2:
                return None
            unit = m2.group(1)
    k = val * {"万": 10.0, "千": 1.0, "k": 1.0, "元": 0.001}[unit]
    if "年" in s:  # 年薪折月（「18-22万/年」-> 15K）
        k /= 12
    return k


def is_high_salary(job: Job) -> bool:
    """月薪下限 ≥ 29K（2.9万）-> True（一律剔除）。"""
    floor = salary_floor_monthly_k(job.salary_text)
    return floor is not None and floor >= HIGH_SALARY_FLOOR_K


def report_exclusion_reason(job: Job) -> str:
    """返回剔除原因；空串 = 保留。领域优先于高薪（便于日志阅读）。"""
    domain = excluded_domain(job)
    if domain:
        return f"排除领域:{domain}"
    if is_high_salary(job):
        return f"月薪下限≥2.9万:{job.salary_text}"
    return ""
