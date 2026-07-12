#!/usr/bin/env python3
"""bash-audit-post.py hook 单元测试。"""
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
except Exception:
    pass

HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "bash-audit-post.py"
WRAPPER_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "bash-safety-wrapper.py"


def run_post(payload, home: Path, session_id: str = "s1", extra_env: dict | None = None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["CLAUDE_SESSION_ID"] = session_id
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=10,
        env=env,
    )
    return result.returncode, result.stdout.decode(), result.stderr.decode()


def run_wrapper(command: str, home: Path, session_id: str = "s1"):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["CLAUDE_SESSION_ID"] = session_id
    with tempfile.TemporaryDirectory() as grants:
        env["CLAUDE_TEST_GRANTS_DIR"] = grants
        result = subprocess.run(
            [sys.executable, str(WRAPPER_SCRIPT)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}).encode("utf-8"),
            capture_output=True,
            timeout=20,
            env=env,
        )
    return result.returncode, result.stdout.decode(), result.stderr.decode()


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_post_exec_audits_dangerous_command_and_remembers_label():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        payload = {"tool_name": "Bash", "tool_input": {"command": "git commit -m test"}}
        code, _, stderr = run_post(payload, home, session_id="s1")
        assert code == 0, stderr

        audit_log = home / ".claude" / "logs" / "bash-safety-audit.jsonl"
        entries = read_jsonl(audit_log)
        assert len(entries) == 1
        assert entries[0]["source"] == "post-exec"
        assert entries[0]["operations"] == ["git commit"]

        memo = json.loads((home / ".claude" / "logs" / "ask-approved-cache.json").read_text(encoding="utf-8"))
        assert "git:git commit" in memo["sessions"]["s1"]["operations"]
    print("[PASS] post-exec 审计与 memo 记录测试通过")


def test_safe_and_malformed_inputs_fail_safe():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        code, _, stderr = run_post({"tool_name": "Bash", "tool_input": {"command": "git status"}}, home)
        assert code == 0, stderr
        assert not (home / ".claude" / "logs" / "bash-safety-audit.jsonl").exists()

        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        result = subprocess.run([sys.executable, str(HOOK_SCRIPT)], input=b"not json", capture_output=True, timeout=10, env=env)
        assert result.returncode == 0, "malformed input must fail-safe"
    print("[PASS] safe/malformed fail-safe 测试通过")


def test_session_memo_allows_same_operation_label_only():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        run_post({"tool_name": "Bash", "tool_input": {"command": "git commit -m test"}}, home, session_id="s1")

        code, stdout, stderr = run_wrapper("git commit -m again", home, session_id="s1")
        assert code == 0 and "permissionDecision" not in stdout, f"同 session 同操作标签应直接放行：{stdout} {stderr}"
        entries = read_jsonl(home / ".claude" / "logs" / "bash-safety-audit.jsonl")
        assert entries[-1]["source"] == "session-ask-memo"

        code, stdout, _ = run_wrapper("git push origin main", home, session_id="s1")
        assert code == 0 and "permissionDecision" in stdout, "同类别不同操作标签仍应 ask"

        code, stdout, _ = run_wrapper("git commit -m other-session", home, session_id="s2")
        assert code == 0 and "permissionDecision" in stdout, "不同 session 仍应 ask"
    print("[PASS] session memo 操作标签粒度测试通过")


def test_memo_lru_prunes_old_sessions():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        for i in range(3):
            run_post(
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m test"}},
                home,
                session_id=f"s{i}",
                extra_env={"CLAUDE_ASK_MEMO_MAX_SESSIONS": "2"},
            )
        memo = json.loads((home / ".claude" / "logs" / "ask-approved-cache.json").read_text(encoding="utf-8"))
        assert sorted(memo["sessions"].keys()) == ["s1", "s2"]
    print("[PASS] memo LRU 裁旧测试通过")


def _call_post(home_dir, idx):
    payload = {"tool_name": "Bash", "tool_input": {"command": f"git commit -m concurrent-{idx}"}}
    code, _, _ = run_post(payload, Path(home_dir), session_id=f"s{idx}")
    return code == 0


def test_concurrent_post_exec_writes_no_interleaving():
    with tempfile.TemporaryDirectory() as tmp:
        num = 8
        with multiprocessing.Pool(processes=num) as pool:
            results = pool.starmap(_call_post, [(tmp, i) for i in range(num)])
        assert all(results), results
        entries = read_jsonl(Path(tmp) / ".claude" / "logs" / "bash-safety-audit.jsonl")
        assert len(entries) == num
        assert all(entry["source"] == "post-exec" for entry in entries)
    print("[PASS] post-exec 并发审计写入测试通过")


def main():
    if not HOOK_SCRIPT.exists():
        print(f"错误：Hook 脚本未找到：{HOOK_SCRIPT}", file=sys.stderr)
        return 1
    try:
        test_post_exec_audits_dangerous_command_and_remembers_label()
        test_safe_and_malformed_inputs_fail_safe()
        test_session_memo_allows_same_operation_label_only()
        test_memo_lru_prunes_old_sessions()
        test_concurrent_post_exec_writes_no_interleaving()
        print("\n[OK] 全部 bash-audit-post.py 测试通过！")
        return 0
    except AssertionError as exc:
        print(f"\n[FAIL] 测试失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[FAIL] 意外错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
