# 🐱 Claude Code 私有配置 · 俏皮猫娘版

> 主人定制的 Claude Code 工作规范、安全守卫、自动化工具与健康自检体系。
>
> 版本号以 [`CHANGELOG.md`](CHANGELOG.md) 顶部 `## [x.y.z]` 为准（**Single Source of Truth**）。

---

## 📋 目录总览

```
~/.claude/
├── CLAUDE.md              # 【最高优先级】全局工作规范（身份/模式/规则加载/Memory）
├── README.md              # ← 本文件：项目总览与导航
├── CHANGELOG.md           # 完整版本历史（56KB）
├── MIGRATION.md           # 跨机器/跨系统环境迁移指南
│
├── settings.json          # Claude Code 主设置（API token、模型、环境变量）
├── settings.local.json    # 本地设置（权限 allowlist、hook 注册）
├── env.json               # 环境配置（python_exe、os_type、debug 开关）
│
├── hook-runner.cmd        # Windows Hook 调度入口（自定位）
├── hook-runner.sh         # Unix Hook 调度入口（自定位）
├── scripts/
│   ├── hook-runner.py     # Hook 调度器——读 env.json → 分发到目标 hook 脚本
│   └── migrate-env.py     # 环境迁移辅助脚本（自动检测 OS、生成 settings.local.json）
│
├── skills/                # 技能与规则系统（核心）
├── projects/              # 项目会话数据（含跨会话持久 memory）
├── logs/                  # 运行时日志（审计/性能/健康检查）
├── backups/               # settings.json 自动备份
├── plugins/               # 官方插件市场克隆
├── sessions/              # 会话快照
├── shell-snapshots/       # Shell 环境状态快照
├── file-history/          # 文件变更历史记录
├── telemetry/             # 遥测数据
├── cache/                 # 缓存
├── plans/                 # 工作计划
├── tasks/                 # 后台任务数据
├── session-env/           # 会话级环境变量
├── .grants/               # 授权文件目录（hook 放行凭证）
│
├── .gitignore             # 排除 secrets、运行时产物、日志等
├── .gitattributes         # 统一 LF 换行符
└── .last-cleanup          # 上次清理时间戳
```

---

## 🚀 快速上手

### 初次部署

迁移部署步骤详见 [`MIGRATION.md`](MIGRATION.md)（6 步完整流程）。

### 日常使用

| 场景 | 操作 |
|:---|:---|
| 开始任务 | 直接描述需求，AI 会自动判断模式 |
| 静默执行 | prompt 以 `[silent]` 或 `[静默]` 开头 |
| 健康自检 | `/util-check` |
| 查看 memory | `/util-memory list` |
| 保存会话 | `/util-session save` |
| 项目初始化 | `/util-init` |

### 授权机制

当安全 hook 拦截一个操作时，有三种放行方式：

1. **ask 弹窗**（默认）→ 点 Allow 放行当前操作
2. **一次性 grant** → 主人输入 `! touch ~/.claude/.grants/<category>`
3. **会话级 grant** → 主人输入 `! touch ~/.claude/.grants/<category>.session`（整个会话有效）
4. **环境变量** → 启动前设 `CLAUDE_HOOK_APPROVED_<CATEGORY>=1`

> ⚠ **重要**：AI 永不能自建 grant！写 `.grants/` 目录是 exit 2 硬阻断。

### 故障排查

| 症状 | 排查方向 |
|:---|:---|
| Bash 命令被意外阻断 | 看 stderr 的"拦截原因"。危险命令默认走 ask/Allow 弹窗，不需预先创建 grant；只有需预授权、无弹窗可用、或 hook 明确提示时，才创建 `.claude/.grants/<category>` 一次性文件或设 `CLAUDE_HOOK_APPROVED_<CATEGORY>=1`。多类别命令需逐个建 grant |
| Write/Edit 被阻断 | 看 stderr 的"Blocked reason(s)"。主人执行 `! touch ~/.claude/.grants/control-plane`（一次性）或 `.session`（会话级），输入 `!` 命令后会自动重试。也可在启动 Claude Code 的真实终端预设 `CLAUDE_HOOK_APPROVED_CONTROL_PLANE=1`（会话内 Bash export 对 hook 子进程无效）|
| `/util-check` 报错 | 按 ERROR 提示逐项修复；常见为引用不存在的文件、frontmatter 缺字段 |
| Session 启动慢 | 看 `logs/health-check-startup.jsonl` |
| 写 `.grants/` 被拒绝 | **设计如此**：AI 写 grant 目录一律 deny，主人走 `! touch` 创建。AI 永不自建 grant |

