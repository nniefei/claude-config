# Debugging Memory

## Windows_Python_subprocess_冷启动开销

**现象**：Windows 上 hook 单次执行 800-1500ms，远超计划文档"<200ms"目标；以为是 hook 代码效率问题。

**根因**：每次以 subprocess 形式调用 Python 脚本，Windows 上至少要付 ~730ms 的 Python 解释器冷启动开销（实测 `C:/App/Python311/python.exe -c pass` 平均 732ms/call）。这是 Windows 进程创建 + Python 解释器初始化的固有开销，跟 hook 代码本身效率无关。

**验证方式**：用 in-process 调用（`importlib.util.spec_from_file_location` 加载并直接调函数）测 hook 逻辑本身耗时——实测在 0.00ms/call（接近测量精度下限），证明瓶颈在 subprocess 启动而非代码。

**解决方案**：
- 调优时先用 in-process 测一遍，确认逻辑本身已经够快就别再优化 Python 代码
- 测试场景下 timeout 阈值 ≥ 3s，避免误判（如 session-start 同步段的 < 3s 阈值就是包含冷启动开销的合理值）
- 真要把延迟压到 200ms 以下，方案不在 Python 优化，而在改用 native 二进制（Go/Rust）或预启动常驻进程

**已过时标记**：当前未过时（2026-05-22 实测数据）

**相关**：`plans/gentle-doodling-crescent.md.done` 阶段 9 Step 5 性能基准

---

## session-start.py_70秒卡死（✅ 已解决，留作踩坑警示）

**现象**：跑 `skills-health-check.py` 报 `session-start.py 同步段超过 10s`；直接 `subprocess.run(session-start.py)` timeout 20 秒不退出。

**根因**：`spawn_background_check()` 末尾用 `sorted(glob("run-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)`——对 `health-check-runs/` 里堆积的 531 个文件**每个都调 `stat()`**，Windows IO 累积 70 秒，阻塞 session-start 同步路径。

**修复**：文件名格式 `run-{YYYYmmddTHHMMSS}-{pid}.log` 字典序即时间序，去掉 `stat()`：
```python
run_logs = sorted(runs_dir.glob("run-*.log"), reverse=True)
```
commit `3d3a95a session-start: avoid per-spawn O(N) stat() in log retention`。

**踩坑警示**：未来任何在 Windows 上对大量文件排序，应优先考虑文件名内编码时间戳，避免 `stat()` O(N) IO。误诊弯路：一度以为是 detect_stop 改动 / Windows 子进程 fd 继承，均无用——最终靠 in-process 逐函数打点定位到 `spawn_background_check()` 才破。详见 `skills/util-safety/SKILL.md`「日志与文件保留策略」章节。

---

## Python 进程堆积：session-start 健康检查递归 spawn（✅ 已解决，留作踩坑警示）(2026-05-29)

**症状**：任务管理器中出现几十个 `pythonw.exe`/`python.exe`，电脑变卡、发热。

**根因**：三条链路叠加 —
1. `session-start.py` 每次 SessionStart 用 `DETACHED_PROCESS` spawn `skills-health-check.py`，父进程退出后子进程无法回收。
2. `skills-health-check.py` 的 `check_new_hook_behaviors()` 对 `session-start.py` 做行为测试（subprocess.run），被测的 session-start 又 spawn 新的 health-check，形成递归链。
3. 审计日志（bash-safety-audit / health-check-startup / mcp-audit）无界增长到 45MB。

