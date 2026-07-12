#!/usr/bin/env python3
"""Claude Code PreToolUse 守卫：高风险 Bash 命令拦截（单进程，fail-closed）。

从 stdin 读取 hook JSON。拦截破坏性或影响共享状态的 Bash 命令，
除非存在带外的显式授权（.grants/<category> 一次性文件或真实进程 env）。

由原 bash-safety-wrapper.py + bash-safety.py 合并而来，消除了额外的
子进程跳转。通过内部超时（concurrent.futures）和顶层 try/except 保持
fail-closed 语义。

Plan-2（计划2）安全加固：
  - 信任根改造：移除命令文本内联 marker 识别（被约束的 AI 能自拼 marker
    实现自我授权）。授权统一走 .grants/<category> 一次性文件 + 真实进程 env，
    与 write-safety.py 完全一致。
  - 命令归一化：检测前 shlex.split 取 argv[0] 的 basename 再比对命令名，
    拦截路径前缀（/bin/rm）、引号（"rm"）等常见变体。不承诺防对抗性绕过。

退出码：
  0 = 放行（安全检查通过）
  2 = 阻断（安全检查失败、超时或任何未预期错误 — fail-closed）
"""
import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Windows GBK 终端强制 UTF-8（命令可能含 CJK/emoji）
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TIMEOUT_SECONDS = 15

# 性能监控日志路径
PERF_LOG = Path.home() / ".claude" / "logs" / "hook-performance.jsonl"


def _log_performance(command: str, elapsed_ms: float, patterns_matched: list, decision: str):
    """记录性能数据到 logs/hook-performance.jsonl（fail-safe）"""
    try:
        cmd_hash = hashlib.sha256(command.encode('utf-8', errors='replace')).hexdigest()[:16]
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "hook": "bash-safety-wrapper",
            "command_hash": cmd_hash,
            "elapsed_ms": round(elapsed_ms, 2),
            "patterns_checked": len(DANGEROUS_PATTERNS),
            "patterns_matched": patterns_matched,
            "decision": decision,
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


SECRET_PATTERNS = _load_shared_patterns()


# 授权信任根：与 write-safety.py 完全一致——.grants/<category> 一次性文件
# 或真实进程 env（CLAUDE_HOOK_APPROVED_<CATEGORY>=1）。
# 不再从命令文本解析 marker：被约束的 AI 能自己拼内联 marker 实现自我授权，
# 那是把信任根放进了不可信的输入里。带外授权（文件/env）由人或外层环境控制。
GRANTS_DIR = Path(os.environ.get("CLAUDE_TEST_GRANTS_DIR",
                  str(Path.home() / ".claude" / ".grants")))

GRANT_CATEGORIES = ("git", "delete", "netexec", "package", "sensitive", "subshell",
                     "git-rewrite", "api-modify", "perm-escalate", "db-write")

_GRANT_ENV_MAP = {
    category: f"CLAUDE_HOOK_APPROVED_{category.upper()}"
    for category in GRANT_CATEGORIES
}

AUDIT_LOG_PATH = Path.home() / ".claude" / "logs" / "bash-safety-audit.jsonl"
AUDIT_ROTATE_MAX_BYTES = int(os.environ.get("CLAUDE_AUDIT_ROTATE_MAX_BYTES", str(5 * 1024 * 1024)))
AUDIT_ROTATE_MAX_LINES = int(os.environ.get("CLAUDE_AUDIT_ROTATE_MAX_LINES", "5000"))
ASK_MEMO_PATH = Path.home() / ".claude" / "logs" / "ask-approved-cache.json"
ASK_MEMO_MAX_SESSIONS = int(os.environ.get("CLAUDE_ASK_MEMO_MAX_SESSIONS", "200"))

