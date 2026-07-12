#!/usr/bin/env python3
"""write-safety.py hook 单元测试。"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Windows GBK 终端强制 UTF-8（重新打开 fd 绕过默认编码）
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
try:
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, closefd=False)
except Exception:
    pass



HOOK_SCRIPT = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "write-safety.py"


def run_hook(file_path: str, tool_name: str = "Write", content: str | None = None,
             new_string: str | None = None,
             extra_env: dict | None = None,
             keep_approval_env: bool = False) -> tuple[int, str, str]:
    tool_input: dict[str, str] = {"file_path": file_path}
    if content is not None:
        tool_input["content"] = content
    if new_string is not None:
        tool_input["new_string"] = new_string
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
    }

    if keep_approval_env:
        env = dict(os.environ)
    else:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("CLAUDE_HOOK_APPROVED_")}
    # 默认隔离 grants 目录：防真实 ~/.claude/.grants/（如残留的 control-plane.session）
    # 污染拦截测试。调用方可在 extra_env 显式覆盖 CLAUDE_TEST_GRANTS_DIR。
    _tmp_ctx = None
    if not (extra_env and "CLAUDE_TEST_GRANTS_DIR" in extra_env):
        _tmp_ctx = tempfile.TemporaryDirectory()
        env["CLAUDE_TEST_GRANTS_DIR"] = _tmp_ctx.name
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=5,
            env=env,
        )
    finally:
        if _tmp_ctx is not None:
            _tmp_ctx.cleanup()

    return result.returncode, result.stdout.decode(), result.stderr.decode()


def test_safe_paths():
    safe_paths = [
        "C:/Users/nnie/project/src/main.py",
        "/home/user/project/src/app.ts",
    ]

    for path in safe_paths:
        code, _, stderr = run_hook(path)
        assert code == 0, f"安全路径被阻断：{path} ({stderr})"

    print("[PASS] 安全 Write/Edit 路径测试通过")


def test_sensitive_path_false_positives_allowed():
    safe_paths = [
        "C:/Users/nnie/project/src/tokenizer.py",
        "C:/Users/nnie/project/docs/计划-token.md",
        "C:/Users/nnie/project/docs/update_token_count.md",
        "C:/Users/nnie/project/src/secret_manager_notes.txt",
        "C:/Users/nnie/project/src/password_strength.ts",
        "C:/Users/nnie/project/src/api_key_parser.py",
    ]

    for path in safe_paths:
        code, _, stderr = run_hook(path)
        assert code == 0, f"关键词普通路径被误阻断：{path} ({stderr})"

    print("[PASS] 敏感关键词普通路径不误伤测试通过")


def test_sensitive_paths():
    sensitive_paths = [
        "C:/Users/nnie/project/.env",
        "C:/Users/nnie/project/config/.env.production",
        "C:/Users/nnie/project/credentials.json",
        "C:/Users/nnie/project/serviceAccount.json",
        "C:/Users/nnie/project/private.key",
        "C:/Users/nnie/project/cert.pem",
        "C:/Users/nnie/project/id_rsa",
    ]

    for path in sensitive_paths:
        code, _, stderr = run_hook(path)
        assert code == 2, f"敏感路径未被阻断：{path}"
        assert "敏感" in stderr or "sensitive" in stderr.lower(), f"缺少敏感警告：{path}"

    print("[PASS] 敏感 Write/Edit 路径测试通过")


def test_system_paths():
    system_paths = [
        "C:/Windows/System32/drivers/etc/hosts",
        "C:/Program Files/App/config.ini",
        "C:/ProgramData/app/config.ini",
        "/etc/hosts",
        "/usr/bin/tool",
        "/var/log/app.log",
    ]

    for path in system_paths:
        code, _, stderr = run_hook(path)
        assert code == 2, f"系统路径未被阻断：{path}"
        assert "系统" in stderr or "system" in stderr.lower(), f"缺少系统路径警告：{path}"

    print("[PASS] 系统 Write/Edit 路径测试通过")


def test_control_plane_paths():
    control_plane_paths = [
        "C:/Users/nnie/.claude/settings.json",
        "C:/Users/nnie/.claude/settings.local.json",
        "C:/Users/nnie/.claude/CLAUDE.md",
        "C:/Users/nnie/.claude/skills/util-safety/hooks/rule-loader.py",
        "C:/Users/nnie/.claude/skills/util-check/scripts/skills-health-check.py",
        "C:/Users/nnie/.claude/skills/util-safety/tests/test_write_safety.py",
        "C:/Users/nnie/.claude/skills/util-check/SKILL.md",
    ]

    for path in control_plane_paths:
        code, _, stderr = run_hook(path)
        assert code == 2, f"控制平面路径未被阻断：{path}"
        assert "控制平面" in stderr or "control-plane" in stderr.lower(), f"缺少控制平面警告：{path}"

    print("[PASS] 控制平面 Write 路径测试通过")


def test_facade_files_guarded():
    """v2.18.1：README.md / MIGRATION.md / memory 索引(MEMORY.md) 是门面文件，必须受 control-plane 守卫。

    此前这三者既不在 PREFIXES 也不在 CRITICAL_FILES，write-safety 对其零拦截，
    AI 可静默改写门面绕过守卫。修复后归入 CRITICAL_FILES，本测试断言命中、防回退。
    注意 normalize_path 会 .lower() 与统一正斜杠，故 memory 路径取小写形式。
    """
    facade_paths = [
        "C:/Users/nnie/.claude/README.md",
        "C:/Users/nnie/.claude/MIGRATION.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/MEMORY.md",
    ]
    for path in facade_paths:
        code, _, stderr = run_hook(path)
        assert code == 2, f"门面文件未被阻断：{path}"
        assert "控制平面" in stderr or "control-plane" in stderr.lower(), f"缺少控制平面警告：{path}"

    # 反向断言：memory 正文文件(debugging/decisions/conventions)故意不守卫，
    # 高频写 memory 免每次弹窗。此处确认它们仍放行，防止有人将来误把正文也加进守卫。
    memory_body_paths = [
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/decisions.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/conventions.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/debugging.md",
    ]
    for path in memory_body_paths:
        code, _, _ = run_hook(path)
        assert code == 0, f"memory 正文文件不应被 control-plane 拦截（高频写免摩擦）：{path}"

    print("[PASS] 门面文件守卫测试通过（README/MIGRATION/MEMORY.md 拦截 + memory 正文放行）")


def test_control_plane_edit_paths():
    critical_edit_paths = [
        "C:/Users/nnie/.claude/settings.json",
        "C:/Users/nnie/.claude/settings.local.json",
        "C:/Users/nnie/.claude/CLAUDE.md",
    ]
    for path in critical_edit_paths:
        code, _, stderr = run_hook(path, tool_name="Edit", new_string="harmless edit")
        assert code == 2, f"关键控制平面 Edit 未被阻断：{path} ({stderr})"
        assert "控制平面" in stderr or "control-plane" in stderr.lower(), f"缺少控制平面警告（关键 Edit）：{path}"

    # v2.2.1：PREFIXES（hooks/、scripts/、tests/、skills/）现在也拦截 Edit。
    # 此前此处断言 code == 0；改为 code == 2 以封堵 AI 通过 Edit 解除安全 hook
    # 的自我修改向量。
    prefix_edit_paths = [
        "C:/Users/nnie/.claude/skills/util-safety/hooks/write-safety.py",
        "C:/Users/nnie/.claude/skills/util-safety/hooks/bash-safety-wrapper.py",
        "C:/Users/nnie/.claude/skills/util-safety/SKILL.md",
        "C:/Users/nnie/.claude/skills/util-check/scripts/skills-health-check.py",
    ]
    for path in prefix_edit_paths:
        code, _, stderr = run_hook(path, tool_name="Edit", new_string="harmless edit")
        assert code == 2, f"控制平面 PREFIX Edit 未被阻断：{path} ({stderr})"
        assert "控制平面" in stderr or "control-plane" in stderr.lower(), f"缺少控制平面警告（Edit）：{path}"

    print("[PASS] 控制平面 Edit 路径测试通过")


def test_control_plane_escape_hatch():
    """v2.2.1：CLAUDE_HOOK_APPROVED_CONTROL_PLANE=1 必须放行控制平面 Edit。"""
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "C:/Users/nnie/.claude/skills/util-safety/hooks/write-safety.py",
            "new_string": "trusted update",
        },
    }
    env = dict(os.environ)
    env["CLAUDE_HOOK_APPROVED_CONTROL_PLANE"] = "1"
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=5,
        env=env,
    )
    code = result.returncode
    stderr = result.stderr.decode()
    assert code == 0, f"逃生舱口未放行控制平面 Edit：{stderr}"

    print("[PASS] 控制平面逃生舱口测试通过")


def test_secret_detection_in_control_plane_files():
    """settings.json / settings.local.json / CLAUDE.md 中的嵌入密钥必须被阻断。"""
    secret_content_cases = [
        ("C:/Users/nnie/.claude/settings.json", '{"token": "sk-AbCdEf0123456789-_ZyXwVuT"}'),
        ("C:/Users/nnie/.claude/settings.local.json", '{"github": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"}'),
        ("C:/Users/nnie/.claude/CLAUDE.md", "aws key AKIAABCDEFGHIJKLMNOP in docs"),
        ("C:/Users/nnie/.claude/settings.json", '{"h": "Authorization: Bearer abcdef0123456789ABCDEF=="}'),
        ("C:/Users/nnie/.claude/settings.json", '{"api_key": "abcdef0123456789ABCDEF"}'),
    ]
    for path, body in secret_content_cases:
        code, _, stderr = run_hook(path, tool_name="Edit", new_string=body)
        assert code == 2, f"密钥未被阻断：{path} | body={body[:40]}..."
        assert "嵌入的密钥" in stderr or "embedded secret" in stderr.lower(), f"缺少密钥警告：{path}"

    print("[PASS] 控制平面文件密钥检测测试通过")


def test_secret_detection_in_normal_files_now_blocked():
    """v2.2.0 重大变更：密钥扫描现默认覆盖所有 Write/Edit 内容。此前此用例被放行。"""
    normal_path = "C:/Users/nnie/project/src/normal.ts"
    body = 'const example = "sk-AbCdEf0123456789-_ZyXwVuT"; // looks like a real secret'
    code, _, stderr = run_hook(normal_path, tool_name="Write", content=body)
    assert code == 2, f"v2.2.0 应阻断普通文件中的密钥，exit={code} stderr={stderr}"
    assert "嵌入的密钥" in stderr or "embedded secret" in stderr.lower(), f"缺少密钥警告：{stderr}"

    print("[PASS] 普通文件密钥检测（v2.2.0 重大变更）测试通过")


def test_secret_scan_whitelist_paths():
    """白名单路径即使含嵌入密钥也不应触发密钥扫描。"""
    fixture_paths = [
        "C:/Users/nnie/project/tests/fixtures/sample.json",
        "C:/Users/nnie/project/__fixtures__/data.json",
        "C:/Users/nnie/project/test_fixtures/leaked.json",
        "C:/Users/nnie/project/node_modules/some-lib/dist/example.js",
    ]
    body = '{"api_key": "sk-LooksLikeSecretButFixture12345"}'
    for path in fixture_paths:
        code, _, stderr = run_hook(path, tool_name="Write", content=body)
        assert code == 0, f"白名单路径被阻断：{path} | {stderr}"

    print("[PASS] 密钥扫描白名单路径测试通过")


def test_secret_scan_whitelist_filenames():
    """Whitelisted filenames (.example/.sample/.template/README/CHANGELOG) must NOT trigger secret scan.
    Note: paths whose basename matches sensitive patterns (.env, *.key) are still blocked by path rule.
    This test only validates the content-scan whitelist, not the path whitelist."""
    paths = [
        "C:/Users/nnie/project/config.template.json",
        "C:/Users/nnie/project/data.sample.yaml",
        "C:/Users/nnie/project/settings.example.json",
        "C:/Users/nnie/project/README.md",
        "C:/Users/nnie/project/CHANGELOG.md",
    ]
    body = 'token=sk-LooksLikeSecretInDocs1234567890'
    for path in paths:
        code, _, stderr = run_hook(path, tool_name="Write", content=body)
        assert code == 0, f"白名单文件名被阻断：{path} | {stderr}"

    print("[PASS] 密钥扫描白名单文件名测试通过")


def test_secret_scan_skip_for_safety_tests():
    """The safety hook's own test files must be writable with literal secret samples."""
    test_path = "C:/Users/nnie/.claude/skills/util-safety/tests/test_write_safety.py"
    body = 'secret_value = "sk-LiteralForTesting12345abcdefg"  # noqa: test fixture'
    code, _, stderr = run_hook(test_path, tool_name="Write", content=body)
    # 注意：控制平面 Write 阻断仍会触发，但密钥扫描白名单必须放行。
    # 通过 stderr 断言"嵌入的密钥"不在阻断原因中。
    assert "嵌入的密钥" not in stderr and "embedded secret" not in stderr.lower(), \
        f"Safety test path triggered secret scan unexpectedly: {stderr}"

    print("[PASS] 安全测试密钥扫描跳过测试通过")


