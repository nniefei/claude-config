#!/usr/bin/env python3
"""Claude Code Hook Runner — 自定位 Hook 调度器

将 .claude 目录中散落的 Python 路径配置集中到 env.json，
从此迁移只需改一个文件。

用法 (Windows):
  hook-runner.cmd bash-safety-wrapper.py

用法 (Unix):
  ./hook-runner.sh write-safety.py

协议：透传 stdin/stdout/stderr，透传退出码。
"""
import json
import os
import subprocess
import sys


def find_claude_root():
    """自定位：从本脚本路径反推 .claude/ 根目录"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # scripts/ 下的 hook-runner.py → 父目录为 .claude/
    candidate = os.path.normpath(os.path.join(script_dir, '..'))
    # 校验：确保 skills/rules/ 存在，确认是 .claude/ 根目录
    marker = os.path.join(candidate, 'skills', 'rules')
    if os.path.isdir(marker):
        return candidate
    # 回退：当前目录
    return script_dir


def load_config(claude_root):
    """读取 env.json，不存在则返回空字典"""
    env_file = os.path.join(claude_root, 'env.json')
    if not os.path.isfile(env_file):
        return {}
    try:
        with open(env_file, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_python(config):
    """确定 Python 解释器路径"""
    python_exe = config.get('python_exe', '')
    if python_exe:
        return python_exe
    # 没有 env.json 配置则用当前解释器
    return sys.executable or 'python3'


def get_creation_flags(os_type, python_exe):
    """Windows 上用 pythonw.exe 时避免闪控制台窗口"""
    if os_type == 'windows' or sys.platform == 'win32':
        if 'pythonw' in python_exe:
            return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}


def main():
    if len(sys.argv) < 2:
        print("用法: hook-runner.py <hook_script_name> [args...]", file=sys.stderr)
        sys.exit(2)

    claude_root = find_claude_root()
    config = load_config(claude_root)
    python_exe = resolve_python(config)
    os_type = config.get('os_type', sys.platform)
    debug = config.get('debug', False)

    # 拼接 hook 脚本路径
    hook_name = sys.argv[1]
    hook_script = os.path.join(claude_root, 'skills', 'util-safety', 'hooks', hook_name)

    if not os.path.isfile(hook_script):
        print(f"[hook-runner] 错误: hook 脚本不存在: {hook_script}", file=sys.stderr)
        sys.exit(2)

    # 构建命令行
    cmd = [python_exe, hook_script] + sys.argv[2:]

    if debug:
        print(f"[hook-runner] claude_root={claude_root}", file=sys.stderr)
        print(f"[hook-runner] python_exe={python_exe}", file=sys.stderr)
        print(f"[hook-runner] hook_script={hook_script}", file=sys.stderr)
        print(f"[hook-runner] cmd={cmd}", file=sys.stderr)

    # 运行 hook（透传 I/O）
    kwargs = get_creation_flags(os_type, python_exe)
    try:
        proc = subprocess.run(
            cmd,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            **kwargs
        )
        sys.exit(proc.returncode)
    except FileNotFoundError:
        print(f"[hook-runner] 错误: 找不到 Python 解释器: {python_exe}", file=sys.stderr)
        print(f"[hook-runner] 请在 env.json 中设置正确的 python_exe 路径", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"[hook-runner] 错误: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
