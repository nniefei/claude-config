---
name: util-check
description: 自动检查 Skills 系统的健康状态，验证结构完整性和引用一致性
user-invocable: true
argument-hint: "[test]"
depends-on: []
allowed-tools:
  - Bash
version: 1.1.0
last-updated: 2026-07-04
---

# Skill 健康自检

> 自动检查 Skills 系统的结构完整性、引用一致性和规范合规性。
> 定位：**自动化检查**——替代 `skill-org.md` 中的手动四维自检。

## 使用方式
`/util-check` — 执行完整健康检查
`/util-check test` — 运行核心回归测试脚本

## 检查项（16 项）

### 1. 结构完整性 `check_structure_integrity`

对 `skills/` 下每个子目录（排除 `rules/`）检查：

| 检查点 | 通过条件 |
|:---|:---|
| SKILL.md 存在 | 每个 Skill 目录必须有 SKILL.md |
| frontmatter 有效 | 必须包含 `name`、`description`、`user-invocable` 字段 |
| name 与目录名一致 | frontmatter 中 `name` 值 = 目录名 |
| 命名前缀合规 | 目录名以 `dev-`、`util-` 开头 |

### 2. 引用一致性 `check_reference_consistency`

| 检查点 | 方法 |
|:---|:---|
| CLAUDE.md 中引用的 rules 文件都存在 | 提取 CLAUDE.md 中 `skills/rules/*.md` 路径，逐一验证 |
| Skill 间交叉引用有效 | 搜索所有 SKILL.md 中 `/dev-*`、`/util-*` 的引用，验证目标 Skill 存在 |
| depends-on 字段引用有效 | 检查所有 Skill 的 frontmatter 中 `depends-on` 字段，验证引用的 Skill 存在 |

### 3. 规范合规性 `check_compliance`

| 检查点 | 通过条件 |
|:---|:---|
| CLAUDE.md 行数 | ≤ 100 行（超过则需提取到 rules） |
| rules 文件被引用 | `rules/` 下每个文件至少被 CLAUDE.md 或某个 SKILL.md 引用 |

### 4. Memory 路径一致性 `check_memory_path_consistency`

| 检查点 | 方法 |
|:---|:---|
| 路径引用统一 | 搜索所有 `.md` 中的 `memory/` 引用，确认有路径基准说明或使用完整路径 |

### 5. 依赖关系检查 `check_dependency_graph`

| 检查点 | 通过条件 |
|:---|:---|
| 循环依赖检测 | 不存在 A → B → C → A 的环形依赖 |
| 调用链深度 | 最长的依赖链 ≤ 3 层（从入口 Skill 到最深依赖） |
| 悬空依赖 | depends-on 中引用的 Skill 都存在 |

### 6. 强约束运行时检查 `check_runtime_constraints`

| 检查点 | 通过条件 |
|:---|:---|
| Bash 安全 hook | `settings.json` 配置了 `PreToolUse` + `Bash` matcher，并调用 `bash-safety-wrapper.py` |
| Write/Edit 安全 hook | `settings.json` 配置了 `PreToolUse` + `Write|Edit` matcher，并调用 `write-safety.py` |
| hook 脚本存在 | `skills/util-safety/hooks/bash-safety-wrapper.py`、`skills/util-safety/hooks/write-safety.py` 文件存在 |
| fail-closed wrapper | `bash-safety-wrapper.py` 包含超时/异常阻断逻辑 |
| 绝对 Python 路径 | hook 命令不使用裸 `python` / `py`，避免 PATH 劫持 |
| Hook 解释器存在 | settings.json 注册的 Python 解释器（绝对路径）真实存在 |
| Hook 脚本注册路径有效 | settings.json 注册的 hook 脚本路径真实存在 |
| Hook schema smoke test | 用合成 PreToolUse JSON 调用 settings.json 注册的真实脚本，能正确阻断高风险输入 |
| 行为级验证 | 使用合成 hook JSON 验证 Bash、wrapper、Write/Edit 守卫能阻断高风险输入 |
| 工具 fallback | 使用 `Glob` / `Grep` 的 Skill 文档必须说明只读 Python fallback |

### 7. 版本号一致性 `check_versioning`

| 检查点 | 通过条件 |
|:---|:---|
| CHANGELOG 顶版存在 | `CHANGELOG.md` 顶部有 `## [x.y.z]` 格式版本 |
| frontmatter version 一致 | `CLAUDE.md` 的 version = CHANGELOG 顶版 |

### 8. 新 hook 注册检查 `check_new_hook_registrations`

| 检查点 | 通过条件 |
|:---|:---|
| hook 脚本都已注册 | `skills/util-safety/hooks/*.py` 中的 hook 脚本都在 `settings.json` 中注册 |