def test_secret_pattern_not_leaked_in_stderr():
    """When blocking for embedded secret, stderr must contain pattern label only, never raw value."""
    secret_value = "sk-LeakyValueShouldNeverAppear12345"
    path = "C:/Users/nnie/.claude/settings.json"
    body = f'{{"token": "{secret_value}"}}'
    code, _, stderr = run_hook(path, tool_name="Edit", new_string=body)
    assert code == 2
    assert secret_value not in stderr, f"Raw secret leaked into stderr! stderr={stderr}"
    assert "sk-* 风格密钥" in stderr or "sk-* style key" in stderr.lower(), f"模式标签未在 stderr 中出现：{stderr}"

    print("[PASS] 密钥值未泄露到 stderr 测试通过")


def test_system_automemory_filenames_blocked():
    """v2.2.1+ Plan-2: 系统默认 auto-memory 命名 (user_*/feedback_*/project_*/reference_*) 必须被阻断。"""
    blocked_paths = [
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/user_role.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/feedback_testing.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/project_status.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/reference_api.md",
        "/home/user/project/memory/user_profile.md",
    ]
    for path in blocked_paths:
        code, _, stderr = run_hook(path, tool_name="Write", content="# automemory")
        assert code == 2, f"系统 auto-memory 文件名未被阻断：{path} ({stderr})"
        assert "auto-memory" in stderr.lower(), f"缺少 auto-memory 警告：{path}"

    allowed_paths = [
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/debugging.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/decisions.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/conventions.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/dependencies.md",
        "C:/Users/nnie/.claude/projects/C--Users-nnie/memory/patterns.md",
        # 注：MEMORY.md（索引）已不在此列——v2.18.1 起它被 write-safety 的
        # control-plane CRITICAL_FILES 守卫，见 test_facade_files_guarded。本用例
        # 只断言「auto-memory 敏感文件名黑名单不误伤 5 个正文文件」，MEMORY.md 的
        # 命中由 control-plane 层负责，两检查层独立、不冲突。
    ]
    for path in allowed_paths:
        code, _, stderr = run_hook(path, tool_name="Write", content="# 5-class")
        assert code == 0, f"5 类文件名被错误阻断：{path} ({stderr})"

    print("[PASS] 系统 auto-memory 文件名阻断测试通过")


