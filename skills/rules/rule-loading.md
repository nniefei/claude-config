---
version: 1.1.1
last-updated: 2026-07-10
---

# 规则按需加载机制

> **自动加载触发**：本文件由 CLAUDE.md 引用，作为规则加载机制的详细说明文档。
>
> **加载时机**：AI 需要了解规则加载机制的详细规则时加载（通常在会话初始化或遇到规则加载问题时）

## 实际运作方式

规则加载由 `rule-loader.py` hook 自动注入为主，AI 主动 Read 为兜底。

### hook 注入（主路径）

`rule-loader.py` 在以下事件自动注入规则：
- **UserPromptSubmit**：根据用户输入推断模式，注入对应规则
- **PreToolUse**：注入工具相关规则

### AI 主动 Read（兜底）

当 hook 注入失败或规则不在上下文时，AI 主动 Read 对应规则文件。

## 触发场景与加载文件映射

| 触发场景 | 加载文件 |
|:---|:---|
| 进入标准/静默模式 | `skills/rules/core-principles.md` + `skills/rules/workflow.md` + `skills/rules/karpathy-guidelines.md`（含第5条「失败模式自救」常驻） |
| 执行 git 写操作 | `skills/rules/git-safety.md` |
| 执行 `git commit` | 上述 + 追加「临提交前失败模式自查」软提示（rule-loader.py 内联，非独立 rule 文件） |
| 新增或修改 Skill | `skills/rules/skill-org.md` |
| 引入第三方依赖（装包意图） | 软提示：karpathy full 第8条（rule-loader.py `detect_dependency_hint`） |
| 调试 / 修 bug | 软提示：karpathy full 第5+第7条（rule-loader.py `detect_debug_hint`） |
| 接手陌生代码库 | 软提示：karpathy full 第1条（rule-loader.py `detect_unfamiliar_codebase_hint`） |

> **常驻 vs 软提示的切分依据**：karpathy 完整版增量的「Should 类正向步奏」（先读后写 / 验证用测试 / 调试 / 依赖管理）按场景信号浮现，不进常驻——避免平时不相关的「教科书」条款全程占用上下文并产生规则噪声；而「Don't 类紧箍咒」（失败模式）则进常驻第5条。软提示均 per-session 抑制一次，与 `detect_review_memory_hint` 同范式。

## 重新加载条件

- 发生上下文压缩
- 规则文件被修改
- 用户明确要求重读
- AI 无法确认规则内容仍在有效上下文中

---

## 设计理念

### 为什么按需加载

1. **减少 token 消耗**：只在需要时加载规则，避免每次会话都加载全部规则
2. **提升响应速度**：减少不必要的文件读取操作
3. **降低上下文压力**：长会话中不会被不相关的规则占用上下文窗口

### Fail-safe 原则

当无法确定规则是否仍在上下文中时，**优先选择重新加载**而非冒险使用可能过期的规则。重载的成本（几百 tokens）远低于执行错误的成本（需要重做）。
