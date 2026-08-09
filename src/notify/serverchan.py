"""
Server酱推送模块：将招聘摘要推送到微信。
官方文档：https://sct.ftqq.com/
"""
from __future__ import annotations

import httpx

from src.config import SERVER_CHAN_SENDKEY
from src.models import Job, CompanyType
from src.report.generator import safe_url

# 卡片版网页报告（GitHub Pages 托管，raw/jsdelivr 返回 text/plain 不可渲染）
# 该 URL 必须能打开成美观的 HTML 卡片页，否则推送里的链接会 404。
GITHUB_PAGES_BASE = "https://oniking099.github.io/cd-job-tracker"


async def _send(title: str, desp: str) -> bool:
    """发送 ServerChan 推送（desp 为 Markdown，支持内联 HTML 加粗/变色）。返回是否成功。"""
    if not SERVER_CHAN_SENDKEY:
        print("[Server酱] SENDKEY 未配置，跳过推送")
        return False
    url = f"https://sctapi.ftqq.com/{SERVER_CHAN_SENDKEY}.send"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, data={"title": title, "desp": desp})
            result = resp.json()
            if result.get("code") == 0:
                print("[Server酱] 推送成功")
                return True
            print(f"[Server酱] 推送失败: {result}")
            return False
        except Exception as e:
            print(f"[Server酱] 推送异常: {e}")
            return False


async def push_report(
    jobs: list[Job],
    report_date: str,
    reminder: str = "",
) -> bool:
    """
    推送招聘摘要到微信。
    reminder：可选，Markdown 文本，插在推送最顶端（如 BOSS 扫码登录提醒）。
    只影响微信推送消息，绝不改动 HTML 报告的 UI/UX。
    返回是否推送成功。
    """
    # 过滤有效岗位（修复3：没有正确 JD 页面的绝对不要，防御性再滤一遍）
    valid = [j for j in jobs if not j.excluded and safe_url(j.url)]
    if len(valid) != sum(1 for j in jobs if not j.excluded):
        print(f"[Server酱] 过滤掉 {sum(1 for j in jobs if not j.excluded) - len(valid)} 条无有效JD页面的岗位")

    # 按类别统计
    stats: dict[str, int] = {}
    for job in valid:
        ct = job.company_type or CompanyType.OTHER
        stats[ct.value] = stats.get(ct.value, 0) + 1

    # 卡片版网页报告 URL（用户 2026-08-08：拆成行业类 + 专业类两份）
    industry_url = f"{GITHUB_PAGES_BASE}/output/{report_date}/report-industry.html"
    professional_url = f"{GITHUB_PAGES_BASE}/output/{report_date}/report-professional.html"

    # 构建摘要：提醒（如有）置顶 → 报告正文
    lines: list[str] = []
    if reminder:
        lines.append(reminder)
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.extend([
        f"## 🏙️ 成都招聘日报 ({report_date})",
        f"",
        f"**共 {len(valid)} 个岗位**",
        f"",
        f"📄 [行业类JD报告]({industry_url})",
        f"📄 [专业类JD报告]({professional_url})",
        "",
    ])
    for ct in ["国企", "央企", "外资", "合资", "其他"]:
        count = stats.get(ct, 0)
        if count > 0:
            lines.append(f"- {ct}：{count} 个")

    # 取每类前 2 个展示
    lines.append("")
    lines.append("---")
    lines.append("")

    for ct_label, ct_val in [("🔴 国企", CompanyType.STATE_OWNED), ("🟡 央企", CompanyType.CENTRAL),
                               ("🔵 外资", CompanyType.FOREIGN), ("🟢 合资", CompanyType.JOINT_VENTURE),
                               ("⚫ 其他", CompanyType.OTHER)]:
        ct_jobs = [j for j in valid if (j.company_type or CompanyType.OTHER) == ct_val]
        if ct_jobs:
            lines.append(f"**{ct_label}** ({len(ct_jobs)}个)")
            for j in ct_jobs[:3]:
                dist = f" {j.distance_km}km" if j.distance_km is not None else ""
                # 修复1：公司名做成 Markdown 链接，点击跳转到该岗位真实 JD 页面
                # ServerChan 要求整个链接在单行内，故公司名链接后跟标题在同一行
                jd_url = safe_url(j.url)
                company_link = f"[{j.company}]({jd_url})" if jd_url else j.company
                lines.append(f"> {company_link} - {j.title}")
                lines.append(f"> 💰{j.salary_display} 📍{j.location}{dist}")
                lines.append("")

    # 发送
    title = f"📊 成都招聘日报 {report_date} · {len(valid)}个岗位"
    body = "\n".join(lines)
    return await _send(title, body)


async def push_reminder_only(reminder: str) -> bool:
    """无检索数据时仅推送提醒（保证每日 11:00 都能收到扫码登录提醒）。"""
    title = "⚠️ BOSS直聘 Cookie 需刷新（今日暂无检索数据）"
    return await _send(title, reminder)
