"""
LLM Agent 智能体：多模态模型驱动浏览器，模拟人类操作招聘网站。
observe → think → act 循环 + 多截图视觉提取。
"""
from src.agent.actions import AgentAction
from src.agent.agent import AgentLoop, AgentStep, LoopResult
from src.agent.extract import extract_jobs_from_page

__all__ = [
    "AgentAction",
    "AgentLoop",
    "AgentStep",
    "LoopResult",
    "extract_jobs_from_page",
]
