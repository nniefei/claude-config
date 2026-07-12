#!/usr/bin/env python3
"""Unit tests for rule-loader.py hook."""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "rule-loader.py"
MODE_LOG = Path.home() / ".claude" / "logs" / "mode-transitions.jsonl"


def run_hook(payload):
    """Run rule-loader with JSON payload, return (exit, stdout, stderr).
    Tests enable VERBOSE so stderr asserts still work after Plan-2 step 2.5 silenced stderr by default."""
    env = dict(os.environ)
    env["CLAUDE_RULE_LOADER_VERBOSE"] = "1"
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        timeout=10,
        env=env,
    )
    return (
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def parse_stdout_json(stdout):
    """Parse first JSON line from stdout."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    return None


def test_bash_git_triggers_git_safety():
    code, stdout, stderr = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m test"},
    })
    assert code == 0, f"Expected exit 0, got {code}"
    data = parse_stdout_json(stdout)
    assert data is not None, f"No JSON in stdout: {stdout!r}"
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "git-safety" in ctx.lower(), f"git-safety not in context"
    assert "loaded: git-safety.md" in stderr
    print("[PASS] Bash git triggers git-safety test passed")


def test_bash_non_git_no_inject():
    code, stdout, _ = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
    })
    assert code == 0
    assert not stdout.strip(), f"Expected empty stdout for non-git Bash, got {stdout!r}"
    print("[PASS] Bash non-git no-inject test passed")


def test_write_memory_no_longer_injects_memory_rule():
    # v2.13: memory 规则已回归原生，写入 memory 文件不再注入自定义规则
    code, stdout, _ = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "C:/Users/nnie/proj/memory/debugging.md"},
    })
    assert code == 0
    assert not stdout.strip(), f"Expected no inject after memory rules removal, got {stdout!r}"
    print("[PASS] Write memory no longer injects memory rule test passed")


def test_skill_md_triggers_skill_org():
    code, stdout, _ = run_hook({
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "C:/Users/nnie/.claude/skills/util-foo/SKILL.md"},
    })
    assert code == 0
    data = parse_stdout_json(stdout)
    assert data is not None
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "skill-org" in ctx.lower()
    print("[PASS] SKILL.md triggers skill-org test passed")


def test_userpromptsubmit_silent_mode_kw():
    code, stdout, _ = run_hook({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "你看着办",
    })
    assert code == 0
    data = parse_stdout_json(stdout)
    assert data is not None, f"Expected JSON output, got: {stdout[:200]}"
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "workflow" in ctx.lower()
    assert "如需进入，请在下一条 prompt 前加 `[silent]` 前缀" in ctx
    print("[PASS] UserPromptSubmit soft-hint stays standard test passed")


def test_userpromptsubmit_explicit_silent_prefix():
    cases = [
        ("[silent] 修改 src/foo.ts", "explicit-prefix"),
        ("[静默] 干就完了", "explicit-prefix"),
    ]
    for prompt, expected_hit in cases:
        code, stdout, _ = run_hook({
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        })
        assert code == 0
        data = parse_stdout_json(stdout)
        assert data is not None, f"Expected JSON output, got: {stdout[:200]}"
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "workflow" in ctx.lower()
        assert expected_hit
    print("[PASS] UserPromptSubmit explicit silent prefix test passed")


def test_userpromptsubmit_logs_mode():
    marker = "TESTMARKER_RULE_LOADER_LOG_001"
    before = MODE_LOG.read_text(encoding="utf-8") if MODE_LOG.exists() else ""
    code, _, _ = run_hook({
        "hook_event_name": "UserPromptSubmit",
        "prompt": f"别问了直接做 修改 foo.ts {marker}",
    })
    assert code == 0
    after = MODE_LOG.read_text(encoding="utf-8") if MODE_LOG.exists() else ""
    new = after[len(before):]
    assert marker in new, f"Marker not found in new log entries: {new[:200]}"
    for line in new.splitlines():
        if marker in line:
            entry = json.loads(line)
            assert entry["inferred_mode"] == "standard", f"Expected standard, got {entry['inferred_mode']}"
            assert entry["trigger_keywords"] == ["soft-hint:别问了直接做"]
            break
    else:
        raise AssertionError("marker line not found")
    print("[PASS] UserPromptSubmit logs soft-hint standard mode test passed")


def test_userpromptsubmit_quick_mode_no_inject():
    code, stdout, _ = run_hook({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Vue3 响应式原理是什么",
    })
    assert code == 0
    assert not stdout.strip(), f"Expected no inject for quick mode, got {stdout!r}"
    print("[PASS] UserPromptSubmit quick mode no-inject test passed")


def test_userpromptexpansion_util_skill():
    code, stdout, _ = run_hook({
        "hook_event_name": "UserPromptExpansion",
        "command_name": "util-check",
    })
    assert code == 0
    # v2.12: skill-boundaries.md 已降级为参考文档，不再自动注入
    # util-* UserPromptExpansion 不再注入任何规则
    assert not stdout.strip(), f"Expected no inject after skill-boundaries demotion, got {stdout!r}"
    print("[PASS] UserPromptExpansion util-skill no-inject test passed")


def test_rule_loader_never_blocks():
    """Even with malformed input or missing files, never block (return 0)."""
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=b"not json at all",
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, f"Malformed input should not block: exit={result.returncode}"

    code, _, _ = run_hook({"hook_event_name": "SomeUnknownEvent"})
    assert code == 0

    code, _, _ = run_hook({"hook_event_name": "PreToolUse", "tool_name": "Bash"})
    assert code == 0

    print("[PASS] Rule-loader never blocks test passed")


def test_stop_event_no_inject():
    """Stop event must NOT inject anything.

    Plan-3 3.5 曾尝试用 hookSpecificOutput.additionalContext 注入 memory 提醒，
    实测证明 Stop hook 的 schema 不支持 additionalContext（Claude Code 会报
    "Hook JSON output validation failed - Invalid input"）。回退到 no-op。
    """
    code, stdout, _ = run_hook({"hook_event_name": "Stop"})
    assert code == 0
    data = parse_stdout_json(stdout)
    assert data is None, f"Stop event should emit nothing, got: {stdout!r}"
    print("[PASS] Stop event no-inject test passed")


def test_stderr_silent_by_default():
    """Plan-2 2.5: 默认不输出 [rule-loader] loaded 行（除非 VERBOSE=1）。"""
    env = {k: v for k, v in os.environ.items()
           if k != "CLAUDE_RULE_LOADER_VERBOSE"}
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m test"},
        }).encode("utf-8"),
        capture_output=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert "[rule-loader] loaded" not in stderr, \
        f"stderr should be silent without VERBOSE, got: {stderr!r}"
    # stdout 仍正常包含 JSON
    assert "additionalContext" in result.stdout.decode("utf-8", errors="replace")
    print("[PASS] Stderr silent by default test passed")


def test_stderr_verbose_mode():
    """Plan-2 2.5: VERBOSE=1 时 stderr 输出 [rule-loader] loaded 行。"""
    env = dict(os.environ)
    env["CLAUDE_RULE_LOADER_VERBOSE"] = "1"
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m test"},
        }).encode("utf-8"),
        capture_output=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert "[rule-loader] loaded" in stderr, \
        f"VERBOSE should emit loaded line, got: {stderr!r}"
    print("[PASS] Stderr verbose mode test passed")


def test_session_dedup():
    """P1: 同 session_id 内 workflow.md 仅注入一次，第二次不注入。"""
    import uuid
    # 用唯一 session_id，避免被历史残留缓存污染（重跑稳定）
    sid = f"test-dedup-{uuid.uuid4()}"
    try:
        # 第一次：应注入 workflow.md
        code1, stdout1, _ = run_hook({
            "hook_event_name": "UserPromptSubmit",
            "session_id": sid,
            "prompt": "修改 src/foo.ts",
        })
        assert code1 == 0
        data1 = parse_stdout_json(stdout1)
        assert data1 is not None, f"First inject should emit JSON, got: {stdout1[:200]}"
        assert "workflow" in data1["hookSpecificOutput"]["additionalContext"].lower()
        # 第二次：同 session_id，不应再注入
        code2, stdout2, _ = run_hook({
            "hook_event_name": "UserPromptSubmit",
            "session_id": sid,
            "prompt": "再修改 src/bar.ts",
        })
        assert code2 == 0
        assert not stdout2.strip(), f"Second inject should be empty (dedup), got: {stdout2!r}"
    finally:
        # 清理本测试在真实缓存里留下的 session（Stop 清理已移除，直接删缓存条目）。
        # 缓存路径与 rule-loader._DEDUP_CACHE_PATH 一致。
        cache_path = Path.home() / ".claude" / "logs" / "rule-injection-cache.json"
        try:
            if cache_path.exists():
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                if sid in cache:
                    del cache[sid]
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    print("[PASS] Session dedup test passed")


def test_session_dedup_fallback_no_session_id():
    """P1: 无 session_id 时回退到每次都注入。"""
    code1, stdout1, _ = run_hook({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "修改 foo.py",
    })
    assert code1 == 0
    data1 = parse_stdout_json(stdout1)
    assert data1 is not None
    assert "workflow" in data1["hookSpecificOutput"]["additionalContext"].lower()
    # 第二次：无 session_id，仍应注入
    code2, stdout2, _ = run_hook({
        "hook_event_name": "UserPromptSubmit",
        "prompt": "修改 bar.py",
    })
    assert code2 == 0
    data2 = parse_stdout_json(stdout2)
    assert data2 is not None
    assert "workflow" in data2["hookSpecificOutput"]["additionalContext"].lower()
    print("[PASS] Session dedup fallback (no session_id) test passed")


def test_file_ext_pattern_expanded():
    """P2: 新增扩展名 .kt .swift .cs .rb .php .sql .tf 等触发标准模式。"""
    new_extensions = [
        "修改 src/Main.kt",
        "修复 App.swift",
        "重构 Program.cs",
        "改一下 helper.rb",
        "修改 index.php",
        "改一下 schema.sql",
        "修改 deploy.tf",
        "改一下 main.tf backend.hcl",
        "部署 docker-compose.yml",
        "修改 setup.sh",
        "重构 script.ps1",
    ]
    for prompt in new_extensions:
        code, stdout, _ = run_hook({
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        })
        assert code == 0
        data = parse_stdout_json(stdout)
        assert data is not None, f"New ext should trigger standard mode for: {prompt}"
        assert "workflow" in data["hookSpecificOutput"]["additionalContext"].lower(), \
            f"workflow not injected for: {prompt}"
    print("[PASS] FILE_EXT_PATTERN expanded test passed")


def test_silent_command_user_prompt_submit():
    """P3: /silent 命令在 UserPromptSubmit 中触发静默模式。"""
    cases = [
        "/silent 修改 src/foo.ts",
        "  /silent 干就完了",
        "/silent",
    ]
    for prompt in cases:
        code, stdout, _ = run_hook({
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        })
        assert code == 0
        data = parse_stdout_json(stdout)
        assert data is not None, f"Expected JSON for /silent prompt: {prompt}"
        ctx = data["hookSpecificOutput"]["additionalContext"]
        assert "workflow" in ctx.lower(), f"workflow not injected for /silent prompt: {prompt}"
        # /silent 不应该输出 soft-hint（因为已进入静默模式）
        assert "如需进入" not in ctx, \
            f"/silent should not emit soft-hint, got hint in: {prompt}"
    print("[PASS] /silent command UserPromptSubmit test passed")


def test_pretooluse_dedup():
    """P2-3: 同 session 连续两次 PreToolUse git 命令，第二次不应重复注入。"""
    import uuid
    sid = f"pretooluse-dedup-{uuid.uuid4()}"
    try:
        code1, stdout1, _ = run_hook({
            "hook_event_name": "PreToolUse",
            "session_id": sid,
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m test"},
        })
        assert code1 == 0
        data1 = parse_stdout_json(stdout1)
        assert data1 is not None, f"First PreToolUse should emit JSON: {stdout1[:200]}"
        assert "git-safety" in data1["hookSpecificOutput"]["additionalContext"].lower()

        code2, stdout2, _ = run_hook({
            "hook_event_name": "PreToolUse",
            "session_id": sid,
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        })
        assert code2 == 0
        assert not stdout2.strip(), f"Second PreToolUse should be empty (dedup): {stdout2!r}"
    finally:
        cache_path = Path.home() / ".claude" / "logs" / "rule-injection-cache.json"
        try:
            if cache_path.exists():
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cache.pop(sid, None)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    print("[PASS] PreToolUse dedup test passed")


def test_pretooluse_different_sessions():
    """不同 session 应各自注入。"""
    import uuid
    s1 = f"pre-diff-s1-{uuid.uuid4()}"
    s2 = f"pre-diff-s2-{uuid.uuid4()}"
    try:
        code1, stdout1, _ = run_hook({
            "hook_event_name": "PreToolUse",
            "session_id": s1,
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m test"},
        })
        assert code1 == 0
        assert "git-safety" in parse_stdout_json(stdout1)["hookSpecificOutput"]["additionalContext"].lower()

        code2, stdout2, _ = run_hook({
            "hook_event_name": "PreToolUse",
            "session_id": s2,
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m test"},
        })
        assert code2 == 0
        assert "git-safety" in parse_stdout_json(stdout2)["hookSpecificOutput"]["additionalContext"].lower()
    finally:
        cache_path = Path.home() / ".claude" / "logs" / "rule-injection-cache.json"
        try:
            if cache_path.exists():
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cache.pop(s1, None); cache.pop(s2, None)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    print("[PASS] PreToolUse different sessions test passed")


def test_session_start_compact_clears_dedup_for_pretooluse():
    """2.7.2 的 clear_session_dedup（source=compact）应使 PreToolUse 重新注入。"""
    # 手动模拟 clear_session_dedup 效果：直接删缓存中该 session 条目
    import uuid
    sid = f"compact-pt-{uuid.uuid4()}"
    try:
        code1, stdout1, _ = run_hook({
            "hook_event_name": "PreToolUse",
            "session_id": sid,
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m test"},
        })
        assert code1 == 0 and "git-safety" in stdout1.lower()

        code2, stdout2, _ = run_hook({
            "hook_event_name": "PreToolUse",
            "session_id": sid,
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        })
        assert code2 == 0 and not stdout2.strip(), "Second inject should be empty"

        # 模拟 compact 清缓存
        cache_path = Path.home() / ".claude" / "logs" / "rule-injection-cache.json"
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        cache.pop(sid, None)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

        code3, stdout3, _ = run_hook({
            "hook_event_name": "PreToolUse",
            "session_id": sid,
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        })
        assert code3 == 0 and "git-safety" in stdout3.lower(), "After compact clear, should re-inject"
    finally:
        cache_path = Path.home() / ".claude" / "logs" / "rule-injection-cache.json"
        try:
            if cache_path.exists():
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cache.pop(sid, None)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    print("[PASS] PreToolUse compact clears dedup test passed")


def test_userpromptexpansion_dedup():
    """v2.12: util-* UserPromptExpansion 不再注入规则，两次调用均应无输出。"""
    import uuid
    sid = f"exp-dedup-{uuid.uuid4()}"
    try:
        code1, stdout1, _ = run_hook({
            "hook_event_name": "UserPromptExpansion",
            "session_id": sid,
            "command_name": "util-check",
        })
        assert code1 == 0
        # skill-boundaries.md 已降级，不再自动注入
        assert not stdout1.strip(), f"Expected no inject after skill-boundaries demotion: {stdout1!r}"

        code2, stdout2, _ = run_hook({
            "hook_event_name": "UserPromptExpansion",
            "session_id": sid,
            "command_name": "util-safety",
        })
        assert code2 == 0
        assert not stdout2.strip(), f"Second UserPromptExpansion should also be empty: {stdout2!r}"
    finally:
        cache_path = Path.home() / ".claude" / "logs" / "rule-injection-cache.json"
        try:
            if cache_path.exists():
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cache.pop(sid, None)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    print("[PASS] UserPromptExpansion dedup test passed")


def test_silent_command_user_prompt_expansion():
    """P3: /silent 在 UserPromptExpansion 中注入 workflow.md。"""
    code, stdout, _ = run_hook({
        "hook_event_name": "UserPromptExpansion",
        "command_name": "/silent",
    })
    assert code == 0
    data = parse_stdout_json(stdout)
    assert data is not None, f"Expected JSON for /silent expansion"
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "workflow" in ctx.lower(), f"workflow not injected for /silent expansion"
    print("[PASS] /silent command UserPromptExpansion test passed")


def main():
    if not HOOK_SCRIPT.exists():
        print(f"Error: hook script not found at {HOOK_SCRIPT}", file=sys.stderr)
        return 1

    try:
        test_bash_git_triggers_git_safety()
        test_bash_non_git_no_inject()
        test_write_memory_triggers_memory_rule()
        test_skill_md_triggers_skill_org()
        test_userpromptsubmit_silent_mode_kw()
        test_userpromptsubmit_explicit_silent_prefix()
        test_userpromptsubmit_logs_mode()
        test_userpromptsubmit_quick_mode_no_inject()
        test_userpromptexpansion_util_skill()
        test_rule_loader_never_blocks()
        test_stop_event_no_inject()
        test_stderr_silent_by_default()
        test_stderr_verbose_mode()
        test_session_dedup()
        test_session_dedup_fallback_no_session_id()
        test_file_ext_pattern_expanded()
        test_silent_command_user_prompt_submit()
        test_silent_command_user_prompt_expansion()
        test_pretooluse_dedup()
        test_pretooluse_different_sessions()
        test_session_start_compact_clears_dedup_for_pretooluse()
        test_userpromptexpansion_dedup()

        print("\n[OK] All rule-loader.py tests passed!")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
