"""
配置管理：从环境变量读取所有配置项。
GitHub Actions 中通过 secrets 注入，本地通过 .env 文件。
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env（本地开发用，GitHub Actions 中不存在也不报错）
load_dotenv(Path(__file__).parent.parent / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# ---- 项目路径 ----
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
SESSIONS_DIR = PROJECT_ROOT / ".sessions"  # 登录态文件（capture_session.py 导出，gitignore）

# ---- LLM ----
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = "deepseek-chat"  # v4-flash 通过 API 调用

QWENVL_API_KEY = _env("QWENVL_API_KEY")
QWENVL_BASE_URL = _env("QWENVL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWENVL_MODEL = "qwen-vl-max"

GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.1-flash-lite"  # 2.5-flash-lite 已废弃（404）

# ---- 地理 ----
GAODE_API_KEY = _env("GAODE_API_KEY")
HOME_LNG = float(_env("HOME_LNG", "103.93"))
HOME_LAT = float(_env("HOME_LAT", "30.78"))

# ---- 通知 ----
SERVER_CHAN_SENDKEY = _env("SERVER_CHAN_SENDKEY")

# ---- 搜索配置 ----
REQUEST_DELAY_MIN = 2.0   # 最小请求间隔（秒）
REQUEST_DELAY_MAX = 5.0   # 最大请求间隔（秒）
MAX_RETRIES = 3           # 最大重试次数
SEARCH_TIMEOUT = 30       # 单个平台搜索超时（秒）
MIN_RESULTS_PER_ROUND = 20  # 每轮最低新增数
MAX_RESULTS_PER_ROUND = 80  # 每轮最高新增数

# ---- Agent 智能体 ----
AGENT_MAX_STEPS = int(_env("AGENT_MAX_STEPS", "15"))     # 单个关键词操作步数上限
AGENT_TIMEOUT = int(_env("AGENT_TIMEOUT", "240"))        # 单平台 agent 总超时（秒）
AGENT_LLM_TIMEOUT = float(_env("AGENT_LLM_TIMEOUT", "45"))  # 单次决策调用超时（秒，单图）
AGENT_EXTRACT_TIMEOUT = float(_env("AGENT_EXTRACT_TIMEOUT", "120"))  # 单次提取调用超时（秒，多图）

# ---- 单平台时间预算（秒）----
# 每轮 12 个平台并行，慢速 HTML 爬虫会拖垮 gather；给每个平台设硬预算，超时就跳过。
PLATFORM_BUDGET_AGENT = int(_env("PLATFORM_BUDGET_AGENT", "300"))  # agent 平台（含 agent 循环 + 提取）
PLATFORM_BUDGET_HTML = int(_env("PLATFORM_BUDGET_HTML", "40"))     # HTML 爬虫（基本在失败/重试，卡死）
