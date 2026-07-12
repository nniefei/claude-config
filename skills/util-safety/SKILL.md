---
name: util-safety
description: 提供 Claude Code 本地 Bash / Write-Edit / MCP 安全守卫 hook 与审计
user-invocable: false
depends-on: []
allowed-tools:
  - Bash
version: 1.5.0
last-updated: 2026-07-08
---

# Safety Hooks

> 存放 Claude Code 本地安全 hook 的实现脚本与回归测试。

## 职责

- `hooks/bash-safety-wrapper.py`：自包含的 Bash 安全守卫，fail-closed + 内置 15s 超时，默认 ask 弹窗 + grants/env 带外预授权。
- `hooks/bash-audit-post.py`：Bash PostToolUse 审计 hook，记录 ask-Allow 后实际执行的危险命令到 `logs/bash-safety-audit.jsonl`，并维护同 session 操作标签级已 Allow 记忆；fail-safe。
- `hooks/write-safety.py`：阻断敏感路径、系统路径、控制面路径、auto-memory 系统命名、CI/CD 与容器配置以及内容中的明文 secret。
- `hooks/mcp-safety.py`：MCP 工具安全守卫，扫描 tool_input 中的明文 secret，并对 `config/mcp_blocklist.json` 中的高风险 MCP 工具要求 `CLAUDE_HOOK_APPROVED_MCP=1` 授权；fail-closed + 内置 5s 超时。
- `hooks/mcp-audit.py`：MCP 工具审计 hook，记录 tool name 与输入摘要到 `logs/mcp-audit.jsonl`；fail-safe。
- `hooks/write-audit.py`：Write/Edit PostToolUse 审计 hook（v1.5.0 新增），记录所有成功执行的 Write/Edit 操作到 `logs/write-audit.jsonl`；fail-safe。
- `hooks/rule-loader.py`：按事件按需注入规则文件；fail-safe，永不阻断。
- `hooks/session-start.py`：SessionStart 时同步轻量检查 + 后台健康检查（每天最多一次，CLAUDE_FORCE_STARTUP_HEALTH_CHECK=1 可强制）。fail-safe。
- `tests/`：安全 hook 的回归测试。

## 边界

Claude Code hook 入口仍由全局 `settings.json` 注册；本 Skill 只拥有 hook 实现脚本与测试。

### 子代理 hook 继承

Claude Code hooks 在 `settings.json` 中基于事件类型（PreToolUse / PostToolUse 等）注册，
对**所有** tool use 事件生效，包括 `Task` / `Agent` 子代理发起的 Bash / Write / Edit / MCP 调用。
子代理无法绕过安全守卫。若未来 Claude Code 架构变更（如子代理使用独立进程且不加载 hooks），
需重新验证此假设。

## 日志与文件保留策略（Plan-3 3.6）

| 日志/目录 | 模式 | 保留策略 | 备注 |
|:---|:---|:---|:---|
| `logs/bash-safety-audit.jsonl` | 滚动 | 超过 5MB 或 5000 行时自动归档为 `bash-safety-audit.jsonl.<timestamp>.1` | 记录所有授权放行或实际执行的危险命令；`source` 区分 `grant` / `post-exec` / `session-ask-memo`；归档文件不自动删除 |
| `logs/ask-approved-cache.json` | LRU JSON | 最多保留 200 个 session | 记录同 session 已 Allow 的危险操作标签，用于减少重复 ask 弹窗 |
| `logs/mcp-audit.jsonl` | 滚动 | 超过 5MB 或 5000 行时自动归档为 `mcp-audit.jsonl.<timestamp>.1` | 记录 MCP 工具调用的 tool name 与输入摘要；归档文件不自动删除 |
| `logs/write-audit.jsonl` | 滚动 | 超过 5MB 或 5000 行时自动归档为 `write-audit.jsonl.<timestamp>.1` | v1.5.0 新增：记录所有成功执行的 Write/Edit 操作（路径哈希 + 行数）；归档文件不自动删除 |
| `logs/mode-transitions.jsonl` | append-only | 不自动清理，建议每月手动归档 | 记录每次用户输入的推断模式 |
| `logs/health-check-startup.jsonl` | append-only | 不自动清理 | 记录 session-start 启动事件 |
| `logs/health-check-runs/run-*.log` | 滚动 | **保留最新 5 个**，按文件名时序排序删除（**不使用 stat()**） | 历史踩坑：早期用 `stat()` mtime 排序在 Windows 上 N 个文件触发 O(N) 同步 IO，会卡住 session-start 几秒。改用文件名内的时间戳排序后解决（见 debugging.md）|

**清理建议**：
- audit / mode-transitions 日志体量不大，月度归档即可
- 若长期不归档，体量主要由 audit log 占据，按需观察
- 不要手动 rm 整个 `logs/`，会丢失审计轨迹（hook 会自动重建目录但历史不可恢复）
