# 代码审查 & GitHub Actions 错误分析报告

> **日期**: 2026-08-03  
> **仓库**: oniking099/cd-job-tracker  
> **分支**: main  
> **审查范围**: 全项目代码 + CI/CD 流水线 + 最近两次 Actions 失败

---

## 目录

1. [GitHub Actions 近期错误回顾](#1-github-actions-近期错误回顾)
2. [核心矛盾：版本二选一困境](#2-核心矛盾版本二选一困境)
3. [P0 致命问题](#3-p0-致命问题)
4. [P1 高优先级问题](#4-p1-高优先级问题)
5. [P2 中优先级问题](#5-p2-中优先级问题)
6. [P3 优化建议](#6-p3-优化建议)
7. [修复优先级路线图](#7-修复优先级路线图)
8. [附录：完整问题清单](#8-附录完整问题清单)

---

## 1. GitHub Actions 近期错误回顾

### 错误 #1：`StealthConfig ImportError`（commit `070ecb4`）

**现象**：`from playwright_stealth import StealthConfig` 导入失败

**根因**（✅ 已通过 WebSearch 验证）：`playwright-stealth` 在 **2025 年 6 月 18 日** 发布了 2.0.0 版本，进行了破坏性 API 变更：
- **移除**：`StealthConfig` 类、`stealth_async`/`stealth_sync` 函数
- **新增**：`Stealth` 类、`apply_stealth_async`/`apply_stealth_sync` 方法、`use_async`/`use_sync` 上下文管理器
- 这并非孤立问题——[crawl4ai 等知名项目也踩过同样的坑](https://github.com/unclecode/crawl4ai/issues/1273)

> ⚠️ **额外发现**：PyPI 上存在两个容易混淆的包——`tf-playwright-stealth`（1.x，小写导出）和 `playwright-stealth`（2.x，`Stealth` 类）。本项目用的是后者，迁移路径清晰。

初始 commit (`e1eb48f`) 的 `base.py` 使用的是 1.x API：
```python
from playwright_stealth import StealthConfig
from playwright_stealth import stealth_async as inject_stealth
# ...
await inject_stealth(context, StealthConfig(webdriver=True, ...))
```

但 `requirements.txt` 没有锁定版本上限，CI 环境安装了 2.x 包，导致 `StealthConfig` 找不到。

**当时的修复**（应急方案）：在 `requirements.txt` 中锁定 `<2.0.0`：
```
playwright-stealth>=1.0.6,<2.0.0
```

### 错误 #2：`ModuleNotFoundError: No module named 'pkg_resources'`（commit `47b83ba`）

**现象**：运行时找不到 `pkg_resources` 模块

**根因**（✅ 已通过 WebSearch 验证）：Python 3.12 的 venv 不再预装 setuptools（[gh-95299](https://github.com/python/cpython/issues/95299)），而 `playwright-stealth 1.x` 内部依赖了 `pkg_resources`。

**修复**：在 `requirements.txt` 中追加：
```
setuptools>=65.0.0
```

### 🔴 错误 #3（新发现）：`setuptools` 修复已失效 — `pkg_resources` 再次报错

**现象**：2026-08-03 所有 9 次 GitHub Actions 运行**全部失败**，包括最近两次（Run #7 Job Search + Daily Report），错误与 #2 完全相同：

```
File "src/scrapers/base.py", line 20, in <module>
    from playwright_stealth import StealthConfig
  File ".../playwright_stealth/__init__.py", line 2, in <module>
    from playwright_stealth.stealth import stealth_sync, stealth_async, StealthConfig
  File ".../playwright_stealth/stealth.py", line 6, in <module>
    import pkg_resources
ModuleNotFoundError: No module named 'pkg_resources'
```

**根因**（✅ 已通过 CI 日志验证）：

```
requirements.txt (远程)        CI 实际安装
─────────────────────────────────────────────
setuptools>=65.0.0      →     setuptools-83.0.0  ← pkg_resources 已移除！
playwright-stealth<2.0.0 →     playwright-stealth-1.0.6  ← 仍然依赖 pkg_resources
```

> 🕒 **时间炸弹已爆炸**：`pkg_resources` 在 2025 年 11 月 30 日从 setuptools 中移除。现在是 2026 年 8 月，`setuptools>=65.0.0` 解析为 **83.0.0**，该版本**不再包含 `pkg_resources`**。1.x 路线的应急补丁已彻底失效。

**这意味着什么**：回退到 1.x + setuptools 的路已经**走不通了**。迁移到 2.x 不再是"推荐"，而是**唯一可行的选择**。

### 完整错误链（3 次失败的演进）

三次错误本质上是同一个问题的连锁反应，最终证明了 1.x 路线是死胡同：

```
初始代码 (1.x API) + requirements.txt (无版本锁)
  → CI 安装 2.x → StealthConfig ImportError          [错误 #1]
    → 锁版本到 1.x (<2.0.0)
      → 缺少 setuptools → pkg_resources ModuleNotFoundError  [错误 #2]
        → 添加 setuptools>=65.0.0
          → setuptools 83.0.0 已移除 pkg_resources
            → ❌ pkg_resources 再次 ModuleNotFoundError   [错误 #3, 当前]
              → ✅ 唯一出路：迁移到 playwright-stealth 2.x
```

**教训**：错误 #2 的"修复"（加 `setuptools`）只是一个有时效性的创可贴——分析文档已正确预言了它的失效时间（2025-11-30）。现在这个预言已成现实。

---

## 2. 核心矛盾：版本二选一困境

当前仓库存在**本地与远程的代码分歧**：

| 项目 | 本地（working tree） | 远程（GitHub main） |
|------|---------------------|---------------------|
| `requirements.txt` | `playwright-stealth>=2.0.3` | `playwright-stealth>=1.0.6,<2.0.0` |
| `base.py` Stealth 用法 | `Stealth()` + `apply_stealth_async` (2.x API) | `StealthConfig` + `stealth_async` (1.x API) |
| `setuptools` 依赖 | 不需要 | `setuptools>=65.0.0` |
| 测试文件 | `tests/test_stealth_migration.py` ✅ | 无 |

### 推荐方案：坚持 2.x

| 维度 | 1.x（远程当前） | 2.x（本地当前） |
|------|----------------|----------------|
| API 稳定性 | ✅ CI 已验证 | ⚠️ 需在 CI 重新验证 |
| 依赖复杂度 | ❌ 需要 setuptools hack | ✅ 无额外依赖 |
| 反指纹效果 | ⚠️ 旧版，可能过时 | ✅ 更新 |
| 维护状态 | ❌ 不再更新 | ✅ 活跃维护 |
| 代码清晰度 | ⚠️ 函数式 API | ✅ OOP，更清晰 |
| 迁移进度 | — | ✅ 已完成 + 有测试 |

**结论**：坚持本地 2.x 路线。**这是唯一可行的方案**——1.x + setuptools 应急路线已在 CI 环境被证实失效（setuptools 83.0.0 不再包含 `pkg_resources`）。

> ⚠️ **紧急程度提升**：全部 9 次 GitHub Actions 运行均因此失败（100% 失败率）。在 push 本地 2.x 代码之前，项目 CI 将保持完全瘫痪状态。

---

## 3. P0 致命问题

### 3.1 `base.py` 中存在重复的 `search` 方法定义

**文件**：`src/scrapers/base.py:160-165` 和 `:190-193`

```python
# 第 160-165 行（死代码——无 @abstractmethod，无 round_label 参数）
async def search(self, keyword: str) -> list[Job]:
    """在平台上搜索关键词..."""
    raise NotImplementedError

# ... 中间隔了 30 行 ...

# 第 190-193 行（实际生效的版本）
@abstractmethod
async def search(self, keyword: str, round_label: str = "") -> list[Job]:
    """搜索岗位"""
    ...
```

**影响**：第一个定义是死代码（Python 中后定义覆盖先定义）。所有子类都使用第 190 行的签名（含 `round_label`）。存在两个定义会误导维护者。

**修复**：删除第 160-165 行。

### 3.2 本地与远程代码版本不一致

**现状**：
- 本地 `git status` 显示 `requirements.txt` 和 `base.py` 已修改但未提交
- 本地改到了 2.x，远程已回退到 1.x
- 如果直接 push 本地改动，远程的 1.x fix 会被覆盖，一切从 2.x 重新开始

**修复步骤**：
1. 确认 `base.py` 中 2.x API 调用与 [playwright-stealth 2.0 官方文档](https://pypi.org/project/playwright-stealth/2.0) 一致
2. 提交本地 `base.py`、`requirements.txt`、`tests/test_stealth_migration.py`
3. Push 并观察 CI 是否通过
4. 如果 CI 报错，检查是 PyPI 上的 `playwright-stealth` 包版本问题还是 API 参数问题

---

## 4. P1 高优先级问题

### 4.1 异常完全静默吞噬

**严重程度**：🔴 高 — 线上问题无法排查

**涉及文件**：
| 文件 | 位置 | 问题 |
|------|------|------|
| `base.py:151-152` | `_retry_get` | `except Exception: pass` — 网络失败无日志 |
| `boss.py:29-30, 57-63, 126-127, 164-165` | 多处 | 反爬失败、解析失败全部静默 |
| `job51.py:49-50` | `search` | 单页失败 `continue` 无日志 |
| `classifier.py:170` | `classify_with_llm` | LLM 调用失败静默标记为"其他"（但仅影响规则未判定的 job，参见下方 6.4 修正） |
| `deepseek.py:119-120` | `cross_platform_dedup` | LLM 去重失败静默返回原列表，可能与 `classifier.py` 的问题叠加 |
| `qwen_vl.py:76-78` | `extract_jobs_from_screenshot` | JSON 解析失败返回 `[]` 无日志 |
| `gemini.py:62-64` | 同上 | 同上 |

**修复建议**：
```python
# ❌ 当前
except Exception:
    pass

# ✅ 推荐
import logging
logger = logging.getLogger(__name__)

except Exception as e:
    logger.warning(f"[{self.platform_name}] 请求失败: {url}, 原因: {e}")
```

至少使用 `print()` 输出关键错误信息，方便在 CI 日志中排查。

### 4.2 `storage.py` 中 `CompanyType` 导入位置不当

**文件**：`src/storage.py:178`

```python
# 文件中间...使用了 CompanyType
def _dict_to_job(d: dict) -> Job:
    ct = d.get("company_type")
    return Job(
        ...
        company_type=CompanyType(ct) if ct else None,  # 第 151 行就用了！
        ...
    )

# ... 25 行之后 ...

from src.models import CompanyType  # 第 178 行才导入
```

虽然 Python 函数内引用是惰性求值的，运行时不会报错，但这违反 PEP 8（导入应在文件顶部），且极易在重构时引入 bug。

**修复**：将 `from src.models import CompanyType` 移到文件顶部（第 14 行 `from src.models import Job, SearchRound` 旁边）。

### 4.3 CI 无限循环风险 — 与 PAT Token 的交互

**文件**：`.github/workflows/search.yml:47-54`, `report.yml:36-43`

```yaml
- name: Checkout
  uses: actions/checkout@v4
  with:
    token: ${{ secrets.GH_PAT }}    # ⚠️ 使用的是 PAT 而非默认 GITHUB_TOKEN
```

```yaml
- name: Commit search data
  if: always()
  uses: stefanzweifel/git-auto-commit-action@v5
  with:
    commit_message: "data: search results [skip ci]"
```

**关键分析**（✅ 已通过 WebSearch 验证）：

| 场景 | 行为 |
|------|------|
| 默认 `GITHUB_TOKEN` + auto-commit | ✅ 自动提交**不会**触发新 workflow（GitHub 内置安全机制） |
| `GH_PAT` + auto-commit | ⚠️ 安全机制被绕过，提交**可能**触发新 workflow |
| `GH_PAT` + `[skip ci]` 标签 | ✅ 标签生效，阻止级联触发 |

**结论**：因为这个项目用了 `GH_PAT` 做 checkout（而非默认的 `GITHUB_TOKEN`），所以 `[skip ci]` 标签**不是冗余的，而是关键防线**。

这个配置是安全的，但依赖两项：
1. GitHub 的 `[skip ci]` 标签识别（稳定，不会变）
2. `GH_PAT` 权限最小化（确保 token 只有必要的 repo 权限）

**建议**：在 `search.yml` 中添加注释说明此依赖关系，避免后续维护者误删 `[skip ci]` 标签。

---

## 5. P2 中优先级问题

### 5.1 多 Cron 并发下的轮次重复执行

**文件**：`.github/workflows/search.yml:5-11`, `scripts/search.py:22-28`

```yaml
schedule:
  - cron: '0 4 * * *'    # 12:00 BJT
  - cron: '0 5 * * *'    # 13:00 BJT
  # ...共 6 个
```

```python
def get_current_round() -> str:
    now = datetime.now(BJT)
    hour = now.hour
    if hour < 12 or hour > 17:
        return "12"
    return str(hour)  # 依赖当前北京时间的小时数
```

**问题**：由于 `concurrency.cancel-in-progress: false`，如果某个 cron 触发后因 GitHub Actions 队列延迟而在下一小时才实际运行，会得到错误的 `round_label`。

**建议**：在 workflow 中通过环境变量显式传递轮次：

```yaml
# 方案：每个 cron 时间点配置对应的轮次
- cron: '0 4 * * *'
  env:
    SEARCH_ROUND: "12"
```

或者在 workflow 中使用 strategy matrix 减少重复配置：
```yaml
strategy:
  matrix:
    round: [12, 13, 14, 15, 16, 17]
```

### 5.2 Stealth 参数名对齐问题

**文件**：`src/scrapers/base.py:109-128`

根据 [playwright-stealth 2.0 官方文档](https://pypi.org/project/playwright-stealth/2.0)，推荐用法是：

```python
# 推荐：自动应用到所有页面
async with Stealth().use_async(async_playwright()) as p:
    ...

# 手动用法（当前代码的方式）
stealth = Stealth(navigator_webdriver=True, ...)
await stealth.apply_stealth_async(context)
```

当前代码使用的方式（手动 `apply_stealth_async`）是**支持**的。参数验证状态：
- ✅ `navigator_webdriver=True` — 文档确认存在
- ✅ `navigator_languages=True` — `test_stealth_migration.py:143` 中已通过属性断言本地验证通过（`stealth.navigator_languages is True`）
- ✅ `chrome_load_times`, `chrome_csi` — 测试中 `test_stealth_attributes` 已逐个验证所有参数可用
- ✅ `outerdimensions` — 测试中 `test_outerdimensions_removed` 明确验证了此参数在 2.x 已移除，与代码注释一致

**建议**：
1. 在 CI 中运行 `pytest tests/test_stealth_migration.py -v` 做最终验证（本地测试已覆盖参数检查，CI 验证可确认环境差异）
2. 考虑切换到推荐的 `Stealth().use_async()` 模式以简化代码

### 5.3 Gemini OpenAI 兼容层风险

**文件**：`src/llm/gemini.py:16`

```python
base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
```

Gemini 的 OpenAI 兼容层是 beta 版本，多模态格式（base64 图片 + JSON）可能与标准 OpenAI API 有差异。实际使用中可能遇到：
- 图片大小限制（Gemini 对 base64 图片有 4MB 上限）
- 响应格式差异

**建议**：在 `base.py` 的 `_parse_with_fallback` 降级逻辑中已经通过 `try/except` 处理了此风险，但应在 except 块中添加日志。

### 5.4 `report.yml` 缺少 Playwright 安装

**文件**：`.github/workflows/report.yml:26-27`

```yaml
- name: Install dependencies
  run: pip install -r requirements.txt
```

Report workflow 没有 `playwright install chromium`，但因为报告流程不使用浏览器，当前不会出错。然而：
- `ALL_SCRAPERS` 导入了所有爬虫类 → 所有爬虫继承 `BaseScraper` → `BaseScraper` 导入 `playwright`
- `pip install playwright` 会安装 Python 包，但不安装浏览器二进制文件
- 如果不小心在报告流程中触发了爬虫逻辑，会报错 `Executable doesn't exist`

**建议**：在报告中明确排除爬虫依赖，或者在 `report.py` 中延迟导入爬虫模块。

---

## 6. P3 优化建议

### 6.1 架构优化

#### Browser 实例复用

**问题**：`search_all_platforms` 并发运行 12 个爬虫，每个爬虫在 `__aenter__` 中启动独立的 `Browser` 实例。12 个 Chromium 同时启动，资源消耗极大。

**建议**：在基类或管理层实现 browser pool：
```python
# pipeline.py 中共享 browser
async def search_all_platforms(keyword, round_label):
    async with async_playwright() as p:
        browser = await p.chromium.launch(...)
        # 所有爬虫共享同一个 browser，各自创建 context
        tasks = [search_with_shared_browser(browser, cls, keyword, round_label) 
                 for cls in ALL_SCRAPERS.values()]
        ...
```

#### 过滤管道顺序优化

**当前顺序**：`薪资过滤 → 资格排除 → 专业匹配 → 行业排除`

**建议**：将**最严格的过滤器放在最前面**（减少后续步骤的处理量）：
1. 资格排除（剔除校招/实习/管培生等不相关类型）— 通常排除率最高
2. 行业排除（剔除游戏/智驾等）
3. 薪资过滤
4. 专业匹配

### 6.2 代码质量优化

#### 去除冗余 `hasattr` 检查

**文件**：`boss.py:79, 82, 104`

```python
title = title_el.text(strip=True) if hasattr(title_el, 'text') else ""
```

`selectolax` 的 `HTMLParser` 节点**始终**有 `text()` 方法和 `attrs` 属性——这些检查是多余的，可以直接调用。

#### 细化异常捕获类型

将宽泛的 `except Exception` 改为具体类型：

| 当前 | 建议 |
|------|------|
| `except Exception` | `except (httpx.HTTPError, asyncio.TimeoutError, json.JSONDecodeError)` |
| `except Exception: pass` | `except PlaywrightError as e: logger.warning(...)` |

#### 类型注解完善

**文件**：`pipeline.py:129`

```python
async def search_single_platform(
    scraper_class: type,  # ❌ 太宽泛
    ...
```

应改为：
```python
from typing import Type
async def search_single_platform(
    scraper_class: Type[BaseScraper],  # ✅ 精确
    ...
```

### 6.3 CI/CD 优化

#### 添加测试步骤

```yaml
# search.yml 和 report.yml 中添加
- name: Run tests
  run: python -m pytest tests/ -v
```

#### 明确指定 Python 依赖缓存路径

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: '3.12'
    cache: 'pip'
    cache-dependency-path: 'requirements.txt'  # 显式指定
```

#### 依赖安装耗时优化

从 CI 日志观察到的实际耗时：

| 步骤 | 耗时 | 瓶颈 |
|------|------|------|
| `pip install` 依赖解析 | ~15s | `grpcio-status` 版本回溯（pip 尝试了 10+ 个版本） |
| Playwright Chromium 下载 | ~5s | 184 MB 下载 |
| Playwright FFmpeg 下载 | ~1s | 2.3 MB |
| Playwright Headless Shell 下载 | ~3s | 115 MB |
| **总计安装耗时** | **~25s** | 占 30min timeout 的 1.4% |

**建议**：
1. 在 `requirements.txt` 中锁定 `grpcio-status` 版本以减少依赖回溯（如 `grpcio-status==1.71.2`）
2. 考虑使用 `pip install --no-deps` + 预编译的 lock file (`requirements.lock`)

#### Node.js 20 弃用警告

所有 CI 日志中反复出现：
```
Node.js 20 is deprecated. The following actions target Node.js 20 but are being forced 
to run on Node.js 24: actions/checkout@v4, actions/setup-python@v5, 
stefanzweifel/git-auto-commit-action@v5
```

**建议**：将三个 actions 升级到使用 Node.js 24 的最新版本（目前均为 v4/v5，等待上游更新）。这不是紧急问题，但每次运行都会产生 warning 噪音。

### 6.4 其他发现

| # | 文件 | 问题 | 建议 |
|---|------|------|------|
| 1 | `src/report/templates/report.html:7` | 使用 CDN 的 Tailwind（~3MB），微信内置浏览器加载极慢 | 构建时内联关键 CSS，或使用轻量替代 |
| 2 | `config.py:15` | `_env` 返回空字符串而非 `None`，语义模糊 | 区分"未设置"和"设置但为空" |
| 3 | `src/filters/salary.py:34` | `"薪资open"` 在 `UNKNOWN_SALARY_KEYWORDS` 列表中出现了两次 | 去重 |
| 4 | `pipeline.py:189` | 早停阈值 `120` 是硬编码魔法数字 | 提取为配置常量 |
| 5 | `classifier.py:171-173` | LLM 分类异常时将所有**规则未命中**的 job 标记为 `CompanyType.OTHER`（line 120: `uncertain = [j for j in jobs if j.company_type is None ...]`，**不会覆盖规则已分类结果**） — 此处逻辑正确，但缺少日志记录 | 添加 warning 日志，区分"LLM 判定为其他"和"LLM 调用异常兜底为其他" |
| 6 | `pipeline.py:302` | `(j.company_type or "未知").value` — 若 LLM 分类步骤（line 280-285）在 `classify_with_llm` 调用前即抛出异常，则部分 job 的 `company_type` 仍为 `None`，`"未知".value` 会触发 **`AttributeError` 崩溃** | 将 `"未知"` 改为 `CompanyType.OTHER`，或先过滤掉 `company_type is None` 的 job |

---

## 7. 修复优先级路线图

### 立即修复（本次会话）

| # | 问题 | 文件 | 操作 |
|---|------|------|------|
| 1 | 删除死代码 — 重复的 `search` 方法 | `base.py:160-165` | 删除 6 行 |
| 2 | `CompanyType` 导入移到顶部 | `storage.py:178` → `:14` | 移动 1 行 |
| 3 | 统一 2.x 路线 — 提交本地改动 | `base.py` + `requirements.txt` + `tests/` | commit & push |
| 4 | `pipeline.py:302` 潜在的 `AttributeError` | `pipeline.py:302` | `"未知"` → `CompanyType.OTHER` |

### 本周内修复

| # | 问题 | 操作 |
|---|------|------|
| 5 | 所有 `except Exception: pass` 添加日志 | 遍历全部爬虫文件，加 `print()` 或 `logging` |
| 6 | CI 中启用测试步骤 | 修改 `search.yml` 和 `report.yml` |
| 7 | 验证 Stealth 2.x 所有参数兼容性 | 在 CI 中跑 `test_stealth_migration.py` |

### 下个迭代

| # | 问题 | 操作 |
|---|------|------|
| 8 | Browser pool 复用 | 重构 `pipeline.py` |
| 9 | 过滤管道顺序优化 | 调整 `run_search_round` 中过滤器顺序 |
| 10 | 细化异常捕获类型 | 逐文件替换 `except Exception` |

---

## 8. 附录：完整问题清单

### 问题统计

| 级别 | 数量 | 描述 |
|------|------|------|
| 🔴 P0 | 2 | 致命：死代码 + 版本分歧 |
| 🟠 P1 | 3+1 | 高优先级：异常吞噬(修正路径) + 导入顺序 + CI 循环 + `pipeline.py:302` 崩溃隐患 |
| 🟡 P2 | 4 | 中优先级：并发 + API 兼容(参数已本地验证) + Gemini 风险 + 浏览器依赖 |
| 🔵 P3 | 10+ | 优化建议：架构 + 代码质量 + CI/CD |

### 本次审核修正记录

| 修正项 | 原文 | 修正后 |
|--------|------|--------|
| `classify_with_llm` 文件路径 | `deepseek.py:170` | `classifier.py:170`（`src/filters/classifier.py`） |
| 6.4 #5 逻辑判断 | "丢失了规则已分类的结果" | **逻辑正确**：`uncertain` 仅含 `company_type is None` 的 job，不会覆盖规则分类结果 |
| 遗漏的崩溃隐患 | 未提及 | 新增 `pipeline.py:302` `AttributeError` 风险 |
| `navigator_languages` 参数 | 标记为"需验证" | 本地测试已通过，降级为"待 CI 确认" |
| `salary.py` 路径 | 缺少 `src/filters/` 前缀 | 修正为 `src/filters/salary.py:34` |
| `report.html` 路径 | 缺少 `src/report/templates/` 前缀 | 修正为完整路径 |

### 关键决策记录

1. **playwright-stealth 版本选择**：选 2.x（OOP API），放弃 1.x（函数式 API + setuptools hack）
2. **GitHub `[skip ci]` 依赖**：确认 GitHub Actions 原生支持，当前配置安全
3. **异常处理策略**：保持"容错先行"的设计（单个平台失败不影响整体），但必须添加可观测性日志

---

> **审查结论**：代码整体架构设计合理（爬虫基类继承 + 过滤器管道 + LLM 降级）。**当前 CI 完全瘫痪**（全部 9 次运行 100% 失败），根因是 1.x 应急路线已失效——setuptools 83.0.0 不再包含 `pkg_resources`，而 `playwright-stealth 1.x` 仍依赖它。唯一的修复路径是**立即 push 本地 2.x 代码**。P0/P1 修复后，项目即可恢复 CI 正常运行。