---

## 🧩 核心组件详解

### 1. 主规范入口 — `CLAUDE.md`

~72 行的路由器文档，定义了：

- **身份设定**：俏皮猫娘风格 AI 编程伙伴
- **三种工作模式**：快速爪击 / 标准 / 独自巡猎（静默）
- **规则按需加载**：触发场景时主动 Read 对应规则文件
- **核心约束指引**：指向 `skills/rules/core-principles.md`（全局底线）与 `skills/rules/karpathy-guidelines.md`（行为准则，标准/静默模式自动注入常驻 5 条）

> 这是最高优先级的规范文件，系统默认行为被显式覆盖。

### 2. 安全守卫系统 — `skills/util-safety/`

最复杂的子系统，约 **12 个脚本（8 Hook + 4 共享模块）+ 14 个测试文件**。

#### 守卫 Hook（fail-closed — 崩了就阻断）

| Hook 脚本 | 职责 |
|:---|:---|
| `bash-safety-wrapper.py` | Bash 命令安全审查（38KB）——10 大 grant 类别、50 条危险模式、git 子命令深度解析、secret 打码 |
| `write-safety.py` | Write/Edit 安全审查（22KB）——敏感路径阻断、secret 扫描（熵阈值 4.0）、symlink 绕过防护 |
| `mcp-safety.py` | MCP 调用安全审查——三层策略（精确黑名单 + 动词模式 + 未知动词 ask）、secret 扫描 |
| `mcp-audit.py` | MCP 审计日志记录 |

#### 增强 Hook（fail-safe — 崩了静默跳过）

| Hook 脚本 | 职责 |
|:---|:---|
| `rule-loader.py` | 规则按需注入——检测触发场景、写 `additionalContext`、维护去重缓存（LRU 200 + TTL 1h） |
| `session-start.py` | 会话启动初始化——清理旧 session grant、后台跑健康检查、每天最多一次完整检查 |
| `bash-audit-post.py` | Bash 执行后审计——危险命令实际执行后记录审计日志 |
| `write-audit.py` | Write/Edit 执行后审计——记录路径哈希 + 行数 |

#### 共享模块

| 模块 | 用途 |
|:---|:---|
| `_shared_patterns.py` | 危险模式定义（secret 正则、git 危险操作、路径黑名单） |
| `_audit_log.py` | 审计日志基础（文件锁、轮转、归档保留 10 个） |
| `_load_patterns_utils.py` | 模式加载工具函数 |
| `platform_utils.py` | 跨平台工具（Windows pythonw / Unix 差异封装） |

#### 配置

- `config/mcp_blocklist.json` — MCP 黑名单与动词模式规则
- `HOOK-CONVENTIONS.md` — Hook 编写约定（失败策略/超时/stderr 输出规范）

#### 测试套件（14 个测试文件）

