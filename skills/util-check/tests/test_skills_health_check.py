#!/usr/bin/env python3
"""Unit tests for skills-health-check.py.

Uses temporary fixture directories so the production ~/.claude tree is not modified.
"""
import importlib.util
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT_PATH = Path.home() / ".claude" / "skills" / "util-check" / "scripts" / "skills-health-check.py"


def load_module():
    spec = importlib.util.spec_from_file_location("skills_health_check", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def configure_module(module, root: Path) -> None:
    module.CLAUDE_DIR = root
    module.SKILLS_DIR = root / "skills"
    module.RULES_DIR = module.SKILLS_DIR / "rules"
    module.SAFETY_SKILL_DIR = module.SKILLS_DIR / "util-safety"
    module.SAFETY_HOOKS_DIR = module.SAFETY_SKILL_DIR / "hooks"
    module.SAFETY_TESTS_DIR = module.SAFETY_SKILL_DIR / "tests"
    module.CHECK_SKILL_DIR = module.SKILLS_DIR / "util-check"
    module.CHECK_SCRIPTS_DIR = module.CHECK_SKILL_DIR / "scripts"
    module.CHECK_TESTS_DIR = module.CHECK_SKILL_DIR / "tests"
    module.TEST_SCRIPTS = [
        module.SAFETY_TESTS_DIR / "test_bash_safety.py",
        module.SAFETY_TESTS_DIR / "test_write_safety.py",
        module.CHECK_TESTS_DIR / "test_skills_health_check.py",
    ]
    module.SETTINGS_FILE = root / "settings.json"
    module.CLAUDE_MD = root / "CLAUDE.md"
    module.CHANGELOG_MD = root / "CHANGELOG.md"
    module.README_MD = root / "skills" / "README.md"
    # v2.2.1: redirect secret-scan target list to fixture dir
    module.CONFIG_FILES_TO_SCAN = [root / "settings.json", root / "settings.local.json"]
    module.passed = []
    module.warnings = []
    module.errors = []
    module.stats = {}


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def skill_doc(name: str, extra_frontmatter: str = "", body: str = "") -> str:
    return f"""---
name: {name}
description: test skill
user-invocable: true
{extra_frontmatter}---

# {name}

{body}
"""


def with_fixture(fn):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module = load_module()
        configure_module(module, root)
        return fn(module, root)


def create_minimal_valid_tree(root: Path) -> None:
    write(root / "skills" / "util-test" / "SKILL.md", skill_doc("util-test"))
    write(root / "skills" / "util-safety" / "SKILL.md", skill_doc("util-safety"))
    write(root / "skills" / "rules" / "rule.md", "# Rule\n")
    write(root / "CHANGELOG.md", "## [99.0.0] - 2026-01-01\n\n### Fixed\n- test\n")
    write(root / "CLAUDE.md", "See skills/rules/rule.md\n")
    write(root / "skills" / "README.md", "# Claude Code\n")
    write(root / "skills" / "util-safety" / "hooks" / "bash-safety-wrapper.py", """#!/usr/bin/env python3
# Minimal fixture wrapper for v2.2.0 self-contained design.
# 计划2: 带外授权（.grants/<category> 文件）+ 命令名归一化 fixture。
import concurrent.futures
import json
import os
import re
import sys
from pathlib import Path

DANGEROUS_PATTERNS = [("git", "git commit")]  # noqa: marker for health check
TIMEOUT_SECONDS = 15

GRANTS_DIR = Path(os.environ.get("CLAUDE_TEST_GRANTS_DIR",
                  str(Path.home() / ".claude" / ".grants")))


def _grant(cat):
    f = GRANTS_DIR / cat
    if f.exists():
        try:
            f.unlink()
        except OSError:
            return False
        return True
    return os.environ.get("CLAUDE_HOOK_APPROVED_" + cat.upper()) == "1"


def _norm(command):
    # fixture 简化版：把每个 token 的路径前缀（/bin/rm -> rm）剥掉，供命令名匹配
    toks = command.replace("|", " ").replace("&", " ").replace(";", " ").split()
    based = [t.rsplit("/", 1)[-1].rsplit(chr(92), 1)[-1] for t in toks]
    return " ".join(based)


def _ask(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))
    return 0


def check_command(payload):
    command = payload.get("tool_input", {}).get("command", "")
    norm = _norm(command)
    blob = command + " || " + norm
    if "bash -c" in blob:
        if _grant("subshell"):
            return 0
        return _ask("bash/sh -c 子壳")
    if "rm -rf" in blob or "rm -fr" in blob:
        if _grant("delete"):
            return 0
        return _ask("递归强制删除")
    if "git commit" in blob or "git push" in blob:
        if _grant("git"):
            return 0
        return _ask("git 提交")
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            try:
                return ex.submit(check_command, payload).result(timeout=TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                print("为安全起见阻断命令（fail-closed）", file=sys.stderr)
                return 2
    except Exception:
        print("为安全起见阻断命令（fail-closed）", file=sys.stderr)
        return 2


raise SystemExit(main())
""")
    write(root / "skills" / "util-safety" / "hooks" / "write-safety.py", """#!/usr/bin/env python3
# fail-closed + TimeoutError fixture stub: just enough for util-check compliance
import json
import sys
try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TIMEOUT_SECONDS = 5
# TimeoutError marker for Plan-3 3.7 compliance check

payload = json.load(sys.stdin)
file_path = payload.get("tool_input", {}).get("file_path", "")
if file_path.endswith(".env"):
    print("sensitive 拦截", file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(0)
""")
    write(root / "skills" / "util-safety" / "hooks" / "mcp-safety.py", """#!/usr/bin/env python3
# fail-closed fixture with TimeoutError marker and CLAUDE_HOOK_APPROVED_MCP support
import json
import os
import sys
try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TIMEOUT_SECONDS = 5
# TimeoutError marker

payload = json.load(sys.stdin)
tool = payload.get("tool_name", "")
tool_input = payload.get("tool_input", {})
# 精确黑名单（含 smoke test 的 force_push）
BLOCKLIST = {"mcp__slack__send_message", "mcp__github__force_push"}
if tool in BLOCKLIST and os.environ.get("CLAUDE_HOOK_APPROVED_MCP") != "1":
    print("blocklist 黑名单", file=sys.stderr)
    raise SystemExit(2)
if "token" in tool_input:
    print("credential 凭据", file=sys.stderr)
    raise SystemExit(2)
# 动词模式：取 tool 名最后一段，allow 只读前缀优先放行，block 高危动词阻断
_segment = tool.rsplit("__", 1)[-1].lower()
if _segment.startswith(("get_", "list_", "search_", "read_")):
    raise SystemExit(0)
if any(_verb in _segment for _verb in ("delete", "deploy", "publish", "force", "send")):
    if os.environ.get("CLAUDE_HOOK_APPROVED_MCP") != "1":
        print("高风险动作模式", file=sys.stderr)
        raise SystemExit(2)
raise SystemExit(0)
""")
    write(root / "skills" / "util-safety" / "hooks" / "mcp-audit.py", """#!/usr/bin/env python3
import sys
raise SystemExit(0)
""")
    write(root / "skills" / "util-safety" / "hooks" / "bash-audit-post.py", """#!/usr/bin/env python3
import sys
raise SystemExit(0)
""")
    write(root / "settings.json", json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": f"{sys.executable} {root / 'skills' / 'util-safety' / 'hooks' / 'bash-safety-wrapper.py'}"}]
                },
                {
                    "matcher": "Write|Edit",
                    "hooks": [{"type": "command", "command": f"{sys.executable} {root / 'skills' / 'util-safety' / 'hooks' / 'write-safety.py'}"}]
                },
                {
                    "matcher": "mcp__.*",
                    "hooks": [
                        {"type": "command", "command": f"{sys.executable} {root / 'skills' / 'util-safety' / 'hooks' / 'mcp-safety.py'}"},
                        {"type": "command", "command": f"{sys.executable} {root / 'skills' / 'util-safety' / 'hooks' / 'mcp-audit.py'}"}
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": f"{sys.executable} {root / 'skills' / 'util-safety' / 'hooks' / 'bash-audit-post.py'}"}]
                }
            ]
        }
    }))


