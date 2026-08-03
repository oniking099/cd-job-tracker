#!/usr/bin/env python3
"""
搜索轮次入口脚本。
由 GitHub Actions 在 12:00~17:00 BJT 每小时触发。
通过环境变量 SEARCH_ROUND 指定轮次（12/13/14/15/16/17），
或根据当前北京时间自动判断。
"""
import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pipeline import run_search_round

# 北京时间时区
BJT = timezone(timedelta(hours=8))


def get_current_round() -> str:
    """根据当前北京时间返回轮次标签"""
    now = datetime.now(BJT)
    hour = now.hour
    if hour < 12 or hour > 17:
        # 非搜索时段，默认走 12 点逻辑
        return "12"
    return str(hour)


async def main():
    # 优先从环境变量获取，否则自动判断
    round_label = os.environ.get("SEARCH_ROUND") or get_current_round()

    print(f"开始执行搜索轮次: {round_label}")
    print(f"当前时间: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')} BJT")

    try:
        round_data = await run_search_round(round_label)
        print(f"\n完成！获取 {round_data.total_raw} 条，有效 {round_data.total_after_filter} 条")
    except Exception as e:
        print(f"搜索出错: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
