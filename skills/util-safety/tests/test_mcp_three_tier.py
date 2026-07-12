#!/usr/bin/env python3
"""测试 MCP 三层策略（P3-4）— allow 放行、block 按配置、未知 ask。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MCP_HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "mcp-safety.py"


def run_mcp_hook(tool_name: str, grants_dir: Path, tool_input: dict = None) -> tuple[int, str, str]:
    """运行 mcp-safety.py hook，返回 (returncode, stdout, stderr)。"""
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input or {"test": "data"},
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


def test_allow_pattern_passes():
    """allow_tool_patterns 命中的只读工具应直接放行。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # mcp__demo__get_user 命中 allow (get_)
        code, stdout, stderr = run_mcp_hook("mcp__demo__get_user", grants_dir)

        assert code == 0, f"allow 工具应放行（exit 0），实际 {code}"
        assert "permissionDecision" not in stdout, "allow 不应输出 ask 决策"
        assert not stderr.strip(), "allow 不应输出 stderr"

    print("[PASS] allow_pattern 直接放行测试通过")


def test_block_pattern_deny():
    """block_tool_patterns 命中的工具走 default_risk_level（deny）。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # mcp__demo__delete_record 命中 block (delete)
        code, stdout, stderr = run_mcp_hook("mcp__demo__delete_record", grants_dir)

        assert code == 2, f"block 工具应 deny（exit 2），实际 {code}"
        assert "permissionDecision" not in stdout, "deny 不应输出 ask 决策"
        assert "高风险动作模式" in stderr, "stderr 应包含拦截原因"

    print("[PASS] block_pattern deny 测试通过")


def test_unknown_verb_ask():
    """未知动词（不在 allow 也不在 block）走 unknown_verb_risk_level（ask）。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # mcp__demo__process_data 不命中 allow 也不命中 block
        code, stdout, stderr = run_mcp_hook("mcp__demo__process_data", grants_dir)

        assert code == 0, f"未知动词应 ask（exit 0），实际 {code}"
        assert "hookSpecificOutput" in stdout, "未知动词应输出 hookSpecificOutput"

        output = json.loads(stdout.strip())
        hook_output = output.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "ask", "决策类型应为 ask"
        assert "process_data" in hook_output.get("permissionDecisionReason", ""), "reason 应包含工具动词"

    print("[PASS] unknown_verb ask 测试通过")


def test_secret_overrides_allow():
    """密钥泄漏应强制 deny，即使工具命中 allow。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # mcp__demo__get_config 命中 allow，但包含密钥
        code, stdout, stderr = run_mcp_hook(
            "mcp__demo__get_config",
            grants_dir,
            {"api_key": "sk-ant-1234567890abcdefghijklmnopqrstuvwxyz"}
        )

        assert code == 2, f"密钥泄漏应强制 deny（exit 2），实际 {code}"
        assert "permissionDecision" not in stdout, "密钥泄漏不应输出 ask 决策"
        assert "凭据模式" in stderr, "stderr 应包含密钥拦截原因"

    print("[PASS] 密钥强制 deny 覆盖 allow 测试通过")


def main() -> int:
    if not MCP_HOOK_SCRIPT.exists():
        print(f"错误：Hook 脚本未找到：{MCP_HOOK_SCRIPT}", file=sys.stderr)
        return 1

    try:
        test_allow_pattern_passes()
        test_block_pattern_deny()
        test_unknown_verb_ask()
        test_secret_overrides_allow()

        print("\n[OK] 全部 MCP 三层策略测试通过！")
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
