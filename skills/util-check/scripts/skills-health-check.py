#!/usr/bin/env python3
"""Claude Code Skills 系统健康检查器

只读检查器，验证 Skills 系统的结构完整性、引用一致性和强约束运行时配置。
不修改任何文件，只输出结构化报告。
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# 配置路径
CLAUDE_DIR = Path.home() / ".claude"
SKILLS_DIR = CLAUDE_DIR / "skills"
RULES_DIR = SKILLS_DIR / "rules"
SAFETY_SKILL_DIR = SKILLS_DIR / "util-safety"
SAFETY_HOOKS_DIR = SAFETY_SKILL_DIR / "hooks"
SAFETY_TESTS_DIR = SAFETY_SKILL_DIR / "tests"
CHECK_SKILL_DIR = SKILLS_DIR / "util-check"
CHECK_SCRIPTS_DIR = CHECK_SKILL_DIR / "scripts"
CHECK_TESTS_DIR = CHECK_SKILL_DIR / "tests"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
SETTINGS_LOCAL_FILE = CLAUDE_DIR / "settings.local.json"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"
README_MD = SKILLS_DIR / "README.md"
CHANGELOG_MD = CLAUDE_DIR / "CHANGELOG.md"

TEST_SCRIPTS = [
    SAFETY_TESTS_DIR / "test_bash_safety.py",
    SAFETY_TESTS_DIR / "test_write_safety.py",
    SAFETY_TESTS_DIR / "test_rule_loader.py",
    SAFETY_TESTS_DIR / "test_rule_loader_ttl.py",
    SAFETY_TESTS_DIR / "test_session_start.py",
    SAFETY_TESTS_DIR / "test_mcp_safety.py",
    SAFETY_TESTS_DIR / "test_mcp_audit.py",
    SAFETY_TESTS_DIR / "test_bash_audit_post.py",
    SAFETY_TESTS_DIR / "test_audit_log_concurrency.py",
    SAFETY_TESTS_DIR / "test_mcp_grant_concurrency.py",
    SAFETY_TESTS_DIR / "test_mcp_risk_level.py",
    SAFETY_TESTS_DIR / "test_mcp_three_tier.py",
    SAFETY_TESTS_DIR / "test_pattern_performance.py",
    SAFETY_TESTS_DIR / "test_security_functions_positive.py",
    CHECK_TESTS_DIR / "test_skills_health_check.py",
]

# v2.2.0: frontmatter version check targets
VERSIONED_FILES = []  # populated lazily inside check_versioning()
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 检查结果
passed: List[str] = []
warnings: List[str] = []
errors: List[str] = []
stats: Dict[str, int] = {}


def check_structure_integrity() -> None:
    """检查 1: 结构完整性"""
    _err0 = len(errors)
    skill_dirs = [d for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name != "rules"]
    stats["skill_count"] = len(skill_dirs)
    stats["dev_count"] = sum(1 for d in skill_dirs if d.name.startswith("dev-"))
    stats["util_count"] = sum(1 for d in skill_dirs if d.name.startswith("util-"))

    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"

        # 检查 SKILL.md 存在
        if not skill_md.exists():
            errors.append(f"`{skill_dir.name}/SKILL.md` 不存在")
            continue

        # 检查 frontmatter
        content = skill_md.read_text(encoding="utf-8")
        if not content.startswith("---"):
            errors.append(f"`{skill_dir.name}/SKILL.md` 缺少 frontmatter")
            continue

        try:
            fm_end = content.index("---", 3)
            fm_text = content[3:fm_end]
        except ValueError:
            errors.append(f"`{skill_dir.name}/SKILL.md` frontmatter 格式错误")
            continue

        # 检查必需字段
        if "name:" not in fm_text:
            errors.append(f"`{skill_dir.name}/SKILL.md` frontmatter 缺少 name 字段")
        elif f"name: {skill_dir.name}" not in fm_text:
            errors.append(f"`{skill_dir.name}/SKILL.md` frontmatter name 与目录名不一致")

        if "description:" not in fm_text:
            errors.append(f"`{skill_dir.name}/SKILL.md` frontmatter 缺少 description 字段")

        if "user-invocable:" not in fm_text:
            errors.append(f"`{skill_dir.name}/SKILL.md` frontmatter 缺少 user-invocable 字段")

        # 检查命名前缀
        if not (skill_dir.name.startswith("dev-") or skill_dir.name.startswith("util-")):
            errors.append(f"`{skill_dir.name}` 命名不符合 dev-/util- 前缀规范")

    if len(errors) == _err0:
        passed.append("结构完整性：全部通过")


def check_reference_consistency() -> None:
    """检查 2: 引用一致性"""
    _err0 = len(errors)
    # 检查 CLAUDE.md 引用的 rules 文件
    if CLAUDE_MD.exists():
        claude_content = CLAUDE_MD.read_text(encoding="utf-8")
        rule_refs = re.findall(r"skills/rules/([\w-]+\.md)", claude_content)

        for rule_file in rule_refs:
            if not (RULES_DIR / rule_file).exists():
                errors.append(f"CLAUDE.md 引用了 `skills/rules/{rule_file}`，但文件不存在")

    # 检查 Skill 间交叉引用
    skill_dirs = [d for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name != "rules"]
    skill_names = {d.name for d in skill_dirs}

    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        content = skill_md.read_text(encoding="utf-8")

        # 查找 /dev-* 和 /util-* 引用
        refs = re.findall(r"/(?:dev-|util-)([\w-]+)", content)
        for ref in refs:
            full_name = f"dev-{ref}" if f"dev-{ref}" in skill_names else f"util-{ref}"
            if full_name not in skill_names:
                errors.append(f"`{skill_dir.name}` 引用了 `/{full_name}`，但 Skill 不存在")

        # 检查 depends-on 字段
        if "depends-on:" in content:
            try:
                fm_end = content.index("---", 3)
                fm_text = content[3:fm_end]
                # 简单提取 depends-on 数组
                deps_match = re.search(r"depends-on:\s*\[(.*?)\]", fm_text, re.DOTALL)
                if deps_match:
                    deps_text = deps_match.group(1)
                    deps = [d.strip().strip('"\'') for d in deps_text.split(",") if d.strip()]
                    for dep in deps:
                        if dep and dep not in skill_names:
                            errors.append(f"`{skill_dir.name}` depends-on 引用了 `{dep}`，但 Skill 不存在")
            except (ValueError, AttributeError):
                pass

    if len(errors) == _err0:
        passed.append("引用一致性：全部通过")


def check_compliance() -> None:
    """检查 3: 规范合规性"""
    _warn0 = len(warnings)
    _err0 = len(errors)
    # 检查 CLAUDE.md 行数
    if CLAUDE_MD.exists():
        lines = len(CLAUDE_MD.read_text(encoding="utf-8").splitlines())
        stats["claude_md_lines"] = lines
        if lines > 100:
            warnings.append(f"`CLAUDE.md` 当前 {lines} 行，超过 100 行建议上限")

    # 检查 rules 文件被引用
    if RULES_DIR.exists():
        rule_files = list(RULES_DIR.glob("*.md"))
        stats["rules_count"] = len(rule_files)

        # 收集所有引用
        all_refs: Set[str] = set()

        if CLAUDE_MD.exists():
            claude_content = CLAUDE_MD.read_text(encoding="utf-8")
            all_refs.update(re.findall(r"skills/rules/([\w-]+\.md)", claude_content))

        skill_dirs = [d for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name != "rules"]
        for skill_dir in skill_dirs:
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
                all_refs.update(re.findall(r"skills/rules/([\w-]+\.md)", content))

        # rule-loader.py 按场景动态注入规则（如 git-safety.md / skill-org.md），
        # 用纯文件名字面量引用，不走
        # "skills/rules/xxx.md" 路径模式。扫描其注入语句，避免把这些动态加载
        # 的规则误报为"未被引用"。
        rule_loader = SAFETY_HOOKS_DIR / "rule-loader.py"
        if rule_loader.exists():
            loader_content = rule_loader.read_text(encoding="utf-8")
            all_refs.update(re.findall(r'"([\w-]+\.md)"', loader_content))

        for rule_file in rule_files:
            if rule_file.name not in all_refs:
                warnings.append(f"`skills/rules/{rule_file.name}` 未被任何文件引用")

    if len(warnings) == _warn0 and len(errors) == _err0:
        passed.append("规范合规性：全部通过")


def check_memory_path_consistency() -> None:
    """检查 4: Memory 路径一致性"""
    warning_count = len(warnings)
    all_md_files = list(SKILLS_DIR.glob("**/*.md"))

    for md_file in all_md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        # 查找 memory/ 引用（在反引号内）
        memory_refs = re.findall(r"`(memory/[^`]+)`", content)

        if memory_refs:
            # 检查是否有路径基准说明
            has_baseline = bool(re.search(
                r"(?:路径说明|memory 路径|系统提供的.*memory.*路径|memory/.*均指)",
                content,
                re.IGNORECASE
            ))

            if not has_baseline:
                # 检查是否所有引用都是完整路径（包含盘符或 ~）
                incomplete_refs = [
                    ref for ref in memory_refs
                    if not (ref.startswith("C:/") or ref.startswith("~/") or ref.startswith("/"))
                ]

                if incomplete_refs:
                    rel_path = md_file.relative_to(SKILLS_DIR)
                    warnings.append(
                        f"`{rel_path}` 包含 memory/ 引用但缺少路径基准说明"
                    )

    if len(warnings) == warning_count and not errors:
        passed.append("Memory 路径一致性：全部通过")


def check_dependency_graph() -> None:
    """检查 5: 依赖关系"""
    _err0 = len(errors)
    skill_dirs = [d for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name != "rules"]
    deps_graph: Dict[str, List[str]] = {}

    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        content = skill_md.read_text(encoding="utf-8")
        deps_graph[skill_dir.name] = []

        if "depends-on:" in content:
            try:
                fm_end = content.index("---", 3)
                fm_text = content[3:fm_end]
                deps_match = re.search(r"depends-on:\s*\[(.*?)\]", fm_text, re.DOTALL)
                if deps_match:
                    deps_text = deps_match.group(1)
                    deps = [d.strip().strip('"\'') for d in deps_text.split(",") if d.strip()]
                    deps_graph[skill_dir.name] = [d for d in deps if d]
            except (ValueError, AttributeError):
                pass

    # 检测循环依赖
    def has_cycle(node: str, visited: Set[str], rec_stack: Set[str]) -> Tuple[bool, List[str]]:
        visited.add(node)
        rec_stack.add(node)

        for neighbor in deps_graph.get(node, []):
            if neighbor not in visited:
                has_c, path = has_cycle(neighbor, visited, rec_stack)
                if has_c:
                    return True, [node] + path
            elif neighbor in rec_stack:
                return True, [node, neighbor]

        rec_stack.remove(node)
        return False, []

    visited: Set[str] = set()
    cycle_found = False
    for skill in deps_graph:
        if skill not in visited:
            cycle, path = has_cycle(skill, visited, set())
            if cycle:
                cycle_found = True
                cycle_str = " → ".join(path)
                errors.append(f"检测到循环依赖：{cycle_str}")

    if cycle_found:
        stats["max_dependency_chain"] = 0
        return

    # 计算最长依赖链
    def max_depth(node: str, memo: Dict[str, int]) -> int:
        if node in memo:
            return memo[node]

        if not deps_graph.get(node):
            memo[node] = 0
            return 0

        max_d = max((max_depth(dep, memo) for dep in deps_graph[node]), default=0) + 1
        memo[node] = max_d
        return max_d

    memo: Dict[str, int] = {}
    max_chain = 0
    deepest_skill = ""

    for skill in deps_graph:
        depth = max_depth(skill, memo)
        if depth > max_chain:
            max_chain = depth
            deepest_skill = skill

    stats["max_dependency_chain"] = max_chain

    if max_chain > 3:
        warnings.append(f"`{deepest_skill}` 的调用链深度为 {max_chain} 层，超过 3 层建议上限")
    elif max_chain == 3:
        warnings.append(f"`{deepest_skill}` 的调用链深度为 {max_chain} 层，接近 3 层上限")

    if len(errors) == _err0 and max_chain <= 2:
        passed.append("依赖关系：全部通过")


def _subprocess_creationflags():
    """获取 subprocess 创建标志（跨平台兼容）。"""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _sanitized_hook_env() -> dict:
    """构造干净 env，剥离所有 CLAUDE_HOOK_APPROVED_* 逃生舱变量，
    确保行为验证测的是 hook 真实拦截能力，而不是父进程的临时授权。
    同时设置 CLAUDE_HEALTH_CHECK_CONTEXT=1 防止 session-start 递归 spawn。"""
    import os
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE_HOOK_APPROVED_")}
    env["CLAUDE_HEALTH_CHECK_CONTEXT"] = "1"
    return env


def _load_merged_settings() -> dict:
    """加载合并后的 settings，local 覆盖 global，hooks 做数组级合并。"""
    merged: dict = {}

    for path in (SETTINGS_FILE, SETTINGS_LOCAL_FILE):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for key, value in data.items():
            if key == "hooks" and isinstance(value, dict):
                existing = merged.setdefault("hooks", {})
                for event, entries in value.items():
                    if not isinstance(entries, list):
                        continue
                    existing.setdefault(event, [])
                    existing[event].extend(entries)
            else:
                merged[key] = value

    return merged


def run_hook_behavior(script: Path, payload: dict, expected_text: str) -> bool:
    """用合成 hook 输入验证脚本会阻断高风险操作。"""
    return run_hook_behavior_exit(script, payload, 2, expected_text)


def run_hook_behavior_exit(script: Path, payload: dict, expected_exit: int, expected_text: str = "", grants: list | None = None) -> bool:
    """用合成 hook 输入验证脚本返回预期退出码。

    grants: 类别列表；会在隔离临时目录创建 .grants/<category> 一次性文件，
    并通过 CLAUDE_TEST_GRANTS_DIR 注入，用于验证带外授权放行（信任根改造后）。
    """
    import tempfile
    env = _sanitized_hook_env()
    _tmp = None
    try:
        if grants is not None:
            _tmp = tempfile.TemporaryDirectory()
            gdir = Path(_tmp.name)
            for cat in grants:
                (gdir / cat).write_text("", encoding="utf-8")
            env["CLAUDE_TEST_GRANTS_DIR"] = str(gdir)
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(payload).encode(),
                capture_output=True,
                timeout=5,
                creationflags=_subprocess_creationflags(),
                env=env,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"`{script.relative_to(CLAUDE_DIR)}` 行为验证超时")
            return False
        except Exception as exc:
            errors.append(f"`{script.relative_to(CLAUDE_DIR)}` 行为验证失败：{exc}")
            return False
    finally:
        if _tmp is not None:
            _tmp.cleanup()

    stderr = result.stderr.decode("utf-8", errors="replace").lower()
    if result.returncode != expected_exit or (expected_text and expected_text.lower() not in stderr):
        errors.append(f"`{script.relative_to(CLAUDE_DIR)}` 行为验证失败：期望 exit={expected_exit}，实际 exit={result.returncode}")
        return False

    return True


def run_bash_hook_ask(script: Path, payload: dict, expected_text: str) -> bool:
    """危险 Bash 命令无带外授权时应返回 ask 决策，而不是直接 exit 2。"""
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=5,
            creationflags=_subprocess_creationflags(),
            env=_sanitized_hook_env(),
        )
    except subprocess.TimeoutExpired:
        errors.append(f"`{script.relative_to(CLAUDE_DIR)}` ask 行为验证超时")
        return False
    except Exception as exc:
        errors.append(f"`{script.relative_to(CLAUDE_DIR)}` ask 行为验证失败：{exc}")
        return False

    if result.returncode != 0:
        errors.append(f"`{script.relative_to(CLAUDE_DIR)}` ask 行为验证失败：期望 exit=0，实际 exit={result.returncode}")
        return False
    try:
        output = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        errors.append(f"`{script.relative_to(CLAUDE_DIR)}` ask 行为验证失败：stdout 不是 JSON")
        return False

    hook_output = output.get("hookSpecificOutput", {})
    reason = str(hook_output.get("permissionDecisionReason", ""))
    if hook_output.get("permissionDecision") != "ask" or not reason:
        errors.append(f"`{script.relative_to(CLAUDE_DIR)}` ask 行为验证失败：缺少 ask 决策或原因文案")
        return False
    return True


def check_runtime_constraints() -> None:
    """检查 6: 强约束运行时"""
    _warn0 = len(warnings)
    _err0 = len(errors)
    # 检查 Bash 安全 hook
    if not SETTINGS_FILE.exists() and not SETTINGS_LOCAL_FILE.exists():
        errors.append("`settings.json` 和 `settings.local.json` 均不存在")
        return

    settings = _load_merged_settings()
    hooks = settings.get("hooks", {})
    pre_tool_use = hooks.get("PreToolUse", [])

    bash_hook_found = False
    write_edit_hook_found = False
    mcp_hook_found = False
    for hook_group in pre_tool_use:
        matcher = hook_group.get("matcher", "")
        hook_cmds = hook_group.get("hooks", [])

        for hook_cmd in hook_cmds:
            command = hook_cmd.get("command", "").strip()
            if re.match(r"^(?:python|py)(?:\s|$)", command, re.IGNORECASE):
                errors.append("Hook 命令使用 PATH 解析 Python，建议改为 Python 绝对路径")

            # Check 1: Python 解释器可定位
            # 命令可能写成 `%USERPROFILE%/.claude/hook-runner.cmd <脚本>`（Windows）
            # 或 `$HOME/.claude/hook-runner.sh <脚本>`（Unix）等形式，
            # os.path.exists 不会自动展开 %VAR%/$VAR，需先展开再校验。
            try:
                tokens = shlex.split(command, posix=False)
            except ValueError:
                tokens = command.split()
            if tokens:
                interpreter = tokens[0].strip('"').strip("'")
                if ("/" in interpreter or "\\" in interpreter or interpreter.lower().endswith(".exe")):
                    expanded = os.path.expandvars(interpreter)
                    if not Path(expanded).exists():
                        errors.append(f"Hook Python 解释器 `{interpreter}` 不存在")

            # Check 2: 注册的 hook 脚本存在且符合 Claude Code schema
            # tokens[1] 是脚本名：hook-runner.py 在运行时会把纯文件名拼到
            # skills/util-safety/hooks/ 下（见 hook-runner.py L76）。
            # 校验必须复刻该调度，否则把纯文件名当相对路径查 cwd 必然误报。
            if len(tokens) >= 2:
                script_token = tokens[1].strip('"').strip("'")
                has_sep = ("\/" if os.name == "nt" else "/")  # 路径分隔符集合
                if any(ch in has_sep for ch in script_token) or ":" in script_token:
                    # 已带路径（绝对或相对）——按字面（展开环境变量后）校验
                    script_p = Path(os.path.expandvars(script_token))
                else:
                    # 纯文件名 —— 复刻 hook-runner.py 调度，拼到 hooks 目录
                    script_p = SAFETY_HOOKS_DIR / script_token
                if not script_p.exists():
                    errors.append(f"Hook 脚本 `{script_token}` 不存在（settings.json 注册路径无效）")
                elif script_p.name == "rule-loader.py":
                    # rule-loader is intentionally non-blocking (advisory only); skip block-smoke
                    pass
                elif "Bash" in matcher:
                    smoke_payload = {
                        "tool_name": "Bash",
                        "tool_input": {"command": "git commit -m smoke"},
                    }
                    # 失败已在函数内 append 到 errors
                    run_bash_hook_ask(script_p, smoke_payload, "git 提交")
                elif "Write" in matcher and "Edit" in matcher:
                    smoke_payload = {
                        "tool_name": "Write",
                        "tool_input": {"file_path": str(Path.home() / "project" / ".env")},
                    }
                    run_hook_behavior(script_p, smoke_payload, "拦截")
                elif matcher == "mcp__.*" and script_p.name == "mcp-safety.py":
                    secret_payload = {
                        "tool_name": "mcp__server__tool",
                        "tool_input": {"token": "sk-" + "A" * 24},
                    }
                    run_hook_behavior(script_p, secret_payload, "凭据")
                    normal_payload = {
                        "tool_name": "mcp__server__tool",
                        "tool_input": {"query": "hello"},
                    }
                    run_hook_behavior_exit(script_p, normal_payload, 0)
                    blocklist_payload = {
                        "tool_name": "mcp__github__force_push",
                        "tool_input": {"branch": "main"},
                    }
                    run_hook_behavior(script_p, blocklist_payload, "黑名单")
                elif matcher == "mcp__.*" and script_p.name == "mcp-audit.py":
                    audit_payload = {
                        "tool_name": "mcp__server__tool",
                        "tool_input": {"query": "hello"},
                    }
                    run_hook_behavior_exit(script_p, audit_payload, 0)

        if "Bash" in matcher:
            bash_hook_found = True
            if not any("bash-safety-wrapper" in h.get("command", "") for h in hook_cmds):
                errors.append("Bash hook 配置必须调用 bash-safety-wrapper 脚本")

        if "Write" in matcher and "Edit" in matcher:
            write_edit_hook_found = True
            if not any("write-safety" in h.get("command", "") for h in hook_cmds):
                errors.append("Write/Edit hook 配置存在但未调用 write-safety 脚本")

        if matcher == "mcp__.*":
            mcp_hook_found = True
            if not any("mcp-safety" in h.get("command", "") for h in hook_cmds):
                errors.append("MCP hook 配置存在但未调用 mcp-safety 脚本")
            if not any("mcp-audit" in h.get("command", "") for h in hook_cmds):
                errors.append("MCP hook 配置存在但未调用 mcp-audit 脚本")

    if not bash_hook_found:
        errors.append("未配置 Bash PreToolUse hook（检查 settings.json 和 settings.local.json）")
    if not write_edit_hook_found:
        errors.append("未配置 Write/Edit PreToolUse hook（检查 settings.json 和 settings.local.json）")
    if not mcp_hook_found:
        errors.append("未配置 MCP PreToolUse hook（检查 settings.json 和 settings.local.json）")



    # 检查 hook 脚本存在
    bash_wrapper = SAFETY_HOOKS_DIR / "bash-safety-wrapper.py"
    if not bash_wrapper.exists():
        errors.append("`skills/util-safety/hooks/bash-safety-wrapper.py` 不存在")
    else:
        wrapper_content = bash_wrapper.read_text(encoding="utf-8")
        if "为安全起见阻断命令（fail-closed）" not in wrapper_content:
            errors.append("`skills/util-safety/hooks/bash-safety-wrapper.py` 缺少 fail-closed 阻断文案")
        if "return 2" not in wrapper_content:
            errors.append("`skills/util-safety/hooks/bash-safety-wrapper.py` 缺少异常阻断逻辑")
        # v2.2.0: wrapper now uses concurrent.futures internal timeout instead of subprocess.TimeoutExpired
        if "TimeoutError" not in wrapper_content and "TimeoutExpired" not in wrapper_content:
            errors.append("`skills/util-safety/hooks/bash-safety-wrapper.py` 缺少超时处理逻辑")
        # v2.2.0: wrapper should be self-contained (merged with bash-safety.py)
        if "DANGEROUS_PATTERNS" not in wrapper_content:
            errors.append("`skills/util-safety/hooks/bash-safety-wrapper.py` 未合并 bash-safety 核心逻辑（缺 DANGEROUS_PATTERNS）")
        run_bash_hook_ask(
            bash_wrapper,
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'test'"}},
            "git 提交",
        )
        run_bash_hook_ask(
            bash_wrapper,
            {"tool_name": "Bash", "tool_input": {"command": 'bash -c "echo"'}},
            "bash/sh -c 子壳",
        )
        run_bash_hook_ask(
            bash_wrapper,
            {"tool_name": "Bash", "tool_input": {"command": 'CLAUDE_HOOK_APPROVED_SUBSHELL=1 bash -c "echo"'}},
            "bash/sh -c 子壳",
        )
        # 计划2 带外授权放行冒烟（.grants 文件）：subshell + git
        run_hook_behavior_exit(
            bash_wrapper,
            {"tool_name": "Bash", "tool_input": {"command": 'bash -c "echo"'}},
            0,
            grants=["subshell"],
        )
        run_hook_behavior_exit(
            bash_wrapper,
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m test"}},
            0,
            grants=["git"],
        )
        # 计划2 命令归一化拦截冒烟：路径前缀变体
        run_bash_hook_ask(
            bash_wrapper,
            {"tool_name": "Bash", "tool_input": {"command": "/bin/rm -rf x"}},
            "递归强制删除",
        )

    mcp_safety = SAFETY_HOOKS_DIR / "mcp-safety.py"
    if not mcp_safety.exists():
        errors.append("`skills/util-safety/hooks/mcp-safety.py` 不存在")
    else:
        mcp_content = mcp_safety.read_text(encoding="utf-8")
        if "TIMEOUT_SECONDS" not in mcp_content:
            errors.append("`skills/util-safety/hooks/mcp-safety.py` 缺少 TIMEOUT_SECONDS 常量")
        if "fail-closed" not in mcp_content.lower():
            errors.append("`skills/util-safety/hooks/mcp-safety.py` 缺少 fail-closed 阻断文案")
        if "CLAUDE_HOOK_APPROVED_MCP" not in mcp_content:
            errors.append("`skills/util-safety/hooks/mcp-safety.py` 缺少 MCP 授权 marker")
        run_hook_behavior(
            mcp_safety,
            {"tool_name": "mcp__server__tool", "tool_input": {"token": "sk-" + "A" * 24}},
            "凭据",
        )
        run_hook_behavior_exit(
            mcp_safety,
            {"tool_name": "mcp__server__tool", "tool_input": {"query": "hello"}},
            0,
        )
        run_hook_behavior(
            mcp_safety,
            {"tool_name": "mcp__github__force_push", "tool_input": {"branch": "main"}},
            "黑名单",
        )

    mcp_audit = SAFETY_HOOKS_DIR / "mcp-audit.py"
    if not mcp_audit.exists():
        errors.append("`skills/util-safety/hooks/mcp-audit.py` 不存在")
    else:
        run_hook_behavior_exit(
            mcp_audit,
            {"tool_name": "mcp__server__tool", "tool_input": {"query": "hello"}},
            0,
        )

    bash_audit_post = SAFETY_HOOKS_DIR / "bash-audit-post.py"
    if not bash_audit_post.exists():
        errors.append("`skills/util-safety/hooks/bash-audit-post.py` 不存在")
    else:
        run_hook_behavior_exit(
            bash_audit_post,
            {"tool_name": "Bash", "tool_input": {"command": "not dangerous"}},
            0,
        )
        try:
            result = subprocess.run(
                [sys.executable, str(bash_audit_post)],
                input=b"not json",
                capture_output=True,
                timeout=10,
                creationflags=_subprocess_creationflags(),
                env=_sanitized_hook_env(),
            )
            if result.returncode != 0:
                errors.append(f"`bash-audit-post.py` 必须 fail-safe（malformed 输入返回 {result.returncode}）")
        except Exception as exc:
            errors.append(f"`bash-audit-post.py` malformed 测试异常：{exc}")

    write_safety = SAFETY_HOOKS_DIR / "write-safety.py"
    if not write_safety.exists():
        errors.append("`skills/util-safety/hooks/write-safety.py` 不存在")
    else:
        # Plan-3 3.7: write-safety 也应满足 fail-closed + 内部超时（HOOK-CONVENTIONS.md）
        ws_content = write_safety.read_text(encoding="utf-8")
        if "TIMEOUT_SECONDS" not in ws_content:
            errors.append("`skills/util-safety/hooks/write-safety.py` 缺少 TIMEOUT_SECONDS 常量（Plan-3 3.7 要求）")
        if "fail-closed" not in ws_content.lower():
            errors.append("`skills/util-safety/hooks/write-safety.py` 缺少 fail-closed 阻断文案")
        if "TimeoutError" not in ws_content:
            errors.append("`skills/util-safety/hooks/write-safety.py` 缺少超时处理逻辑")
        run_hook_behavior(
            write_safety,
            {"tool_name": "Write", "tool_input": {"file_path": str(CLAUDE_DIR / "project" / ".env")}},
            "拦截",
        )

    # 检查工具 fallback 说明
    fallback_skills = ["util-check", "util-memory", "util-init", "util-session"]
    for skill_name in fallback_skills:
        skill_md = SKILLS_DIR / skill_name / "SKILL.md"
        if skill_md.exists():
            content = skill_md.read_text(encoding="utf-8")
            if "Glob" in content or "Grep" in content:
                if "工具失败兜底" not in content and "fallback" not in content.lower():
                    warnings.append(f"`{skill_name}` 使用 Glob/Grep 但未说明 fallback 策略")

    if len(errors) == _err0 and len(warnings) == _warn0:
        passed.append("强约束运行时：全部通过")


def print_report() -> int:
    """输出报告并返回退出码"""
    import sys

    # 强制使用 UTF-8 输出（测试重定向 stdout 时可能没有 reconfigure）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding='utf-8')

    print("Skills 系统健康检查报告\n", flush=True)

    if passed:
        print(f"### [PASS] 通过 ({len(passed)} 项)", flush=True)
        for item in passed:
            print(f"- {item}", flush=True)
        print(flush=True)

    if warnings:
        print(f"### [WARN] 警告 ({len(warnings)} 项)", flush=True)
        for item in warnings:
            print(f"- {item}", flush=True)
        print(flush=True)

    if errors:
        print(f"### [ERROR] 错误 ({len(errors)} 项)", flush=True)
        for item in errors:
            print(f"- {item}", flush=True)
        print(flush=True)

    print("### [STATS] 统计", flush=True)
    print(f"- Skill 总数: {stats.get('skill_count', 0)} 个 (dev: {stats.get('dev_count', 0)}, util: {stats.get('util_count', 0)})", flush=True)
    print(f"- Rules 文件: {stats.get('rules_count', 0)} 个", flush=True)
    print(f"- CLAUDE.md 行数: {stats.get('claude_md_lines', 0)} 行", flush=True)
    print(f"- 最长依赖链: {stats.get('max_dependency_chain', 0)} 层", flush=True)
    print(f"- 循环依赖: {sum(1 for e in errors if '循环依赖' in e)} 个", flush=True)

    return 1 if errors else 0


def run_tests() -> int:
    """运行核心回归测试脚本"""
    for script in TEST_SCRIPTS:
        if not script.exists():
            print(f"错误：测试脚本不存在：{script}", file=sys.stderr)
            return 1

        result = subprocess.run(
            [sys.executable, str(script)],
            creationflags=_subprocess_creationflags(),
        )
        if result.returncode != 0:
            return result.returncode

    return 0


def _extract_frontmatter(content: str) -> dict:
    """Parse YAML-like frontmatter from a markdown file (simple key: value pairs only)."""
    if not content.startswith("---"):
        return {}
    try:
        end = content.index("\n---", 3)
    except ValueError:
        return {}
    fm_text = content[3:end]
    result = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()
    return result


def check_versioning() -> None:
    """检查 7: frontmatter version / last-updated 字段（v2.2.0 引入）"""
    targets: List[Path] = []
    targets.extend(sorted(RULES_DIR.glob("*.md")))
    for skill_dir in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir() and p.name != "rules"):
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            targets.append(skill_md)
    if CLAUDE_MD.exists():
        targets.append(CLAUDE_MD)

    issues = 0
    for path in targets:
        rel = path.relative_to(CLAUDE_DIR)
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            errors.append(f"`{rel}` 无法读取：{exc}")
            issues += 1
            continue

        fm = _extract_frontmatter(content)
        if "version" not in fm:
            errors.append(f"`{rel}` 缺少 frontmatter `version` 字段")
            issues += 1
        elif not SEMVER_RE.match(fm["version"]):
            errors.append(f"`{rel}` `version` 不符合 semver：{fm['version']}")
            issues += 1

        if "last-updated" not in fm:
            errors.append(f"`{rel}` 缺少 frontmatter `last-updated` 字段")
            issues += 1
        elif not DATE_RE.match(fm["last-updated"]):
            errors.append(f"`{rel}` `last-updated` 格式应为 YYYY-MM-DD：{fm['last-updated']}")
            issues += 1

    if issues == 0:
        passed.append("版本号与日期：全部通过")
    stats["versioned_files"] = len(targets)

    _check_release_version_consistency()


_CHANGELOG_VERSION_RE = re.compile(r"^##\s*\[(\d+\.\d+\.\d+)\]")


def _check_release_version_consistency() -> None:
    """整套规范的发布版本号必须单一来源：CHANGELOG 顶部版本号为 SSOT，
    CLAUDE.md 的 frontmatter version 必须与之一致。

    根治历史反复出现的版本漂移（frontmatter / 正文标题 / README 三处各说各话）。
    各 rules/SKILL.md 文件保留自己独立的语义化版本，不在此检查范围内。
    """
    if not CHANGELOG_MD.exists():
        return
    try:
        changelog_text = CHANGELOG_MD.read_text(encoding="utf-8")
    except Exception as exc:
        errors.append(f"`CHANGELOG.md` 无法读取：{exc}")
        return

    ssot = None
    for line in changelog_text.splitlines():
        m = _CHANGELOG_VERSION_RE.match(line.strip())
        if m:
            ssot = m.group(1)
            break
    if ssot is None:
        errors.append("`CHANGELOG.md` 未找到 `## [x.y.z]` 版本条目，无法校验发布版本一致性")
        return

    mismatches = []
    for path in (CLAUDE_MD,):
        if not path.exists():
            continue
        rel = path.relative_to(CLAUDE_DIR)
        try:
            fm = _extract_frontmatter(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"`{rel}` 无法读取：{exc}")
            continue
        ver = fm.get("version")
        if ver != ssot:
            mismatches.append(f"`{rel}` version={ver}（应为 {ssot}）")

    if mismatches:
        errors.append(
            "发布版本号漂移（CHANGELOG 顶版 "
            f"{ssot} 为准）：" + "；".join(mismatches)
        )

    # 正文一级标题不得含版本号：版本只在 frontmatter 一处，防止漂移复发
    _H1_VERSION_PATTERN = re.compile(r"^#\s+.*\bv?\d+\.\d+\.\d+\b")
    for path in (CLAUDE_MD,):
        if not path.exists():
            continue
        rel = path.relative_to(CLAUDE_DIR)
        try:
            body = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for line in body.splitlines():
            if _H1_VERSION_PATTERN.match(line.strip()):
                errors.append(f"`{rel}` 一级标题含版本号（`{line.strip()[:60]}`），应去掉")
                break

    if not errors:
        passed.append(f"发布版本一致性：CLAUDE.md 均为 {ssot}")


def check_new_hook_registrations() -> None:
    """检查 8: settings.json 注册 v2.2.0 新增 hook 入口"""
    if not SETTINGS_FILE.exists() and not SETTINGS_LOCAL_FILE.exists():
        return

    config = _load_merged_settings()

    hooks = config.get("hooks") or {}
    issues = 0

    def has_command_matching(event: str, substr: str) -> bool:
        for group in hooks.get(event) or []:
            for h in group.get("hooks") or []:
                cmd = h.get("command") or ""
                if substr in cmd:
                    return True
        return False

    # rule-loader on PreToolUse Bash / Write|Edit, plus UserPromptSubmit
    expectations = [
        ("PreToolUse", "rule-loader.py", "rule-loader 未在 PreToolUse 注册"),
        ("UserPromptSubmit", "rule-loader.py", "rule-loader 未在 UserPromptSubmit 注册"),
        ("SessionStart", "session-start.py", "session-start 未在 SessionStart 注册"),
        ("PostToolUse", "bash-audit-post.py", "bash-audit-post 未在 PostToolUse 注册"),
    ]
    for event, substr, msg in expectations:
        if not has_command_matching(event, substr):
            errors.append(f"settings 中 {msg}")
            issues += 1

    if issues == 0:
        passed.append("新 hook 注册：全部通过")


def check_new_hook_behaviors() -> None:
    """检查 9: rule-loader 和 session-start 行为级冒烟"""
    issues = 0

    rule_loader = SAFETY_HOOKS_DIR / "rule-loader.py"
    if not rule_loader.exists():
        errors.append("`skills/util-safety/hooks/rule-loader.py` 不存在")
        return

    # rule-loader: git command should inject git-safety.md
    try:
        result = subprocess.run(
            [sys.executable, str(rule_loader)],
            input=json.dumps({
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git commit -m test"}
            }).encode("utf-8"),
            capture_output=True,
            timeout=10,
            creationflags=_subprocess_creationflags(),
            env=_sanitized_hook_env(),
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        if result.returncode != 0:
            errors.append(f"`rule-loader.py` 不应阻断（exit={result.returncode}）")
            issues += 1
        elif "git-safety" not in stdout.lower():
            errors.append("`rule-loader.py` 行为验证失败：git 命令未注入 git-safety")
            issues += 1
    except Exception as exc:
        errors.append(f"`rule-loader.py` 行为验证异常：{exc}")
        issues += 1

    # rule-loader: never blocks on malformed input
    try:
        result = subprocess.run(
            [sys.executable, str(rule_loader)],
            input=b"not json",
            capture_output=True,
            timeout=10,
            creationflags=_subprocess_creationflags(),
            env=_sanitized_hook_env(),
        )
        if result.returncode != 0:
            errors.append(f"`rule-loader.py` 必须 fail-open（malformed 输入返回 {result.returncode}）")
            issues += 1
    except Exception as exc:
        errors.append(f"`rule-loader.py` malformed 测试异常：{exc}")
        issues += 1

    session_start = SAFETY_HOOKS_DIR / "session-start.py"
    if not session_start.exists():
        errors.append("`skills/util-safety/hooks/session-start.py` 不存在")
        return

    # session-start: returns quickly with no errors in healthy state
    try:
        result = subprocess.run(
            [sys.executable, str(session_start)],
            input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}).encode("utf-8"),
            capture_output=True,
            timeout=10,
            creationflags=_subprocess_creationflags(),
            env=_sanitized_hook_env(),
        )
        if result.returncode != 0:
            errors.append(f"`session-start.py` 不应阻断（exit={result.returncode}）")
            issues += 1
    except subprocess.TimeoutExpired:
        errors.append("`session-start.py` 同步段超过 10s（应 < 3s）")
        issues += 1
    except Exception as exc:
        errors.append(f"`session-start.py` 行为验证异常：{exc}")
        issues += 1

    if issues == 0:
        passed.append("新 hook 行为：全部通过")


def _load_shared_secret_patterns():
    """从 _shared_patterns.py 加载共享的 SECRET_PATTERNS，失败时回退到内联定义。"""
    import importlib.util
    shared_path = SAFETY_HOOKS_DIR / "_shared_patterns.py"
    try:
        spec = importlib.util.spec_from_file_location("_shared_patterns", shared_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["_shared_patterns"] = module
        spec.loader.exec_module(module)
        return list(module.SECRET_PATTERNS)
    except Exception:
        return [
            ("sk-* style key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
            ("github personal access token", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
            ("aws access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
            ("bearer token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
            ("credential assignment", re.compile(
                r"(?i)(?:api[_-]?key|secret|password|auth[_-]?token|access[_-]?token)"
                r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_/+=.\-]{16,}"
            )),
        ]

CONFIG_SECRET_PATTERNS = _load_shared_secret_patterns()

CONFIG_FILES_TO_SCAN = [
    CLAUDE_DIR / "settings.json",
    CLAUDE_DIR / "settings.local.json",
]

# 配置文件 secret 豁免白名单：key = 字段名（命中 secret 的上下文里出现该字段名即豁免），
# value = 豁免理由（替代 WARN 文案输出到 passed 行）。
# 历史：settings.json 的 ANTHROPIC_AUTH_TOKEN 明文是 CC-Switch 切换供应商时的
# 正常工作产物（见 conventions.md 已四犯豁免），扫描器自身应尊重该豁免而非反复误报。
CONFIG_SECRET_EXEMPTIONS = {
    "ANTHROPIC_AUTH_TOKEN": "CC-Switch 产物，主人已豁免（勿建议迁出）",
}


def _secret_in_exempted_field(content: str, match: re.Match, field_name: str) -> bool:
    """判断 secret 命中是否落在豁免字段名上下文内。
    JSON 形如 `"ANTHROPIC_AUTH_TOKEN": "sk-..."`。不同 pattern 命中点不同：
    匹配的是密钥主体（`sk-...`），则字段名在命中点前；匹配的模式是
    "凭据赋值"（`AUTH_TOKEN": "sk-..."`），字段名会被吃进 match 内部。
    因此取命中点前后窗口（前 100 + match 内容 + 后 30）作为上下文判断。
    """
    start = max(0, match.start() - 100)
    end = min(len(content), match.end() + 30)
    window = content[start:end]
    return f'"{field_name}"' in window or field_name in window


def check_pattern_consistency() -> None:
    """检查 7: SECRET_PATTERNS 一致性

    验证 write-safety.py / mcp-safety.py 内联回退副本的标签列表
    与 _shared_patterns.py 共享模块一致。不一致 → ERROR（回退副本过时）。
    """
    import ast
    import importlib.util

    shared_path = SAFETY_HOOKS_DIR / "_shared_patterns.py"
    write_safety_path = SAFETY_HOOKS_DIR / "write-safety.py"
    mcp_safety_path = SAFETY_HOOKS_DIR / "mcp-safety.py"

    def _extract_inline_sec_pattern_labels(source: str, source_name: str) -> list[str] | None:
        """从 Python 源码中提取模块级 SECRET_PATTERNS 赋值的标签列表。

        支持两种形式：
        1. 直接赋值: SECRET_PATTERNS = (("label1", ...), ("label2", ...))
        2. 动态加载: SECRET_PATTERNS = _load_shared_patterns()
           此时从 except 分支的 return 语句中提取回退标签。
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            errors.append(f"{source_name} AST 解析失败：{exc}")
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "SECRET_PATTERNS":
                        if isinstance(node.value, ast.Tuple):
                            return _labels_from_tuple(node.value)
                        if isinstance(node.value, ast.Call):
                            # 动态加载形式，查找 _load_shared_patterns 的回退
                            return _extract_dynamic_fallback(tree)
        return None

    def _labels_from_tuple(tuple_node: ast.Tuple) -> list[str]:
        labels = []
        for elt in tuple_node.elts:
            if isinstance(elt, ast.Tuple) and elt.elts:
                first = elt.elts[0]
                if isinstance(first, ast.Constant):
                    labels.append(first.value)
        return labels

    def _extract_dynamic_fallback(tree: ast.Module) -> list[str] | None:
        """从 _load_shared_patterns 的 except 分支提取回退标签列表。"""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_load_shared_patterns":
                for item in ast.walk(node):
                    if isinstance(item, ast.ExceptHandler):
                        for sub in ast.walk(item):
                            if isinstance(sub, ast.Return) and sub.value:
                                if isinstance(sub.value, ast.Tuple):
                                    return _labels_from_tuple(sub.value)
        return None

    def _extract_shared_labels(path: Path) -> list[str] | None:
        """从 _shared_patterns.py 提取 SECRET_PATTERNS 标签列表。"""
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception as exc:
            errors.append(f"_shared_patterns.py AST 解析失败：{exc}")
            return None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "SECRET_PATTERNS":
                        if isinstance(node.value, ast.Tuple):
                            labels = []
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Tuple) and elt.elts:
                                    first = elt.elts[0]
                                    if isinstance(first, ast.Constant):
                                        labels.append(first.value)
                            return labels
        return None

    if not shared_path.exists():
        return  # 共享模块不存在（如测试夹具），跳过检查

    shared_labels = _extract_shared_labels(shared_path)
    if shared_labels is None:
        errors.append("无法解析 _shared_patterns.py 的 SECRET_PATTERNS 标签")
        return

    for hook_path, hook_name in [
        (write_safety_path, "write-safety.py"),
        (mcp_safety_path, "mcp-safety.py"),
    ]:
        if not hook_path.exists():
            continue
        try:
            source = hook_path.read_text(encoding="utf-8")
        except Exception as exc:
            errors.append(f"无法读取 {hook_name}：{exc}")
            continue
        inline_labels = _extract_inline_sec_pattern_labels(source, hook_name)
        if inline_labels is None:
            continue  # 未找到 SECRET_PATTERNS 定义，跳过
        if inline_labels != shared_labels:
            missing = set(shared_labels) - set(inline_labels)
            extra = set(inline_labels) - set(shared_labels)
            detail_parts = []
            if missing:
                detail_parts.append(f"缺少：{', '.join(sorted(missing))}")
            if extra:
                detail_parts.append(f"多余：{', '.join(sorted(extra))}")
            errors.append(
                f"{hook_name} SECRET_PATTERNS 回退副本与 _shared_patterns.py 不一致"
                f"（{'；'.join(detail_parts)}）"
            )
        else:
            passed.append(f"{hook_name} 回退副本与共享模块一致（{len(shared_labels)} 个 pattern）")


