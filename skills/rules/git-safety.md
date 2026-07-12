---
version: 1.4.1
last-updated: 2026-06-10
---

# Git 操作安全约束

> **自动加载触发**：当需要执行任何 git 写操作时，本文件自动加载到执行上下文。
>
> **触发条件**：调用 Bash 工具执行 git 写操作（commit、push、merge、rebase、reset --hard、branch -d/-D、checkout --、stash drop/clear、clean -f、tag -d）
>
> **加载时机**：在执行 Bash 前检查命令是否为 git 写操作，是则加载；同一会话内仅加载一次
>
> ## 📋 提交前必须写 CHANGELOG
>
> **每次 `git commit` 前必须先更新 `CHANGELOG.md`**，并在 commit diff 中包含 CHANGELOG.md 的变更。这是硬性规范（CHANGELOG v2.6.0 确立，bash-safety-wrapper 有对应合规检查）。
>
> 违例的 commit 会被 bash-safety-wrapper 的 changelog 检查输出警告。
>
> **🚫 未经主人明确授权，以下 git 操作一律禁止执行！**

## 绝对禁止(无授权不可执行)

| 操作类型 | 禁止命令示例 | 风险说明 |
|:---|:---|:---|
| **分支删除** | `git branch -d/-D`, `git push origin --delete` | 可能丢失未合并的工作 |
| **commit** | `git commit`, `git commit --amend` | 提交内容和信息需主人确认 |
| **push** | `git push`, `git push --force` | 影响远程仓库,不可轻易撤销 |
| **rebase** | `git rebase`, `git rebase -i` | 重写历史,影响协作者 |
| **重置回退** | `git reset --hard`, `git checkout -- .`, `git checkout .` | 可能丢失未保存的修改 |
| **merge** | `git merge` | 可能引入冲突或非预期代码 |
| **标签管理** | `git tag -d`, `git push --delete tag` | 影响版本标记 |
| **暂存清理** | `git stash drop`, `git stash clear` | 丢失暂存的工作内容 |
| **强制覆盖** | `git clean -f/-fd` | 不可逆地删除未跟踪文件 |

## 子壳调用（SUBSHELL 分类）

以下间接执行路径被纳入 `SUBSHELL` 分类，默认 ask 弹窗确认：

- `bash -c` / `sh -c` / `zsh -c` / `fish -c`
- `pwsh -Command` / `powershell -Command`
- `pwsh -EncodedCommand` / `powershell -EncodedCommand`（隐藏命令）
- `bash <<EOF ... EOF`（heredoc）
- `xargs sh|bash`

**为什么 ask 确认**：这些路径能让 AI 通过包裹外层 shell 来绕过 `DANGEROUS_PATTERNS` 的直接匹配。ask 弹窗让用户确认，而非硬阻断。

**例外**：`python -c` / `node -e` / `eval` 等不在此列。

## 可自主执行(只读/安全操作)

以下操作**不影响仓库状态**,可以自由使用:
- `git status` / `git log` / `git diff` — 查看状态和历史
- `git branch` (不带 -d/-D) — 查看分支列表
- `git stash list` — 查看暂存列表
- `git remote -v` — 查看远程仓库信息
- `git blame` / `git show` — 查看代码归属和 commit 详情

## 需确认但风险较低的操作

以下操作会改变本地状态,但不影响远程仓库,执行前简要告知即可:
- `git add` — 将文件添加到暂存区(建议指定文件名,避免 `git add .` 或 `git add -A` 意外暂存敏感文件)
- `git checkout -b 分支名` — 创建并切换到新分支(不影响已有分支)
- `git stash` — 临时保存修改(不带 drop/clear)

## 需请求授权的操作流程

执行 git 写操作前，必须先向用户说明**意图、原因和影响文件**，再执行命令。Claude Code 的 Bash 工具会弹出确认窗，用户可在确认窗中批准或拒绝。

**输出格式**：
```text
[Git 操作]

我要执行：git commit -m "feat: 添加用户登录表单验证"
原因：登录表单验证功能已实现完毕
影响文件：src/components/LoginForm.tsx, src/utils/validation.ts
```

