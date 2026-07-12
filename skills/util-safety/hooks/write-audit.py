#!/usr/bin/env python3
"""Claude Code PostToolUse 审计：记录 Write/Edit 工具的实际调用。

PreToolUse 的 write-safety 可能 deny（exit 2）或 allow（exit 0）；
PostToolUse 只在工具实际执行后触发，因此记录所有成功通过的 Write/Edit 操作。

失败策略：fail-safe。任何异常均静默返回 0，不阻断用户工作流。
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

AUDIT_LOG_PATH = Path.home() / ".claude" / "logs" / "write-audit.jsonl"
AUDIT_ROTATE_MAX_BYTES = int(os.environ.get("CLAUDE_AUDIT_ROTATE_MAX_BYTES", str(5 * 1024 * 1024)))
AUDIT_ROTATE_MAX_LINES = int(os.environ.get("CLAUDE_AUDIT_ROTATE_MAX_LINES", "5000"))


def _load_shared_audit():
    """加载共享审计模块的 append_audit_log。失败返回 None（fail-safe）。"""
    shared_path = Path(__file__).resolve().parent / "_audit_log.py"
    try:
        spec = importlib.util.spec_from_file_location("_audit_log", shared_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.append_audit_log
    except Exception:
        return None


def _extract_file_path(payload: dict) -> str:
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(tool_input, dict):
        return str(tool_input.get("file_path") or "")
    return ""


def _extract_content(payload: dict) -> str:
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(tool_input, dict):
        content = tool_input.get("content") or tool_input.get("new_string") or ""
        return str(content) if isinstance(content, str) else json.dumps(content)[:256]
    return ""


def _is_write_or_edit(payload: dict) -> bool:
    name = str(payload.get("tool_name") or payload.get("tool") or "")
    return name.lower() in {"write", "edit"}


def _file_path_hash(file_path: str) -> str:
    import hashlib
    return hashlib.sha256(file_path.encode("utf-8", errors="replace")).hexdigest()[:16]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    try:
        if not _is_write_or_edit(payload):
            return 0

        file_path = _extract_file_path(payload)
        if not file_path:
            return 0

        content = _extract_content(payload)
        line_count = content.count("\n") + 1 if content else 0
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "")

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "file_path_hash": _file_path_hash(file_path),
            "line_count": line_count,
        }

        append_fn = _load_shared_audit()
        if append_fn is None:
            return 0

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        append_fn(AUDIT_LOG_PATH, line, AUDIT_ROTATE_MAX_BYTES, AUDIT_ROTATE_MAX_LINES)
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
