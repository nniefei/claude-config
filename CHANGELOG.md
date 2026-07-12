# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/) 风格。版本号遵循 [SemVer](https://semver.org/)。

## [2.18.1] - 2026-07-13

### Added
- **write-safety 守卫门面文件盲区封堵**：`CONTROL_PLANE_CRITICAL_FILES` 此前仅含 `settings.json` / `settings.local.json` / `claude.md` / `changelog.md` 四个 `.claude/` 根级裸文件名。`README.md`、`MIGRATION.md`、memory 索引 `MEMORY.md` 三个门面文件既不在 `PREFIXES` 前缀（`hooks/`/`scripts/`/`tests/`/`skills/`）范围、也不在 `CRITICAL_FILES` 裸名清单内 → write-safety 对其**零拦截**，AI 可静默改写门面文件绕过 control-plane 守卫（且 CLAUDE.md 与 README 互引、风险不对称）。本次把三者补进 critical files 封堵
  - **新增 3 项**（`write-safety.py` L112-120）：`readme.md`、`migration.md`、`projects/c--users-nnie/memory/memory.md`；memory 正文文件（debugging/decisions/conventions）**故意不守卫**——高频写 memory 免每次弹窗，仅守 MEMORY.md 索引（被篡改会误导原生召回命中标的）
  - **元组项大小写约束**：`normalize_path()` = `.lower()` 整条路径，嵌套路径项须**全小写 + 正斜杠**匹配（`C--Users-nnie` → `c--users-nnie`，详见 debugging memory 条目），既有裸名项碰巧全小写、首次含大写段的嵌套项才暴露此副作用
- **测试新增 `test_facade_files_guarded`**：正向断言 README/MIGRATION/MEMORY.md 三门面被 `code==2` 拦截 + 反向断言 3 个 memory 正文文件仍 `code==0` 放行（防将来有人误把正文也加守卫破坏高频写场景）

### Changed
- **`test_auto_memory_filenames` 的 allowed_paths 移除 MEMORY.md**：该用例断言"auto-memory 敏感文件名黑名单不误伤 memory 文件"，MEMORY.md 现归 control-plane 层守卫、本就不该在此层放行清单里；保留 5 个正文文件继续断言"auto-memory 不误伤"。两层检查（auto-memory 黑名单 / control-plane 守卫）独立、不冲突
- **README.md 全量同步到 2.18.0 现状**：发现 README 自 2.15.0 演进到 2.18.0 期间多次改动未同步，停在 07-09、且统计表/组件详解除多处对不上实际
  - **统计表**：规则文件 5→6（2.16.0 新增 `karpathy-guidelines.md`）；Hook 脚本"10 个 Python 脚本"→"8 Hook（4 守卫+4 增强）+ 4 共享模块"；health-check 全 PASS 16→19 项（2.17.0 升 19）；CHANGELOG 体积 47KB→56KB（实测 56814 字节）
  - **授权类别列表更正**：旧表 `git/delete/netexec/package/sensitive/subshell/control-plane/secret/infra/mcp` 是凭印象把三套 hook 清单混拼而成、且含 bash 实际没有的 `control-plane/secret/infra` ——更正为 bash hook `GRANT_CATEGORIES` 元组实读的 10 类 `git/delete/netexec/package/sensitive/subshell/git-rewrite/api-modify/perm-escalate/db-write`，并标注 MCP 独立 `mcp` 类别见 `mcp-safety.py`，write 的 `control-plane` 见其路径前缀保护
  - **组件详解 L99**："核心约束指引"补 `karpathy-guidelines.md`（标准/静默模式自动注入常驻 5 条）
  - **工作机制日常工作流图**：补 2.18.0 新增的 git-commit 失败模式自查软提示、依赖/调试/陌生代码库三组 full 条款软提示、审阅类任务先 Read memory 提示
  - 底部日期 `2026-07-09 → 2026-07-10`；Memory 文件描述显式列出 4 个文件名

### Added (Memory)
- **decisions.md**：`三套hook授权清单_不可并到README单一表` —— 查明 bash/write/mcp 三 hook 各持独立授权清单，README 不可凭印象并倒单一表；写授权类别清单前先 Read 各 hook 的 `GRANT_CATEGORIES`/路径前缀/`GRANTS_DIR` 定义
- **conventions.md**：`改动后主动申请更新README` —— 任务动过 hook/规则/Skill/health-check 项数/授权类别/目录/版本日期等"系统现状描述"时，收尾前主动申请是否更新 README，确认后再动；申请格式点出具体脱节节号 + 旧值 vs 实读值，改完跑 util-check 防漂移
- **conventions.md**：`三套hook授权清单_不可并到README单一表` 索引补指针
- **debugging.md**：`write-safety critical files 元组项大小写依赖 normalize.lower()` —— 嵌套路径项必须全小写+正斜杠匹配；副踩坑「Edit 只改注释不动值、工具返回 success 但实际值未变」操作纪律（改后 Read 重看真值或 Grep 精确核对）

### Verified
- **`test_write_safety.py` 全套 26 项 PASS / 0 FAIL**：新增 `test_facade_files_guarded` 通过、既有用例零回归（`test_auto_memory_filenames` 的 allowed_paths 调整后通过）
- **`/util-check` 19 项 PASS / 0 ERROR / 0 WARN**：本次 hook 源码改动未破坏系统级一致性，文档↔实现无漂移
- **动态加载实证**：`importlib.util.spec_from_file_location` 加载 write-safety，`is_control_plane_path(MEMORY.md, 'Write')` 由 False（修正前）转 True（修正后），元组项大小写修正有效

## [2.18.0] - 2026-07-10

### Added
- **Karpathy 完整版（full 10 条）增量按场景自动落地**：full 版相对 simple 4 条的增量条款此前仅在主人桌面两份临时 md 中、需手动喊口令触发，现按"Rule 擅长 Don't 紧箍咒、不擅长 Should 教科书"的切分准则融入现有 hook 自动触发体系，主人无需手动调取
  - **常驻第 5 条「失败模式自救」入 `karpathy-guidelines.md`**：Don't 类紧箍咒（厨房水槽 / 错误抽象 / 乐观路径 / 失控重构），边界清晰、AI 犯就触发，符合常驻标准；常驻由 4 条扩为 5 条，零 hook 改动即随标准/静默模式自动注入
  - **`rule-loader.py` 新增 3 组场景软提示（Should 类教科书按信号浮现）**：仿 `detect_review_memory_hint` 同范式——`detect_dependency_hint`（装包意图→full 第8条「先查标准库」）、`detect_debug_hint`（调试/修 bug→full 第5+第7条「调查别猜、先写失败测试」）、`detect_unfamiliar_codebase_hint`（接手陌生代码库→full 第1条「先读后写」）；各带独立 sentinel per-session 抑制一次，防"教科书"条款平时占用常驻上下文并产生规则噪声
  - **`git commit` 信号叠加失败模式自查**：`detect_pretooluse` 主分支侧路命中 `_GIT_COMMIT_PATTERN`（仅 commit，不含 push/merge）→ 追加临提交前自查清单（厨房水槽/乐观路径/错误抽象/失控重构）作为 `extra_context`，与常驻第5条互补（常驻管日常警觉、提交点是硬性自查关口）
  - **`emit_review_standalone_notice` 泛化**：由"审阅提示"专用兜底泛化为通用软提示兜底（`[rule-loader] 软提示`），现服务于 memory 审阅 + 依赖管理 + 调试 + 陌生代码库四组；函数名保留 `review` 系历史命名，注释说明，未额外重命名避免级联改动

### Changed
- **`skills/rules/rule-loading.md` 触发场景映射表更新**：新增 `git commit`、引入依赖、调试/修 bug、接手陌生代码库 4 行场景软提示说明，并补一段「常驻 vs 软提示切分依据」（Should 走场景、Don't 进常驻），保持文档层与 `rule-loader.py` 实现层 SSOT 一致
- **`CLAUDE.md` version 2.17.0 → 2.18.0**：与 CHANGELOG 顶版同步

### Verified
- **行为测试通过**：4 场景触发（依赖管理/调试/陌生代码库各自浮现对应 full 条款，commit 自查与 git-safety.md 同条注入）、负面场景（"解释递归"等闲聊）零触发无规则噪声、per-session 抑制（同 session 二次同信号不再浮现）
- **`/util-check` 19 项 PASS / 0 ERROR / 0 WARN**：规则冲突检测扫描含新增第5条在内的规则文件未发现冲突模式

## [2.17.0] - 2026-07-10

