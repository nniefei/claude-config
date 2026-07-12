"""共享的 pattern 加载工具。

提供统一的 SECRET_PATTERNS 和 entropy_recheck 加载逻辑，
供 bash-safety-wrapper / write-safety / mcp-safety 使用。

注意：各 hook 仍保留各自的 fallback 内联副本（容错设计），
本模块仅减少重复代码，不改变 fail-safe 语义。
"""
import importlib.util
import re
import sys
from pathlib import Path


def load_secret_patterns(fallback_patterns=None):
    """从 _shared_patterns.py 加载 SECRET_PATTERNS，失败时回退到内联定义。

    Args:
        fallback_patterns: 回退模式元组，默认为标准 5 种模式
    """
    shared_path = Path(__file__).resolve().parent / "_shared_patterns.py"
    try:
        spec = importlib.util.spec_from_file_location("_shared_patterns", shared_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["_shared_patterns"] = module
        spec.loader.exec_module(module)
        return module.SECRET_PATTERNS
    except Exception:
        if fallback_patterns is not None:
            return fallback_patterns
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


def load_entropy_recheck():
    """加载 passes_entropy_recheck；不可用时回退为恒 True（维持命中，不做复核）。"""
    try:
        import _shared_patterns
        return _shared_patterns.passes_entropy_recheck
    except Exception:
        return lambda label, content: True


# 标准回退模式（供各 hook 使用）
STANDARD_FALLBACK_PATTERNS = (
    ("sk-* 风格密钥", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("GitHub 个人访问令牌", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("AWS 访问密钥 ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Bearer 令牌", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("凭据赋值", re.compile(
        r"(?i)(?:api[_-]?key|secret|password|auth[_-]?token|access[_-]?token)"
        r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_/+=.\-]{16,}"
    )),
)
