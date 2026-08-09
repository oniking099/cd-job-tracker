# 会话状态（2026-08-09 更新——崩溃恢复已完成 ✅）

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