### Changed
- **Memory 架构优化：索引从「3 行=3 文件」拆到「N 行=N 条规矩」条目粒度**：`MEMORY.md` 由 3 行索引（conventions 一行曾塞 8 条独立规矩）重构为 23 行（debugging 5 + decisions 6 + conventions 12），每条规矩独立成行、各带最关键辨识词（召回命中标的）；「已 N 犯」重犯计数显化进 hook 行（金子显化）。文件名降级为物理容器，语义边界落到二级标题粒度，提升原生召回命中率。详见 `projects/C--Users-nnie/memory/MEMORY.md`。

### Added
- **`check_hook_runner_coupling` 检查项（轨 B1，纯硬断言）**：`skills/util-check/scripts/skills-health-check.py` 新增检查项，把 v2.16.1 那条「health-check 必须对齐 hook-runner 调度」从软约束（memory）升为硬断言——运行时合成不存在的脚本名喂给 `hook-runner.py` 验证 exit=2 + stderr 反映所喂脚本名 + 拼到 `skills/util-safety/hooks/` 目录；静态读源码断言仍含三段路径常量。任一耦合破裂 → ERROR，避免有人改 runner 调度目录时检查器静默误判。双向回归：破裂态报 2 条 ERROR（运行时+静态各一），恢复态 PASS。PASS 计数 18 → 19。
- **配置 secret 扫描 allowlist 白名单（轨 B2，半硬）**：`check_config_secret_leakage` 新增字段级豁免——`CONFIG_SECRET_EXEMPTIONS`（`ANTHROPIC_AUTH_TOKEN` → “CC-Switch 产物，主人已豁免”）。`_secret_in_exempted_field` 取命中点前后窗口判定（`凭据赋值` pattern 会把字段名吃进 match 内部，需前后窗查），命中豁免字段不报 WARN，passed 文案体现「N 项已豁免」。斩断了 v2.16.1 前 settings.json token 明文反复误报 WARN 的路径，同时保留对非豁免字段明文密钥的完整拦截力（注入测试：豁免字段双命中都豁免、非豁免字段双命中照报）。
- **`detect_review_memory_hint` 审阅主动提示（轨 B3，准主动）**：`skills/util-safety/hooks/rule-loader.py` 新增 `MEMORY_READ_KEYWORDS`（审阅/排查/体检/health-check/review 等评判性词，排除“修复/重构”执行性动词），命中则浮现「动手前先 Read conventions.md+decisions.md 正文，只读索引不够」提示。复用 `should_inject` 伪哨兵做 per-session 抑制（每会话仅首轮浮现）；兜底 `emit_review_standalone_notice` 让 quick 模式下无规则注入时仍独立浮现。判别场景全验：首次审阅浮现、同session二次抑制、quick+审阅走兜底通道、普通开发不误触。

## [2.16.1] - 2026-07-10

### Fixed
- **`skills/util-check/scripts/skills-health-check.py` 修复 14 条误报**：检查 6（强约束运行时）校验 hook 注册路径时未复刻 `scripts/hook-runner.py` 的真实调度逻辑，导致 `/util-check` 误报 7×「Hook Python 解释器不存在」+ 7×「Hook 脚本不存在」（共 14 ERROR），而 hooks 实际注册完整、`hook-runner.cmd` / `hook-runner.py` / 5 个 hook 脚本全部真实存在
  - Check 1（解释器路径）：命令可能写成 `%USERPROFILE%/.claude/hook-runner.cmd <脚本>`（Windows）或 `$HOME/.claude/hook-runner.sh <脚本>`（Unix），`os.path.exists` 不会自动展开环境变量 → 改为先 `os.path.expandvars()` 再校验存在性
  - Check 2（脚本名）：hooks 注册的是 `hook-runner.cmd <纯脚本名>` 形式，`hook-runner.py` L76 把纯文件名固定拼到 `skills/util-safety/hooks/` 下查找 → 校验拿纯名当相对路径查 cwd 必然 `exists()==False`；改为纯名拼 `SAFETY_HOOKS_DIR`、带路径分隔符/冒号的 token 才按字面（展开后）校验
  - 补 `import os`；修复后跑 `/util-check` 由「16 PASS + 14 ERROR」转为「18 PASS / 0 ERROR / 0 WARN」
- **`conventions.md` 新增「health-check 的 hook 校验必须对齐 hook-runner 调度」**：记录该设计耦合根因（后续改 hook-runner 调度目录或动检查脚本此段，忘了同步会再炸一屏误报）+ How to apply（注册路径写 `%USERPROFILE%/...` 是正常可移植设计，应改检查脚本而非建议改绝对路径）；`MEMORY.md` 索引补指针

## [2.16.0] - 2026-07-10

### Changed
- **`skills/rules/workflow.md` v1.1.1 → v1.2.0**：移除「核心行为准则」4 条整章（编码前先思考 / 简单优先 / 精准修改 / 目标驱动执行），原内容以独立 skill `util-karpathy-guidelines` 承载；`workflow.md` 瘦身聚焦工作流本身，章节内「遵循准则 X」字样清理
- **`skills/rules/rule-loading.md` v1.1.0 → v1.1.1**：触发场景映射表中「进入标准/静默模式」加载列表增加 `skills/util-karpathy-guidelines/SKILL.md`，原准则内容在标准/静默模式自动注入到上下文
- **`util-karpathy-guidelines` Skill → `karpathy-guidelines.md` Rule**：行为准则是「工作时要遵守的文本规则」，**不是可执行工具**——按 Rule 形态承载更符合体系定位（与 `core-principles.md` / `workflow.md` 同级）；删除 `skills/util-karpathy-guidelines/` 目录，新建 `rules/karpathy-guidelines.md`（frontmatter 仅 `version` + `last-updated`，无 `user-invocable` / `allowed-tools` / `depends-on` 等 Skill 元数据）
- **`rule-loading.md` / `workflow.md` / `README.md` 引用同步**：所有 `util-karpathy-guidelines/SKILL.md` 引用统一改为 `karpathy-guidelines.md`
- **`rule-loader.py` 闭合 auto-load gap**：`detect_userpromptsubmit` 硬编码列表从 `core-principles.md + workflow.md` 扩为三者，新增 `karpathy-guidelines.md`——标准/静默模式自动注入 4 条 Karpathy 行为准则到 AI 上下文，至此文档层（rule-loading.md 映射表）与实现层（rule-loader.py 硬编码列表）SSOT 一致

## [2.15.0] - 2026-07-09

### Added
- **环境迁移系统**：新增 `env.json` 集中管理环境变量（`python_exe`、`os_type`），迁移只需改一个文件
- **`scripts/hook-runner.py`**：自定位 Hook 调度器，读取 `env.json` 确定 Python 路径，按相对路径分发到目标 hook 脚本
- **`hook-runner.cmd`**（Windows 入口）：通过 `%~dp0` 自定位，无需硬编码路径
- **`hook-runner.sh`**（Unix 入口）：通过 `dirname $0` 自定位，自动 `chmod +x`
- **`scripts/migrate-env.py`**：迁移辅助脚本，自动检测 OS 类型、Python 位置，生成正确的 `settings.local.json`
- **`MIGRATION.md`**：环境迁移指南文档，包含 6 步详细操作流程

### Changed
- **settings.local.json**：12 处 hook 命令从硬编码 `C:/App/Python311/pythonw.exe` 改为统一走 `hook-runner.cmd`，路径使用 `%USERPROFILE%`（Windows）或 `$HOME`（Unix）系统变量，**零用户名零 Python 路径硬编码**
- **settings.local.json allowlist**：移除 `Bash(/c/App/Python311/python *)`（与 `Bash(python *)` 重复）
- **README.md 全面重写**：从 7 行路由文档扩写为 340+ 行完整项目文档，包含目录总览、快速上手、核心组件详解（6 大子系统）、工作机制、版本管理等
- **删除 `skills/README.md`**：其有价值内容（故障排查表、日常流图）合并到根 README，消除 60~70% 内容重叠和版本漂移源
- **CLAUDE.md 引用更新**：`skills/README.md` → `README.md`
- **util-check 版本校验简化**：`_check_release_version_consistency()` 仅校验 `CLAUDE.md`，不再校验已删除的 `skills/README.md`

### Added
- **bash-safety-wrapper changelog 合规检查**：`git commit` 时自动检查 CHANGELOG.md 是否在暂存区，未更新则 stderr 警告（`_check_changelog_staged()`，fail-safe）
- **git-safety.md CHANGELOG 规则**：新增"提交前必须写 CHANGELOG"章节，禁止违例 commit

## [2.14.0] - 2026-07-08