**修复**（3 处）：
1. `session-start.py` `spawn_background_check()`：移除 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`，仅留 `CREATE_NO_WINDOW`；新增 PID 锁文件 `logs/health-check.pid`，每次 spawn 前 `taskkill` 上一轮；新增 `CLAUDE_HEALTH_CHECK_CONTEXT=1` 检查阻断递归。
2. `skills-health-check.py` `_sanitized_hook_env()`：把 `CLAUDE_HEALTH_CHECK_CONTEXT=1` 加入测试环境。
3. 审计日志裁剪为最近 200-500 行。

**效果**：最多 1 个 health-check 进程运行，不再堆积；日志总量 ~45MB → ~0.1MB。

---

## control-plane.session 会话级 grant 反复失效 (2026-06-17)

**现象**：静默模式连续改多个控制平面文件（CHANGELOG.md / skills/ 下脚本），每次 `! touch ~/.claude/.grants/control-plane.session` 授权后只能成功一次 Edit，下一次 Edit 又被 write-safety 拦，需重新 touch。一度误判为「session grant 消费即删」。

**真凶（非消费即删）**：`write-safety.py` 的 `_grant_available()` 检查 `{key}.session` 与 `{key}`，`_consume_grant()` **只 unlink `{key}`（一次性），从不删 `{key}.session`**。实测连续两次 Edit 同一 session grant 均 exit=0、grant 文件持久存在——**会话内 session grant 本就持久有效**。

真正清理发生在 `session-start.py` 的 `cleanup_session_grants()`：SessionStart 事件遍历 `.grants/*.session` 全部 unlink（设计目的：会话隔离，清上次会话残留）。本轮频繁失效是因为对话轮次间触发了 SessionStart（resume/新会话启动），把 `.session` 清掉。

**正解**：
- `.session` grant **同一会话内持久有效**，不必每次 Edit 都 touch
- 跨会话/resume 后被 session-start 清理，需重新 touch——这是设计行为非 bug
- 若嫌跨会话反复授权烦，在**启动 Claude Code 的真实终端**预设 `export CLAUDE_HOOK_APPROVED_CONTROL_PLANE=1`（env 授权永不消费、不被 session-start 清，真正一劳永逸）。注意：会话内 Bash `export` 对 hook 子进程无效
- 一次性 `control-plane`（无 .session）才是消费即删——别和 `.session` 混淆

**相关**：[[conventions]] hook 拦截后必须请示禁止绕过；git-safety.md 的「授权机制」章节

---

## CC-Switch 切换供应商会整体重写 settings.json，抹掉全部 hooks 注册 (2026-07-07)

**现象**：Edit CLAUDE.md 时 write-safety hook 完全没触发（连 hook-performance.jsonl 都无记录），只弹了 Claude Code 默认权限框——安全守卫整体失效。

**根因**：CC-Switch 每个 provider 在 `~/.cc-switch/cc-switch.db` 的 `providers.settings_config` 里存的是**完整 settings.json 快照**，切换时**整体覆盖** `~/.claude/settings.json`。当前 provider（Any Router Claude）的快照里没有 `hooks` 字段，14:23 切换后 hooks 注册全部丢失。库里 `settings.common_config_claude` 倒是完整保留了 hooks 块，但这次切换并未把它合并进来。**所有 provider 的 settings_config 都不含 hooks**——每次切换供应商都会复发。

**定位证据链**：`logs/hook-performance.jsonl` 最后一条 hook 执行是 14:22:57（write-safety deny），settings.json mtime 14:23:43，`.cc-switch/settings.json` mtime 同为 14:23（provider 切换动作），之后所有工具调用零 hook 日志。

**修复/预防**：
- 事后恢复：从 `cc-switch.db` 的 `common_config_claude`（`SELECT value FROM settings WHERE key='common_config_claude'`）取出 hooks 块写回 settings.json
- 根治：在 CC-Switch UI 里把 hooks 块加入各 provider 配置或确认「公共配置」在切换时生效
- 警示：**每次 CC-Switch 切换供应商后，应检查 settings.json 是否还有 hooks 字段**；hooks 失效时 write-safety/bash-safety 全部裸奔

**相关**：[[conventions]] settings.json token 明文豁免（CC-Switch）；`skills/util-safety/SKILL.md`「hook 入口由全局 settings.json 注册」

---

## write-safety critical files 元组项大小写依赖 normalize.lower() (2026-07-13)

**现象**：给 `write-safety.py` 的 `CONTROL_PLANE_CRITICAL_FILES` 加嵌套路径项 `projects/C--Users-nnie/memory/memory.md` 守 MEMORY.md 索引，自检脚本实测 `is_control_plane_path()` 返回 False、测试用例 `test_facade_files_guarded` FAIL，但 README.md / MIGRATION.md 两个裸名项却 PASS。

**根因**：`normalize_path()`（L238-239）= `file_path.replace("\\","/").lower()`——整条路径统一正斜杠 **且全小写**。`control_plane_relative_path()` 截取 `/.claude/` 后的相对路径做匹配，匹配是 `relative in TUPLE` 精确字符串比对。被守目标经过 normalize 后 `relative = "projects/c--users-nnie/memory/memory.md"`（全小写），但元组项我写的是 `"projects/C--Users-nnie/memory/memory.md"`（保留原 `C--Users` 大写）→ 永远不相等。**既有的 4 个 critical files（settings.json / claude.md 等）都是裸文件名、碰巧全小写**，所以这套 `.lower()` 副作用一直没暴露——**首次出现含大写字母的嵌套路径项才显形**。

**踩坑警示**：
- 给 `CONTROL_PLANE_CRITICAL_FILES` 新增任何**嵌套路径项**（非 .claude/ 根级裸文件名），元组项必须**全小写 + 正斜杠**匹配 normalize 的输出。含大写字母的段（如项目编码 `C--Users-nnie` = `C:` 经 `C--` 编码）最易踩
- 调试方法：用 `importlib.util.spec_from_file_location` 动态加载 write-safety，直接 print `normalize_path(p)` / `control_plane_relative_path(n)` 的输出对照元组项，一眼定位
- 这条也适用于 `CONTROL_PLANE_PREFIXES` 等其他经 normalize 比对的常量

**副踩坑（操作纪律）**：自检时我一眼看出"元组项大小写问题"，但动手发 Edit 时第一次只改了**注释**、没改**字符串本身**（`old_string` 与 `new_string` 内容主体一致、只注释动了），工具提示 Edit 成功但行为没变。教训：**Edit 成功 ≠ 改对**——`old_string` 和 `new_string` 若只在注释/空白有差异，工具照常 success，实际值未变。改完需用 Read 重看那一行确认真值变了，或用 Grep 对目标 token 精确核对，别凭"Edit 返回 success"就以为改到位。

**修复**：元组项改为 `projects/c--users-nnie/memory/memory.md`（全小写）；新增 `test_facade_files_guarded` 含正向（3 门面被 code==2 拦截）+ 反向（3 个 memory 正文文件仍放行）双向断言防回退；既有 `test_auto_memory_filenames` 的 allowed_paths 把 MEMORY.md 移出（它现在归 control-plane 层守，auto-memory 黑名单层本就不该拦，两层独立）。

**相关**：[[三套hook授权清单_不可并到README单一表]]（同次审阅发现）；`write-safety.py` L112-120 的 `CONTROL_PLANE_CRITICAL_FILES`

