#!/usr/bin/env python3
"""审计日志并发写入与轮转安全性验证。"""
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
except Exception:
    pass

BASH_HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "bash-safety-wrapper.py"
BASH_AUDIT_POST_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "bash-audit-post.py"
MCP_AUDIT_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "mcp-audit.py"


def _call_bash_hook(home_dir, process_id, extra_env=None):
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"git commit -m 'concurrent-{process_id}'"},
    }
    env = os.environ.copy()
    env["HOME"] = home_dir
    env["USERPROFILE"] = home_dir
    env["CLAUDE_HOOK_APPROVED_GIT"] = "1"
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        [sys.executable, str(BASH_HOOK_SCRIPT)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=30,
        env=env,
    )
    return result.returncode == 0


def _call_bash_post_hook(home_dir, process_id, extra_env=None):
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"git commit -m 'post-concurrent-{process_id}'"},
    }
    env = os.environ.copy()
    env["HOME"] = home_dir
    env["USERPROFILE"] = home_dir
    env["CLAUDE_SESSION_ID"] = f"post-{process_id}"
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        [sys.executable, str(BASH_AUDIT_POST_SCRIPT)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=30,
        env=env,
    )
    return result.returncode == 0


def _call_mcp_audit_hook(home_dir, process_id, extra_env=None):
    payload = {
        "tool_name": "mcp__demo__tool",
        "tool_input": {"process_id": process_id},
    }
    env = os.environ.copy()
    env["HOME"] = home_dir
    env["USERPROFILE"] = home_dir
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        [sys.executable, str(MCP_AUDIT_SCRIPT)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=30,
        env=env,
    )
    return result.returncode == 0


def _read_jsonl(path: Path):
    entries = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            raise AssertionError(f"第 {i} 行 JSON 无效（可能存在交错写入）：{line[:120]}")
    return entries


def test_concurrent_audit_writes_no_interleaving():
    """10 进程同时写入 Bash 审计日志，JSONL 各行必须有效且无丢失。"""
    with tempfile.TemporaryDirectory() as tmp:
        home = tmp
        log_dir = Path(home) / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        num_procs = 10
        with multiprocessing.Pool(processes=num_procs) as pool:
            results = pool.starmap(_call_bash_hook, [(home, i) for i in range(num_procs)])

        assert all(results), f"部分 hook 调用失败：{results}"

        audit_log = log_dir / "bash-safety-audit.jsonl"
        assert audit_log.exists(), "审计日志未创建"
        entries = _read_jsonl(audit_log)
        assert len(entries) == num_procs, f"期望 {num_procs} 条日志，实际 {len(entries)} 条（存在丢失）"

        for entry in entries:
            assert "timestamp" in entry, "缺少 timestamp"
            assert "categories" in entry, "缺少 categories"
            assert "operations" in entry, "缺少 operations"
            assert "command_summary" in entry, "缺少 command_summary"
            assert "pid" in entry, "缺少 pid"

    print("[PASS] Bash 审计日志并发安全性测试通过")


def test_concurrent_audit_pid_uniqueness():
    """验证每个 Bash 子进程的 pid 都被记录。"""
    with tempfile.TemporaryDirectory() as tmp:
        home = tmp
        log_dir = Path(home) / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        num_procs = 5
        with multiprocessing.Pool(processes=num_procs) as pool:
            results = pool.starmap(_call_bash_hook, [(home, i) for i in range(num_procs)])
        assert all(results)

        entries = _read_jsonl(log_dir / "bash-safety-audit.jsonl")
        pids = {entry["pid"] for entry in entries}
        assert len(pids) >= 1, "应至少记录一个 pid"

    print("[PASS] Bash 审计日志 PID 记录测试通过")


def test_bash_audit_log_rotates_without_deleting_history():
    with tempfile.TemporaryDirectory() as tmp:
        home = tmp
        log_dir = Path(home) / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        audit_log = log_dir / "bash-safety-audit.jsonl"
        old_lines = [json.dumps({"old": i}) for i in range(3)]
        audit_log.write_text("\n".join(old_lines) + "\n", encoding="utf-8")

        ok = _call_bash_hook(
            home,
            1,
            {"CLAUDE_AUDIT_ROTATE_MAX_LINES": "2", "CLAUDE_AUDIT_ROTATE_MAX_BYTES": "1048576"},
        )
        assert ok, "触发轮转的 Bash hook 调用失败"

        archives = sorted(log_dir.glob("bash-safety-audit.jsonl.*.1"))
        assert len(archives) == 1, f"期望 1 个归档日志，实际 {len(archives)} 个"
        assert archives[0].read_text(encoding="utf-8") == "\n".join(old_lines) + "\n", "归档内容不完整"

        entries = _read_jsonl(audit_log)
        assert len(entries) == 1, "轮转后当前日志应只包含新写入行"
        assert entries[0]["command_summary"].startswith("git commit"), "新日志内容异常"

    print("[PASS] Bash 审计日志轮转测试通过")


