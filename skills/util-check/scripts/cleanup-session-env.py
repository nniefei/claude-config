#!/usr/bin/env python3
"""清理旧的 session-env 目录，保留最近 N 个会话

用法:
    python cleanup-session-env.py              # dry-run 模式（仅预览）
    python cleanup-session-env.py --execute    # 执行归档
    python cleanup-session-env.py --keep 50    # 保留最近 50 个会话
"""
import os
import shutil
from pathlib import Path
from datetime import datetime
import argparse

CLAUDE_DIR = Path.home() / ".claude"
SESSION_ENV_DIR = CLAUDE_DIR / "session-env"
ARCHIVE_DIR = SESSION_ENV_DIR / "archive"
DEFAULT_KEEP_COUNT = 30


def cleanup_old_sessions(keep_count: int, dry_run: bool = True):
    """清理旧会话目录，归档到 archive/ 子目录"""
    if not SESSION_ENV_DIR.exists():
        print(f"❌ session-env 目录不存在：{SESSION_ENV_DIR}")
        return

    # 获取所有会话目录（排除 archive 目录本身）
    sessions = []
    for d in SESSION_ENV_DIR.iterdir():
        if d.is_dir() and d.name != "archive":
            sessions.append((d, d.stat().st_mtime))

    if not sessions:
        print("✅ 没有会话目录需要清理")
        return

    # 按时间降序排序（最新的在前）
    sessions.sort(key=lambda x: x[1], reverse=True)

    # 保留最近 N 个，归档其余
    to_keep = sessions[:keep_count]
    to_archive = sessions[keep_count:]

    print(f"统计信息")
    print(f"   总会话数：{len(sessions)}")
    print(f"   保留：{len(to_keep)} 个（最近 {keep_count} 个）")
    print(f"   归档：{len(to_archive)} 个")
    print()

    if not to_archive:
        print("✅ 所有会话都在保留范围内，无需归档")
        return

    if dry_run:
        print(f"[DRY RUN] 将归档以下目录到 {ARCHIVE_DIR.relative_to(CLAUDE_DIR)}：")
        print()
        for i, (path, mtime) in enumerate(to_archive[:10], 1):
            age_days = (datetime.now().timestamp() - mtime) / 86400
            print(f"  {i:2d}. {path.name} (距今 {age_days:.1f} 天)")

        if len(to_archive) > 10:
            print(f"  ... 还有 {len(to_archive) - 10} 个")

        print()
        print("提示：执行清理 python cleanup-session-env.py --execute")
    else:
        # 创建归档目录
        ARCHIVE_DIR.mkdir(exist_ok=True)

        # 归档旧会话
        archived_count = 0
        for path, _ in to_archive:
            try:
                dest = ARCHIVE_DIR / path.name
                shutil.move(str(path), str(dest))
                archived_count += 1
            except Exception as e:
                print(f"⚠️  归档失败：{path.name} - {e}")

        print(f"已归档 {archived_count} 个旧会话目录到 {ARCHIVE_DIR.relative_to(CLAUDE_DIR)}")
        print(f"磁盘空间释放：~{archived_count * 0.5:.1f} KB")
        print()
        print(f"如需恢复会话，从 {ARCHIVE_DIR.relative_to(CLAUDE_DIR)} 移回即可")


def main():
    parser = argparse.ArgumentParser(
        description="清理旧的 session-env 目录（归档模式）"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="执行归档操作（默认为 dry-run 模式）"
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP_COUNT,
        help=f"保留最近 N 个会话（默认 {DEFAULT_KEEP_COUNT}）"
    )

    args = parser.parse_args()

    print("session-env 清理工具（归档模式）")
    print("=" * 60)
    print()

    cleanup_old_sessions(keep_count=args.keep, dry_run=not args.execute)


if __name__ == "__main__":
    main()
