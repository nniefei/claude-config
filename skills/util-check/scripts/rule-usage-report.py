#!/usr/bin/env python3
"""生成规则使用频率报告"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows GBK 终端强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LOGS_DIR = Path.home() / ".claude" / "logs"
STATS_PATH = LOGS_DIR / "rule-usage-stats.json"


def load_stats():
    """加载统计数据"""
    if not STATS_PATH.exists():
        return {}
    try:
        return json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def calculate_days_ago(iso_timestamp):
    """计算距今天数"""
    try:
        last_used = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - last_used
        return delta.days
    except Exception:
        return None


def frequency_label(count):
    """根据调用次数返回频率标签"""
    if count >= 100:
        return "高频 ⭐⭐⭐"
    elif count >= 30:
        return "中频 ⭐⭐"
    elif count >= 10:
        return "低频 ⭐"
    else:
        return "极低频"


def main():
    stats = load_stats()

    if not stats:
        print("📊 规则使用频率报告\n")
        print("暂无统计数据（规则文件尚未被加载过）")
        return 0

    # 按调用次数排序
    sorted_rules = sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True)

    print("📊 规则使用频率报告\n")
    print("| 规则文件 | 调用次数 | 最后使用 | 使用频率 |")
    print("|:---|---:|:---|:---|")

    for rule_name, data in sorted_rules:
        count = data.get("count", 0)
        last_used = data.get("last_used", "")
        days_ago = calculate_days_ago(last_used)

        if days_ago is not None:
            if days_ago == 0:
                last_used_str = "今天"
            elif days_ago == 1:
                last_used_str = "昨天"
            else:
                last_used_str = f"{days_ago} 天前"
        else:
            last_used_str = "未知"

        freq = frequency_label(count)

        print(f"| {rule_name} | {count} | {last_used_str} | {freq} |")

    # 统计摘要
    total_rules = len(sorted_rules)
    total_loads = sum(data["count"] for data in stats.values())
    avg_loads = total_loads / total_rules if total_rules > 0 else 0

    print(f"\n### 统计摘要\n")
    print(f"- 规则文件总数：{total_rules} 个")
    print(f"- 累计加载次数：{total_loads} 次")
    print(f"- 平均每个规则被加载：{avg_loads:.1f} 次")

    # 未使用规则提示
    all_rules = {"workflow.md", "git-safety.md",
                 "skill-org.md", "core-principles.md"}
    unused_rules = all_rules - set(stats.keys())

    if unused_rules:
        print(f"\n### ⚠️ 未使用的规则文件\n")
        for rule in sorted(unused_rules):
            print(f"- `{rule}` — 从未被触发，考虑检查触发条件")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
