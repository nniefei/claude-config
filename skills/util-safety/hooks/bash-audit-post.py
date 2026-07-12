#!/usr/bin/env python3
"""Claude Code PostToolUse 审计：记录实际执行后的危险 Bash 命令。

PreToolUse 的 ask 决策无法知道主人最终点 Allow 还是 Deny；PostToolUse 只在工具
实际执行后触发，因此用于补齐 ask-Allow 主路径审计，并记录本 session 已 Allow 的
危险操作标签，供 bash-safety-wrapper.py 在同 session 后续同标签操作中免重复弹窗。

失败策略：fail-safe。任何异常均静默返回 0，不阻断用户工作流。
"""
import importlib.util
import json
import sys
from pathlib import Path

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _load_wrapper():
    wrapper_path = Path(__file__).resolve().parent / "bash-safety-wrapper.py"
    spec = importlib.util.spec_from_file_location("bash_safety_wrapper", wrapper_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_command(payload: dict) -> str:
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(tool_input, dict):
        return str(tool_input.get("command") or "")
    return ""


def _is_bash(payload: dict) -> bool:
    name = str(payload.get("tool_name") or payload.get("tool") or "")
    return name.lower() == "bash"


def _is_grant_source(blocked: list, command: str) -> bool:
    """检查最近的审计记录是否是 grant 放行（source=grant）。"""
    try:
        wrapper = _load_wrapper()
        audit_path = wrapper.AUDIT_LOG_PATH
        if not audit_path.exists():
            return False
        # 读取最后几行审计记录
        with audit_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines:
            return False
        # 检查最后一条记录
        last_entry = json.loads(lines[-1].strip())
        # 如果 source=grant 且操作标签匹配，则是 grant 放行
        if last_entry.get("source") == "grant":
            entry_ops = set(last_entry.get("operations", []))
            blocked_ops = {label for _, label in blocked}
            if entry_ops & blocked_ops:  # 有交集
                return True
        return False
    except Exception:
        return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    try:
        if not _is_bash(payload):
            return 0
        command = _extract_command(payload)
        if not command:
            return 0

        wrapper = _load_wrapper()
        if wrapper.writes_to_grants(command):
            return 0

        blocked, _, _ = wrapper.analyze_dangerous_command(command)
        if not blocked:
            return 0

        wrapper.write_audit_log(command, blocked, source="post-exec")
        # 只有 ask 弹窗确认的情况才写 memo；grant 带外授权不升级为会话级
        session_id = wrapper.session_id_from_payload(payload)
        if not _is_grant_source(blocked, command):
            wrapper.remember_ask_approved(session_id, blocked)
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
