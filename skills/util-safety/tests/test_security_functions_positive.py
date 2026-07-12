#!/usr/bin/env python3
"""安全功能点正向断言测试。

确保每个安全功能「确实发生了」，而非仅「没崩」。
背景：P0 修复发现 ask 格式错误和打码失效时，127 个测试全绿——
现有测试只验证「没崩」，不验证「功能发生了」。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows GBK 终端强制 UTF-8
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, closefd=False)
except Exception:
    pass


BASH_HOOK = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "bash-safety-wrapper.py"
MCP_HOOK = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "mcp-safety.py"
AUDIT_LOG = Path.home() / ".claude" / "logs" / "bash-safety-audit.jsonl"


def run_bash_hook(command: str, grants_dir: Path | None = None, grants: list[str] | None = None) -> tuple[int, str, str]:
    """运行 bash-safety-wrapper hook。"""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command}
    }
    env = os.environ.copy()
    for cat in ("GIT", "DELETE", "NETEXEC", "PACKAGE", "SENSITIVE", "SUBSHELL"):
        env.pop(f"CLAUDE_HOOK_APPROVED_{cat}", None)

    _tmp_ctx = None
    if grants_dir is None:
        _tmp_ctx = tempfile.TemporaryDirectory()
        grants_dir = Path(_tmp_ctx.name)
    env["CLAUDE_TEST_GRANTS_DIR"] = str(grants_dir)

    # 创建 grant 文件
    for cat in (grants or []):
        (grants_dir / cat).write_text("", encoding="utf-8")

    try:
        result = subprocess.run(
            [sys.executable, str(BASH_HOOK)],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=20,
            env=env,
        )
        return result.returncode, result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace")
    finally:
        if _tmp_ctx is not None:
            _tmp_ctx.cleanup()


def run_mcp_hook(tool_name: str, tool_input: dict | None = None, grants_dir: Path | None = None) -> tuple[int, str, str]:
    """运行 mcp-safety hook。"""
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input or {}
    }
    env = os.environ.copy()
    env.pop("CLAUDE_HOOK_APPROVED_MCP", None)

    _tmp_ctx = None
    if grants_dir is None:
        _tmp_ctx = tempfile.TemporaryDirectory()
        grants_dir = Path(_tmp_ctx.name)
    env["CLAUDE_TEST_GRANTS_DIR"] = str(grants_dir)

    try:
        result = subprocess.run(
            [sys.executable, str(MCP_HOOK)],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=20,
            env=env,
        )
        return result.returncode, result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace")
    finally:
        if _tmp_ctx is not None:
            _tmp_ctx.cleanup()


# ============================================================
# P1.1 — 正向断言测试：验证功能「确实发生了」
# ============================================================


def test_secret_redaction_actually_happens():
    """打码确实发生：含密钥的命令，摘要中应出现 ***REDACTED***。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # 备份并清空审计日志
        backup_entries = []
        if AUDIT_LOG.exists():
            with AUDIT_LOG.open("r", encoding="utf-8") as f:
                backup_entries = f.readlines()
            AUDIT_LOG.unlink()

        try:
            # 使用 grant 执行含密钥的危险命令
            # git commit 需要 git grant
            command = 'git commit -m "api_key=sk-ant-abcdefghijklmnopqrstuvwxyz123456"'
            code, stdout, stderr = run_bash_hook(command, grants_dir, grants=["git"])

            # 读取审计日志
            assert AUDIT_LOG.exists(), "审计日志应存在"
            with AUDIT_LOG.open("r", encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) > 0, "审计日志应有记录"

            # 找到包含 git commit 的审计条目
            found_redacted = False
            for line in lines:
                entry = json.loads(line.strip())
                summary = entry.get("command_summary", "")
                if "git commit" in summary:
                    # 正向断言：打码确实发生
                    assert "***REDACTED" in summary, f"打码未生效，摘要: {summary}"
                    assert "sk-ant-abcdefghijklmnopqrstuvwxyz123456" not in summary, f"密钥明文泄露: {summary}"
                    found_redacted = True
                    break

            assert found_redacted, "未找到包含 git commit 的审计条目"

        finally:
            # 恢复审计日志
            with AUDIT_LOG.open("w", encoding="utf-8") as f:
                f.writelines(backup_entries)

    print("[PASS] 打码确实发生测试通过")