def check_config_secret_leakage() -> None:
    """检查 8: 配置文件 secret 扫描

    扫描 settings.json / settings.local.json 是否含明文 secret。
    检测到则归入 WARN（不阻断运行，但醒目提示用户改用环境变量或密钥管理器）。
    """
    scanned_count = 0
    exempt_count = 0
    exempt_reasons: List[str] = []
    for config_file in CONFIG_FILES_TO_SCAN:
        if not config_file.exists():
            continue
        scanned_count += 1
        try:
            content = config_file.read_text(encoding="utf-8")
        except Exception as exc:
            warnings.append(f"无法读取 `{config_file.name}` 进行 secret 扫描：{exc}")
            continue
        for label, pattern in CONFIG_SECRET_PATTERNS:
            for match in pattern.finditer(content):
                # 字段级 allowlist：命中落在豁免字段上下文则跳过，记录豁免理由
                exempted = False
                for field_name, reason in CONFIG_SECRET_EXEMPTIONS.items():
                    if _secret_in_exempted_field(content, match, field_name):
                        exempt_count += 1
                        if reason not in exempt_reasons:
                            exempt_reasons.append(reason)
                        exempted = True
                        break
                if exempted:
                    continue
                warnings.append(
                    f"`{config_file.name}` 包含疑似 secret：{label}"
                    f"（建议改用环境变量或 OS 密钥管理器）"
                )

    if scanned_count > 0:
        if exempt_count > 0:
            passed.append(
                f"配置文件 secret 扫描：已扫描 {scanned_count} 个文件，"
                f"{exempt_count} 项已豁免（{'; '.join(exempt_reasons)}）"
            )
        else:
            passed.append(f"配置文件 secret 扫描：已扫描 {scanned_count} 个文件")