### Added
- **util-safety Bash hook 新增 6 大 grant 类别**：`git-rewrite`（filter-branch/filter-repo/update-ref -d/symbolic-ref --delete）、`self-destruct`（reflog expire/gc --prune，deny 不可授权）、`api-modify`（gh api -X DELETE/POST/PUT/PATCH、curl -X 写操作、git config --global 危险 key）、`perm-escalate`（chmod -R 777/chown -R）、`db-write`（mysql/psql/sqlcmd DROP/TRUNCATE/DELETE）、`disk-destroy`（dd/mkfs/fdisk，deny 不可授权）；同时扩展 `netexec` 覆盖 pip/npm install URL 供应链投毒
- **util-safety Write/Edit symlink 绕过修复**：`_check_path` 中 grants 检查之后调用 `os.path.realpath()` 解析父目录符号链接，防止 symlink 指向敏感文件绕过路径规则；仅对父目录存在的路径生效，避免 Windows 上不存在绝对路径被错误合并到 CWD
- **util-safety Write-audit PostToolUse hook**：新建 `hooks/write-audit.py`，fail-safe 记录所有成功执行的 Write/Edit 操作（路径哈希 + 行数）到 `logs/write-audit.jsonl`
- **MCP blocklist 扩展**：`always_block` 新增 `mcp__filesystem__write_file` / `mcp__filesystem__delete_file`（deny）
- **SKILL.md 子代理 hook 继承说明**：明确 hooks 基于事件类型对所有 tool use 生效，子代理无法绕过

### Changed
- **util-safety 版本**：`SKILL.md` v1.4.0 → v1.5.0
- **write-safety.py**：docstring 升 v2.8.0，grants 拦截提示文案修正（「回复继续」→「自动重试」）
- **bash-safety-wrapper.py**：`GRANT_CATEGORIES` 从 6 类扩至 10 类；`DANGEROUS_PATTERNS` 从 32 条扩至 50 条；`dangerous_git_operations()` 新增 7 个子命令 argv 级检测
- **测试**：`test_bash_safety.py` 新增 9 个测试函数（+126 行）覆盖全部新增 pattern；`test_write_safety.py` 新增 2 个 symlink 绕过测试
- **settings.local.json**：PostToolUse 注册 Write|Edit → write-audit.py

### Fixed
- **write-safety.py 拦截提示文案**：grants 提示中「执行后回复"继续"」改为「输入 ! 命令后自动重试」，与实际行为一致（`!` 命令执行后 Claude Code 自动重试被拦截操作）

## [2.13.6] - 2026-07-06

### Added
- **CLAUDE.md `## Memory` 章节补充存取指引**：在原有「完全交由原生机制」一句话后，补三段轻量指引——什么值得记（疑难 bug/性能坑→debugging、拍板决策→decisions、行为约束与豁免项→conventions）、什么不写（可从代码/git/CLAUDE.md 直接得知的事实、仅本次对话关心的临时信息、已废止旧机制内容）、写法（事实/决策 + Why + How to apply + `[[名称]]` 互链；MEMORY.md 钩子写辨识词）。定位为「建议」而非「强制分类」，明确「命名自由、不必强行归类」，沿用 v2.13.0 回归原生决策，不自定义过期/归档策略。

### Changed
- **debugging.md 两条已解决条目精简**：`session-start.py_70秒卡死`、`Python 进程堆积递归 spawn` 两条已修复的踩坑记录去除冗长误诊 narration 水分，保留「现象 → 根因 → 修复 commit → 踩坑警示」骨架，内容不变质，继续作为 Windows 大量文件排序避免 `stat()` O(N)、子进程递归 spawn 的警示条目留存。
- **MEMORY.md 索引钩子更新**：`debugging.md` 钩子弱化已解决的「70秒卡死」表述，突出仍鲜活且易复发的 `control-plane.session 会话级 grant 反复失效` 条目。
- **README 行数描述同步**：`skills/README.md` 表格中 CLAUDE.md 行数从「~63 行」更新为「~72 行」（配合本次 Memory 章节扩充）。

## [2.13.5] - 2026-07-04

### Fixed
- **rule-loader.py UTIL_SKILL_NAMES 同步恢复**：从仅 `{"util-check"}` 更新为包含全部 5 个 Skill，修复恢复的 Skill 不被 rule-loader 识别的问题
- **TEST_SCRIPTS 补全 test_rule_loader_ttl.py**：修复 TTL 相关回归测试未纳入统一测试入口的问题
- **README 行数描述更新**：CLAUDE.md 行数从"~55 行"更新为"~63 行"

## [2.13.4] - 2026-07-04

### Fixed
- **settings.local.json allowlist 精简**：移除过宽通配项（`Bash(python *)`、`Bash(env *)`等），从 98 条精简到 43 条，避免绕过安全 hook
- **fallback_skills 列表更新**：从仅 `["util-check"]` 更新为包含全部 5 个 Skill
- **TEST_SCRIPTS 列表补全**：添加遗漏的 5 个测试文件（mcp_grant_concurrency / mcp_risk_level / mcp_three_tier / pattern_performance / security_functions_positive）
- **README 健康检查项数更新**：从"10+ 项"更新为"16 项"
- **CLAUDE.md 补充辅助工具说明**：添加 util-memory / util-session / util-init 的简要说明
- **util-memory 过期策略描述精简**：简化 30 天/90 天规则描述，明确标注为参考建议

## [2.13.3] - 2026-07-04

### Fixed
- **util-memory/SKILL.md**：移除对已删除的"Memory 自动过期策略"规则的引用，改为参考策略说明
- **util-session/SKILL.md**：移除对已删除的 output-formats.md 的引用，内联保存模板
- **util-init/SKILL.md**：更新 last-updated 到 2026-07-04
- **rule-loading.md**：补充 core-principles.md 注入映射
- **CLAUDE.md**：明确 util-memory 定位为"辅助工具"而非"替代原生"
- **三个 hook 的 _load_shared_patterns()**：统一使用 _load_patterns_utils 共享模块（保留内联副本容错）

## [2.13.2] - 2026-07-04

### Added
- **恢复 util-memory**：查看和维护 memory 文件，清理过期记录
- **恢复 util-init**：项目初始化，识别技术栈并建立上下文
- **恢复 util-session**：保存当前会话上下文到 memory，支持手动保存和读取

## [2.13.1] - 2026-07-04

### Fixed
- **MCP ask 决策 JSON 格式修复**：`mcp-safety.py` ask 分支输出从裸顶层 `permissionDecision` 改为正确的 `hookSpecificOutput` 包裹结构，修复 Claude Code 不识别导致的 fail-open（所有 ask 级 MCP 工具从未弹窗）
- **审计日志打码修复**：`bash-safety-wrapper.py` 添加 `_load_shared_patterns()` + `SECRET_PATTERNS`，修复 `command_summary()` 因引用未定义变量导致打码 100% 不生效（密钥明文进审计日志）
- **rule-loader 越权 allow 移除**：`rule-loader.py` 删除 PreToolUse 事件的 `permissionDecision: "allow"` 输出，避免跳过正常权限评估流程
- **性能日志失真修复**：`bash-safety-wrapper.py` 的 `matched_patterns` 现在记录实际匹配的 pattern 标签；ask 路径正确记录为 `decision: "ask"` 而非 `"allow"`

### Added
- **安全功能正向断言测试**：新增 `test_security_functions_positive.py`，包含 5 个测试验证安全功能「确实发生了」：打码生效、ask JSON schema 合规、deny 路径 exit 2、grant 消费 + 审计落盘

### Fixed (P2 — 拦截路径缺口)
- **敏感文件扫描目录剪枝修复**：`_scan_sensitive_paths()` 从 `rglob("*")` 改为 `os.walk` + 原地修剪 `dirnames[:]`，修复 node_modules/.git 等大目录导致的 20000 文件预算耗尽或 15s 超时
- **rm -rf 变体漏检修复**：正则添加大写 R（`-Rf`）和长选项（`--recursive --force`）支持，`re.IGNORECASE` 标志
- **git branch 变体漏检修复**：`dangerous_git_operations()` 支持 `-Df`、`-d -f` 等组合短选项解析
- **git add 相对路径扫错目录修复**：`staged_sensitive_paths()` 相对路径统一以 payload cwd 为基准
- **ask memo 语义收紧**：grant 带外授权不再升级为会话级 memo，只有 ask 弹窗确认才写 memo

