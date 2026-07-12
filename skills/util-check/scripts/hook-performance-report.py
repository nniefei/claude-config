#!/usr/bin/env python3
"""生成 Hook 性能报告

分析 logs/hook-performance.jsonl，生成性能统计报告。

用法:
    python hook-performance-report.py              # 生成完整报告
    python hook-performance-report.py --top 10     # 只显示 Top 10 慢操作
"""
import json
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def analyze_performance(log_path: Path, top_n: int = None):
    """分析性能日志，生成报告"""
    if not log_path.exists():
        print("暂无性能数据")
        print(f"日志路径：{log_path}")
        print()
        print("性能数据将在下次 Hook 执行后自动记录。")
        return

    # 按 Hook 类型分组统计
    stats = defaultdict(lambda: {
        "count": 0,
        "total_ms": 0,
        "max_ms": 0,
        "min_ms": float('inf'),
        "allow_count": 0,
        "deny_count": 0,
        "samples": [],
    })

    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                hook = entry["hook"]
                elapsed = entry["elapsed_ms"]
                decision = entry.get("decision", "unknown")

                stats[hook]["count"] += 1
                stats[hook]["total_ms"] += elapsed
                stats[hook]["max_ms"] = max(stats[hook]["max_ms"], elapsed)
                stats[hook]["min_ms"] = min(stats[hook]["min_ms"], elapsed)

                if decision == "allow":
                    stats[hook]["allow_count"] += 1
                elif decision == "deny":
                    stats[hook]["deny_count"] += 1

                # 保留慢操作样本（Top 10）
                stats[hook]["samples"].append((elapsed, entry))
                stats[hook]["samples"].sort(reverse=True, key=lambda x: x[0])
                stats[hook]["samples"] = stats[hook]["samples"][:10]

            except Exception:
                continue

    # 输出报告
    print("=" * 70)
    print("Hook 性能统计报告")
    print("=" * 70)
    print()

    for hook, data in sorted(stats.items()):
        avg_ms = data["total_ms"] / data["count"] if data["count"] > 0 else 0

        print(f"[{hook}]")
        print(f"  调用次数：{data['count']}")
        print(f"  平均耗时：{avg_ms:.2f} ms")
        print(f"  最小耗时：{data['min_ms']:.2f} ms")
        print(f"  最大耗时：{data['max_ms']:.2f} ms")
        print(f"  放行次数：{data['allow_count']}")
        print(f"  拦截次数：{data['deny_count']}")
        print()

        # 显示慢操作样本
        if top_n and data["samples"]:
            print(f"  Top {min(top_n, len(data['samples']))} 慢操作：")
            for i, (elapsed, entry) in enumerate(data["samples"][:top_n], 1):
                ts = entry.get("ts", "")
                reason = entry.get("reason", "")
                decision = entry.get("decision", "")

                # 根据 hook 类型显示不同的标识符
                if hook == "bash-safety-wrapper":
                    identifier = entry.get("command_hash", "")[:8]
                    label = f"cmd:{identifier}"
                elif hook == "write-safety":
                    identifier = entry.get("file_hash", "")[:8]
                    label = f"file:{identifier}"
                elif hook == "mcp-safety":
                    identifier = entry.get("tool_hash", "")[:8]
                    label = f"tool:{identifier}"
                else:
                    label = ""

                print(f"    {i}. {elapsed:6.2f} ms | {label} | {decision} | {ts[:19]}")
                if reason:
                    print(f"       原因: {reason}")
            print()

    # 总体统计
    total_calls = sum(s["count"] for s in stats.values())
    total_time = sum(s["total_ms"] for s in stats.values())
    overall_avg = total_time / total_calls if total_calls > 0 else 0

    print("=" * 70)
    print("总体统计")
    print("=" * 70)
    print(f"总调用次数：{total_calls}")
    print(f"总耗时：{total_time:.2f} ms")
    print(f"平均耗时：{overall_avg:.2f} ms")
    print()

    # 性能建议
    print("=" * 70)
    print("性能建议")
    print("=" * 70)
    for hook, data in stats.items():
        avg_ms = data["total_ms"] / data["count"]
        if avg_ms > 20:
            print(f"- {hook}: 平均耗时 {avg_ms:.2f} ms，建议优化")
        elif avg_ms > 10:
            print(f"- {hook}: 平均耗时 {avg_ms:.2f} ms，可接受但有优化空间")
        else:
            print(f"- {hook}: 平均耗时 {avg_ms:.2f} ms，性能良好")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="生成 Hook 性能统计报告"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="显示 Top N 慢操作（默认 5）"
    )

    args = parser.parse_args()

    log_path = Path.home() / ".claude" / "logs" / "hook-performance.jsonl"
    analyze_performance(log_path, top_n=args.top)


if __name__ == "__main__":
    main()