def test_bash_ask_json_schema_valid():
    """Bash ask 输出符合 hookSpecificOutput 协议。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # 危险命令，应触发 ask
        code, stdout, stderr = run_bash_hook("git commit -m test", grants_dir)

        assert code == 0, f"ask 应返回 0，实际 {code}"
        assert "hookSpecificOutput" in stdout, f"缺少 hookSpecificOutput，输出: {stdout}"

        output = json.loads(stdout.strip())
        hook_output = output.get("hookSpecificOutput", {})

        # 正向断言：协议字段完整
        assert hook_output.get("hookEventName") == "PreToolUse", f"hookEventName 错误: {hook_output}"
        assert hook_output.get("permissionDecision") == "ask", f"permissionDecision 错误: {hook_output}"
        assert "permissionDecisionReason" in hook_output, f"缺少 permissionDecisionReason: {hook_output}"

    print("[PASS] Bash ask JSON schema 验证通过")


def test_mcp_ask_json_schema_valid():
    """MCP ask 输出符合 hookSpecificOutput 协议。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # 配置为 ask 的工具
        code, stdout, stderr = run_mcp_hook("mcp__slack__send_message", {"text": "hello"}, grants_dir)

        assert code == 0, f"ask 应返回 0，实际 {code}"
        assert "hookSpecificOutput" in stdout, f"缺少 hookSpecificOutput，输出: {stdout}"

        output = json.loads(stdout.strip())
        hook_output = output.get("hookSpecificOutput", {})

        # 正向断言：协议字段完整
        assert hook_output.get("hookEventName") == "PreToolUse", f"hookEventName 错误: {hook_output}"
        assert hook_output.get("permissionDecision") == "ask", f"permissionDecision 错误: {hook_output}"
        assert "permissionDecisionReason" in hook_output, f"缺少 permissionDecisionReason: {hook_output}"

    print("[PASS] MCP ask JSON schema 验证通过")


def test_deny_path_exits_with_reason():
    """deny 路径 exit 2 且 stderr 含拦截原因。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # 写 .grants 目录的命令应被 deny（自我授权防护）
        command = 'touch ~/.claude/.grants/self-grant'
        code, stdout, stderr = run_bash_hook(command, grants_dir)

        # 正向断言：deny 路径
        assert code == 2, f"deny 应返回 2，实际 {code}"
        assert len(stderr) > 0, "deny 应输出 stderr 拦截原因"
        assert ".grants" in stderr or "grant" in stderr.lower(), f"stderr 缺少拦截原因: {stderr}"

    print("[PASS] deny 路径 exit 2 + stderr 原因验证通过")


def test_grant_consumed_and_audit_logged():
    """grant 消费后文件确实消失、审计确实落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # 创建一次性 grant
        grant_file = grants_dir / "git"
        grant_file.write_text("", encoding="utf-8")
        assert grant_file.exists(), "grant 文件应存在"

        # 执行需要 grant 的命令
        code, stdout, stderr = run_bash_hook("git commit -m test", grants_dir)

        # 正向断言：grant 被消费（文件消失）
        assert not grant_file.exists(), f"grant 文件应被消费，但仍存在: {grant_file}"

        # 正向断言：审计落盘
        assert AUDIT_LOG.exists(), "审计日志应存在"
        with AUDIT_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) > 0, "审计日志应有记录"

        # 最后一条应包含 git commit
        last_entry = json.loads(lines[-1].strip())
        assert "git commit" in last_entry.get("command_summary", ""), f"审计记录应包含命令: {last_entry}"

    print("[PASS] grant 消费 + 审计落盘验证通过")


def main() -> int:
    try:
        test_secret_redaction_actually_happens()
        test_bash_ask_json_schema_valid()
        test_mcp_ask_json_schema_valid()
        test_deny_path_exits_with_reason()
        test_grant_consumed_and_audit_logged()

        print("\n[OK] 全部安全功能正向断言测试通过！")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[ERROR] 测试异常: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
