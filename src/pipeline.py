"""
主流程管线：搜索 → 过滤 → 分类 → 去重 → 存储
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from src.config import (
    CONFIRM_MIN_VALID,
    DETAIL_MAX_JOBS,
    GAODE_API_KEY,
    PLATFORM_BUDGET_AGENT,
    PLATFORM_BUDGET_HTML,
    SEARCH_DEADLINE_HOUR,
    SEARCH_DEADLINE_MINUTE,
    bjt_now,
    bjt_today,
)
from src.models import CompanyType, Job, SearchRound
from src.scrapers import AGENT_SCRAPERS, ALL_SCRAPERS
from src.scrapers.detail import enrich_job_details
from src.filters.salary import filter_salary
from src.filters.major import match_major
from src.filters.qualification import filter_qualification
from src.filters.industry import filter_industry
from src.filters.classifier import classify_company, classify_with_llm
from src.storage import save_round, load_all_rounds, deduplicate_all, save_deduped


# 五轮搜索关键词分配（顺序制 round-1~5，与执行时刻解耦）
# 用户确认：保留全部 6 个主题，把最薄弱的两个（AI/大模型、补充/长尾）合并为第 3 轮。
ROUND_KEYWORDS: dict[str, dict] = {
    "1": {
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
    "2": {
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
    "3": {
        "theme": "AI/大模型 + 补充/长尾",
        # 原第 14 轮（AI/大模型宽泛）+ 原第 17 轮（补充/长尾）合并
        "primary": [
            "成都 人工智能", "成都 AI工程师", "成都 算法工程师",
            "成都 大模型", "成都 LLM", "成都 语言模型",
            "成都 深度学习", "成都 机器学习", "成都 神经网络",
            "成都 自然语言处理", "成都 NLP", "成都 计算机视觉",
            "成都 数据科学家", "成都 数据分析师",
            "成都 研究岗", "成都 科研助理", "成都 博士后",
            "成都 数据工程师", "成都 大数据开发",
            "成都 技术顾问", "成都 解决方案", "成都 售前",
            "成都 产品经理AI", "成都 AI产品", "成都 技术产品",
        ],
        "expand": [
            "成都 AI", "成都 算法", "成都 模型",
            "成都 人工智能工程师", "成都 数据标注",
            "成都 技术支持", "成都 咨询顾问",
            "成都 项目经理", "成都 架构师",
        ],
        "borrow": [
            "成都 Python开发", "成都 研发工程师",
            "成都 气象", "成都 环境", "成都 AI", "成都 算法",
        ],
    },
    "4": {
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
    "5": {
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
}


def _past_deadline() -> bool:
    """当前 BJT 时刻是否已过截止时间（默认 21:30）。"""
    now = bjt_now()
    return (now.hour, now.minute) >= (SEARCH_DEADLINE_HOUR, SEARCH_DEADLINE_MINUTE)


# 平台并发上限：每轮 12 个平台各自启动无头浏览器，
# 全量并发会抢爆 CPU/内存（预导航超时、截图失败），限流后 agent 平台才能正常出数。
PLATFORM_CONCURRENCY = 3
_platform_semaphore = asyncio.Semaphore(PLATFORM_CONCURRENCY)


async def _run_platform(
    active_class: type,
    keyword: str,
    round_label: str,
) -> list[Job]:
    """打开平台爬虫并执行一次搜索。"""
    async with active_class() as scraper:
        return await scraper.search(keyword, round_label)


async def search_single_platform(
    scraper_class: type,
    keyword: str,
    round_label: str,
) -> tuple[list[Job], list[str]]:
    """在单个平台上搜索单个关键词。
    若该平台有 Agent 智能体适配器，则优先用 Agent 模拟人类操作，否则走 HTML 爬虫。
    信号量限制并发浏览器数；时间预算防止慢速 HTML 爬虫拖垮整轮。
    返回 (jobs, errors)；超时/异常不再静默吞掉，写进 errors 供轮次落盘。
    """
    active_class = AGENT_SCRAPERS.get(scraper_class.platform_name, scraper_class)
    is_agent = scraper_class.platform_name in AGENT_SCRAPERS
    budget = PLATFORM_BUDGET_AGENT if is_agent else PLATFORM_BUDGET_HTML

    async with _platform_semaphore:
        try:
            jobs = await asyncio.wait_for(
                _run_platform(active_class, keyword, round_label),
                timeout=budget,
            )
            return jobs, []
        except asyncio.TimeoutError:
            msg = f"[{active_class.platform_name}] 超过 {budget}s 预算，跳过"
            print(f"  {msg}")
            return [], [msg]
        except Exception as e:
            msg = f"[{active_class.platform_name}] 异常: {e}"
            print(f"  {msg}")
            return [], [msg]


async def search_all_platforms(
    keyword: str,
    round_label: str,
) -> tuple[list[Job], list[str]]:
    """在所有平台上搜索单个关键词（受限并发）。返回 (jobs, errors)。"""
    # 优先 agent 平台：先建任务先拿并发槽，避免被慢速/失败的 HTML 爬虫挤占
    platforms = sorted(
        ALL_SCRAPERS.items(),
        key=lambda item: item[0] not in AGENT_SCRAPERS,
    )
    tasks = [search_single_platform(cls, keyword, round_label) for _, cls in platforms]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_jobs: list[Job] = []
    errors: list[str] = []
    for (name, _), r in zip(platforms, results):
        if isinstance(r, tuple):
            jobs, errs = r
            all_jobs.extend(jobs)
            errors.extend(errs)
        elif isinstance(r, Exception):
            msg = f"[{name}] 错误: {r}"
            print(f"  {msg}")
            errors.append(msg)

    return all_jobs, errors


def _keep_chengdu(jobs: list[Job]) -> list[Job]:
    """数据层硬性兜底：只保留工作地点为成都的岗位（用户约束：地点必须只有成都）。

    agent 适配器内部已做城市过滤，这里兜住所有来源（含 HTML 爬虫），
    确保非成都岗位绝不可能进入报告。
    """
    return [j for j in jobs if "成都" in (j.location or "")]


async def run_search_round(round_label: str) -> SearchRound:
    """
    执行一轮搜索（顺序制轮次，round_label: "1" ~ "5"）。

    流程：主关键词（截止检查）→ 扩展 → 借用 → 城市过滤 → 详情页富集（JD 正文）
    → 过滤链 → 有效数不足则补充未搜过的关键词并重过滤 → 统计落盘。
    任何一步异常都不中断整轮：已有数据照常保存，错误写入 errors。
    """
    config = ROUND_KEYWORDS.get(round_label)
    if not config:
        print(f"未知轮次: {round_label}")
        return SearchRound(round_label=round_label)

    print(f"\n{'='*50}")
    print(f"第 {round_label} 轮搜索 — {config['theme']}")
    print(f"{'='*50}")

    if _past_deadline():
        print("已过截止时间，跳过本轮")
        return SearchRound(round_label=round_label, errors=["超过截止时间"])

    all_jobs: list[Job] = []
    searched_keywords: list[str] = []
    errors: list[str] = []

    async def _search_batch(keywords: list[str], tag: str, stop_at: int) -> None:
        """按关键词批量搜索，带截止检查与早停。"""
        for kw in keywords:
            if _past_deadline():
                print("已过截止时间，停止搜索，保存已有结果")
                return
            print(f"搜索({tag}): {kw}")
            batch, errs = await search_all_platforms(kw, round_label)
            all_jobs.extend(batch)
            searched_keywords.append(kw)
            errors.extend(errs)
            print(f"  获取 {len(batch)} 条")
            if len(all_jobs) >= stop_at:
                print(f"已超 {stop_at} 条，停止{tag}搜索")
                return

    try:
        # Phase 1: 主关键词
        await _search_batch(config["primary"], "主", 120)

        # Phase 2: 扩展关键词（原始量不足时）
        if len(all_jobs) < 30 and config.get("expand"):
            print(f"结果不足 ({len(all_jobs)}条)，启动扩展搜索...")
            await _search_batch(config["expand"], "扩展", 30)

        # Phase 3: 借用相邻主题
        if len(all_jobs) < 20 and config.get("borrow"):
            print(f"结果仍不足 ({len(all_jobs)}条)，借用相邻主题...")
            await _search_batch(config["borrow"], "借用", 20)

        raw_count = len(all_jobs)
        print(f"\n第{round_label}轮共获取 {raw_count} 条原始结果")

        # 应用过滤器
        print("应用过滤器...")

        # [0] 城市硬性过滤：只保留成都（用户硬约束）
        non_chengdu = [j for j in all_jobs if "成都" not in (j.location or "")]
        all_jobs = _keep_chengdu(all_jobs)
        if non_chengdu:
            print(f"  城市过滤: 剔除 {len(non_chengdu)} 条非成都岗位")

        # ★ 详情页富集（根因修复）：过滤前补齐 JD 正文，让 match_major 有文本可判
        await _enrich_step(all_jobs, errors)

        await _apply_filters(all_jobs)

        after_filter = len([j for j in all_jobs if not j.excluded])
        print(f"过滤后: {after_filter} 条 (排除 {len(all_jobs) - after_filter} 条)")

        # 数量门槛确认：有效数不足 → 补充未搜过的 expand/borrow 关键词并重过滤
        if after_filter < CONFIRM_MIN_VALID:
            supplement = [
                kw for kw in config.get("expand", []) + config.get("borrow", [])
                if kw not in searched_keywords
            ]
            if supplement:
                print(f"有效数不足 ({after_filter}<{CONFIRM_MIN_VALID})，补充搜索 {len(supplement)} 个关键词...")
                await _search_batch(supplement, "补充", 30)
                # 重置过滤标记后整体重过滤（各过滤器只置位不重置）
                for j in all_jobs:
                    j.excluded = False
                    j.exclude_reason = ""
                all_jobs = _keep_chengdu(all_jobs)
                await _enrich_step(all_jobs, errors)
                await _apply_filters(all_jobs)
                after_filter = len([j for j in all_jobs if not j.excluded])
                print(f"补充后过滤: {after_filter} 条")
            else:
                print(f"有效数不足 ({after_filter}<{CONFIRM_MIN_VALID}) 且无补充关键词可用")
    except Exception as e:
        # 不中断整轮：记录异常，把已收集数据照常保存
        errors.append(f"轮内异常: {e}")
        print(f"轮内异常: {e}")
        all_jobs = _keep_chengdu(all_jobs)
        after_filter = len([j for j in all_jobs if not j.excluded])

    stats = _compute_stats(round_label, all_jobs)
    round_data = SearchRound(
        round_label=round_label,
        keywords_used=searched_keywords,
        total_raw=len(all_jobs),
        total_after_filter=after_filter,
        jobs=all_jobs,
        errors=errors,
        stats=stats,
    )

    # 保存
    path = save_round(round_data)
    print(f"已保存: {path}")

    return round_data


async def _enrich_step(all_jobs: list[Job], errors: list[str]) -> None:
    """轮级详情页富集：为岗位补齐 JD 正文（根因修复）。"""
    if not all_jobs:
        return
    try:
        enriched, detail_errors = await enrich_job_details(all_jobs, max_jobs=DETAIL_MAX_JOBS)
        errors.extend(detail_errors)
        if enriched:
            print(f"  详情富集: {enriched} 条成功")
    except Exception as e:
        errors.append(f"详情富集失败: {e}")
        print(f"  详情富集失败: {e}")


async def _apply_filters(all_jobs: list[Job]) -> None:
    """按顺序应用过滤链（就地置 excluded 标记）。

    顺序说明（2026-08-05 修正）：classify_company 必须排在 filter_salary 之前——
    filter_salary 按企业类型分门槛（国企/央企/外资/合资 ≥1万，其他/未知 ≥1.6万），
    原顺序把分类放在最后，导致薪资过滤时 company_type 恒为 None、国企 1 万门槛
    分支成为死代码。先分类，薪资才能按企业类型真正分档（用户：最终保留的必须
    满足我定的薪资条件）。
    """
    # [1] 企业分类（规则）——先分类，薪资门槛才能按企业类型分档
    classify_company(all_jobs)
    # [2] 薪资过滤
    filter_salary(all_jobs)
    # [3] 岗位类型过滤
    filter_qualification(all_jobs)
    # [4] 专业过滤
    match_major(all_jobs)
    # [5] 行业排除
    filter_industry(all_jobs)


def _compute_stats(round_label: str, jobs: list[Job]) -> dict:
    """轮次统计：抓取覆盖率（JD 正文非空率）、每平台有效数、排除原因分布。

    用户原则（2026-08-05）：所有没写的信息保留——缺 JD 正文、缺专业要求的岗位都不排除。
    故不再统计"JD文本未抓取"排除数，改为统计"缺 JD 正文但保留"的有效数。
    """
    valid = [j for j in jobs if not j.excluded]
    not_matched = sum(1 for j in jobs if j.exclude_reason == "专业不匹配")
    jd_text_count = sum(1 for j in jobs if (j.responsibilities or j.requirements).strip())
    kept_without_jd_text = sum(1 for j in valid if not (j.responsibilities or j.requirements).strip())
    by_platform: dict[str, int] = {}
    for j in valid:
        by_platform[j.platform] = by_platform.get(j.platform, 0) + 1
    return {
        "round_label": round_label,
        "valid_count": len(valid),
        "excluded_count": len(jobs) - len(valid),
        "jd_text_coverage": jd_text_count,          # JD 正文非空的岗位数（覆盖率分子）
        "jd_text_missing": len(jobs) - jd_text_count,
        "kept_without_jd_text": kept_without_jd_text,  # 缺 JD 正文但仍保留（缺信息不排除）
        "not_matched_excluded": not_matched,          # 明确写了其他专业要求被排除
        "by_platform": dict(sorted(by_platform.items(), key=lambda x: -x[1])),
    }


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

    # 用户要求（2026-08-05）：JD 正文空白的有效岗位必须去详情页补抓，
    # 不能"保留但空着"——对最终保留集里的缺正文岗位再跑一次详情富集（不设 60 条上限）。
    # 抓回后重置排除标记并重跑过滤链，用真实 JD 文本做最终判定。
    missing_text_jobs = [j for j in valid_jobs if not (j.responsibilities or j.requirements).strip()]
    if missing_text_jobs:
        print(f"JD 正文空白: {len(missing_text_jobs)} 条，补抓详情页...")
        try:
            enriched, detail_errors = await enrich_job_details(
                missing_text_jobs, max_jobs=len(missing_text_jobs),
            )
            print(f"  详情补抓: {enriched} 条成功，{len(detail_errors)} 条失败/跳过")
            # 数据层修复（用户 2026-08-06）：只重置「成功富集」的岗位。
            # 富集失败（详情页 404/登录墙/落地页）的岗位已被 detail.py 置 excluded，
            # 绝不能在这里重置——否则"没有正确 JD 页面的岗位"会重新混入报告。
            for j in missing_text_jobs:
                if j.exclude_reason.startswith("无有效JD页面"):
                    continue
                j.excluded = False
                j.exclude_reason = ""
            await _apply_filters(missing_text_jobs)
            valid_jobs = [j for j in all_jobs if not j.excluded]
            print(f"  补抓重过滤后有效岗位: {len(valid_jobs)} 条")
        except Exception as e:
            print(f"  详情补抓失败: {e}")

    # 修复3：没有正确 JD 页面的岗位绝对不要（占位/幻觉/空 URL 一律剔除）。
    # 必须在补抓重过滤之后（补抓会从 all_jobs 重新取值，覆盖前面的过滤），
    # 保证最终进入报告与推送的每个岗位都能点进真实 JD 页。
    from src.report.generator import safe_url
    before_url_filter = len(valid_jobs)
    valid_jobs = [j for j in valid_jobs if safe_url(j.url)]
    dropped = before_url_filter - len(valid_jobs)
    if dropped:
        print(f"无有效JD页面过滤: 剔除 {dropped} 条，保留 {len(valid_jobs)} 条")

    # LLM 企业分类（规则未判定的）
    try:
        from src.llm.deepseek import create_client
        client = create_client()
        valid_jobs = await classify_with_llm(valid_jobs, client)
    except Exception as e:
        print(f"LLM 分类失败: {e}")

    # 离家距离补充（高德地理编码，按地点缓存；无 Key 自动跳过，不影响主流程）
    from src.geo.gaode import get_distance
    if GAODE_API_KEY:
        loc_cache: dict[str, tuple[float, float, float]] = {}
        for j in valid_jobs:
            if j.distance_km is not None or not j.location:
                continue
            loc = j.location
            if loc not in loc_cache:
                try:
                    res = await get_distance(loc)
                except Exception as e:
                    print(f"  距离计算失败 [{loc}]: {e}")
                    continue
                if not res:
                    continue
                loc_cache[loc] = res
            lng, lat, dist = loc_cache[loc]
            j.lng, j.lat, j.distance_km = lng, lat, dist
        print(f"离家距离: 地理编码 {len(loc_cache)} 个地点")
    else:
        print("高德 Key 未配置，跳过离家距离计算")

    # 保存去重结果（必须在距离补全之后，否则 deduped.json 的 distance_km 恒为 null）
    save_deduped(valid_jobs)

    # 生成 HTML 报告（行业类 + 专业类两份）
    from src.report.generator import generate_report
    report_paths = generate_report(valid_jobs)
    for cat, p in report_paths.items():
        print(f"HTML 报告[{cat}]: {p}")

    # 推送到微信（用 BJT 日期，凌晨跨 UTC 日不串天）
    from src.notify.serverchan import push_report
    await push_report(valid_jobs, bjt_today())

    # 统计摘要
    from collections import Counter
    type_counts = Counter((j.company_type or CompanyType.OTHER).value for j in valid_jobs)
    print("\n今日统计:")
    for ct, count in type_counts.most_common():
        print(f"  {ct}: {count}")
    print(f"  总计: {len(valid_jobs)}")

    return valid_jobs
