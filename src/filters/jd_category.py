"""
JD 行业/专业分类：将岗位拆分为「行业类」与「专业类」两份报告。

分类规则（用户要求 2026-08-08；精细版 2026-08-09 用户确认）：
- 行业类：大气科学 / 气象 / 大气环境 / 环保 / 生态 等领域岗位
- 专业类：AI / agent / 大模型 / 深度学习 等领域岗位
- 同一岗位同时命中两类（标题或正文任一来源）-> 归行业类（用户明确要求）
- 两类都不命中（如「算法工程师」「Python开发」「解决方案经理」）-> 归行业类（兜底）

匹配依据（精细版）：标题 + 正文（responsibilities + requirements），两处信号合并判定。
正文匹配必须降噪，否则会把大量岗位错划：
1. 行业正文词只用具体复合词，排除三个高噪声裸词——
   裸「环境」（「办公环境/开发环境」会把 AI 岗误判行业）、
   裸「大气」（「高端大气」是装修形容词）、
   裸「生态」（「AI生态/开源生态/生态合作」是互联网黑话）；
   这些领域靠「生态环境/大气科学/大气环境」等复合词仍能被正文兜住。
2. 专业正文拉丁词（ai/llm/agent 等）用单词边界正则——裸子串会命中
   email / detail / retail 等英文单词里的 "ai"。
"""
from __future__ import annotations

import logging
import re

from src.models import Job

logger = logging.getLogger(__name__)

# 行业类关键词（标题用）：大气科学 / 气象 / 大气环境 / 环保 / 生态 / 水处理
# 注意：不用裸「环境」--「AI工程师（外企英文办公环境）」里的「办公环境」会假阳性
# 把 AI 岗误判为行业类。改用「环境检测/环境评价/环境监测/水环境/微污染」等具体复合词；
# 标题只有裸「环境」（如「高级工程师(勘察技术/环境)」「QEHS」）的岗位靠兜底仍归行业类。
INDUSTRY_KEYWORDS: list[str] = [
    "气象", "大气", "气候", "环保", "生态",
    "碳中和", "低碳", "水处理", "水环境", "微污染", "大气科学",
    "环境检测", "环境评价", "环境监测",
]

# 专业类关键词（标题用）：AI / agent / 大模型 / 深度学习
# 注意：不用裸「算法」（「用户增长算法」「雷达应用产品算法」并非 AI/大模型岗）
PROFESSIONAL_KEYWORDS: list[str] = [
    "ai", "人工智能", "agent", "智能体", "大模型", "llm",
    "深度学习", "机器学习", "aigc", "生成式",
]

# 行业类正文关键词：全部为具体复合词/强领域词（裸 环境/大气/生态 已排除，见模块 docstring）
INDUSTRY_BODY_KEYWORDS: list[str] = [
    "气象", "气候", "环保", "碳中和", "低碳",
    "水处理", "水环境", "微污染",
    "大气科学", "大气环境", "大气探测", "大气物理", "大气污染", "大气治理",
    "环境检测", "环境评价", "环境监测", "生态环境", "环境保护", "环境影响评价", "环评",
    "污染治理", "污染防治", "固废", "废水", "废气", "污水处理", "排污",
    "碳排放", "双碳", "数值预报", "天气预报", "水文",
]

# 专业类正文关键词（中文，子串匹配安全）
PROFESSIONAL_BODY_ZH: list[str] = [
    "人工智能", "智能体", "大模型", "深度学习", "机器学习",
    "自然语言处理", "生成式", "多模态", "强化学习", "神经网络", "计算机视觉",
]

# 专业类正文关键词（拉丁词，必须单词边界：裸子串会命中 email/detail/retail 里的 "ai"）
_PROFESSIONAL_BODY_LATIN_RE = re.compile(
    r"(?<![a-z])(?:ai|llm|agent|aigc|nlp|rag|gpt|mlops|transformer)(?![a-z])"
)


def _match_professional_body(body: str) -> bool:
    """正文专业信号：中文子串 + 拉丁词单词边界。"""
    if _PROFESSIONAL_BODY_LATIN_RE.search(body):
        return True
    return any(kw in body for kw in PROFESSIONAL_BODY_ZH)


def classify_jd_category(job: Job) -> str:
    """判定岗位属于「行业类」还是「专业类」。

    返回 "industry"（行业类）或 "professional"（专业类）。
    匹配优先级：行业类 > 专业类 > 兜底行业类（两类都不命中时归行业类）。
    标题与正文信号合并判定：任一来源命中行业即归行业（用户要求：都占 -> 行业）。
    """
    title = (job.title or "").lower()
    body = f"{job.responsibilities or ''} {job.requirements or ''}".lower()

    title_industry = bool(title) and any(kw in title for kw in INDUSTRY_KEYWORDS)
    body_industry = bool(body.strip()) and any(kw in body for kw in INDUSTRY_BODY_KEYWORDS)
    if title_industry or body_industry:
        return "industry"

    title_professional = bool(title) and any(kw in title for kw in PROFESSIONAL_KEYWORDS)
    body_professional = bool(body.strip()) and _match_professional_body(body)
    if title_professional or body_professional:
        return "professional"

    # 兜底：无法判定领域（如「算法工程师」「Python开发」）-> 行业类
    return "industry"