# Plan 2.16.2: hook-runner 调度耦合硬断言
# 历史：v2.16.1 修了本检查器对 hook 注册路径的 14 条误报，根因是校验逻辑
# 没对齐 hook-runner.py 的真实调度（纯脚本名拼 skills/util-safety/hooks/）。
# 此项把那条软约束警示（memory）转为硬约束：运行时真跑 runner 验证调度行为，
# + 静态断言 runner 源码仍用三段路径常量拼接。任一破裂 → ERROR，避免下次
# 有人改 runner 调度目录时检查器静默误判。
HOOK_RUNNER_SCRIPT = CLAUDE_DIR / "scripts" / "hook-runner.py"
HOOK_RUNNER_DISPATCH_SEGMENTS = ("skills", "util-safety", "hooks")


def check_hook_runner_coupling() -> None:
    """检查：hook-runner 调度耦合

    两个互补断言：
    1. 运行时：合成不存在的脚本名喂给 hook-runner.py，断言 exit=2 +
       stderr 含「hook 脚本不存在」且 stderr 路径含三段拼接目录——证明
       runner 仍在用 skills/util-safety/hooks/ 调度（而非相对 cwd）。
    2. 静态：读 hook-runner.py 源码，断言其中含三段路径常量——防止
       有人改 runner 调度目录时检查器和 runner 漂移。
    """
    _err0 = len(errors)
    if not HOOK_RUNNER_SCRIPT.exists():
        errors.append("`scripts/hook-runner.py` 不存在，hook 调度器失效")
        return

    # 断言 1：运行时行为
    smoke_script = "__health_check_no_such_hook__.py"
    try:
        result = subprocess.run(
            [sys.executable, str(HOOK_RUNNER_SCRIPT), smoke_script],
            input=b"",
            capture_output=True,
            timeout=5,
            creationflags=_subprocess_creationflags(),
            env=_sanitized_hook_env(),
        )
    except Exception as exc:
        errors.append(f"hook-runner 调度验证失败：无法启动 runner：{exc}")
        return

    stderr_text = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 2:
        errors.append(
            f"hook-runner 调度验证失败：喂不存在的脚本名应 exit=2，"
            f"实际 exit={result.returncode}"
        )
    elif smoke_script.replace(".py", "") not in stderr_text and smoke_script not in stderr_text:
        # stderr 必须反映出它确实尝试拼了我喂的假脚本名（不依赖中文文案，
        # 避免 Windows GBK 控制台编码导致断言误判）
        errors.append(
            "hook-runner 调度验证失败：stderr 未反映所喂脚本名"
            f"（runner 协议漂移）：{stderr_text.strip()[:120]}"
        )
    else:
        # stderr 路径应含三段，证明 runner 拼到了正确目录
        missing_segs = [s for s in HOOK_RUNNER_DISPATCH_SEGMENTS if s not in stderr_text]
        if missing_segs:
            errors.append(
                f"hook-runner 调度耦合破裂：runner 拼接的路径缺少 {missing_segs}，"
                f"与 SAFETY_HOOKS_DIR 不符：{stderr_text.strip()[:120]}"
            )

    # 断言 2：静态源码常量
    try:
        source = HOOK_RUNNER_SCRIPT.read_text(encoding="utf-8")
    except Exception as exc:
        errors.append(f"hook-runner 耦合静态校验失败：无法读取源码：{exc}")
    else:
        missing_consts = [s for s in HOOK_RUNNER_DISPATCH_SEGMENTS if s not in source]
        if missing_consts:
            errors.append(
                f"hook-runner 调度耦合破裂：源码缺少路径常量 {missing_consts}，"
                "health-check 的 hook 校验已与 runner 调度目录漂移"
            )

    if len(errors) == _err0:
        passed.append("hook-runner 调度耦合：runner 调度目录与 SAFETY_HOOKS_DIR 一致")


