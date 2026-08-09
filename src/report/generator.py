"""
HTML 报告生成器：使用 Jinja2 模板渲染最终的招聘信息报告。

布局规范（用户要求）：
- 一司一卡：不管一个公司有几个岗位，都合并在同一张卡片里，一个岗位一行
- 同一类型的公司卡片放在同一个大框里，从上到下：国企 / 央企 / 外资 / 合资 / 其他
- 大框可展开收起，默认全部展开
- 卡片含：公司名称、HR活跃、公司地点、薪资范围（含多少薪）、岗位职责、岗位要求、离家距离、信息来源、查看详情
- 查看详情 → 该岗位的真实招聘详情页 URL（不是列表/父子页）
"""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.config import OUTPUT_DIR, bjt_today
from src.filters.jd_category import classify_jd_category
from src.filters.report_exclusion import report_exclusion_reason
from src.models import Job, CompanyType

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 类别配色方案
CATEGORY_STYLE = {
    CompanyType.STATE_OWNED: {
        "label": "国有企业", "emoji": "🔴",
        "bg_color": "#FEF2F2", "text_color": "#991B1B", "badge_color": "#DC2626",
        "grad": "linear-gradient(135deg, #FEF2F2 0%, #FDE4E4 100%)",
    },
    CompanyType.CENTRAL: {
        "label": "中央企业", "emoji": "🟡",
        "bg_color": "#FFFBEB", "text_color": "#92400E", "badge_color": "#D97706",
        "grad": "linear-gradient(135deg, #FFFBEB 0%, #FDEBC8 100%)",
    },
    CompanyType.FOREIGN: {
        "label": "外资企业", "emoji": "🔵",
        "bg_color": "#EFF6FF", "text_color": "#1E40AF", "badge_color": "#2563EB",
        "grad": "linear-gradient(135deg, #EFF6FF 0%, #DBEAFE 100%)",
    },
    CompanyType.JOINT_VENTURE: {
        "label": "合资企业", "emoji": "🟢",
        "bg_color": "#ECFDF5", "text_color": "#065F46", "badge_color": "#059669",
        "grad": "linear-gradient(135deg, #ECFDF5 0%, #D1FAE5 100%)",
    },
    CompanyType.OTHER: {
        "label": "其他企业", "emoji": "⚫",
        "bg_color": "#F9FAFB", "text_color": "#374151", "badge_color": "#4B5563",
        "grad": "linear-gradient(135deg, #F9FAFB 0%, #F3F4F6 100%)",
    },
}

# 显示顺序
CATEGORY_ORDER = [
    CompanyType.STATE_OWNED,
    CompanyType.CENTRAL,
    CompanyType.FOREIGN,
    CompanyType.JOINT_VENTURE,
    CompanyType.OTHER,
]

# 占位符/幻觉 URL（LLM 编造）：如 xxxxx.html / ddddd.html / 123456789.html 假 ID
_FAKE_URL_RE = re.compile(
    r"(x{4,}|y{4,}|z{4,}|a{4,}|b{4,}|c{4,}|d{4,}|tbd|placeholder|example\.com)"
)


def safe_url(url: str | None) -> str:
    """只保留看起来真实的 http(s) 链接，丢弃占位符/幻觉 URL。

    报告层兜底净化：不管数据来自新 agent（DOM 真实 href）还是旧历史数据，
    假链接一律不渲染成可点击的「查看详情」，避免点进 404/错误页面。
    """
    u = (url or "").strip()
    if not u or not u.startswith(("http://", "https://")) or _FAKE_URL_RE.search(u):
        return ""
    # 职友集搜索页 URL（/jobs?）不是职位详情页，点开是搜索列表/重定向，不渲染为可点击链接
    if "jobui.com/jobs" in u:
        return ""
    return u

# 薪资中的"N薪"提取："6千-1万·14薪" → "14薪"
_SALARY_MONTHS_RE = re.compile(r"(\d{1,2})\s*薪")
# 去掉薪资文本末尾的"N薪"段："6千-1万·14薪" → "6千-1万"（由徽标单独展示）
_SALARY_SUFFIX_RE = re.compile(r"[-·\s]?\d{1,2}薪\s*$")


def salary_months(salary_text: str) -> str:
    """从薪资文本提取"多少薪"（13薪/14薪/15薪…），无则返回空。"""
    m = _SALARY_MONTHS_RE.search(salary_text or "")
    return f"{m.group(1)}薪" if m else ""


def strip_salary_suffix(salary_text: str) -> str:
    """去掉薪资文本末尾的"N薪"段，避免与"N薪"徽标重复展示。"""
    return _SALARY_SUFFIX_RE.sub("", salary_text or "").strip("· ")


def format_distance(distance_km: float | None) -> str:
    """格式化离家距离：<1km 显示"约 Xm"，否则 "X.Xkm"。"""
    if distance_km is None:
        return ""
    if distance_km < 1:
        return f"约{int(distance_km * 1000)}m"
    return f"{distance_km:.1f}km"


