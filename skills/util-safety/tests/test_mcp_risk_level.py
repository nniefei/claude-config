#!/usr/bin/env python3
"""测试 MCP risk_level 字段（P3-1）— deny 硬阻断、ask 弹窗确认。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MCP_HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "mcp-safety.py"
BLOCKLIST_PATH = Path.home() / ".claude" / "skills" / "util-safety" / "config" / "mcp_blocklist.json"


def run_mcp_hook(tool_name: str, grants_dir: Path) -> tuple[int, str, str]:
    """运行 mcp-safety.py hook，返回 (returncode, stdout, stderr)。"""
    payload = {
        "tool_name": tool_name,
        "tool_input": {"test": "data"},
    }
    env = os.environ.copy()
    env["CLAUDE_TEST_GRANTS_DIR"] = str(grants_dir)

    result = subprocess.run(
        [sys.executable, str(MCP_HOOK_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def test_deny_risk_level_blocks():
    """risk_level='deny' 的工具应硬阻断（exit 2），无 ask 决策。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # mcp__github__force_push 配置为 deny
        code, stdout, stderr = run_mcp_hook("mcp__github__force_push", grants_dir)

        assert code == 2, f"deny 工具应返回 2（硬阻断），实际 {code}"
        assert "permissionDecision" not in stdout, "deny 不应输出 ask 决策 JSON"
        assert "MCP 已拦截" in stderr, "stderr 应包含拦截消息"

    print("[PASS] risk_level='deny' 硬阻断测试通过")


def test_ask_risk_level_prompts():
    """risk_level='ask' 的工具应输出 permissionDecision: ask（exit 0）。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # mcp__slack__send_message 配置为 ask
        code, stdout, stderr = run_mcp_hook("mcp__slack__send_message", grants_dir)

        assert code == 0, f"ask 工具应返回 0（弹窗确认），实际 {code}"
        assert "hookSpecificOutput" in stdout, "ask 应输出 hookSpecificOutput 到 stdout"

        output = json.loads(stdout.strip())
        hook_output = output.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "ask", "决策类型应为 ask"
        assert "mcp__slack__send_message" in hook_output.get("permissionDecisionReason", ""), "reason 应包含工具名"

    print("[PASS] risk_level='ask' 弹窗确认测试通过")


def test_secret_overrides_ask_to_deny():
    """密钥泄漏应强制 deny，即使工具配置为 ask。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # slack send_message 配置为 ask，但输入包含密钥
        payload = {
            "tool_name": "mcp__slack__send_message",
            "tool_input": {"text": "sk-ant-1234567890abcdefghijklmnopqrstuvwxyz"},
        }
        env = os.environ.copy()
        env["CLAUDE_TEST_GRANTS_DIR"] = str(grants_dir)

        result = subprocess.run(
            [sys.executable, str(MCP_HOOK_SCRIPT)],
            input=json.dumps(payload),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env=env,
        )

        assert result.returncode == 2, f"密钥泄漏应强制 deny（exit 2），实际 {result.returncode}"
        assert "permissionDecision" not in result.stdout, "密钥泄漏不应输出 ask 决策"
        assert "凭据模式" in result.stderr, "stderr 应包含凭据拦截原因"

    print("[PASS] 密钥泄漏强制 deny 测试通过")


def test_grant_bypasses_all_risk_levels():
    """grant 授权应绕过所有 risk_level（deny 和 ask）。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # 创建 mcp grant
        grant_file = grants_dir / "mcp"
        grant_file.touch()

        # deny 工具有 grant 应放行
        code, _, _ = run_mcp_hook("mcp__github__force_push", grants_dir)
        assert code == 0, f"有 grant 的 deny 工具应放行，实际 exit {code}"

        # grant 应被消费
        assert not grant_file.exists(), "grant 应被消费"

        # ask 工具有 grant 也应放行（重新创建 grant）
        grant_file.touch()
        code, _, _ = run_mcp_hook("mcp__slack__send_message", grants_dir)
        assert code == 0, f"有 grant 的 ask 工具应放行，实际 exit {code}"
        assert not grant_file.exists(), "grant 应被消费"

    print("[PASS] grant 绕过所有 risk_level 测试通过")


def main() -> int:
    if not MCP_HOOK_SCRIPT.exists():
        print(f"错误：Hook 脚本未找到：{MCP_HOOK_SCRIPT}", file=sys.stderr)
        return 1
    if not BLOCKLIST_PATH.exists():
        print(f"错误：配置文件未找到：{BLOCKLIST_PATH}", file=sys.stderr)
        return 1

    try:
        test_deny_risk_level_blocks()
        test_ask_risk_level_prompts()
        test_secret_overrides_ask_to_deny()
        test_grant_bypasses_all_risk_levels()

        print("\n[OK] 全部 MCP risk_level 测试通过！")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败：{e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FAIL] 意外错误：{e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
