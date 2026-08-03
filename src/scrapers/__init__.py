"""爬虫模块"""
from src.scrapers.job51 import Job51Scraper
from src.scrapers.zhilian import ZhilianScraper
from src.scrapers.liepin import LiepinScraper
from src.scrapers.lagou import LagouScraper
from src.scrapers.boss import BossScraper
from src.scrapers.yupao import YupaoScraper
from src.scrapers.yingjiesheng import YingjieshengScraper
from src.scrapers.wuba import WubaScraper
from src.scrapers.ganji import GanjiScraper
from src.scrapers.jobui import JobuiScraper
from src.scrapers.maimai import MaimaiScraper
from src.scrapers.chinahr import ChinahrScraper

ALL_SCRAPERS: dict[str, type] = {
    "51Job": Job51Scraper,
    "智联招聘": ZhilianScraper,
    "猎聘": LiepinScraper,
    "拉勾": LagouScraper,
    "BOSS直聘": BossScraper,
    "鱼泡直聘": YupaoScraper,
    "应届生求职网": YingjieshengScraper,
    "58同城": WubaScraper,
    "赶集直招": GanjiScraper,
    "职友集": JobuiScraper,
    "脉脉": MaimaiScraper,
    "中华英才网": ChinahrScraper,
}
