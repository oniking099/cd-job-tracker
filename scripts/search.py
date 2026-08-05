#!/usr/bin/env python3
"""
搜索轮次入口脚本（顺序制 round-1~5）。

由 GitHub Actions 顺序执行：
    python scripts/search.py --round 1
    python scripts/search.py --round 2
    ...
每轮结束后调用轮次确认（数量门槛 + LLM 评审），确认结果写入 round JSON。
确认完成一轮后，CI 才提交该轮数据并进入下一轮。

兼容旧用法：无 --round / 无 SEARCH_ROUND 时，按 BJT 时段自动映射到顺序制轮次。
"""
import argparse
import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pipeline import run_search_round
from src.confirm import confirm_round

# 北京时间时区
BJT = timezone(timedelta(hours=8))

# 兼容旧用法：旧 6 轮按执行小时命名（12~17），映射到顺序制轮次
AUTO_MAP = {"12": "1", "13": "2", "14": "3", "15": "4", "16": "5"}


def get_current_round() -> str:
    """根据当前北京时间自动判断轮次（仅兼容无参调用）。"""
    now = datetime.now(BJT)
    hour = now.hour
    return AUTO_MAP.get(str(hour), "1")


async def main():
    parser = argparse.ArgumentParser(description="运行一轮检索并确认")
    parser.add_argument(
        "--round", type=str, default="",
        help="顺序制轮次 1~5（缺省按 BJT 时段自动判断）",
    )
    args = parser.parse_args()

    round_label = args.round or os.environ.get("SEARCH_ROUND") or get_current_round()

    print(f"开始执行搜索轮次: round-{round_label}")
    print(f"当前时间: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')} BJT")

    # 单轮内部已做 21:00 BJT 截止兜底与异常捕获，最多影响本轮数据质量，不抛给 CI
    round_data = await run_search_round(round_label)
    print(f"\n完成！原始 {round_data.total_raw} 条，有效 {round_data.total_after_filter} 条")

    # 轮次确认：数量门槛 + LLM 评审，结果写回 round JSON
    try:
        confirmation = await confirm_round(round_data)
        print(
            f"确认: {confirmation.get('result')} — "
            f"有效 {confirmation['valid_count']}/{confirmation['total_count']} "
            f"(门槛 {confirmation['threshold']})"
        )
        reason = confirmation.get("reason")
        if reason:
            print(f"      原因: {reason}")
    except Exception as e:
        print(f"轮次确认失败（不影响数据落盘）: {e}")


if __name__ == "__main__":
    asyncio.run(main())