# Plan-3 3.4: 意图-实现一致性检查（WARN 级别）
# 抓取 SKILL.md 中"❌ ... `path`"格式的具体路径声明（带 /、扩展名或多段命名），
# 验证 write-safety hook 是否真的阻断对应写入。只匹配明确路径，避免抓"❌ 不修改其他 memory 文件"等抽象描述。
INTENT_PATH_REF_PATTERN = re.compile(
    r"❌\s*[^\n`]*?`([^`]+)`"
)


def _looks_like_concrete_path(token: str) -> bool:
    """判断反引号内是否像具体路径/文件名（而非函数名、变量、抽象概念）。"""
    if not token or len(token) > 200:
        return False
    # 排除明显的代码符号：函数调用、CamelCase、纯标识符
    if "(" in token or ")" in token:
        return False
    if token.startswith("--") or token.startswith("-"):
        return False
    # 必须含 / 或 . 才像路径/文件名
    if "/" not in token and "." not in token:
        return False
    # 排除版本号、命令行选项等
    if re.fullmatch(r"v?\d+(\.\d+)+", token):
        return False
    return True


def check_intent_implementation_consistency() -> None:
    """检查 10: 意图-实现一致性

    扫描各 SKILL.md 中"❌ ...`path`"形式的具体路径声明，
    构造 mock PreToolUse payload 调用 write-safety.py，
    若 hook 允许 → WARN（声明与实现不一致）。
    """
    write_safety = SAFETY_HOOKS_DIR / "write-safety.py"
    if not write_safety.exists():
        return

    skill_dirs = [d for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name != "rules"]
    rules_files = list(RULES_DIR.glob("*.md")) if RULES_DIR.exists() else []
    targets: List[Path] = []
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            targets.append(skill_md)
    targets.extend(rules_files)

    inconsistencies = 0
    checked_refs = 0
    seen: Set[Tuple[str, str]] = set()

    for md_file in targets:
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = md_file.relative_to(CLAUDE_DIR)

        for path_ref in INTENT_PATH_REF_PATTERN.findall(content):
            token = path_ref.strip()
            if not _looks_like_concrete_path(token):
                continue
            # 只取最后一段（防止 `memory/sessions/` 这种目录引用走偏）
            if (str(rel), token) in seen:
                continue
            seen.add((str(rel), token))

            # 构造合成路径：放到一个 project 子目录下，确保命中通用 path 规则
            # 不命中 control-plane（避免被 .claude/ 前缀直接拦截，模糊原始 ❌ 意图）
            test_path = f"C:/_intent_check_/{token.lstrip('/').lstrip('.').lstrip('/')}"
            payload = {
                "tool_name": "Write",
                "tool_input": {"file_path": test_path, "content": "intent-check"},
            }
            try:
                result = subprocess.run(
                    [sys.executable, str(write_safety)],
                    input=json.dumps(payload).encode("utf-8"),
                    capture_output=True,
                    timeout=5,
                    creationflags=_subprocess_creationflags(),
                    env=_sanitized_hook_env(),
                )
            except Exception:
                continue
            checked_refs += 1
            if result.returncode == 0:
                inconsistencies += 1
                warnings.append(
                    f"`{rel}` 声明 ❌ `{token}` 但 write-safety 未硬拦截"
                    f"（意图层声明无 hook 兜底，仅靠 AI 自律）"
                )

    stats["intent_checks"] = checked_refs
    if inconsistencies == 0:
        if checked_refs > 0:
            passed.append(f"意图-实现一致性：{checked_refs} 个 ❌ 声明均有 hook 兜底")
        else:
            passed.append("意图-实现一致性：未发现带具体路径的 ❌ 声明（抽象声明不在本检查范围）")



