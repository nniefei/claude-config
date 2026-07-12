"""安全钩子共享常量 —— 单点维护，多处引用。

本模块被 write-safety.py / mcp-safety.py / skills-health-check.py
通过 importlib 动态加载。修改 SECRET_PATTERNS 时只需改此文件。
"""
import math
import re

# 新增宽模式（无强前缀、可能误伤合法代码）时，
# 必须同步添加标签到 _ENTROPY_RECHECK_LABELS。
# 高置信前缀模式（sk-/ghp_/AKIA/bearer）不需复核。
SECRET_PATTERNS = (
    ("sk-* 风格密钥", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("GitHub 个人访问令牌", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("AWS 访问密钥 ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Bearer 令牌", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("凭据赋值", re.compile(
        r"(?i)(?:api[_-]?key|secret|password|auth[_-]?token|access[_-]?token)"
        r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_/+=.\-]{16,}"
    )),
)

# P2：仅"凭据赋值"宽模式需要熵复核——它会误伤 password_hash_algorithm = "argon2id_v19_config"
# 这类长但低熵的合法标识符。高置信模式（sk-/ghp_/AKIA/bearer）有强前缀，不复核。
_ENTROPY_RECHECK_LABELS = frozenset({"凭据赋值"})

# 香农熵阈值（bits/char）。真随机密钥（base62）熵接近 5.0；英文短语/snake_case
# 标识符（含 argon2id_v19_config、correct_horse_battery_staple 等）实测在 3.4~3.8，
# 极少超过 4.0。取 4.0 作为分界：高于此判为疑似真密钥维持命中，低于此撤销命中。
_ENTROPY_THRESHOLD = 4.0

# 从"凭据赋值"匹配中抽取赋值号右侧的值，用于算熵。
_ASSIGNMENT_VALUE_RE = re.compile(
    r"[:=]\s*[\"']?([A-Za-z0-9_/+=.\-]{16,})",
)


def shannon_entropy(s: str) -> float:
    """字符串的香农熵（bits/char）。空串返回 0。"""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def passes_entropy_recheck(label: str, content: str) -> bool:
    """对需要熵复核的标签，判断匹配值是否像真密钥（高熵）。

    返回 True = 维持命中（高熵，疑似真密钥）；False = 撤销命中（低熵，疑似误报）。
    不需要复核的标签一律返回 True（维持原行为）。
    """
    if label not in _ENTROPY_RECHECK_LABELS:
        return True
    # 取所有赋值右值，只要有一个高熵就维持命中
    for m in _ASSIGNMENT_VALUE_RE.finditer(content):
        if shannon_entropy(m.group(1)) >= _ENTROPY_THRESHOLD:
            return True
    return False
