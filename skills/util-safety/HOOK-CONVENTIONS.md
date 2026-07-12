---
version: 1.1.0
last-updated: 2026-05-28
---

# Hook 编写约定

> 本文档不是 Skill，是 hook 脚本的统一编写规范。Plan-3 3.7 引入。
> 新增 hook 时严格遵循；修改现有 hook 时如违反应同步迁移。

## 失败策略矩阵

| Hook 脚本 | 失败策略 | 理由 |
|:---|:---|:---|
| `bash-safety-wrapper.py` | **fail-closed**（exit 2） | 安全守卫：任何崩溃都应阻断，避免危险命令绕过 |
| `write-safety.py` | **fail-closed**（exit 2） | 安全守卫：同上 |
| `mcp-safety.py` | **fail-closed**（exit 2） | 安全守卫：MCP 可能触发外部副作用或泄露凭据 |
| `mcp-audit.py` | **fail-safe**（exit 0） | 增强型：审计失败不应阻断正常 MCP 查询 |
| `bash-audit-post.py` | **fail-safe**（exit 0） | 增强型：PostToolUse 审计/记忆失败不应阻断已执行的 Bash 工作流 |
| `rule-loader.py` | **fail-safe**（exit 0） | 增强型：注入规则失败时不应阻断用户工作流 |
| `session-start.py` | **fail-safe**（exit 0） | 增强型：启动自检失败不应阻挡 session 开始 |

**规则**：守卫型 hook（PreToolUse 用于拦截）→ fail-closed；增强型 hook（提示/日志/上下文注入）→ fail-safe。

## 超时

| Hook | 内部超时 | 实现方式 |
|:---|:---:|:---|
| `bash-safety-wrapper.py` | 15s | `concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=...)` |
| `write-safety.py` | 5s | 同上（Plan-3 3.7 添加） |
| `mcp-safety.py` | 5s | 同上；包含 JSON 解析、配置 IO 与 regex secret scan |
| `mcp-audit.py` | 不需要 | fail-safe 审计，异常直接放行 |
| `rule-loader.py` | 不需要 | 纯文本注入，O(N) 操作，N 极小 |
| `session-start.py` | 不需要 | 同步段 < 200ms；后台检查已 detached |

**新 hook 加超时的判断**：若包含 regex match、文件 IO、JSON 解析等可能因恶意/极端输入退化的操作，必须加内部超时。

## stderr 输出规范

| Hook | 默认行为 | VERBOSE 模式 |
|:---|:---|:---|
| `bash-safety-wrapper.py` | 仅阻断时输出原因 | 无 VERBOSE 切换 |
| `write-safety.py` | 仅阻断时输出原因 | 无 VERBOSE 切换 |
| `mcp-safety.py` | 仅阻断时输出原因 | 无 VERBOSE 切换 |
| `mcp-audit.py` | 静默 | 无 VERBOSE 切换 |
| `bash-audit-post.py` | 静默 | 无 VERBOSE 切换 |
| `rule-loader.py` | 静默（不输出 loaded 行） | `CLAUDE_RULE_LOADER_VERBOSE=1` 时输出 `[rule-loader] loaded: ...` |
| `session-start.py` | 仅有 ERROR 时输出 `[session-start] surfaced N ERROR(s) to AI context` | 无 VERBOSE 切换 |

**规则**：守卫 hook 静默正常路径，仅在阻断时给出明确诊断；rule-loader 默认静默避免污染 AI 上下文，VERBOSE 仅供调试。

## stdin 编码

**所有 hook 必须强制 UTF-8 处理 stdin/stderr**，应对 Windows GBK 终端 + CJK/emoji 输入：

```python
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 仅 rule-loader/session-start 需要（要 print JSON）
except Exception:
    pass
```

## 退出码语义

| 退出码 | 含义 | 适用 hook |
|:---|:---|:---|
| `0` | 允许操作通过 / 增强 hook 正常完成 | 全部 |
| `2` | 阻断操作（PreToolUse） | 仅守卫 hook（bash-safety-wrapper、write-safety）|
| 其他非零 | **禁止使用** | — |

Claude Code 的 hook 协议规定 `2` 是阻断信号；其他非零退出码会被视为 hook 配置错误。

## 路径与硬编码

- `Path.home() / ".claude"` 取根，不要硬编码 `C:/Users/...`
- 测试 fixture 用 `tempfile.TemporaryDirectory`，不污染真实 `~/.claude`
- Python 解释器引用：settings.json 必须使用绝对路径（如 `C:/App/Python311/python.exe`），避免 PATH 劫持

## 审计日志写入原子化

**适用 hook**：所有写入 JSONL 审计日志的 hook（`bash-safety-wrapper.py`、`bash-audit-post.py`、`mcp-audit.py`）。

**原则**：JSONL 追加写入必须加锁，防止多进程并发产生交错行。

**实现**：
- **Windows**：`msvcrt.locking(fd, LK_LOCK, ...)` + `msvcrt.locking(fd, LK_UNLCK, ...)`
- **POSIX**：`fcntl.flock(fd, LOCK_EX)` + `fcntl.flock(fd, LOCK_UN)`

**性能影响**：文件锁开销可忽略（审计日志非常频操作，仅记录危险命令放行和 MCP 调用）。

**验证**：`test_audit_log_concurrency.py` —— 10 进程并发写入，验证 JSONL 各行有效且无丢失。

## 新增 hook 检查清单

1. ☐ 选定失败策略（守卫 fail-closed / 增强 fail-safe）
2. ☐ 若有 regex/IO/JSON 解析 → 加内部超时
3. ☐ stdin/stderr UTF-8 reconfigure
4. ☐ 顶层 try/except 包裹 main()
5. ☐ 退出码仅用 0 / 2
6. ☐ settings.json 注册时使用绝对 Python 路径
7. ☐ 加 tests/test_<name>.py 覆盖：正常路径 + 阻断路径 + 边界（empty/malformed）+ 失败策略
8. ☐ util-check 中新增/更新对应行为校验
