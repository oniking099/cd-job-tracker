# 成都招聘信息定时采集推送系统

每天自动从 12 个招聘平台采集成都地区气象、环境、LLM、AI Agent 领域岗位，经过多层智能筛选，生成精美 HTML 报告，通过 Server酱推送到微信。

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

## 数据格式

搜索数据保存在 `data/YYYY-MM-DD/` 目录，HTML 报告保存在 `output/YYYY-MM-DD/`。