def test_edit_tool():
    code, _, stderr = run_hook("C:/Users/nnie/project/.env", tool_name="Edit")
    assert code == 2, f"Edit 敏感路径未被阻断：{stderr}"

    code, _, stderr = run_hook("C:/Users/nnie/project/src/main.py", tool_name="Edit")
    assert code == 0, f"Edit 安全路径被阻断：{stderr}"

    print("[PASS] Edit 工具测试通过")


def test_infra_config_paths_blocked():
    """Plan-3 3.3: CI/CD pipelines 和容器配置默认应被阻断（无 INFRA marker 时）。"""
    infra_paths = [
        "C:/Users/nnie/project/.github/workflows/ci.yml",
        "C:/Users/nnie/project/.github/workflows/deploy.yaml",
        "C:/Users/nnie/project/.gitlab-ci.yml",
        "C:/Users/nnie/project/Jenkinsfile",
        "C:/Users/nnie/project/Jenkinsfile.release",
        "C:/Users/nnie/project/azure-pipelines.yml",
        "C:/Users/nnie/project/bitbucket-pipelines.yml",
        "C:/Users/nnie/project/Dockerfile",
        "C:/Users/nnie/project/Dockerfile.dev",
        "C:/Users/nnie/project/docker-compose.yml",
        "C:/Users/nnie/project/docker-compose.prod.yaml",
        "C:/Users/nnie/project/kubernetes/deployment.yml",
        "C:/Users/nnie/project/k8s/service.yaml",
    ]
    for path in infra_paths:
        code, _, stderr = run_hook(path, tool_name="Edit", new_string="changed")
        assert code == 2, f"基础设施配置未被阻断：{path} ({stderr})"
        assert "基础设施" in stderr or "infrastructure" in stderr.lower(), f"缺少基础设施警告：{path}"

    print("[PASS] 基础设施配置路径阻断测试通过")


