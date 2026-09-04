"""
配置管理：从环境变量读取所有配置项。
GitHub Actions 中通过 secrets 注入，本地通过 .env 文件。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env（本地开发用，GitHub Actions 中不存在也不报错）
load_dotenv(Path(__file__).parent.parent / ".env")

# 北京时间时区（所有轮次/日期统一用 BJT，避免 GitHub 凌晨调度跨 UTC 日错位）
BJT_TZ = timezone(timedelta(hours=8))


def bjt_now() -> datetime:
    """当前北京时间（datetime，含时区）。"""
    return datetime.now(BJT_TZ)


def bjt_today() -> str:
    """当前北京时间日期（YYYY-MM-DD）。检索 01:00 BJT=17:00 UTC 启动时
    UTC 日比 BJT 日早一天，数据分目录必须按 BJT 日期，否则轮次落错天。
    """
    return bjt_now().date().isoformat()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# ---- 项目路径 ----
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
SESSIONS_DIR = PROJECT_ROOT / ".sessions"  # 登录态文件（capture_session.py 导出，gitignore）

# ---- LLM ----
# GLM 视觉多模态（兜底，视觉链末位：ModelScope → GLM）：智谱 GLM Coding Plan
# （OpenAI 兼容），glm-5.3-flash 原生多模态可读图，套餐内额度为 GLM-5.3 的 3 倍，
# 非高峰时段（含周末全天）积分减半。
# ⚠️ base_url 必须用 Coding Plan 专属端点 /api/coding/paas/v4（普通 /api/paas/v4
#    扣平台余额而非套餐额度）。思考模型官方推荐 temperature=1 / top_p=0.95。
GLM_API_KEY = _env("GLM_API_KEY")
GLM_BASE_URL = _env("GLM_BASE_URL", "https://open.bigmodel.cn/api/coding/paas/v4")
GLM_VL_MODEL = _env("GLM_VL_MODEL", "glm-5.3-flash")

DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = "deepseek-chat"  # v4-flash 通过 API 调用

QWENVL_API_KEY = _env("QWENVL_API_KEY")
QWENVL_BASE_URL = _env("QWENVL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWENVL_MODEL = "qwen-vl-max"

GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.1-flash-lite"  # 2.5-flash-lite 已废弃（404）

# ModelScope 魔搭 api-inference（阿里云百炼托管）
# 总配额 2000 次/天（所有模型加和），单模型最高 200 次/天（先进模型实际更少）。
# → 把可用模型全部列进优先列表，429/额度不足时自动切下一个，不做单选。
MODELSCOPE_API_KEY = _env("MODELSCOPE_API_KEY")
MODELSCOPE_BASE_URL = _env("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1")
# 视觉链（只有 VL 模型能读图）：能力优先、非思考在前，429/空内容逐级降
# 实测（2026-08-05 全量复测）：Qwen3-VL 五模型可用（235B-I/30B-I/30B-T/8B-I/8B-T）；
# Qwen2.5-VL 全系与 Qwen3-VL-235B-Thinking 报 no provider；中文「千问/」前缀全部 400 Invalid。
MODELSCOPE_VL_MODELS = _env(
    "MODELSCOPE_VL_MODELS",
    "Qwen/Qwen3-VL-235B-A22B-Instruct,"
    "Qwen/Qwen3-VL-30B-A3B-Instruct,"
    "Qwen/Qwen3-VL-30B-A3B-Thinking,"
    "Qwen/Qwen3-VL-8B-Instruct,"
    "Qwen/Qwen3-VL-8B-Thinking",
)
# 文本链（决策兜底，DeepSeek 失败时用）：匹配度×能力排序——决策任务延迟关键，
# 故快且稳的模型在前，能力顶级但偶发空内容/慢的居中，DeepSeek-V4-Flash-0731 收尾兜底。
MODELSCOPE_TEXT_MODELS = _env(
    "MODELSCOPE_TEXT_MODELS",
    "deepseek-ai/DeepSeek-V4-Pro,"
    "Qwen/Qwen3-Next-80B-A3B-Instruct,"
    "Qwen/Qwen3-235B-A22B-Thinking-2507,"
    "Qwen/Qwen3-30B-A3B-Thinking-2507,"
    "Qwen/Qwen3.5-397B-A17B,"
    "Qwen/Qwen3.5-35B-A3B,"
    "deepseek-ai/DeepSeek-V4-Flash-0731",
)
# 兼容旧版单模型变量（列表为空时的兜底，不再作为主配置）
MODELSCOPE_VL_MODEL = _env("MODELSCOPE_VL_MODEL", "")

# SiliconFlow 硅基流动（OpenAI 兼容）
# - SILICONFLOW_OCR_MODEL 云端 OCR 兜底（PaddleOCR-VL-1.5 永久免费，RapidOCR 空时用）
# - SILICONFLOW_VL_MODEL 视觉提取（2026-09-04 已退出视觉链，变量保留供 siliconflow.py 备用）
SILICONFLOW_API_KEY = _env("SILICONFLOW_API_KEY")
SILICONFLOW_BASE_URL = _env("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_VL_MODEL = _env("SILICONFLOW_VL_MODEL", "Qwen/Qwen3-VL-8B-Instruct")
SILICONFLOW_OCR_MODEL = _env("SILICONFLOW_OCR_MODEL", "PaddlePaddle/PaddleOCR-VL-1.5")

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

# ---- 检索窗口（BJT 时刻）：17:00 开始，跨午夜至次日 02:30 截止 ----
# 用户 2026-08-10：检索固定 17:00 BJT 开始；次日 10:00 才推送，
# 窗口放宽到 02:30 不影响推送，同时给 GitHub 调度延迟留足余量。
SEARCH_START_HOUR = int(_env("SEARCH_START_HOUR", "17"))
SEARCH_DEADLINE_HOUR = int(_env("SEARCH_DEADLINE_HOUR", "2"))
SEARCH_DEADLINE_MINUTE = int(_env("SEARCH_DEADLINE_MINUTE", "30"))

# ---- 详情页正文富集 ----
DETAIL_MAX_JOBS = int(_env("DETAIL_MAX_JOBS", "60"))             # 每轮最多富集岗位数（URL 去重后）
DETAIL_BUDGET_SECONDS = int(_env("DETAIL_BUDGET_SECONDS", "900"))  # 富集总时间预算（秒）
DETAIL_JD_TEXT_LIMIT = int(_env("DETAIL_JD_TEXT_LIMIT", "3000"))   # JD 正文保留最大字符数

# ---- 轮次确认 ----
CONFIRM_MIN_VALID = int(_env("CONFIRM_MIN_VALID", "5"))           # 每轮有效 JD 数量门槛