### Fixed (P3 — 文档对齐与卫生)
- **rule-loading.md 重写**：以 rule-loader.py 实际行为为准，删除不可执行的「每次工具调用前自检最近 3 轮 / 20 轮计数」强制检查点
- **util-safety/SKILL.md 更新**：删除已废止的 Stop 行为描述，更新 source 字段（`grant-env` → `grant`），版本升至 1.4.0
- **mode log 打码**：rule-loader.py 的 mode-transitions.jsonl 对 prompt 前 80 字符复用 SECRET_PATTERNS 打码
- **core-principles.md 加载缺口修复**：rule-loader 在首次 UserPromptSubmit 注入 core-principles.md（全局底线）
- **util-check/SKILL.md 重写**：检查项章节按脚本实有 16 个 check 函数重写
- **悬空引用修复**：workflow.md「CLAUDE.md 中 L3」→「core-principles.md 中 L3」；git-safety.md「CLAUDE.md 强制刹车例外」→「workflow.md 强制刹车例外」；git-safety.md SUBSHELL 旧表述改 ask-first
- **workflow.md frontmatter**：version 升至 1.1.1，last-updated 更新
- **CLAUDE.md 括号摘要对齐**：补充第 5 条「Hook 拦截后必须请示」
- **_audit_log.py 归档保留上限**：保留最新 10 个归档文件；行数统计改 errors="replace"

## [2.13.0] - 2026-07-01

### Changed
- **Memory 回归原生**：放弃自定义 5 类分类（debugging/decisions/conventions/dependencies/patterns）、30/90 天过期归档策略和 auto-memory 禁用声明，完全交由 Claude Code 原生 memory 机制（写入、去重、按相关性召回）。CLAUDE.md 精简至 55 行
- **Skill 瘦身**：删除 3 个零使用的 skill——util-memory（归档制维护工具）、util-session（零次 save 调用，被系统上下文压缩替代）、util-init（从未在新项目运行）。Skill 数从 5 降至 2（util-safety + util-check）
- **Rules 精简**：删除 memory-guidelines.md（归档制规则）、skill-boundaries.md（通篇讲三个已删 skill 的边界）、memory.md（空壳 stub）、output-formats.md（217 行模板内联到 workflow.md）。Rules 从 9 文件降至 5 文件
- **rule-loader 净化**：移除 memory-guidelines.md 动态注入逻辑，MEMORY_FILENAMES 清空为死代码标记，UTIL_SKILL_NAMES 仅保留 util-check
- **workflow.md 瘦身**：静默模式累计风险计分系统（~90 行精细计分）替换为 2 行简单规则（改 6+ 文件时暂停汇报）；汇报格式内联到阶段四和静默完成汇报段
- **根 README 路由化**：从 53 行瘦到 7 行，只做文档路由，避免与 skills/README.md 双写漂移

### Removed
- `skills/util-memory/` — Memory 查看/清理/归档工具
- `skills/util-session/` — 会话上下文快照保存
- `skills/util-init/` — 项目初始化与技术栈识别
- `skills/rules/memory-guidelines.md` — 5 类分类详解与过期归档策略
- `skills/rules/skill-boundaries.md` — 三个已删 skill 的职责边界与冲突解决
- `skills/rules/memory.md` — 已迁移的占位 stub
- `skills/rules/output-formats.md` — 汇报格式模板（已内联到 workflow.md）
- `scripts/migrate.py` — 跨机器迁移脚本（不再维护环境迁移能力）
- `skills/README.md` 初次部署章节中的跨平台说明、迁移步骤、token 替换指引

### Fixed
- util-check 硬编码 fallback_skills 清单同步（移除已删 skill），Agent 权限检查同步移除
- test_rule_loader.py / test_rule_loader_ttl.py 测试用例清理：删除对已移除 skill/rule 的引用，更新断言为现行行为
- rule-usage-report.py all_rules 集合同步
- skills-health-check.py 注释与冲突规则库同步

## [2.12.0] - 2026-06-30

### Changed
- **配置瘦身**：`settings.local.json` allowlist 从 96 条精简到 63 条，移除历史排查命令、grant 操作残留和过宽的通配项；根 `README.md` 版本漂移修复（改为以 CHANGELOG.md 顶部为准）
- **规则收敛**：memory 规则入口迁移至 `memory-guidelines.md`（`memory.md` 保留为兼容 stub）；`skill-boundaries.md` 从自动注入降级为参考文档；`core-principles.md` 精简到 5 条核心底线（‑60% 篇幅），消除与 `workflow.md` 的重复
- **SessionStart 节流**：后台完整 health-check 改为每天最多一次（`CLAUDE_FORCE_STARTUP_HEALTH_CHECK=1` 强制）；同步轻量检查每次启动仍运行

## [2.11.0] - 2026-06-16

### Fixed
- **util-check 规则引用检测**：新增扫描 `rule-loader.py` 的动态注入引用（纯文件名字面量，如 `"git-safety.md"`），消除 `git-safety.md` / `memory.md` / `skill-org.md` / `skill-boundaries.md` 四个「未被任何文件引用」误报。这些规则由 rule-loader 按场景动态加载，不走 `skills/rules/xxx.md` 静态路径模式，旧检测逻辑漏判。修复后 util-check WARN 从 6 项降至 2 项（仅剩 settings.json token 明文豁免项）
- **util-check 回归测试 fixture**：补全 `test_skills_health_check.py` 的 mcp-safety 桩逻辑——精确黑名单集合加 `force_push`、新增动词模式段（allow `get_/list_/search_/read_` 只读前缀放行，block `delete/deploy/publish/force/send` 高危动词阻断）。修复历史遗留 FAIL：fixture 桩过简，无法满足 `check_runtime_constraints` 与 `check_mcp_pattern_rules` 两个 smoke test 期望（`force_push` 黑名单、`delete_page` 动词模式均期望 exit=2），导致 `util-check test` 子命令报 FAIL。生产 mcp-safety.py 本就完整故生产全绿；修复后测试套件 16 项全 PASS

### Evaluated（评估后确认现状最优，不改造）
- **P2 启动窗口**：随 P1 `platform_utils`（`pythonw.exe` + `CREATE_NO_WINDOW`）已解决；`settings.local.json` 全部 hook 入口已用绝对 `pythonw.exe` 路径；hook 性能基线 7-19ms 无瓶颈。不臆造优化
- **P4 MCP 白名单模式**：威胁模型明确「非白名单为有意设计」；现有三层策略（精确黑名单 + 动词模式 + allow 只读豁免 + 未知动词 ask）已含部分白名单能力，纯白名单模式损可用性、ROI 低，保持现状
- **P5 版本同步自动化**：util-check 已实现 SSOT 校验（CHANGELOG 顶版为准 + CLAUDE.md/README.md frontmatter 一致 + H1 标题版本号漂移检测），无需额外 pre-commit hook 引入新依赖

### Added
- **系统审阅报告**：以资深 AI Agent 工程师角度全面审阅系统架构
  - 识别 5 个主要改进方向：跨平台兼容性、性能优化、复杂度管理、安全边界、文档自动化
  - 评估各维度评分：架构设计 ⭐⭐⭐⭐⭐、安全防护 ⭐⭐⭐⭐⭐、可观测性 ⭐⭐⭐⭐、可维护性 ⭐⭐⭐⭐、跨平台性 ⭐⭐⭐

- **系统优化计划**：制定分阶段改进方案
  - P1: 跨平台兼容性改造（支持 macOS/Linux，消除 Windows 硬编码依赖）
  - P2: 性能优化（降低 hook 调用延迟，解决启动窗口弹出问题）
  - P3: 复杂度管理（规则依赖图可视化、hook 脚本模块化）
  - P4: 安全边界增强（MCP 白名单模式、安全文档完善）
  - P5: 文档自动化（文档生成工具、版本同步自动化）

- **跨平台工具模块**：新增 `platform_utils.py` 封装平台特定逻辑
  - `is_windows()` / `is_macos()` / `is_linux()`：平台判断
  - `get_python_exe()`：获取适合后台运行的 Python 解释器（Windows 优先 pythonw.exe）
  - `get_creation_flags()`：获取 subprocess 创建标志（Windows CREATE_NO_WINDOW）
  - `kill_process()`：跨平台进程终止（Windows taskkill / Unix SIGTERM）
  - `get_lock_mechanism()`：跨平台文件锁（Windows msvcrt / Unix fcntl）

### Changed
- **session-start.py 跨平台改造**：
  - 使用 `platform_utils` 模块替代硬编码的 Windows 特定逻辑
  - 支持回退到原始逻辑（兼容旧版本）
  - 保持 fail-safe 设计不变
  - 同步更新 `skills/README.md` 跨平台部署说明（移除"仅 Windows 验证"平台限制）

