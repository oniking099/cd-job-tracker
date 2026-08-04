"""
主循环 AgentLoop：observe → think → act。

每步：感知（元素清单 + 截图 + 页面文本）→ 决策（多模态 LLM 输出动作 JSON）→ 执行。
防呆三件套：
① 重复守卫：相同动作连续 ≥N 次且页面未变 → 注入警告 / 判定卡死中止
② 状态信号：页面指纹未变 → 下轮 prompt 追加"页面未变化，请换策略"
③ 登录墙预检：step 1 检测到登录/验证码且无岗位信号 → 快速中止省 API
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

from src.agent.actions import AgentAction
from src.agent.decision import decide_next_action
from src.agent.executor import execute_action
from src.agent.perceive import (
    capture_screenshot,
    detect_login_wall,
    inventory_to_text,
    page_text_excerpt,
    snapshot_elements,
    state_fingerprint,
)

logger = logging.getLogger(__name__)

MAX_STEPS_DEFAULT = 15
REPETITION_WARN_THRESHOLD = 3   # 相同动作且页面未变连续 N 次 → 下轮注入警告
REPETITION_ABORT_THRESHOLD = 5  # 连续 N 次 → 判定卡死，中止

# 页面文本里出现这些词 → 更像正常招聘页，不是登录墙
_JOB_SIGNAL_KEYWORDS = ("薪资", "经验", "学历", "招聘", "岗位", "投递", "简历", "月薪")


@dataclass
class AgentStep:
    step: int
    action: AgentAction
    result_ok: bool
    result_message: str
    state_changed: bool
    screenshot_path: str = ""


@dataclass
class LoopResult:
    succeeded: bool
    reason: str
    steps: list[AgentStep] = field(default_factory=list)
    last_screenshot: bytes | None = None


def _looks_like_job_page(page_text: str) -> bool:
    return any(k in page_text for k in _JOB_SIGNAL_KEYWORDS)


def _format_step(s: AgentStep) -> str:
    ok = "✓" if s.result_ok else "✗"
    target = s.action.target or s.action.target_id or s.action.coord or ""
    return f"step{s.step}: [{ok}] {s.action.action}({target}) {s.result_message}"


def _format_last_result(history: list[AgentStep]) -> str:
    if not history:
        return "刚开始，尚未执行任何动作"
    last = history[-1]
    prefix = "已执行成功" if last.result_ok else "执行失败"
    return f"{last.action.action} {prefix}：{last.result_message}"


class AgentLoop:
    """在给定 Playwright page 上执行人类式操作循环。"""

    def __init__(
        self,
        page,
        task: str,
        trace_dir: Path | None = None,
        max_steps: int = MAX_STEPS_DEFAULT,
    ):
        self.page = page
        self.task = task
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.max_steps = max_steps

    async def run(self, start_url: str = "") -> LoopResult:
        """启动循环。start_url 非空则先导航。"""
        if start_url:
            try:
                await self.page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
                await self.page.wait_for_timeout(1500)
            except Exception as e:
                return LoopResult(False, f"导航失败: {e}")

        history: list[AgentStep] = []
        prev_fingerprint: str | None = None
        prev_action: AgentAction | None = None
        repeat_count = 0
        last_screenshot: bytes | None = None

        for step in range(1, self.max_steps + 1):
            # ---- 感知 ----
            inventory = await snapshot_elements(self.page)
            inventory_text = inventory_to_text(inventory)
            screenshot = await capture_screenshot(self.page)
            last_screenshot = screenshot
            page_text = await page_text_excerpt(self.page)
            fingerprint = await state_fingerprint(self.page)

            state_changed = (prev_fingerprint is not None) and (fingerprint != prev_fingerprint)
            prev_fingerprint = fingerprint

            # ---- 快速中止预检（step 1，省 API）----
            if step == 1:
                reason = self._quick_abort_reason(page_text, inventory)
                if reason:
                    await self._save_screenshot(screenshot, step, "abort")
                    return LoopResult(False, reason, steps=history, last_screenshot=screenshot)

            # ---- 状态信号 + 重复警告 ----
            if prev_fingerprint is not None and not state_changed:
                state_signal = "页面未发生变化（上一动作执行后页面状态相同）"
            else:
                state_signal = "页面状态已发生变化"
            if repeat_count >= REPETITION_WARN_THRESHOLD:
                state_signal += (
                    f" ⚠️ 你已连续执行相同动作 {repeat_count} 次且页面未变，请更换策略或输出 done"
                )

            # ---- 决策 ----
            try:
                action = await decide_next_action(
                    task=self.task,
                    state_signal=state_signal,
                    last_result=_format_last_result(history),
                    history_lines=[_format_step(s) for s in history],
                    inventory_text=inventory_text,
                    page_text=page_text,
                    screenshot=screenshot,
                )
            except Exception as e:
                logger.warning(f"[agent] 决策失败: {e}")
                await self._save_screenshot(screenshot, step, "abort")
                return LoopResult(False, f"决策失败: {e}", steps=history, last_screenshot=screenshot)

            # ---- 执行 ----
            result = await execute_action(self.page, action, inventory)
            shot_path = await self._save_screenshot(screenshot, step, f"after-{action.action}")
            history.append(AgentStep(
                step=step,
                action=action,
                result_ok=result.ok,
                result_message=result.message,
                state_changed=state_changed,
                screenshot_path=shot_path,
            ))

            # ---- 终止判断 ----
            if action.is_terminal():
                if action.action == "done" and "登录墙" in action.reason:
                    return LoopResult(False, action.reason, steps=history, last_screenshot=screenshot)
                return LoopResult(True, action.reason or "任务结束", steps=history, last_screenshot=screenshot)

            # ---- 重复守卫 ----
            sig = action.signature()
            if (
                prev_action is not None
                and sig == prev_action.signature()
                and not state_changed
            ):
                repeat_count += 1
            else:
                repeat_count = 0
            prev_action = action

            if repeat_count >= REPETITION_ABORT_THRESHOLD:
                return LoopResult(
                    False,
                    f"卡死循环（连续 {repeat_count} 次相同动作无变化）",
                    steps=history,
                    last_screenshot=screenshot,
                )

            await asyncio.sleep(random.uniform(0.6, 1.5))

        return LoopResult(
            False, f"达到最大步数 {self.max_steps}", steps=history, last_screenshot=last_screenshot,
        )

    def _quick_abort_reason(self, page_text: str, inventory: list[dict]) -> str | None:
        """step 1 预检：登录墙 / 空页面 → 直接中止。"""
        if detect_login_wall(page_text) and not _looks_like_job_page(page_text):
            return "登录墙（页面出现登录/验证码且无岗位内容，跳过此平台）"
        if len(page_text.strip()) < 20 and not inventory:
            return "空页面（无内容无元素，跳过此平台）"
        return None

    async def _save_screenshot(self, screenshot: bytes, step: int, tag: str) -> str:
        if not self.trace_dir:
            return ""
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            path = self.trace_dir / f"step-{step:02d}-{tag}.png"
            path.write_bytes(screenshot)
            return str(path)
        except Exception as e:
            logger.warning(f"[agent] 截图保存失败: {e}")
            return ""
