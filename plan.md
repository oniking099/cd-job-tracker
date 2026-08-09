# 修复卡片内容错配 + 拆分双 HTML 方案

## 问题1：卡片信息与详情 JD 页对不上 + 分类错

### 根因（已用 data/2026-08-07/deduped.json 实测确认）

1. **URL 回填错配（主因）**：`src/agent/extract.py` 的 `_merge_card_urls` 按"标题文本"回填 URL。同标题多公司时 `by_text[title]` 只存第一个 URL，导致多家公司共享同一链接。
   - 实测：BOSS"AI工程师"5 家（伍柒必乐/探路神/步速者科技/蚂蚁搬家/开源智慧城市）全指向同一 zhipin URL；职友集"ai工程师"4 家共享一个 `jobui.com/jobs?` 搜索页 URL（甚至非详情页）。
   - 通用 DOM 通道 `_collect_card_urls` 只采 `{text标题, href}`，不含 company，无法双键匹配。
2. **"集团"误判国企**：`src/filters/classifier.py` 的 `STATE_OWNED_KEYWORDS` 含 `"集团"`，民营"XX集团"被误判。实测：海诺尔环保集团、明信能源集团、中科安环科技产业集团 均被误判国企。
3. **公司名为空**：OCR/DeepSeek 提取遗漏 company（实测多条 company 为空）。

### 修复方案（止血 + 深度重构，用户已选）

#### A. 止血（低风险，立即）

**A1. 移除"集团"宽泛国企关键词** — `src/filters/classifier.py`
- 从 `STATE_OWNED_KEYWORDS` 删除 `"集团"`（保留"市属国企""成都城投""四川能投"等具体国企名关键词）。
- 理由："集团"太宽泛，民营普遍含"集团"；具体国企名才是可靠信号。
- 风险：极低。需检查 `tests/` 无断言依赖"集团"→国企（已查：`test_new_providers.py` 的"成都环境集团"是 LLM 分类测试数据，不走规则；不破坏）。

**A2. 通用 DOM 通道同时采集 company** — `src/agent/extract.py` `_collect_card_urls` 通用分支
- 对每个 anchor，向上找最近的卡片容器，取其中含企业后缀词（有限公司/集团/科技/股份 等）的文本节点作为 company。
- 返回 `{text, href, company}`（51Job/职友集专属采集器同步升级带 company）。
- 理由：有了 company 才能按 (title, company) 双键匹配，根治同标题多公司错配。

**A3. `_merge_card_urls` 改回填逻辑** — `src/agent/extract.py`
- 精确匹配（title==text）仍保留；**同 title 多 URL 候选时**：按 company 双匹配；匹配不上或仍有歧义 → **不回填**（留空，被 `safe_url` 过滤，卡片显示"暂无链接"），宁缺毋滥绝不错配。
- `link.get("company","")` 缺失时退化为标题精确匹配（单候选才回填）——保持 `tests/test_report_urls.py` 的 5 个用例（links 无 company）全通过。
- 兼容：`_merge_card_urls` 是纯函数，测试直接构造 links，签名不变（links 增字段不破坏旧断言）。

**A4. 加固职友集搜索页 URL 过滤** — `src/agent/extract.py` `_collect_jobui_urls` / `_merge_card_urls`
- 实测 `jobui.com/jobs?` 搜索页 URL 混入了 deduped（说明 `_merge_card_urls` 子串兜底 `title in t` 引入了搜索页锚点）。
- 修复：`_collect_jobui_urls` 已只收 `/job/数字/`；在 `_merge_card_urls` 回填后再用 `sanitize_url` 净化（已有 `safe_url` 在报告层兜底，但数据层 `sanitize_url` 也应校验非搜索页）。

#### B. 深度重构（逐平台结构化 DOM 提取，根治错配 + 空公司名）

参照 `src/agent/extract.py` 的 `_extract_yupao_from_dom`（鱼泡范本，从卡片容器同时取 title/company/salary/location/url）。

**B1. 各平台写 `_extract_{platform}_from_dom(page) -> list[dict]`**（优先级按错配严重度）：
- **智联**（已用 Playwright 验证 DOM）：容器 `.joblist-box__item`；标题+URL `a.jobinfo__name`；公司=公司锚点（`/companydetail/` 的 a 文本）；薪资 `.jobinfo__salary`；地点 `.jobinfo__other-info-item`。
- **51Job**：升级现有 `_collect_job51_urls`——sensorsdata JSON 已含 jobId/jobTitle，查是否含 company；若不含从卡片其他 DOM 元素取。
- **职友集**：升级 `_collect_jobui_urls` 带 company（对每个 `/job/数字/` 锚点向上取卡片容器取公司名），或写 `_extract_jobui_from_dom`。
- **BOSS**（错配最重）：`.job-card-wrapper` 卡片，取 `.job-name`/`.company-name`；详情 URL 从卡片数据属性/加密参数构造（`/job_detail/{enc}.html`）。**需 Camoufox+登录态环境调试**（MCP 浏览器遇风控看不到卡片）。
- **猎聘/58/中华英才**：实施时用 agent 环境调试 DOM，参照智联模式。（拉勾 2026-08-09 用户决定放弃：阿里云行为级滑块 WAF 需付费打码+IP 轮换才可能过，不花钱+不浪费 CI 时长，代码在 git 94e3bab 可找回）

