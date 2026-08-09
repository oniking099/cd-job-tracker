#!/usr/bin/env python3
"""
报告生成入口脚本。
由 GitHub Actions 在 21:30 BJT 触发。
汇总当日所有搜索数据，生成 HTML 报告，推送到微信。
"""
import os
import sys
import argparse
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pipeline import run_report


async def main():
    parser = argparse.ArgumentParser(description="报告生成（+ 可选微信推送）")
    parser.add_argument("--no-push", action="store_true",
                        help="只生成 HTML 报告、不推送微信（每日 11:00 由 push.yml 单独推送）")
    args = parser.parse_args()

    print("开始生成每日报告...")
    try:
        jobs = await run_report(push=not args.no_push)
        print(f"\n报告生成完成！有效岗位: {len(jobs) if jobs else 0} 个")
    except Exception as e:
        print(f"报告生成出错: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