### Planning
- **需求确认**：明确改造优先级和范围
  - 平台需求：需要支持多平台（Windows/macOS/Linux）
  - 性能问题：启动时命令窗口弹出影响用户体验
  - 优先级偏好：兼容性优先
  - 时间投入：集中 1-2 周完成
  - 改造范围：全面改造

## [2.10.1] - 2026-06-16

### Added
- **P2-1: Hook 性能监控**：为 bash/write/mcp 三个 hook 增加性能埋点
  - 记录每次调用的耗时、决策、命中模式等
  - 输出到 `logs/hook-performance.jsonl`（fail-safe 设计）
  - 新增 `hook-performance-report.py` 性能分析工具
  - 支持统计报告：平均/最小/最大耗时、Top N 慢操作

### Performance
- **性能基线建立**：bash-safety 平均 21.27 ms，write-safety 平均 14.86 ms
- **识别优化点**：bash-safety 最慢操作 78.48 ms，有优化空间

## [2.10.0] - 2026-06-16

### Added
- **P3-2: 大文件处理告警**：Write/Edit 超过 5000 行时输出 stderr 警告，建议分块写入防止截断
- **P2-3: session-env 清理工具**：`cleanup-session-env.py` 支持归档旧会话（默认保留最近 30 个）
- **P3-3: 核心约束文档化**：新增 `skills/rules/core-principles.md`（严禁猜测、活泼沟通、错误响应、事前声明）

### Changed
- **CLAUDE.md 精简**：从 75 行精简到 72 行（-4%），将"核心约束"拆分到独立文档
- **rule-loading.md 更新**：触发场景表新增 core-principles.md 加载时机

### Fixed
- **P2-2: 规则使用统计激活**：验证统计功能正常运行，生成首份使用频率基线报告

### Maintenance
- **session-env 清理**：归档 99 个旧会话目录，释放 ~50KB 磁盘空间
- **规则使用基线**：git-safety.md（5 次）、workflow.md（2 次）为高频规则

## [2.9.1] - 2026-06-15

### Changed
- **CLAUDE.md 精简重构**：从 130 行精简至 75 行（减少 42%），将详细规则拆分到独立文档
  - 新增 `skills/rules/rule-loading.md`（规则按需加载机制详解，36 行触发场景映射表、重载检查点、典型场景）
  - 新增 `skills/rules/memory-guidelines.md`（Memory 积累规范详解，5 类分类、过期策略、写入格式、最佳实践）
  - CLAUDE.md 保留核心配置和索引，详细规范按需加载
  - 符合"规则按需加载"设计理念，降低初始上下文消耗

### Fixed
- **bash hook 命令描述去中文化**：bash-safety-wrapper.py 中 38 个命令描述改为英文原文（git commit、rm -rf 等），保持专业术语一致性。描述性文字（拦截原因、说明）保持中文

## [2.9.0] - 2026-06-15

### Added
- **P1 - 规则重载检查点**：`CLAUDE.md` 新增"规则重载检查点（强制执行）"段落，要求 AI 在调用工具前自检最近 3 轮对话是否出现上下文压缩通知、用户要求刷新规则、或距上次加载超过 20 轮对话，满足任一条件强制重新 Read 规则文件。解决长会话中规则被压缩丢失、用户修改规则后 AI 不感知的问题，提升行为一致性
- **P1 - 静默模式累计风险追踪**：`skills/rules/workflow.md` 新增"静默模式累计风险追踪"段落，要求 AI 在静默模式下维护累计风险计分卡（修改普通文件 +1 分、核心配置 +5 分、净删除每 10 行 +1 分、外部命令 +2 分、CI/CD 配置 +10 分），累计 15 分触发警告询问、25 分强制退出静默模式。避免多次小改动累积成重大变更却从未触发单次刹车条件的安全漏洞
- **P2 - Memory 自动过期策略**：`CLAUDE.md` 新增"Memory 自动过期策略"段落，定义 30 天规则（标记"已解决"的条目归档）和 90 天规则（强制归档），要求 AI 在会话开始时扫描过期条目并主动询问清理。`skills/util-memory/SKILL.md` (v1.1.0) 增强 `clean` 功能，支持基于日期的过期检测（优先于语义检测），归档到 `memory/archive/` 按月分组。避免过期 memory 污染上下文，降低加载成本
- **P2 - 规则冲突检测**：`skills/util-check/scripts/skills-health-check.py` 新增 `check_rule_conflicts()` 函数，扫描所有规则文件提取强制性约束（"必须/禁止/不得/一律"关键词），基于人工维护的冲突规则库检测潜在矛盾（如 workflow.md 要求"必须用 TaskCreate" vs skill-boundaries.md 禁止"Task 工具"）。随规则文件增多，自动化检测替代人工排查，提升系统可维护性
- **P3 - 模式切换视觉增强**：`skills/rules/output-formats.md` (v1.1.0) 新增"模式切换视觉增强"段落，为模式切换、静默模式风险警告（15 分阈值）、强制刹车（25 分阈值）、通用刹车提示增加 ASCII 边框格式，提升视觉反馈，增强用户对模式状态的感知
- **P3 - 规则使用频率统计**：`skills/util-safety/hooks/rule-loader.py` 新增 `_record_rule_usage()` 函数，每次注入规则时记录统计数据（调用次数、首次使用、最后使用时间）到 `logs/rule-usage-stats.json`。新增 `skills/util-check/scripts/rule-usage-report.py` 脚本生成使用频率报告（高频/中频/低频标签、未使用规则提示），指导规则优化决策

### Docs
- **系统优化计划表**：生成 `系统优化计划表.md` 到桌面，包含 6 个优化问题（2 个 P1、2 个 P2、2 个 P3）的详细修复方案、验证方法、预计耗时、执行路线图，预计总工作量 8-12 小时

## [2.8.0] - 2026-06-12

### Added
- **P2 - 统一测试入口**：新增 `skills/util-safety/tests/run-all-tests.py`，自动发现所有 test_*.py 文件并按拓扑排序执行（基础 → 单 hook → 集成），汇总报告显示通过/失败/跳过数量与总耗时；单个测试失败不中止后续测试；支持 `--verbose` 详细输出。当前 10 个测试中 8 个通过（`test_bash_audit_post.py` 和 `test_audit_log_concurrency.py` 因测试环境缺 PostToolUse hook 注册失败，非代码缺陷）
- **P3 - MCP risk_level 字段**：`mcp_blocklist.json` 的 always_block 支持 `{"tool": "...", "risk_level": "deny"|"ask"}`，低风险工具（如 slack send_message）走 ask 弹窗确认，高风险工具（force_push、delete_branch）保持 deny 硬阻断。密钥泄漏强制 deny 覆盖 ask。新增 `test_mcp_risk_level.py` 验证
- **P3 - MCP 三层策略**：(1) allow_tool_patterns 命中 → 放行；(2) always_block 或 block_tool_patterns 命中 → 按 risk_level 处理；(3) 未知动词（不在前两者）→ 按 unknown_verb_risk_level 处理（默认 ask）。关闭了先前"未知动词直接放行"的安全缺口。新增 `test_mcp_three_tier.py` 验证
- **P3 - rule-loader TTL 机制**：去重缓存值从列表改为 `{rule_name: timestamp}`，超过 1 小时自动过期重新注入。既解决非通知型上下文压缩（token 超限自动丢弃不通知 hook），也解决长会话规则自然老化。向后兼容旧列表格式自动迁移。新增 `test_rule_loader_ttl.py` 验证

### Fixed
- **P1 - mcp-safety grant 消费 TOCTOU**：`mcp-safety.py` 原先按「检查 grant 文件 exists() + unlink()」两步消费一次性 grant，两步间存在竞态——并发 MCP 调用可能都通过检查、都消费同一个 grant，等于一份授权放行多条调用。修复：新增 `acquire_mcp_grant()` 函数，导入 `_audit_log.py` 的 `with_audit_log_lock` 用跨平台文件锁原子化「检查 + 消费」；锁不可用时 fail-safe 回退非加锁路径。env 授权（`CLAUDE_HOOK_APPROVED_MCP=1`）与会话级 grant（`mcp.session`）不消费，持续有效。新增 `test_mcp_grant_concurrency.py`：8 并发争抢单 grant 验证恰好 1 个放行、env/session grant 不消费全放行、无 grant 全拒
- **P2 - session-start taskkill TOCTOU**：`session-start.py` 原先用 tasklist 校验 PID 是否为 Python 进程，再用裸 `taskkill /F /PID`（无 IMAGENAME）终止，两步间 PID 可被 OS 复用为其他进程导致误杀。修复：合并为单次 `taskkill /F /PID <pid> /FI "IMAGENAME eq pythonw.exe"`，IMAGENAME 过滤确保只终止 Python 进程；若 PID 已复用为其他进程，taskkill 返回非 0 但不误杀

