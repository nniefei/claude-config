#!/usr/bin/env python3
"""SECRET_PATTERNS regex 性能基准测试（子计划 5）。

验证所有 pattern 在极端大输入下不触发灾难性回溯。
注意：此测试不在 CI/health-check 中运行，仅在独立 pytest 中执行。
"""
import importlib.util
import re
import sys
import time
from pathlib import Path


def _load_patterns():
    shared_path = Path(__file__).resolve().parent.parent / "hooks" / "_shared_patterns.py"
    spec = importlib.util.spec_from_file_location("_shared_patterns", shared_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_shared_patterns"] = module
    spec.loader.exec_module(module)
    return module.SECRET_PATTERNS


SECRET_PATTERNS = _load_patterns()


def test_secret_patterns_no_catastrophic_backtracking():
    """1MB 类密钥输入下每个 pattern 耗时 < 500ms。"""
    big_input = "sk-" + "A" * 1_000_000

    for label, pattern in SECRET_PATTERNS:
        t0 = time.perf_counter()
        pattern.search(big_input)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.5, \
            f"{label} 在 1MB 输入下耗时 {elapsed:.3f}s（超过 500ms 阈值）"
        print(f"  {label}: {elapsed*1000:.1f}ms (1MB input)")

    print("[PASS] SECRET_PATTERNS 无灾难性回溯")


def test_secret_patterns_with_many_false_positives():
    """10000 行类密钥文本（每行一个部分匹配）每个 pattern 耗时 < 1s。"""
    lines = []
    for i in range(10000):
        lines.append(f"const key_{i} = 'sk-abc123xyz'")

    big_input = "\n".join(lines)

    for label, pattern in SECRET_PATTERNS:
        t0 = time.perf_counter()
        list(pattern.finditer(big_input))
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, \
            f"{label} 在 10000 行输入下耗时 {elapsed:.3f}s（超过 1s 阈值）"
        print(f"  {label}: {elapsed*1000:.1f}ms (10000 lines)")

    print("[PASS] SECRET_PATTERNS 多假阳性性能达标")


def main():
    try:
        test_secret_patterns_no_catastrophic_backtracking()
        test_secret_patterns_with_many_false_positives()
        print("\n[OK] 全部 SECRET_PATTERNS 性能基准测试通过！")
        return 0
    except AssertionError as e:
        print(f"\n[FAIL] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FAIL] 意外错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
