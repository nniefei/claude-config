#!/usr/bin/env python3
"""Claude Code PreToolUse 守卫：高风险 Write/Edit 路径拦截。

从 stdin 读取 hook JSON。拦截对敏感文件和系统位置的写入，
放行正常的项目/配置编辑。

v2.2.1: control-plane PREFIXES（skills/、hooks/、scripts/、tests/）下的 Edit
现已被拦截，除非设置 CLAUDE_HOOK_APPROVED_CONTROL_PLANE=1。此前仅拦截 Write，
AI 可静默 Edit 安全 hook 脚本自身以解除武装。

Plan-3 3.7: 增加 5s 内部超时（fail-closed），与 bash-safety-wrapper 风格统一。
极端大 content 触发 regex 灾难时不再无限阻塞。

v2.7.2: secret 扫描加 256KB 长度上限，避免病理超长内容触发超时。
v2.8.0: P0 安全加固 — _check_path 中调用 os.path.realpath() 解析符号链接后再做路径
匹配，防止 symlink 指向敏感文件绕过路径检查。
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

# Windows GBK 终端强制 UTF-8
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TIMEOUT_SECONDS = 5

# 性能监控日志路径
PERF_LOG = Path.home() / ".claude" / "logs" / "hook-performance.jsonl"


def _log_performance(file_path: str, elapsed_ms: float, decision: str, reason: str = ""):
    """记录性能数据到 logs/hook-performance.jsonl（fail-safe）"""
    try:
        path_hash = hashlib.sha256(file_path.encode('utf-8', errors='replace')).hexdigest()[:16]
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "hook": "write-safety",
            "file_hash": path_hash,
            "elapsed_ms": round(elapsed_ms, 2),
            "decision": decision,
            "reason": reason,
        }
        PERF_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PERF_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Fail-safe：性能日志失败不影响主流程

# 对超长内容只扫描前 N 字节，避免 ReDoS 吃满 5s 超时导致误拦。
# 真实密钥泄漏通常在文件头部配置区，256KB 已充分覆盖。
SECRET_SCAN_MAX_BYTES = 256 * 1024

GRANTS_DIR = Path(os.environ.get("CLAUDE_TEST_GRANTS_DIR",
                str(Path.home() / ".claude" / ".grants")))

SENSITIVE_PATH_PATTERN = re.compile(
    r"(^|/|\\)(?:"
    r"\.env(?:\.|$)"
    r"|credentials(?:\.[^.]+)?\.json$"
    r"|serviceAccount\.json$"
    r"|.*\.(?:pem|key|p12)$"
    r"|id_rsa$"
    r"|id_ed25519$"
    r")",
    re.IGNORECASE,
)

# Plan-3 3.3: 基础设施/容器配置——非完全禁止，但要求 CLAUDE_HOOK_APPROVED_INFRA=1 marker。
# CI/CD 与容器配置变更影响构建、部署、安全策略，AI 不应未经授权修改。
# 负向断言：Dockerfile/Jenkinsfile 仅匹配真正的根级配置（无扩展，或带 stage 标识如 .dev/.prod），
# 排除 .md/.txt/.rst/.ts/.js/.py/.go/.rs 等文档/源码扩展，避免误伤 docs/Dockerfile.md 等。
_INFRA_DOC_OR_CODE_EXT = r"(?:md|txt|rst|adoc|html?|ts|tsx|js|jsx|mjs|cjs|py|go|rs|java|kt|rb|php|cs|swift|scala|sh|bash|zsh)"
INFRA_CONFIG_PATTERN = re.compile(
    r"(?:^|/)("
    r"\.github/workflows/[^/]+\.ya?ml"
    r"|\.gitlab-ci\.ya?ml"
    r"|Jenkinsfile(?!\." + _INFRA_DOC_OR_CODE_EXT + r"\b)(?:\.[\w-]+)?"
    r"|azure-pipelines\.ya?ml"
    r"|bitbucket-pipelines\.ya?ml"
    r"|Dockerfile(?!\." + _INFRA_DOC_OR_CODE_EXT + r"\b)(?:\.[\w-]+)?"
    r"|docker-compose(?:\.[\w-]+)?\.ya?ml"
    r"|(?:kubernetes|k8s)/[^/]+\.ya?ml"
    r")$",
    re.IGNORECASE,
)

SYSTEM_AUTOMEMORY_FILENAME_PATTERN = re.compile(
    r"/memory/(?:user|feedback|project|reference)_[\w-]+\.md$",
    re.IGNORECASE,
)

CONTROL_PLANE_ROOT = "/.claude/"

CONTROL_PLANE_PREFIXES = (
    "hooks/",
    "scripts/",
    "tests/",
    "skills/",
)

CONTROL_PLANE_CRITICAL_FILES = (
    "settings.json",
    "settings.local.json",
    "claude.md",
    "changelog.md",
    "readme.md",   # v2.18.1：规范门面，CLAUDE.md 顶部明确「新用户上手 / 系统总览详见 README」，与 claude.md 同级、互引——此前裸文件名漏列、write-safety 不拦，AI 可静默改写绕过 control-plane 守卫
    "migration.md",  # 迁移门面，改坏会误导跨机器/系统部署（6 步流程）
    "projects/c--users-nnie/memory/memory.md",  # memory 索引，被篡改会误导原生召回命中标的；正文文件(debugging/decisions/conventions)保持不守卫（高频写 memory 免每次弹窗）。注意 normalize_path 会 .lower() 整条路径，故本项也须全小写（C--Users → c--users-nnie）
)

CONTROL_PLANE_CONTENT_SCAN_FILES = (
    "settings.json",
    "settings.local.json",
    "claude.md",
)

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

SECRET_SCAN_WHITELIST_PATHS = (
    "/.claude/skills/util-safety/tests/",
    "/.claude/skills/util-check/tests/",
    "/tests/fixtures/",
    "/test_fixtures/",
    "/__fixtures__/",
    "/node_modules/",
    "/.git/",
)

SECRET_SCAN_WHITELIST_FILENAME_PATTERNS = (
    re.compile(r".*\.example(\.[^.]+)?$"),
    re.compile(r".*\.sample(\.[^.]+)?$"),
    re.compile(r".*\.template(\.[^.]+)?$"),
    re.compile(r"^readme(\.[^.]+)?$"),
    re.compile(r"^changelog(\.[^.]+)?$"),
)

WINDOWS_SYSTEM_PREFIXES = (
    "c:/windows/",
    "c:/program files/",
    "c:/program files (x86)/",
    "c:/programdata/",
)

POSIX_SYSTEM_PREFIXES = (
    "/bin/",
    "/boot/",
    "/dev/",
    "/etc/",
    "/lib/",
    "/lib64/",
    "/proc/",
    "/root/",
    "/sbin/",
    "/sys/",
    "/usr/bin/",
    "/usr/lib/",
    "/usr/sbin/",
    "/var/log/",
)


def is_write_or_edit(payload: dict) -> bool:
    name = str(payload.get("tool_name") or payload.get("tool") or "")
    return name.lower() in {"write", "edit"}


def extract_file_path(payload: dict) -> str:
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(tool_input, dict):
        return str(tool_input.get("file_path") or "")
    return ""


def extract_write_content(payload: dict) -> str:
    """提取即将写入磁盘的内容：Write.content 或 Edit.new_string。"""
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if not isinstance(tool_input, dict):
        return ""
    parts: list[str] = []
    for key in ("content", "new_string"):
        value = tool_input.get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def scan_content_secrets(content: str) -> list[str]:
    """返回匹配到的密钥模式标签列表。绝不返回匹配到的实际值。"""
    if not content:
        return []
    # 截断超长内容，避免 ReDoS 触发 5s 超时
    if len(content) > SECRET_SCAN_MAX_BYTES:
        content = content[:SECRET_SCAN_MAX_BYTES]
    hits: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(content) and _passes_entropy_recheck(label, content):
            hits.append(label)
    return hits


def normalize_path(file_path: str) -> str:
    return file_path.replace("\\", "/").lower()


def is_grants_path(normalized: str) -> bool:
    """目标是否落在 .grants/ 授权目录内。

    grant 文件代表「人的带外授权」。AI 用 Write/Edit 写 grant = 自我授权
    （信任根错位）。命中时一律 exit 2 硬 deny，而不是降级为确认弹窗。
    不提供任何 grant/env 豁免：用 grant 文件豁免「写 grant」会构成无限套娃。
    使用 deny 是为了避免跳过权限模式吞掉确认弹窗，确保信任根不可被降级。
    """
    return "/.claude/.grants/" in normalized


def is_sensitive_path(normalized: str) -> bool:
    return bool(SENSITIVE_PATH_PATTERN.search(normalized))


def is_infra_config_path(normalized: str) -> bool:
    """CI/CD pipeline 或容器配置文件，需 CLAUDE_HOOK_APPROVED_INFRA=1 才放行。"""
    return bool(INFRA_CONFIG_PATTERN.search(normalized))


def control_plane_relative_path(normalized: str) -> str:
    marker = CONTROL_PLANE_ROOT
    index = normalized.find(marker)
    if index == -1:
        return ""
    return normalized[index + len(marker):]


def is_control_plane_path(normalized: str, tool_name: str) -> bool:
    relative = control_plane_relative_path(normalized)
    if not relative:
        return False

    if relative in CONTROL_PLANE_CRITICAL_FILES:
        return True

    # v2.2.1：Edit 同样需在 PREFIXES 下被拦截，不仅仅是 Write。
    # 此前 AI 可静默 Edit 安全 hook 脚本以解除武装。
    if tool_name.lower() in ("write", "edit"):
        return relative.startswith(CONTROL_PLANE_PREFIXES)

    return False


def is_system_path(normalized: str) -> bool:
    return normalized.startswith(WINDOWS_SYSTEM_PREFIXES) or normalized.startswith(POSIX_SYSTEM_PREFIXES)


def load_payload() -> dict:
    """解析 stdin payload。json.loads 失败时用正则降级提取基本字段。

    降级路径是防御性设计：当 stdin 内容被截断或编码异常导致 JSON
    解析失败时，仍能做路径级安全检查。正则不处理完整 JSON 转义语义——
    极端情况可能漏检 content 中的密钥，但 file_path 提取通常足够触发
    路径拦截。完整 JSON 解析成功时走主路径，本分支仅在异常输入时触发。
    """
    raw = sys.stdin.read()
    try:
        return json.loads(raw)
    except Exception as exc:
        fallback: dict[str, object] = {}

        tool_name_match = re.search(r'"tool_name"\s*:\s*"([^"]+)"', raw)
        if tool_name_match:
            fallback["tool_name"] = tool_name_match.group(1)

        file_path_match = re.search(r'"file_path"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
        if file_path_match:
            fallback.setdefault("tool_input", {})["file_path"] = \
                file_path_match.group(1).replace("\\\\", "\\")

        content_match = re.search(r'"content"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
        if content_match:
            fallback.setdefault("tool_input", {})["content"] = content_match.group(1)

        new_string_match = re.search(r'"new_string"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
        if new_string_match:
            fallback.setdefault("tool_input", {})["new_string"] = new_string_match.group(1)

        if fallback:
            return fallback

        print(f"Write 安全 hook 解析输入失败：{exc}", file=sys.stderr)
        return {"tool_name": "Write", "tool_input": {}}


def is_control_plane_content_scan_target(normalized: str) -> bool:
    relative = control_plane_relative_path(normalized)
    return bool(relative) and relative in CONTROL_PLANE_CONTENT_SCAN_FILES


def is_secret_scan_whitelisted(normalized: str) -> bool:
    """v2.2.0：密钥扫描现默认覆盖所有 Write/Edit 内容。白名单豁免测试夹具、
    README/CHANGELOG/example/template/sample 文件及第三方目录。"""
    for prefix in SECRET_SCAN_WHITELIST_PATHS:
        if prefix in normalized:
            return True
    filename = normalized.rsplit("/", 1)[-1]
    for pat in SECRET_SCAN_WHITELIST_FILENAME_PATTERNS:
        if pat.match(filename):
            return True
    return False


def _check_path(payload: dict) -> int:
    """核心检查逻辑。返回 0（放行）或 2（阻断）。"""
    if not is_write_or_edit(payload):
        return 0

    file_path = extract_file_path(payload)
    if not file_path:
        print("Write 安全 hook 已阻断此工具调用：缺少 file_path。", file=sys.stderr)
        return 2

    normalized = normalize_path(file_path)

    # 最高优先级：写 .grants/ 一律硬阻断（deny / exit 2），先于一切其他检查与豁免。
    # 为什么用 deny 而非 ask：Claude Code 评估顺序 deny > ask > allow，且 bypass/
    # 跳过权限模式会吞掉 ask；唯有 exit 2 无视 allow 列表与权限模式，可靠拦截。
    # 防止 AI 用 Write/Edit 自建 grant 给自己放行（自建=自我授权=信任根错位）。
    # 主人授权的正道：在对话输入 `! touch ~/.claude/.grants/<category>`（! 命令走
    # 主人真实终端，不经过本 hook）。
    if is_grants_path(normalized):
        print("=" * 60, file=sys.stderr)
        print("[安全守卫] 拒绝：AI 不可写授权目录 .grants/", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"目标路径：{file_path}", file=sys.stderr)
        print("grant 文件代表主人的授权，AI 不能自己创建（否则等于自我授权）。", file=sys.stderr)
        print("如需授权，请主人在对话中输入：", file=sys.stderr)
        print("  ! touch ~/.claude/.grants/<category>", file=sys.stderr)
        print("输入 ! 命令后会自动重试刚才被拦截的操作。", file=sys.stderr)
        print("（! 命令在主人的真实终端执行，不经过本 hook）", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        return 2

    # P0 安全加固：解析符号链接后再做路径匹配，防止 symlink 绕过。
    # 仅在父目录存在时才调用 realpath（基于已存在的目录组件解析符号链接），
    # 避免 Windows 上将不存在的绝对路径（如 /etc/hosts）错误合并到 CWD。
    # 放在 grants 检查之后，确保 grants 路径优先被硬阻断。
    try:
        parent = os.path.dirname(file_path)
        if parent and os.path.exists(parent):
            resolved_parent = os.path.realpath(parent)
            basename = os.path.basename(file_path)
            resolved = os.path.join(resolved_parent, basename)
            if os.path.normpath(resolved) != os.path.normpath(file_path):
                normalized = normalize_path(resolved)
    except Exception:
        pass  # fail-safe：解析失败继续用原始 normalized 路径

    blocked_reasons: list[str] = []
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")

    if is_sensitive_path(normalized):
        blocked_reasons.append("敏感文件路径")
    if is_system_path(normalized):
        blocked_reasons.append("系统路径")
    if is_control_plane_path(normalized, tool_name):
        blocked_reasons.append("控制平面路径")
    if is_infra_config_path(normalized):
        blocked_reasons.append("基础设施配置（CI/CD 或容器）")
    if SYSTEM_AUTOMEMORY_FILENAME_PATTERN.search(normalized):
        blocked_reasons.append("系统 auto-memory 文件名")

    # 大文件警告（不阻断，仅提示）
    content = extract_write_content(payload)
    line_count = content.count('\n') + 1 if content else 0
    if line_count > 5000:
        print(f"⚠️  [Write 安全提示] 文件较大：{line_count} 行", file=sys.stderr)
        print(f"   目标：{file_path}", file=sys.stderr)
        print(f"   建议：分块写入（每块 ≤50 行）以避免截断风险", file=sys.stderr)

    secret_hits: list[str] = []
    if not is_secret_scan_whitelisted(normalized):
        secret_hits = scan_content_secrets(content)
        if secret_hits:
            blocked_reasons.append("嵌入的密钥")

    if not blocked_reasons:
        return 0

    required_grant_keys: set[str] = set()
    for reason in blocked_reasons:
        key = _reason_to_grant_key(reason)
        if key:
            required_grant_keys.add(key)
    if required_grant_keys and acquire_write_grants(required_grant_keys):
        return 0

    missing_grant_keys = {key for key in required_grant_keys if not _grant_available(key)}

    reason_labels: list[str] = []
    seen_keys: set[str] = set()
    for reason in blocked_reasons:
        key = _reason_to_grant_key(reason)
        if key:
            if key in seen_keys:
                continue
            seen_keys.add(key)
        reason_labels.append(reason)

    reason_str = '、'.join(reason_labels)
    print(f"[安全守卫] Write/Edit 已拦截 — {reason_str}：{file_path}", file=sys.stderr)
    if secret_hits:
        print(f"  密钥模式：{'、'.join(secret_hits)}", file=sys.stderr)
    _grant_hints_one_line(missing_grant_keys)
    return 2


def _reason_to_grant_key(reason: str) -> str | None:
    if reason.startswith("控制平面") or reason.startswith("系统 auto-memory"):
        return "control-plane"
    if reason.startswith("敏感文件") or reason.startswith("系统路径"):
        return "sensitive"
    if reason.startswith("基础设施"):
        return "infra"
    if reason.startswith("嵌入的密钥"):
        return "secret"
    return None


_GRANT_ENV_MAP = {
    "control-plane": "CLAUDE_HOOK_APPROVED_CONTROL_PLANE",
    "sensitive": "CLAUDE_HOOK_APPROVED_SENSITIVE",
    "infra": "CLAUDE_HOOK_APPROVED_INFRA",
    "secret": "CLAUDE_HOOK_APPROVED_SECRET",
}


def _grant_available(key: str) -> bool:
    """只检查授权是否存在，不消费（不删除 grant 文件）。"""
    if (GRANTS_DIR / f"{key}.session").exists():
        return True
    if (GRANTS_DIR / key).exists():
        return True
    env_var = _GRANT_ENV_MAP.get(key)
    return bool(env_var and os.environ.get(env_var) == "1")


def _consume_grant(key: str) -> bool:
    """消费 .grants/<key> 一次性文件；成功或无需消费返回 True。"""
    grant_file = GRANTS_DIR / key
    if not grant_file.exists():
        return True
    try:
        grant_file.unlink()
    except OSError:
        return False
    return True


_GRANT_LOCK_PATH = GRANTS_DIR / ".consume.lock"


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


def acquire_write_grants(required: set[str]) -> bool:
    """原子完成「检查全部 required 已授权」+「消费全部消费型 grant」。

    返回 True = 全部满足且已消费（命令放行）；False = 有类别未授权（不消费任何 grant）。
    锁不可用时回退到非加锁的两步实现（fail-safe：本地单用户场景竞态概率极低）。
    """
    with_lock = _load_audit_lock()
    if with_lock is None:
        granted = {key for key in required if _grant_available(key)}
        if not required.issubset(granted):
            return False
        for key in required:
            if not _consume_grant(key):
                return False
        return True

    result = {"ok": False}

    def _critical():
        granted = {key for key in required if _grant_available(key)}
        if not required.issubset(granted):
            return
        for key in required:
            if not _consume_grant(key):
                return
        result["ok"] = True

    try:
        with_lock(_GRANT_LOCK_PATH, _critical)
    except Exception:
        # 锁本身异常 → 回退非加锁路径，绝不因锁故障误拒合法授权
        granted = {key for key in required if _grant_available(key)}
        if not required.issubset(granted):
            return False
        for key in required:
            if not _consume_grant(key):
                return False
        return True
    return result["ok"]


def _grant_hints_one_line(missing_keys: set[str]) -> None:
    grant_commands = []
    session_commands = []
    env_commands = []
    for k in sorted(missing_keys):
        env_var = _GRANT_ENV_MAP.get(k, '')
        if env_var:
            grant_commands.append(f"! touch ~/.claude/.grants/{k}")
            session_commands.append(f"! touch ~/.claude/.grants/{k}.session")
            env_commands.append(f"! export {env_var}=1")

    if grant_commands:
        print("  授权：", file=sys.stderr)
        for cmd in grant_commands:
            print(f"    {cmd}            一次性", file=sys.stderr)
        for cmd in session_commands:
            print(f"    {cmd}    会话级", file=sys.stderr)
        for cmd in env_commands:
            print(f"    {cmd}      环境", file=sys.stderr)
        print('  输入 ! 命令后自动重试', file=sys.stderr)


def main() -> int:
    """顶层入口：解析 stdin，带内部超时运行 _check_path，fail-closed。

    Plan-3 3.7: 与 bash-safety-wrapper 风格统一——5s 内部超时 + 顶层 try/except，
    任何未预期错误（含超时）都返回 2，避免极端 regex 灾难导致无限阻塞 AI。
    """
    start_time = time.perf_counter()
    exit_code = 2
    file_path = ""
    reason = ""

    try:
        payload = load_payload()
        file_path = extract_file_path(payload)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_check_path, payload)
            try:
                exit_code = future.result(timeout=TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                print(f"Write 安全 hook 在 {TIMEOUT_SECONDS} 秒后超时。", file=sys.stderr)
                print("为安全起见阻断 Write/Edit（fail-closed）。", file=sys.stderr)
                exit_code = 2
                reason = "timeout"

    except Exception as exc:
        print(f"Write 安全 hook 崩溃：{exc}", file=sys.stderr)
        print("为安全起见阻断 Write/Edit（fail-closed）。", file=sys.stderr)
        exit_code = 2
        reason = "crash"

    finally:
        # 记录性能数据（fail-safe）
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        decision = "allow" if exit_code == 0 else "deny"
        _log_performance(file_path, elapsed_ms, decision, reason)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