def test_infra_config_escape_marker():
    """Plan-3 3.3：CLAUDE_HOOK_APPROVED_INFRA=1 必须放行基础设施配置编辑。"""
    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "C:/Users/nnie/project/.github/workflows/ci.yml",
            "new_string": "jobs:\n  deploy:\n    runs-on: ubuntu-latest",
        },
    }
    env = dict(os.environ)
    env["CLAUDE_HOOK_APPROVED_INFRA"] = "1"
    env.pop("CLAUDE_HOOK_APPROVED_CONTROL_PLANE", None)
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=5,
        env=env,
    )
    assert result.returncode == 0, \
        f"INFRA marker did not allow infra edit: exit={result.returncode} stderr={result.stderr.decode()}"

    print("[PASS] 基础设施配置逃生标记测试通过")


def test_infra_lookalike_not_blocked():
    """Files that look similar to infra but aren't真的 CI/容器 config 不应被误伤。"""
    safe_paths = [
        "C:/Users/nnie/project/docs/Dockerfile.md",     # 文档，不是真的 Dockerfile
        "C:/Users/nnie/project/src/jenkinsfile.test.ts",  # 源码，非根 Jenkinsfile
        "C:/Users/nnie/project/config/app.yml",         # 普通 yaml，非 CI/CD
    ]
    for path in safe_paths:
        code, _, stderr = run_hook(path, tool_name="Edit", new_string="ok")
        assert code == 0, f"相似基础设施路径被错误阻断：{path} ({stderr})"

    print("[PASS] 基础设施相似路径不误伤测试通过")


