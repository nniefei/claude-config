#!/usr/bin/env python3
"""测试 rule-loader TTL 机制（P3-2）— 缓存超过 1 小时自动过期重新注入。"""
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# 动态导入 rule-loader 的 should_inject 函数
RULE_LOADER_PATH = Path.home() / ".claude" / "skills" / "util-safety" / "hooks" / "rule-loader.py"


def test_ttl_expires_after_one_hour():
    """缓存超过 1 小时应视为过期，重新注入。"""
    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp)
        logs_dir.mkdir(parents=True, exist_ok=True)
        cache_path = logs_dir / "rule-injection-cache.json"

        # 模拟 1 小时前的注入记录
        now = datetime.now(timezone.utc).timestamp()
        one_hour_ago = now - 3601  # 超过 1 小时
        cache = {
            "session-123": {
                "workflow.md": one_hour_ago,
                "git-safety.md": now - 1800,  # 30 分钟前，未过期
            }
        }
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

        # 导入并 patch 路径
        import importlib.util
        spec = importlib.util.spec_from_file_location("rule_loader", RULE_LOADER_PATH)
        rl = importlib.util.module_from_spec(spec)
        sys.modules["rule_loader"] = rl
        spec.loader.exec_module(rl)
        # Patch 路径（在 exec_module 之后）
        rl._DEDUP_CACHE_PATH = cache_path
        rl.LOGS_DIR = logs_dir

        # 测试：workflow.md 过期应重新注入，git-safety.md 未过期不注入
        result = rl.should_inject("session-123", ["workflow.md", "git-safety.md"])

        assert "workflow.md" in result, f"过期规则应重新注入，实际 result={result}"
        assert "git-safety.md" not in result, f"未过期规则不应重新注入，实际 result={result}"

        # 验证缓存已更新 workflow.md 的时间戳
        updated_cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert updated_cache["session-123"]["workflow.md"] > one_hour_ago, "时间戳应更新"

    print("[PASS] TTL 1 小时过期测试通过")


def test_backward_compatible_with_list_cache():
    """旧缓存格式（列表）应自动迁移为字典格式。"""
    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp)
        logs_dir.mkdir(parents=True, exist_ok=True)
        cache_path = logs_dir / "rule-injection-cache.json"

        # 旧格式：列表
        cache = {
            "session-456": ["workflow.md", "git-safety.md"]
        }
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

        import importlib.util
        spec = importlib.util.spec_from_file_location("rule_loader", RULE_LOADER_PATH)
        rl = importlib.util.module_from_spec(spec)
        sys.modules["rule_loader"] = rl
        spec.loader.exec_module(rl)
        rl._DEDUP_CACHE_PATH = cache_path
        rl.LOGS_DIR = logs_dir

        # 请求注入已有规则，应跳过（刚迁移视为新注入）
        result = rl.should_inject("session-456", ["workflow.md", "skill-org.md"])

        # workflow.md 刚迁移视为已注入，skill-org.md 是新规则
        assert "skill-org.md" in result, "新规则应注入"
        assert "workflow.md" not in result, "迁移后的规则视为已注入"

        # 验证缓存已迁移为字典
        updated_cache = json.loads(cache_path.read_text(encoding="utf-8"))
        assert isinstance(updated_cache["session-456"], dict), "缓存应迁移为字典格式"

    print("[PASS] 向后兼容列表缓存测试通过")


def test_no_session_id_returns_all():
    """session_id 为空时应返回所有规则（安全回退）。"""
    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp)
        logs_dir.mkdir(parents=True, exist_ok=True)
        cache_path = logs_dir / "rule-injection-cache.json"

        import importlib.util
        spec = importlib.util.spec_from_file_location("rule_loader", RULE_LOADER_PATH)
        rl = importlib.util.module_from_spec(spec)
        sys.modules["rule_loader"] = rl
        spec.loader.exec_module(rl)
        rl._DEDUP_CACHE_PATH = cache_path
        rl.LOGS_DIR = logs_dir

        result = rl.should_inject("", ["workflow.md", "git-safety.md"])

        assert result == ["workflow.md", "git-safety.md"], "无 session_id 应返回全部规则"

    print("[PASS] 无 session_id 安全回退测试通过")


def main() -> int:
    if not RULE_LOADER_PATH.exists():
        print(f"错误：rule-loader.py 未找到：{RULE_LOADER_PATH}", file=sys.stderr)
        return 1

    try:
        test_ttl_expires_after_one_hour()
        test_backward_compatible_with_list_cache()
        test_no_session_id_returns_all()

        print("\n[OK] 全部 rule-loader TTL 测试通过！")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败：{e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n[FAIL] 意外错误：{e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
