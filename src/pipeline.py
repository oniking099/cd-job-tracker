"""
主流程管线：搜索 → 过滤 → 分类 → 去重 → 存储
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR
from src.models import CompanyType, Job, SearchRound
from src.scrapers import ALL_SCRAPERS
from src.filters.salary import filter_salary
from src.filters.major import match_major
from src.filters.qualification import filter_qualification
from src.filters.industry import filter_industry
from src.filters.classifier import classify_company, classify_with_llm
from src.storage import save_round, load_all_rounds, deduplicate_all, save_deduped


# 六轮搜索关键词分配
ROUND_KEYWORDS: dict[str, dict] = {
    "12": {
        "theme": "气象/大气科学",
        "primary": [
            "成都 气象工程师", "成都 大气科学", "成都 数值预报",
            "成都 气象局", "成都 气候", "成都 气象数据处理",
            "成都 大气物理", "成都 遥感", "成都 卫星气象",
            "成都 天气预报", "成都 大气环境", "成都 大气探测",
        ],
        "expand": [
            "成都 气象", "成都 大气", "成都 气候中心",
            "成都 气象服务", "成都 天文",
        ],
        "borrow": [
            "成都 环境", "成都 地理信息",
        ],
    },
    "13": {
        "theme": "环境/生态",
        "primary": [
            "成都 环境工程师", "成都 环境科学", "成都 环保工程师",
            "成都 生态环境", "成都 环境监测", "成都 环境咨询",
            "成都 碳中和", "成都 碳达峰", "成都 ESG",
            "成都 环境影响评价", "成都 环境规划", "成都 环境管理",
            "成都 水环境", "成都 大气治理", "成都 固废",
            "成都 环境数据分析", "成都 环境建模",
        ],
        "expand": [
            "成都 环保", "成都 生态", "成都 绿色",
            "成都 节能减排", "成都 清洁生产",
        ],
        "borrow": [
            "成都 气象", "成都 遥感", "成都 GIS",
        ],
    },
    "14": {
        "theme": "AI/大模型（宽泛）",
        "primary": [
            "成都 人工智能", "成都 AI工程师", "成都 算法工程师",
            "成都 大模型", "成都 LLM", "成都 语言模型",
            "成都 深度学习", "成都 机器学习", "成都 神经网络",
            "成都 自然语言处理", "成都 NLP", "成都 计算机视觉",
            "成都 数据科学家", "成都 数据分析师",
        ],
        "expand": [
            "成都 AI", "成都 算法", "成都 模型",
            "成都 人工智能工程师", "成都 数据标注",
        ],
        "borrow": [
            "成都 Python开发", "成都 研发工程师",
        ],
    },
    "15": {
        "theme": "AI Agent/应用",
        "primary": [
            "成都 AI Agent", "成都 智能体", "成都 对话系统",
            "成都 AIGC", "成都 生成式AI", "成都 prompt工程师",
            "成都 RAG", "成都 向量数据库", "成都 知识图谱",
            "成都 AI应用", "成都 智能助手", "成都 自动化",
            "成都 多模态", "成都 强化学习", "成都 联邦学习",
            "成都 模型训练", "成都 模型部署", "成都 MLOps",
        ],
        "expand": [
            "成都 人工智能应用", "成都 对话机器人",
            "成都 大模型应用", "成都 AI工具",
        ],
        "borrow": [
            "成都 算法", "成都 后端开发",
        ],
    },
    "16": {
        "theme": "交叉学科",
        "primary": [
            "成都 智慧气象", "成都 气象AI", "成都 环境AI",
            "成都 遥感AI", "成都 气象大模型", "成都 环境大模型",
            "成都 地理信息", "成都 GIS", "成都 空间数据",
            "成都 智慧环保", "成都 数字孪生", "成都 IoT环境",
            "成都 Python开发", "成都 技术岗",
        ],
        "expand": [
            "成都 数据分析", "成都 数据处理",
            "成都 软件开发", "成都 研发",
        ],
        "borrow": [
            "成都 气象", "成都 环境", "成都 AI",
        ],
    },
    "17": {
        "theme": "补充/长尾",
        "primary": [
            "成都 研究岗", "成都 科研助理", "成都 博士后",
            "成都 数据工程师", "成都 大数据开发",
            "成都 技术顾问", "成都 解决方案", "成都 售前",
            "成都 产品经理AI", "成都 AI产品", "成都 技术产品",
        ],
        "expand": [
            "成都 技术支持", "成都 咨询顾问",
            "成都 项目经理", "成都 架构师",
        ],
        "borrow": [
            "成都 气象", "成都 环境", "成都 AI", "成都 算法",
        ],
    },
}


async def search_single_platform(
    scraper_class: type,
    keyword: str,
    round_label: str,
) -> list[Job]:
    """在单个平台上搜索单个关键词"""
    try:
        async with scraper_class() as scraper:
            return await scraper.search(keyword, round_label)
    except Exception as e:
        print(f"  [{scraper_class.platform_name}] 异常: {e}")
        return []


async def search_all_platforms(
    keyword: str,
    round_label: str,
) -> list[Job]:
    """在所有平台上搜索单个关键词（并发）"""
    tasks = []
    for name, scraper_class in ALL_SCRAPERS.items():
        tasks.append(search_single_platform(scraper_class, keyword, round_label))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_jobs: list[Job] = []
    for i, r in enumerate(results):
        if isinstance(r, list):
            all_jobs.extend(r)
        elif isinstance(r, Exception):
            print(f"  [{list(ALL_SCRAPERS.keys())[i]}] 错误: {r}")

    return all_jobs


async def run_search_round(round_label: str) -> SearchRound:
    """
    执行一轮搜索。
    round_label: "12" ~ "17"
    """
    config = ROUND_KEYWORDS.get(round_label)
    if not config:
        print(f"未知轮次: {round_label}")
        return SearchRound(round_label=round_label)

    print(f"\n{'='*50}")
    print(f"第 {round_label} 轮搜索 — {config['theme']}")
    print(f"{'='*50}")

    all_jobs: list[Job] = []
    searched_keywords: list[str] = []
    errors: list[str] = []

    # Phase 1: 主关键词
    for kw in config["primary"]:
        print(f"搜索: {kw}")
        batch = await search_all_platforms(kw, round_label)
        all_jobs.extend(batch)
        searched_keywords.append(kw)
        print(f"  获取 {len(batch)} 条")

        if len(all_jobs) >= 120:  # 早停
            print(f"已超 120 条，停止主搜索")
            break

    # Phase 2: 扩展关键词
    if len(all_jobs) < 30 and config.get("expand"):
        print(f"结果不足 ({len(all_jobs)}条)，启动扩展搜索...")
        for kw in config["expand"]:
            print(f"搜索(扩展): {kw}")
            batch = await search_all_platforms(kw, round_label)
            all_jobs.extend(batch)
            searched_keywords.append(kw)
            if len(all_jobs) >= 30:
                break

    # Phase 3: 借用相邻主题
    if len(all_jobs) < 20 and config.get("borrow"):
        print(f"结果仍不足 ({len(all_jobs)}条)，借用相邻主题...")
        for kw in config["borrow"]:
            print(f"搜索(借用): {kw}")
            batch = await search_all_platforms(kw, round_label)
            all_jobs.extend(batch)
            searched_keywords.append(kw)
            if len(all_jobs) >= 20:
                break

    print(f"\n第{round_label}轮共获取 {len(all_jobs)} 条原始结果")

    # 应用过滤器
    print("应用过滤器...")
    raw_count = len(all_jobs)

    # [1] 薪资过滤
    all_jobs = filter_salary(all_jobs)
    # [2] 岗位类型过滤
    all_jobs = filter_qualification(all_jobs)
    # [3] 专业过滤
    all_jobs = match_major(all_jobs)
    # [4] 行业排除
    all_jobs = filter_industry(all_jobs)
    # [5] 企业分类（规则）
    all_jobs = classify_company(all_jobs)

    after_filter = len([j for j in all_jobs if not j.excluded])
    excluded_count = len(all_jobs) - after_filter
    print(f"过滤后: {after_filter} 条 (排除 {excluded_count} 条)")

    round_data = SearchRound(
        round_label=round_label,
        keywords_used=searched_keywords,
        total_raw=raw_count,
        total_after_filter=after_filter,
        jobs=all_jobs,
        errors=errors,
    )

    # 保存
    path = save_round(round_data)
    print(f"已保存: {path}")

    return round_data


async def run_report():
    """生成最终报告并推送"""
    print("\n" + "=" * 50)
    print("生成每日报告")
    print("=" * 50)

    # 加载所有轮次
    rounds = load_all_rounds()
    if not rounds:
        print("今天没有搜索数据")
        return

    # 汇总所有岗位
    all_jobs: list[Job] = []
    for r in rounds:
        all_jobs.extend(r.jobs)

    print(f"汇总: {len(all_jobs)} 条（含已排除）")

    # 全局去重
    all_jobs = deduplicate_all(all_jobs)
    print(f"去重后: {len(all_jobs)} 条")

    # 过滤排除项
    valid_jobs = [j for j in all_jobs if not j.excluded]
    print(f"有效岗位: {len(valid_jobs)} 条")

    # LLM 企业分类（规则未判定的）
    try:
        from src.llm.deepseek import create_client
        client = create_client()
        valid_jobs = await classify_with_llm(valid_jobs, client)
    except Exception as e:
        print(f"LLM 分类失败: {e}")

    # 保存去重结果
    save_deduped(valid_jobs)

    # 生成 HTML 报告
    from src.report.generator import generate_report
    html_path = generate_report(valid_jobs)
    print(f"HTML 报告: {html_path}")

    # 推送到微信
    from src.notify.serverchan import push_report
    from datetime import date
    await push_report(valid_jobs, date.today().isoformat())

    # 统计摘要
    from collections import Counter
    type_counts = Counter((j.company_type or CompanyType.OTHER).value for j in valid_jobs)
    print("\n今日统计:")
    for ct, count in type_counts.most_common():
        print(f"  {ct}: {count}")
    print(f"  总计: {len(valid_jobs)}")

    return valid_jobs