DANGEROUS_PATTERNS = [
    ("git", "git commit", re.compile(r"(^|[;&|()\s])git\s+commit(\s|$)")),
    ("git", "git push", re.compile(r"(^|[;&|()\s])git\s+push(\s|$)")),
    ("git", "git merge", re.compile(r"(^|[;&|()\s])git\s+merge(\s|$)")),
    ("git", "git rebase", re.compile(r"(^|[;&|()\s])git\s+rebase(\s|$)")),
    ("git", "git reset --hard", re.compile(r"(^|[;&|()\s])git\s+reset\s+[^;&|]*--hard(\s|$)")),
    ("git", "git clean -f", re.compile(r"(^|[;&|()\s])git\s+clean\s+[^;&|]*-[^;&|\s]*f[^;&|]*(\s|$)")),
    ("git", "git branch -d", re.compile(r"(^|[;&|()\s])git\s+branch\s+[^;&|]*(?:-d)(\s|$)")),
    ("delete", "git branch -D", re.compile(r"(^|[;&|()\s])git\s+branch\s+[^;&|]*(?:-D|--delete\s+--force|--delete\s+-f|-(?:[a-z]*f[a-z]*)?D)(\s|$)")),
    ("git", "git checkout --", re.compile(r"(^|[;&|()\s])git\s+checkout\s+(--|\.\s|$)")),
    ("git", "git restore", re.compile(r"(^|[;&|()\s])git\s+restore\s+[^;&|]*(?:--staged|--worktree|\.)(\s|$)")),
    ("git", "git stash drop/clear", re.compile(r"(^|[;&|()\s])git\s+stash\s+(?:drop|clear)(\s|$)")),
    ("git", "git tag -d", re.compile(r"(^|[;&|()\s])git\s+tag\s+[^;&|]*(?:-d|--delete)(\s|$)")),
    # === git-rewrite 类别：改写历史的金牌操作 ===
    ("git-rewrite", "git filter-branch", re.compile(r"(^|[;&|()\s])git\s+filter-branch(\s|$)")),
    ("git-rewrite", "git filter-repo", re.compile(r"(^|[;&|()\s])git\s+filter-repo(\s|$)")),
    ("git-rewrite", "git update-ref -d", re.compile(r"(^|[;&|()\s])git\s+update-ref\s+[^;&|]*(?:-d|--delete)(\s|$)")),
    ("git-rewrite", "git symbolic-ref --delete", re.compile(r"(^|[;&|()\s])git\s+symbolic-ref\s+[^;&|]*(?:-d|--delete)(\s|$)")),
    # === self-destruct 类别：自毁 reflog/object（硬 deny） ===
    ("self-destruct", "git reflog expire", re.compile(r"(^|[;&|()\s])git\s+reflog\s+expire(\s|$)")),
    ("self-destruct", "git gc --prune", re.compile(r"(^|[;&|()\s])git\s+gc\s+[^;&|]*(?:--prune|--aggressive)(\s|$)")),
    ("delete", "rm -rf", re.compile(
        r"(^|[;&|()\s])(?:"
        r"rm\s+[^;&|]*(?:-rf|-fr|-Rf|-fR)"  # 短选项组合
        r"|rm\s+[^;&|]*-[^;&|\s]*[rR][^;&|\s]*f"  # 分离 flag: -r -f
        r"|rm\s+[^;&|]*-[^;&|\s]*f[^;&|\s]*[rR]"  # 分离 flag: -f -r
        r"|rm\s+[^;&|]*(?:--recursive\s+--force|--force\s+--recursive)"  # 长选项
        r")(\s|$)",
        re.IGNORECASE
    )),
    ("netexec", "curl | shell", re.compile(r"curl\s+[^;&|]*\|\s*(?:(?:sudo|env)\s+)?(?:/(?:usr/)?bin/)?(?:bash|sh|zsh|fish)(?:\s|$)", re.IGNORECASE)),
    ("netexec", "wget | shell", re.compile(r"wget\s+[^;&|]*\|\s*(?:(?:sudo|env)\s+)?(?:/(?:usr/)?bin/)?(?:bash|sh|zsh|fish)(?:\s|$)", re.IGNORECASE)),
    ("netexec", "Invoke-Expression", re.compile(r"(^|[;&|()\s])(?:iex|Invoke-Expression)(?:\s|['\"]|$)", re.IGNORECASE)),
    ("package", "npm uninstall -g", re.compile(r"(^|[;&|()\s])npm\s+(?:uninstall|remove|rm|un)\s+[^;&|]*(?:-g|--global)")),
    ("package", "npm install -g", re.compile(r"(^|[;&|()\s])npm\s+(?:install|i|add)\s+[^;&|]*(?:-g|--global)")),
    ("package", "yarn global remove", re.compile(r"(^|[;&|()\s])yarn\s+global\s+(?:remove|uninstall)")),
    ("package", "yarn global add", re.compile(r"(^|[;&|()\s])yarn\s+global\s+add")),
    ("package", "pnpm remove -g", re.compile(r"(^|[;&|()\s])pnpm\s+(?:remove|uninstall|rm|un)\s+[^;&|]*(?:-g|--global)")),
    ("package", "pnpm add -g", re.compile(r"(^|[;&|()\s])pnpm\s+(?:add|install|i)\s+[^;&|]*(?:-g|--global)")),
    ("package", "pip uninstall", re.compile(r"(^|[;&|()\s])pip\s+uninstall")),
("package", "pip install --force", re.compile(r"(^|[;&|()\s])pip\s+install\s+[^;&|]*(?:--force-reinstall|--ignore-installed)")),
    ("netexec", "curl > script", re.compile(r"curl\s+[^;&|]*>\s*[^;&|]+\.(?:sh|bash|py|pl|rb)")),
    ("netexec", "wget -O script", re.compile(r"wget\s+[^;&|]*-O\s+[^;&|]+\.(?:sh|bash|py|pl|rb)")),
    ("netexec", "base64 -d | shell", re.compile(r"base64\s+(?:-d|--decode)[^;&|]*\|\s*(?:bash|sh|zsh|fish)")),
    ("netexec", "echo | shell", re.compile(r"echo\s+[^;&|]*\|\s*(?:bash|sh|zsh|fish)")),
    # === api-modify 类别：API 写操作（非管道执行，独立于 netexec） ===
    ("api-modify", "gh api mutation", re.compile(r"(^|[;&|()\s])gh\s+api\s+[^;&|]*-(?:X|method|field)\s+(?:DELETE|POST|PUT|PATCH)(\s|$)", re.IGNORECASE)),
    ("api-modify", "curl mutation", re.compile(r"(^|[;&|()\s])curl\s+(?!.*\|\s*(?:bash|sh|zsh|fish))[^;&|]*-X\s*(?:DELETE|POST|PUT|PATCH)(\s|$)", re.IGNORECASE)),
    # === netexec 扩展：供应链投毒 ===
    ("netexec", "pip install URL", re.compile(r"(^|[;&|()\s])(?:pip|pip3)\s+install\s+[^;&|]*https?://", re.IGNORECASE)),
    ("netexec", "npm install URL", re.compile(r"(^|[;&|()\s])npm\s+(?:install|i|add)\s+[^;&|]*https?://", re.IGNORECASE)),
    ("subshell", "bash -c", re.compile(
        r"(?:^|[;&|()\s])(?:bash|sh|zsh|fish|dash|ksh)\s+(?:[^;&|]*\s)?-c\b",
        re.IGNORECASE,
    )),
    ("subshell", "powershell -Command", re.compile(
        r"(?:^|[;&|()\s])(?:pwsh|powershell|powershell\.exe)\s+(?:[^;&|]*\s)?-(?:c|Command)\b",
        re.IGNORECASE,
    )),
    ("subshell", "powershell -EncodedCommand", re.compile(
        r"(?:^|[;&|()\s])(?:pwsh|powershell|powershell\.exe)\s+(?:[^;&|]*\s)?-(?:e|enc|EncodedCommand)\b",
        re.IGNORECASE,
    )),
    ("subshell", "bash heredoc", re.compile(
        r"(?:^|[;&|()\s])(?:bash|sh|zsh|fish)\s*<<-?",
        re.IGNORECASE,
    )),
    ("subshell", "xargs shell", re.compile(
        r"(?:^|[;&|()\s])xargs\s+(?:[^;&|]*\s)?(?:bash|sh|zsh|fish|pwsh|powershell)\b",
        re.IGNORECASE,
    )),
    # === perm-escalate 类别：权限放大 ===
    ("perm-escalate", "chmod -R 777", re.compile(r"(^|[;&|()\s])chmod\s+[^;&|]*-R\s+[^;&|]*777(\s|$)", re.IGNORECASE)),
    ("perm-escalate", "chown -R", re.compile(r"(^|[;&|()\s])chown\s+[^;&|]*-R(\s|$)", re.IGNORECASE)),
    # === db-write 类别：数据库写/删除操作（ask 弹窗 + grant 可授权） ===
    ("db-write", "mysql DROP/TRUNCATE", re.compile(r"(^|[;&|()\s])mysql\s+[^;&|]*(?:-e|--execute)[^;&|]*(?:DROP\s+(?:DATABASE|TABLE)|TRUNCATE\s+(?:TABLE\s+)?|DELETE\s+FROM)\b", re.IGNORECASE)),
    ("db-write", "psql DROP/TRUNCATE", re.compile(r"(^|[;&|()\s])psql\s+[^;&|]*(?:-c|--command)[^;&|]*(?:DROP\s+(?:DATABASE|TABLE)|TRUNCATE\s+(?:TABLE\s+)?|DELETE\s+FROM)\b", re.IGNORECASE)),
    ("db-write", "sqlcmd DROP/TRUNCATE", re.compile(r"(^|[;&|()\s])sqlcmd\s+[^;&|]*(?:-Q|-q)[^;&|]*(?:DROP\s+(?:DATABASE|TABLE)|TRUNCATE\s+(?:TABLE\s+)?|DELETE\s+FROM)\b", re.IGNORECASE)),
    # === disk-destroy 类别：磁盘级破坏（硬 deny，不可授权） ===
    ("disk-destroy", "dd overwrite", re.compile(r"(^|[;&|()\s])dd\s+if=", re.IGNORECASE)),
    ("disk-destroy", "mkfs format", re.compile(r"(^|[;&|()\s])(?:mkfs\.|mkfs\s)", re.IGNORECASE)),
    ("disk-destroy", "fdisk partition", re.compile(r"(^|[;&|()\s])fdisk\s", re.IGNORECASE)),
]

