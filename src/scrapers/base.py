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
        self.browser = await self._playwright.chromium.launch(
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
        )
        return self

    async def __aexit__(self, *args):
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _new_context(self) -> BrowserContext:
        """创建带 stealth 注入的浏览器上下文"""
        ua = random.choice(UA_POOL)
        vp = random.choice(VIEWPORT_POOL)

        context = await self.browser.new_context(
            user_agent=ua,
            viewport=vp,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation"],
            geolocation={"latitude": 30.5728, "longitude": 104.0668},  # 成都
        )

        # Stealth 注入（playwright-stealth 2.x API）
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
        截图 → Qwen-VL-Max 视觉提取 → Gemini 备用。
        """
        screenshot_bytes = await page.screenshot(type="png", full_page=False)

        # 尝试 Qwen-VL-Max
        try:
            from src.llm.qwen_vl import extract_jobs_from_screenshot as qwen_extract
            return await qwen_extract(screenshot_bytes, self.platform_name)
        except Exception as e:
            logger.warning(f"[{self.platform_name}] Qwen-VL 降级提取失败: {e}")

        # 尝试 Gemini
        try:
            from src.llm.gemini import extract_jobs_from_screenshot as gemini_extract
            return await gemini_extract(screenshot_bytes, self.platform_name)
        except Exception as e:
            logger.warning(f"[{self.platform_name}] Gemini 降级提取失败: {e}")

        return []

    @abstractmethod
    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        """搜索岗位"""
        ...