**B2. 整合进 `extract_jobs_from_page`** — `src/agent/extract.py`
- 对有专属 DOM 提取器的平台：**优先 DOM 提取**（返回完整列表字段 title/company/url/salary/location），DOM 为空/失败再回退现有 OCR+DeepSeek 链。
- DOM 提取只负责列表字段；responsibilities/requirements 正文仍由 `detail.py` 详情富集（与鱼泡一致）。
- 保证 DOM 失败时回退 OCR 不丢数据（现有鱼泡已是此模式）。

**B3. 风险与验证**
- 各平台 DOM 随改版失效 → 保留 OCR 回退兜底（DOM 失败不中断）。
- BOSS 等需登录态平台，DOM 调试必须在 agent 环境（Camoufox + BOSS_COOKIE），MCP 浏览器无法验证。
- 优先实现并验证：智联（已验证）、51Job、职友集（可基于现有采集器升级）；BOSS/猎聘等随后增量。
- 每个平台提取器加单元测试（mock page.evaluate 返回，断言三元组对齐）。

---

## 问题2：拆分为两个 HTML（用户已选：兜底归行业类、推送放 2 链接）

### C1. 新建分类模块 `src/filters/jd_category.py`
- `classify_jd_category(job) -> "industry" | "professional"`
- `INDUSTRY_KW`：气象、大气科学、大气、大气环境、气候、数值预报、天气预报、大气物理、大气探测、环境、环保、生态、生态环境、碳中和、水环境、固废、大气治理、环境影响评价、水文、环境监测 等。
- `PROFESSIONAL_KW`：AI、人工智能、agent、智能体、大模型、LLM、深度学习、机器学习、NLP、自然语言处理、AIGC、RAG、生成式AI、多模态、强化学习、MLOps、计算机视觉、算法（AI 上下文）等。
- 逻辑：含 INDUSTRY_KW → industry；否则含 PROFESSIONAL_KW → professional；**都不含 → industry（兜底，用户拍板）**。
- 判断文本：`title + responsibilities + requirements`。
- 新增单元测试覆盖三类 + 兜底。

### C2. 抽出渲染内部函数 — `src/report/generator.py`
- 抽 `_render_report_html(jobs_subset, date_str, total_count) -> html_str`：现有渲染逻辑（`_group_by_company` + 模板渲染）原样搬入，**不改模板/排序/CATEGORY_STYLE/CATEGORY_ORDER**。
- `generate_report(jobs, target_date)` 改为：
  - 按 `classify_jd_category` 分两组（industry / professional）。
  - 各调一次 `_render_report_html`，写出：
    - `output/{date}/report-industry.html`（行业类 JD）
    - `output/{date}/report-professional.html`（专业类 JD）
  - **模板 `report.html` 完全不改**（UIUX 绝对不变）；两个 html 仅岗位数量不同。
  - 返回 `{industry: path, professional: path}`（或两路径）。

### C3. 推送改造 — `src/notify/serverchan.py` `push_report`
- 构造 2 个 URL：`{GITHUB_PAGES_BASE}/output/{date}/report-industry.html` + `.../report-professional.html`。
- 推送消息"📄 点我查看卡片版网页报告"改为 2 行链接：
  - `📄 [行业类JD报告（大气/气象/环保）](industry_url)`
  - `📄 [专业类JD报告（AI/大模型）](professional_url)`
- **其余摘要内容/格式/选岗逻辑完全不变**（企业类型分组、每类前3岗位摘要基于全部 valid_jobs，不拆分）。
- 标题更新岗位总数（仍基于全部 valid_jobs）。

### C4. 串接 — `src/pipeline.py` `run_report`
- `generate_report` 返回 2 路径 → `push_report` 接收 2 URL（签名调整）。
- 其余流程（去重、过滤、详情富集、LLM 分类、距离计算）不变。

### C5. 兼容
- `generate_report` 签名变更：同步 `pipeline.run_report`（唯一调用方）；`tests/` 无直接测 `generate_report`（已查），不破坏。
- `push_report` 签名变更：同步 `pipeline.run_report`。
- GitHub Pages 路径：旧的 `report.html` 不再生成；如需兼容旧链接可保留一个重定向，但用户未要求，不做。

---

## 实施顺序

1. **止血**（A1-A4）：classifier 移除"集团"、DOM 通道带 company、`_merge_card_urls` 双匹配/不回填、jobui 搜索页 URL 加固。跑 `tests/` 全绿。
2. **拆分 HTML**（C1-C5）：jd_category 分类、generator 拆分渲染、push 2 链接、pipeline 串接。跑 `tests/`。
3. **深度重构**（B1-B3）：逐平台 DOM 提取器，优先智联（已验证）→ 51Job → 职友集 → BOSS → 猎聘/58/中华英才（拉勾 2026-08-09 已放弃）。每个加测试，DOM 失败回退 OCR。

每步独立可验证、可回滚。先止血上线见效，深度重构增量推进。