SENSITIVE_STAGE_PATTERN = re.compile(
    r"(^|[;&|()\s])git\s+add\s+([^;&|]+)",
    re.IGNORECASE,
)
SENSITIVE_PATH_PATTERN = re.compile(
    r"(^|/|\\)(?:"
    r"\.env(?:\.|$)"
    r"|credentials(?:\.[^.]+)?\.json$"
    r"|serviceAccount\.json$"
    r"|secret-token\.txt$"
    r"|.*\.(?:pem|key|p12)$"
    r"|id_rsa$"
    r"|id_ed25519$"
    r")",
    re.IGNORECASE,
)


def extract_command(payload: dict) -> str:
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(tool_input, dict):
        return str(tool_input.get("command") or "")
    return ""


def is_bash(payload: dict) -> bool:
    name = str(payload.get("tool_name") or payload.get("tool") or "")
    return name.lower() == "bash"


# 写 .grants/ 的常见 Bash 姿势：touch / 重定向 / tee / cp / mv / install。
# 务实定位：拦常见写法（防 AI 用 Bash 自建 grant 自我授权），不承诺防对抗性绕过
# （python -c open()、罕见重定向变体等堵不全，与整体威胁模型一致）。
_GRANTS_DIR_FRAGMENT = re.compile(r"[\\/]\.grants[\\/]", re.IGNORECASE)
_GRANTS_WRITE_VERBS = re.compile(
    r"(?:^|[;&|()\s])(?:touch|tee|cp|mv|install|ln)\b",
    re.IGNORECASE,
)
# 重定向写入：> 或 >>（可带 fd 前缀如 1>）后跟一个落在 .grants/ 的目标。
# 注意必须是「重定向目标是 .grants」，不能只看命令里有没有 > ——否则
# `ls ~/.claude/.grants 2>/dev/null`（读 + 把 stderr 丢到别处）会被误杀。
_GRANTS_REDIRECT_TARGET = re.compile(
    r"\d*>>?\s*[^\s;&|()]*[\\/]\.grants[\\/]",
    re.IGNORECASE,
)


