"""
企业类型分类器：
识别企业性质——国企、央企、外资、合资、其他。

分类策略：
1. 规则优先：公司名关键词匹配（高效且准确）
2. LLM辅助：规则无法判定时，用 DeepSeek 分析公司名和岗位描述
"""
from __future__ import annotations

from src.models import Job, CompanyType

# ---- 规则分类 ----

# 央企关键词（含完整公司名特征）
CENTRAL_KEYWORDS: list[str] = [
    "中国航天", "中国航空", "中国兵器", "中国船舶", "中国电科",
    "中国电子", "中国石油", "中国石化", "中国海油", "中国化工",
    "国家电网", "南方电网", "中国华能", "中国大唐", "中国华电",
    "国家电投", "国家能源", "中国移动", "中国电信", "中国联通",
    "中国铁塔", "中国卫星", "中国核工业", "中国中车", "中铁",
    "中国铁建", "中国交建", "中国建筑", "中建", "中国建材",
    "中国五矿", "中国铝业", "中国黄金", "中国稀土",
    "中国商飞", "中国航发", "中国一汽", "东风汽车",
    "中国节能", "中国环保", "中节能", "中环保",
    "中国气象", "中气象", "华云",  # 气象领域央企
    "中国三峡", "中国广核", "中广核",
    "中粮", "中国中化", "中国通用", "中国保利",
    "中国煤科", "中国钢研", "中国有色", "中国盐业",
    "有研科技", "矿冶科技", "中国建筑科学研究院",
    "中国信科", "中国普天", "中国通号",
]

# 国企关键词
STATE_OWNED_KEYWORDS: list[str] = [
    "集团", "国企", "国有", "市属国企", "省属国企",
    "成都环境", "成都兴蓉", "成都城投", "成都交投",
    "成都轨道", "成都公交", "兴城集团", "锦江集团",
    "四川发展", "四川能投", "四川旅投", "川投",
    "四川航空", "川航",
    "蜀道集团", "四川路桥",
    "高新投资", "天府投资", "成都高投",
    "四川气象", "成都气象", "省气象局", "市气象局",
    "省环保", "市环保", "省环科院", "市环科院",
    # 事业单位（近似国企待遇）
    "气象局", "环保局", "生态环境局", "水文局",
]

# 外资关键词
FOREIGN_KEYWORDS: list[str] = [
    # 英文名特征
    "（中国）投资有限公司", "(中国)投资有限公司",
    "（中国）有限公司", "(中国)有限公司",
    "外资", "外商独资", "外商",
    # 常见外资
    "西门子", "施耐德", "ABB", "通用电气", "霍尼韦尔",
    "IBM", "微软", "Amazon", "亚马逊", "Google", "谷歌",
    "Apple", "苹果", "英特尔", "Intel", "英伟达", "NVIDIA",
    "SAP", "Oracle", "甲骨文", "埃森哲", "德勤", "普华永道",
    "毕马威", "安永", "麦肯锡", "波士顿咨询", "贝恩",
    "巴斯夫", "拜耳", "壳牌", "BP", "道达尔",
    "苏伊士", "威立雅", "博世", "大陆集团", "采埃孚",
    "默沙东", "辉瑞", "诺华", "罗氏", "阿斯利康",
    "索尼", "三星", "LG", "松下", "丰田", "本田", "日产",
    # 气象环境领域外企
    "Vaisala", "维萨拉", "Campbell", "坎贝尔",
    "Thermo Fisher", "赛默飞", "Agilent", "安捷伦",
    "岛津", "PerkinElmer",
]

# 合资关键词
JOINT_VENTURE_KEYWORDS: list[str] = [
    "合资", "中外合资", "中日合资", "中德合资", "中美合资",
    "中法合资", "中英合资", "中韩合资",
    "股份有限公司（合资", "有限公司（合资",
]


def classify_by_rules(company: str, text: str = "") -> CompanyType | None:
    """
    纯规则分类。
    返回 CompanyType 或 None（规则无法判定时）。
    """
    combined = f"{company} {text}"

    # 1. 央企特征最明显，先匹配
    for kw in CENTRAL_KEYWORDS:
        if kw in combined:
            return CompanyType.CENTRAL

    # 2. 外资
    for kw in FOREIGN_KEYWORDS:
        if kw in combined:
            return CompanyType.FOREIGN

    # 3. 合资
    for kw in JOINT_VENTURE_KEYWORDS:
        if kw in combined:
            return CompanyType.JOINT_VENTURE

    # 4. 国企
    for kw in STATE_OWNED_KEYWORDS:
        if kw in combined:
            return CompanyType.STATE_OWNED

    return None


async def classify_with_llm(
    jobs: list[Job],
    llm_client,
) -> list[Job]:
    """
    用 LLM 批量分类规则无法判定的企业。
    传入规则无法分类的 jobs，LLM 根据公司名和行业信息判断企业类型。
    """
    from src.config import DEEPSEEK_MODEL

    # 批量处理：一次 API 调用处理多条
    uncertain = [j for j in jobs if j.company_type is None and not j.excluded]
    if not uncertain:
        return jobs

    companies = "\n".join([
        f"{i+1}. 公司: {j.company}, 岗位: {j.title}, 描述: {j.responsibilities[:100]}"
        for i, j in enumerate(uncertain)
    ])

    prompt = f"""判断以下成都地区企业的类型，只返回编号和类型（国企/央企/外资/合资/其他）：
注意：
- 国企：省属/市属国有企业、事业单位
- 央企：国务院国资委直接管理、属于中央企业名录
- 外资：外商独资
- 合资：中外合资、港澳台合资
- 其他：民营企业、创业公司、非上述四类

企业列表：
{companies}

请用 JSON 格式返回：[{{"index": 1, "type": "国企"}}, ...]"""

    try:
        response = await llm_client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500,
        )
        import json
        raw = response.choices[0].message.content.strip()
        # 提取 JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)

        type_map = {
            "国企": CompanyType.STATE_OWNED,
            "央企": CompanyType.CENTRAL,
            "外资": CompanyType.FOREIGN,
            "合资": CompanyType.JOINT_VENTURE,
            "其他": CompanyType.OTHER,
        }
        for item in result:
            idx = int(item["index"]) - 1
            if idx < len(uncertain):
                uncertain[idx].company_type = type_map.get(item["type"], CompanyType.OTHER)

    except Exception:
        # LLM 失败时全部标记为"其他"
        for j in uncertain:
            j.company_type = CompanyType.OTHER

    return jobs


def classify_company(jobs: list[Job]) -> list[Job]:
    """
    企业分类主函数（同步，仅规则匹配）。
    调用 classify_with_llm() 需要异步上下文。
    """
    for job in jobs:
        ct = classify_by_rules(job.company, f"{job.title} {job.responsibilities}")
        job.company_type = ct
    return jobs
