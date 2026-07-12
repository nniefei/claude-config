# Decisions Memory

## 运行模式实证_是default非bypass

**事实**：主人环境是 **default 权限模式**，不是 bypass。2026-06-04 实证：当前会话 transcript 首行 `{"type":"permission-mode","permissionMode":"default"}`，且每条消息均带 `"permissionMode":"default"`。

**为什么之前误判为 bypass**：旧记录据「`date`/`cp` 等命令不弹窗直接执行」推断为 bypass——这个推断**错了**。真相是这些命令被 `bash-safety-wrapper.py` 检查后返回 exit 0（hook 放行），default 模式信任 hook 放行结果直接执行、不再叠加弹窗。**hook 放行 ≠ bypass 放行**，两者表现都是「不弹窗」，但机制完全不同。

**How to apply**：
- 判定权限模式的**唯一可靠方法**是读会话 transcript 的 `permission-mode` 行，不要靠「某命令是否弹窗」反推（hook 放行会造成假象）。
- 既然是 default 模式，`permissionDecision: ask` 弹窗**可靠生效**（default 不吞 ask），这是下面危险命令改 ask 的前提。

## 危险Bash命令拦截_从deny改为ask弹窗

**决策**（2026-06-04）：危险 Bash 命令（git commit/push/merge、rm -rf、pip uninstall、npm -g、子壳等 DANGEROUS_PATTERNS）命中且无 grant 时，`bash-safety-wrapper.py` 从 `exit 2 硬阻断` 改为输出 `permissionDecision: ask` 的原生确认窗。

**为什么改**：主人诉求是「AI 可操作敏感操作，但必须在我授权之下」。deny 硬阻断逼主人手敲 `! touch grant`，体验差。ask 弹窗让主人**点 Allow 即放行**，零手敲。

**为什么现在可行**（旧记录认为 ask 不可靠，已被推翻）：
1. 环境是 default 模式（见 [[运行模式实证_是default非bypass]]），不吞 ask。
2. claude-code-guide + 官方文档确认：hook 的 ask **会压过** settings 的宽通配 allow（如 `Bash(git *)`）照常弹窗——hook 决策先于规则评估，只能收紧。
3. 实战已验证：`git tag -d` 命中模式 → 弹窗 → 主人点 Allow → 放行成功。

**实现**：`bash-safety-wrapper.py` `check_command()` 末尾，危险命中无 grant 分支输出 ask JSON + return 0（退出码 0 表 hook 正常，决策在 stdout JSON）。grant/env 批量授权通道保留不变（有 grant 仍静默放行）。

**已解决的权衡**：原生弹窗的「don't ask again」对 hook-ask 不生效，过去连续同类操作会重复弹窗。2026-06-11 已新增 `bash-audit-post.py`：PostToolUse 在危险 Bash 命令实际执行后写入 `source=post-exec` 审计，并记录 `session_id + 危险操作标签` 到 `logs/ask-approved-cache.json`；后续同 session 同操作标签由 `bash-safety-wrapper.py` 直接放行并写 `source=session-ask-memo` 审计。粒度按操作标签而非类别，避免 Allow 过 `git commit` 后自动放行 `git push`。

## grants写入保护_仍用deny硬阻断（未变）

**决策**：保护 `.grants/` 目录（防 AI 自建 grant 自我授权）时，hook 仍采用 **exit 2 (deny) 硬阻断**。**注意：上面危险命令改 ask，但这条 grants 写入保护不变，仍是 deny。**

**为什么 grants 保护不能也改 ask**：写 grant = 自我授权 = 信任根错位，必须由人带外授权，AI 永不可碰。即便 default 模式 ask 可靠，也不该给 AI「弹窗点 Allow 就能写 grant」的路径——那等于把信任根的钥匙递回 AI。grants 写入是唯一必须 100% 硬阻断、无任何豁免的操作。

**主人授权的正道**：`! touch ~/.claude/.grants/<category>`——`!` 命令走主人真实终端，不经过任何 hook，畅通无阻。

