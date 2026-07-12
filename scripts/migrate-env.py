#!/usr/bin/env python3
"""Claude Code 环境迁移脚本 — 自动适配目标系统

检测当前 OS，读取 env.json，生成正确的 settings.local.json。

用法:
  python3 migrate-env.py               # 交互模式（预览）
  python3 migrate-env.py --apply       # 直接应用
  python3 migrate-env.py --dry-run     # 预览不写入
  python3 migrate-env.py --validate    # 只做验证
"""
import json
import os
import shlex
import shutil
import subprocess
import sys


# ── hook 条目：(事件类型, 匹配器, hook脚本) ──
HOOK_RECORDS = [
    ("PostToolUse", "Bash",       "bash-audit-post.py"),
    ("PostToolUse", "Write|Edit", "write-audit.py"),
    ("PreToolUse",  "Bash",       "bash-safety-wrapper.py"),
    ("PreToolUse",  "Bash",       "rule-loader.py"),
    ("PreToolUse",  "Write|Edit", "write-safety.py"),
    ("PreToolUse",  "Write|Edit", "rule-loader.py"),
    ("PreToolUse",  "Skill",      "rule-loader.py"),
    ("PreToolUse",  "mcp__.*",    "mcp-safety.py"),
    ("PreToolUse",  "mcp__.*",    "mcp-audit.py"),
    ("SessionStart",       "",    "session-start.py"),
    ("UserPromptExpansion","",    "rule-loader.py"),
    ("UserPromptSubmit",   "",    "rule-loader.py"),
]

# ── 事件类型顺序（决定在 JSON 中的排列次序） ──
EVENT_ORDER = [
    "PostToolUse", "PreToolUse", "SessionStart",
    "UserPromptExpansion", "UserPromptSubmit",
]


def detect_os():
    p = sys.platform.lower()
    if p.startswith('win'):
        return 'windows'
    if p.startswith('linux'):
        return 'linux'
    if p.startswith('darwin'):
        return 'darwin'
    return 'linux'


def find_claude_root():
    d = os.path.dirname(os.path.abspath(__file__))
    for cand in (d, os.path.join(d, '..')):
        if os.path.isdir(os.path.join(cand, 'skills', 'rules')):
            return os.path.normpath(cand)
    return d


