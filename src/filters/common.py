"""
过滤器通用工具。

用户拍板原则（2026-08-05）：所有排除规则加一条——如果规则不是刚性，只是优先性，
就保留。例如"35岁以下优先""计算机相关专业优先"都是软性偏好，不排除。
"""
from __future__ import annotations

import re

# 软性偏好标记（命中视为"该要求是优先性而非刚性条件"）
PREFERENCE_RE = re.compile(r"优先|优先考虑|优先录用|择优|优先条件")

# 局部上下文分隔符：用于界定"紧邻规则命中处"的窗口，避免把相隔一个逗号的
# 独立偏好从句（如"35岁以下，党员优先"）误判为修饰当前规则
_WINDOW_SEP = re.compile(r"[，。；;、\n\r\s：:]+")


def has_preference(s: str) -> bool:
    """字符串内是否包含软性偏好标记。"""
    return bool(PREFERENCE_RE.search(s or ""))


def is_preference(text: str, start: int, end: int) -> bool:
    """
    判断 [start, end) 命中的规则短语是否属于"软性偏好"而非刚性要求。

    取命中处前后各 ≤8 字符、直到最近分隔符的局部片段：
    - "35岁以下优先" → 命中后紧跟"优先" → True（保留）
    - "35岁以下，党员优先" → 命中后紧跟"，"（分隔符）→ 窗口内无"优先" → False（刚性，排除）
    - "优先考虑35岁以下" → 命中前紧邻"优先考虑" → True（保留）
    """
    if not text:
        return False
    after = _WINDOW_SEP.split(text[end:end + 8], maxsplit=1)[0]
    before = _WINDOW_SEP.split(text[max(0, start - 8):start], maxsplit=1)[-1]
    return bool(PREFERENCE_RE.search(after) or PREFERENCE_RE.search(before))
