#!/usr/bin/env python3
"""Rule-loader hook：根据当前操作向 AI 上下文注入相关规则 markdown。
通过 stdin 的 hook_event_name 字段路由。

支持的事件：
  - PreToolUse：检查 tool_name + tool_input 决定注入哪些规则
  - UserPromptSubmit：检测模式信号（silent/standard/quick），记录推断，
    可选注入 workflow.md
  - UserPromptExpansion：检测 /util-* 斜杠命令（v2.12 起不再注入，skill-boundaries.md 已删除）

输出（按 Claude Code hook 协议）：
  - exit 0（永不阻断）
  - stdout：含 hookSpecificOutput.additionalContext 的 JSON（v2.1.9+）
  - stderr：调试行 "[rule-loader] 已加载：..."（在对话记录中可见，exit 0 时 AI 忽略）

Fail-safe：任何内部错误记录到 stderr 并返回 0。永不阻断用户工作。
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows GBK 终端强制 UTF-8（规则文件含 CJK/emoji）
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RULES_DIR = Path.home() / ".claude" / "skills" / "rules"
LOGS_DIR = Path.home() / ".claude" / "logs"
MODE_LOG_PATH = LOGS_DIR / "mode-transitions.jsonl"

VERBOSE = os.environ.get("CLAUDE_RULE_LOADER_VERBOSE") == "1"

# v2.4 起静默模式仅由显式前缀 [silent]/[静默] 或 /silent 命令触发。
# 本列表保留用于"软提示"功能：用户输入含这些关键词时，
# 不进入静默模式（仍为 standard），但输出提醒"如需静默请加 [silent] 前缀"。
# 若确认不再需要软提示引导，可删除此列表及相关分支。
SILENT_MODE_KEYWORDS = [
    "帮我自己搞定", "不要问我", "自己来", "全程不用问",
    "你自己决定", "别问了直接做", "你看着办", "直接做就行",
]

FILE_OPERATION_KEYWORDS = [
    "修改", "改一下", "改下", "修复", "重构", "实现", "添加", "新增",
    "删除", "调整", "优化", "重写", "迁移", "升级",
    "审阅", "排查", "检查", "分析", "诊断", "review",
]

# B3：审阅/排查类任务专属关键词——命中则额外浮现「先读 memory 正文」提醒。
# 与 FILE_OPERATION_KEYWORDS 区别在于缩小到"评判性任务"：排除"修改/重构/实现"
# 这类执行性动词，只保留会触发"翻老决策、查豁免项"的审阅/排查类词。
# 根因：conventions「审阅前必先读memory」已反复犯，因审阅任务虽会被拉到 standard
# 模式注入通用规则，但注入内容里没有针对 memory 正文的明确提醒，AI 仍靠 MEMORY.md
# 索引行（不够）→ 照通用清单逐项套 → 把已豁免/已拍板项当问题重提。此项把软约束升
# 级为"准主动浮现"——每会话首次审阅类 prompt 自动推一次读正文提示。
MEMORY_READ_KEYWORDS = [
    "审阅", "审查", "审 核", "排查", "检查", "体检", "全面审", "安全审",
    "找问题", "找bug", "系统性看", "health-check", "review",
]
# 复用规则去重缓存的伪 rule 名做 per-session「本会话已浮现过」判定
_REVIEW_HINT_SENTINEL = "__review_memory_read_hint__"

# v2.x：full 版"教科书"类条款按场景自动浮现（不进常驻，守 simple 的 token 简洁）。
# 设计依据：Rule 擅长 Don't（紧箍咒，常驻），Should 类正向步奏宜按场景触发而非
# 常驻——否则只在特定时刻才相关的条款会全程占用上下文且产生"规则噪声"。
# 每组独立哨兵 + per-session 抑制一次，与 detect_review_memory_hint 同范式。
_DEPENDENCY_HINT_KEYWORDS = [
    "pip install", "pip3 install", "npm install", "npm i ", "pnpm add",
    "pnpm install", "yarn add", "yarn install", "cargo add", "cargo install",
    "go get", "go install", "brew install", "gem install", "composer require",
    "加入依赖", "引入依赖", "装个包", "装个库", "引入.*库", "添加依赖",
    "dependencies", "devdependencies", "requirements.txt", "package.json",
    "go.mod", "cargo.toml", "pom.xml",
]
_DEP_HINT_SENTINEL = "__dep_management_hint__"

_DEBUG_HINT_KEYWORDS = [
    "修bug", "修 bug", "修个bug", "修这个bug", "debug", "调试", "排错",
    "报错", "出错", "失败", "异常", "为什么不", "为啥不", "卡住", "崩了",
    "栈", "stack", "trace", "复现",
]
_DEBUG_HINT_SENTINEL = "__debug_hint__"

_UNFAMILIAR_CODEBASE_KEYWORDS = [
    "新项目", "接手", "陌生代码", "陌生项目", "第一次看", "刚拿到",
    "没见过的代码", "不熟的代码", "不了解的代码", "通读", "摸底",
]
_UNFAMILIAR_CODEBASE_SENTINEL = "__unfamiliar_codebase_hint__"

# v2.x：git commit 信号触发——临提交前失败模式自查（full 第10条）。
# 与常驻的 karpathy 第5条互补：常驻管日常"diff 不该变大时刻警觉"，提交点是硬性自查关口。
# 仅命中 ``git commit`` 时附加（非全部 git 写操作），避免 push/merge 等也带这段噪声。
_GIT_COMMIT_PATTERN = re.compile(
    r"(^|[;&|()\s])git\s+commit\b", re.IGNORECASE
)
_COMMIT_FAILURE_MODE_NOTICE = (
    "\n\n[rule-loader] 即将 git commit。临提交前按 karpathy 第10条自查这一把改动："
    "**厨房水槽**（顺手改了无关文件）？**乐观路径**（漏了 500/异常分支）？"
    "**错误抽象**（只用了两次就抽象）？**失控重构**（改动级联到多个文件）？"
    "有任一项先列出来，别带着附带伤害提交。"
)

SILENT_EXPLICIT_PREFIX = re.compile(r"^\s*\[(?:silent|静默)\]", re.IGNORECASE)

# P3: /silent 斜杠命令等价于 [silent] 前缀
SILENT_COMMAND_PATTERN = re.compile(r"^\s*/\s*silent\b", re.IGNORECASE)

FILE_EXT_PATTERN = re.compile(
    r"\.(?:py|ts|tsx|js|jsx|vue|md|json|yaml|yml|html|css|scss|less|"
    r"go|rs|java|kt|swift|cpp|c|h|cs|rb|php|sql|toml|xml|sh|bash|"
    r"ps1|psm1|dockerfile|tf|hcl)\b",
    re.IGNORECASE,
)
PATH_PATTERN = re.compile(r"(?:^|\s)(?:[a-z]:[\\/]|\.[\\/]|src[\\/]|\.\.[\\/])", re.IGNORECASE)

GIT_WRITE_PATTERN = re.compile(
    r"(^|[;&|()\s])git\s+(?:commit|push|merge|rebase|reset|clean|branch|checkout|restore|stash|tag|add)",
    re.IGNORECASE,
)

MEMORY_FILENAMES: set[str] = set()  # v2.13: memory 规则已回归原生，不再注入自定义规则

UTIL_SKILL_NAMES = {"util-check", "util-memory", "util-init", "util-session"}

# P1: 文件级同会话去重缓存。hook 每次独立进程启动，模块级变量无法跨调用持久，
# 改用 JSON 文件记录每个 session 已注入的规则名。
_DEDUP_CACHE_PATH = LOGS_DIR / "rule-injection-cache.json"

# 缓存 session 上限。早期靠 Stop 事件清理，但 Stop hook 从未在 settings 注册
# （其 schema 不兼容 additionalContext），清理实际从不触发，缓存会随 session 无界增长。
# 改为写回时按插入顺序裁旧（dict 在 Py3.7+ 保序，首次写入的 session 排在最前），
# 不依赖任何事件，无界增长自愈。Stop 相关死代码已于第三次审阅 S3 移除。
_MAX_CACHED_SESSIONS = 200


def _read_dedup_cache():
    """读取去重缓存；任何错误返回空 dict。"""
    try:
        if _DEDUP_CACHE_PATH.exists():
            return json.loads(_DEDUP_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_dedup_cache(data):
    """原子写入去重缓存。"""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _DEDUP_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_DEDUP_CACHE_PATH)
    except Exception:
        pass


def should_inject(session_id, rule_names):
    """返回 rule_names 中尚未向该 session 注入过（或已过期）的子集。

    session_id 为空时全量返回（安全回退）。
    缓存值为 {rule_name: timestamp}，超过 1 小时视为过期需重新注入。
    这样既解决非通知型上下文压缩（token 超限自动丢弃不通知），
    也解决长会话规则自然老化问题。
    """
    if not session_id:
        return list(rule_names)

    cache = _read_dedup_cache()
    session_cache = cache.get(session_id, {})

    # 向后兼容：旧缓存是列表，迁移为字典
    if isinstance(session_cache, list):
        now = datetime.now(timezone.utc).timestamp()
        session_cache = {name: now for name in session_cache}

    now = datetime.now(timezone.utc).timestamp()
    TTL_SECONDS = 3600  # 1 小时

    new_rules = []
    for rule_name in rule_names:
        last_injected = session_cache.get(rule_name)
        if last_injected is None or (now - last_injected) > TTL_SECONDS:
            new_rules.append(rule_name)
            session_cache[rule_name] = now

    if new_rules:
        cache[session_id] = session_cache
        if len(cache) > _MAX_CACHED_SESSIONS:
            for stale_id in list(cache)[:-_MAX_CACHED_SESSIONS]:
                del cache[stale_id]
        _write_dedup_cache(cache)

    return new_rules


_USAGE_STATS_PATH = LOGS_DIR / "rule-usage-stats.json"


def _record_rule_usage(rule_names):
    """记录规则使用统计。

    统计格式：
    {
        "workflow.md": {
            "count": 156,
            "last_used": "2026-06-15T17:30:45Z",
            "first_used": "2026-05-20T10:15:30Z"
        },
        ...
    }
    """
    if not rule_names:
        return

    try:
        # 读取现有统计
        stats = {}
        if _USAGE_STATS_PATH.exists():
            try:
                stats = json.loads(_USAGE_STATS_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass

        now = datetime.now(timezone.utc).isoformat()

        # 更新统计
        for rule_name in rule_names:
            if rule_name not in stats:
                stats[rule_name] = {
                    "count": 0,
                    "first_used": now,
                    "last_used": now
                }

            stats[rule_name]["count"] += 1
            stats[rule_name]["last_used"] = now

        # 原子写入
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _USAGE_STATS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_USAGE_STATS_PATH)

    except Exception:
        # Fail-safe：统计失败不影响规则加载
        pass


def load_rule(name):
    """读取规则 markdown 文件；任何错误返回空字符串。"""
    try:
        return (RULES_DIR / name).read_text(encoding="utf-8")
    except Exception:
        return ""


def detect_pretooluse(payload):
    """返回 PreToolUse 事件需注入的规则文件名列表。"""
    rules = []
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "").lower()
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if not isinstance(tool_input, dict):
        return rules

    if tool_name == "bash":
        command = str(tool_input.get("command") or "")
        if GIT_WRITE_PATTERN.search(command):
            rules.append("git-safety.md")

    elif tool_name in ("write", "edit"):
        file_path = str(tool_input.get("file_path") or "")
        normalized = file_path.replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1] if "/" in normalized else normalized
        if basename == "SKILL.md":
            rules.append("skill-org.md")

    elif tool_name == "skill":
        # v2.12: skill-boundaries.md 已降级为参考文档，不再自动注入
        pass

    return rules


def infer_mode(prompt):
    """返回从用户提示内容推断的 (mode, hit_keywords)。"""
    if SILENT_EXPLICIT_PREFIX.search(prompt) or SILENT_COMMAND_PATTERN.search(prompt):
        return "silent", ["explicit-prefix"]
    soft_hits = [k for k in SILENT_MODE_KEYWORDS if k in prompt]
    if soft_hits:
        return "standard", ["soft-hint:" + ",".join(soft_hits)]
    if FILE_EXT_PATTERN.search(prompt) or PATH_PATTERN.search(prompt):
        return "standard", []
    for kw in FILE_OPERATION_KEYWORDS:
        if kw in prompt:
            return "standard", []
    return "quick", []


def detect_userpromptsubmit(payload):
    """返回 UserPromptSubmit 事件的 (rules, inferred_mode, hit_keywords)。"""
    prompt = str(payload.get("prompt") or "")
    mode, hits = infer_mode(prompt)
    rules = []
    if mode in ("standard", "silent"):
        # 标准/静默模式注入 core-principles.md（全局底线）+ workflow.md
        rules.append("core-principles.md")
        rules.append("workflow.md")
        rules.append("karpathy-guidelines.md")
    return rules, mode, hits


def detect_userpromptexpansion(payload):
    """返回 UserPromptExpansion 事件（斜杠命令）的规则文件名列表。"""
    rules = []
    command_name = str(payload.get("command_name") or "")
    normalized = command_name.lstrip("/").strip()
    if normalized == "silent":
        rules.append("workflow.md")
    elif normalized in UTIL_SKILL_NAMES:
        # v2.12: skill-boundaries.md 已降级为参考文档，不再自动注入
        pass
    return rules


def soft_hint_context(hits):
    if any(str(hit).startswith("soft-hint:") for hit in hits):
        return (
            "\n\n[rule-loader] 检测到你可能想进入静默模式（命中关键词）。"
            "如需进入，请在下一条 prompt 前加 `[silent]` 前缀。本次保持标准模式。"
        )
    return ""


def detect_review_memory_hint(prompt, session_id):
    """B3：审阅/排查类任务命中 → 返回额外浮现的 memory 正文提醒文案；否则 None。

    per-session 抑制：复用 should_inject 的伪 rule 名哨兵，本会话首次审阅才浮现一次，
    之后同 session 不再重复（与规则注入去重一致）。
    """
    if not any(kw in prompt for kw in MEMORY_READ_KEYWORDS):
        return None
    # per-session 抑制 —— 首次才记一次，避免每条 prompt 都推送
    new = should_inject(session_id, [_REVIEW_HINT_SENTINEL])
    if not new:
        return None  # 本会话已浮现过
    return (
        "\n\n[rule-loader] 检测到审阅/排查类任务关键词。动手前请 **先 Read 正文**："
        "`conventions.md`（尤其豁免项：settings.json token 明文=CC-Switch产物勿提、"
        "commit格式、hook拦截后请示）+ `decisions.md`（已拍板设计：env模式、ask弹窗、"
        "grants deny硬阻断、版本号SSOT）。**只读 MEMORY.md 索引不够**——历史上已反复因"
        "只看索引、照通用清单逐项套，把已豁免/已拍板项当问题重提。"
    )


def detect_dependency_hint(prompt, session_id):
    """检测到引入依赖意图 → 返回 full 第8条精简提醒；否则 None。per-session 抑制一次。"""
    if not any(kw in prompt.lower() for kw in _DEPENDENCY_HINT_KEYWORDS):
        return None
    if not should_inject(session_id, [_DEP_HINT_SENTINEL]):
        return None
    return (
        "\n\n[rule-loader] 检测到引入依赖意图。按 karpathy 第8条：**先查项目本身/标准库能否"
        "实现**（如用原生 `crypto.randomUUID()` 替代引入 `uuid` 包），能不自建不自建；"
        "确需引入时**必须说明原因**，让这个选择可见，而不是偷偷塞进依赖清单。"
    )


def detect_debug_hint(prompt, session_id):
    """调试/修 bug 场景 → 返回 full 第5+第7条精简提醒；否则 None。per-session 抑制一次。"""
    if not any(kw in prompt.lower() for kw in _DEBUG_HINT_KEYWORDS):
        return None
    if not should_inject(session_id, [_DEBUG_HINT_SENTINEL]):
        return None
    return (
        "\n\n[rule-loader] 检测到调试/修 bug 场景。按 karpathy 第5+第7条：**调查，别猜**——"
        "先读完整错误和堆栈、改动前先复现问题、一次只改一处；修 bug 时**先写一个会失败的"
        "测试**看它失败再修（证明修的是根因而非症状）；别用 null 检查掩盖意外 null——查它"
        "为什么是 null。"
    )


def detect_unfamiliar_codebase_hint(prompt, session_id):
    """接手陌生代码库场景 → 返回 full 第1条精简提醒；否则 None。per-session 抑制一次。"""
    if not any(kw in prompt.lower() for kw in _UNFAMILIAR_CODEBASE_KEYWORDS):
        return None
    if not should_inject(session_id, [_UNFAMILIAR_CODEBASE_SENTINEL]):
        return None
    return (
        "\n\n[rule-loader] 检测到接手陌生代码库场景。按 karpathy 第1条「先读后写」：**通读**"
        "要改动的文件（读不是浏览），复制项目已有模式、核对现有依赖（项目全用 fetch 别"
        "顺手写 axios）；找不到可循的模式就**提问**，而不是自己瞎猜。动手前先输出一份"
        "「代码摸底报告」再说。"
    )


def emit_review_standalone_notice(event_name, extra_context):
    """兜底：命中软提示但本条无新规则可注入时，独立浮现提示（不带规则标题）。

    v2.x：现服务于多组软提示（memory 审阅 / 依赖管理 / 调试 / 陌生代码库），
    函数名保留 ``review`` 系历史命名，逻辑已通用化。
    """
    hook_output = {
        "hookEventName": event_name,
        "additionalContext": "[rule-loader] 软提示" + (extra_context or ""),
    }
    output = {"hookSpecificOutput": hook_output}
    print(json.dumps(output, ensure_ascii=False))


def emit_additional_context(rule_names, event_name, extra_context=""):
    """输出 hookSpecificOutput JSON，包含拼接后的规则内容。"""
    seen = set()
    ordered = []
    for name in rule_names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)

    if not ordered:
        return

    parts = [f"[rule-loader] 加载规则：{', '.join(ordered)}\n"]
    for name in ordered:
        content = load_rule(name)
        if content:
            parts.append(f"\n---\n\n# {name}\n\n{content}")

    additional_context = "\n".join(parts) + extra_context

    hook_output = {
        "hookEventName": event_name,
        "additionalContext": additional_context,
    }

    output = {"hookSpecificOutput": hook_output}
    print(json.dumps(output, ensure_ascii=False))
    if VERBOSE:
        print(f"[rule-loader] loaded: {', '.join(ordered)} (mode hint: 仅供 AI 参考)", file=sys.stderr)


def write_mode_log(prompt, mode, hits):
    """追加一条模式推断记录。失败静默。"""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # 对 prompt 前 80 字符进行 secret 打码
        preview = prompt[:80].replace("\n", " ")
        try:
            import importlib.util
            shared_path = Path(__file__).resolve().parent / "_shared_patterns.py"
            spec = importlib.util.spec_from_file_location("_shared_patterns", shared_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for label, pattern in module.SECRET_PATTERNS:
                preview = pattern.sub(f"***REDACTED:{label}***", preview)
        except Exception:
            pass  # 打码失败不阻断日志记录
        entry = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "source": "hook",
            "prompt_preview": preview,
            "inferred_mode": mode,
            "trigger_keywords": hits,
        }
        with MODE_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    try:
        try:
            payload = json.load(sys.stdin)
        except Exception as exc:
            print(f"[rule-loader] 解析 stdin 失败：{exc}", file=sys.stderr)
            return 0

        event = str(payload.get("hook_event_name") or "")
        rules = []
        extra_context = ""

        session_id = str(payload.get("session_id") or "")

        if event == "PreToolUse":
            rules = detect_pretooluse(payload)
            rules = should_inject(session_id, rules)
            # v2.x：git commit 信号 → 临提交前叠加失败模式自查提示
            tool_input = payload.get("tool_input") or payload.get("input") or {}
            if isinstance(tool_input, dict) and str(payload.get("tool_name") or "").lower() == "bash":
                if _GIT_COMMIT_PATTERN.search(str(tool_input.get("command") or "")):
                    extra_context = (extra_context or "") + _COMMIT_FAILURE_MODE_NOTICE
            # 兜底：命中 commit 自查提示但 git-safety.md 本会话已注入（无新规则）时，独立浮现一次
            if extra_context and not rules:
                emit_review_standalone_notice(event, extra_context)
                extra_context = ""
        elif event == "UserPromptSubmit":
            rules, mode, hits = detect_userpromptsubmit(payload)
            extra_context = soft_hint_context(hits)
            # B3：审阅/排查类关键词 → 追加「先读 memory 正文」准主动浮现提示
            review_notice = detect_review_memory_hint(
                str(payload.get("prompt") or ""), session_id
            )
            if review_notice:
                extra_context = (extra_context or "") + review_notice
            # v2.x：full 教科书类条款按场景自动浮现（依赖管理/调试/陌生代码库）
            prompt_text = str(payload.get("prompt") or "")
            for hint_fn in (
                detect_dependency_hint,
                detect_debug_hint,
                detect_unfamiliar_codebase_hint,
            ):
                hint = hint_fn(prompt_text, session_id)
                if hint:
                    extra_context = (extra_context or "") + hint
            write_mode_log(str(payload.get("prompt") or ""), mode, hits)
            rules = should_inject(session_id, rules)
            # 兜底：命中有软提示但本条无新规则可注入时，仍独立浮现一次提示
            if extra_context and not rules:
                emit_review_standalone_notice(event, extra_context)
                extra_context = ""
        elif event == "UserPromptExpansion":
            rules = detect_userpromptexpansion(payload)
            rules = should_inject(session_id, rules)

        if rules:
            _record_rule_usage(rules)
            emit_additional_context(rules, event, extra_context)

    except Exception as exc:
        print(f"[rule-loader] 错误（非阻塞）：{exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
