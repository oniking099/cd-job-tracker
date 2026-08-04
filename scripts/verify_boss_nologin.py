#!/usr/bin/env python3
"""临时验证：BOSS 未登录（空 profile）在 CI 场景能提取多少岗位。

CI runner 无法复用本地登录态（Firefox cookie 绑定本机 key4.db + 跨平台不兼容），
实测未登录可提取量，决定 CI 是否纳入 BOSS。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import SESSIONS_DIR
from src.scrapers.agent_scraper import BossAgentScraper


class BossNoLoginProbe(BossAgentScraper):
    """BossAgentScraper 但用空 profile（未登录）探测。"""

    @property
    def camoufox_user_dir(self) -> str:
        return str(SESSIONS_DIR / "profiles" / "boss_nologin_probe")


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    print("探测 BOSS 未登录提取量（空 profile，模拟 CI 无登录态）...")
    async with BossNoLoginProbe() as scraper:
        jobs = await scraper.search("气象", "probe")

    print(f"\n未登录提取到 {len(jobs)} 条：")
    for j in jobs[:15]:
        print(f"  - {j.title} | {j.company} | {j.salary_text} | {j.location} | {j.url[:70]}")
    if not jobs:
        print("（空结果——BOSS 未登录被登录框/风控完全挡住）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