def test_non_write_tools():
    code, _, stderr = run_hook("C:/Users/nnie/project/.env", tool_name="Read")
    assert code == 0, f"非 Write 工具被阻断：{stderr}"

    print("[PASS] 非 Write 工具测试通过")


def test_missing_file_path():
    payload = {
        "tool_name": "Write",
        "tool_input": {}
    }

    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE_HOOK_APPROVED_")}
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=5,
        env=env,
    )

    assert result.returncode == 2, "缺少 file_path 未被阻断"
    assert "缺少 file_path" in result.stderr.decode() or "missing file_path" in result.stderr.decode().lower(), "缺少 file_path 警告"

    print("[PASS] 缺少 file_path 测试通过")


def test_grant_file_e2e():
    """v2.4.2 grant 文件消费机制端到端验证（T2）。"""
    import tempfile

    # TC-GRANT-01: 创建 .grants/sensitive → Write .env → grant 被消费，放行
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        (grants_dir / 'sensitive').write_text('')
        code, _, stderr = run_hook(
            'C:/Users/nnie/project/.env',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 0, f'TC-GRANT-01 grant 应放行：exit={code} stderr={stderr}'
        assert not (grants_dir / 'sensitive').exists(), 'TC-GRANT-01 grant 应已消费'

    # TC-GRANT-02: 无 grant → Write .env → 阻断
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        code, _, stderr = run_hook(
            'C:/Users/nnie/project/.env',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 2, f'TC-GRANT-02 无 grant 应阻断：exit={code}'
        assert '敏感' in stderr or 'sensitive' in stderr.lower(), f'TC-GRANT-02 应含 sensitive：{stderr}'

    # TC-GRANT-03: grant control-plane → Edit write-safety.py → 放行
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        (grants_dir / 'control-plane').write_text('')
        code, _, stderr = run_hook(
            'C:/Users/nnie/.claude/skills/util-safety/hooks/write-safety.py',
            tool_name='Edit',
            new_string='trusted update',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 0, f'TC-GRANT-03 grant control-plane 应放行：{stderr}'
        assert not (grants_dir / 'control-plane').exists(), 'TC-GRANT-03 grant 应已消费'

    # TC-GRANT-04: grant infra → Write Dockerfile → 放行
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        (grants_dir / 'infra').write_text('')
        code, _, stderr = run_hook(
            'C:/Users/nnie/project/Dockerfile',
            content='FROM python:3.11',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 0, f'TC-GRANT-04 grant infra 应放行：{stderr}'
        assert not (grants_dir / 'infra').exists(), 'TC-GRANT-04 grant 应已消费'

    # TC-GRANT-05: grant secret → Write 含 sk-xxx 文件 → 放行
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        (grants_dir / 'secret').write_text('')
        code, _, stderr = run_hook(
            'C:/Users/nnie/project/config.py',
            content='API_KEY = "sk-AbCdEf0123456789-_ZyXwVuT"',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 0, f'TC-GRANT-05 grant secret 应放行：{stderr}'
        assert not (grants_dir / 'secret').exists(), 'TC-GRANT-05 grant 应已消费'

    # TC-GRANT-06: grant 路径不可消费时的 fallback（fail-closed）
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        # 用目录伪装成 grant，exists() 为真但 unlink() 稳定失败。
        (grants_dir / 'sensitive').mkdir()
        code, _, stderr = run_hook(
            'C:/Users/nnie/project/.env',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 2, f'TC-GRANT-06 不可消费 grant 应阻断（fail-closed）：exit={code}'

    # TC-GRANT-07: env var CLAUDE_HOOK_APPROVED_SENSITIVE=1 直接授权（无 grant 文件）
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        code, _, stderr = run_hook(
            'C:/Users/nnie/project/.env',
            extra_env={
                'CLAUDE_TEST_GRANTS_DIR': str(grants_dir),
                'CLAUDE_HOOK_APPROVED_SENSITIVE': '1',
            },
            keep_approval_env=True,
        )
        assert code == 0, f'TC-GRANT-07 env var 应放行：{stderr}'

    print('[PASS] Grant 文件端到端测试通过')


def test_windows_special_paths():
    """子计划 5：Windows UNC / WSL 路径行为验证。"""
    # TC-PATH-01: 标准 Windows 绝对路径 .env → 阻断
    code, _, stderr = run_hook("C:\\Users\\nnie\\.env")
    assert code == 2, f"TC-PATH-01 标准 Windows .env 应阻断：{stderr}"

    # TC-PATH-02: POSIX 风格 Windows 路径 .env → 阻断
    code, _, stderr = run_hook("C:/Users/nnie/.env")
    assert code == 2, f"TC-PATH-02 POSIX 风格 .env 应阻断：{stderr}"

    # TC-PATH-03: UNC 前缀 Windows 路径 .env → 阻断
    code, _, stderr = run_hook("\\\\?\\C:\\Users\\nnie\\.env")
    assert code == 2, f"TC-PATH-03 UNC 前缀 .env 应阻断：{stderr}"

    # TC-PATH-04: WSL 路径 .env → 阻断
    code, _, stderr = run_hook("\\\\wsl$\\Ubuntu\\home\\user\\.env")
    assert code == 2, f"TC-PATH-04 WSL .env 应阻断：{stderr}"

    # TC-PATH-05: 正常项目文件 → 放行
    code, _, stderr = run_hook("C:\\Users\\nnie\\project\\src\\app.ts")
    assert code == 0, f"TC-PATH-05 正常项目文件应放行：{stderr}"

    # TC-PATH-06: control-plane 关键文件 → 阻断
    code, _, stderr = run_hook("C:\\Users\\nnie\\.claude\\settings.json", tool_name="Edit",
                               new_string="test")
    assert code == 2, f"TC-PATH-06 control-plane 关键文件应阻断：{stderr}"

    # TC-PATH-07: UNC + control-plane prefix → 阻断
    code, _, stderr = run_hook(
        "\\\\?\\C:\\Users\\nnie\\.claude\\skills\\util-safety\\hooks\\write-safety.py",
        tool_name="Edit", new_string="test")
    assert code == 2, f"TC-PATH-07 UNC + control-plane 应阻断：{stderr}"

    print("[PASS] Windows 特殊路径测试通过")


def test_grant_file_env_var_priority():
    """v2.4.2: grant 文件优先级高于 env var（先检查 grant 文件再检查 env var）。"""
    import tempfile

    # grant 文件存在时优先消费 grant 文件
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        (grants_dir / 'sensitive').write_text('')
        code, _, stderr = run_hook(
            'C:/Users/nnie/project/.env',
            extra_env={
                'CLAUDE_TEST_GRANTS_DIR': str(grants_dir),
                'CLAUDE_HOOK_APPROVED_SENSITIVE': '1',
            },
            keep_approval_env=True,
        )
        assert code == 0, f'grant+env 应放行：{stderr}'
        assert not (grants_dir / 'sensitive').exists(), 'grant 文件应已消费（而非仅依赖 env var）'

    print('[PASS] Grant 文件/Env Var 优先级测试通过')

def test_grant_atomicity_and_multi_reason_consumption():
    """同类别多原因只消费一次；跨类别部分授权不应部分消费。"""
    import tempfile

    # TC-GRANT-08: 同类别双原因（sensitive + system 都映射 sensitive）
    # 只给一个 sensitive grant，应该一次放行且只消费一次。
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        (grants_dir / 'sensitive').write_text('')
        code, _, stderr = run_hook(
            'C:/Windows/foo.pem',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 0, f'TC-GRANT-08 同类别双原因应放行：exit={code} stderr={stderr}'
        assert not (grants_dir / 'sensitive').exists(), 'TC-GRANT-08 grant 应恰好消费一次'

    # TC-GRANT-09: 跨类别部分授权（control-plane 有、secret 无）
    # 不应因为 control-plane grant 已经删除而导致 secret 仍缺失时把 grant 白白消费掉。
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        (grants_dir / 'control-plane').write_text('')
        code, _, stderr = run_hook(
            'C:/Users/nnie/.claude/settings.json',
            tool_name='Edit',
            new_string='{"token": "sk-AbCdEf0123456789-_ZyXwVuT"}',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 2, f'TC-GRANT-09 部分授权应阻断：exit={code} stderr={stderr}'
        assert (grants_dir / 'control-plane').exists(), 'TC-GRANT-09 control-plane grant 不应被部分消费'

    # TC-GRANT-10: 全授权多类别同时满足，全部消费型 grant 都应删除，.session 保留
    with tempfile.TemporaryDirectory() as tmp:
        grants_dir = Path(tmp) / '.grants'
        grants_dir.mkdir()
        (grants_dir / 'control-plane').write_text('')
        (grants_dir / 'secret').write_text('')
        (grants_dir / 'infra.session').write_text('')
        code, _, stderr = run_hook(
            'C:/Users/nnie/.claude/settings.json',
            tool_name='Edit',
            new_string='{"token": "sk-AbCdEf0123456789-_ZyXwVuT"}',
            extra_env={'CLAUDE_TEST_GRANTS_DIR': str(grants_dir)},
        )
        assert code == 0, f'TC-GRANT-10 全授权应放行：exit={code} stderr={stderr}'
        assert not (grants_dir / 'control-plane').exists(), 'TC-GRANT-10 control-plane 应被消费'
        assert not (grants_dir / 'secret').exists(), 'TC-GRANT-10 secret 应被消费'
        assert (grants_dir / 'infra.session').exists(), 'TC-GRANT-10 session grant 应保留'

    print('[PASS] Grant 原子性与多原因测试通过')


def test_grants_write_is_denied():
    """信任根加固：AI 用 Write/Edit 写 .grants/ 一律 deny（exit 2），
    不被任何 grant/env 豁免（防自建 grant 自我授权）。
    用 deny 而非 ask：bypass/跳过权限模式会吞 ask，唯 exit 2 可靠拦截。"""
    home = Path.home().as_posix()
    grants_targets = [
        f"{home}/.claude/.grants/git",
        f"{home}/.claude/.grants/control-plane",
        f"{home}/.claude/.grants/delete",
        "C:/Users/nnie/.claude/.grants/sensitive",
    ]
    for target in grants_targets:
        code, _, stderr = run_hook(target, tool_name="Write", content="")
        assert code == 2, f"写 .grants 应 deny（exit 2）：{target}"
        assert "拒绝" in stderr, f"写 .grants 应有拒绝信息：{target}"
        code, _, stderr = run_hook(target, tool_name="Edit", new_string="x")
        assert code == 2, f"Edit .grants 应 deny（exit 2）：{target}"

    # 关键：即使设了 env 授权，写 .grants 仍 deny（不被豁免，破解套娃）
    code, _, stderr = run_hook(
        f"{home}/.claude/.grants/git",
        content="",
        extra_env={"CLAUDE_HOOK_APPROVED_CONTROL_PLANE": "1",
                   "CLAUDE_HOOK_APPROVED_SENSITIVE": "1"},
        keep_approval_env=True,
    )
    assert code == 2, "写 .grants 不应被任何 env 授权豁免"

    print("[PASS] 写 .grants 一律 deny 测试通过")


def main():
    if not HOOK_SCRIPT.exists():
        print(f"错误：Hook 脚本未找到：{HOOK_SCRIPT}", file=sys.stderr)
        return 1

    try:
        test_safe_paths()
        test_sensitive_path_false_positives_allowed()
        test_sensitive_paths()
        test_system_paths()
        test_control_plane_paths()
        test_facade_files_guarded()
        test_control_plane_edit_paths()
        test_control_plane_escape_hatch()
        test_secret_detection_in_control_plane_files()
        test_secret_detection_in_normal_files_now_blocked()
        test_secret_scan_whitelist_paths()
        test_secret_scan_whitelist_filenames()
        test_secret_scan_skip_for_safety_tests()
        test_secret_pattern_not_leaked_in_stderr()
        test_system_automemory_filenames_blocked()
        test_edit_tool()
        test_infra_config_paths_blocked()
        test_infra_config_escape_marker()
        test_infra_lookalike_not_blocked()
        test_grant_file_e2e()
        test_grant_file_env_var_priority()
        test_grant_atomicity_and_multi_reason_consumption()
        test_grants_write_is_denied()
        test_non_write_tools()
        test_missing_file_path()
        test_windows_special_paths()

        print("\n[OK] 全部 write-safety.py 测试通过！")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败：{e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FAIL] 意外错误：{e}", file=sys.stderr)
        return 1


# ===== P0 新增测试 =====

def test_symlink_to_sensitive_is_blocked():
    """P0: 符号链接指向敏感文件应被拦截（symlink 绕过修复）。"""
    with tempfile.TemporaryDirectory() as tmp:
        # 创建敏感文件
        sensitive = Path(tmp) / "real" / ".env"
        sensitive.parent.mkdir(parents=True, exist_ok=True)
        sensitive.write_text("SECRET=1", encoding="utf-8")

        # 在安全目录创建指向敏感文件的符号链接
        safe_dir = Path(tmp) / "safe"
        safe_dir.mkdir(parents=True, exist_ok=True)
        link = safe_dir / "not-sensitive.txt"
        try:
            os.symlink(str(sensitive), str(link))
        except OSError:
            # Windows 非管理员可能无法创建 symlink，跳过测试
            print("[SKIP] 无 symlink 创建权限（Windows 需管理员），跳过 symlink 测试")
            return

        code, _, stderr = run_hook(str(link), tool_name="Write", content="new content")
        assert code == 2, f"symlink 指向敏感文件应被拦截（exit 2），实际 exit={code}：{stderr}"
        print("[PASS] symlink 指向敏感文件测试通过")


def test_symlink_to_system_path_is_blocked():
    """P0: 符号链接指向系统路径应被拦截。"""
    with tempfile.TemporaryDirectory() as tmp:
        # 在临时目录创建"伪装"的系统路径
        fake_system = Path(tmp) / "fake-etc" / "hosts"
        fake_system.parent.mkdir(parents=True, exist_ok=True)
        fake_system.write_text("127.0.0.1 localhost", encoding="utf-8")

        safe_dir = Path(tmp) / "safe2"
        safe_dir.mkdir(parents=True, exist_ok=True)
        link = safe_dir / "not-system.txt"
        try:
            os.symlink(str(fake_system), str(link))
        except OSError:
            print("[SKIP] 无 symlink 创建权限（Windows 需管理员），跳过 symlink 测试")
            return

        code, _, _ = run_hook(str(link), tool_name="Write", content="evil")
        # fake_system 不在真实的 /etc/ 所以不会命中 sytem 路径规则，但仍验证解析未崩溃
        assert code == 0, f"symlink 解析不应导致非敏感路径误拦：exit={code}"
        print("[PASS] symlink 非敏感路径不应误拦测试通过")


if __name__ == "__main__":
    sys.exit(main())
