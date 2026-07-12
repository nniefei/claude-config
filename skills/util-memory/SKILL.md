---
name: util-memory
description: 查看和维护 memory 文件，清理过期记录
user-invocable: true
argument-hint: "list|clean|show [文件名]"
depends-on: []
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
version: 1.1.0
last-updated: 2026-06-15
---

# Memory 维护 Skill

> 查看、管理和清理 memory 文件，保持知识库精简有效。
> 以下简写 `memory/` 均指系统提供的项目 memory 路径（每次会话系统会自动告知当前路径）。

## 使用方式

| 命令 | 说明 |
|:---|:---|
| `/util-memory list` | 列出所有 memory 文件及大小 |
| `/util-memory show [文件名]` | 查看指定 memory 文件内容 |
| `/util-memory clean` | 检查并清理过期/过大的 memory 文件 |
| `/util-memory` | 无参数时等同于 `list` |

## 执行步骤

### 子命令：`list`（也是无参数默认行为）

1. 使用 `Glob` 扫描 `memory/` 下所有 `.md` 文件（含子目录）
2. 使用 `Bash` 获取每个文件的行数
3. 输出格式：
   ```
   📂 Memory 文件列表：

   | 文件 | 行数 | 状态 |
   |:---|:---|:---|
   | MEMORY.md | 18 行 | ✅ 正常 |
   | debugging.md | 156 行 | ⚠️ 超限(>150) |
   | sessions/ | 12 个文件 | ✅ 正常 |
   ```

### 工具失败兜底

当 `Glob` 因本地 `rg` 缺失、路径异常等原因失败时，改用 `Bash` 执行只读 Python 脚本完成同等扫描：
- 使用 `pathlib.Path.rglob('*.md')` 列出 memory 文件
- 使用 Python 统计行数、文件数量和 sessions/ 数量
- 只允许读取、统计和生成报告，不修改、不删除任何 memory 文件
- `clean` 子命令仍必须经过用户确认后才允许使用 `Edit` 修改文件

### 子命令：`show [文件名]`

1. 支持模糊匹配（如 `debug` 匹配 `debugging.md`）
2. 使用 `Read` 读取文件内容
3. 展示完整内容给用户

### 子命令：`clean`

执行以下清理检查：

#### 1. 检查单文件大小

对每个 memory 文件（`MEMORY.md` 除外）检查行数：
- **≤ 150 行**：正常，跳过
- **> 150 行**：标记为需要清理

#### 2. 检查 sessions/ 文件数量

- **≤ 30 个**：正常
- **> 30 个**：报告超限，建议用户运行 `/util-session` 管理 sessions/

#### 3. 检查过时内容

读取各文件，识别可能过时的记录：

**基于日期的过期检测（参考建议）**：
- 识别条目中的明确日期标记（如"2026-06-05"、"2026-05-22 实测数据"）
- 计算距今天数，参考以下过期策略（**仅作参考，不自动执行**）：
  - **30 天规则**：超过 30 天的条目，建议检查是否仍有效
  - **90 天规则**：超过 90 天的条目，建议归档

**注意**：v2.13.0 起 Memory 完全交由 Claude Code 原生机制处理（写入、去重、按相关性召回），本 Skill 的过期检测为辅助参考，不自动执行。实际清理需用户确认。

**基于语义的过时检测（辅助）**：
- `debugging.md`：问题已在代码中修复、或涉及的依赖已升级到不再复现的版本
- `dependencies.md`：已升级解决的兼容问题，或项目已不再使用该依赖
- `decisions.md`：已被后续决策明确推翻或替代的旧决策

#### 4. 输出清理报告

```
🧹 Memory 清理报告

### 过期条目（基于日期）
- `debugging.md`：2 条超过 30 天且标记"已解决"
  - "Windows_Python_subprocess_冷启动开销" (24 天前，标记"未过时") → 保留
  - "session-start.py_70秒卡死" (16 天前，标记"✅ 已解决") → 保留（未超 30 天）
- `decisions.md`：0 条超过 90 天

### 需要清理的文件
- `debugging.md` (156 行 > 150 行上限)
  - 建议：合并 3 条相似记录，归档 2 条已解决记录
- `sessions/` (33 个文件 > 30 个上限)
  - 建议：运行 `/util-session` 管理 sessions/，本 Skill 不直接删除 session 文件

### 不需要清理的文件
- MEMORY.md (18 行) ✅
- conventions.md (42 行) ✅

是否执行清理？（归档的文件将移动到 memory/archive/）
```

#### 5. 确认后执行

使用 `AskUserQuestion` 确认后：
- **过期条目归档**：
  - 创建 `memory/archive/` 目录（如果不存在）
  - 将过期条目移动到按月命名的归档文件（如 `archive/debugging-2026-05.md`）
  - 从原文件中删除已归档的条目
  - 更新 `MEMORY.md` 索引，移除已归档条目的指针
- **超大文件精简**：使用 `Edit` 合并/删除过时条目
- **sessions/ 超限**：仅报告并建议运行 `/util-session`，不直接删除 session 文件
- 完成后输出清理结果摘要：
  ```
  ✅ 清理完成
  
  - 归档 3 条过期记录到 archive/debugging-2026-05.md
  - debugging.md：156 行 → 98 行
  - 已更新 MEMORY.md 索引
  ```

## 与其他 Skill 的关系

| Skill | 管理内容 | 关系说明 |
|:---|:---|:---|
| `/util-session` | `memory/sessions/` | session 文件数量超限时，本 Skill 的 clean 只检测并报告，由 util-session 管理 |
| `/util-init` | `memory/MEMORY.md` 技术栈部分 | init 写入技术栈，本 Skill 管理整体 Memory 健康度 |

## 注意事项

- **MEMORY.md 特殊处理**：上限为 200 行（非 150 行），且只做合并不做删除
- **清理前先备份**：对超大文件清理前，先展示要删除/合并的内容让用户确认
- **sessions/ 清理**：本 Skill 只报告超限，不删除 session 文件；由 `/util-session` 管理
- **不自动执行**：`clean` 只生成报告和建议，实际清理需用户确认