def test_structure_integrity_passes():
    def run(module, root):
        create_minimal_valid_tree(root)
        module.check_structure_integrity()
        assert not module.errors
        assert module.stats["skill_count"] == 2
        assert "结构完整性：全部通过" in module.passed

    with_fixture(run)
    print("[PASS] Structure integrity test passed")


def test_reference_consistency_detects_missing_rule():
    def run(module, root):
        create_minimal_valid_tree(root)
        write(root / "CLAUDE.md", "See skills/rules/missing.md\n")
        module.check_reference_consistency()
        assert any("missing.md" in error for error in module.errors)

    with_fixture(run)
    print("[PASS] Missing rule reference test passed")


def test_memory_path_warning_without_baseline():
    def run(module, root):
        create_minimal_valid_tree(root)
        write(
            root / "skills" / "rules" / "memory-rule.md",
            "# Memory Rule\n\nWrite to `memory/MEMORY.md`.\n"
        )
        module.check_memory_path_consistency()
        assert any("缺少路径基准说明" in warning for warning in module.warnings)

    with_fixture(run)
    print("[PASS] Memory path warning test passed")


def test_memory_path_baseline_suppresses_warning():
    def run(module, root):
        create_minimal_valid_tree(root)
        write(
            root / "skills" / "rules" / "memory-rule.md",
            "# Memory Rule\n\n路径说明：`memory/` 均指系统提供的项目 memory 路径。\nWrite to `memory/MEMORY.md`.\n"
        )
        module.check_memory_path_consistency()
        assert not module.warnings

    with_fixture(run)
    print("[PASS] Memory path baseline test passed")


