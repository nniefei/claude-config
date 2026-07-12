---
name: util-init
description: 项目初始化,识别技术栈并建立上下文
user-invocable: true
argument-hint: ""
depends-on: []
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
version: 1.1.1
last-updated: 2026-07-04
---

# 项目初始化 Skill

> 当本模板复制到新项目时,一键完成初始化：识别技术栈、扫描项目结构、写入 Memory。

## 使用方式
`/util-init` — 执行项目初始化

## 执行步骤

### 1. 检测初始化状态

> 路径说明：以下 `memory/` 均指系统提供的项目 memory 路径（每次会话系统会自动告知当前路径）。

读取 `memory/MEMORY.md`,检查是否已有技术栈记录：
- **已初始化** → 提示"项目已初始化,是否重新扫描？"
- **未初始化** → 继续执行

### 2. 识别技术栈

自动扫描以下文件来推断技术栈：

| 扫描文件 | 识别内容 |
|:---|:---|
| `package.json` | 框架(Vue/React/Angular/Svelte)、UI库、构建工具、测试框架 |
| `tsconfig.json` | TypeScript 配置 |
| `vite.config.*` / `webpack.config.*` | 构建工具和插件 |
| `.eslintrc*` / `prettier*` | 代码规范工具 |
| `pnpm-lock.yaml` / `yarn.lock` / `package-lock.json` | 包管理器 |
| `docker*` / `.github/workflows/*` | 部署方式 |
| `*.py` / `requirements.txt` / `go.mod` / `Cargo.toml` | 后端语言 |

### 3. 扫描项目结构

使用 `Glob` 扫描目录结构，识别：
- 源码目录结构（src/ 下的分层）
- 组件组织方式（按功能/按页面）
- 状态管理方案
- 路由结构
- API 层封装方式

### 工具失败兜底

当 `Glob` 或 `Grep` 因本地 `rg` 缺失、路径异常等原因失败时，改用 `Bash` 执行只读 Python 脚本完成同等扫描：
- 使用 `pathlib.Path.rglob()` 查找配置文件、源码目录和项目结构
- 使用 Python 读取文件内容并匹配技术栈关键词
- 只允许读取、统计和匹配，不修改、不删除任何项目文件
- 识别结果仍必须经过用户确认后才写入 Memory

### 4. 确认结果

使用 `AskUserQuestion` 展示识别结果，让用户确认或补充：
```
📋 项目技术栈识别结果：

- 框架：Vue 3 + TypeScript
- UI 库：Element Plus
- 构建：Vite 5
- 包管理：pnpm
- 状态管理：Pinia
- 测试：Vitest
- 规范：ESLint + Prettier

有需要补充或修正的吗？
```

### 5. 写入 Memory

将确认后的技术栈信息写入 `memory/MEMORY.md` 的"技术栈"部分。

### 6. 输出初始化报告

```
🎉 项目初始化完成喵！

### 技术栈
- 框架：XXX
- UI 库：XXX
- ...

### 项目结构概览
- `src/components/` — 公共组件
- `src/views/` — 页面组件
- `src/stores/` — 状态管理
- ...

### 已写入 Memory
- `memory/MEMORY.md` — 技术栈摘要

现在可以开始使用其他 Skill 了喵！
```

## 注意事项
- 如果项目没有 `package.json` 等标志性文件,通过 `AskUserQuestion` 直接询问用户
- 不覆盖 `MEMORY.md` 中已有的其他内容,只更新"技术栈"部分
- 识别结果必须经过用户确认才写入 Memory

---

## 附录：技术栈识别规则详解（Plan-3 合并自 tech-stack.md）

### 多语言项目配置文件优先级

| 优先级 | 配置文件 | 项目类型 |
|:---:|:---|:---|
| 1 | `package.json` | Node.js / 前端 |
| 2 | `pyproject.toml` / `requirements.txt` | Python |
| 3 | `pom.xml` / `build.gradle` | Java |
| 4 | `go.mod` | Go |
| 5 | `Cargo.toml` | Rust |

### 框架识别细则

**Vue 项目**：依赖含 `vue` / 存在 `*.vue` 文件 / 存在 `vite.config.js` 或 `vue.config.js`
**React 项目**：依赖含 `react` / 存在 `*.jsx` 或 `*.tsx` 文件
**Angular 项目**：依赖含 `@angular/core` / 存在 `angular.json`

### 构建工具识别细则

**Vite**：依赖含 `vite` / 存在 `vite.config.{js,ts}`
**Webpack**：依赖含 `webpack` / 存在 `webpack.config.js`
**Rollup**：依赖含 `rollup` / 存在 `rollup.config.js`

### 状态管理识别细则

**Redux**：依赖含 `redux` 或 `@reduxjs/toolkit`
**Vuex**：依赖含 `vuex`
**Pinia**：依赖含 `pinia`
**MobX**：依赖含 `mobx`

### 写入 MEMORY.md 的格式示例

```markdown
## 技术栈

### 编程语言
- TypeScript 5.0

### 前端框架
- Vue 3

### UI 库
- Ant Design Vue 4.x

### 构建工具
- Vite 4.x

### 包管理器
- npm 9.x

### 测试框架
- Vitest
- Vue Test Utils

### 开发工具
- ESLint
- Prettier
- Husky（Git hooks）

### 项目结构
- src/
  - components/（Vue 组件）
  - views/（页面组件）
  - stores/（Pinia 状态管理）
  - utils/（工具函数）
  - api/（API 调用）
- tests/（测试文件）
- public/（静态资源）

### 特殊配置
- TypeScript 严格模式启用
- ESLint + Prettier 集成
- Git hooks（pre-commit）
```

### 特殊场景处理

**Monorepo 项目**（存在 `lerna.json` / `pnpm-workspace.yaml` / 多个 `package.json`）：
- 识别根目录的技术栈
- 列出所有子项目及其技术栈
- 在 MEMORY.md 中标注"项目类型：Monorepo"

**多语言项目**（同时存在 `package.json` + `requirements.txt` 等）：
- 分别识别每种语言的技术栈
- 在 MEMORY.md 中按"前端（X）/后端（Y）"分段列出

**微服务项目**（存在 `docker-compose.yml` 或 `kubernetes/` 目录 + 多个独立服务目录）：
- 识别每个服务的技术栈
- 标记为"微服务项目"

### 常见问题

| 问题 | 处理 |
|:---|:---|
| 依赖中有不认识的包 | 直接列出包名，不要猜测，让用户确认 |
| 版本信息 | 从配置文件提取，格式 `框架名 版本号`（例：`Vue 3.3.4`）|
| 私有依赖 | 标注"私有依赖"，不尝试访问私有仓库 |
| 开发 vs 生产依赖 | 不强行区分，只列出对项目理解重要的 |