def writes_to_grants(command: str) -> bool:
    """命令是否试图写入 .grants/ 目录（touch/重定向/tee/cp/mv 等）。

    只在「写动作确实指向 .grants」时返回 True：
    - 写动词（touch/tee/cp/mv/install/ln）出现 + 命令含 .grants 路径
    - 或重定向目标本身落在 .grants/（> .grants/x），而非命令里随便出现 >
    读取（cat/ls .grants）与把无关输出重定向到别处（2>/dev/null）不算写。
    """
    if not _GRANTS_DIR_FRAGMENT.search(command):
        return False
    if _GRANTS_WRITE_VERBS.search(command):
        return True
    if _GRANTS_REDIRECT_TARGET.search(command):
        return True
    return False


_SEGMENT_SPLIT = re.compile(r"(\|\||&&|[;&|()])")


def _check_changelog_staged() -> bool:
    """检查 CHANGELOG.md 是否已在暂存区（git commit 前合规检查）。

    提交前必须写 CHANGELOG 是硬性规范（CHANGELOG v2.6.0）。
    若 CHANGELOG.md 未在暂存区中，输出警告到 stderr 但仍放行。
    fail-safe：git 命令失败/非 git 仓库时不阻断。
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent.parent.parent.parent,
        )
        if result.returncode != 0:
            return False  # 非 git 仓库或 git 命令失败
        staged = result.stdout.splitlines()
        return any("CHANGELOG.md" in line for line in staged)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _normalize_token(token: str) -> str:
    """去引号 + 取路径 basename。

    /bin/rm -> rm；"rm" -> rm；'git' -> git；/usr/bin/git -> git。
    引号穿插（r''m）由 shlex 在 normalize_command 中先行合并。
    """
    stripped = token.strip().strip("\"'")
    if not stripped:
        return token
    base = re.split(r"[\\/]", stripped)[-1]
    return base


def normalize_command(command: str) -> str:
    """生成归一化命令文本供 DANGEROUS_PATTERNS 二次匹配。

    务实定位（任务 2.2）：防手滑 + 拦常见变体（路径前缀 / 引号 / 引号穿插），
    **不承诺防对抗性绕过**（边界见 README 威胁模型声明）。

    做法：按 shell 操作符分段，对每段用 shlex 还原 argv，把 argv[0] 替换为其
    basename，重新拼回。shlex 自然处理 "rm"、'git'、r''m 等引号变体。
    """
    out_parts: list[str] = []
    for segment in _SEGMENT_SPLIT.split(command):
        if not segment or _SEGMENT_SPLIT.fullmatch(segment):
            out_parts.append(segment)
            continue
        leading = segment[:len(segment) - len(segment.lstrip())]
        trailing = segment[len(segment.rstrip()):]
        body = segment.strip()
        if not body:
            out_parts.append(segment)
            continue
        try:
            argv = shlex.split(body)
        except ValueError:
            out_parts.append(segment)
            continue
        if not argv:
            out_parts.append(segment)
            continue
        # 跳过前导 env 赋值（FOO=bar cmd）与 env/sudo 包装，定位真正的命令名
        cmd_index = 0
        while cmd_index < len(argv) and (
            re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[cmd_index])
            or argv[cmd_index].lower() in {"sudo", "env"}
        ):
            cmd_index += 1
        if cmd_index < len(argv):
            argv[cmd_index] = _normalize_token(argv[cmd_index])
        out_parts.append(leading + " ".join(argv) + trailing)
    return "".join(out_parts)


_GIT_GLOBAL_OPTIONS_WITH_VALUE = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
    "--config-env", "--exec-path",
}


def _git_subcommand(argv: list[str], git_index: int) -> tuple[str, list[str]] | tuple[None, list[str]]:
    index = git_index + 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            index += 1
            break
        if not arg.startswith("-"):
            break
        if arg in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if any(arg.startswith(option + "=") for option in _GIT_GLOBAL_OPTIONS_WITH_VALUE):
            index += 1
            continue
        if arg.startswith("-C") and arg != "-C":
            index += 1
            continue
        if arg.startswith("-c") and arg != "-c":
            index += 1
            continue
        index += 1
    if index >= len(argv):
        return None, []
    return argv[index], argv[index + 1:]


def dangerous_git_operations(command: str) -> list[tuple[str, str]]:
    """用 shlex argv 解析检测 git 危险子命令。

    与 DANGEROUS_PATTERNS 的 git 正则是互补关系——
    DANGEROUS_PATTERNS 做快速粗筛（纯正则匹配原始+归一化命令文本），
    本函数用 shlex argv 解析精确处理 git 全局选项变体
    （如 git -C /path commit、git -c key=val push），
    这些变体会被正则的分隔符锚点跳过。两者结果在 check_command() 中去重合并。
    """
    hits = []
    for segment in _SEGMENT_SPLIT.split(command):
        if not segment or _SEGMENT_SPLIT.fullmatch(segment):
            continue
        try:
            argv = shlex.split(segment.strip())
        except ValueError:
            continue
        if not argv:
            continue
        cmd_index = 0
        while cmd_index < len(argv) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[cmd_index]):
            cmd_index += 1
        if cmd_index >= len(argv) or _normalize_token(argv[cmd_index]) != "git":
            continue
        subcommand, args = _git_subcommand(argv, cmd_index)
        if subcommand in {"commit", "push", "merge", "rebase"}:
            labels = {"commit": "git commit", "push": "git push", "merge": "git merge", "rebase": "git rebase"}
            hits.append(("git", labels[subcommand]))
            # changelog 合规检查：git commit 前必须更新 CHANGELOG.md
            if subcommand == "commit" and "--amend" not in args and not _check_changelog_staged():
                print("[安全守卫] ⚠ CHANGELOG 检查：CHANGELOG.md 未在暂存区中，"
                      "提交前必须先更新 CHANGELOG.md。", file=sys.stderr)
        elif subcommand == "reset" and any(arg == "--hard" or arg.startswith("--hard=") for arg in args):
            hits.append(("git", "git reset --hard"))
        elif subcommand == "clean" and any(arg == "--force" or (arg.startswith("-") and "f" in arg) for arg in args):
            hits.append(("git", "git clean -f"))
        elif subcommand == "branch" and any(arg in {"-d", "-D", "--delete"} or (arg.startswith("-") and ("d" in arg[1:] or "D" in arg[1:])) for arg in args):
            # -d: 安全删除（需已合并），仅需 git grant
            # -D: 强制删除，需 git + delete 双 grant
            # -d -f: 强制删除（分离 flag），需 git + delete 双 grant
            # -Df: 组合短选项，需 git + delete 双 grant
            # --delete: 安全删除（需已合并），仅需 git grant
            # --delete --force / --delete -f: 强制删除，需 git + delete 双 grant
            has_d = "-d" in args or "--delete" in args or any(arg.startswith("-") and "d" in arg[1:] and "D" not in arg[1:] for arg in args)
            has_D = "-D" in args or any(arg.startswith("-") and "D" in arg[1:] for arg in args)
            has_force = "--force" in args or "-f" in args or any(arg.startswith("-") and "f" in arg[1:] for arg in args)

            if has_d or has_D or "--delete" in args:
                hits.append(("git", "git branch -d"))
            if has_D or ("--delete" in args and has_force) or (has_d and has_force):
                hits.append(("delete", "git branch -D"))
        elif subcommand == "checkout" and ("--" in args or "." in args):
            hits.append(("git", "git checkout --"))
        elif subcommand == "restore" and any(arg in {"--staged", "--worktree", "."} for arg in args):
            hits.append(("git", "git restore"))
        elif subcommand == "stash" and args and args[0] in {"drop", "clear"}:
            hits.append(("git", "git stash drop/clear"))
        elif subcommand == "tag" and any(arg in {"-d", "--delete"} for arg in args):
            hits.append(("git", "git tag -d"))
        elif subcommand == "filter-branch":
            hits.append(("git-rewrite", "git filter-branch"))
        elif subcommand == "filter-repo":
            hits.append(("git-rewrite", "git filter-repo"))
        elif subcommand == "update-ref" and any(arg in {"-d", "--delete"} for arg in args):
            hits.append(("git-rewrite", "git update-ref -d"))
        elif subcommand == "symbolic-ref" and any(arg in {"-d", "--delete"} for arg in args):
            hits.append(("git-rewrite", "git symbolic-ref --delete"))
        elif subcommand == "reflog" and args and args[0] == "expire":
            hits.append(("self-destruct", "git reflog expire"))
        elif subcommand == "gc" and any(arg.startswith("--prune") or arg == "--aggressive" for arg in args):
            hits.append(("self-destruct", "git gc --prune"))
        elif subcommand == "config" and any(
            arg in {"--global", "--system"} for arg in args
        ) and any(
            any(key in arg for key in ("core.gitProxy", "core.sshCommand", "url.", "insteadOf"))
            for arg in args
        ):
            hits.append(("api-modify", "git config --global danger"))
    return hits


def _grant_available(category: str) -> bool:
    """只检查授权是否存在，不消费（不删除 grant 文件）。"""
    # 检查会话级 grant（不删除）
    if (GRANTS_DIR / f"{category}.session").exists():
        return True
    # 检查消费型 grant
    if (GRANTS_DIR / category).exists():
        return True
    # 检查环境变量
    env_var = _GRANT_ENV_MAP.get(category)
    return bool(env_var and os.environ.get(env_var) == "1")


def _consume_grant(category: str) -> None:
    """消费 .grants/<category> 一次性文件（env 授权不消费）。"""
    grant_file = GRANTS_DIR / category
    if grant_file.exists():
        try:
            grant_file.unlink()
        except OSError:
            pass


def approved_categories(required: set) -> set:
    """对每个被拦截的类别检查带外授权（不消费），返回已授权的类别集合。

    仅在全部 required 类别都已授权时，调用方才应消费 grant 文件——
    避免命令因缺少某个类别而被拒，却已白白用掉其他类别的一次性 grant。
    """
    return {category for category in required if _grant_available(category)}


def _load_audit_lock():
    """加载共享审计模块的 with_audit_log_lock（复用其跨平台文件锁）。
    失败返回 None，调用方回退到非加锁路径（fail-safe：锁不可用不应阻断授权）。"""
    import importlib.util
    shared_path = Path(__file__).resolve().parent / "_audit_log.py"
    try:
        spec = importlib.util.spec_from_file_location("_audit_log", shared_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.with_audit_log_lock
    except Exception:
        return None


# 消费型 grant 的并发保护锁。检查「全部 required 已授权」与消费「全部消费型
# grant」之间存在 TOCTOU 窗口：两个并发命令可能都通过 exists() 检查、都 unlink
# 同一个一次性 grant，等于一份授权放行两条命令。用一把跨平台文件锁把
# 「检查 + 消费」整段串行化，保持「全满足才消费」语义不变。
_GRANT_LOCK_PATH = GRANTS_DIR / ".consume.lock"


def acquire_grants(required: set) -> bool:
    """加锁内原子完成「检查全部 required 已授权」+「消费全部消费型 grant」。

    返回 True = 全部满足且已消费（命令放行）；False = 有类别未授权（不消费任何 grant）。
    锁不可用时回退到非加锁的两步实现（fail-safe：本地单用户场景竞态概率极低）。
    """
    with_lock = _load_audit_lock()
    if with_lock is None:
        granted = approved_categories(required)
        if not required.issubset(granted):
            return False
        for category in required:
            _consume_grant(category)
        return True

    result = {"ok": False}

    def _critical():
        granted = approved_categories(required)
        if not required.issubset(granted):
            return
        for category in required:
            _consume_grant(category)
        result["ok"] = True

    try:
        with_lock(_GRANT_LOCK_PATH, _critical)
    except Exception:
        # 锁本身异常 → 回退非加锁路径，绝不因锁故障误拒合法授权
        granted = approved_categories(required)
        if not required.issubset(granted):
            return False
        for category in required:
            _consume_grant(category)
        return True
    return result["ok"]


# git add 全仓扫描护栏：跳过的大目录 + 文件数上限
_SCAN_SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    ".env", "env", "dist", "build", ".tox", ".eggs",
    "site-packages", ".mypy_cache", ".pytest_cache",
})
_SCAN_MAX_FILES = 20000


def _scan_sensitive_paths(root: Path, _scan_stats: dict | None = None) -> list[str]:
    """扫描敏感文件路径。使用 os.walk 实现目录剪枝，避免 rglob 的无效遍历。"""
    import os
    hits = []
    if root.is_file():
        normalized = str(root)
        if SENSITIVE_PATH_PATTERN.search(normalized):
            hits.append(normalized)
        return hits
    if not root.is_dir():
        return hits
    if _scan_stats is None:
        _scan_stats = {"count": 0, "truncated": False}

    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        # 原地修剪：跳过 node_modules/.git 等大目录
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]

        for filename in filenames:
            _scan_stats["count"] += 1
            if _scan_stats["count"] > _SCAN_MAX_FILES:
                _scan_stats["truncated"] = True
                return hits
            normalized = os.path.join(dirpath, filename)
            if SENSITIVE_PATH_PATTERN.search(normalized):
                hits.append(normalized)
    return hits


def staged_sensitive_paths(command: str, cwd: str | None = None) -> tuple[list, bool]:
    hits = []
    scan_cwd = Path(cwd) if cwd else Path.cwd()
    scan_stats = {"count": 0, "truncated": False}
    for match in SENSITIVE_STAGE_PATTERN.finditer(command):
        args = match.group(2).strip()
        try:
            parts = shlex.split(args)
        except ValueError:
            parts = args.split()
        scan_roots = []
        for part in parts:
            if part == "--":
                continue
            if part in (".", "-A", "--all"):
                scan_roots.append(scan_cwd)
                continue
            if part.startswith("-"):
                continue
            normalized = part.strip('"\'')
            if SENSITIVE_PATH_PATTERN.search(normalized):
                hits.append(normalized)
            else:
                # 相对路径以 payload cwd 为基准
                path = Path(normalized)
                if not path.is_absolute():
                    path = scan_cwd / path
                scan_roots.append(path)
        for root in scan_roots:
            hits.extend(_scan_sensitive_paths(root, scan_stats))
    return sorted(set(hits)), scan_stats["truncated"]


def analyze_dangerous_command(command: str, cwd: str | None = None) -> tuple[list[tuple[str, str]], list[str], bool]:
    """返回危险命中列表与敏感文件列表；不做授权/审计副作用。
    返回 (blocked, sensitive_paths, scan_truncated) 三元组。
    """
    normalized_command = normalize_command(command)
    blocked = [
        (category, label)
        for category, label, pattern in DANGEROUS_PATTERNS
        if pattern.search(command) or pattern.search(normalized_command)
    ]
    blocked.extend(dangerous_git_operations(normalized_command))
    sensitive, scan_truncated = staged_sensitive_paths(command, cwd)
    if sensitive:
        blocked.append(("sensitive", "敏感文件 git add"))

    # 去重（保序）：同一操作可能同时被 DANGEROUS_PATTERNS 正则与
    # dangerous_git_operations 命中（如 git commit），避免审计日志记重复项。
    seen_blocked: set = set()
    deduped = []
    for item in blocked:
        if item not in seen_blocked:
            seen_blocked.add(item)
            deduped.append(item)
    return deduped, sensitive, scan_truncated


def command_summary(command: str) -> str:
    normalized = " ".join(command.split())
    # 审计日志打码：命中 SECRET_PATTERNS 的片段替换为 ***REDACTED:<label>***
    try:
        for label, pattern in SECRET_PATTERNS:
            normalized = pattern.sub(f"***REDACTED:{label}***", normalized)
    except Exception:
        pass  # 审计旁路 fail-safe：打码失败不丢摘要
    if len(normalized) <= 240:
        return normalized
    return normalized[:237] + "..."


def _load_audit_append():
    """加载共享审计模块的 append_audit_log。失败则抛异常——bash-safety 的
    审计写入失败必须 fail-closed（上层 check_command 捕获后 return 2 阻断命令），
    不可静默跳过（与 mcp-audit 的旁路语义不同）。"""
    import importlib.util
    shared_path = Path(__file__).resolve().parent / "_audit_log.py"
    spec = importlib.util.spec_from_file_location("_audit_log", shared_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.append_audit_log


def session_id_from_payload(payload: dict) -> str:
    """尽量从 hook payload 中提取 Claude session id，测试可用 env 覆盖。"""
    env_session = os.environ.get("CLAUDE_SESSION_ID")
    if env_session:
        return env_session
    for key in ("session_id", "sessionId"):
        value = payload.get(key)
        if value:
            return str(value)
    return "default"


def _memo_key(category: str, label: str) -> str:
    return f"{category}:{label}"


def _load_audit_lock_or_raise():
    import importlib.util
    shared_path = Path(__file__).resolve().parent / "_audit_log.py"
    spec = importlib.util.spec_from_file_location("_audit_log", shared_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.with_audit_log_lock


def _load_ask_memo_unlocked() -> dict:
    try:
        data = json.loads(ASK_MEMO_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}}
    if not isinstance(data, dict):
        return {"sessions": {}}
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        data["sessions"] = {}
    return data


def ask_memo_allows(session_id: str, blocked: list[tuple[str, str]]) -> bool:
    """同 session 下全部危险操作标签都已 Allow 过时返回 True。"""
    if not session_id or not blocked:
        return False
    try:
        data = _load_ask_memo_unlocked()
        allowed = data.get("sessions", {}).get(session_id, {}).get("operations", [])
        allowed_set = set(allowed if isinstance(allowed, list) else [])
        return {_memo_key(category, label) for category, label in blocked}.issubset(allowed_set)
    except Exception:
        return False


def remember_ask_approved(session_id: str, blocked: list[tuple[str, str]]) -> None:
    """记录本 session 已实际执行过的危险操作标签，原子写入 memo。"""
    if not session_id or not blocked:
        return
    with_lock = _load_audit_lock_or_raise()

    def _update() -> None:
        data = _load_ask_memo_unlocked()
        sessions = data.setdefault("sessions", {})
        current = sessions.setdefault(session_id, {"operations": []})
        ops = current.setdefault("operations", [])
        if not isinstance(ops, list):
            ops = []
        merged = list(dict.fromkeys(ops + [_memo_key(category, label) for category, label in blocked]))
        current["operations"] = merged
        current["updated_pid"] = os.getpid()

        # 简单 LRU：dict 保持插入序，更新过的 session 移到末尾。
        sessions[session_id] = sessions.pop(session_id)
        while len(sessions) > ASK_MEMO_MAX_SESSIONS:
            oldest = next(iter(sessions))
            sessions.pop(oldest, None)

        ASK_MEMO_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = ASK_MEMO_PATH.with_suffix(ASK_MEMO_PATH.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(ASK_MEMO_PATH)

    with_lock(ASK_MEMO_PATH, _update)


def write_audit_log(command: str, blocked: list, source: str = "grant-env") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "categories": sorted({category for category, _ in blocked}),
        "operations": [label for _, label in blocked],
        "command_summary": command_summary(command),
        "source": source,
        "pid": os.getpid(),
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    append_audit_log = _load_audit_append()
    append_audit_log(AUDIT_LOG_PATH, line, AUDIT_ROTATE_MAX_BYTES, AUDIT_ROTATE_MAX_LINES)

def check_command(payload: dict) -> tuple[int, list[str]]:
    """核心检查逻辑。返回退出码和匹配的 pattern 标签。"""
    if not is_bash(payload):
        return 0, []

    command = extract_command(payload)
    if not command:
        return 0, []

    # 最高优先级：写 .grants/ 一律硬阻断（deny / exit 2），先于一切其他检查。
    if writes_to_grants(command):
        print(f"[安全守卫] AI 不可写 .grants/ — {command_summary(command)}", file=sys.stderr)
        print("  grant=主人授权，AI 不可自建。请输入：! touch ~/.claude/.grants/<category>", file=sys.stderr)
        print("  （! 走主人终端，不经 hook）", file=sys.stderr)
        return 2, ["grants-write"]

    # 任务 2.2：对原始命令与归一化命令分别匹配，拦截路径前缀/引号变体。
    command_cwd = payload.get("cwd") or None
    blocked, sensitive, scan_truncated = analyze_dangerous_command(command, command_cwd)

    if scan_truncated:
        print("[安全守卫] git add 全仓扫描超限（仅扫描前 20000 个文件），建议显式指定待添加文件", file=sys.stderr)

    required_categories = {category for category, _ in blocked}
    matched_labels = [label for _, label in blocked]
    if not blocked:
        return 0, []

    granted = approved_categories(required_categories)
    if required_categories.issubset(granted):
        if acquire_grants(required_categories):
            try:
                # source=grant 标记这是带外授权，post 侧应跳过 memo
                write_audit_log(command, blocked, source="grant")
            except Exception as exc:
                print(f"Bash 安全审计日志写入失败：{exc}", file=sys.stderr)
                return 2, matched_labels
            return 0, matched_labels

    session_id = session_id_from_payload(payload)
    if ask_memo_allows(session_id, blocked):
        try:
            write_audit_log(command, blocked, source="session-ask-memo")
        except Exception as exc:
            print(f"Bash 安全审计日志写入失败：{exc}", file=sys.stderr)
            return 2, matched_labels
        return 0, matched_labels

    # 危险命令命中且无带外授权 → 返回 ask 决策
    blocked_labels = [label for _, label in blocked]
    reason = f"AI 请求执行敏感操作：{'、'.join(blocked_labels)}。命令：{command_summary(command)}。请在权限弹窗中选择 Allow 或 Deny；无需预先创建 .grants 文件。"
    if sensitive:
        reason += f"（敏感文件：{'、'.join(sensitive)}）"
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0, matched_labels


def main() -> int:
    """顶层入口：解析 stdin，带内部超时运行 check_command，fail-closed。"""
    start_time = time.perf_counter()
    exit_code = 2
    command_str = ""
    matched_patterns = []

    try:
        try:
            payload = json.load(sys.stdin)
            command_str = payload.get("tool_input", {}).get("command", "")
        except Exception as exc:
            print(f"Hook 守卫解析输入失败：{exc}", file=sys.stderr)
            return 2

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(check_command, payload)
            try:
                result = future.result(timeout=TIMEOUT_SECONDS)
                if isinstance(result, tuple):
                    exit_code, matched_patterns = result
                else:
                    exit_code = result if isinstance(result, int) else 0
            except concurrent.futures.TimeoutError:
                print(f"Bash 安全 hook 在 {TIMEOUT_SECONDS} 秒后超时。", file=sys.stderr)
                print("为安全起见阻断命令（fail-closed）。", file=sys.stderr)
                exit_code = 2

    except Exception as exc:
        print(f"Bash 安全 hook 崩溃：{exc}", file=sys.stderr)
        print("为安全起见阻断命令（fail-closed）。", file=sys.stderr)
        exit_code = 2

    finally:
        # 记录性能数据（fail-safe）
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        # 根据退出码和匹配的 patterns 决定 decision
        if exit_code == 2:
            decision = "deny"
        elif matched_patterns:
            decision = "ask"
        else:
            decision = "allow"
        _log_performance(command_str, elapsed_ms, matched_patterns, decision)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

