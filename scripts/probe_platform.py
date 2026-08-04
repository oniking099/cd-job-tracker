#!/usr/bin/env python3
"""临时探测脚本：对比 Chromium vs Camoufox 访问目标平台，判断是否需要 Camoufox。

用途：推广 Camoufox 方案到新平台前的第一步——确认该平台是否检测 CDP 自动化
（Chromium 跳 about:blank），以及登录墙/风控表现。

用法：
  python scripts/probe_platform.py --url "https://www.lagou.com/wn/jobs?city=成都&kd=气象" --platform lagou
  python scripts/probe_platform.py --url "https://cd.58.com/job/?key=气象" --platform wuba
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import SESSIONS_DIR


async def probe_chromium(url: str, tag: str) -> None:
    """系统 Chrome（Chromium 通道）访问：检测是否跳 about:blank。"""
    from playwright.async_api import async_playwright
    from src.scrapers.base import apply_stealth

    print(f"\n=== [{tag}] Chromium（系统 Chrome + stealth） ===")
    try:
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=str(SESSIONS_DIR / "profiles" / f"probe_{tag}"),
                channel="chrome", headless=True, locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                ignore_default_args=["--enable-automation"],
            )
            await apply_stealth(ctx)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)
            low = page.url.lower()
            body_len = await page.evaluate("() => document.body?.innerText.length || 0")
            body = (await page.evaluate("() => document.body?.innerText || ''"))[:150].replace("\n", " | ")
            print(f"  最终 URL: {page.url}")
            print(f"  about:blank: {'⚠️ 是（CDP 被检测）' if 'about:blank' in low else '否（正常加载）'}")
            print(f"  body 长度: {body_len}")
            print(f"  body 开头: {body}")
            await ctx.close()
    except Exception as e:
        print(f"  ❌ 异常: {e}")


async def probe_camoufox(url: str, tag: str) -> None:
    """Camoufox 访问：确认引擎本身能否过检测、是否登录墙。"""
    from camoufox.async_api import AsyncCamoufox

    print(f"\n=== [{tag}] Camoufox（Firefox fork 真实指纹） ===")
    try:
        async with AsyncCamoufox(
            persistent_context=True, headless=True, os="windows",
            locale="zh-CN", timezone_id="Asia/Shanghai",
            user_data_dir=str(SESSIONS_DIR / "profiles" / f"probe_{tag}"),
        ) as ctx:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3500)
            low = page.url.lower()
            body_len = await page.evaluate("() => document.body?.innerText.length || 0")
            body = (await page.evaluate("() => document.body?.innerText || ''"))[:150].replace("\n", " | ")
            print(f"  最终 URL: {page.url}")
            print(f"  about:blank: {'⚠️ 是（引擎被检测）' if 'about:blank' in low else '否（正常加载）'}")
            print(f"  body 长度: {body_len}")
            print(f"  body 开头: {body}")
    except Exception as e:
        print(f"  ❌ 异常: {e}")


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="探测 URL（中文需 URL 编码）")
    parser.add_argument("--platform", required=True, help="平台 tag（用于 profile 目录名）")
    parser.add_argument("--engine", default="both", choices=["chromium", "camoufox", "both"])
    args = parser.parse_args()

    url = args.url
    if args.engine in ("both", "chromium"):
        await probe_chromium(url, args.platform)
    if args.engine in ("both", "camoufox"):
        await probe_camoufox(url, args.platform)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