| 测试文件 | 覆盖 |
|:---|:---|
| `test_bash_safety.py` | Bash 安全审查（31KB，大量回归用例） |
| `test_write_safety.py` | Write/Edit 审查（32KB，含 symlink 绕过） |
| `test_mcp_safety.py` | MCP 安全三层策略 |
| `test_mcp_risk_level.py` | MCP 风险等级分类 |
| `test_mcp_three_tier.py` | MCP 三层决策逻辑 |
| `test_mcp_grant_concurrency.py` | MCP grant 原子消费并发测试 |
| `test_mcp_audit.py` | MCP 审计日志 |
| `test_rule_loader.py` | 规则注入器 |
| `test_rule_loader_ttl.py` | 规则缓存 TTL 过期 |
| `test_session_start.py` | 会话启动清理 |
| `test_audit_log_concurrency.py` | 审计日志并发写入 |
| `test_bash_audit_post.py` | Bash 后置审计 |
| `test_pattern_performance.py` | 正则模式性能 |
| `test_security_functions_positive.py` | 安全功能正向断言 |
| `run-all-tests.py` | 统一测试入口（拓扑排序） |

### 3. 技能系统 — `skills/util-*`

| Skill | 命令 | 用途 |
|:---|:---|:---|
| **util-check** | `/util-check` | 健康自检——16 项检查（结构完整性、引用一致性、运行时行为） |
| **util-safety** | — | 安全守卫系统（主动触发，无需手动调用） |
| **util-memory** | `/util-memory` | Memory 查看/清理/归档辅助工具 |
| **util-session** | `/util-session` | 会话上下文保存/加载 |
| **util-init** | `/util-init` | 项目初始化，识别技术栈并建立上下文 |

### 4. 规则文件 — `skills/rules/`

按需加载，不常驻上下文：

| 规则文件 | 触发场景 |
|:---|:---|
| `workflow.md` | 标准/静默模式启动时 |
| `karpathy-guidelines.md` | 标准/静默模式启动时（行为准则） |
| `git-safety.md` | git 写操作时 |
| `skill-org.md` | Write/Edit SKILL.md 时 |
| `core-principles.md` | 首次 UserPromptSubmit 时（全局底线） |
| `rule-loading.md` | 规则加载机制详解（触发场景映射表） |

### 5. 配置中心

#### `settings.json`（主设置 — 含 API token，⚠ 不入 git）

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "sk-...",     // API token
    "ANTHROPIC_BASE_URL": "https://...",   // API 端点
    "ANTHROPIC_MODEL": "deepseek-v4-flash" // 统一模型覆盖
  },
  "model": "opus[1m]"
}
```

所有模型（opus/sonnet/haiku/fable）统一指向 `deepseek-v4-flash`，通过第三方中转。

#### `settings.local.json`（本地设置 — 权限 + hook 注册）

- **permissions.allow**：预授权了 Python 执行、特定 Bash 命令和 WebSearch
- **hooks**：完整注册了 **14 个钩子**覆盖所有事件点（PreToolUse/PostToolUse/SessionStart/UserPromptSubmit/UserPromptExpansion）

#### `env.json`（环境配置）

```json
{
  "python_exe": "C:/App/Python311/pythonw.exe",
  "os_type": "windows",
  "debug": false
}
```

切换 Windows/Unix 时只需改这一个文件。

### 6. Hook 调度链路

```
settings.local.json 注册 hook 事件点
  │
  ▼
hook-runner.cmd / hook-runner.sh  （自定位入口）
  │
  ▼
scripts/hook-runner.py            （读 env.json → 按路径分派）
  │
  ▼
skills/util-safety/hooks/<script>.py  （具体 hook 逻辑）
```

---

## 🔧 工作机制

### Hook 执行流程

```
工具调用（Bash/Write/Edit/MCP）
  │
  ▼ PreToolUse
  ├── rule-loader.py → 检测场景 → 注入对应规则到上下文
  ├── bash-safety-wrapper.py / write-safety.py / mcp-safety.py
  │     ├── allow  → 放行
  │     ├── ask    → 弹窗让主人确认
  │     └── deny   → 硬阻断（exit 2）
  │
  ▼ 实际执行
  │
  ▼ PostToolUse
  ├── bash-audit-post.py / write-audit.py → 记录审计日志
  └── mcp-audit.py → 记录 MCP 调用日志
```

### 日常工作流

```
开始任务
  ↓
[rule-loader] 检测 `[silent]`/`[静默]` 显式前缀决定模式：快速 / 标准 / 静默
  ↓ 标准/静默模式自动注入 workflow.md + karpathy-guidelines.md（常驻 5 条）