def test_post_exec_audit_concurrent_writes_no_interleaving():
    """10 进程同时通过 PostToolUse 写入 Bash 审计日志，JSONL 各行必须有效且无丢失。"""
    with tempfile.TemporaryDirectory() as tmp:
        home = tmp
        log_dir = Path(home) / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        num_procs = 10
        with multiprocessing.Pool(processes=num_procs) as pool:
            results = pool.starmap(_call_bash_post_hook, [(home, i) for i in range(num_procs)])

        assert all(results), f"部分 PostToolUse hook 调用失败：{results}"

        audit_log = log_dir / "bash-safety-audit.jsonl"
        assert audit_log.exists(), "PostToolUse 审计日志未创建"
        entries = _read_jsonl(audit_log)
        assert len(entries) == num_procs, f"期望 {num_procs} 条日志，实际 {len(entries)} 条（存在丢失）"
        assert all(entry.get("source") == "post-exec" for entry in entries), "PostToolUse 审计 source 异常"

    print("[PASS] Bash PostToolUse 审计日志并发安全性测试通过")


def test_mcp_audit_concurrent_writes_no_interleaving():
    """10 进程同时写入 MCP 审计日志，JSONL 各行必须有效且无丢失。"""
    with tempfile.TemporaryDirectory() as tmp:
        home = tmp
        log_dir = Path(home) / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        num_procs = 10
        with multiprocessing.Pool(processes=num_procs) as pool:
            results = pool.starmap(_call_mcp_audit_hook, [(home, i) for i in range(num_procs)])

        assert all(results), f"部分 MCP audit 调用失败：{results}"

        audit_log = log_dir / "mcp-audit.jsonl"
        assert audit_log.exists(), "MCP 审计日志未创建"
        entries = _read_jsonl(audit_log)
        assert len(entries) == num_procs, f"期望 {num_procs} 条 MCP 日志，实际 {len(entries)} 条（存在丢失）"

        for entry in entries:
            assert "ts" in entry, "缺少 ts"
            assert entry.get("tool") == "mcp__demo__tool", "tool 字段异常"
            assert "input_preview" in entry, "缺少 input_preview"
            assert "pid" in entry, "缺少 pid"

    print("[PASS] MCP 审计日志并发安全性测试通过")


def test_mcp_audit_log_rotates_without_deleting_history():
    with tempfile.TemporaryDirectory() as tmp:
        home = tmp
        log_dir = Path(home) / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        audit_log = log_dir / "mcp-audit.jsonl"
        old_lines = [json.dumps({"old": i}) for i in range(3)]
        audit_log.write_text("\n".join(old_lines) + "\n", encoding="utf-8")

        ok = _call_mcp_audit_hook(
            home,
            1,
            {"CLAUDE_AUDIT_ROTATE_MAX_LINES": "2", "CLAUDE_AUDIT_ROTATE_MAX_BYTES": "1048576"},
        )
        assert ok, "触发轮转的 MCP audit 调用失败"

        archives = sorted(log_dir.glob("mcp-audit.jsonl.*.1"))
        assert len(archives) == 1, f"期望 1 个 MCP 归档日志，实际 {len(archives)} 个"
        assert archives[0].read_text(encoding="utf-8") == "\n".join(old_lines) + "\n", "MCP 归档内容不完整"

        entries = _read_jsonl(audit_log)
        assert len(entries) == 1, "MCP 轮转后当前日志应只包含新写入行"
        assert entries[0]["tool"] == "mcp__demo__tool", "MCP 新日志内容异常"

    print("[PASS] MCP 审计日志轮转测试通过")


def main():
    if not BASH_HOOK_SCRIPT.exists():
        print(f"错误：Hook 脚本未找到：{BASH_HOOK_SCRIPT}", file=sys.stderr)
        return 1
    if not MCP_AUDIT_SCRIPT.exists():
        print(f"错误：Hook 脚本未找到：{MCP_AUDIT_SCRIPT}", file=sys.stderr)
        return 1

    shim_path = BASH_HOOK_SCRIPT.with_name("bash-safety.py")
    if shim_path.exists():
        print(f"错误：旧版 shim 仍然存在：{shim_path}", file=sys.stderr)
        return 1

    try:
        test_concurrent_audit_writes_no_interleaving()
        test_concurrent_audit_pid_uniqueness()
        test_bash_audit_log_rotates_without_deleting_history()
        test_post_exec_audit_concurrent_writes_no_interleaving()
        test_mcp_audit_concurrent_writes_no_interleaving()
        test_mcp_audit_log_rotates_without_deleting_history()

        print("\n[OK] 全部审计日志并发/轮转测试通过！")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败：{e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FAIL] 意外错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