### Docs
- **P2 - settings.json 明文 token 防护指引**：`skills/README.md` 迁移步骤新增「迁移后必须立即替换 ANTHROPIC_AUTH_TOKEN」警告，明确旧机器 API token 不应在新机器使用（尤其跨组织/跨账号迁移），避免误提交或凭据泄漏
- **P2 - migrate.py 迁移后 token 提醒**：`scripts/migrate.py` 写入 settings.local.json 后输出安全提醒，引导用户立即替换 settings.json 中的 ANTHROPIC_AUTH_TOKEN

## [2.7.3] - 2026-06-11

### Added
- **P1 - Bash PostToolUse 审计闭环**：新增 `bash-audit-post.py`，在 Bash 命令实际执行后复用 `bash-safety-wrapper.py` 的危险命令识别逻辑写入 `logs/bash-safety-audit.jsonl`，补齐 ask-Allow 主路径先前无审计记录的问题；审计条目新增 `source` 字段区分 `grant-env` / `post-exec` / `session-ask-memo`
- **操作标签级 ask memo**：PostToolUse 记录 `session_id + 危险操作标签` 到 `logs/ask-approved-cache.json`；同 session 同操作标签第二次起由 PreToolUse 直接放行并写 `source=session-ask-memo` 审计，避免同一操作重复弹窗，同时不把 `git commit` 泛化到 `git push`

### Docs
- **P2 - README 标题去硬编码版本号**：`skills/README.md` 正文一级标题去掉 `v2.7.0`，版本只在 frontmatter 保留；health-check 新增正则检查——CLAUDE.md/README.md 一级标题含 `v?\d+\.\d+` 即报 ERROR，根治"标题忘改版本"漂移
- **P2 - README 故障排查表修正无效 export 授权指引**：把「Bash export 即可授权」改写为正道——`! touch ~/.claude/.grants/<category>`，并注明「会话内 Bash export 对 hook 子进程无效」；`git-safety.md` 的 CI/CD 段同步补强限定语
- **P3 - 日志保留数对齐**：`util-safety/SKILL.md` 的 health-check run log 保留数从 20 → 5（与 `session-start.py` 实际代码一致）

### Fixed
- **P1 - write-safety grant 原子消费**：`write-safety.py` 原先按阻断原因逐条调用 `_check_write_grant()`，会在同类别双原因（如 `C:/Windows/foo.pem` 同时命中敏感文件路径 + 系统路径）时先消费 `sensitive` grant、再因第二条同类别原因找不到 grant 而误拦；跨类别部分授权时也会先消费已授权类别再整体阻断。修复：先把阻断原因映射为去重后的 grant 类别集合，再用 `acquire_write_grants()` 原子完成「全量检查 + 统一消费」；任一类别缺失时不消费任何 grant，`.session` 与 env 授权仍不消费
- **P2 - grants 写入保护 docstring 漂移**：`is_grants_path()` 注释从旧的 `permissionDecision=ask` 语义改为实际的 exit 2 硬 deny 语义，明确不被任何 grant/env 豁免，避免未来维护者按过期注释把信任根降级
- **P2 - rule-loader PreToolUse/UserPromptExpansion 重复注入**：`rule-loader.py` 的 PreToolUse 与 UserPromptExpansion 以前每次命中都全文重注入规则（如 git 命令每次注入约 180 行的 git-safety.md），违背「同一会话内仅加载一次」约定。修复：两条路径同样调用 `should_inject(session_id, rules)`，写入 `rule-injection-cache.json` 去重缓存；2.7.2 的 compact/clear/resume 清缓存机制自动覆盖。新增 4 项回归测试

## [2.7.2] - 2026-06-10

### Fixed
- **P1 - 压缩后规则重注入缺失**：`rule-loader.py` 的 UserPromptSubmit 去重缓存以"整个 session 一次"为粒度，但注入的 `additionalContext`（如 workflow.md）进入可被压缩的对话历史，上下文压缩后会被摘要/丢弃，去重缓存仍记"已注入"不再补发。修复：`session-start.py` 新增 `clear_session_dedup()`——读 stdin 的 `source` 字段，当 `source in {"compact", "clear", "resume"}` 时清除本 session 的去重记录，下一轮 UserPromptSubmit 自然重新注入。`source == "startup"`（全新会话）不清。内联实现原子写（不复用 rule-loader 函数），fail-safe 静默吞异常。新增 3 项单元测试验证 compact/clear/resume 清缓存、startup 不清
- **P2 - git checkout . 漏拦**：`git-safety.md` 第 24 行规定禁止 `git checkout -- .`（丢弃工作区改动），但 `bash-safety-wrapper.py` 的正则（第 68 行 `checkout\s+--`）与 argv 分支（第 297 行 `"--" in args`）都漏拦无 `--` 的 `git checkout .`（效果相同）。修复：正则改为 `checkout\s+(--|\.\s|$)`，argv 分支改为 `("--" in args or "." in args)`。新增测试用例 `git checkout .` 命中 ask 决策，同时新增负向用例 `git checkout main`、`git checkout -b feature` 放行（防误伤分支切换）
- **P3 - secret 扫描超长内容性能**：`_shared_patterns.py` 的"凭据赋值"宽模式 `[A-Za-z0-9_/+=.\-]{16,}` 在超长无分隔字符串上可能触发 5s 超时，导致 fail-closed 误拦一次合法写入。修复：在 `write-safety.py` 的 `scan_content_secrets()` 与 `mcp-safety.py` 的 `scan_secrets()` 入口处，对超长 content 截断至前 256KB（`SECRET_SCAN_MAX_BYTES = 256 * 1024`），避免触发超时。真实密钥泄漏通常在文件头部配置区，256KB 充分覆盖。`test_pattern_performance.py` 跑绿无回归

## [2.7.1] - 2026-06-09

### Fixed
- **发布版本号漂移**：`CLAUDE.md` frontmatter `version` 2.6.0 → 2.7.0、`last-updated` → 2026-06-09，与 CHANGELOG/README 真实最新版对齐；正文标题去掉硬编码 `v2.4`（改为引用 CHANGELOG），根除"改版本忘改标题"这一反复发作的漂移源

### Added
- **util-check 发布版本一致性检查**：`skills-health-check.py` 新增 `_check_release_version_consistency()`——以 `CHANGELOG.md` 顶部 `## [x.y.z]` 为 SSOT，强制校验 `CLAUDE.md` 与 `skills/README.md` 的 frontmatter `version` 与之一致，漂移即报 ERROR。各 `rules/*.md`、`util-*/SKILL.md` 保留独立语义化版本，不参与此校验。双向验证（正常→passed、改坏→errors），15 项 health-check 单元测试全绿无回归

## [2.7.0] - 2026-06-07

### Added
- **跨机迁移脚本** `scripts/migrate.py`：自动探测 `sys.executable` 与 `home()`，重写 `settings.local.json` 的硬编码路径；重写前备份、重写后校验 JSON 与 hook 脚本路径存在性；支持 `--dry-run`。README 部署章节简化为调用脚本
- **MCP 动作类别模式规则**：`mcp_blocklist.json` 新增 `block_tool_patterns`（高危动词 send/delete/remove/publish/deploy 等）与 `allow_tool_patterns`（只读动词豁免，优先级更高）。覆盖未列入精确黑名单的写类工具，`get_sender` 等含写动词子串的只读工具不误拦
  - 扩充 `block_tool_patterns` 至写/改/权限类动词（update/set/modify/write/put/patch/rotate/grant/disable/enable/reset/terminate/approve/merge/cancel），堵 `rotate_secret`/`grant_access`/`update_config` 等先前漏拦的写动作；`get_settings`/`list_secrets` 等只读工具靠 allow 前缀正确豁免
- **secret 凭据赋值熵复核**：`_shared_patterns.py` 新增香农熵检测，"凭据赋值"宽模式命中后复核值的熵（阈值 4.0），撤销低熵合法标识符（如 `defaultplaceholder`）的误报；高置信模式（sk-/ghp_/AKIA/bearer）不复核
- **审计日志共享模块** `_audit_log.py`：抽取原 bash-safety-wrapper.py 与 mcp-audit.py 中逐字重复的文件锁/轮转逻辑。bash-safety 加载失败 fail-closed（审计失败阻断命令），mcp-audit 加载失败 fail-safe（跳过审计，旁路无害）
- **健康检查新增 2 项**：验证 MCP 模式规则生效、双向验证 secret 熵阈值（阈值被改坏能被捕获）

