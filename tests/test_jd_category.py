"""
jd_category 分类单元测试：行业类 / 专业类拆分逻辑（精细版：标题 + 正文合并判定）。
覆盖：纯行业、纯专业、两类都占（归行业）、都不占（兜底行业）、空标题、
     正文信号升级/翻转、正文降噪（办公环境/英文词 ai/互联网「生态」黑话）。
"""
from __future__ import annotations

from src.models import Job
from src.filters.jd_category import classify_jd_category


def _job(title: str, responsibilities: str = "", requirements: str = "") -> Job:
    """构造测试 Job（精细版分类看 标题+正文）。"""
    return Job(
        platform="test", job_id="x", url="", title=title, company="",
        responsibilities=responsibilities, requirements=requirements,
    )


class TestIndustryCategory:
    def test_atmosphere_keyword(self):
        assert classify_jd_category(_job("大气环保核心研发工程师")) == "industry"

    def test_meteorology_keyword(self):
        assert classify_jd_category(_job("高级嵌入式研发工程师--民航气象设备方向")) == "industry"

    def test_environment_keyword(self):
        assert classify_jd_category(_job("环境检测实验分析工程师")) == "industry"

    def test_water_treatment_keyword(self):
        assert classify_jd_category(_job("自来水处理工程师")) == "industry"

    def test_ecology_keyword(self):
        assert classify_jd_category(_job("生态修复工程师")) == "industry"


class TestProfessionalCategory:
    def test_ai_uppercase(self):
        assert classify_jd_category(_job("AI工程师")) == "professional"

    def test_ai_lowercase(self):
        assert classify_jd_category(_job("ai工程师")) == "professional"

    def test_agent_keyword(self):
        assert classify_jd_category(_job("智能体研发工程师")) == "professional"

    def test_llm_keyword(self):
        assert classify_jd_category(_job("中级AI工程师(LLM应用方向优先）")) == "professional"

    def test_machine_learning_keyword(self):
        assert classify_jd_category(_job("机器学习工程师")) == "professional"

    def test_office_environment_not_false_industry(self):
        # 「办公环境」里的「环境」不应把 AI 岗误判为行业类（回归用例）
        assert classify_jd_category(_job("AI工程师（外企英文办公环境）")) == "professional"


class TestPriorityAndFallback:
    def test_both_keywords_goes_industry(self):
        # 同时命中 AI（专业）+ 气象（行业）-> 归行业类（用户明确要求）
        assert classify_jd_category(_job("AI气象应用工程师")) == "industry"

    def test_neither_goes_industry_fallback(self):
        # 用户指定：算法工程师 / Python开发 / 解决方案经理 等归行业类
        assert classify_jd_category(_job("算法工程师")) == "industry"
        assert classify_jd_category(_job("Python开发")) == "industry"
        assert classify_jd_category(_job("解决方案经理")) == "industry"

    def test_bare_algorithm_not_professional(self):
        # 裸「算法」不算 AI/大模型岗 -> 行业类兜底
        assert classify_jd_category(_job("用户增长算法")) == "industry"
        assert classify_jd_category(_job("雷达应用产品算法工程师")) == "industry"

    def test_empty_title_goes_industry(self):
        assert classify_jd_category(_job("")) == "industry"


class TestBodyRefinedMatching:
    """精细版：正文（responsibilities+requirements）信号参与判定，且必须降噪。"""

    def test_body_professional_upgrades_fallback_title(self):
        # 标题看不出领域（兜底本归行业），正文有大模型信号 -> 专业类
        assert classify_jd_category(_job(
            "Python开发工程师",
            responsibilities="参与大模型训练平台开发与微调",
        )) == "professional"

    def test_body_latin_word_boundary_hit(self):
        # 正文拉丁词按单词边界命中
        assert classify_jd_category(_job(
            "后端工程师",
            requirements="熟悉 LLM 推理优化，有 RAG 项目经验",
        )) == "professional"

    def test_body_industry_overrides_title_professional(self):
        # 标题专业（AI）+ 正文行业（环境监测）-> 都占归行业（用户规则，正文来源也算）
        assert classify_jd_category(_job(
            "AI工程师",
            responsibilities="负责环境监测数据的智能分析平台建设",
        )) == "industry"

    def test_body_industry_compound_hit_on_unknown_title(self):
        # 标题看不出领域，正文行业复合词命中 -> 行业（与兜底同向，验证不误判专业）
        assert classify_jd_category(_job(
            "技术支持工程师",
            responsibilities="负责生态环境保护项目现场支持",
        )) == "industry"

    def test_body_dev_env_noise_ignored(self):
        # 正文「开发环境/办公环境」裸环境噪声 -> 不翻行业（回归：标题专业判定不受影响）
        assert classify_jd_category(_job(
            "AI工程师",
            responsibilities="熟悉Linux开发环境搭建，维护团队办公环境",
        )) == "professional"

    def test_body_ai_substring_in_english_ignored(self):
        # email/detail 里的 "ai" 不算专业命中 -> 兜底行业
        assert classify_jd_category(_job(
            "行政专员",
            responsibilities="负责email收发、合同detail核对",
        )) == "industry"

    def test_body_ai_tools_word_boundary_hits(self):
        # 「运用 AI 工具」是真 AI 提及（单词边界命中）-> 专业类
        assert classify_jd_category(_job(
            "产品经理",
            requirements="熟练运用 AI 工具进行需求分析",
        )) == "professional"

    def test_body_internet_ecology_jargon_ignored(self):
        # 「开源生态/AI生态」互联网黑话里的裸「生态」不算行业命中
        assert classify_jd_category(_job(
            "AI工程师",
            responsibilities="参与开源生态建设与大模型社区运营",
        )) == "professional"