### 9. 新 hook 行为检查 `check_new_hook_behaviors`

| 检查点 | 通过条件 |
|:---|:---|
| hook 行为符合预期 | 使用合成 payload 测试每个 hook 的关键行为（fail-closed、fail-safe） |

### 10. Pattern 一致性检查 `check_pattern_consistency`

| 检查点 | 通过条件 |
|:---|:---|
| SECRET_PATTERNS 一致 | `write-safety.py`、`mcp-safety.py`、`bash-safety-wrapper.py` 的 SECRET_PATTERNS 与 `_shared_patterns.py` 一致 |

### 11. 配置文件 secret 扫描 `check_config_secret_leakage`

| 检查点 | 通过条件 |
|:---|:---|
| settings.json secret | `settings.json` 不含 sk-/ghp_/AKIA/bearer/credential 等明文密钥 |
| settings.local.json secret | `settings.local.json` 同上 |

检测到 secret 时归入 **WARN**（不阻断），建议改用环境变量或 OS 密钥管理器。

### 12. 意图-实现一致性检查 `check_intent_implementation_consistency`

| 检查点 | 通过条件 |
|:---|:---|
| 抽取 SKILL.md / rules 中"❌ ...`path`"声明 | 仅取反引号内含 `/` 或 `.` 的具体路径，跳过抽象表述 |
| 调用 write-safety 验证 | mock PreToolUse payload 调用 `write-safety.py`，期望 exit=2 |
| 不一致 → WARN | 若某 ❌ 路径声明的 write-safety 未拦截，归入 WARN 提醒用户 |

### 13. Hook stderr 消息格式检查 `check_hook_stderr_messages`

| 检查点 | 通过条件 |
|:---|:---|
| stderr 格式一致 | hook 的 stderr 消息符合统一格式（`[安全守卫] ...`） |

### 14. MCP 模式规则检查 `check_mcp_pattern_rules`

| 检查点 | 通过条件 |
|:---|:---|
| 高危动词拦截 | `mcp-safety.py` 对 blocklist 中的高危工具返回 deny |
| 只读动词放行 | `mcp-safety.py` 对只读工具返回 allow |

### 15. Secret 熵复核检查 `check_entropy_recheck`

| 检查点 | 通过条件 |
|:---|:---|
| 高熵密钥拦截 | 熵 ≥ 4.0 的真密钥被拦截 |
| 低熵标识符放行 | 熵 < 4.0 的占位符/标识符被放行 |

### 16. 规则冲突检测 `check_rule_conflicts`

| 检查点 | 通过条件 |
|:---|:---|
| 无已知冲突模式 | 扫描 rules 文件，未发现已知冲突模式 |

## 执行步骤

调用 `Bash` 执行固定的只读 Python 健康检查脚本：

```bash
C:/App/Python311/python.exe C:/Users/nnie/.claude/skills/util-check/scripts/skills-health-check.py
```

运行核心回归测试脚本：

```bash
C:/App/Python311/python.exe C:/Users/nnie/.claude/skills/util-check/scripts/skills-health-check.py test
```

脚本使用 Python 标准库（pathlib、json、re）完成所有检查，不依赖外部工具（rg、grep 等），输出结构化报告。

## 输出格式

```
Skills 系统健康检查报告

### [PASS] 通过 (X 项)
- 结构完整性：全部通过
- 引用一致性：全部通过
- 规范合规性：全部通过
- 依赖关系：全部通过
- 强约束运行时：全部通过

### [WARN] 警告 (N 项)
- `CLAUDE.md` 当前 105 行，超过 100 行建议上限
- `util-safety` 的调用链深度为 2 层

### [ERROR] 错误 (N 项)
- `skills/xxx/SKILL.md` frontmatter 缺少 name 字段
- CLAUDE.md 引用了 `skills/rules/xxx.md`，但文件不存在
- `settings.json` 未配置 Bash PreToolUse hook
- `settings.json` 未配置 Write/Edit PreToolUse hook
- Bash hook 配置必须调用 bash-safety-wrapper 脚本
- Hook 命令使用 PATH 解析 Python，建议改为 Python 绝对路径
- `skills/util-safety/hooks/write-safety.py` 行为验证失败：未阻断高风险输入
- 检测到循环依赖：A → B → A（仅作格式示例）

### [STATS] 统计
- Skill 总数：X 个（dev: X, util: X）
- Rules 文件：X 个
- CLAUDE.md 行数：X 行
- 最长依赖链：X 层
- 循环依赖：X 个
```

## 注意事项

- 只读检查，不修改任何文件
- 对于警告级别的问题给出修复建议，但不自动修复
- 错误级别的问题建议立即修复
- **循环依赖是严重错误**，必须立即修复
- **调用链深度 > 3 层是警告**，建议重新设计依赖关系