def check_hook_stderr_messages() -> None:
    """检查 11: Hook stderr 消息格式

    验证三个守卫 hook 的 stderr 输出中包含：
    - [安全守卫] 标签
    - 人可读的拦截原因
    - 授权方式提示
    """
    hooks_to_check = [
        (SAFETY_HOOKS_DIR / "bash-safety-wrapper.py", {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m test"}
        }, [
            r"permissionDecision",
            r"ask",
            r"permissionDecisionReason",
            r"AI 请求执行敏感操作",
        ]),
        (SAFETY_HOOKS_DIR / "write-safety.py", {
            "tool_name": "Write",
            "tool_input": {"file_path": str(Path.home() / "project" / ".env")}
        }, [
            r"\[安全守卫\]",
            r"敏感文件路径：",
            r"授权",
        ]),
        (SAFETY_HOOKS_DIR / "mcp-safety.py", {
            "tool_name": "mcp__server__tool",
            "tool_input": {"token": "sk-" + "A" * 24}
        }, [
            r"\[安全守卫\]",
            r"工具：",
            r"原因：",
            r"CLAUDE_HOOK_APPROVED_MCP",
        ]),
    ]

    issues = 0
    for script, payload, required_patterns in hooks_to_check:
        if not script.exists():
            errors.append(f"`{script.name}` 不存在，无法验证 stderr 消息格式")
            issues += 1
            continue

        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(payload).encode("utf-8"),
                capture_output=True,
                timeout=5,
                creationflags=_subprocess_creationflags(),
                env=_sanitized_hook_env(),
            )
        except subprocess.TimeoutExpired:
            errors.append(f"`{script.name}` stderr 消息验证超时")
            issues += 1
            continue
        except Exception as exc:
            errors.append(f"`{script.name}` stderr 消息验证异常：{exc}")
            issues += 1
            continue

        output_text = result.stdout.decode("utf-8", errors="replace") if script.name == "bash-safety-wrapper.py" else result.stderr.decode("utf-8", errors="replace")
        stream_name = "stdout" if script.name == "bash-safety-wrapper.py" else "stderr"
        for pattern in required_patterns:
            if not re.search(pattern, output_text):
                errors.append(
                    f"`{script.name}` {stream_name} 缺少必要模式：{pattern}"
                )
                issues += 1

    if issues == 0:
        passed.append("Hook stderr 消息格式：全部通过")


