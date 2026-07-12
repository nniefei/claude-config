#!/usr/bin/env python3
"""Unit tests for mcp-audit.py hook."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "mcp-audit.py"


def run_hook(payload: dict, home: Path) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=10,
        env=env,
    )
    return result.returncode, result.stdout.decode(), result.stderr.decode()


def test_mcp_call_writes_jsonl():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        code, _, stderr = run_hook({"tool_name": "mcp__acemcp__search_context", "tool_input": {"query": "hello"}}, home)
        assert code == 0, stderr
        log_path = home / ".claude" / "logs" / "mcp-audit.jsonl"
        assert log_path.exists(), "Audit log was not created"
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert entry["tool"] == "mcp__acemcp__search_context"
        assert "input_preview" in entry
    print("[PASS] MCP audit writes jsonl")


def test_large_input_truncated_and_non_mcp_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        code, _, stderr = run_hook({"tool_name": "mcp__server__tool", "tool_input": {"text": "A" * 500}}, home)
        assert code == 0, stderr
        log_path = home / ".claude" / "logs" / "mcp-audit.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
        assert len(entry["input_preview"]) <= 240

        code, _, stderr = run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, home)
        assert code == 0, stderr
        assert len(log_path.read_text(encoding="utf-8").splitlines()) == 1
    print("[PASS] MCP audit truncates and skips non-MCP")


def test_audit_fail_safe_on_bad_home():
    bad_home = Path("Z:/definitely/missing/path/for/mcp/audit")
    code, _, _ = run_hook({"tool_name": "mcp__server__tool", "tool_input": {"query": "hello"}}, bad_home)
    assert code == 0, "Audit hook must fail-safe"
    print("[PASS] MCP audit fail-safe")


def main() -> int:
    if not HOOK_SCRIPT.exists():
        print(f"Error: Hook script not found at {HOOK_SCRIPT}", file=sys.stderr)
        return 1
    try:
        test_mcp_call_writes_jsonl()
        test_large_input_truncated_and_non_mcp_skipped()
        test_audit_fail_safe_on_bad_home()
        print("\n[OK] All mcp-audit.py tests passed!")
        return 0
    except AssertionError as exc:
        print(f"\n[FAIL] Test failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[FAIL] Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
