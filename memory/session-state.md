# 会话状态（2026-08-09 更新——崩溃恢复已完成 ✅）

# 以下为 2026-08-09 20:30 记录（拉勾完整移除 ✅）

## 拉勾平台完整移除（用户拍板：不花钱 + 不浪费 GitHub Actions 时长）
- **决定**：拉勾 阿里云行为级滑块 WAF 需付费打码+IP 轮换才可能过；用户明确「不花钱 + 不能浪费 GitHub 云端服务器时长」→ 完整移除，不保留空转占 CI。
- **删除**：`src/scrapers/lagou.py`（旧 LagouScraper）、`tests/test_lagou_extract.py`（6 测试）
- **编辑**：models.py 删 LAGOU 枚举；__init__.py 删 4 处注册（import/AGENT 导入/ALL_SCRAPERS/AGENT_SCRAPERS）；agent_scraper.py 删 LagouAgentScraper；extract.py 删 `_extract_lagou_from_dom`+注册表；verify_extractors.py 删 import+平台表；test_agent/capture_session/probe_platform 删 key 与示例；README 13→12 平台删行；report.html 尾部删来源；search.yml/verify.yml 门槛注释与步骤名改「(BOSS直聘/58同城)」；plan.md 标记已放弃
- **验证**：pytest **224 全绿**（230−6）；YAML 两 workflow 解析 OK；导入冒烟：12 平台 / DOM 提取器 6 个（鱼泡/智联/猎聘/中华英才/58/BOSS）/ verify 平台表仅 58+BOSS
- **找回**：全部拉勾代码在 git commit **94e3bab**，未来若想复活可 checkout 恢复
- ⚠️ 未提交：本批次（B1 提取器 + verify 门槛 + 拉勾移除）仍挂在 wip 分支，等用户拍板 commit → 推送后今晚 CI 自动验证 BOSS(Camoufox)/58

---

# 以下为 2026-08-09 19:30 记录（复核沉淀成自动化，结束"每次改代码重做复核"）

## 提取器复核 → 脚本 + CI 门槛（用户拍板选方案A）✅
- **问题**：验证对象=提取器代码本身，改选择器/逻辑→旧验证对不上新代码，所以感觉"每次改代码都要重做"。根因=复核没沉淀成可复用资产。
- **方案**：新建 `scripts/verify_extractors.py`（可重跑一条命令）：
  - 真实加载平台页 → 直接调 `_DOM_EXTRACTORS[platform]`（不经 OCR/视觉兜底，验证提取器本身）
  - 结构性断言：标题填充率≥90% + URL 真实同域无 javascript: 占位（错配根因检查）
  - verdict：PASS / BLOCKED(平台风控非bug) / SKIP(本地无Camoufox) / **FAIL(真bug,退出码1)**
  - 平台表：58同城(domains=58.com, chromium)、BOSS直聘(zhipin.com, Camoufox)（拉勾 20:30 已随移除而删除）
  - 坑已修：普通 scraper 的 context 是 None，`_new_context()` 后才 new_page；Camoufox `_new_context` 幂等
- **CI 门槛**：
  - `search.yml` 加 "Verify DOM extractors" 步骤（每晚 5 轮前跑，FAIL 亮红）
  - 新建 `.github/workflows/verify.yml`：**push/PR 时跑 pytest+verify_extractors** —— 以后改代码自动复核，不再手工
- **本地实测 verdict**（2026-08-09）：58=BLOCKED（本机 IP 被 58 按 IP 验证码墙：`请输入验证码 ws:39.144.199.75`，非提取器 bug；早先 Playwright MCP 真浏览器 30 卡 0 缺失已验证过）、BOSS=SKIP（本地无 Camoufox，CI 会跑）、拉勾=BLOCKED（滑动验证 WAF）
- **关键结论**：58/BOSS/拉勾 的验证都依赖网络/IP（本机 IP 已被 58/BOSS 风控、拉勾 WAF），本地装 Camoufox 也绕不开 → **只有 CI（GitHub runner 干净 IP + Camoufox + BOSS_COOKIE）能验证**，这正是 verify.yml + search.yml 门槛存在的意义。今晚 CI 自动出 verdict。
- 测试：pytest **230 全绿**；两个 workflow YAML 已 yaml.safe_load 验证合法

---

# 以下为 2026-08-09 18:50 记录（B1 提取器三平台收尾）