def test_dependency_cycle_detected():
    def run(module, root):
        write(root / "skills" / "util-a" / "SKILL.md", skill_doc("util-a", "depends-on: [util-b]\n"))
        write(root / "skills" / "util-b" / "SKILL.md", skill_doc("util-b", "depends-on: [util-a]\n"))
        module.check_dependency_graph()
        assert any("循环依赖" in error for error in module.errors)

    with_fixture(run)
    print("[PASS] Dependency cycle test passed")


def test_runtime_constraints_accepts_wrapper():
    def run(module, root):
        create_minimal_valid_tree(root)
        module.check_runtime_constraints()
        assert not module.errors

    with_fixture(run)
    print("[PASS] Runtime constraints wrapper test passed")



def test_runtime_constraints_requires_bash_wrapper():
    def run(module, root):
        create_minimal_valid_tree(root)
        write(root / "settings.json", json.dumps({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "python bash-safety-wrapper.py"}]
                    },
                    {
                        "matcher": "Write|Edit",
                        "hooks": [{"type": "command", "command": "C:/Python/python.exe write-safety.py"}]
                    }
                ]
            }
        }))
        module.check_runtime_constraints()
        assert any("bash-safety-wrapper" in error for error in module.errors)

    with_fixture(run)
    print("[PASS] Runtime constraints bash wrapper test passed")


def test_runtime_constraints_checks_wrapper_fail_closed():
    def run(module, root):
        create_minimal_valid_tree(root)
        write(root / "skills" / "util-safety" / "hooks" / "bash-safety-wrapper.py", "# missing fail closed\n")
        module.check_runtime_constraints()
        assert any("fail-closed" in error for error in module.errors)
        # v2.2.0: split into separate checks for return-2 / timeout / merged core
        assert any("异常阻断逻辑" in error for error in module.errors)
        assert any("超时处理逻辑" in error for error in module.errors)
        assert any("未合并 bash-safety 核心逻辑" in error for error in module.errors)

    with_fixture(run)
    print("[PASS] Runtime constraints fail-closed test passed")


def test_runtime_constraints_warns_bare_python_hook():
    def run(module, root):
        create_minimal_valid_tree(root)
        write(root / "settings.json", json.dumps({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "python bash-safety-wrapper.py"}]
                    },
                    {
                        "matcher": "Write|Edit",
                        "hooks": [{"type": "command", "command": "C:/Python/python.exe write-safety.py"}]
                    }
                ]
            }
        }))
        module.check_runtime_constraints()
        assert any("PATH 解析 Python" in error for error in module.errors)

    with_fixture(run)
    print("[PASS] Runtime constraints bare python hook test passed")


def test_runtime_constraints_detects_missing_python_interpreter():
    """If settings.json references a non-existent Python exe, healthcheck must error."""
    def run(module, root):
        create_minimal_valid_tree(root)
        write(root / "settings.json", json.dumps({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "C:/nonexistent/python.exe bash-safety-wrapper.py"}]
                    },
                    {
                        "matcher": "Write|Edit",
                        "hooks": [{"type": "command", "command": "C:/nonexistent/python.exe write-safety.py"}]
                    }
                ]
            }
        }))
        module.check_runtime_constraints()
        assert any("Hook Python 解释器" in e and "不存在" in e for e in module.errors), \
            f"Missing interpreter not caught. errors={module.errors}"

    with_fixture(run)
    print("[PASS] Runtime constraints missing interpreter test passed")


def test_runtime_constraints_detects_missing_registered_script():
    """If settings.json references a non-existent script path, healthcheck must error."""
    def run(module, root):
        create_minimal_valid_tree(root)
        write(root / "settings.json", json.dumps({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": f"{sys.executable} C:/nonexistent/bash-safety-wrapper.py"}]
                    },
                    {
                        "matcher": "Write|Edit",
                        "hooks": [{"type": "command", "command": f"{sys.executable} C:/nonexistent/write-safety.py"}]
                    }
                ]
            }
        }))
        module.check_runtime_constraints()
        assert any("Hook 脚本" in e and "不存在" in e for e in module.errors), \
            f"Missing script not caught. errors={module.errors}"

    with_fixture(run)
    print("[PASS] Runtime constraints missing script test passed")


