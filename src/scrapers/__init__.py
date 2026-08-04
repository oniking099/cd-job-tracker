"""爬虫模块"""
from src.scrapers.job51 import Job51Scraper
from src.scrapers.zhilian import ZhilianScraper
from src.scrapers.liepin import LiepinScraper
from src.scrapers.lagou import LagouScraper
from src.scrapers.boss import BossScraper
from src.scrapers.yupao import YupaoScraper
from src.scrapers.wuba import WubaScraper
from src.scrapers.jobui import JobuiScraper
from src.scrapers.chinahr import ChinahrScraper

from src.scrapers.agent_scraper import (
    AgentScraperBase,
    BossAgentScraper,
    Job51AgentScraper,
    ZhilianAgentScraper,
    # 转换平台（HTML 爬虫失效 → agent 模拟人类操作）
    LiepinAgentScraper,
    LagouAgentScraper,
    WubaAgentScraper,
    ChinahrAgentScraper,
    YupaoAgentScraper,
    JobuiAgentScraper,
    # 专业垂直平台（点3 新增）
    IguopinAgentScraper,
    QixiangAgentScraper,
    BjxHuanbaoAgentScraper,
    GaoxiaoJobAgentScraper,
)

# 覆盖的招聘渠道（点3：专业垂直平台 + 主流综合平台；低效平台已剔除）
ALL_SCRAPERS: dict[str, type] = {
    "51Job": Job51Scraper,
    "智联招聘": ZhilianScraper,
    "猎聘": LiepinScraper,
    "拉勾": LagouScraper,
    "BOSS直聘": BossScraper,
    "鱼泡直聘": YupaoScraper,
    "58同城": WubaScraper,
    "职友集": JobuiScraper,
    "中华英才网": ChinahrScraper,
    # 专业垂直平台
    "国聘网": IguopinAgentScraper,
    "气象人才网": QixiangAgentScraper,
    "北极星环保招聘": BjxHuanbaoAgentScraper,
    "高校人才网": GaoxiaoJobAgentScraper,
}

# LLM Agent 智能体平台：命中则用 Agent 模拟人类操作，否则走 HTML 爬虫
AGENT_SCRAPERS: dict[str, type] = {
    "智联招聘": ZhilianAgentScraper,
    "51Job": Job51AgentScraper,
    "BOSS直聘": BossAgentScraper,
    # 转换平台
    "猎聘": LiepinAgentScraper,
    "拉勾": LagouAgentScraper,
    "58同城": WubaAgentScraper,
    "中华英才网": ChinahrAgentScraper,
    "鱼泡直聘": YupaoAgentScraper,
    "职友集": JobuiAgentScraper,
    # 专业垂直平台
    "国聘网": IguopinAgentScraper,
    "气象人才网": QixiangAgentScraper,
    "北极星环保招聘": BjxHuanbaoAgentScraper,
    "高校人才网": GaoxiaoJobAgentScraper,
}
