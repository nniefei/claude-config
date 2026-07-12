#!/usr/bin/env python3
"""Claude Code PreToolUse 审计 hook：记录 MCP 工具调用。"""
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

AUDIT_LOG_PATH = Path.home() / ".claude" / "logs" / "mcp-audit.jsonl"
AUDIT_ROTATE_MAX_BYTES = int(os.environ.get("CLAUDE_AUDIT_ROTATE_MAX_BYTES", str(5 * 1024 * 1024)))
AUDIT_ROTATE_MAX_LINES = int(os.environ.get("CLAUDE_AUDIT_ROTATE_MAX_LINES", "5000"))


def _load_audit_helper():
    """加载共享审计模块的 append_audit_log；失败则返回 no-op（审计是旁路，
    其加载失败不应阻断主流程）。"""
    import importlib.util
    shared_path = Path(__file__).resolve().parent / "_audit_log.py"
    try:
        spec = importlib.util.spec_from_file_location("_audit_log", shared_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.append_audit_log
    except Exception:
        return lambda *a, **k: None


_append_audit_log = _load_audit_helper()


def _load_redact_patterns():
    """加载共享的 SECRET_PATTERNS 用于审计日志打码；
    失败时返回空列表（审计旁路 fail-safe：打码失败不应丢日志）。"""
    try:
        import importlib.util
        shared_path = Path(__file__).resolve().parent / "_shared_patterns.py"
        spec = importlib.util.spec_from_file_location("_shared_patterns", shared_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, "SECRET_PATTERNS", ())
    except Exception:
        return ()


_SECRET_PATTERNS = _load_redact_patterns()


def _redact_secrets(text: str) -> str:
    """将文本中匹配 SECRET_PATTERNS 的片段替换为 ***REDACTED:<label>***。
    失败时返回原文（审计旁路 fail-safe）。"""
    if not _SECRET_PATTERNS:
        return text
    for label, pattern in _SECRET_PATTERNS:
        try:
            text = pattern.sub(f"***REDACTED:{label}***", text)
        except Exception:
            pass
    return text


def input_preview(tool_input: object) -> str:
    preview = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    preview = _redact_secrets(preview)
    if len(preview) <= 240:
        return preview
    return preview[:237] + "..."


def append_audit_log(line: str) -> None:
    _append_audit_log(AUDIT_LOG_PATH, line, AUDIT_ROTATE_MAX_BYTES, AUDIT_ROTATE_MAX_LINES)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
        if not tool_name.startswith("mcp__"):
            return 0

        tool_input = payload.get("tool_input") or payload.get("input") or {}
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "input_preview": input_preview(tool_input),
            "pid": os.getpid(),
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        append_audit_log(line)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