**How to apply**：
- 危险命令（git/rm/pip/npm 等）→ ask 弹窗，主人点 Allow 放行。
- 写 `.grants/` 目录 → exit 2 硬阻断，永不降级 ask。实现位置：`bash-safety-wrapper.py` 的 `writes_to_grants()` + `write-safety.py` 的 `is_grants_path()`，命中即 exit 2，最高优先级、无任何 grant/env 豁免。
- 控制平面文件（skills/、hooks/ 等）的 Write/Edit 仍需 `control-plane` grant 或 `CLAUDE_HOOK_APPROVED_CONTROL_PLANE=1`。
- 关联 conventions「hook拦截后必须先请示」。

## secret凭据赋值熵复核_阈值4.0偏向少误报

**决策**（2026-06-07）：`_shared_patterns.py` 的"凭据赋值"宽模式（`api_key|secret|password|...=值`）命中后，加香农熵复核——值的熵 ≥ 4.0 才维持命中（判为真密钥），低于 4.0 撤销命中（判为误报放行）。高置信前缀模式（sk-/ghp_/AKIA/bearer）不复核。

**为什么加**：宽模式会误伤合法代码，如 `api_key = "defaultplaceholder"`（熵 3.42）这类低熵占位/标识符。误报多了会让主人习惯性 grant 放行，反而削弱防护。

**阈值 4.0 的来源**：实测真随机 base62 密钥熵接近 5.0、16 位随机密钥熵恰好 4.0；英文短语/snake_case 标识符（argon2id_v19_config、correct_horse_battery_staple）实测 3.4~3.8。4.0 是分界。

**已知权衡（漏报方向）**：阈值偏向**少误报**，代价是低熵的真密钥可能漏报——如 16 位重复字符多的密钥（`aaaa...`熵=0）、或顺序串。这是 precision/recall 取舍，当前选 precision。**不要误以为熵复核能抓所有密钥**；高熵随机密钥才是它的强项。

**注意主正则的隐藏边界**：`password_hash_algorithm = "..."` 其实**主正则就不命中**（`password` 后跟 `_hash` 而非 `[:=]`），不是靠熵复核放行的。真正靠熵复核放行的是 `password = "低熵值"` 这种。验证熵逻辑的测试用例必须满足「主正则命中 + 值低熵」（见 health-check 的 `check_entropy_recheck`）。

**实现**：`_shared_patterns.py` 的 `passes_entropy_recheck(label, content)`；消费方 `write-safety.py` / `mcp-safety.py` 在 `pattern.search() and _passes_entropy_recheck()` 处接入。health-check 的 `check_entropy_recheck` 双向验证（阈值改 0 或改极大都能捕获）。

## hook共享代码_审计锁抽取但secret副本保留（区别对待）

**决策**（2026-06-07，第三次审阅 S1/S2）：两处跨 hook 重复代码，**一个收敛、一个保留**，看似矛盾，实有原则。

**S1 审计锁 → 收敛到 `_audit_log.py`**：`with_audit_log_lock` / `rotate_audit_log_if_needed` / `audit_log_line_count` 原在 bash-safety-wrapper.py 与 mcp-audit.py 逐字重复。它们是**纯文件锁工具，无容错诉求**——抽到共享模块，import 失败时调用方 fail-safe（mcp-audit 跳过审计；bash-safety 抛异常→上层 return 2，因其审计失败须 fail-closed）。

**S2 secret 内联副本 → 保留不动**：`write-safety.py` / `mcp-safety.py` 的 `_load_shared_patterns` 各带一份 SECRET_PATTERNS 内联回退副本。**这不是无谓复制，是有意的容错**：`_shared_patterns.py` import 失败时，仍能用内联副本降级扫描密钥（安全检查不能因 import 失败而完全失效）。漂移风险已由 health-check 的 `check_pattern_consistency`（AST 比对）治理。

**判断原则**：跨 hook 重复要不要收敛，看**这段代码失败时的安全后果**——
- 失败无安全后果（审计是旁路）→ 可收敛，import 失败 fail-safe
- 失败有安全后果（secret 扫描是防线）→ 保留内联副本做容错，另加一致性检查防漂移

**别将来看到 secret 副本就想"统一收敛"**——那会牺牲 import 失败时的降级能力。

## 发布版本号SSOT_CHANGELOG顶版为准_util-check强制校验

