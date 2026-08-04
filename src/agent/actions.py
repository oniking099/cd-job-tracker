"""
Agent 动作契约：Pydantic 模型 + 决策 prompt 模板。

决策模型每步输出一个 JSON 动作对象，由 AgentLoop 执行。
执行失败时上层会重试，因此这里提供宽容解析 + Pydantic 校验。
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

# 动作类型
ActionType = Literal[
    "navigate",  # 跳转到 text 指定的 URL
    "type",      # 在 target_id / target 元素里输入 text（逐字，拟人）
    "click",     # 点击 target_id / target / coord 指定的元素
    "scroll",    # 向下滚动视口（模拟人类翻看）
    "press",     # 按键盘键，如 Enter / Escape
    "wait",      # 等待页面加载（如点击搜索后等结果）
    "extract",   # 认为已拿到岗位列表，结束循环进入提取
    "done",      # 任务完成或无法继续（如登录墙）
]


class AgentAction(BaseModel):
    """决策模型输出的单个动作。"""

    thought: str = Field(default="", description="模型对该步的推理过程，写进调试日志")
    action: ActionType
    target_id: int | None = Field(
        default=None,
        description="元素清单中的索引，点击/输入时优先使用",
    )
    target: str = Field(default="", description="语义描述，target_id 不可用时兜底")
    text: str = Field(default="", description="type 的输入文本 / navigate 的 URL")
    coord: tuple[int, int] | None = Field(default=None, description="视口 CSS 坐标兜底 (x, y)")
    reason: str = Field(default="", description="执行这个动作的原因")

    def is_terminal(self) -> bool:
        """终止动作：extract / done 结束操作循环。"""
        return self.action in ("extract", "done")

    def signature(self) -> str:
        """动作签名，用于重复守卫比较（相同签名连续执行视为卡死）。"""
        target = self.target_id if self.target_id is not None else self.target
        return f"{self.action}:{target}:{self.text}"


# 决策模型的动作契约说明（注入 system prompt）
ACTION_CONTRACT = f"""\
你是一个浏览器操作智能体，模拟人类在招聘网站上找工作。每轮你看到页面截图和可交互元素清单，
决定执行一个动作。只输出一个 JSON 对象，字段如下：
{{
  "thought": "你对该页面的判断（简短）",
  "action": "navigate|type|click|scroll|press|wait|extract|done",
  "target_id": 0,        // 元素清单里的索引，点击/输入优先用它；没有则省略
  "target": "搜索按钮",   // 语义描述，target_id 不可用时用
  "text": "",            // type 要输入的文字 / navigate 的 URL
  "coord": [400, 300],   // 视口 CSS 坐标兜底 (x, y)，不需要则省略
  "reason": "为什么这样做"
}}
规则：
1. 只输出 JSON 对象本身，禁止 markdown 代码块、禁止多余文字、禁止数组。
2. 点击/输入优先用 target_id 引用元素清单里的元素，其次是 target 语义描述。
3. 不要点击广告、二维码、弹窗上的关闭按钮以外的无关元素。
4. 若页面出现"登录/验证码/扫码/安全验证"且没有岗位内容，直接输出 done，reason 写"登录墙"。
5. 若页面已出现招聘岗位列表且能看到足够多岗位，输出 extract，reason 写"已找到岗位列表"。
6. 每次只执行一个动作，执行后你会看到新的页面截图与元素清单。
"""

SYSTEM_PROMPT = (
    "你是一个谨慎、有耐心的浏览器操作智能体，只做对任务有帮助的动作，"
    "不要无意义地反复点击同一个元素。\n\n" + ACTION_CONTRACT
)


def build_decision_user_message(
    task: str,
    state_signal: str,
    last_result: str,
    history_lines: list[str],
    inventory_text: str,
    page_text_excerpt: str = "",
) -> str:
    """拼接决策模型 user 消息的文本部分（截图由调用方追加为图片）。"""
    parts = [
        f"【任务】{task}",
        f"【页面状态】{state_signal}",
        f"【上一步结果】{last_result}",
    ]
    if history_lines:
        parts.append("【最近动作历史】\n" + "\n".join(history_lines[-8:]))
    if inventory_text:
        parts.append(f"【可交互元素清单】\n{inventory_text}")
    else:
        parts.append("【可交互元素清单】（空，页面上没有可交互元素，可能需要等待加载或用 coord 点击）")
    if page_text_excerpt:
        parts.append(f"【页面文字摘要】{page_text_excerpt}")
    parts.append("请根据截图和元素清单，输出你决定执行的下一步动作 JSON：")
    return "\n\n".join(parts)


def parse_action(raw: str) -> AgentAction:
    """
    宽容解析模型输出的动作 JSON。
    清理 markdown 代码块、多余前后缀，取首个 JSON 对象，Pydantic 校验。
    解析或校验失败抛 ValueError（由决策层触发重试）。
    """
    text = raw.strip()

    # 清理 markdown 代码块
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    # 尝试直接解析
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # 取第一个 "{" 到最后一个 "}" 之间的子串再试
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"输出中没有 JSON 对象: {raw[:200]}")
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}, raw={raw[:200]}") from e

    if not isinstance(obj, dict):
        raise ValueError(f"JSON 不是对象: {obj}")

    try:
        return AgentAction(**obj)
    except Exception as e:
        raise ValueError(f"动作校验失败: {e}, obj={obj}") from e