需要 add → commit → push 等多个 git 操作时，首次说明完整意图和所有步骤，后续可简化说明当前执行步骤。

用户拒绝操作后，停止执行并询问是否需要调整方案；同一操作被拒绝两次，不再重复询问。

## 授权机制：ask 优先，grant/env 作二线授权

`bash-safety-wrapper`（管 Bash）与 `write-safety`（管 Write/Edit）都支持带外授权（`.grants/<category>` 一次性文件或真实进程 env），但**默认交互流程不同**：

| Hook | 管辖 | 默认流程 | grant/env 使用场景 |
|:---|:---|:---|:---|
| `bash-safety-wrapper.py` | git commit/push、rm -rf、npm -g 等 Bash 命令 | 返回 PreToolUse `ask`，由主人在权限弹窗点 Allow/Deny | 仅在需要预授权、无弹窗可用，或 hook 明确提示时使用 |
| `write-safety.py` | CLAUDE.md / skills/ 等控制平面文件的 Write/Edit | 硬阻断（exit 2），防止敏感写入静默发生 | 按 hook 提示创建对应 `.grants/<category>` 或设置 env 后重试 |

**为什么改**（信任根改造）：旧版 bash hook 从命令文本里解析内联 marker（`CLAUDE_HOOK_APPROVED_GIT=1 git push`）。被约束的 AI 能自己拼这个 marker 实现**自我授权**——信任根放在了不可信的输入里。现在授权只能来自权限弹窗、带外 `.grants` 文件，或外层启动环境设的 env，AI 无法靠改写命令文本绕过。

**Bash 授权要点**：
- ⚠️ **命令内联 marker 已失效**：`CLAUDE_HOOK_APPROVED_GIT=1 git push` 不再被当作授权，会照常进入 ask/Allow 流程
- **默认不要要求主人先创建 `.grants/<category>`**：先按事前声明要求说明意图，然后直接调用命令，让权限弹窗处理 Allow/Deny
- grant/env 是二线方案：只有需要预授权、无弹窗可用，或 hook 明确提示时，才请主人创建 `.claude/.grants/<category>` 文件（**消费即删除**）或设置真实环境变量 `CLAUDE_HOOK_APPROVED_<CATEGORY>=1`
- 分类：`GIT`（commit/push/merge/rebase/reset --hard 等）、`DELETE`、`NETEXEC`、`PACKAGE`、`SENSITIVE`、`SUBSHELL`
- ⚠️ **`git branch -D`（强制删除）同时命中 `GIT` 与 `DELETE` 两个分类**，需**两个 grant 各建一个**（`! touch ~/.claude/.grants/git` + `! touch ~/.claude/.grants/delete`），缺一不可且不会被部分消费；`git branch -d`（普通删除）只需 `GIT`
- `git add`（非敏感文件）不在拦截清单，无需授权；仅 commit/push 等需要
- **命令归一化**：检测前会 `shlex` 取命令名 basename，`/bin/rm -rf`、`/usr/bin/git push`、`"rm" -rf`、`'git' push` 等变体同样被拦截（务实定位：防手滑 + 拦常见变体，不承诺防对抗性绕过）

## ⚠️ grant 文件本身受保护：AI 不可自建 grant

`.grants/<category>` 文件代表**主人的授权**。如果 AI 能自己创建 grant 文件，就等于自我授权——信任根又回到了 AI 手里（这正是信任根改造要堵死的）。所以：

- **AI 用 Write/Edit/Bash 写 `.grants/` 目录** → hook **一律 deny 硬阻断（exit 2）**，命令不执行。
- 此规则是**最高优先级**，先于一切其他检查，且**不被任何 grant/env 豁免**（用 grant 豁免"写 grant"会无限套娃）。
- **为什么用 deny 而非弹确认窗（ask）**：Claude Code 权限评估顺序是 `deny > ask > allow`，且 **bypass / 跳过权限模式会吞掉 ask**（实测：bypass 下 ask 静默放行）。唯有 `exit 2` 无视 allow 列表与权限模式，可靠拦截。
- **主人授权的正道**：在对话里输入 `! touch ~/.claude/.grants/<category>`——`!` 命令在主人的真实终端执行，**不经过任何 hook**，所以畅通无阻。
- 读 `.grants/`（`cat`/`ls`）不受影响，只拦写入。