### Fixed
- **bash-safety grant 消费 TOCTOU**：`approved_categories`（检查不消费）与 `_consume_grant`（删除）两步间存在竞态——并发命令可能都通过检查、都消费同一个一次性 grant，等于一份授权放行多条命令。新增 `acquire_grants()` 用跨平台文件锁（复用 `_audit_log.py` 的 `with_audit_log_lock`）把「检查全部 + 消费全部」原子化；锁不可用时 fail-safe 回退非加锁路径。8 并发争抢单 grant 验证恰好 1 个放行
- **rule-loader 缓存无界增长**：`should_inject` 写回时按插入顺序裁旧（上限 200 session），不再依赖未注册的 Stop 事件清理
- **bash-safety 审计重复记录**：`git commit` 被 DANGEROUS_PATTERNS 与 dangerous_git_operations 重复检测，审计日志记重复项，加保序去重修复
- **测试断言脱节**：`test_bash_safety.py` 与 health-check fixture 中多处断言信任根改造前的 exit 2 硬阻断，更新为验证 ask 决策（exit 0 + permissionDecision）
- **测试隔离**：`test_write_safety.py` 的 `run_hook` 默认指向隔离临时 grants 目录，防真实 `.grants/` 残留污染拦截测试

### Changed
- **健康检查冒烟写法**：移除 `check_runtime_constraints` 中 `if not X: pass` 的误导写法，改为直接调用（错误已在函数内 append）

### Docs
- **git -D 双 grant 说明**：`git-safety.md` 补充 `git branch -D` 同时命中 `GIT`+`DELETE` 两分类、需各建一个 grant；`-d` 只需 `GIT`
- **README 平台限制**：初次部署章节标注本系统仅 Windows 验证（硬编码 Python 路径、pythonw、taskkill、msvcrt、CREATE_NO_WINDOW），migrate.py 只做同 OS 跨机器迁移
- **审阅前必读 memory 纪律**：`conventions.md` 新增——审阅/排查类任务动手前先读 conventions/decisions，避免照搬通用清单把已豁免项（如 CC-Switch 的 token 明文）当问题重提

### Removed
- **Stop 事件死代码**：`rule-loader.py` 的 `clear_session_cache` / `detect_stop` / `event=="Stop"` 分支从未触发（Stop hook 未注册），缓存清理已由 LRU 接管，移除以减少认知负担

## [2.6.1] - 2026-06-05

### Added
- **会话级 grant 文件支持**：新增 `<category>.session` 格式的会话级授权文件，hook 检测到后放行但不删除，整个 Claude Code 会话期间持续有效，避免反复 touch 授权
  - `write-safety.py`：`_check_write_grant` 函数优先检测 `.session` 文件（不删除）
  - `bash-safety-wrapper.py`：`_grant_available` 函数同步支持 `.session` 文件
  - `session-start.py`：启动时自动清理上次会话残留的 `*.session` 文件，确保会话隔离
  - 授权提示新增"会话级授权"选项：`! touch ~/.claude/.grants/<category>.session`

### Changed
- **术语标准化**：修正文档中的 git 命令中文化问题，将"代码提交/推送远程/变基操作/合并操作/提交信息"等统一改为英文原文 `commit/push/rebase/merge`，符合 CLAUDE.md 语言规范"专业术语保留英文原文"
  - `git-safety.md`：4 处术语修正（操作类型表格 + 正文）
  - `conventions.md`：3 处 git 命令标准化
  - `CHANGELOG.md`：1 处标题术语修正
- **授权提示格式升级**：hook 拦截消息现显示三种授权方式（一次性 / 会话级 / 整个环境），将"会话授权"重命名为"整个环境授权"以区分新增的会话级 grant 文件
- **write-safety 输出精简**：优化 hook 拦截消息格式，移除冗余空行并在授权方式之间保留分隔空行，避免 Claude Code 自动折叠导致不便查看（主拦截 -1 空行、grants 拒绝 -2 空行、授权提示优化间距）
- **workflow 阶段标签输出规则**：明确阶段标签（`[好奇研究中]` / `[构思小鱼干]` / `[开工敲代码!]` / `[舔毛自检]`）仅在进入新阶段时输出一次，同一阶段内不重复输出，避免视觉噪音
- **[安全声明] 去重优化**：`git-safety.md` 事前声明规范新增"避免重复 hook 输出"说明——当操作被 hook 拦截时，hook stderr 已包含路径/原因/授权方式，此时 `[安全声明]` 仅需说明操作意图和业务原因，不重复授权命令

## [2.6.0] - 2026-06-04

### Changed
- **commit 信息必须写详细 bullet**：新增 memory 规范，后续提交需参考仓库历史风格，在 commit body 中用多条 bullet 说明关键改动、影响范围、验证/文档同步与重要取舍
- **提交前必须写 CHANGELOG**：新增 memory 规范，后续每次 `git commit` 前必须先检查并更新 `CHANGELOG.md`，并确认 changelog 已包含在待提交 diff 中
- **危险 Bash 命令拦截 deny → ask**：`bash-safety-wrapper.py` 对危险命令（git commit/push/merge、rm -rf、pip uninstall、npm -g、子壳等）命中且无 grant 时，从 `exit 2 硬阻断` 改为输出 `permissionDecision: ask` 的原生确认窗——主人点 Allow 即放行、Deny 即拒绝，日常 0 次手敲 `! touch`。grant/env 批量授权通道保留不变
- **grants 写入保护不变**：写 `.grants/` 目录仍是 exit 2 硬阻断（信任根铁律，不随危险命令一起降级 ask）

### Fixed
- **git -C/-c 等全局参数绕过 ASK**：`bash-safety-wrapper.py` 新增 `_git_subcommand` 解析器，跳过 `-C`/`-c`/`--git-dir`/`--work-tree` 等全局参数后识别危险子命令，堵死 `git -C repo commit` 绕过 ask 弹窗的缺口。同时 `dangerous_git_operations` 与正则结果互补不重复，已有分支删除/重置/清理/暂存/标签等子命令的精确覆盖
- **纠正"疑似 bypass"误判**：实证当前为 default 权限模式（会话 transcript `permission-mode: default`）。旧判断据"命令不弹窗"反推 bypass 有误——实为 hook 放行（exit 0）造成的假象，非 bypass。default 模式不吞 ask，是本次改 ask 可行的前提
- **decisions.md 重写**：拆为三条（default 实证 / 危险命令改 ask / grants 仍 deny），同步 MEMORY.md 索引

### Known issues
- hook 每次重跑都返回 ask，原生弹窗「don't ask again」对 hook-ask 不生效，连续同类操作会重复弹窗。如需「弹一次后本会话免弹」需加 PostToolUse 会话状态记忆（未实现）

## [2.5.2] - 2026-06-03

> 补记：此版本随 commit `5a1030d` 发布，当时漏写 CHANGELOG。

### Fixed
- **堵死 grant 自授权漏洞**：bash-safety + write-safety 新增最高优先级规则——AI 用 Write/Edit/Bash 写 `.grants/` 一律 deny（exit 2），防自建 grant 自我授权（信任根错位）。用 deny 而非 ask（当时认为 bypass 吞 ask）
- **修重定向误杀**：`ls .grants 2>/dev/null` 等读操作不再被当作写入拦截
- **清理 settings.local.json allow（65→37）**：删 .grants 写入后门、失效内联 marker、调试残留
- **测试加 deny 回归用例**（含读/重定向不误杀边界）；文档同步 deny 说明

## [2.5.1] - 2026-06-01

### Changed
- **CLAUDE.md**：静默模式块 12 行 → 3 行，仅保留触发方式/禁止语义推断/首行标识 + 指向 workflow.md；消除与 workflow.md 的重复内容，减少常驻上下文 token
- **skills/rules/workflow.md**：静默模式小节新增「设计理由」段落，收纳从 CLAUDE.md 搬来的"为何只能显式前缀"+ 退出机制
- **语言规范补充命令**：CLAUDE.md 与 skills/README.md 的语言规范均显式纳入"命令（如 `git`、`npm` 等操作）使用英文"，明确交流用中文、术语/标识符/命令用英文的分层
- **frontmatter 版本对齐**：CLAUDE.md `version` 2.4.2 → 2.5.1，修正此前 v2.5.0 提交漏改 version 行导致的漂移

## [2.5.0] - 2026-06-01

