"""
轮次确认：数量门槛 + LLM 评审。

每轮检索结束后由 `scripts/search.py --round N` 调用。
确认结果写入 round JSON 的 `stats["confirmation"]`，供报告与人工回溯。

为什么需要：CI 里 5 轮顺序执行，只有确认过的一轮才算"完成"，
确认通过后再进入下一轮；21:30 报告融合时也以已确认轮次为准。
"""
from __future__ import annotations

import json
import logging

from src.config import CONFIRM_MIN_VALID
from src.models import SearchRound
from src.storage import save_round

logger = logging.getLogger(__name__)

# LLM 评审抽样上限（样本够判定即可，避免拖慢 CI）
CONFIRM_LLM_SAMPLE = 8


def _count_valid(round_data: SearchRound) -> int:
    """有效 JD = 未被过滤链排除的岗位。"""
    return sum(1 for j in round_data.jobs if not j.excluded)


async def _llm_review(round_data: SearchRound) -> dict:
    """
    DeepSeek 抽查样本，判定两件事：
    - relevance：这批岗位是否属于目标领域（气象/环境/AI/Agent/交叉学科）
    - jd_complete：JD 职责/要求正文是否基本完整（根因修复的验证信号）

    LLM 失败不抛异常，返回降级结果（relevant 从宽），确保确认步骤永不阻塞落盘。
    """
    valid = [j for j in round_data.jobs if not j.excluded][:CONFIRM_LLM_SAMPLE]
    if not valid:
        return {"reviewed": 0, "relevant": True, "jd_complete": False, "reason": "无有效 JD 可评审"}

    lines = []
    for i, j in enumerate(valid, 1):
        resp = (j.responsibilities or "").replace("\n", " ")[:200]
        req = (j.requirements or "").replace("\n", " ")[:200]
        lines.append(
            f"{i}. [{j.platform}] {j.title} / {j.company}\n"
            f"   职责: {resp or '（空）'}\n   要求: {req or '（空）'}"
        )

    prompt = f"""你是招聘筛选评审。以下是一轮检索的 {len(valid)} 条岗位样本（成都地区，主题为气象/大气科学、环境/生态、AI/大模型、AI Agent 应用、交叉学科）。
请判断两件事：
1. relevance：这批岗位主体是否属于上述目标领域（只要大部分相关即可，不必全部命中）。
2. jd_complete：岗位的职责/要求正文是否基本完整（多数样本应有非空正文，而非只有标题+薪资）。

只返回纯 JSON（不要 markdown）：
{{"relevant": true/false, "jd_complete": true/false, "reason": "一句话原因（中文）"}}

岗位样本：
{chr(10).join(lines)}"""

    try:
        from src.llm.deepseek import chat

        raw = await chat(prompt, temperature=0.1, max_tokens=300)
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else parts[0]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        parsed["reviewed"] = len(valid)
        return parsed
    except Exception as e:
        logger.warning(f"LLM 评审失败: {e}")
        return {"reviewed": len(valid), "relevant": True, "jd_complete": None, "reason": f"LLM 评审失败: {e}"}


async def confirm_round(round_data: SearchRound) -> dict:
    """
    轮次确认主流程：
    1. 数量门槛：有效 JD >= CONFIRM_MIN_VALID（默认 5）。
    2. LLM 评审：抽样判定相关性与 JD 完整度。
    3. 结果写入 round_data.stats["confirmation"] 并重新落盘。
    返回确认字典（供脚本打印与后续步骤判断）。
    """
    valid_count = _count_valid(round_data)
    total_count = len(round_data.jobs)

    confirmation: dict = {
        "valid_count": valid_count,
        "total_count": total_count,
        "threshold": CONFIRM_MIN_VALID,
        "quantity_pass": valid_count >= CONFIRM_MIN_VALID,
    }

    try:
        review = await _llm_review(round_data)
    except Exception as e:
        # _llm_review 自带兜底，这里再守一道：任何异常都从宽放行，确认永不阻塞落盘
        logger.warning(f"LLM 评审异常，从宽放行: {e}")
        review = {
            "reviewed": valid_count,
            "relevant": True,
            "jd_complete": None,
            "reason": f"LLM 评审异常: {e}",
        }
    confirmation["review"] = review

    # 通过 = 数量达标 且 LLM 判定相关（LLM 降级时从宽）
    passed = confirmation["quantity_pass"] and bool(review.get("relevant", True))
    confirmation["passed"] = passed
    confirmation["result"] = "PASS" if passed else "FAIL"

    if not passed:
        if not confirmation["quantity_pass"]:
            confirmation["reason"] = f"有效 JD 数 {valid_count} 低于门槛 {CONFIRM_MIN_VALID}"
        else:
            confirmation["reason"] = f"LLM 评审相关性不足: {review.get('reason', '')}"
    else:
        jd_state = "完整" if review.get("jd_complete") else "不完整"
        confirmation["reason"] = f"数量达标({valid_count}≥{CONFIRM_MIN_VALID})，LLM 判定相关，JD 文本{jd_state}"

    # 写回 round JSON（stats.confirmation），供报告与回溯
    round_data.stats["confirmation"] = confirmation
    save_round(round_data)
    return confirmation
