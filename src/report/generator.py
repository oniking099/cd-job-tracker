"""
HTML 报告生成器：使用 Jinja2 模板渲染最终的招聘信息报告。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.config import OUTPUT_DIR
from src.models import Job, CompanyType

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 类别配色方案
CATEGORY_STYLE = {
    CompanyType.STATE_OWNED: {
        "label": "国有企业", "emoji": "🔴",
        "bg_color": "#FEF2F2", "text_color": "#991B1B", "badge_color": "#EF4444",
    },
    CompanyType.CENTRAL: {
        "label": "中央企业", "emoji": "🟡",
        "bg_color": "#FFFBEB", "text_color": "#92400E", "badge_color": "#F59E0B",
    },
    CompanyType.FOREIGN: {
        "label": "外资企业", "emoji": "🔵",
        "bg_color": "#EFF6FF", "text_color": "#1E40AF", "badge_color": "#3B82F6",
    },
    CompanyType.JOINT_VENTURE: {
        "label": "合资企业", "emoji": "🟢",
        "bg_color": "#ECFDF5", "text_color": "#065F46", "badge_color": "#10B981",
    },
    CompanyType.OTHER: {
        "label": "其他企业", "emoji": "⚫",
        "bg_color": "#F9FAFB", "text_color": "#374151", "badge_color": "#6B7280",
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


def generate_report(
    jobs: list[Job],
    target_date: str | None = None,
) -> str:
    """
    生成 HTML 报告。
    返回生成的 HTML 文件路径。
    """
    # 去重过滤
    jobs = [j for j in jobs if not j.excluded]

    # 按类别分组
    grouped: dict[CompanyType, list[Job]] = {ct: [] for ct in CATEGORY_ORDER}
    for job in jobs:
        ct = job.company_type or CompanyType.OTHER
        if ct in grouped:
            grouped[ct].append(job)
        else:
            grouped[CompanyType.OTHER].append(job)

    # 组装模板数据
    groups = []
    summary = []
    for ct in CATEGORY_ORDER:
        style = CATEGORY_STYLE[ct]
        job_list = grouped[ct]
        groups.append({
            **style,
            "jobs": job_list,
        })
        summary.append({
            "label": style["label"],
            "emoji": style["emoji"],
            "count": len(job_list),
            "bg_color": style["bg_color"],
            "text_color": style["text_color"],
        })

    total = len(jobs)
    today = target_date or date.today().isoformat()

    # 渲染模板
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("report.html")
    html = template.render(
        date_str=today,
        groups=groups,
        summary=summary,
        total_count=total,
    )

    # 保存文件
    out_dir = OUTPUT_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "report.html"
    out_path.write_text(html, encoding="utf-8")

    return str(out_path)
