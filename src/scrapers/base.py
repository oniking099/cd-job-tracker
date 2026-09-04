"""
爬虫基类：Playwright + playwright-stealth 封装。
所有平台爬虫继承此类，获得统一的反爬、重试、降级能力。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
)
from playwright_stealth import Stealth

from src.config import (
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    MAX_RETRIES,
    SEARCH_TIMEOUT,
)
from src.models import Job

# 真实浏览器 User-Agent 池（移动端+桌面端）
UA_POOL: list[str] = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Safari macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Mobile Chrome
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.135 Mobile Safari/537.36",
    # Mobile Safari
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
]

VIEWPORT_POOL: list[dict] = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 390, "height": 844},   # iPhone 14
    {"width": 412, "height": 915},   # Android
]


async def apply_stealth(context: BrowserContext) -> None:
    """注入 playwright-stealth 反检测（模块级，供爬虫与登录态采集脚本复用）。

    登录态采集脚本必须用与爬虫完全相同的 stealth 配置，否则 BOSS 等强风控平台
    检测到裸自动化浏览器会无限刷新/跳转验证，页面根本出不来。
    """
    stealth = Stealth(
        navigator_webdriver=True,   # 1.x 的 webdriver → navigator_webdriver
        webgl_vendor=True,
        chrome_app=True,
        chrome_csi=True,
        chrome_load_times=True,
        chrome_runtime=True,
        iframe_content_window=True,
        media_codecs=True,
        navigator_hardware_concurrency=4,
        navigator_languages=True,
        navigator_permissions=True,
        navigator_platform=True,
        navigator_plugins=True,
        navigator_user_agent=False,
        navigator_vendor=True,
        hairline=True,
        # outerdimensions 在 2.x 已移除
    )
    await stealth.apply_stealth_async(context)


class BaseScraper(ABC):
    """平台爬虫基类"""

    platform_name: str = ""  # 子类必须设置
    base_url: str = ""       # 平台首页 URL
    search_url: str = ""     # 搜索页 URL 模板

    def __init__(self):
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        launch_kwargs = dict(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
            # 移除 playwright 默认的 --enable-automation 自动化标志，
            # 否则 BOSS 等强风控平台检测到 CDP 自动化特征会把页面跳到 about:blank
            ignore_default_args=["--enable-automation"],
        )
        try:
            # 本地优先系统真实 Chrome：指纹（GPU/字体/canvas）全真，能过 BOSS 的 CDP 检测
            self.browser = await self._playwright.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception as e:
            # CI（GitHub Actions runner）无系统 Chrome → 回退内置 Chromium
            logger.info(f"[{self.platform_name}] 无系统 Chrome，回退内置 Chromium: {e}")
            self.browser = await self._playwright.chromium.launch(**launch_kwargs)
        return self

    async def __aexit__(self, *args):
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    def _storage_state_path(self) -> str:
        """登录态文件路径（.sessions/{platform}.json）。返回空字符串表示该平台无需登录态。

        登录态由 scripts/capture_session.py 手动登录一次后导出；
        存在时 newContext 自动加载 Cookie/localStorage，实现免登录访问。
        """
        return ""

    async def _new_context(self) -> BrowserContext:
        """创建带 stealth 注入的浏览器上下文（可选加载登录态）"""
        ua = random.choice(UA_POOL)
        vp = random.choice(VIEWPORT_POOL)

        context_kwargs = dict(
            user_agent=ua,
            viewport=vp,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation"],
            geolocation={"latitude": 30.5728, "longitude": 104.0668},  # 成都
        )

        # 登录态复用：子类指定 storage_state 文件且存在时加载（免登录访问，绕过登录墙）
        storage_path = self._storage_state_path()
        if storage_path and Path(storage_path).exists():
            context_kwargs["storage_state"] = str(storage_path)

        context = await self.browser.new_context(**context_kwargs)

        # Stealth 注入（与 apply_stealth 复用同一套配置，保证登录态指纹一致）
        await apply_stealth(context)

        return context

    async def _random_delay(self):
        """随机延迟 2~5 秒"""
        delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
        await asyncio.sleep(delay)

    async def _human_scroll(self, page: Page, times: int = 3):
        """模拟人类滚动行为"""
        for _ in range(times):
            scroll_amount = random.randint(200, 600)
            await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            await asyncio.sleep(random.uniform(0.5, 1.5))

    async def _retry_get(self, page: Page, url: str) -> bool:
        """带重试的页面导航"""
        for attempt in range(MAX_RETRIES):
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if response and response.ok:
                    return True
            except Exception as e:
                logger.warning(f"[{self.platform_name}] 请求失败(第{attempt+1}次): {url}, 原因: {e}")

            if attempt < MAX_RETRIES - 1:
                wait = 5 * (2 ** attempt)
                await asyncio.sleep(wait)

        return False

    async def _parse_with_fallback(self, page: Page) -> list[Job]:
        """
        HTML 解析失败时的降级方案：
        截图 → ModelScope VL 视觉提取 → GLM-5.3-Flash 备用。
        （用户 2026-09-04：视觉链只留 ModelScope → GLM，Qwen-VL/Gemini 已移除。）
        """
        screenshot_bytes = await page.screenshot(type="png", full_page=False)

        # 尝试 ModelScope（免费 2000/天，VL 链内自动降级）
        try:
            from src.llm.modelscope import extract_jobs_from_screenshot as modelscope_extract
            return await modelscope_extract(screenshot_bytes, self.platform_name)
        except Exception as e:
            logger.warning(f"[{self.platform_name}] ModelScope 降级提取失败: {e}")

        # 尝试 GLM-5.3-Flash（智谱 Coding Plan）
        try:
            from src.llm.glm import extract_jobs_from_screenshot as glm_extract
            return await glm_extract(screenshot_bytes, self.platform_name)
        except Exception as e:
            logger.warning(f"[{self.platform_name}] GLM 降级提取失败: {e}")

        return []

    @abstractmethod
    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        """搜索岗位"""
        ...
