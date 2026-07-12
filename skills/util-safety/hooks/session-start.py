#!/usr/bin/env python3
"""SessionStart hook：会话启动时浮现健康检查 ERROR。

两阶段执行：
  - 同步：轻量结构/引用检查（< 200ms，无子进程）。
    若发现 ERROR，通过 hookSpecificOutput.additionalContext 向 AI 上下文
    注入启动提醒。
  - 异步：在分离子进程中启动完整 skills-health-check.py，结果写入
    logs/health-check-startup.jsonl。绝不阻塞启动。

Fail-safe：任何错误静默返回 0。绝不阻塞会话启动。
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 导入跨平台工具模块
try:
    from platform_utils import get_python_exe, get_creation_flags, kill_process
except ImportError:
    # 回退到原始逻辑（兼容旧版本）
    def get_python_exe():
        python_exe = sys.executable
        if sys.platform == "win32" and python_exe.endswith("python.exe"):
            pythonw = python_exe[:-10] + "pythonw.exe"
            if Path(pythonw).exists():
                return pythonw
        return python_exe

    def get_creation_flags():
        if sys.platform == "win32":
            return subprocess.CREATE_NO_WINDOW
        return 0

    def kill_process(pid):
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid), "/FI", "IMAGENAME eq pythonw.exe"],
                    capture_output=True,
                    timeout=5,
                )
                return result.returncode == 0
            else:
                os.kill(pid, 15)  # SIGTERM
                return True
        except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
            return False

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CLAUDE_DIR = Path.home() / ".claude"
SKILLS_DIR = CLAUDE_DIR / "skills"
RULES_DIR = SKILLS_DIR / "rules"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"
HEALTH_CHECK_SCRIPT = SKILLS_DIR / "util-check" / "scripts" / "skills-health-check.py"
LOGS_DIR = CLAUDE_DIR / "logs"
STARTUP_LOG = LOGS_DIR / "health-check-startup.jsonl"
GRANTS_DIR = CLAUDE_DIR / ".grants"

# rule-loader.py 的 UserPromptSubmit 去重缓存。压缩/清空/恢复会话后，注入到
# 对话历史的规则（如 workflow.md）会被摘要丢弃，但去重缓存仍记得"已注入"，
# 导致不再补发。这些 source 下清除本 session 的记录，使下一轮重新注入。
_DEDUP_CACHE_PATH = LOGS_DIR / "rule-injection-cache.json"
_DEDUP_RESET_SOURCES = {"compact", "clear", "resume"}


def clear_session_dedup(session_id, source):
    """source 命中重置集时，清除该 session 在去重缓存中的记录。

    内联实现原子写（不复用 rule-loader 的函数）：session-start 是 fail-safe
    增强 hook，缓存清理失败无安全后果，跨文件 import 反而会拖累启动路径。
    任何异常静默吞掉，绝不阻塞会话启动。
    """
    if not session_id or source not in _DEDUP_RESET_SOURCES:
        return
    try:
        if not _DEDUP_CACHE_PATH.exists():
            return
        cache = json.loads(_DEDUP_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(cache, dict) or session_id not in cache:
            return
        del cache[session_id]
        tmp = _DEDUP_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_DEDUP_CACHE_PATH)
    except Exception:
        pass


def cleanup_session_grants():
    """清理上次会话残留的 .session 文件。

    会话级 grant 文件（*.session）应仅在本次 Claude Code 会话内有效。
    启动时自动清理上次会话的残留文件，确保会话隔离。
    """
    try:
        if not GRANTS_DIR.exists():
            return
        for session_file in GRANTS_DIR.glob("*.session"):
            try:
                session_file.unlink()
            except OSError:
                pass
    except Exception:
        pass


def quick_structure_check():
    """轻量检查：每个 skill 目录是否有含 frontmatter 的 SKILL.md。"""
    errors = []
    if not SKILLS_DIR.exists():
        return ["skills/ directory missing"]
    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir() or skill_dir.name == "rules":
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            errors.append(f"{skill_dir.name}/SKILL.md missing")
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as exc:
            errors.append(f"{skill_dir.name}/SKILL.md unreadable: {exc}")
            continue
        if not content.startswith("---"):
            errors.append(f"{skill_dir.name}/SKILL.md missing frontmatter")
    return errors


def quick_reference_check():
    """轻量检查：CLAUDE.md 引用的 rules 文件是否存在。"""
    errors = []
    if not CLAUDE_MD.exists():
        return ["CLAUDE.md missing"]
    try:
        content = CLAUDE_MD.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"CLAUDE.md unreadable: {exc}"]
    for rule_file in re.findall(r"skills/rules/([\w-]+\.md)", content):
        if not (RULES_DIR / rule_file).exists():
            errors.append(f"CLAUDE.md references missing skills/rules/{rule_file}")
    return errors


def emit_additional_context(errors):
    """输出 SessionStart hookSpecificOutput.additionalContext。"""
    summary_lines = [f"[session-start] 启动自检发现 {len(errors)} 项 ERROR："]
    for i, err in enumerate(errors[:10], start=1):
        summary_lines.append(f"  {i}. {err}")
    if len(errors) > 10:
        summary_lines.append(f"  ... 另有 {len(errors) - 10} 项，详见 logs/health-check-startup.jsonl")
    summary_lines.append("\n建议立即运行 `/util-check` 排查并修复。")
    additional_context = "\n".join(summary_lines)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    print(f"[session-start] 已向 AI 上下文浮现 {len(errors)} 项 ERROR", file=sys.stderr)


def spawn_background_check():
    """在后台子进程中启动完整 skills-health-check.py。

    通过 PID 锁文件防止并发堆积：每次启动前先终止上一轮的残留进程，
    然后用 CREATE_NO_WINDOW（不带 DETACHED_PROCESS）派生子进程。

    v2.12: 节流为每天最多一次（CLAUDE_FORCE_STARTUP_HEALTH_CHECK=1 可强制）。
    同步轻量检查仍在每次 SessionStart 运行，不受此限制。
    """
    if not HEALTH_CHECK_SCRIPT.exists():
        return
    # 避免递归：health-check 行为测试会触发 session-start，不应再 spawn
    if os.environ.get("CLAUDE_HEALTH_CHECK_CONTEXT") == "1":
        return

    # 每天最多一次：读取 daily stamp 文件，同日已 spawn 则跳过
    force = os.environ.get("CLAUDE_FORCE_STARTUP_HEALTH_CHECK") == "1"
    if not force:
        try:
            stamp_file = LOGS_DIR / "health-check.daily-stamp"
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if stamp_file.exists():
                if stamp_file.read_text(encoding="utf-8").strip() == today:
                    return
        except Exception:
            pass  # stamp 读取失败不影响主流程，fall through 执行 spawn

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        runs_dir = LOGS_DIR / "health-check-runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        pid_file = LOGS_DIR / "health-check.pid"

        # 终止上一轮残留进程
        if pid_file.exists():
            try:
                prev_pid = int(pid_file.read_text().strip())
                kill_process(prev_pid)
            except Exception:
                pass

        ts = datetime.now(timezone.utc).astimezone()
        ts_compact = ts.strftime("%Y%m%dT%H%M%S")
        run_log = runs_dir / f"run-{ts_compact}-{os.getpid()}.log"

        log_handle = run_log.open("w", encoding="utf-8")

        creationflags = get_creation_flags()
        python_exe = get_python_exe()

        child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

        proc = subprocess.Popen(
            [python_exe, str(HEALTH_CHECK_SCRIPT)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            env=child_env,
        )
        log_handle.close()

        # 写入 PID 锁
        pid_file.write_text(str(proc.pid))

        with STARTUP_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": ts.isoformat(timespec="seconds"),
                "event": "spawned",
                "pid": proc.pid,
                "run_log": run_log.name,
            }, ensure_ascii=False) + "\n")

        # 写入 daily stamp，同日不再重复 spawn
        try:
            stamp_file = LOGS_DIR / "health-check.daily-stamp"
            stamp_file.write_text(
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                encoding="utf-8",
            )
        except Exception:
            pass

        try:
            run_logs = sorted(runs_dir.glob("run-*.log"), reverse=True)
            for stale in run_logs[5:]:
                stale.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception:
        pass


def main():
    try:
        t0 = time.perf_counter()

        # 清理上次会话的 .session 文件
        cleanup_session_grants()

        payload = {}
        try:
            payload = json.load(sys.stdin)
        except Exception:
            pass
        if not isinstance(payload, dict):
            payload = {}

        # 压缩/清空/恢复会话后，重置规则注入去重缓存，使规则重新注入
        clear_session_dedup(
            str(payload.get("session_id") or ""),
            str(payload.get("source") or ""),
        )

        errors = quick_structure_check() + quick_reference_check()

        if errors:
            emit_additional_context(errors)

        spawn_background_check()

        elapsed_ms = (time.perf_counter() - t0) * 1000
        if elapsed_ms > 500:
            print(
                f"\n[session-start] 同步自检耗时 {elapsed_ms:.0f}ms（> 500ms 承诺），"
                f"建议检查 skills/ 目录大小或磁盘 I/O 性能。",
                file=sys.stderr,
            )

    except Exception as exc:
        print(f"[session-start] 错误（非阻塞）：{exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
