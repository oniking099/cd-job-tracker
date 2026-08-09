"""
数据模型：Job 及其相关类型定义。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib


class CompanyType(str, Enum):
    STATE_OWNED = "国企"       # 国有企业
    CENTRAL = "央企"           # 中央企业
    FOREIGN = "外资"           # 外资企业
    JOINT_VENTURE = "合资"     # 合资企业
    OTHER = "其他"             # 其他（民营、创业公司等）


class SourcePlatform(str, Enum):
    JOB51 = "51Job"
    ZHILIAN = "智联招聘"
    LIEPIN = "猎聘"
    BOSS = "BOSS直聘"
    YUPAO = "鱼泡直聘"
    WUBA = "58同城"
    JOBUI = "职友集"
    CHINAHR = "中华英才网"
    # 专业垂直平台（点3 新增）
    IGUOPIN = "国聘网"
    QIXIANG = "气象人才网"
    BJX_HUANBAO = "北极星环保招聘"
    GAOXIAOJOB = "高校人才网"


@dataclass
class Job:
    """标准化后的招聘岗位"""
    # 唯一标识
    platform: str                    # 来源平台
    job_id: str                      # 平台内岗位ID
    url: str                         # 原始招聘页面URL（必须真实可打开）

    # 基本信息
    title: str                       # 岗位名称
    company: str                     # 企业名称
    company_type: CompanyType | None = None  # 企业类型（LLM判定）

    # 薪资（标准化为月薪元）
    salary_min: float = 0.0          # 最低月薪
    salary_max: float = 0.0          # 最高月薪
    salary_text: str = ""            # 原始薪资文本

    # 地点
    location: str = ""               # 工作地点描述
    district: str = ""               # 所在区/县
    lng: float | None = None         # 经度（高德查询）
    lat: float | None = None         # 纬度（高德查询）
    distance_km: float | None = None # 离家距离（km）

    # 内容
    responsibilities: str = ""       # 岗位职责
    requirements: str = ""           # 岗位要求

    # 元信息
    hr_active: bool = False          # HR是否活跃
    posted_date: str = ""            # 发布日期
    scraped_at: str = ""             # 抓取时间
    search_round: str = ""           # 所属搜索轮次（12/13/14/15/16/17）

    # 过滤标记
    excluded: bool = False           # 是否被过滤掉
    exclude_reason: str = ""         # 被过滤原因

    @property
    def dedup_key(self) -> str:
        """去重键：平台 + 岗位ID"""
        return hashlib.md5(f"{self.platform}:{self.job_id}".encode()).hexdigest()

    @property
    def cross_platform_key(self) -> str:
        """跨平台去重键：公司名 + 岗位名 + 城市"""
        raw = f"{self.company}:{self.title}:{self.location}"
        return hashlib.md5(raw.encode()).hexdigest()

    @property
    def salary_display(self) -> str:
        """格式化薪资显示"""
        if self.salary_min <= 0:
            return self.salary_text or "薪资面议"
        w_min = self.salary_min / 10000
        w_max = self.salary_max / 10000 if self.salary_max > 0 else w_min
        if w_min == w_max:
            return f"{w_min:.1f}万/月"
        return f"{w_min:.1f}-{w_max:.1f}万/月"


@dataclass
class SearchRound:
    """一轮搜索的结果"""
    round_label: str                 # 轮次标签（1~5）
    keywords_used: list[str] = field(default_factory=list)
    total_raw: int = 0               # 原始采集数
    total_after_filter: int = 0      # 过滤后数量
    jobs: list[Job] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)   # 覆盖率/每平台有效数/确认结果（确认后写入）
