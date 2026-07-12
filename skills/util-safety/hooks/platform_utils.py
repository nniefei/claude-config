"""跨平台工具模块：封装平台特定逻辑。

提供统一的接口，隐藏 Windows/macOS/Linux 的实现差异。
用于替代各 hook 脚本中分散的平台判断代码。
"""
import os
import signal
import subprocess
import sys
from pathlib import Path


def is_windows() -> bool:
    """判断是否为 Windows 平台。"""
    return sys.platform == "win32"


def is_macos() -> bool:
    """判断是否为 macOS 平台。"""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """判断是否为 Linux 平台。"""
    return sys.platform.startswith("linux")


def get_python_exe() -> str:
    """获取适合后台运行的 Python 解释器路径。

    Windows: 优先使用 pythonw.exe（无控制台窗口）
    macOS/Linux: 使用 python3 或当前解释器
    """
    python_exe = sys.executable

    if is_windows():
        # Windows 上优先使用 pythonw.exe（无控制台窗口）
        if python_exe.endswith("python.exe"):
            pythonw = python_exe[:-10] + "pythonw.exe"
            if Path(pythonw).exists():
                return pythonw
    # macOS/Linux 或 Windows 上 pythonw.exe 不存在时，使用当前解释器
    return python_exe


def get_creation_flags() -> int:
    """获取 subprocess 创建标志。

    Windows: CREATE_NO_WINDOW（无控制台窗口）
    macOS/Linux: 0（不需要特殊标志）
    """
    if is_windows():
        return subprocess.CREATE_NO_WINDOW
    return 0


def kill_process(pid: int) -> bool:
    """跨平台终止进程。

    Windows: 使用 taskkill（带 IMAGENAME 过滤）
    macOS/Linux: 使用 os.kill (SIGTERM)

    Returns:
        bool: 是否成功终止
    """
    try:
        if is_windows():
            # Windows: 使用 taskkill 带 IMAGENAME 过滤
            # 合并为单次 taskkill 带 IMAGENAME 过滤，避免 tasklist 校验与 taskkill
            # 两步间 PID 被 OS 复用导致误杀无关进程（TOCTOU）。
            # /FI "IMAGENAME eq pythonw.exe" 确保只终止 Python 进程；若 PID 已非
            # pythonw.exe（复用为其他进程），taskkill 返回非 0 但不误杀。
            result = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/FI", "IMAGENAME eq pythonw.exe"],
                capture_output=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        else:
            # macOS/Linux: 使用 SIGTERM
            os.kill(pid, signal.SIGTERM)
            return True
    except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
        # 进程不存在或终止失败
        return False


def get_lock_mechanism():
    """获取文件锁机制。

    返回一个上下文管理器，用于跨平台文件锁定。
    Windows: msvcrt
    macOS/Linux: fcntl
    """
    if is_windows():
        import msvcrt

        class WindowsFileLock:
            def __init__(self, file_handle):
                self.file_handle = file_handle

            def __enter__(self):
                msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_LOCK, 1)
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_UNLCK, 1)

        return WindowsFileLock
    else:
        import fcntl

        class UnixFileLock:
            def __init__(self, file_handle):
                self.file_handle = file_handle

            def __enter__(self):
                fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_EX)
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_UN)

        return UnixFileLock


def get_shell_redirect_stderr() -> str:
    """获取 stderr 重定向命令。

    Windows: 2>NUL
    macOS/Linux: 2>/dev/null
    """
    if is_windows():
        return "2>NUL"
    return "2>/dev/null"
