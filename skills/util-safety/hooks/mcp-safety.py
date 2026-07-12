#!/usr/bin/env python3
"""Claude Code PreToolUse 守卫：MCP 工具调用拦截。

v2.7.2: secret 扫描加 256KB 长度上限，避免病理超长内容触发超时。
v2.8.0: grant 消费 TOCTOU 修复 — 导入 _audit_log.py 的 with_audit_log_lock，
        用 acquire_mcp_grant() 原子化「检查 + 消费」，锁不可用时 fail-safe 回退。
        支持 risk_level 字段：黑名单工具可配置 'deny' 或 'ask'，密钥泄漏强制 deny。
        三层策略：allow 放行、block 按配置、未知动词 ask（unknown_verb_risk_level）。
"""
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TIMEOUT_SECONDS = 5
SECRET_SCAN_MAX_BYTES = 256 * 1024
GRANTS_DIR = Path(os.environ.get("CLAUDE_TEST_GRANTS_DIR",
                  str(Path.home() / ".claude" / ".grants")))
BLOCKLIST_PATH = Path(__file__).resolve().parent.parent / "config" / "mcp_blocklist.json"

# 性能监控日志路径
PERF_LOG = Path.home() / ".claude" / "logs" / "hook-performance.jsonl"


def _log_performance(tool_name: str, elapsed_ms: float, decision: str, reason: str = ""):
    """记录性能数据到 logs/hook-performance.jsonl（fail-safe）"""
    try:
        tool_hash = hashlib.sha256(tool_name.encode('utf-8', errors='replace')).hexdigest()[:16]
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "hook": "mcp-safety",
            "tool_hash": tool_hash,
            "elapsed_ms": round(elapsed_ms, 2),
            "decision": decision,
            "reason": reason,
        }
        PERF_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PERF_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Fail-safe：性能日志失败不影响主流程

