#!/usr/bin/env python3
"""Unit tests for session-start.py hook."""
import json
import subprocess
import sys
import time
from pathlib import Path

HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "session-start.py"
STARTUP_LOG = Path.home() / ".claude" / "logs" / "health-check-startup.jsonl"
DAILY_STAMP = Path.home() / ".claude" / "logs" / "health-check.daily-stamp"
DEDUP_CACHE = Path.home() / ".claude" / "logs" / "rule-injection-cache.json"


def run_hook(payload):
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        timeout=10,
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def test_returns_quickly():
    """Sync portion should return < 3 seconds even with full skills tree."""
    start = time.time()
    code, _, _ = run_hook({"hook_event_name": "SessionStart", "source": "startup"})
    elapsed = time.time() - start
    assert code == 0, f"Expected exit 0, got {code}"
    assert elapsed < 3.0, f"Sync portion took {elapsed:.2f}s, expected < 3s"
    print(f"[PASS] Returns quickly test passed ({elapsed:.2f}s)")


def test_spawns_background_check():
    """v2.12: 清除 daily stamp 后首次调用应 spawn（每天一次节流）。"""
    # 清除 daily stamp，确保首次 spawn
    DAILY_STAMP.unlink(missing_ok=True)
    before_size = STARTUP_LOG.stat().st_size if STARTUP_LOG.exists() else 0
    code, _, _ = run_hook({"hook_event_name": "SessionStart", "source": "startup"})
    assert code == 0
    # File should be created or grown
    assert STARTUP_LOG.exists(), "STARTUP_LOG should exist after spawn"
    after_size = STARTUP_LOG.stat().st_size
    assert after_size > before_size, "STARTUP_LOG should grow with spawn entry"
    # Last spawn entry should be parseable
    lines = STARTUP_LOG.read_text(encoding="utf-8").splitlines()
    spawn_entries = [line for line in lines if '"event": "spawned"' in line]
    assert len(spawn_entries) >= 1, "Should have at least one spawn entry"
    json.loads(spawn_entries[-1])  # parse-test
    # Daily stamp 应已写入
    assert DAILY_STAMP.exists(), "Daily stamp should be written after spawn"
    print("[PASS] Spawns background check test passed")

def test_daily_throttle_skips_second_call():
    """v2.12: 同一天内第二次调用应跳过 spawn（daily stamp 已存在）。"""
    # 确保 stamp 存在（由上一个测试或手动写入）
    from datetime import datetime, timezone
    DAILY_STAMP.parent.mkdir(parents=True, exist_ok=True)
    DAILY_STAMP.write_text(
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        encoding="utf-8",
    )
    before_size = STARTUP_LOG.stat().st_size if STARTUP_LOG.exists() else 0
    code, _, _ = run_hook({"hook_event_name": "SessionStart", "source": "startup"})
    assert code == 0
    # STARTUP_LOG 不应增长（没有新 spawn 事件）
    after_size = STARTUP_LOG.stat().st_size if STARTUP_LOG.exists() else 0
    assert after_size == before_size, \
        f"STARTUP_LOG should not grow on throttled call: {before_size} → {after_size}"
    print("[PASS] Daily throttle skips second call test passed")


def test_healthy_state_no_inject():
    """In healthy state (current real config), sync check finds no errors → no JSON output."""
    code, stdout, _ = run_hook({"hook_event_name": "SessionStart", "source": "startup"})
    assert code == 0
    # Healthy state: no additionalContext JSON in stdout
    json_lines = [line for line in stdout.splitlines() if line.strip().startswith("{")]
    assert not json_lines, f"Healthy state should not emit JSON, got: {json_lines}"
    print("[PASS] Healthy state no-inject test passed")


def test_never_blocks_on_malformed_input():
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=b"not json",
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, f"Should not block on malformed input: {result.returncode}"
    print("[PASS] Never blocks on malformed input test passed")


def test_performance_guard_no_crash():
    """P4: 同步段耗时检测逻辑不抛异常。"""
    code, stdout, stderr = run_hook({"hook_event_name": "SessionStart", "source": "startup"})
    assert code == 0, f"Performance guard should not crash hook: exit={code}"
    # 正常情况（<500ms）不应输出性能警告
    assert "同步自检耗时" not in stderr, \
        f"Healthy sync should not emit perf warning, got stderr: {stderr!r}"
    print("[PASS] Performance guard no-crash test passed")


def _run_dedup_case(source, sid, should_clear):
    """在去重缓存中预置 {sid: ["workflow.md"]}，跑 hook，验证是否按 source 清除。

    backup/restore 真实缓存文件，用不会与真实 session 冲突的测试专用 sid。
    """
    backup = DEDUP_CACHE.read_text(encoding="utf-8") if DEDUP_CACHE.exists() else None
    try:
        cache = json.loads(backup) if backup else {}
        cache[sid] = ["workflow.md"]
        DEDUP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        DEDUP_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

        code, _, _ = run_hook({
            "hook_event_name": "SessionStart",
            "source": source,
            "session_id": sid,
        })
        assert code == 0, f"hook should exit 0, got {code}"

        after = json.loads(DEDUP_CACHE.read_text(encoding="utf-8"))
        if should_clear:
            assert sid not in after, f"source={source} should clear sid, but it remains"
        else:
            assert after.get(sid) == ["workflow.md"], \
                f"source={source} should NOT clear sid, but it changed"
    finally:
        # 恢复原始缓存内容（剔除测试 sid）
        try:
            if backup is not None:
                DEDUP_CACHE.write_text(backup, encoding="utf-8")
            else:
                cur = json.loads(DEDUP_CACHE.read_text(encoding="utf-8"))
                cur.pop(sid, None)
                DEDUP_CACHE.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def test_dedup_cleared_on_compact():
    """P1: source=compact 应清除本 session 的去重记录，使规则重新注入。"""
    _run_dedup_case("compact", "test-p1-compact-sid", should_clear=True)
    print("[PASS] Dedup cleared on compact test passed")


def test_dedup_cleared_on_clear_and_resume():
    """P1: source=clear / resume 同样清除去重记录。"""
    _run_dedup_case("clear", "test-p1-clear-sid", should_clear=True)
    _run_dedup_case("resume", "test-p1-resume-sid", should_clear=True)
    print("[PASS] Dedup cleared on clear/resume test passed")


def test_dedup_preserved_on_startup():
    """P1: source=startup 不清除去重记录（全新会话缓存本就无记录，不应误删既有）。"""
    _run_dedup_case("startup", "test-p1-startup-sid", should_clear=False)
    print("[PASS] Dedup preserved on startup test passed")


def main():
    if not HOOK_SCRIPT.exists():
        print(f"Error: hook script not found at {HOOK_SCRIPT}", file=sys.stderr)
        return 1

    try:
        test_returns_quickly()
        test_spawns_background_check()
        test_healthy_state_no_inject()
        test_never_blocks_on_malformed_input()
        test_performance_guard_no_crash()
        test_dedup_cleared_on_compact()
        test_dedup_cleared_on_clear_and_resume()
        test_dedup_preserved_on_startup()
        test_daily_throttle_skips_second_call()

        print("\n[OK] All session-start.py tests passed!")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
