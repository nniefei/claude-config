#!/usr/bin/env python3
"""统一测试入口：自动发现、按拓扑排序、汇总报告。

设计要点：
  - 自动发现 tests/ 下所有 test_*.py 文件
  - 按依赖拓扑排序（先运行基础模块测试，再运行集成测试）
  - 单个测试失败不中止，继续运行后续测试
  - 最终汇总：通过/失败/跳过数量、总耗时、失败文件清单
"""
import subprocess
import sys
import time
from pathlib import Path

# Windows GBK 终端强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TESTS_DIR = Path(__file__).resolve().parent

# 测试拓扑排序：基础 → 集成
# 第一层：基础单元测试（无跨文件依赖）
TIER_1_BASIC = [
    "test_pattern_performance.py",
]

# 第二层：单 hook 单元测试
TIER_2_HOOKS = [
    "test_bash_safety.py",
    "test_write_safety.py",
    "test_mcp_safety.py",
    "test_mcp_risk_level.py",
    "test_mcp_three_tier.py",
    "test_mcp_audit.py",
    "test_rule_loader.py",
    "test_rule_loader_ttl.py",
    "test_session_start.py",
]

# 第三层：hook 集成测试（需要多个 hook 协作或并发场景）
TIER_3_INTEGRATION = [
    "test_bash_audit_post.py",
    "test_audit_log_concurrency.py",
    "test_mcp_grant_concurrency.py",
]

ORDERED_TESTS = TIER_1_BASIC + TIER_2_HOOKS + TIER_3_INTEGRATION


def run_test(test_file: Path) -> dict:
    """运行单个测试文件，返回 {name, passed, duration, error}。"""
    start = time.time()
    result = subprocess.run(
        [sys.executable, str(test_file)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    duration = time.time() - start

    return {
        "name": test_file.name,
        "passed": result.returncode == 0,
        "duration": duration,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def print_separator(char="=", length=80):
    print(char * length)


def main() -> int:
    print_separator()
    print("Skills 测试套件统一入口")
    print_separator()
    print()

    results = []
    all_start = time.time()

    for test_name in ORDERED_TESTS:
        test_path = TESTS_DIR / test_name
        if not test_path.exists():
            print(f"[SKIP] {test_name} -- file not found")
            results.append({
                "name": test_name,
                "passed": None,
                "duration": 0,
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            })
            continue

        print(f">> Running {test_name} ...", end=" ", flush=True)
        try:
            result = run_test(test_path)
            results.append(result)
            if result["passed"]:
                print(f"[PASS] ({result['duration']:.2f}s)")
            else:
                print(f"[FAIL] ({result['duration']:.2f}s)")
        except subprocess.TimeoutExpired:
            print("[FAIL] TIMEOUT (>120s)")
            results.append({
                "name": test_name,
                "passed": False,
                "duration": 120,
                "stdout": "",
                "stderr": "Test timed out after 120 seconds",
                "returncode": -1,
            })
        except Exception as e:
            print(f"[FAIL] ERROR: {e}")
            results.append({
                "name": test_name,
                "passed": False,
                "duration": 0,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
            })

    all_duration = time.time() - all_start

    # 汇总报告
    print()
    print_separator()
    print("Test Summary")
    print_separator()

    passed = [r for r in results if r["passed"] is True]
    failed = [r for r in results if r["passed"] is False]
    skipped = [r for r in results if r["passed"] is None]

    print(f"[PASS] Passed: {len(passed)}/{len(results)}")
    print(f"[FAIL] Failed: {len(failed)}/{len(results)}")
    print(f"[SKIP] Skipped: {len(skipped)}/{len(results)}")
    print(f"[TIME] Total: {all_duration:.2f}s")
    print()

    if failed:
        print("Failed tests:")
        for r in failed:
            print(f"  - {r['name']} (exit {r['returncode']})")
            if r["stderr"]:
                stderr_preview = r["stderr"][:200].replace("\n", " ")
                print(f"    Error: {stderr_preview}...")
        print()

    # 详细输出（可选）
    if "--verbose" in sys.argv or "-v" in sys.argv:
        print_separator("-")
        print("Detailed Output")
        print_separator("-")
        for r in results:
            status = "[PASS]" if r["passed"] else ("[SKIP]" if r["passed"] is None else "[FAIL]")
            print(f"\n{status} {r['name']} ({r['duration']:.2f}s)")
            if r["stdout"]:
                print("STDOUT:")
                print(r["stdout"])
            if r["stderr"]:
                print("STDERR:")
                print(r["stderr"])

    print_separator()
    if failed:
        print(f"[FAIL] Test suite failed: {len(failed)} test(s) did not pass")
        return 1
    else:
        print(f"[PASS] All tests passed! ({len(passed)} test(s))")
        return 0


if __name__ == "__main__":
    sys.exit(main())
