"""
LLM Agent 驱动爬虫基类 + 试点平台适配器。

继承 BaseScraper 复用 stealth 浏览器启动逻辑，用 AgentLoop 在页面上模拟人类操作：
打开搜索页 → 智能体输入关键词 → 点搜索 → 滚到岗位列表 → 多截图视觉提取。
子类只需定义 start_url 与任务话术，search() 流程统一。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from playwright.async_api import async_playwright

from src.agent.agent import AgentLoop
from src.agent.extract import extract_jobs_from_page
from src.config import AGENT_TIMEOUT, DATA_DIR, SESSIONS_DIR
from src.models import Job
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class AgentScraperBase(BaseScraper):
    """Agent 驱动的平台爬虫基类。"""

    # 子类配置
    start_url: str = ""       # 起始页（搜索页或首页）
    task_template: str = (
        "在 {platform} 搜索岗位「{keyword}」。"
        "找到搜索框输入关键词，点击搜索，等待结果加载，滚动查看岗位列表。"
        "看到岗位列表后执行 extract。"
    )

    # 目标城市：提取结果只保留该城市的岗位
    target_city: str = "成都"

    def build_start_url(self, keyword: str) -> str:
        """构造起始页 URL。默认用固定 start_url；子类可覆写为带关键词/城市筛选参数的搜索页。"""
        return self.start_url

    async def _prewarm(self, page, start_url: str) -> None:
        """预导航起始页，再启动 agent 循环。

        部分平台首次访问有 WAF 挑战（51Job 资源全断连，刷新后 cookie 建立才正常），
        或 SPA 需要时间渲染。先导航到位并确认页面有内容，agent 再接管（不再重复 goto）。
        """
        try:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            body_len = await page.evaluate("() => document.body ? document.body.innerText.length : 0")
            if body_len < 50:
                # 首次被 WAF 挑战/空白 → 刷新一次建立 cookie
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(3500)
        except Exception as e:
            logger.warning(f"[{self.platform_name}] 预导航失败: {e}")

    async def search(self, keyword: str, round_label: str = "") -> list[Job]:
        self.context = await self._new_context()
        page = await self.context.new_page()

        task = self.task_template.format(
            platform=self.platform_name,
            keyword=keyword,
            city=self.target_city,
        )
        trace_dir = DATA_DIR / "agent-traces" / self.platform_name / self._safe_name(keyword)

        start_url = self.build_start_url(keyword)
        await self._prewarm(page, start_url)

        loop = AgentLoop(page, task=task, trace_dir=trace_dir)

        # 操作循环有整体超时，防止某平台无限拖慢整轮
        try:
            result = await asyncio.wait_for(loop.run(start_url=""), timeout=AGENT_TIMEOUT)
        except asyncio.TimeoutError:
            result = None
            print(f"  [{self.platform_name}] agent 超时(>{AGENT_TIMEOUT}s)，跳过")
        except Exception as e:
            result = None
            print(f"  [{self.platform_name}] agent 异常: {e}")

        if result is not None:
            self._print_trace(result)

        # 尽力提取：无论循环成功/超时/异常，只要页面有内容就尝试提取
        jobs: list[Job] = []
        try:
            jobs = await extract_jobs_from_page(page, self.platform_name, round_label)
        except Exception as e:
            logger.warning(f"[{self.platform_name}] 提取失败: {e}")

        # 数据层兜底：只保留目标城市的岗位（agent 可能没选对城市筛选）
        raw_count = len(jobs)
        jobs = self._filter_city(jobs)
        if raw_count and len(jobs) != raw_count:
            print(f"  [{self.platform_name}] 城市过滤: {raw_count} -> {len(jobs)}（仅保留{self.target_city}）")

        await page.close()
        await self.context.close()
        return jobs

    def _filter_city(self, jobs: list[Job]) -> list[Job]:
        """只保留工作地点包含目标城市的岗位。"""
        return [j for j in jobs if self.target_city in (j.location or "")]

    def _print_trace(self, result):
        print(f"  [{self.platform_name}] agent 结束: {result.reason}（{len(result.steps)} 步）")
        for s in result.steps:
            ok = "OK" if s.result_ok else "X"
            target = s.action.target or s.action.target_id or s.action.coord or ""
            print(f"      step{s.step:>2} [{ok}] {s.action.action} {target} - {s.result_message}")

    @staticmethod
    def _safe_name(keyword: str) -> str:
        """关键词转安全目录名。"""
        return "".join(c for c in keyword if c not in '\\/:*?"<>|').strip() or "kw"


class CamoufoxAgentScraperBase(AgentScraperBase):
    """Camoufox persistent context 基类：检测 CDP 自动化的登录墙平台共用。

    反检测：BOSS/鱼泡 等平台检测 CDP 自动化连接通道 + WebDriver 指纹，普通
    Chromium（含 playwright-stealth、系统真实 Chrome）会被识别跳 about:blank。
    Camoufox 是 Firefox 源码级 fork，在 C++ 引擎层伪造指纹，headless/有头均能过。
    不可叠加 apply_stealth（JS 层注入会破坏一致性指纹）。

    登录态：capture_session.py 手动扫码登录一次后持久化在
    .sessions/profiles/{platform_key}/，本类每次启动加载该 persistent context。
    为什么用 persistent 而非 storage_state：非持久模式每次生成不同指纹，短时间
    多次访问指纹不一致会触发平台风控；persistent context 指纹固定在启动最稳定。
    """

    # 子类指定平台 key（对应 .sessions/profiles/{key} 与 capture_session --platform）
    platform_key: str = ""
    # 环境变量名：保存登录 Cookie（JSON 数组，Playwright add_cookies 格式）。
    # CI 上没有 .sessions 登录态，凭此 GitHub Secret 也能免登录访问；
    # 未配置时详情页遇登录墙降级为列表摘要，不影响整轮。
    cookie_env: str = ""

    @property
    def camoufox_user_dir(self) -> str:
        return str(SESSIONS_DIR / "profiles" / self.platform_key)

    async def __aenter__(self):
        from camoufox.async_api import AsyncCamoufox

        self._camoufox = AsyncCamoufox(
            persistent_context=True,
            headless=True,
            os="windows",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_data_dir=self.camoufox_user_dir,
        )
        self.context = await self._camoufox.__aenter__()
        await self._inject_cookie_if_configured()
        return self

    async def _inject_cookie_if_configured(self) -> None:
        """把 GitHub Secret 里的登录 Cookie 注入 Camoufox persistent context。

        Cookie 格式：Playwright add_cookies 的 JSON 数组，例如
        [{"name":"zpw_index","value":"...","domain":".zhipin.com","path":"/"}]
        """
        if not self.cookie_env:
            return
        raw = os.environ.get(self.cookie_env, "").strip()
        if not raw:
            return
        try:
            cookies = json.loads(raw)
            if not isinstance(cookies, list) or not cookies:
                logger.warning(f"[{self.platform_name}] {self.cookie_env} 为空 JSON，跳过注入")
                return
            await self.context.add_cookies(cookies)
            logger.info(f"[{self.platform_name}] 已注入 {len(cookies)} 条 {self.cookie_env} 登录 Cookie")
        except Exception as e:
            logger.warning(f"[{self.platform_name}] {self.cookie_env} 注入失败: {e}")

    async def __aexit__(self, *args):
        if self._camoufox:
            try:
                # AsyncCamoufox 会关闭其持有的 persistent context（browser.close）
                await self._camoufox.__aexit__(*args)
            finally:
                self._camoufox = None
        self.context = None
        self.browser = None

    async def _new_context(self):
        """返回 __aenter__ 已创建的 persistent context（含登录态）。

        search() 末尾会 close 一次；__aexit__ 中 AsyncCamoufox 再 close 幂等。
        """
        return self.context

    def _storage_state_path(self) -> str:
        # 登录态走 Camoufox persistent context，不使用 storage_state JSON
        return ""


# ---- 试点平台适配器 ----

class ZhilianAgentScraper(AgentScraperBase):
    """智联招聘：直接进入已按成都筛选的搜索页（jl=城市代码），不再操作地点筛选控件。

    智联的地点筛选控件交互不可靠（需弹面板选城市，且易被登录弹窗遮罩拦截），
    实测 jl=801（成都城市代码）URL 参数可一步到位返回成都岗位。
    """
    platform_name = "智联招聘"
    start_url = "https://sou.zhaopin.com/"
    ZHAOPIN_CITY_CODE = "801"  # 成都

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        return (
            f"https://sou.zhaopin.com/?jl={self.ZHAOPIN_CITY_CODE}"
            f"&kw={quote(keyword)}"
        )

    task_template = (
        "在智联招聘的成都岗位搜索结果页查看「{keyword}」岗位。"
        "页面标题应包含{city}（已按{city}筛选）。"
        "如果页面出现登录/验证码弹窗，忽略它，不要点击。"
        "滚动查看岗位卡片列表，看到{city}的岗位列表后执行 extract。"
    )


class Job51AgentScraper(AgentScraperBase):
    """51Job：直接进入已按成都筛选的搜索页（jobArea=成都代码 090200）。

    51Job 首次访问有 WAF 挑战（CDN 资源断连、SPA 空白），刷新后 cookie 建立才正常，
    因此覆写 _prewarm 已由基类统一处理（body 空则 reload）。不操作城市筛选控件。
    """
    platform_name = "51Job"
    start_url = "https://we.51job.com/pc/search"
    JOBAREA_CHENGDU = "090200"

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        return (
            f"https://we.51job.com/pc/search?keyword={quote(keyword)}"
            f"&searchType=2&jobArea={self.JOBAREA_CHENGDU}&sortType=0"
        )

    task_template = (
        "在51Job的{city}岗位搜索结果页查看「{keyword}」岗位。"
        "页面标题应包含{city}（已按{city}筛选）。"
        "滚动查看岗位卡片列表（51Job 卡片含 岗位名/薪资/地点/公司），"
        "看到{city}的岗位列表后执行 extract。"
    )


class BossAgentScraper(CamoufoxAgentScraperBase):
    """BOSS直聘：直接进入已按成都筛选的搜索页（city=成都代码 101270100）。

    登录墙处理：BOSS 未登录仅返回约 15 条岗位，且会弹登录框。本类用 Camoufox
    persistent context 加载登录态（scripts/capture_session.py 手动扫码登录一次后
    持久化在 .sessions/profiles/boss/），实现免登录访问，解除 15 条限制。
    登录态过期后重跑 capture 脚本刷新。浏览器生命周期见 CamoufoxAgentScraperBase。
    """
    platform_name = "BOSS直聘"
    start_url = "https://www.zhipin.com/web/geek/job"
    platform_key = "boss"
    cookie_env = "BOSS_COOKIE"  # GitHub Secret：BOSS 登录 Cookie（JSON 数组）
    BOSS_CITY_CHENGDU = "101270100"

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        # BOSS 的 query 只接受岗位关键词，城市已由 city=101270100 过滤。
        # 若关键词带"成都"前缀（本项目统一格式"成都 气象"），BOSS 会把整串
        # 当搜索词 → 返回"没有找到相关职位"。实测 query=气象 正常出结果。
        q = keyword.strip()
        if q.startswith("成都"):
            q = q[2:].strip()
        if not q:
            q = keyword.strip()  # 剥完为空（关键词就是"成都"）则回退原词
        return (
            f"https://www.zhipin.com/web/geek/job?query={quote(q)}"
            f"&city={self.BOSS_CITY_CHENGDU}"
        )

    task_template = (
        "BOSS直聘的{city}岗位搜索结果页已打开并显示「{keyword}」岗位（搜索框已填好关键词，"
        "页面已按{city}自动筛选加载，无需重新搜索）。"
        "⚠️ 绝对不要点击页面顶部的搜索输入框/搜索按钮——那会清空已加载的结果导致页面显示"
        "'没有找到相关职位'。"
        "如果页面出现登录、验证码、扫码等要求且没有岗位内容，直接输出 done（reason 写'登录墙'）。"
        "否则直接滚动查看{city}的岗位卡片列表（卡片含 岗位名/薪资/地点/公司），看到后执行 extract。"
    )


# ---- 转换平台适配器（HTML 爬虫失效，改用 agent 模拟人类操作） ----

class LiepinAgentScraper(AgentScraperBase):
    """猎聘：搜索 URL 带 city=280020（成都），agent 在结果页滚动提取。

    注意：猎聘城市代码 city=040 是重庆（曾误用），成都实测代码为 280020
    （点击页面城市筛选"成都"后 URL 自动生成 ?city=280020&dq=280020）。
    即使带成都参数，页面仍会混入少量推荐位岗位（其他城市），
    由数据层 _filter_city 兜底过滤，只保留成都岗位。
    """
    platform_name = "猎聘"
    start_url = "https://www.liepin.com/zhaopin/?city=280020&dq=280020"

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        return (
            f"https://www.liepin.com/zhaopin/?city=280020&dq=280020"
            f"&key={quote(keyword)}&currentPage=0"
        )

    task_template = (
        "在猎聘的{city}岗位搜索结果页查看「{keyword}」岗位。"
        "页面标题应包含{city}（已按{city}筛选）。"
        "如果页面出现登录/验证码弹窗，忽略它，不要点击。"
        "滚动查看岗位卡片列表（卡片含 岗位名/薪资/地点/公司），看到{city}的岗位列表后执行 extract。"
    )


class WubaAgentScraper(AgentScraperBase):
    """58同城：改走 wap 频道页 m.58.com/cd/job/（无登录墙，2026-08-09 实测）。

    ⚠️ 桌面版 cd.58.com/job/ 对自动化直接 302 到 passport 登录墙（2026-08-09 实测
    无法访问）；wap 频道页可直开且卡片数据完整（标题/薪资/公司/地点，30 卡 0 缺失）。
    wap 无关键词搜索入口（key 参数不生效，搜索框提交只重载通用列表）——
    故 keyword 不再进 URL，agent 直接从成都全职列表页滚动提取，关键词语义由
    下游 _filter_industry/qualification 等做。地点为"区-商圈"无城市前缀，
    提取器已统一补 "成都" 前缀，保证 _filter_city 不误剔。
    """
    platform_name = "58同城"
    start_url = "https://m.58.com/cd/job/"

    def build_start_url(self, keyword: str) -> str:
        # wap 频道页不支持关键词过滤，固定进成都全职列表即可
        return "https://m.58.com/cd/job/"

    task_template = (
        "在58同城成都 wap 版（m.58.com/cd/job/）的岗位列表页查看岗位。"
        "页面是成都全职招聘列表，无需登录。"
        "不要点击页面顶部的搜索框/搜索按钮（wap 版没有关键词过滤，提交只会重载通用列表）。"
        "不要点击任何登录/APP 下载/弹窗，忽略它们。"
        "滚动查看岗位卡片列表（卡片含 岗位名/薪资/地点/公司），"
        "看到岗位列表后执行 extract。"
    )


class ChinahrAgentScraper(AgentScraperBase):
    """中华英才网：搜索 URL 用 /job?value={kw}（旧 /search/job 已失效跳转首页）。

    注意：中华英才已改版为"新华英才-大学生就业云平台"，气象岗位多为高校/气象局
    校招岗（硕士博士应届、标注"全国"），城市筛选点击后岗位仍标全国，
    提取后由数据层 _filter_city 兜底，仅保留明确标注成都的岗位。
    """
    platform_name = "中华英才网"
    start_url = "https://www.chinahr.com/job"

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        return f"https://www.chinahr.com/job?value={quote(keyword)}"

    task_template = (
        "在中华英才网（新华英才）的岗位搜索结果页查看「{keyword}」岗位。"
        "页面顶部筛选区有 城市/薪资/学历 等控件，如果城市筛选中能选{city}就选择它。"
        "如果页面出现登录/验证码弹窗，忽略它，不要点击。"
        "滚动查看岗位卡片列表（卡片含 岗位名/薪资/地点/公司），看到{city}的岗位列表后执行 extract。"
    )


class YupaoAgentScraper(CamoufoxAgentScraperBase):
    """鱼泡直聘（成都站）：Camoufox 加载登录态，成都站为拼音路径。

    登录墙处理：鱼泡搜索页未登录仅显示少量岗位（"登录账号，查看更多好职位"），
    用 Camoufox persistent context 加载登录态（capture_session 扫码登录一次）。
    Chromium 访问鱼泡会被检测跳 about:blank（同 BOSS 的 CDP 检测），必须 Camoufox。

    注意：鱼泡有频率风控，短时多次访问会触发 /safe/verify/ 人机验证，headless
    无法过交互验证，依赖登录态 cookie + 低频访问降低触发概率。
    """
    platform_name = "鱼泡直聘"
    start_url = "https://www.yupao.com/chengdu/"
    platform_key = "yupao"
    cookie_env = "YUPAO_COOKIE"  # GitHub Secret：鱼泡登录 Cookie（JSON 数组）

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        # 成都站搜索 URL：topic/a322c0/?keywords={kw}（a322=成都城市码，c0=搜索结果前缀）。
        # keywords 只放岗位词：城市已由 a322 决定，带"成都"前缀会搜不到（同 BOSS 教训）。
        q = keyword.strip()
        if q.startswith("成都"):
            q = q[2:].strip()
        if not q:
            q = keyword.strip()
        return f"https://www.yupao.com/topic/a322c0/?keywords={quote(q)}"

    task_template = (
        "鱼泡直聘的{city}岗位搜索结果页已打开并显示「{keyword}」岗位（URL 已带城市 a322"
        "与关键词，结果已加载，无需重新搜索）。⚠️ 绝对不要点击顶部搜索框/搜索按钮——"
        "那会清空已加载的结果。"
        "如果出现安全访问验证（点击进行验证）页面，直接输出 done（reason 写'风控'）。"
        "如果出现登录/验证码弹窗，忽略它，不要点击。"
        "然后滚动查看{city}的岗位卡片列表（卡片含 岗位名/薪资/地点/公司），"
        "看到{city}的岗位列表后执行 extract。"
    )


class JobuiAgentScraper(AgentScraperBase):
    """职友集：搜索 URL 带 cityKw=成都，agent 在结果页滚动提取。"""
    platform_name = "职友集"
    start_url = "https://www.jobui.com/jobs?cityKw=成都"

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        return f"https://www.jobui.com/jobs?cityKw={quote('成都')}&q={quote(keyword)}&page=1"

    task_template = (
        "在职友集的{city}岗位搜索结果页查看「{keyword}」岗位。"
        "页面标题应包含{city}（已按{city}筛选）。"
        "如果页面出现登录/验证码弹窗，忽略它，不要点击。"
        "滚动查看岗位卡片列表（卡片含 岗位名/薪资/地点/公司），看到{city}的岗位列表后执行 extract。"
    )


# ---- 专业垂直平台适配器（点3 新增，贴合气象/环境核心需求） ----

class IguopinAgentScraper(AgentScraperBase):
    """国聘网：央企国企官方招聘平台（气象/环境国企岗位密集）。

    国聘网是 SPA，搜索框需 agent 操作；国企岗位多含校招，提取时按需过滤。
    """
    platform_name = "国聘网"
    start_url = "https://www.iguopin.com/"

    def build_start_url(self, keyword: str) -> str:
        return self.start_url

    task_template = (
        "在国聘网（央企国企官方招聘平台）搜索「{keyword}」岗位，城市选{city}。"
        "在搜索框输入关键词，选择{city}地区，点击搜索，等待结果加载。"
        "如果页面出现登录/验证码弹窗，忽略它，不要点击。"
        "滚动查看{city}的岗位卡片列表（卡片含 岗位名/薪资/地点/公司），看到后执行 extract。"
    )


class QixiangAgentScraper(AgentScraperBase):
    """气象人才网：中国气象局气象人才招聘系统（zp.cmatec.cn），气象垂直核心渠道。"""
    platform_name = "气象人才网"
    start_url = "http://zp.cmatec.cn/"

    def build_start_url(self, keyword: str) -> str:
        return self.start_url

    task_template = (
        "在中国气象局气象人才招聘网搜索「{keyword}」岗位。"
        "在页面的搜索框输入关键词，点击搜索/查询按钮，等待结果加载。"
        "如果出现登录/验证码要求，忽略或按页面引导跳过。"
        "滚动查看岗位列表（含 岗位名/单位/地点/要求），看到{city}或全国的岗位列表后执行 extract。"
    )


class BjxHuanbaoAgentScraper(AgentScraperBase):
    """北极星环保招聘：环保/水处理/固废垂直平台（hbjob.bjx.com.cn）。"""
    platform_name = "北极星环保招聘"
    start_url = "https://hbjob.bjx.com.cn/"

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        # 北极星支持按地区筛选，构造成都地区搜索链接
        return f"https://hbjob.bjx.com.cn/search/?kw={quote(keyword)}"

    task_template = (
        "在北极星环保招聘网搜索「{keyword}」岗位，地区选{city}。"
        "如果搜索页有地区/城市筛选控件，选择{city}；没有则直接查看当前结果。"
        "滚动查看岗位卡片列表（卡片含 岗位名/薪资/地点/公司），看到{city}的岗位列表后执行 extract。"
    )


class GaoxiaoJobAgentScraper(AgentScraperBase):
    """高校人才网：事业单位/科研院所招聘（gaoxiaojob.com），含气象研究所编制岗。"""
    platform_name = "高校人才网"
    start_url = "https://www.gaoxiaojob.com/"

    def build_start_url(self, keyword: str) -> str:
        from urllib.parse import quote
        return f"https://www.gaoxiaojob.com/search.html?keyword={quote(keyword)}"

    task_template = (
        "在高校人才网（事业单位/科研院所招聘）搜索「{keyword}」岗位，地区选{city}。"
        "在搜索框输入关键词，选择{city}地区，点击搜索。"
        "如果出现登录/验证码，忽略它，不要点击。"
        "滚动查看岗位卡片列表（含 岗位名/单位/地点/要求），看到{city}的岗位列表后执行 extract。"
    )
