# 成都招聘信息定时采集推送系统

每天自动从 13 个招聘平台采集成都地区气象、环境、LLM、AI Agent 领域岗位，经过多层智能筛选，生成精美 HTML 报告，通过 Server酱推送到微信。

## 工作流程

```
12:00 ─ 搜索轮次 1（气象/大气科学） ─→ 保存到 data/
13:00 ─ 搜索轮次 2（环境/生态）     ─→ 保存到 data/
14:00 ─ 搜索轮次 3（AI/大模型）     ─→ 保存到 data/
15:00 ─ 搜索轮次 4（AI Agent）      ─→ 保存到 data/
16:00 ─ 搜索轮次 5（交叉学科）      ─→ 保存到 data/
17:00 ─ 搜索轮次 6（补充/长尾）     ─→ 保存到 data/
                         ↓
21:30 ─ 汇总去重 → LLM增强 → HTML报告 → Server酱推送
```

## 平台覆盖

12 个招聘平台，全部通过 LLM Agent 模拟人类操作采集（observe→think→act），优先用 URL 城市参数直达成都，数据层 `_filter_city` 二次兜底：

| 平台 | 类型 | 采集方式 | 实测状态 |
|---|---|---|---|
| 智联招聘 | 综合 | Agent + `jl=801`(成都) URL | ✅ 有成都岗位 |
| 51Job | 综合 | Agent + `jobArea=090200`(成都) URL | ✅ 有成都岗位 |
| BOSS直聘 | 综合 | Agent + `city=101270100`(成都) URL | ❌ 登录墙 |
| 猎聘 | 综合 | Agent + `city=280020&dq=280020`(成都) URL | ✅ 有成都岗位 |
| 58同城 | 综合 | Agent + `cd.58.com`(成都站) | ❌ 登录墙/验证码 |
| 中华英才网 | 综合 | Agent + `/job?value=` 搜索 | ✅ 有成都岗位 |
| 职友集 | 综合 | Agent + `cityKw=成都` | ✅ 有成都岗位 |
| 鱼泡直聘 | 蓝领/综合 | Agent + `city=成都` | ❌ 验证码滑块 |
| 国聘网 | 央企国企官方 | Agent（搜索框+成都筛选） | ✅ 有成都岗位 |
| 气象人才网 | 气象垂直（中国气象局） | Agent | ✅ 有成都气象岗位 |
| 北极星环保招聘 | 环保垂直 | Agent + 四川地区筛选 | ✅ 有成都环境岗位 |
| 高校人才网 | 高校/事业单位 | Agent + `/search.html?keyword=` | ✅ 有成都高校岗 |

> **登录墙说明**：BOSS/58/鱼泡 有平台级登录/验证码风控，Agent 无法绕过（不提供账号凭据），适配器会在识别到登录墙时快速跳过，不浪费 API 预算。其余 9 平台实测均能提取成都岗位。

> 曾评估的 赶集直招/脉脉/应届生求职网 因数据质量低已剔除。

## 部署

### 1. Fork 仓库到你的 GitHub

### 2. 配置 GitHub Secrets

在仓库 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 |
|--------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `QWENVL_API_KEY` | 阿里云百炼 Qwen-VL-Max Key |
| `SERVER_CHAN_SENDKEY` | Server酱 SendKey |
| `GAODE_API_KEY` | （可选）高德 Web服务 Key |
| `GEMINI_API_KEY` | （可选）Gemini API Key |
| `GH_PAT` | GitHub Personal Access Token |

### 3. 本地开发

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium --with-deps

# 复制配置
cp .env.example .env
# 编辑 .env 填入 API Key

# 运行搜索
python scripts/search.py

# 生成报告
python scripts/report.py
```

## 筛选规则

- **地点（硬约束）**：只保留工作地点为**成都**的岗位，其他城市一律剔除（双保险：agent 操作层 URL 城市参数 + 数据层全局 `_keep_chengdu` 过滤）
- **薪资**：国企/央企/外资/合资 ≥ 1万/月，其他 ≥ 1.6万/月
- **专业**：气象/大气/环境/生态/遥感/GIS/碳中和，或专业不限
- **排除**：35岁以下、博士、党员、校招/实习/兼职/管培生
- **企业分类**：规则匹配 + DeepSeek LLM 辅助判断
- **行业排除**：游戏、智能驾驶、前端LLM/后端LLM/大数据LLM

## 技术栈

- Python 3.12 + Playwright + playwright-stealth
- DeepSeek v4-flash（文本推理）
- Qwen-VL-Max（视觉提取兜底）
- Jinja2 + Tailwind CSS（HTML 报告）
- GitHub Actions（定时调度）

## HTML 报告格式规范

报告由 `src/report/generator.py` + `templates/report.html` 渲染，布局规则如下：

### 布局

- **一司一卡**：一个公司所有岗位合并到一张卡片，一个岗位一行（`_group_by_company` 全局按公司聚合）
- **类型大框**：同一类型公司的卡片放在同一个大框内，从上到下依次为 **国企 → 央企 → 外资 → 合资 → 其他**；大框可展开/收起，**默认全部展开**
- 每类配色区分（红/黄/蓝/绿/灰 + 渐变标题条），头部有今日岗位总数与各类别司/岗数统计

### 卡片字段

| 字段 | 说明 |
|------|------|
| 公司名称 | 卡片标题；HR 活跃时显示绿色呼吸点徽标 |
| 信息来源 | 平台标签（51Job / 智联 / BOSS 等） |
| 薪资范围（多少薪） | 原始薪资文本 + `13薪/14薪/15薪` 独立徽标 |
| 岗位职责 | 前 180 字 + 展开省略 |
| 岗位要求 | 前 180 字 + 展开省略 |
| 公司地点 | 如 `成都·武侯区` |
| 离家距离 | 高德地理编码算距，`约Xm` / `X.Xkm`（需 `GAODE_API_KEY`） |
| 查看详情 | 该岗位的**真实详情页 URL** |

### 查看详情 = 真实岗位页（重点）

**"查看详情"必须链接到当前岗位的真实招聘详情页，不是列表页/公司主页/父级页面。**

实现方式（`src/agent/extract.py`）：
- 提取阶段同步从 DOM 采集岗位详情链接（`_collect_card_urls`），两种通道：
  1. **平台专属**：SPA 平台岗位卡无 `<a>` 链接时，从数据属性提取真实 jobId 构造详情 URL（如 51Job 的 `sensorsdata.jobId` → `jobs.51job.com/chengdu/{jobId}.html`，实测可达）
  2. **通用锚点**：岗位标题即锚点文本的平台，锚点文本与标题匹配回填
- 按归一化标题匹配回填（`_merge_card_urls`），全/半角括号、截断标题也能对上
- `sanitize_url` / 报告层 `safe_url` 双重净化：丢弃 LLM 编造的占位符链接（如 `xxxxx.html`），宁缺毋滥；未采到真实链接的岗位显示灰色"暂无链接"，不渲染假跳转

### 数据流

```
agent 抓取 → OCR 读字(RapidOCR) → DeepSeek 结构化 → DOM 采集真实 jobId/href
  → 标题匹配回填 URL → 报告生成器按公司聚合 → 类型大框分组 → HTML 渲染
```

## 数据格式

搜索数据保存在 `data/YYYY-MM-DD/` 目录，HTML 报告保存在 `output/YYYY-MM-DD/`。
