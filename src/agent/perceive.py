"""
感知层：构建决策模型每步看到的页面状态。

三份输入：
1. 可交互元素清单（含 viewport CSS 坐标，直接可用于 page.mouse.click）
2. 页面截图（多模态"看"）
3. 页面文本摘要（登录墙预检 / 决策辅助）
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

logger = logging.getLogger(__name__)

# 登录墙/风控关键词（命中且无岗位元素 → 快速跳过，省 API 调用）
LOGIN_WALL_KEYWORDS = (
    "登录", "扫码", "验证码", "安全验证", "人机验证",
    "请先登录", "账号密码", "手机号登录", "验证身份",
)


# 收集可见可交互元素的 JS（返回 viewport 内、尺寸可见的元素，含中心坐标）
_ELEMENT_INVENTORY_JS = r"""() => {
    const selector = [
        'input', 'button', 'a[href]', 'textarea', 'select',
        '[role="button"]', '[role="link"]', '[role="tab"]',
        '[role="checkbox"]', '[role="radio"]', '[role="searchbox"]',
        '[onclick]', '[contenteditable="true"]'
    ].join(',');
    const nodes = document.querySelectorAll(selector);
    const raw = [];
    for (const el of nodes) {
        const r = el.getBoundingClientRect();
        if (r.width < 5 || r.height < 5) continue;
        if (r.bottom < 0 || r.right < 0 || r.top > window.innerHeight || r.left > window.innerWidth) continue;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') continue;
        const text = (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40);
        const ph = (el.getAttribute && (el.getAttribute('placeholder') || '')) || '';
        const aria = (el.getAttribute && (el.getAttribute('aria-label') || '')) || '';
        const name = (el.getAttribute && el.getAttribute('name')) || '';
        const type = (el.getAttribute && el.getAttribute('type')) || '';
        raw.push({
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || '',
            type: type,
            name: name,
            text: text,
            placeholder: ph,
            aria: aria,
            x: Math.round(r.x + r.width / 2),
            y: Math.round(r.y + r.height / 2),
        });
    }
    // 同坐标同文案同标签的去重（SPA 重复渲染）
    const uniq = [];
    for (const el of raw) {
        const dup = uniq.find(u => u.x === el.x && u.y === el.y && u.text === el.text && u.tag === el.tag);
        if (!dup) uniq.push(Object.assign({id: uniq.length}, el));
    }
    return uniq;
}
"""

# 页面滚动/尺寸快照（用于状态指纹）
_SCROLL_JS = "() => [window.scrollX, window.scrollY, window.innerHeight, document.body ? document.body.scrollHeight : 0]"


async def snapshot_elements(page) -> list[dict]:
    """收集可见可交互元素清单（含 id 与 viewport CSS 中心坐标）。"""
    try:
        els = await page.evaluate(_ELEMENT_INVENTORY_JS)
        return els if isinstance(els, list) else []
    except Exception as e:
        logger.warning(f"[agent] 元素清单获取失败: {e}")
        return []


async def capture_screenshot(page) -> bytes:
    """截取当前视口（device pixels，多模态模型使用）。"""
    return await page.screenshot(type="png")


async def page_text_excerpt(page, limit: int = 300) -> str:
    """页面可见文本摘要（登录墙预检 / 决策辅助）。"""
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return ""
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


async def page_full_text(page) -> str:
    """页面完整可见文本（详情页 JD 正文提取用，不截断）。

    与 page_text_excerpt 的区别：excerpt 供 agent 决策/预检（300 字符足够），
    这里保留全文供详情富集写入 responsibilities/requirements。
    """
    try:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        return ""
    return re.sub(r"[ \t]+", " ", (text or "")).strip()


async def state_fingerprint(page) -> str:
    """页面状态指纹：元素清单 + 滚动位置哈希，用于检测页面是否变化。"""
    els = await snapshot_elements(page)
    try:
        scroll = await page.evaluate(_SCROLL_JS)
    except Exception:
        scroll = [0, 0, 0, 0]
    payload = json.dumps({"els": els, "scroll": scroll}, ensure_ascii=False)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def inventory_to_text(inventory: list[dict], limit: int = 40) -> str:
    """元素清单转紧凑文本，注入决策 prompt。"""
    if not inventory:
        return ""
    lines = []
    for el in inventory[:limit]:
        parts = []
        if el.get("tag"):
            parts.append(el["tag"])
        if el.get("role"):
            parts.append(f"role={el['role']}")
        if el.get("type"):
            parts.append(f"type={el['type']}")
        label = el.get("text") or el.get("placeholder") or el.get("aria") or el.get("name")
        if label:
            parts.append(f'"{label}"')
        parts.append(f"@{el.get('x')},{el.get('y')}")
        lines.append(f"[{el['id']}] {' '.join(parts)}")
    if len(inventory) > limit:
        lines.append(f"...共 {len(inventory)} 个元素，仅列出前 {limit} 个")
    return "\n".join(lines)


def detect_login_wall(text: str) -> bool:
    """页面文本命中登录/验证关键词则判定为登录墙。"""
    return any(k in text for k in LOGIN_WALL_KEYWORDS)
