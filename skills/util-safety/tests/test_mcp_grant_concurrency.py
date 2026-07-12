#!/usr/bin/env python3
"""MCP grant 消费并发安全性验证 — 验证 acquire_mcp_grant() TOCTOU 修复。"""
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

MCP_HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "mcp-safety.py"


def _call_mcp_hook(grants_dir, process_id):
    """调用 mcp-safety.py hook，命中黑名单需 grant 才能放行。"""
    payload = {
        "tool_name": "mcp__github__force_push",
        "tool_input": {"branch": f"test-{process_id}"},
    }
    env = os.environ.copy()
    # 指向隔离的 grants 目录
    env["CLAUDE_TEST_GRANTS_DIR"] = str(grants_dir)
    # 不设置 CLAUDE_HOOK_APPROVED_MCP，强制走 grant 文件路径

    result = subprocess.run(
        [sys.executable, str(MCP_HOOK_SCRIPT)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=10,
        env=env,
    )
    # 返回是否放行（exit 0）
    return result.returncode == 0


def _call_mcp_hook_with_env(grants_dir, process_id):
    """使用 env 授权调用 mcp-safety.py，应放行且不消费 grant。"""
    payload = {
        "tool_name": "mcp__github__force_push",
        "tool_input": {"branch": f"test-{process_id}"},
    }
    env = os.environ.copy()
    env["CLAUDE_TEST_GRANTS_DIR"] = str(grants_dir)
    env["CLAUDE_HOOK_APPROVED_MCP"] = "1"

    result = subprocess.run(
        [sys.executable, str(MCP_HOOK_SCRIPT)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=10,
        env=env,
    )
    return result.returncode == 0


def test_single_grant_concurrent_contention():
    """8 并发进程争抢单个 mcp grant，仅恰好 1 个放行，其余被拒。

    验证 acquire_mcp_grant() 的文件锁原子化「检查 + 消费」后，
    不存在「两个进程都通过 exists() 检查、都 unlink 同一个 grant」的竞态。
    """
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # 创建单个一次性 grant
        grant_file = grants_dir / "mcp"
        grant_file.touch()

        num_procs = 8
        with multiprocessing.Pool(processes=num_procs) as pool:
            results = pool.starmap(_call_mcp_hook, [(grants_dir, i) for i in range(num_procs)])

        passed = sum(results)
        assert passed == 1, f"期望恰好 1 个进程放行（消费 grant），实际 {passed} 个（存在 TOCTOU 竞态）"

        # grant 文件应已被消费
        assert not grant_file.exists(), "grant 文件应被消费后删除"

    print("[PASS] MCP single grant 并发争抢测试通过 — 恰好 1 个放行")


def test_env_grant_not_consumed():
    """env 授权不消费，所有并发进程都应放行。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        num_procs = 5
        with multiprocessing.Pool(processes=num_procs) as pool:
            results = pool.starmap(_call_mcp_hook_with_env, [(grants_dir, i) for i in range(num_procs)])

        assert all(results), f"env 授权应放行所有进程，实际失败 {sum(not r for r in results)} 个"

    print("[PASS] MCP env 授权不消费测试通过 — 所有进程放行")


def test_session_grant_not_consumed():
    """会话级 grant（mcp.session）不消费，所有并发进程都应放行。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)

        # 创建会话级 grant
        session_grant = grants_dir / "mcp.session"
        session_grant.touch()

        num_procs = 5
        with multiprocessing.Pool(processes=num_procs) as pool:
            results = pool.starmap(_call_mcp_hook, [(grants_dir, i) for i in range(num_procs)])

        assert all(results), f"会话级 grant 应放行所有进程，实际失败 {sum(not r for r in results)} 个"

        # 会话级 grant 不应被消费
        assert session_grant.exists(), "会话级 grant 不应被删除"

    print("[PASS] MCP 会话级 grant 不消费测试通过 — 所有进程放行且 grant 保留")


def test_no_grant_all_blocked():
    """无 grant 时，所有并发进程都应被拒。"""
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / ".grants"
        grants_dir.mkdir(parents=True, exist_ok=True)
        # 不创建任何 grant

        num_procs = 5
        with multiprocessing.Pool(processes=num_procs) as pool:
            results = pool.starmap(_call_mcp_hook, [(grants_dir, i) for i in range(num_procs)])

        assert not any(results), f"无 grant 应拒绝所有进程，实际放行 {sum(results)} 个"

    print("[PASS] MCP 无 grant 全拒测试通过")


def main():
    if not MCP_HOOK_SCRIPT.exists():
        print(f"错误：Hook 脚本未找到：{MCP_HOOK_SCRIPT}", file=sys.stderr)
        return 1

    try:
        test_single_grant_concurrent_contention()
        test_env_grant_not_consumed()
        test_session_grant_not_consumed()
        test_no_grant_all_blocked()

        print("\n[OK] 全部 MCP grant 并发测试通过！")
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
