# chengdu-job-tracker

## 🔴 会话启动（SessionStart hook）

**每次会话启动时，SessionStart hook 自动注入项目上下文。** 这不是真正的"崩溃恢复"，只是帮你快速了解项目状态。

1. 注意系统提示中的 `[会话恢复系统 — 自动注入]` 块
2. 检查 `memory/session-state.md` 了解当前进度
3. 如果待办列表有未完成任务，主动告知用户

## 🔴 MCP / Skills 强制规则

**以下规则优先级最高，覆盖所有默认行为。**

### 核心原则：逐条过，命中的全用，不选其一

**回答任何问题之前，必须逐条过一遍以下清单。一个用户问题可能同时命中多条 → 全部调用，不挑。**

### 🔴 WebSearch 优先原则（最高优先级）

**只要命中以下任意一条判定条件，整个问题的所有环节：**
1. **必须先执行 `WebSearch`**（收集外部信息、验证假设、确认最新状态）
2. **再结合本地代码/文件做推理组合**
3. **绝对禁止跳过 WebSearch 直接凭内部知识推理回答**

此规则无论问题大小、简单复杂，**没有例外**。即使你"确定知道"答案，也必须先搜——搜索结果可能推翻你的假设。

| 判定条件（满足即适用） | 必须使用的 MCP/Skill |
|---|---|
| 涉及第三方库/框架/CLI 的用法、版本、兼容性？ | `context7` 查文档 |
| 涉及报错、bug、API 变化、新闻、外部信息？ | `WebSearch` 搜最新状态 |
| 需要多步骤推理、多因素权衡、复杂决策？ | `sequential-thinking` 结构化拆解 |
| 涉及 GitHub 操作（PR/Issue/文件/搜索）？ | GitHub MCP，**禁止绕 `gh` CLI** |
| 涉及浏览器/页面操作？ | Playwright 或 chrome-devtools MCP |
| 涉及 UI/设计/Figma？ | Figma MCP |
| 需要搜索本地文件？ | `everything-search`，**禁止用 bash `find`/`ls`** |
| 属于可复用的工作流（定时任务、部署、审查）？ | 对应 Skill |
| 涉及数据分析/可视化/图表？ | `claude-101` 相应工具 |
| 需要获取网页实际内容？ | `fetch` MCP |

**判断时在 `thinking` 中输出每条的自检结果（命中/不命中/原因），然后一次调用所有命中的 MCP/Skill。**

### 只有以下场景可以不使用 MCP/Skills（白名单，穷举制）

| 可跳过场景 | 示例 |
|---|---|
| 纯文件读写 | `Read` 看代码、`Write`/`Edit` 改文件 |
| 纯 git 本地操作 | `git status`、`git log`、`git diff` |
| 语言基础语法 | "Python dict 怎么遍历"、"JS 箭头函数语法" |
| **已经在当前对话中通过 MCP 查过的信息** | 同一个库连续追问，不需要反复查 |

满足白名单某一条 → 该条对应的 MCP/Skill 可跳过，但**其他条件仍需逐条过**。不在白名单内的 → 绝不跳过。

### 如果判断"不需要 MCP/Skill"

必须在 `thinking` 中明确说明理由（基于白名单中的哪一条），不能默默跳过。

### Skills 触发规则

Skills 声明了 `TRIGGER` 条件的，只要匹配就必须调用 `Skill` 工具，不允许绕过。

## 通用规则

- **语言**：用中文回复，代码和术语保留英文
- **代码风格**：先看项目现有风格，保持一致
- **注释**：关键逻辑加注释，不要废话注释
- **提交**：用约定式提交（feat/fix/docs/chore/refactor）
- **修改前**：先读文件再改，不要猜测代码内容
- **修改后**：自审一遍 diff，确保没有意外改动
- **报错**：贴完整的错误信息，不要摘要

## 技术栈
- 待定（新项目）

## 编码规范

### 文件命名
- 组件：PascalCase（`UserList.tsx`）
- 工具函数：camelCase（`formatDate.ts`）
- 目录：kebab-case（`user-profile/`）
- 常量：UPPER_SNAKE_CASE

### TypeScript
- `strict: true`
- 优先 `interface` 而非 `type`（对象形状）
- 禁止 `any`，用 `unknown`
- 函数必须声明返回值类型

### 前端
- 移动端优先（mobile-first）
- 组件控制在 200 行以内
- 交互元素必须有 hover/focus 状态
- 图片必须设 `alt`

### 后端
- 接口统一分页（page/pageSize）
- 错误返回统一格式 `{ error: { code, message } }`
- 敏感信息只在 env 变量中
- 输入校验用 Zod/class-validator

### 爬虫
- 先看 robots.txt
- 请求间隔 ≥ 1 秒
- 带 User-Agent 标识
- 数据缓存本地避免重复请求

## 项目结构约定
```
src/
├── components/   # UI 组件
├── lib/          # 工具函数
├── types/        # TypeScript 类型
└── tests/        # 测试文件
```

## 环境信息
- **系统**：Windows 11
- **包管理**：pnpm > npm
- **镜像**：npm→npmmirror.com, pip→清华, uv→清华
- **Everything**：C:\Program Files (x86)\Everything（文件搜索可用）
- **可用 MCP**：playwright, sequential-thinking, context7, fetch, github, figma, everything-search, chrome-devtools, claude-101