**给 AI 的铁律**：被 hook 拦截后，**永远请示主人、等主人 `! touch` 授权**，绝不自己建 grant、绝不换工具绕过。详见 conventions memory「hook拦截后必须先请示_禁止绕过」。

## 事前声明要求

执行任何可能被安全 hook 拦截的操作前，**必须先在对话中**输出以下格式的声明，再调用 Bash/Write/Edit 工具：

```
[安全声明]
操作：<具体命令或文件路径>
原因：<为什么需要执行>
影响：<涉及的文件或范围>
风险：<潜在风险，如"仅本地"、"影响 CI/CD">
```

**禁止不声明就调用**。如果 AI 跳过声明直接调工具 → hook 拦截 → 用户看到无上下文的弹窗 → 体验极差。

**避免重复 hook 输出**：当操作被 hook 拦截时，hook stderr 已包含目标路径、拦截原因、授权方式等信息。此时 `[安全声明]` 仅需说明**操作意图和业务原因**，不必重复授权命令（hook 已展示）。未被拦截的操作按完整格式输出。

## 静默模式下的 git 约束

即使在 `[模式:独自巡猎]` 下,**git 写操作的授权要求不可豁免**。
遇到需要 commit / push / merge / rebase 等操作时,必须暂停并询问主人。

## 敏感文件保护

以下文件**禁止 commit 到版本控制**,如发现暂存区包含此类文件需立即提醒:
- `.env` / `.env.local` / `.env.production` — 环境变量(可能含密钥)
- `credentials.json` / `serviceAccount.json` — 服务凭证
- `*.pem` / `*.key` / `*.p12` — 证书和私钥
- `id_rsa` / `id_ed25519` — SSH 密钥
- 包含 `secret`、`password`、`token`、`api_key` 等关键词的配置文件

**处理方式**:
- 发现此类文件时使用 Level 3 紧急刹车
- 建议用户将其添加到 `.gitignore`
- 如果用户明确要求 commit,需二次确认并说明风险

## .gitignore 修改提醒

编辑 `.gitignore` 等同于修改"敏感文件保护"清单。当 AI 计划：

- 移除 `.gitignore` 中的某行条目
- 在 `.gitignore` 中新增 `!` 反否定规则（取消忽略）
- 整文件替换 `.gitignore`

**必须先告知主人**，说明：
- 修改的具体规则（diff 形式）
- 哪些文件可能因此被纳入版本控制
- 是否存在敏感信息（.env、密钥、token、credentials）泄漏风险

未经确认，不得静默修改 `.gitignore`。

## CI/CD 与容器配置修改授权

CI/CD pipeline 文件（`.github/workflows/*.yml`、`.gitlab-ci.yml`、`Jenkinsfile`、`azure-pipelines.yml`、`bitbucket-pipelines.yml`）与容器配置（`Dockerfile`、`docker-compose*.yml`、`kubernetes/*.yml`、`k8s/*.yml`）受 write-safety hook 保护，默认禁止 Write/Edit。

**修改前必须**：
1. 向主人说明：改什么、为什么、是否影响生产部署或安全策略
2. 主人确认后，需在**启动 Claude Code 的真实终端**中显式设置授权 marker（**在 Claude Code 会话内 Bash export 对 hook 子进程无效**）：

   ```bash
   # PowerShell
   $env:CLAUDE_HOOK_APPROVED_INFRA = "1"
   # bash
   export CLAUDE_HOOK_APPROVED_INFRA=1
   ```

3. 完成后建议清除该环境变量，恢复默认保护

**为什么不能在静默模式下豁免**：CI/CD 改动经常涉及凭证、部署目标、镜像标签，影响范围超出本地仓库；容器配置变更可能引入基础镜像漏洞或运行时权限提升。属于 workflow.md 中"强制刹车例外"条款。

`version` 元数据：保持文件 frontmatter 与改动日期同步。
