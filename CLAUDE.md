# chengdu-job-tracker

## 🔴 会话启动（SessionStart hook）

**每次会话启动时，SessionStart hook 自动注入项目上下文。** 这不是真正的"崩溃恢复"，只是帮你快速了解项目状态。

1. 注意系统提示中的 `[会话恢复系统 — 自动注入]` 块
2. 检查 `memory/session-state.md` 了解当前进度
3. 如果待办列表有未完成任务，主动告知用户

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
