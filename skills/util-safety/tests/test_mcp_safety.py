#!/usr/bin/env python3
"""mcp-safety.py hook 单元测试。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows GBK 终端强制 UTF-8（重新打开 fd 绕过默认编码）
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, closefd=False)
except Exception:
    pass



HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "mcp-safety.py"
GRANTS_DIR = Path.home() / ".claude" / ".grants"


def _clean_grants():
    grant_file = GRANTS_DIR / "mcp"
    if grant_file.exists():
        try:
            grant_file.unlink()
        except OSError:
            pass


def run_hook(payload: dict | bytes, approved: bool = False) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.pop("CLAUDE_HOOK_APPROVED_MCP", None)
    _clean_grants()
    if approved:
        env["CLAUDE_HOOK_APPROVED_MCP"] = "1"
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=data,
        capture_output=True,
        timeout=10,
        env=env,
    )
    return result.returncode, result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace")


def test_normal_mcp_call_passes():
    code, _, stderr = run_hook({"tool_name": "mcp__acemcp__search_context", "tool_input": {"query": "hello"}})
    assert code == 0, stderr
    print("[PASS] 正常 MCP 调用通过")


def test_secret_patterns_block():
    secret_cases = [
        {"token": "sk-" + "A" * 24},
        {"token": "ghp_" + "A" * 32},
        # P2：纯"凭据赋值"模式现走熵复核，需用高熵随机值（全 A 熵=0 会被判误报放行）
        {"nested": {"auth_token": "j5K2pL8nQ3rX7wZ9bV4cM6dF1aH0sG"}},
    ]
    for tool_input in secret_cases:
        code, _, stderr = run_hook({"tool_name": "mcp__server__tool", "tool_input": tool_input})
        assert code == 2, f"Secret input not blocked: {tool_input}"
        assert "凭据" in stderr or "credential" in stderr.lower(), stderr
    print("[PASS] MCP 密钥模式阻断测试通过")


def test_blocklist_requires_marker():
    # v2.13: 使用 deny 级别工具（force_push）验证硬阻断；ask 级别工具返回 0+弹窗
    payload = {"tool_name": "mcp__github__force_push", "tool_input": {"reason": "test"}}
    code, _, stderr = run_hook(payload)
    assert code == 2, "deny 级别 MCP 工具应被硬阻断"
    assert "黑名单" in stderr or "blocklist" in stderr.lower() or "拦截" in stderr, stderr

    code, _, stderr = run_hook(payload, approved=True)
    assert code == 0, stderr
    print("[PASS] MCP 黑名单标记行为测试通过")


def test_block_tool_patterns():
    """P6：未列入精确黑名单但命中高风险动作模式的工具应被拦。"""
    block_cases = [
        "mcp__notion__delete_page",
        "mcp__fs__remove_file",
        "mcp__x__publish_post",
    ]
    for tool in block_cases:
        code, _, stderr = run_hook({"tool_name": tool, "tool_input": {"x": "y"}})
        assert code == 2, f"高风险模式工具未被拦：{tool}"
        assert "高风险动作模式" in stderr, stderr
    print("[PASS] MCP 高风险动作模式阻断测试通过")


def test_allow_tool_patterns_not_blocked():
    """P6：只读动词工具不应被误拦，含写动词子串的只读工具（get_sender）也不误拦。"""
    allow_cases = [
        "mcp__acemcp__search_context",
        "mcp__api__list_users",
        "mcp__db__get_sender",  # 含 send 子串但 get_ 前缀豁免
    ]
    for tool in allow_cases:
        code, _, stderr = run_hook({"tool_name": tool, "tool_input": {"x": "y"}})
        assert code == 0, f"只读工具被误拦：{tool}（{stderr}）"
    print("[PASS] MCP 只读动词豁免测试通过")


def test_non_mcp_and_malformed():
    code, _, stderr = run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}})
    assert code == 0, stderr

    code, _, stderr = run_hook(b"not json")
    assert code == 2, "畸形 JSON 应 fail-closed"
    assert "解析" in stderr or "parse" in stderr.lower(), stderr
    print("[PASS] 非 MCP 放行及畸形输入 fail-closed 测试通过")


def main() -> int:
    if not HOOK_SCRIPT.exists():
        print(f"错误：Hook 脚本未找到：{HOOK_SCRIPT}", file=sys.stderr)
        return 1
    try:
        test_normal_mcp_call_passes()
        test_secret_patterns_block()
        test_blocklist_requires_marker()
        test_block_tool_patterns()
        test_allow_tool_patterns_not_blocked()
        test_non_mcp_and_malformed()
        print("\n[OK] 全部 mcp-safety.py 测试通过！")
        return 0
    except AssertionError as exc:
        print(f"\n[FAIL] 测试失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[FAIL] 意外错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