**决策**（2026-06-09）：整套规范的「发布版本号」单一来源（SSOT）= `CHANGELOG.md` 顶部 `## [x.y.z]` 条目。`CLAUDE.md` 与 `skills/README.md` 的 frontmatter `version` 必须与之相等，由 `skills-health-check.py` 的 `_check_release_version_consistency()` 强制校验，不一致报 ERROR。

**为什么加**：版本漂移是**反复发作的老问题**——CHANGELOG 多次"补记漏写 version"（见 2.5.0/2.5.2 补记）、2.4.2 专门修过一次标题/frontmatter 漂移。本次审阅又发现三处不一致：CLAUDE.md frontmatter=2.6.0、正文标题写死"v2.4"、README=2.7.0（真实最新）。旧 `check_versioning()` 只校验各文件 semver 格式 + 字段存在，**不校验跨文件一致性**，所以漂移永远抓不到。

**两个配套改动**：
1. CLAUDE.md 正文标题从 `# AI辅助开发工作规范 v2.4` 改为 `# AI辅助开发工作规范`（去掉硬编码版本号），版本只在 frontmatter 出现一次，根除"标题忘了改"这一漂移源。
2. frontmatter version 对齐到 2.7.0。

**边界（重要）**：此检查**只管 CLAUDE.md + README.md 这两个"发布版本"载体**。各 `rules/*.md`、`util-*/SKILL.md` 文件保留**自己独立的语义化版本**（如 workflow.md=1.1.0、git-safety.md=1.4.0），它们是单文件级版本，**不参与**这个一致性校验，别误把它们也拉齐到 2.7.0。

**实现**：`skills-health-check.py` 新增常量 `README_MD`/`CHANGELOG_MD` + `_CHANGELOG_VERSION_RE`（`^##\s*\[(\d+\.\d+\.\d+)\]`）+ `_check_release_version_consistency()`，在 `check_versioning()` 末尾调用。双向验证过：正常→passed、改坏版本→errors。

## 三套hook授权清单_不可并到README单一表

**事实**（2026-07-13 审阅 README 时查明）：bash-safety / write-safety / mcp-safety **三套 hook 各持有自己的授权清单，不是一张统一清单**——
- **`bash-safety-wrapper.py`** `GRANT_CATEGORIES` 元组：`git / delete / netexec / package / sensitive / subshell / git-rewrite / api-modify / perm-escalate / db-write`（10 个 Bash 维度类别）
- **`write-safety.py`**：`control-plane`（保护 skills/hooks/scripts/tests 前缀）+ `sensitive`/`infra`/`secret` 等 Write 维度机制，且 `control-plane grant` 走路径前缀保护而非类别元组
- **`mcp-safety.py`**：独立 `mcp` grant 类别（`GRANTS_DIR / "mcp"`、`mcp.session`）

**Why**：旧 README 统计表曾把三者混写成一张 `git / delete / netexec / package / sensitive / subshell / control-plane / secret / infra / mcp` 单子——这是**凭印象拼的**，既含 bash 实际没有的 `control-plane/secret/infra`（那些属 write 维度），又丢掉了 bash 2.14.0 新增的 `git-rewrite/api-modify/perm-escalate/db-write`。结果 README 与代码长期对不上（凭印象写文档是版本漂移的常见来源，与 [[发布版本号SSOT_CHANGELOG顶版为准_util-check强制校验]] 同源问题）。

**How to apply**：
- 更新 README/任何文档里的"授权类别"清单时，**先 `Grep`/`Read` 各 hook 源码的 `GRANT_CATEGORIES` / `is_*_path` / `GRANTS_DIR` 定义**，按 hook 分列，不要并表
- 三套 hook 列各自的类别：Bash 10 类、Write 的 `control-plane` 前缀保护、MCP 的 `mcp` —— 标清"哪套 hook 的清单"
- 任何"统计数字"（hook 脚本数、规则文件数、health-check 项数、CHANGELOG 字节数等）写进文档前都从源码/目录实读，别凭记忆——尤其经过几版未更新 README 容易积攒漂移
- 已在 memory 的 [[README_docDriftCheck]]（如记）或下次审阅时，把"README 描述 vs 源码实读"作为一项核查

**Date**: 2026-07-13
