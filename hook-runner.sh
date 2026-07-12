#!/bin/sh
# Claude Code Hook Runner — Unix 入口（自定位）
# 用法: hook-runner.sh <hook_script_name> [args...]
exec python3 "$(dirname "$0")/scripts/hook-runner.py" "$@"
