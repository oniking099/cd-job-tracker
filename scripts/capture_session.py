#!/usr/bin/env python3
"""
登录态采集器：本地有头浏览器手动登录平台，导出 Playwright storage_state 供爬虫复用。

用法：
  python scripts/capture_session.py --platform boss
  python scripts/capture_session.py --platform lagou
  python scripts/capture_session.py --url https://www.zhipin.com/ --platform boss

流程（自动检测模式，默认）：
  1. 弹出有头浏览器窗口，打开平台入口页
  2. 你在浏览器里手动完成登录（扫码/输账号）
  3. 脚本每 2 秒检测 Cookie 变化，检测到登录成功自动导出，无需按键
  4. 导出 .sessions/{platform}.json（含 cookies + localStorage）
  5. 爬虫通过 newContext({storage_state}) 复用该登录态，绕过登录墙

也可用 --manual 切到交互模式（登录后按 Enter 再导出）。

注意：
  - 登录态会过期（BOSS 约数天~一周），过期后重跑本脚本刷新即可
  - 导出的文件含真实会话 Cookie，已被 .gitignore 排除，不要提交
  - 账号密码全程在你自己的浏览器里输入，本脚本/对话都不接触凭据
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from src.config import SESSIONS_DIR
from src.scrapers.base import apply_stealth, UA_POOL, VIEWPORT_POOL

# 平台 → 默认入口 URL（打开后自行找登录入口；也可用 --url 指定）
PLATFORM_URLS = {
    "boss": "https://www.zhipin.com/",
    "lagou": "https://www.lagou.com/",
    "wuba": "https://cd.58.com/job/",
    "yupao": "https://www.yupao.com/chengdu/",  # 成都站（拼音路径，非数字城市码）
}

LOGIN_MARKERS = ["login", "passport", "verify", "captcha", "sso", "safe"]
LOGIN_TIMEOUT_S = 600  # 最长等待登录 10 分钟

# 平台 → 导航栏未登录标记文本（登录成功后消失；如鱼泡"登录丨注册"变用户名）。
# 缺省平台用 cookie 数判断；这类平台 cookie 判断不可靠（匿名 cookie 也达标），
# 必须等导航栏未登录标记消失才算真正登录。
LOGOUT_MARKER_BY_PLATFORM = {
    "yupao": "登录丨注册",
}


async def _wait_login_auto(page, context, timeout_s: int, platform: str = "") -> tuple[bool, object]:
    """轮询检测登录完成：Cookie 数量相对基线显著增加且不在登录/验证页。

    对设置了 LOGOUT_MARKER_BY_PLATFORM 的平台（如鱼泡）：cookie 增加只是匿名 cookie，
    必须额外确认导航栏未登录标记（"登录丨注册"）消失，才算真正登录。

    返回 (是否成功, storage_state 对象)。
    """
    # 基线：页面打开几秒后的基础 cookie 数（未登录也有部分 cookie）
    await page.wait_for_timeout(4000)
    baseline = len((await context.storage_state()).get("cookies", []))
    logout_marker = LOGOUT_MARKER_BY_PLATFORM.get(platform, "")
    if logout_marker:
        print(f"  基线 cookie 数: {baseline}，等待登录（检测导航栏「{logout_marker}」消失，最多 {timeout_s // 60} 分钟）...")
    else:
        print(f"  基线 cookie 数: {baseline}，等待登录（最多 {timeout_s // 60} 分钟，检测到即自动导出）...")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await page.wait_for_timeout(5000)  # 低频检测，避免干扰页面
        try:
            state = await context.storage_state()
            low_url = page.url.lower()
        except Exception as e:
            print(f"  ⚠️ 浏览器窗口异常/被关闭，无法继续: {e}")
            return False, None
        n = len(state.get("cookies", []))
        still_login = any(m in low_url for m in LOGIN_MARKERS)

        # 登录成功判断：cookie 明显增多（>= 基线+5）且当前不在登录/验证页
        if n >= baseline + 5 and not still_login:
            # 平台带导航栏标记：需额外确认未登录标记已消失（避免匿名 cookie 误判）
            if logout_marker:
                try:
                    html = await page.content()
                except Exception:
                    html = ""
                if logout_marker in html:
                    continue  # 导航栏仍是"登录丨注册"，未真正登录
            return True, state
    return False, None


# 检测 CDP 自动化连接的登录墙平台 → Camoufox 引擎（真实指纹）；其余平台用系统 Chrome
ENGINE_BY_PLATFORM = {
    "boss": "camoufox",
    "yupao": "camoufox",  # 实测鱼泡也会把 Chromium 跳 about:blank（同 BOSS 的 CDP 检测）
}


async def _capture_camoufox(platform: str, url: str, manual: bool) -> int:
    """Camoufox persistent context：C++ 引擎层伪造指纹，唯一能过 BOSS 检测的方案。

    - persistent_context=True + user_data_dir：登录态天然持久化在 profiles/{platform}/
    - 不再叠加 apply_stealth（JS 层注入可能破坏 Camoufox 的一致性指纹）
    - 登录完成后导出 storage_state JSON 供 new_context({storage_state}) 复用
    """
    from camoufox.async_api import AsyncCamoufox

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SESSIONS_DIR / f"{platform}.json"

    print(f"平台: {platform}")
    print("引擎: Camoufox（Firefox fork，真实指纹，绕 CDP 自动化检测）")
    print("登录态: 持久化到 profiles/{platform}/，同时导出 storage_state")

    async with AsyncCamoufox(
        persistent_context=True,
        headless=False,  # 必须要有界面才能手动登录
        os="windows",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_data_dir=str(SESSIONS_DIR / "profiles" / platform),
    ) as context:
        page = context.pages[0] if context.pages else await context.new_page()

        print(f"[1/2] 打开浏览器: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        if page.url.startswith("about:blank"):
            print("❌ 被 about:blank 拦截（Camoufox 未生效），检查指纹配置")
            return 1

        if manual:
            print("[2/2] 请在浏览器里手动完成登录，完成后按 Enter 继续...")
            await asyncio.to_thread(input)
            state = await context.storage_state()
            ok = True
        else:
            print("[2/2] 请在浏览器里手动完成登录（推荐 App 扫码）。")
            ok, state = await _wait_login_auto(page, context, LOGIN_TIMEOUT_S, platform)

        if not ok or state is None:
            print("❌ 未检测到登录成功。确认浏览器里已登录后重新运行本脚本。")
            return 1

        out_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        n_cookies = len(state.get("cookies", []))
        print(f"✅ 已导出 {n_cookies} 个 cookie 到 {out_path}")
        print("   爬虫将自动加载该登录态绕过登录墙。")
        return 0


async def _capture_chromium(platform: str, url: str, manual: bool) -> int:
    """系统真实 Chrome + persistent context（非 BOSS 平台默认引擎）。"""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SESSIONS_DIR / f"{platform}.json"

    # 固定桌面 UA/视口（移动版在桌面浏览器渲染异常；桌面版首页有登录入口）
    ua = UA_POOL[0]  # Windows Chrome 131
    vp = {"width": 1440, "height": 900}

    print(f"平台: {platform}")
    print(f"UA : {ua[:60]}...")
    print(f"视口: {vp['width']}x{vp['height']}")

    async with async_playwright() as p:
        # 用系统真实 Chrome（channel="chrome"）+ persistent context：
        #   - 真实指纹（GPU/字体/canvas）无法被 BOSS 检测
        #   - ignore_default_args 移除 --enable-automation，navigator.webdriver=undefined
        #   - 登录态天然持久化在 user_data_dir
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(SESSIONS_DIR / "profiles" / platform),
            channel="chrome",  # 系统真实 Chrome（用户机器上已确认存在）
            headless=False,    # 必须要有界面才能手动登录
            user_agent=ua,
            viewport=vp,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--start-maximized",
            ],
            ignore_default_args=["--enable-automation"],  # 关键：去掉自动化标志
        )
        # 与爬虫相同 stealth 配置，双保险
        await apply_stealth(context)
        page = context.pages[0] if context.pages else await context.new_page()

        print(f"[1/2] 打开浏览器: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)

        if manual:
            print("[2/2] 请在浏览器里手动完成登录，完成后按 Enter 继续...")
            await asyncio.to_thread(input)
            state = await context.storage_state()
            ok = True
        else:
            print("[2/2] 请在浏览器里手动完成登录（推荐 App 扫码）。")
            ok, state = await _wait_login_auto(page, context, LOGIN_TIMEOUT_S, platform)

        await context.close()

        if not ok or state is None:
            print("❌ 未检测到登录成功。确认浏览器里已登录后重新运行本脚本。")
            return 1

        out_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        n_cookies = len(state.get("cookies", []))
        print(f"✅ 已导出 {n_cookies} 个 cookie 到 {out_path}")
        print("   爬虫将自动加载该登录态绕过登录墙。")
        return 0


async def capture(platform: str, url: str, manual: bool, engine: str = "") -> int:
    # 引擎选择：--engine 显式指定 > 平台默认（BOSS 强制 Camoufox）
    engine = engine or ENGINE_BY_PLATFORM.get(platform, "chromium")
    if engine == "camoufox":
        return await _capture_camoufox(platform, url, manual)
    return await _capture_chromium(platform, url, manual)


def main() -> int:
    # Windows 终端编码不一致 → 强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="手动登录导出登录态（storage_state）")
    parser.add_argument("--platform", default="boss", help="平台 key（boss/lagou/wuba/yupao）")
    parser.add_argument("--url", default="", help="自定义登录入口 URL（默认取 PLATFORM_URLS）")
    parser.add_argument("--manual", action="store_true", help="交互模式：登录后按 Enter 再导出")
    parser.add_argument(
        "--engine", default="",
        help="浏览器引擎：camoufox / chromium（默认按平台自动选，BOSS 强制 camoufox）",
    )
    args = parser.parse_args()

    url = args.url or PLATFORM_URLS.get(args.platform, "")
    if not url:
        print(f"未知平台 {args.platform}，支持: {list(PLATFORM_URLS)}，或 --url 指定")
        return 1

    return asyncio.run(capture(args.platform, url, args.manual, args.engine))


if __name__ == "__main__":
    sys.exit(main())
