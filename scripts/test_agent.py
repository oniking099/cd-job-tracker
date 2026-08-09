#!/usr/bin/env python3
"""
本地验证 LLM Agent 智能体：跑单个平台单个关键词，打印操作 trace 和提取结果。

用法：
  python scripts/test_agent.py --platform zhilian --keyword "成都 气象"
  python scripts/test_agent.py --platform boss     --keyword "成都 气象"
  python scripts/test_agent.py --platform iguopin  --keyword "成都 气象"

支持全部 AGENT_SCRAPERS 平台（zhilian/job51/boss/liepin/wuba/chinahr/yupao/jobui/iguopin/qixiang/bjx_huanbao/gaoxiaojob）。
前提：.env 里配置 QWENVL_API_KEY（决策+提取主力），GEMINI_API_KEY 兜底。
操作过程截图存 data/agent-traces/<平台>/<关键词>/ 供人工核对。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 让脚本可以直接从仓库根运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scrapers import AGENT_SCRAPERS

# 平台 key → 适配器（动态读取 AGENT_SCRAPERS，新增平台自动可用）
PLATFORMS: dict[str, type] = {
    "zhilian": AGENT_SCRAPERS["智联招聘"],
    "job51": AGENT_SCRAPERS["51Job"],
    "boss": AGENT_SCRAPERS["BOSS直聘"],
    "liepin": AGENT_SCRAPERS["猎聘"],
    "wuba": AGENT_SCRAPERS["58同城"],
    "chinahr": AGENT_SCRAPERS["中华英才网"],
    "yupao": AGENT_SCRAPERS["鱼泡直聘"],
    "jobui": AGENT_SCRAPERS["职友集"],
    "iguopin": AGENT_SCRAPERS["国聘网"],
    "qixiang": AGENT_SCRAPERS["气象人才网"],
    "bjx_huanbao": AGENT_SCRAPERS["北极星环保招聘"],
    "gaoxiaojob": AGENT_SCRAPERS["高校人才网"],
}


async def main() -> int:
    # Windows 终端编码不一致（Python 按 GBK 输出，终端按 UTF-8 解码）→ 强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="本地验证 LLM Agent 智能体")
    parser.add_argument("--platform", choices=list(PLATFORMS), default="zhilian")
    parser.add_argument("--keyword", default="成都 气象")
    parser.add_argument("--round", default="test")
    args = parser.parse_args()

    scraper_cls = PLATFORMS[args.platform]
    print(f"平台: {scraper_cls.platform_name}   关键词: {args.keyword}")

    async with scraper_cls() as scraper:
        jobs = await scraper.search(args.keyword, args.round)

    print(f"\n提取到 {len(jobs)} 条岗位：")
    for j in jobs[:20]:
        print(f"  - {j.title} | {j.company} | {j.salary_text} | {j.location}")

    if not jobs:
        print("\n（未提取到岗位。查看 data/agent-traces/<平台>/<关键词>/ 下的 step 截图，")
        print("  核对 agent 是否完成了 输入关键词→点搜索→滚到列表 的操作。）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