> 补记：此版本随 commit `8df7298` 发布，但当时漏写 CHANGELOG 且未同步 frontmatter version。

### Changed
- **三个安全 hook stderr 重写**：统一 `[安全守卫]` 格式，去掉"请先向主人说明意图"等面向 AI 的指令，新增命令摘要/目标路径/拦截原因/授权方式
- **write-safety**：`_print_grant_hints` → `_grant_hints_one_line` 精简授权提示
- **git-safety.md**：新增"事前声明要求"，含 `[安全声明]` 模板
- **workflow.md / CLAUDE.md**：补充高风险操作事前声明约束（核心约束第 4 条）
- **health-check**：新增 `check_hook_stderr_messages()` + 修复 `settings.local.json` 合并（`_load_merged_settings`）+ 更新 expected_text + fail-closed 检查字符串修正

## [2.4.2] - 2026-05-29

### Changed
- **CLAUDE.md**：frontmatter `version` 2.4.0 → 2.4.1，正文标题 v2.2 → v2.4，新增 CHANGELOG 交叉引用
- **skills/README.md**：新增 frontmatter（`version: 2.4.1`），标题 v2.4 → v2.4.1
- **skills/rules/output-formats.md**：`last-updated` 2026-05-22 → 2026-05-29
- **skill-boundaries.md**：移除已废弃的 `bash-safety.py` shim 引用（4 处），统一改为 `bash-safety-wrapper.py`
- **skills-health-check.py**：移除 `bash-safety.py` shim 残留检测（shim 已于 Plan-3 删除）
- **test_skills_health_check.py**：测试 fixture 中 shim 引用改为 wrapper

## [2.4.1] - 2026-05-28

### Added
- **授权文件机制**：`.claude/.grants/` 目录下的单次授权文件，hook 放行后自动删除；替代之前需退出 Claude Code 重开才能设环境变量的笨重方式
- **write-safety 授权出口补全**：`sensitive file path` 新增 `CLAUDE_HOOK_APPROVED_SENSITIVE=1` / `.grants/sensitive`；`embedded secret` 新增 `CLAUDE_HOOK_APPROVED_SECRET=1` / `.grants/secret`；`system auto-memory filename` 复用 `control-plane` 授权
- **mcp-safety 授权出口补全**：`credential pattern` 与 `blocklist` 统一走 `.grants/mcp` 或 `CLAUDE_HOOK_APPROVED_MCP=1`

### Fixed
- **AUTH_MARKER 锚点修复**：正则从 `^` 放开至 `(?:^|&&|[;&|])`，支持 `cd /path && MARKER cmd` 写法；`.match()` 改为 `.search()` 使其生效
- **write-safety/mcp-safety 不再永久堵死**：所有拦截点均可通过授权文件或环境变量放行
- **rule-loader docstring**：Stop 事件描述从 "always inject output-formats.md" 更新为 "no-op"
- **测试加固**：环境变量清理覆盖全部 hook 前缀；mcp-safety 测试增加 grant 文件清理

## [2.4.0] - 2026-05-28

### Added
- **静默模式显式触发**：静默模式仅在 prompt 以 `[silent]` / `[静默]` 前缀开头时进入；旧关键词只提示用户使用前缀，本次保持标准模式
- **MCP safety guard**：新增 `mcp-safety.py`，对 `mcp__.*` 工具调用扫描明文 secret，并对 `mcp_blocklist.json` 中的高风险 MCP 工具要求 `CLAUDE_HOOK_APPROVED_MCP=1` 授权
- **MCP audit hook**：新增 `mcp-audit.py`，记录 MCP tool name 与输入摘要到 `logs/mcp-audit.jsonl`，采用 fail-safe 策略
- **MCP hook 测试与 health-check**：新增 MCP 守卫/审计测试，并在健康检查中验证 MCP hook 注册、凭据阻断、正常放行和黑名单阻断
- **bash-safety SUBSHELL 分类**：阻断 `bash/sh -c`、PowerShell `-Command`/`-EncodedCommand`、shell heredoc 与 `xargs` 转 shell 等间接执行路径，需 `CLAUDE_HOOK_APPROVED_SUBSHELL=1` 显式授权
- **bash-safety 测试**：新增 SUBSHELL 阻断、授权放行、非 shell 解释器例外与 `eval` 暂不拦截的回归用例
- **util-check 行为验证**：健康检查新增子壳阻断与授权放行 smoke test

## [2.2.0] - 2026-05-22

### Added
- **rule-loader hook** (`skills/util-safety/hooks/rule-loader.py`)：监听 PreToolUse/UserPromptSubmit/UserPromptExpansion/Stop，按场景把对应规则文件内容通过 `hookSpecificOutput.additionalContext` 强制注入 AI 上下文，替代之前"靠 AI 自律按需读规则"的弱机制
- **session-start hook** (`skills/util-safety/hooks/session-start.py`)：会话启动时同步跑轻量自检（< 200ms），ERROR 通过 SessionStart additionalContext 告知 AI；后台 detached 跑完整 health-check，结果落 `logs/health-check-startup.jsonl`
- **模式审计日志** (`logs/mode-transitions.jsonl`)：rule-loader 在 UserPromptSubmit 时推断模式（silent/standard/quick）并记录，供事后分析模式分布
- 所有 `skills/rules/*.md`（7 个）首次添加 `version` + `last-updated` frontmatter
- 所有 `skills/util-*/SKILL.md`（5 个）追加 `version` + `last-updated` frontmatter
- `CLAUDE.md` 顶部添加 frontmatter `version: 2.2.0`
- `.gitignore` / `.gitattributes` / `README.md` / `CHANGELOG.md`（本文件）
- `skills/util-safety/tests/test_rule_loader.py`（10 case，全过）
- `skills/util-safety/tests/test_session_start.py`（4 case，全过）

### Changed
- **bash-safety**：合并 `bash-safety-wrapper.py` 与 `bash-safety.py` 为单进程，省一次 subprocess 启动（~50-150ms）；timeout 从 5s 提升到 15s，改用 `concurrent.futures.ThreadPoolExecutor` 实现内部超时（Windows 兼容）
- **bash-safety.py**：保留为薄壳，via `importlib` 委托给 wrapper，保持向后兼容（旧测试与 health-check L402 引用不变）
- **write-safety**：secret content scan 默认对所有 Write/Edit 内容生效（之前仅对 settings.json/CLAUDE.md），新增路径白名单（tests/fixtures/、__fixtures__/、node_modules/ 等）和文件名白名单（*.example/*.sample/*.template/README/CHANGELOG）
- **CLAUDE.md** 静默模式：从字面字符串匹配改为"语义判断 + 显式宣告"，必须以 `[模式:独自巡猎] 已进入静默模式。理由：...` 开头；强制刹车例外新增"git 写操作"
- **CLAUDE.md** Memory 段顶部：新增"优先级声明"明确忽略 Claude Code 系统默认 auto-memory，按本规范的 5 文件分类
- **settings.json**：注册 5 个新 hook（PreToolUse Skill / UserPromptSubmit / UserPromptExpansion / SessionStart / Stop），均指向新增脚本
- **util-check (skills-health-check.py)**：wrapper 自检新增 `DANGEROUS_PATTERNS` 关键字断言（验证合并完成），`TimeoutExpired` 匹配改为通用 `timeout`

### Removed
- `bash-safety.py` 的实际检测逻辑（移入 wrapper），仅保留 ~15 行薄壳

### Breaking
- **write-safety secret scan 默认全启用**：含密钥模式（sk-*、ghp_*、AKIA*、bearer 等）的普通源文件 Write/Edit 现在会被阻断；之前只阻断 settings.json/CLAUDE.md。如有合法的密钥示例需求，请使用 `.example`/`.template`/`.sample` 后缀或放在 `tests/fixtures/` 等白名单路径

### Notes
- `settings.json` 含敏感凭据，未纳入版本控制（`.gitignore`）；hook 注册变更必须手动在本地维护
- Rule injection 双通道：优先 `additionalContext`（v2.1.9+），同时 stderr 输出 debug 行作为兜底，规避 GitHub issue #19432 / #20062 的 additionalContext 偶发丢失

## [2.1.0] - 2026-05-18

基线版本（git tag `v2.1-baseline`）。

- 7 个 rules：workflow、git-safety、output-formats、memory、skill-org、tech-stack、skill-boundaries
- 5 个 util skills：util-check、util-init、util-memory、util-safety、util-session
- 双轨硬约束 hook：bash-safety-wrapper.py + bash-safety.py（双进程）、write-safety.py
- 健康自检脚本 skills-health-check.py