## plan B1：拉勾/58/BOSS 提取器收尾 ✅（未提交，等用户拍板）
- ✅ **58同城实页验证成功**（2026-08-09 Playwright MCP 实测 `m.58.com/cd/job/` 频道页）：
  - 30 卡片 title/company/url/salary/location **0 缺失**、URL 0 重复/0 非 58.com，详情页真实可达（含完整 JD：岗位职责/任职资格/薪资/公司）
  - `_extract_wuba_from_dom` 重写为多模式：①wap 频道页 `a.list-item-a.tcb_list_item_link`+`.info-title/.info-salary/.company/.local_quXianName`（**地点补"成都"前缀**，否则 `_filter_city` 会把全站岗位误剔）②wap sou 页 `li>a>dl>dt.tit strong`（无公司/薪资，留空不伪造）③桌面回退旧选择器
  - `WubaAgentScraper`：`build_start_url` 改 `https://m.58.com/cd/job/`（桌面 cd.58.com 对自动化 302 登录墙）；task_template 提示 agent 别点 wap 搜索框（无关键词过滤，key 参数不生效）
- ✅ **BOSS提取器实现**：`_extract_boss_from_dom`（SSR `__NEXT_DATA__/__INITIAL_STATE__` jobList 优先，encryptJobId 拼 `job_detail/{eid}.html`，jobLabels→requirements、bossInfo.online→hr_active；DOM `li.job-card-wrapper` 回退），已注册 `_DOM_EXTRACTORS["BOSS直聘"]`
  - ⚠️ **本机无法实页验证**：出口 IP 被 BOSS 风控拦"当前 IP 地址可能存在异常访问行为"；需 CI/Camoufox 环境实跑复核
- 拉勾 `_extract_lagou_from_dom`（此前已写）：⚠️ 同样本机无法实页验证（阿里云滑动验证 WAF 全挡），需 CI 复核
- 测试：`tests/test_wuba_extract.py` 文档更新（已验证）；新建 `tests/test_boss_extract.py`（6 个，mock 镜像 SSR→输出字段映射+内部回退分支）
- **pytest 230 全绿**
- ⚠️ 三平台验证状态总结：**58=实页验证成功**；拉勾/BOSS=选择器沿用旧 scraper 已验证路径，但本机 WAF/IP 风控看不到卡片，首次 agent/CI 实跑后需复核命中率（DOM 提取失败自动回退 OCR 兜底，不会比现在差；但 OCR 有幻觉风险=虚假数据，见 Actions 后果说明）

---

## 恢复结果
- tmp 残留恢复（猎聘+中华英才提取器）**此前已完成**：extract.py 744 行，`_extract_liepin_from_dom` / `_extract_chinahr_from_dom` 已注册进 `_DOM_EXTRACTORS`，tmp 文件已消失
- 本会话补齐 plan B3 测试：新建 `tests/test_liepin_chinahr_extract.py`（8 个用例：三元组对齐/异常回退/空返回/集成 DOM 优先 ×2 平台）
- **pytest 158 全绿**
- ✅ 已修（用户拍板）：同公司同标题不同地点/编制不再被压成 1 条——`_dedup_jobs` key 改 `company|title|location|url`；`_dict_to_job` job_id 哈希加 location+url（防下游 dedup_key 再压）。新增 5 测试
- ⚠️ 残留边界（未改，已告知用户）：`cross_platform_key`=公司:标题:地点 不含 URL——同公司同标题**同地点**不同编制的两条在 `deduplicate_all` 层仍会被跨平台键合并（该键故意不含 URL，否则跨平台去重失效）
- ✅ C1 偏差已解决（用户拍板要精细版）：jd_category 改 标题+正文 合并判定。正文降噪设计：行业词只收复合词（排除裸 环境/大气/生态），专业拉丁词单词边界正则（email/detail 里的 ai 不命中）。pytest 166 全绿。08-07 实数据重分类：46/39 → **40/45**（6 岗位翻转，已逐一审计）。已重新生成两份报告
- ⚠️ 精细版已知边界：正文提及 AI 但岗位本身非 AI 的会归专业——用户拍板**保持现状**（方案 1：提及即算）
- ✅ 报告层排除规则（2026-08-09 用户要求）：新建 `src/filters/report_exclusion.py`，generate_report 拆分前过滤（两份报告同时生效）：
  - 领域排除：医学/法律/游戏/证券/电商/餐饮（匹配 标题+公司名，制造/服务两侧都剔）
  - 高薪排除：月薪下限 ≥**2.9万** 一律剔（用户 2026-08-09 由 3万 下调；年薪"/年"折月；面议/乱码保留）
  - pytest 214 全绿；08-07 实数据：剔 6 条（临床×3、电商×1、3-6万×2），85→79（行业 37/专业 42），报告已重新生成（阈值下调对本数据集无新增剔除——无落在 [2.9,3) 万下限的岗位）
- ⚠️ 范围说明：排除只在 generate_report 生效；Server酱推送摘要（pipeline valid_jobs）不受影响——用户未要求，未动
- ⚠️ 环境坑：本机 `Temp\pytest-of-pc` 有 ACL 限制（WinError 5），测试里别用 pytest tmp_path，用 tempfile.mkdtemp

---

---

# 以下为 18:45 记录

## 当前任务
plan.md「修复卡片内容错配 + 拆分双 HTML 方案」——上次会话在 DOM 重构阶段崩溃，本会话已接管盘点。

