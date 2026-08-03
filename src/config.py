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

# ---- LLM ----
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = "deepseek-chat"  # v4-flash 通过 API 调用

QWENVL_API_KEY = _env("QWENVL_API_KEY")
QWENVL_BASE_URL = _env("QWENVL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWENVL_MODEL = "qwen-vl-max"

GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash-lite"

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