def check_mcp_pattern_rules() -> None:
    """检查 12: MCP 动作类别模式规则（P6）行为冒烟。

    验证 mcp-safety.py 的 block_tool_patterns / allow_tool_patterns 生效：
    - 未列入精确黑名单但命中高危动词的工具被拦（exit 2）
    - 只读动词工具放行（exit 0），含写动词子串的只读工具（get_sender）不误拦
    若模式规则被改坏（如清空 block_tool_patterns），此检查会捕获。
    """
    mcp_safety = SAFETY_HOOKS_DIR / "mcp-safety.py"
    if not mcp_safety.exists():
        return

    block_ok = run_hook_behavior(
        mcp_safety,
        {"tool_name": "mcp__notion__delete_page", "tool_input": {"x": "y"}},
        "高风险动作模式",
    )
    allow_ok = run_hook_behavior_exit(
        mcp_safety,
        {"tool_name": "mcp__db__get_sender", "tool_input": {"x": "y"}},
        0,
    )
    if block_ok and allow_ok:
        passed.append("MCP 模式规则（P6）：拦高危动词 + 放行只读动词")


def check_entropy_recheck() -> None:
    """检查 13: secret 凭据赋值熵复核（P2）行为冒烟。

    验证 write-safety.py 的熵复核生效：
    - 高熵随机值的凭据赋值被拦（疑似真密钥）
    - 低熵合法标识符的凭据赋值放行（撤销误报）
    若熵阈值被误改（如设为 0 放行一切，或设为极大值拦一切），此检查会捕获。
    同时检查 _ENTROPY_RECHECK_LABELS 不为空，提醒开发者新增宽模式时补充。
    """
    # 检查 _ENTROPY_RECHECK_LABELS 非空
    shared_path = SAFETY_HOOKS_DIR / "_shared_patterns.py"
    if shared_path.exists():
        try:
            import ast
            source = shared_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "_ENTROPY_RECHECK_LABELS":
                            if isinstance(node.value, (ast.Call,)):
                                # frozenset({...})
                                for arg in getattr(node.value, 'args', []):
                                    if isinstance(arg, ast.Set) and len(arg.elts) == 0:
                                        warnings.append(
                                            "_shared_patterns.py 的 _ENTROPY_RECHECK_LABELS 为空——"
                                            "新增宽模式 secret pattern 时记得补充，否则熵复核形同虚设"
                                        )
                            elif isinstance(node.value, (ast.Set, ast.Tuple, ast.List)):
                                if len(node.value.elts) == 0:
                                    warnings.append(
                                        "_shared_patterns.py 的 _ENTROPY_RECHECK_LABELS 为空——"
                                        "新增宽模式 secret pattern 时记得补充，否则熵复核形同虚设"
                                    )
                            break
        except Exception:
            pass

    write_safety = SAFETY_HOOKS_DIR / "write-safety.py"
    if not write_safety.exists():
        return

    # 高熵随机值用拼接构造，避免本脚本源码字面量被 write-safety 的 secret 扫描误拦。
    high_entropy_value = "aB3xK9mQ" + "7zR2wL5n" + "P8vT4cF6" + "dH1jS0"
    high_entropy_ok = run_hook_behavior(
        write_safety,
        {"tool_name": "Write",
         "tool_input": {"file_path": "C:/proj/app.py",
                        "content": f'api_key = "{high_entropy_value}"'}},
        "嵌入的密钥",
    )
    # 低熵但主正则命中的凭据赋值 → 熵复核撤销命中（放行，exit 0）。
    # 用例须满足：主正则命中（api_key= 前缀）+ 值低熵（defaultplaceholder 熵≈3.4<4.0），
    # 才能真正验证熵复核逻辑——若阈值被误改为 0，此用例会被拦从而暴露问题。
    low_entropy_ok = run_hook_behavior_exit(
        write_safety,
        {"tool_name": "Write",
         "tool_input": {"file_path": "C:/proj/app.py",
                        "content": 'api_key = "defaultplaceholder"'}},
        0,
    )
    if high_entropy_ok and low_entropy_ok:
        passed.append("secret 熵复核（P2）：拦高熵密钥 + 放行低熵标识符")