## 已完成（全部验证通过）
- **止血 A1-A4**：
  - A1 classifier.py 移除宽泛"集团"国企关键词（保留具体国企名）
  - A2 `_collect_card_urls` 通用 DOM 通道采集 company（卡片容器向上 6 层找公司锚点 + 企业后缀词兜底）
  - A3 `_merge_card_urls` 改双键匹配：同 title 多候选按 company 消歧，歧义/无匹配不回填（宁缺毋滥）
  - A4 `sanitize_url` 拦截 `jobui.com/jobs` 搜索页 URL；`_collect_jobui_urls` 只收 `/job/数字/`
- **拆分双 HTML C1-C5**：
  - C1 `src/filters/jd_category.py`（行业类/专业类分类，兜底归行业类）
  - C2 generator.py 抽 `_render_report_html`，`generate_report` 返回 `{industry, professional}` 两路径
  - C3 serverchan.py 推送 2 条报告链接
  - C4 pipeline.py 串接；demo_report.py 同步适配
  - ⚠️ 与 plan 的偏差：plan 写"判断文本=title+responsibilities+requirements"，实际实现**只匹配 title**（注释里说明理由：避免"办公环境"噪声误判）——疑似崩溃前有意调整，待用户确认
- **深度重构 B（部分）**：
  - 智联 `_extract_zhilian_from_dom`：优先 `window.__INITIAL_STATE__` SSR JSON，回退 DOM 卡片选择器（旧 sou + 新 www 两套）；已注册进 `_DOM_EXTRACTORS`
  - 51Job `_collect_job51_urls` / 职友集 `_collect_jobui_urls` 已升级带 company

## 验证状态
- `pytest tests/` 145 全绿
- 用 data/2026-08-07/deduped.json 实数据重新生成：85 条 → 行业类 46 / 专业类 39，`output/2026-08-07/report-industry.html` + `report-professional.html` 生成成功，total_count 渲染正确（旧 report.html 保留未删）

## 待办（plan 剩余项）
- [ ] BOSS `_extract_boss_from_dom`：错配最重，但**需 Camoufox + BOSS_COOKIE 登录态环境调试**，MCP 浏览器遇风控看不到卡片，本地无法验证
- [ ] 猎聘/拉勾/58/中华英才 DOM 提取器（增量，参照智联模式，需 agent 环境调 DOM）
- [ ] 未提交：6 个改动文件 + 3 个新文件（plan.md/jd_category.py/2 个测试）等用户拍板是否 commit

---

# 以下为 2026-08-09 15:50 记录（GitHub 备份完成）

## 防重启丢工作
- ✅ `wip/report-split-exclusion` 已推送 GitHub（commit cd2ac45，与远端一致）
- ✅ 本仓库 git 已固化 Clash 代理 `http(s).proxy=http://127.0.0.1:7890`（repo-local）——之前 push 卡死根因是 git 不走系统代理
- ⚠️ 远端 main 已被定时 Actions 推进到 43291ee（本地未拉取），wip 合回 main 前需先 fetch + rebase
- 习惯要求：每个小里程碑 commit+push（走代理增量秒级）

---

# 以下为 2026-08-09 16:10 记录（拉勾提取器完成）

## plan B1 增量：拉勾 DOM 提取器 ✅
- `_extract_lagou_from_dom`：SSR JSON（__INITIAL_STATE__.positionResult）优先 + DOM 锚点扫描回退，已注册 _DOM_EXTRACTORS["拉勾"]
- ⚠️ **未经实页验证**：拉勾搜索页对 MCP 浏览器弹滑动验证（2026-08-09 实测），字段路径沿用旧 lagou.py；首次 agent 实跑需复核选择器
- 测试 tests/test_lagou_extract.py 6 个；pytest 220 全绿
- 剩余：58同城（同样可能有风控，先探）、BOSS（需 Camoufox+登录态）

---

# 以下为 2026-08-09 16:25 记录（58提取器完成，B1 本地可做部分收尾）

## plan B1 增量：58同城 DOM 提取器 ✅
- `_extract_wuba_from_dom`：卡片容器直取（li.job_item/div.job-list-item 等旧验证选择器），已注册 _DOM_EXTRACTORS["58同城"]
- ⚠️ **未经实页验证**：cd.58.com 搜索页对 MCP 浏览器直接跳 passport 登录墙（m版重定向错乱），选择器沿用旧 wuba.py；首次 agent 实跑需复核
- 测试 tests/test_wuba_extract.py 4 个；pytest 224 全绿
- 风控实测结论（2026-08-09）：拉勾=滑动验证、58=强制登录、BOSS=风控——MCP 浏览器全部看不到卡片
- 剩余：仅 BOSS（错配最重，需 Camoufox+BOSS_COOKIE 登录态环境调试）
- 拉勾/58 提取器首次 agent 实跑后需复核命中率（提取失败自动回退 OCR，不会比现在差）