def test_runtime_constraints_smoke_test_catches_faulty_wrapper():
    """If the registered wrapper script returns exit 0 (broken) for a dangerous command, healthcheck must error."""
    def run(module, root):
        create_minimal_valid_tree(root)
        # Overwrite the wrapper with a broken one that always returns 0
        faulty = root / "skills" / "util-safety" / "hooks" / "bash-safety-wrapper.py"
        write(faulty, "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
        write(root / "settings.json", json.dumps({
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": f"{sys.executable} {faulty}"}]
                    },
                    {
                        "matcher": "Write|Edit",
                        "hooks": [{"type": "command", "command": f"{sys.executable} {root / 'skills' / 'util-safety' / 'hooks' / 'write-safety.py'}"}]
                    }
                ]
            }
        }))
        module.check_runtime_constraints()
        # The faulty wrapper test should fail behavior verification
        assert any("行为验证失败" in e for e in module.errors), \
            f"Faulty wrapper not detected. errors={module.errors}"

    with_fixture(run)
    print("[PASS] Runtime constraints smoke test passed")


def test_release_version_consistency_detects_h1_version():
    """如果 CLAUDE.md 一级标题含版本号 vX.Y.Z，健康检查应报错（根除漂移）。"""
    def run(module, root):
        create_minimal_valid_tree(root)
        write(root / "CLAUDE.md", "# Work Specification v4.5.6\nSee skills/rules/rule.md\n")
        module._check_release_version_consistency()
        assert any("一级标题含版本号" in e for e in module.errors), \
            f"标题版本号未被检测。errors={module.errors}"
    with_fixture(run)
    print("[PASS] Release version consistency H1 version test passed")


def test_print_report_returns_error_code_for_errors():
    def run(module, root):
        module.errors.append("测试错误")
        output = io.StringIO()
        with redirect_stdout(output):
            code = module.print_report()
        assert code == 1
        assert "测试错误" in output.getvalue()

    with_fixture(run)
    print("[PASS] Report error code test passed")


def test_secret_scan_detects_sk_key():
    """v2.2.1: check_config_secret_leakage must detect sk-* style keys."""
    def run(module, root):
        create_minimal_valid_tree(root)
        # construct test secret via concatenation to avoid hardcoding a full literal
        fake_token = "sk-" + "X" * 30
        write(root / "settings.json", json.dumps({"env": {"AUTH": fake_token}}))
        module.check_config_secret_leakage()
        assert any("sk-" in w for w in module.warnings),             f"sk- secret not detected. warnings={module.warnings}"

    with_fixture(run)
    print("[PASS] Secret scan detects sk-* key")


def test_secret_scan_clean_config():
    """v2.2.1: a clean settings.json must produce no secret warnings."""
    def run(module, root):
        create_minimal_valid_tree(root)
        write(root / "settings.json", json.dumps({"model": "opus", "theme": "dark"}))
        write(root / "settings.local.json", json.dumps({"permissions": {"allow": []}}))
        # save baseline of warnings (other checks may pollute)
        baseline = list(module.warnings)
        module.check_config_secret_leakage()
        new = [w for w in module.warnings if w not in baseline]
        assert not new, f"Clean config produced false positives: {new}"

    with_fixture(run)
    print("[PASS] Secret scan clean config")


def main() -> int:
    if not SCRIPT_PATH.exists():
        print(f"Error: Health check script not found at {SCRIPT_PATH}", file=sys.stderr)
        return 1

    try:
        test_structure_integrity_passes()
        test_reference_consistency_detects_missing_rule()
        test_memory_path_warning_without_baseline()
        test_memory_path_baseline_suppresses_warning()
        test_dependency_cycle_detected()
        test_runtime_constraints_accepts_wrapper()
        test_runtime_constraints_requires_bash_wrapper()
        test_runtime_constraints_checks_wrapper_fail_closed()
        test_runtime_constraints_warns_bare_python_hook()
        test_runtime_constraints_detects_missing_python_interpreter()
        test_runtime_constraints_detects_missing_registered_script()
        test_runtime_constraints_smoke_test_catches_faulty_wrapper()
        test_secret_scan_detects_sk_key()
        test_secret_scan_clean_config()
        test_release_version_consistency_detects_h1_version()
        test_print_report_returns_error_code_for_errors()
        print("\n[OK] All skills-health-check.py tests passed!")
        return 0
    except AssertionError as exc:
        print(f"\n[FAIL] Test failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\n[FAIL] Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