执行（Edit/Write/Bash）
  ↓ git 写操作 → 注入 git-safety.md
  ↓ git commit 信号 → 叠加"失败模式自查"临提交清单 + git-safety.md
  ↓ Write/Edit SKILL.md → 注入 skill-org.md
  ↓ 引入依赖 / 调试修 bug / 接手陌生代码库 → 浮现对应 full 条款软提示（per-session 抑制）
  ↓ 审阅排查类任务 → 提示先 Read conventions.md+decisions.md 正文
排查 bug / 做决策 / 用户纠正 → 写入 memory
  ↓
任务结束 → AI 根据上下文决定是否 Write 入库
下一轮
```

### 安全策略层级

| 层级 | 机制 | 说明 |
|:---|:---|:---|
| L1 — 意图层 | SKILL.md 声明 | 描述工具用途和限制 |
| L2 — hook 准入 | `settings.local.json` allowlist | 预授权的命令白名单 |
| L3 — hook 硬约束 | bash/write/mcp-safety | 运行时强制检查，不可绕过 |

### 威胁模型

> **一句话**：防的是 **AI 手滑/误删/误改**，不是对抗性攻击。

**在防护范围内**：
- AI 误执行破坏性命令（`rm -rf`、`git push --force` 等）
- AI 误改控制平面文件（CLAUDE.md、skills/、hooks/、settings.json）
- AI 误把密钥/凭据写入文件或暂存到 git

**不在防护范围内**：
- 对抗性绕过（有意规避的 AI 总能找到盲区）
- 未知 MCP 动词（需手动补充到黑名单）
- 低熵真密钥（熵阈值 4.0 偏向少误报）

---

## 📊 统计一览

| 指标 | 数值 |
|:---|:---|
| Hook 脚本 | 8 个 Hook（4 守卫 + 4 增强）+ 4 共享模块 |
| 规则文件 | 6 个 `.md` |
| Skill 技能 | 5 个（util-*） |
| 测试文件 | 14 个（util-safety）+ 1 个（util-check） |
| 全测试通过 | ✓（19 项 health-check 全 PASS） |
| Hook 性能基线 | bash 21ms / write 15ms |
| 日志类型 | 审计 / 性能 / 模式切换 / 规则使用 / 健康检查 |
| 授权类别 | Bash: `git` / `delete` / `netexec` / `package` / `sensitive` / `subshell` / `git-rewrite` / `api-modify` / `perm-escalate` / `db-write`（10 类）；MCP 独立类别见 `mcp-safety.py` |

---

## 📝 版本管理

- **SSOT**：`CHANGELOG.md` 顶部 `## [x.y.z]`
- **校验**：`/util-check` 自动验证 `CLAUDE.md` 的 frontmatter `version` 与 CHANGELOG 一致
- **漂移检测**：检测一级标题是否含 `v?\d+\.\d+` 硬编码版本号
- **各规则/SKILL.md**：独立语义化版本，不参与 SSOT 校验

---

## 🧹 周期维护

| 频率 | 操作 | 方法 |
|:---|:---|:---|
| 每次使用 | 健康自检 | `/util-check` |
| 每周 | 检查审计日志 | `logs/*.jsonl` |
| 每月 | 日志归档 | 手动归档 `logs/` 下旧文件 |
| 迁移后 | 运行健康检查 | `/util-check` |

## 📖 更多参考

| 文档 | 路径 | 说明 |
|:---|:---|:---|
| 环境迁移 | `MIGRATION.md` | 跨机器/跨系统迁移 6 步流程 |
| 版本历史 | `CHANGELOG.md` | 完整变更记录（56KB） |
| Memory | `projects/C--Users-nnie/memory/` | 跨会话持久记忆（4 个文件：MEMORY.md 索引 + debugging/decisions/conventions） |

---

*最后更新：2026-07-10 · 版本号以 CHANGELOG.md 顶部为准*