---
version: 1.0.0
last-updated: 2026-05-22
---

# Skill 组织规范

> **自动加载触发**：当需要新增或修改 Skill 时，本文件自动加载到执行上下文。
>
> **触发条件**：创建或编辑 SKILL.md 文件（路径：`skills/*/SKILL.md`）
>
> **加载时机**：在执行 Write/Edit 前检查目标文件是否为 SKILL.md，是则加载；同一会话内仅加载一次

## 命名前缀

| 前缀 | 用途 | 示例 |
|:---|:---|:---|
| `dev-` | 开发工具 | `dev-review` |
| `util-` | 通用工具 | `util-check`, `util-safety` |

新增 Skill 时：前缀必选、目录名与 frontmatter `name` 一致、`rules/` 存规范文档不用前缀。

## Frontmatter 规范

每个 Skill 的 `SKILL.md` 必须包含以下 frontmatter 字段：

```yaml
---
name: [skill-name]                    # 必选：与目录名一致
description: [简短描述]                # 必选：一句话说明功能
user-invocable: true/false            # 必选：是否用户可调用
argument-hint: "[参数提示]"            # 可选：参数格式说明
depends-on:                           # 可选：依赖的其他 Skill
  - [skill-name-1]
  - [skill-name-2]
allowed-tools:                        # 必选：允许使用的工具列表
  - Read
  - Write
  - ...
---
```

### 字段说明

- **name**：Skill 的唯一标识，必须与目录名完全一致
- **description**：简短描述，用户在 Skill 列表中看到的说明
- **user-invocable**：是否允许用户直接调用（`true` 表示可用 `/skill-name` 调用）
- **argument-hint**：参数格式提示，例如 `"start|save|stop|load [文件名]|list"`
- **depends-on**：该 Skill 依赖的其他 Skill 列表（用于检测循环依赖和调用链深度）
- **allowed-tools**：该 Skill 允许使用的工具，严格限制权限范围

## 依赖关系声明

### 依赖规则

- **depends-on 字段**：列出该 Skill 直接依赖的其他 Skill
- **空列表**：表示该 Skill 没有依赖其他 Skill
- **示例**：
  ```yaml
  depends-on:
    - util-safety      # 示例：某 Skill 依赖安全守卫
  ```

### 依赖检查

`util-check` 会自动检查：
1. **循环依赖**：A → B → C → A 的环形依赖，严禁出现
2. **调用链深度**：最长依赖链不能超过 3 层
3. **悬空依赖**：depends-on 中引用的 Skill 必须存在
4. **深度计算**：无依赖的 Skill 深度为 1；依赖链 A → B → C → D 时 depth(A) = 4

发现深度超过 3 层时，优先移除不必要依赖；确实无法避免时，在 SKILL.md 中说明原因。

## 配置变更自检

对 `.claude/` 配置做结构性调整后，按四个维度自检：

1. **结构完整性** — SKILL.md 有效 frontmatter，name 与目录名一致
2. **引用一致性** — CLAUDE.md 中路径与实际对应，无残留
3. **内容规范性** — 术语统一，格式正确
4. **功能可用性** — 系统识别全部 slash skill

### 自检工具

运行 `/util-check` 自动执行上述四个维度的检查。

## Skill 间的协作模式

| 场景 | 推荐 Skill 组合 | 执行顺序 | 依赖关系 |
|:---|:---|:---|:---|
| 日常开发 | 自动积累 memory | 自动 | 无依赖 |
| 系统检查 | util-check | 按需执行 | 无依赖 |

