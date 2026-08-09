#!/usr/bin/env python3
"""每日 11:00 BJT 微信推送：加载最近一日检索数据 → ServerChan 推送结果 + BOSS 扫码登录提醒。

背景（用户 2026-08-09）：BOSS cookie 约 24h 过期，需每天扫码登录刷新，否则拉不到 JD。
检索改为 17:00 BJT 开始，故 11:00 的推送要「提醒用户当天 17:00 前登录」，并附上前一日结果。
HTML 报告不在此生成（search.yml 检索完已生成并提交 output/），本脚本只做微信推送。

本脚本只改微信推送消息，绝不改动 HTML 报告的 UI/UX。
"""
from __future__ import annotations

import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DATA_DIR
from src.storage import load_deduped
from src.notify.serverchan import push_report, push_reminder_only

# BOSS 扫码登录提醒：加红加粗、置顶（ServerChan desp 支持内联 HTML 变色/加粗）
LOGIN_REMINDER = (
    '<font color="red"><b>⚠️ 重要提醒：BOSS直聘 Cookie 可能已过期，'
    '请在今日 17:00 检索前扫码登录刷新，否则 BOSS 拉不到 JD！</b></font>'
    "\n\n"
    "**在本地电脑操作（约 1 分钟）：**\n"
    "1. 打开命令行执行：`python scripts/capture_session.py --platform boss`\n"
    "2. 会自动弹出浏览器窗口（BOSS直聘），用手机 App **扫码登录**\n"
    "3. 登录成功后，把 `.sessions/boss.json` 里的 cookies 数组，"
    "更新到 GitHub secret `BOSS_COOKIE`\n\n"
    "<font color=\"red\"><b>不刷新的话，今天 BOSS 的 JD 就拉不到了。</b></font>"
)


def _latest_data_date() -> str | None:
    """最近一个有 deduped.json 的日期目录。

    检索 17:00 BJT 写 data/D/，推送次日 11:00 BJT 跑时 bjt_today() 已是 D+1，
    必须显式取最近完成检索的那一天，否则 load_deduped 会落到空目录。
    """
    candidates = []
    for d in DATA_DIR.iterdir():
        if d.is_dir() and (d / "deduped.json").exists():
            candidates.append(d.name)
    return sorted(candidates)[-1] if candidates else None


async def main() -> int:
    date = _latest_data_date()
    jobs = load_deduped(date) if date else []
    if date and jobs:
        print(f"[push_daily] 推送 {date} 数据（{len(jobs)} 条）+ 登录提醒")
        ok = await push_report(jobs, date, reminder=LOGIN_REMINDER)
    else:
        print(f"[push_daily] 无检索数据（date={date}），仅推送登录提醒")
        ok = await push_reminder_only(LOGIN_REMINDER)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