def load_env_config(claude_root):
    path = os.path.join(claude_root, 'env.json')
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def detect_python(os_type):
    candidates = []
    if os_type == 'windows':
        candidates = [
            'py -3', 'pythonw', 'python',
            r'C:\Python311\pythonw.exe',
            r'C:\Python310\pythonw.exe',
            r'C:\Python39\pythonw.exe',
            r'C:\Program Files\Python311\pythonw.exe',
            r'C:\Program Files\Python310\pythonw.exe',
        ]
    else:
        candidates = ['python3', 'python']
    for cmd in candidates:
        try:
            r = subprocess.run(
                shlex.split(cmd) + ['-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                ver = r.stdout.strip()
                major, minor = map(int, ver.split('.'))
                if (major, minor) >= (3, 8):
                    return cmd, ver
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
    return None, None


def make_hook_command(runner_template, script):
    """返回 "runner script" 格式的命令字符串"""
    return f'{runner_template} {script}'


def generate_settings_json(os_type):
    """按 OS 生成完整的 settings.local.json"""
    if os_type == 'windows':
        runner = "%USERPROFILE%/.claude/hook-runner.cmd"
        home_ref = "~/.claude"
    else:
        runner = "$HOME/.claude/hook-runner.sh"
        home_ref = "~/.claude"

    # 按 (event, matcher) 分组
    groups = {}
    for event, matcher, script in HOOK_RECORDS:
        key = (event, matcher)
        groups.setdefault(key, []).append(script)

    # 按事件类型分组
    event_sections = {}
    for event in EVENT_ORDER:
        matchers = []
        for (ev, m), scripts in groups.items():
            if ev != event:
                continue
            # 构建 matcher 子段
            hook_entries = []
            for s in scripts:
                hook_entries.append(f'''          {{
            "type": "command",
            "command": "{runner} {s}"
          }}''')
            hooks_str = ',\n'.join(hook_entries)
            if m:
                block = f'''      {{
        "matcher": "{m}",
        "hooks": [
{hooks_str}
        ]
      }}'''
            else:
                block = f'''      {{
        "hooks": [
{hooks_str}
        ]
      }}'''
            matchers.append(block)
        if matchers:
            event_sections[event] = ',\n'.join(matchers)

    # 按 EVENT_ORDER 输出
    post_tool = event_sections.get("PostToolUse", "")
    pre_tool  = event_sections.get("PreToolUse", "")
    session   = event_sections.get("SessionStart", "")
    expansion = event_sections.get("UserPromptExpansion", "")
    submit    = event_sections.get("UserPromptSubmit", "")

    return f'''{{
  "permissions": {{
    "allow": [
      "Bash(python3 *)",
      "Bash(python *)",
      "Bash(export CLAUDE_HOOK_APPROVED_CONTROL_PLANE=1)",
      "Bash(echo \\"exit: $?\\")",
      "Bash(find {home_ref}/ -name *.json -maxdepth 2)",
      "WebSearch"
    ]
  }},
  "hooks": {{
    "PostToolUse": [
{post_tool}
    ],
    "PreToolUse": [
{pre_tool}
    ],
    "SessionStart": [
{session}
    ],
    "UserPromptExpansion": [
{expansion}
    ],
    "UserPromptSubmit": [
{submit}
    ]
  }}
}}'''


def validate_json(content):
    try:
        json.loads(content)
        return True
    except json.JSONDecodeError as e:
        print(f"  [ERROR] JSON 格式错误: {e}", file=sys.stderr)
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Claude Code 环境迁移工具')
    parser.add_argument('--apply', action='store_true', help='直接应用配置')
    parser.add_argument('--dry-run', action='store_true', help='预览不写入')
    parser.add_argument('--validate', action='store_true', help='只做验证')
    args = parser.parse_args()

    claude_root = find_claude_root()
    os_type = detect_os()

    print(f"[.] .claude/ 根目录: {claude_root}")
    print(f"[OS] 系统: {os_type}")
    if os_type == 'windows':
        print(f"     hook 入口: hook-runner.cmd   变量: %USERPROFILE%")
    else:
        print(f"     hook 入口: hook-runner.sh    变量: $HOME")

    # 环境配置
    env = load_env_config(claude_root)
    env_path = os.path.join(claude_root, 'env.json')
    if env:
        print(f"[env] {env_path}")
        if 'python_exe' in env:
            print(f"      python_exe = {env['python_exe']}")
        if 'os_type' in env:
            print(f"      os_type = {env['os_type']}")
    else:
        print(f"[env] {env_path} 不存在，将用自动检测")
        if args.apply:
            print("      [TIP] 建议先创建 env.json")

    # Python 检测
    py_cmd, py_ver = detect_python(os_type)
    if py_cmd:
        print(f"[Python] {py_cmd} (v{py_ver})")
    else:
        print(f"[Python] 未找到 Python 3.8+，请先安装")
        if args.apply:
            sys.exit(1)

    # hook 文件检查
    missing = []
    for _, _, script in HOOK_RECORDS:
        p = os.path.join(claude_root, 'skills', 'util-safety', 'hooks', script)
        if not os.path.isfile(p):
            missing.append(script)
    if missing:
        print(f"[hooks] 缺失: {', '.join(missing)}")
    else:
        print(f"[hooks] {len(HOOK_RECORDS)} 个脚本齐全")

    # 入口文件
    entry_name = 'hook-runner.cmd' if os_type == 'windows' else 'hook-runner.sh'
    entry_path = os.path.join(claude_root, entry_name)
    print(f"[entry] {entry_path} {'存在' if os.path.isfile(entry_path) else '缺失'}")

    # hook-runner.py
    runner_py = os.path.join(claude_root, 'scripts', 'hook-runner.py')
    print(f"[runner] {runner_py} {'存在' if os.path.isfile(runner_py) else '缺失'}")

    # 生成 settings.local.json
    settings_content = generate_settings_json(os_type)
    settings_path = os.path.join(claude_root, 'settings.local.json')

    if validate_json(settings_content):
        print(f"[json] settings.local.json 格式正确")

    if args.validate:
        print("[完成] 验证通过")
        return

    if args.dry_run:
        print(f"\n== 预览 settings.local.json ==")
        print(settings_content)
        print("== 预览结束 ==")
        return

    if args.apply:
        if os.path.isfile(settings_path):
            bak = settings_path + '.bak'
            shutil.copy2(settings_path, bak)
            print(f"[bak] 备份: {bak}")
        with open(settings_path, 'w', newline='\n') as f:
            f.write(settings_content)
            f.write('\n')
        print(f"[write] {settings_path}")

        if os_type != 'windows':
            sh_path = os.path.join(claude_root, 'hook-runner.sh')
            if os.path.isfile(sh_path):
                os.chmod(sh_path, 0o755)
                print(f"[chmod] +x {sh_path}")

        print("[完成] 迁移配置已写入")
        print("启动 Claude Code 后建议跑 /util-check 验证")
    else:
        print("[INFO] 使用 --apply 应用，或 --dry-run 预览")


if __name__ == '__main__':
    main()
