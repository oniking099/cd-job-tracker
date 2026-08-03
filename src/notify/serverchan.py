"""
Server酱推送模块：将招聘摘要推送到微信。
官方文档：https://sct.ftqq.com/
"""
from __future__ import annotations

import httpx

from src.config import SERVER_CHAN_SENDKEY
from src.models import Job, CompanyType


async def push_report(
    jobs: list[Job],
    report_date: str,
) -> bool:
    """
    推送招聘摘要到微信。
    返回是否推送成功。
    """
    if not SERVER_CHAN_SENDKEY:
        print("[Server酱] SENDKEY 未配置，跳过推送")
        return False

    # 过滤有效岗位
    valid = [j for j in jobs if not j.excluded]

    # 按类别统计
    stats: dict[str, int] = {}
    for job in valid:
        ct = job.company_type or CompanyType.OTHER
        stats[ct.value] = stats.get(ct.value, 0) + 1

    # 构建摘要
    lines = [
        f"## 🏙️ 成都招聘日报 ({report_date})",
        f"",
        f"**共 {len(valid)} 个岗位**",
        "",
    ]
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
                lines.append(f"> {j.company} - {j.title}")
                lines.append(f"> 💰{j.salary_display} 📍{j.location}{dist}")
                lines.append("")

    # 发送
    url = f"https://sctapi.ftqq.com/{SERVER_CHAN_SENDKEY}.send"
    title = f"📊 成都招聘日报 {report_date} · {len(valid)}个岗位"
    body = "\n".join(lines)

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, data={
                "title": title,
                "desp": body,
            })
            result = resp.json()
            if result.get("code") == 0:
                print(f"[Server酱] 推送成功")
                return True
            else:
                print(f"[Server酱] 推送失败: {result}")
                return False
        except Exception as e:
            print(f"[Server酱] 推送异常: {e}")
            return False
