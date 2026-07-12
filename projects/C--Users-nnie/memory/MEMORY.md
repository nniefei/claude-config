# Memory Index

> 每条规矩独立一行，hook 字段含最关键辨识词（召回命中标的）。**已 N 犯计数是金子**——标注哪些软约束最不可靠、应优先补硬兜底。
> 正文文件不再强制分类（v2.13.0 起回归原生），以下 3 个文件仅作物理容器。

## debugging.md

- [Windows_Python_subprocess_冷启动开销](debugging.md) — Windows Python subprocess 冷启动 ~730ms 固有开销，性能调优前先 in-process 测逻辑本身
- [session-start.py_70秒卡死](debugging.md) — ✅已解决的踩坑警示
- [Python进程堆积_递归spawn](debugging.md) — ✅已解决，留警示：大量文件排序用文件名内时间戳别 stat()、子进程递归 spawn 防 PID 锁+env 断链
- [control-plane.session_会话级grant反复失效](debugging.md) — control-plane.session 会话内持久、跨会话被 session-start cleanup 清理（resume 后 grant 反复失效的真相）
- [CC-Switch重写settings.json抹掉hooks](debugging.md) — CC-Switch 切换供应商整体重写 settings.json 抹掉 hooks 注册（切换后必查 hooks 字段）
- [write-safety critical files 元组项大小写依赖normalize](debugging.md) — CONTROL_PLANE_CRITICAL_FILES 加嵌套路径项必须全小写+正斜杠匹配 normalize_path 的 .lower() 输出（C--Users → c--users）；Edit 只改注释不动值是动作陷阱，改后 Read 重看真值

## decisions.md

- [运行模式实证_是default非bypass](decisions.md) — 环境是 default 非 bypass（实证）
- [危险Bash命令拦截_从deny改为ask弹窗](decisions.md) — 危险 Bash 命令改 ask 弹窗点 Allow 放行
- [grants写入保护_仍用deny硬阻断](decisions.md) — grants 写入仍 deny 硬阻断
- [secret凭据赋值熵复核_阈值4.0](decisions.md) — secret 凭据赋值熵复核阈值 4.0 偏向少误报
- [hook共享代码_审计锁vs secret副本](decisions.md) — hook 共享代码区别对待（审计锁收敛/secret 副本保留）
- [发布版本号SSOT_CHANGELOG顶版](decisions.md) — 发布版本号 SSOT=CHANGELOG 顶版，util-check 强制校验 CLAUDE.md+README
- [三套hook授权清单_不可并到README单一表](decisions.md) — bash/write/mcp 三 hook 各持授权清单，README 不可混拼一表，写文档前先 Read 各 hook 的 GRANT_CATEGORIES/路径前缀/GRANTS_DIR 定义

## conventions.md

- [commit必须写CHANGELOG](conventions.md) — commit 前必先写 CHANGELOG 并 staged
- [memory_5类分类_已废止](conventions.md) — memory 5 类分类已废止（v2.13.0 回归原生）
- [hook拦截后必须先请示_禁止绕过](conventions.md) — hook 拦 write/bash/mcp 后必先请示主人，禁止自建 .grants 绕过（已 2 犯）
- [settings.json中ANTHROPIC_AUTH_TOKEN明文_不要再提](conventions.md) — CC-Switch 产物，审阅时勿当问题提，勿建议迁出（已 4 犯）
- [git_commit信息必须写详细bullet](conventions.md) — commit 信息写详细 bullet，第一行版本号后必须跟摘要（别光秃秃一个版本号）
- [git_commit前必须先写CHANGELOG](conventions.md) — git commit 前必须先检查并更新 CHANGELOG，diff 须含 changelog 改动
- [bash与write两个安全hook授权机制已统一](conventions.md) — bash 与 write 两 hook 授权统一走 grants+env，内联 marker 失效
- [gitignore从忽略目录捞单个子目录_逐级开路](conventions.md) — gitignore 反否定逐级精确，`!父目录/` 会包含全部内容
- [审阅排查类任务开始前必先读memory](conventions.md) — 审阅/排查类任务动手前必读 conventions+decisions 正文，不只看索引（已反复犯）
- [workflow阶段标签同阶段只输出一次](conventions.md) — workflow 阶段标签同阶段只输出一次，`[模式:XXX]` 不是阶段标签
- [Hook输出的命令路径描述保持英文](conventions.md) — hook 输出的命令/路径描述保持英文，专业术语不翻译
- [health-check的hook校验_必须对齐hook-runner调度](conventions.md) — health-check 校验 hook 必须对齐 hook-runner 调度（展开 %USERPROFILE%+纯脚本名拼 SAFETY_HOOKS_DIR，否则 14 条误报）
- [改动后主动申请更新README](conventions.md) — 任务动过 hook/规则/Skill/health-check 项数/授权类别/目录/版本日期等"系统现状描述"时，收尾前主动申请是否更新 README，确认后再动
