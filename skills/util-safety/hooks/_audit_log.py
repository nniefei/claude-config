"""审计日志共享工具 —— 跨平台文件锁 + 大小/行数轮转。

被 bash-safety-wrapper.py / mcp-audit.py 通过 importlib 动态加载，消除两处
逐字重复的审计日志锁实现。函数均接受 path 与限值参数，不依赖调用方的模块级常量。

加载失败时调用方应 fail-safe（跳过审计，不阻断主流程）——审计是旁路，
其失败不应影响安全拦截或正常工具调用。
"""
import glob
import os
from datetime import datetime, timezone
from pathlib import Path

# 归档文件保留上限（默认保留最新 10 个）
MAX_ARCHIVE_FILES = 10


def audit_log_line_count(path: Path, max_lines: int) -> int:
    """统计日志行数；超过 max_lines 即提前停止（无需精确总数）。"""
    count = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for count, _ in enumerate(handle, 1):
                if count > max_lines:
                    break
    except FileNotFoundError:
        return 0
    return count


def _cleanup_old_archives(log_path: Path) -> None:
    """清理旧的归档文件，保留最新 MAX_ARCHIVE_FILES 个。"""
    try:
        pattern = str(log_path) + ".*.1"
        archives = sorted(glob.glob(pattern), reverse=True)
        for old_archive in archives[MAX_ARCHIVE_FILES:]:
            try:
                os.remove(old_archive)
            except OSError:
                pass
    except Exception:
        pass  # fail-safe


def rotate_audit_log_if_needed(path: Path, max_bytes: int, max_lines: int) -> None:
    """日志超过大小或行数上限时，重命名归档（加时间戳后缀）。"""
    if not path.exists():
        return
    if path.stat().st_size <= max_bytes and audit_log_line_count(path, max_lines) <= max_lines:
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path.replace(path.with_name(f"{path.name}.{timestamp}.1"))
    # 清理旧归档
    _cleanup_old_archives(path)


def with_audit_log_lock(log_path: Path, callback) -> None:
    """跨平台文件锁（Windows msvcrt / POSIX fcntl）下执行 callback。"""
    lock_path = log_path.with_suffix(log_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import msvcrt
        with open(lock_path, "a+", encoding="utf-8") as lock_handle:
            lock_handle.seek(0)
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                callback()
            finally:
                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        with open(lock_path, "a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                callback()
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def append_audit_log(log_path: Path, line: str, max_bytes: int, max_lines: int) -> None:
    """加锁 + 按需轮转后，追加一行到审计日志。"""
    def _append() -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        rotate_audit_log_if_needed(log_path, max_bytes, max_lines)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line)

    with_audit_log_lock(log_path, _append)
