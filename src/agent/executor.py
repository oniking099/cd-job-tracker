"""
执行层：把 AgentAction 转成真实的浏览器操作。

定位优先级：元素清单 target_id（坐标点击）→ 语义描述（Playwright 定位级联）→ coord 兜底。
输入用逐字拟人输入（delay 40~120ms），滚动用鼠标滚轮模拟人类翻看。
"""
from __future__ import annotations

import logging
import random

logger = logging.getLogger(__name__)


class ActionResult:
    """动作执行结果。"""

    def __init__(self, ok: bool, message: str = ""):
        self.ok = ok
        self.message = message

    def __repr__(self) -> str:
        return f"ActionResult(ok={self.ok}, message={self.message})"


async def execute_action(page, action, inventory: list[dict]) -> ActionResult:
    """执行单个动作，返回 (是否成功, 说明)。"""
    name = action.action
    try:
        if name == "type":
            return await _do_type(page, action, inventory)
        if name == "click":
            return await _do_click(page, action, inventory)
        if name == "scroll":
            return await _do_scroll(page, action)
        if name == "press":
            return await _do_press(page, action)
        if name == "navigate":
            return await _do_navigate(page, action)
        if name == "wait":
            await page.wait_for_timeout(1500)
            return ActionResult(True, "已等待 1.5s")
        if name in ("extract", "done"):
            return ActionResult(True, f"终止动作: {name}")
        return ActionResult(False, f"未知动作: {name}")
    except Exception as e:
        logger.warning(f"[agent] 动作执行异常 {name}: {e}")
        return ActionResult(False, f"{name} 执行异常: {e}")


def find_element(inventory: list[dict], target_id: int | None, target_text: str) -> dict | None:
    """从元素清单找元素：优先 target_id，其次 target 文本模糊匹配。"""
    if target_id is not None:
        for el in inventory:
            if el.get("id") == target_id:
                return el
    if target_text:
        kw = target_text.lower()
        for el in inventory:
            hay = f"{el.get('tag')} {el.get('text')} {el.get('placeholder')} {el.get('aria')}".lower()
            if kw in hay:
                return el
    return None


async def _semantic_locator(page, text: str):
    """语义定位级联：placeholder → button/link role → 文本。找不到返回 None。"""
    candidates = []
    try:
        loc = page.get_by_placeholder(text, exact=False)
        if await loc.count() > 0:
            candidates.append(loc.first)
    except Exception:
        pass
    try:
        for role in ("button", "link", "searchbox"):
            loc = page.get_by_role(role, name=text, exact=False)
            if await loc.count() > 0:
                candidates.append(loc.first)
                break
    except Exception:
        pass
    try:
        loc = page.get_by_text(text, exact=False)
        if await loc.count() > 0:
            candidates.append(loc.first)
    except Exception:
        pass
    return candidates[0] if candidates else None


async def _focus_target(page, action, inventory: list[dict]) -> bool:
    """点击目标元素使其获得焦点。"""
    el = find_element(inventory, action.target_id, action.target)
    if el:
        await page.mouse.click(el["x"], el["y"])
        return True
    if action.target:
        loc = await _semantic_locator(page, action.target)
        if loc is not None:
            try:
                await loc.click()
                return True
            except Exception:
                pass
    if action.coord:
        await page.mouse.click(action.coord[0], action.coord[1])
        return True
    return False


async def _do_type(page, action, inventory: list[dict]) -> ActionResult:
    if not action.text:
        return ActionResult(False, "type 动作缺少 text")
    if not await _focus_target(page, action, inventory):
        return ActionResult(False, f"找不到输入目标: target={action.target!r} id={action.target_id}")
    # 清空已有内容后逐字输入（拟人）
    try:
        await page.keyboard.press("Control+A")
        await page.wait_for_timeout(100)
        await page.keyboard.type(action.text, delay=random.randint(40, 120))
    except Exception as e:
        return ActionResult(False, f"输入失败: {e}")
    return ActionResult(True, f"在 {action.target or action.target_id} 输入 '{action.text}'")


async def _do_click(page, action, inventory: list[dict]) -> ActionResult:
    el = find_element(inventory, action.target_id, action.target)
    if el:
        await page.mouse.click(el["x"], el["y"])
        return ActionResult(True, f"点击元素[{el['id']}] {el.get('tag')} @{el['x']},{el['y']}")
    if action.coord:
        await page.mouse.click(action.coord[0], action.coord[1])
        return ActionResult(True, f"点击坐标 {action.coord}")
    if action.target:
        loc = await _semantic_locator(page, action.target)
        if loc is not None:
            try:
                await loc.click()
                return ActionResult(True, f"点击(语义) {action.target}")
            except Exception as e:
                return ActionResult(False, f"语义点击失败: {e}")
    return ActionResult(False, f"找不到可点击元素: target={action.target!r} id={action.target_id}")


async def _do_scroll(page, action) -> ActionResult:
    delta = random.randint(400, 900)
    try:
        vp = page.viewport_size
        cx, cy = (vp["width"] // 2, vp["height"] // 2) if vp else (960, 400)
        await page.mouse.move(cx, cy)
        await page.mouse.wheel(0, delta)
        await page.wait_for_timeout(random.randint(400, 900))
        return ActionResult(True, f"向下滚动 {delta}px")
    except Exception as e:
        await page.evaluate(f"window.scrollBy(0, {delta})")
        await page.wait_for_timeout(500)
        return ActionResult(True, f"向下滚动(JS) {delta}px，原因: {e}")


async def _do_press(page, action) -> ActionResult:
    if not action.text:
        return ActionResult(False, "press 动作缺少按键名")
    await page.keyboard.press(action.text)
    await page.wait_for_timeout(500)
    return ActionResult(True, f"按键 {action.text}")


async def _do_navigate(page, action) -> ActionResult:
    if not action.text:
        return ActionResult(False, "navigate 动作缺少 URL")
    try:
        await page.goto(action.text, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logger.warning(f"[agent] 导航失败 {action.text}: {e}")
        return ActionResult(False, f"导航失败: {e}")
    await page.wait_for_timeout(1000)
    return ActionResult(True, f"导航到 {action.text}")