def _load_shared_patterns():
    """从 _shared_patterns.py 加载共享的 SECRET_PATTERNS，失败时回退到内联定义。"""
    try:
        from _load_patterns_utils import load_secret_patterns
        return load_secret_patterns()
    except Exception:
        # 内联副本：容错设计，import 失败时仍能降级扫描密钥
        return (
            ("sk-* 风格密钥", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
            ("GitHub 个人访问令牌", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
            ("AWS 访问密钥 ID", re.compile(r"AKIA[0-9A-Z]{16}")),
            ("Bearer 令牌", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
            ("凭据赋值", re.compile(
                r"(?i)(?:api[_-]?key|secret|password|auth[_-]?token|access[_-]?token)"
                r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_/+=.\-]{16,}"
            )),
        )

def _load_entropy_recheck():
    """加载 passes_entropy_recheck；不可用时回退为恒 True（维持命中，不做复核）。"""
    try:
        from _load_patterns_utils import load_entropy_recheck
        return load_entropy_recheck()
    except Exception:
        return lambda label, content: True


SECRET_PATTERNS = _load_shared_patterns()
_passes_entropy_recheck = _load_entropy_recheck()


def is_mcp(payload: dict) -> bool:
    return str(payload.get("tool_name") or payload.get("tool") or "").startswith("mcp__")


def load_blocklist() -> dict[str, str]:
    """加载黑名单，返回 {tool_name: risk_level} 字典。

    risk_level 为 'deny'（硬阻断）或 'ask'（弹窗确认）。
    always_block 可为字符串列表（向后兼容，默认 deny）或对象列表。
    """
    with BLOCKLIST_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    tools = data.get("always_block", [])
    if not isinstance(tools, list):
        raise ValueError("mcp_blocklist.json always_block must be a list")

    result = {}
    for item in tools:
        if isinstance(item, str):
            # 向后兼容：字符串默认 deny
            result[item] = "deny"
        elif isinstance(item, dict):
            tool = item.get("tool")
            risk_level = item.get("risk_level", "deny")
            if tool:
                result[tool] = risk_level
    return result


def load_default_risk_level() -> str:
    """加载 default_risk_level，用于 block_tool_patterns 命中的工具。"""
    with BLOCKLIST_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get("default_risk_level", "deny")


def load_tool_patterns() -> tuple[list, list]:
    """加载 (block_patterns, allow_patterns)，编译为正则。缺失返回空列表。"""
    with BLOCKLIST_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    block = [re.compile(p, re.IGNORECASE) for p in data.get("block_tool_patterns", [])]
    allow = [re.compile(p, re.IGNORECASE) for p in data.get("allow_tool_patterns", [])]
    return block, allow


def tool_segment(tool_name: str) -> str:
    """从 mcp__<server>__<tool> 提取 <tool> 段；非标准格式返回原名。"""
    parts = tool_name.split("__")
    return parts[-1] if len(parts) >= 3 else tool_name


def matches_block_pattern(tool_name: str, block: list, allow: list) -> bool:
    """工具名 <tool> 段命中高风险动词模式，且未命中只读豁免模式。

    allow 优先：get_sender 含 send 子串，但 get_ 前缀命中 allow，整体放行。
    """
    segment = tool_segment(tool_name)
    if any(p.search(segment) for p in allow):
        return False
    return any(p.search(segment) for p in block)


def scan_secrets(value: object) -> list[str]:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    # 截断超长内容，避免 ReDoS 触发 5s 超时
    if len(serialized) > SECRET_SCAN_MAX_BYTES:
        serialized = serialized[:SECRET_SCAN_MAX_BYTES]
    hits: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(serialized) and _passes_entropy_recheck(label, serialized):
            hits.append(label)
    return hits


def _load_audit_lock():
    """加载共享审计模块的 with_audit_log_lock（复用其跨平台文件锁）。

    失败返回 None，调用方回退到非加锁路径（fail-safe：锁不可用不应阻断授权）。
    """
    shared_path = Path(__file__).resolve().parent / "_audit_log.py"
    try:
        spec = importlib.util.spec_from_file_location("_audit_log", shared_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["_audit_log"] = module
        spec.loader.exec_module(module)
        return module.with_audit_log_lock
    except Exception:
        return None


_GRANT_LOCK_PATH = GRANTS_DIR / ".consume.lock"


def acquire_mcp_grant() -> bool:
    """加锁内原子完成「检查 mcp grant 已授权」+「消费 mcp grant 文件」。

    返回 True = 已授权且已消费（MCP 调用放行）；False = 未授权（不消费 grant）。
    锁不可用时回退到非加锁的两步实现（fail-safe：本地单用户场景竞态概率极低）。

    env 授权（CLAUDE_HOOK_APPROVED_MCP=1）不消费，持续有效。
    会话级 grant（mcp.session）不消费，整个会话期间有效。
    """
    # env 授权优先：不消费，持续有效
    if os.environ.get("CLAUDE_HOOK_APPROVED_MCP") == "1":
        return True

    # 会话级 grant：不消费
    session_grant = GRANTS_DIR / "mcp.session"
    if session_grant.exists():
        return True

    # 一次性 grant：需原子消费
    grant_file = GRANTS_DIR / "mcp"

    with_lock = _load_audit_lock()
    if with_lock is None:
        # 锁不可用，回退非加锁路径
        if grant_file.exists():
            try:
                grant_file.unlink()
                return True
            except OSError:
                return False
        return False

    # 加锁路径：原子化「检查 + 消费」
    result = {"ok": False}

    def _critical():
        if grant_file.exists():
            try:
                grant_file.unlink()
                result["ok"] = True
            except OSError:
                pass

    try:
        with_lock(_GRANT_LOCK_PATH, _critical)
    except Exception:
        # 锁本身异常 → 回退非加锁路径，绝不因锁故障误拒合法授权
        if grant_file.exists():
            try:
                grant_file.unlink()
                return True
            except OSError:
                return False
        return False

    return result["ok"]


def load_unknown_verb_risk_level() -> str:
    """加载 unknown_verb_risk_level，用于既不在 allow 也不在 block 中的工具。"""
    with BLOCKLIST_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get("unknown_verb_risk_level", "ask")


def check_payload(payload: dict) -> int:
    if not is_mcp(payload):
        return 0

    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    tool_input = payload.get("tool_input") or payload.get("input") or {}

    # 三层策略判断
    blocked_reasons: list[str] = []
    risk_level = None

    # 先检查密钥（最高优先级）
    secret_hits = scan_secrets(tool_input)
    if secret_hits:
        blocked_reasons.append("凭据模式：" + "、".join(secret_hits))
        risk_level = "deny"  # 密钥泄漏强制 deny

    # 层一：精确黑名单
    blocklist = load_blocklist()
    if tool_name in blocklist:
        blocked_reasons.append("MCP 工具在黑名单中：" + tool_name)
        if risk_level is None:  # 密钥未覆盖
            risk_level = blocklist[tool_name]

    # 层二：模式匹配
    if not blocked_reasons or risk_level is None:
        block_patterns, allow_patterns = load_tool_patterns()
        segment = tool_segment(tool_name)

        # allow 优先：命中只读模式直接放行
        if any(p.search(segment) for p in allow_patterns):
            if not secret_hits:  # 无密钥时放行
                return 0
            # 有密钥时继续走 deny 流程
        elif any(p.search(segment) for p in block_patterns):
            # block 模式命中
            blocked_reasons.append("MCP 工具命中高风险动作模式：" + segment)
            if risk_level is None:
                risk_level = load_default_risk_level()
        else:
            # 层三：未知动词
            blocked_reasons.append("MCP 工具为未知动词，需确认：" + segment)
            if risk_level is None:
                risk_level = load_unknown_verb_risk_level()

    if not blocked_reasons:
        return 0

    # Check grants with TOCTOU protection
    if acquire_mcp_grant():
        return 0

    reason_str = '；'.join(blocked_reasons)

    if risk_level == "ask":
        # 输出 ask 决策 JSON，让用户确认
        # 必须包裹在 hookSpecificOutput 中，否则 Claude Code 不识别
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": f"MCP 工具需要确认 — {tool_name}。{reason_str}",
            }
        }
        print(json.dumps(output, ensure_ascii=False), flush=True)
        return 0
    else:
        # 硬阻断 deny
        print(f"[安全守卫] MCP 已拦截 — 工具：{tool_name}，原因：{reason_str}", file=sys.stderr)
        print("  授权：CLAUDE_HOOK_APPROVED_MCP=1 或 .claude/.grants/mcp", file=sys.stderr)
        return 2



def main() -> int:
    start_time = time.perf_counter()
    exit_code = 2
    tool_name = ""
    reason = ""

    try:
        try:
            payload = json.load(sys.stdin)
            tool_name = payload.get("tool_name", "")
        except Exception as exc:
            print(f"MCP 安全 hook 解析输入失败：{exc}", file=sys.stderr)
            return 2

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(check_payload, payload)
            try:
                exit_code = future.result(timeout=TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                print(f"MCP 安全 hook 在 {TIMEOUT_SECONDS} 秒后超时。", file=sys.stderr)
                print("为安全起见阻断 MCP 工具调用（fail-closed）。", file=sys.stderr)
                exit_code = 2
                reason = "timeout"
    except Exception as exc:
        print(f"MCP 安全 hook 崩溃：{exc}", file=sys.stderr)
        print("为安全起见阻断 MCP 工具调用（fail-closed）。", file=sys.stderr)
        exit_code = 2
        reason = "crash"

    finally:
        # 记录性能数据（fail-safe）
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        decision = "allow" if exit_code == 0 else "deny"
        _log_performance(tool_name, elapsed_ms, decision, reason)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