def check_rule_conflicts() -> None:
    """检查规则文件间的逻辑冲突"""
    _err0 = len(errors)

    # 扫描所有规则文件，提取强制性约束
    rules_constraints = {}
    for rule_file in RULES_DIR.glob("*.md"):
        try:
            content = rule_file.read_text(encoding="utf-8")
            # 提取"必须/禁止/不得/一律"等强制词及其上下文
            constraints = []
            for line in content.split('\n'):
                if any(keyword in line for keyword in ['必须', '禁止', '不得', '一律', '强制']):
                    # 清理 markdown 标记
                    clean_line = line.strip().lstrip('#-*> ').strip()
                    if clean_line:
                        constraints.append(clean_line)
            rules_constraints[rule_file.name] = constraints
        except Exception:
            pass

    # 已知的潜在冲突模式（人工维护的冲突规则库）
    # v2.13: skill-boundaries.md 已删除，示例更新为现存规则
    known_conflicts = [
        {
            'rule1': 'workflow.md',
            'keyword1': 'TaskCreate',
            'rule2': 'skill-org.md',
            'keyword2': '循环依赖',
            'context': '大任务在 Skill 内执行时'
        },
    ]

    # 检测已知冲突
    detected_conflicts = []
    for conflict_pattern in known_conflicts:
        rule1_name = conflict_pattern['rule1']
        rule2_name = conflict_pattern['rule2']

        if rule1_name not in rules_constraints or rule2_name not in rules_constraints:
            continue

        # 简化检测：检查关键词是否同时出现
        rule1_has_keyword = any(
            conflict_pattern['keyword1'] in constraint
            for constraint in rules_constraints[rule1_name]
        )
        rule2_has_keyword = any(
            re.search(conflict_pattern['keyword2'], constraint)
            for constraint in rules_constraints[rule2_name]
        )

        if rule1_has_keyword and rule2_has_keyword:
            detected_conflicts.append(
                f"潜在冲突：{rule1_name} 与 {rule2_name} "
                f"在「{conflict_pattern['context']}」场景下可能矛盾"
            )

    # 报告结果
    if detected_conflicts:
        for conflict in detected_conflicts:
            warnings.append(conflict)

    if len(errors) == _err0:
        stats_msg = f"{len(rules_constraints)} 个规则文件"
        if detected_conflicts:
            passed.append(f"规则冲突检测：扫描 {stats_msg}，发现 {len(detected_conflicts)} 个潜在冲突")
        else:
            passed.append(f"规则冲突检测：扫描 {stats_msg}，未发现已知冲突模式")


def main() -> int:
    """主函数"""
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command in {"test", "--test"}:
            return run_tests()
        print(f"错误：未知参数：{command}", file=sys.stderr)
        print("用法：skills-health-check.py [test|--test]", file=sys.stderr)
        return 1

    if not SKILLS_DIR.exists():
        print(f"错误：Skills 目录不存在：{SKILLS_DIR}", file=sys.stderr)
        return 1

    check_structure_integrity()
    check_reference_consistency()
    check_compliance()
    check_memory_path_consistency()
    check_dependency_graph()
    check_runtime_constraints()
    check_versioning()
    check_new_hook_registrations()
    check_new_hook_behaviors()
    check_pattern_consistency()
    check_config_secret_leakage()
    check_hook_runner_coupling()
    check_intent_implementation_consistency()
    check_hook_stderr_messages()
    check_mcp_pattern_rules()
    check_entropy_recheck()
    check_rule_conflicts()

    return print_report()


if __name__ == "__main__":
    sys.exit(main())