def _group_by_company(jobs: list[Job]) -> dict[CompanyType, list[dict]]:
    """全局按公司聚合：一司一卡。公司类型取该司首个非空判定。"""
    by_company: dict[str, dict] = {}
    for job in jobs:
        name = (job.company or "").strip() or "未署名公司"
        if name not in by_company:
            by_company[name] = {
                "name": name,
                "jobs": [],
                "company_type": None,
                "hr_active": False,
                "max_salary": 0.0,
            }
        c = by_company[name]
        c["jobs"].append(job)
        if c["company_type"] is None and job.company_type:
            c["company_type"] = job.company_type
        if job.hr_active:
            c["hr_active"] = True
        c["max_salary"] = max(c["max_salary"], job.salary_min or 0)

    # 公司内岗位按薪资降序
    for c in by_company.values():
        c["jobs"].sort(key=lambda j: j.salary_min, reverse=True)

    # 分配到类型框
    grouped: dict[CompanyType, list[dict]] = {ct: [] for ct in CATEGORY_ORDER}
    for c in by_company.values():
        ct = c["company_type"] or CompanyType.OTHER
        grouped.setdefault(ct, []).append(c)

    # 框内公司按最高薪资降序
    for ct in grouped:
        grouped[ct].sort(key=lambda c: c["max_salary"], reverse=True)
    return grouped


def _render_report_html(jobs: list[Job], date_str: str) -> str:
    """渲染单份报告 HTML（按公司聚合 + 按类型分组 + 套模板）。

    不写文件、不计算日期，由 generate_report 拆分行业/专业后分别调用。
    模板/排序/样式完全不变，仅 jobs 子集不同 -> 仅数量差异。
    """
    # 按公司聚合 + 按类型分组
    grouped = _group_by_company(jobs)

    # 组装模板数据
    groups = []
    summary = []
    for ct in CATEGORY_ORDER:
        style = CATEGORY_STYLE[ct]
        companies = grouped.get(ct, [])
        total_jobs = sum(len(c["jobs"]) for c in companies)
        groups.append({
            **style,
            "companies": companies,
            "job_count": total_jobs,
        })
        summary.append({
            "label": style["label"],
            "emoji": style["emoji"],
            "company_count": len(companies),
            "job_count": total_jobs,
            "bg_color": style["bg_color"],
            "text_color": style["text_color"],
        })

    # 渲染模板
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    env.filters["salary_months"] = salary_months
    env.filters["strip_salary_suffix"] = strip_salary_suffix
    env.filters["fmt_dist"] = format_distance
    env.filters["safe_url"] = safe_url
    template = env.get_template("report.html")
    return template.render(
        date_str=date_str,
        groups=groups,
        summary=summary,
        total_count=len(jobs),
    )


def generate_report(
    jobs: list[Job],
    target_date: str | None = None,
) -> dict[str, str]:
    """生成 HTML 报告。

    用户要求（2026-08-08）：单个 report.html 拆成两份--
    - report-industry.html：行业类（大气科学/气象/大气环境/环保/生态 等）
    - report-professional.html：专业类（AI/agent/大模型 等）
    两类都占 -> 行业类；都不占 -> 行业类兜底。详见 jd_category.classify_jd_category。
    拆分后模板/排序/样式不变，仅 jobs 子集与数量不同。

    报告层排除（2026-08-09 用户要求，对两份报告同时生效）：
    - 医学/法律/游戏/证券/电商/餐饮 领域岗位（制造/服务两侧都剔除）
    - 月薪下限 ≥ 2.9万 的岗位（年薪折月判断）
    详见 report_exclusion.report_exclusion_reason。

    返回 {"industry": 路径, "professional": 路径}。
    """
    # 去重过滤
    jobs = [j for j in jobs if not j.excluded]

    # 报告层排除：领域 + 月薪下限≥3万（拆分前过滤，两份报告都不含）
    jobs = [j for j in jobs if not report_exclusion_reason(j)]

    # 职友集详情页改用移动版链接：www 版点击会被 valid.php 验证码拦截，
    # m 版会正常 302 到真实来源站（智联/电梯招聘网等），不触发风控（实测 2026-08-07）。
    for j in jobs:
        if j.platform == "职友集" and j.url.startswith("https://www.jobui.com/job/"):
            j.url = "https://m.jobui.com/job/" + j.url[len("https://www.jobui.com/job/"):]

    today = target_date or bjt_today()
    out_dir = OUTPUT_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    # 按行业/专业拆分（行业优先，兜底行业）
    industry_jobs = [j for j in jobs if classify_jd_category(j) == "industry"]
    professional_jobs = [j for j in jobs if classify_jd_category(j) == "professional"]

    paths: dict[str, str] = {}
    for cat, subset in (("industry", industry_jobs), ("professional", professional_jobs)):
        html = _render_report_html(subset, today)
        out_path = out_dir / f"report-{cat}.html"
        out_path.write_text(html, encoding="utf-8")
        paths[cat] = str(out_path)

    return paths
