# 环境迁移指南

把 `.claude/` 配置搬到新机器，或跨系统（Windows / Linux / Mac）迁移。

---

## 整体流程

```
旧机器                     新机器
  │                         
  ├─ 打包 .claude/ ──────→  ├─ 安装 Python 3.8+
  │                         ├─ 解压 .claude/
  │                         ├─ 编辑 env.json（改 1 个文件）
  │                         ├─ 运行迁移脚本
  │                         └─ 启动 Claude Code 验证
```

---

## 第 1 步：从旧机器导出配置

在**旧机器**上执行：

```bash
# 把 .claude/ 目录打包（用你习惯的方式）
tar czf claude-config.tar.gz -C ~ .claude/
# 或 zip
zip -r claude-config.zip ~/.claude/
```

然后把打包文件传到新机器（U 盘、scp、网盘等）。

---

## 第 2 步：在新机器上安装 Python 3.8+

如果新机器已有 Python，跳过此步。检查方法：

```bash
python3 --version   # 或
python --version    # 或 Windows 上
py -3 --version
```

输出 `Python 3.8+` 即可。

如果没有，按系统安装：

| 系统 | 命令 |
|:---|:---|
| **Windows** | 下载 https://python.org 安装包，或 `winget install Python.Python.3.11` |
| **Mac** | `brew install python@3.11` |
| **Ubuntu/Debian** | `sudo apt install python3` |
| **Fedora** | `sudo dnf install python3` |

---

## 第 3 步：把 .claude/ 放到新机器的家目录

```bash
# 假设打包文件在当前目录
tar xzf claude-config.tar.gz -C ~/
# 或解压 zip
unzip claude-config.zip -d ~/
```

确认目录存在：

```bash
ls ~/.claude/
# 应该看到: env.json  hook-runner.cmd  hook-runner.sh  scripts/  skills/  settings.local.json ...
```

---

## 第 4 步：编辑 env.json（这是唯一要手动改的文件）

```bash
vim ~/.claude/env.json
```

当前内容类似：

```json
{
  "python_exe": "C:/App/Python311/pythonw.exe",
  "os_type": "windows"
}
```

根据新机器修改：

### 如果是 Windows

```json
{
  "python_exe": "C:/Python311/pythonw.exe",
  "os_type": "windows"
}
```

> `python_exe` 改为新机器上 Python 的实际路径。
> 如果不知道 Python 装在哪，可以设为 `""`（留空），脚本会自动在 PATH 里找。

### 如果是 Linux

```json
{
  "python_exe": "/usr/bin/python3",
  "os_type": "linux"
}
```

### 如果是 Mac

```json
{
  "python_exe": "/usr/local/bin/python3",
  "os_type": "darwin"
}
```

> `python_exe` 可以用 `which python3` 查看实际路径。

**完成了，就改这一个文件，其他都不用碰。**

---

## 第 5 步：运行迁移脚本

```bash
python3 ~/.claude/scripts/migrate-env.py --apply
```

脚本会自动：

- ✅ 检测当前操作系统（Windows/Linux/Mac）
- ✅ 根据 OS 选择正确的路径格式（`%USERPROFILE%` 或 `$HOME`）
- ✅ 根据 OS 选择正确的入口文件（`.cmd` 或 `.sh`）
- ✅ 生成 `settings.local.json`（12 个 hook 命令一次生成）
- ✅ 验证 JSON 格式正确
- ✅ 验证所有 hook 脚本文件齐全

> Windows 上如果 `python3` 找不到，试试 `py -3` 或 `python`。

---

## 第 6 步：启动 Claude Code 验证

1. 启动 Claude Code：

```bash
cd 你的项目目录
claude
```

2. 在对话中跑健康检查：

```
/util-check
```

3. 确认所有检查项通过。

4. 手动验证 hook 是否生效：试着编辑一下 `.env` 文件，看 write-safety 会不会拦截。

---

## 如果迁移中遇到问题

| 症状 | 原因 | 解决 |
|:---|:---|:---|
| `migrate-env.py` 报 `python3: command not found` | 没装 Python 或 PATH 没配 | 安装 Python 3.8+，或用 `py -3` 代替 `python3` |
| `migrate-env.py` 报 JSON 解析错误 | `env.json` 格式不对 | 检查引号、逗号，用 `python3 -c "import json; json.load(open('env.json'))"` 测试 |
| Claude Code 启动后所有 hook 无响应 | Python 路径不对 | 检查 `env.json` 的 `python_exe` 值是否正确 |
| `/util-check` 报 hook 缺失 | hook 脚本没拷贝全 | 确认 `~/.claude/skills/util-safety/hooks/` 下有 `.py` 文件 |
| 写文件时不弹拦截直接成功 | write-safety 没加载 | 检查 `settings.local.json` 的 `hooks.PreToolUse` 有没有 `write-safety.py` |
| 危险命令不弹确认窗 | bash-safety 没加载 | 同上，检查 `bash-safety-wrapper.py` 条目 |

---

## 补充说明

### 跨系统迁移（如 Windows → Linux）

迁移脚本的 `--apply` 会自动处理：

- 路径变量：`%USERPROFILE%` → `$HOME`
- 入口文件：`hook-runner.cmd` → `hook-runner.sh`（并 `chmod +x`）
- Python 名称：`pythonw` → `python3`

不需要手动改任何路径，只需修改 `env.json` 里的 `os_type` 和 `python_exe`。

### 如果新机器没有命令行能运行迁移脚本

找一台有 Python 的机器，运行：

```bash
python3 ~/.claude/scripts/migrate-env.py --dry-run
```

把输出的 `settings.local.json` 内容存成文件，再复制到新机器覆盖同名文件即可。

---

## 附录：迁移前后文件变化

| 文件 | 迁移前（旧机器） | 迁移后（新机器） |
|:---|:---|:---|
| `env.json` | `python_exe: C:/App/Python311/...` | → 手动改为新机器路径 |
| `settings.local.json` | `%USERPROFILE%/.claude/...` | → 不变（系统变量自动适配） |
| `hook-runner.cmd` | Windows 入口 | → 不变（`%~dp0` 自定位） |
| `hook-runner.sh` | Unix 入口 | → 不变（`dirname $0` 自定位） |
| `scripts/hook-runner.py` | 核心调度器 | → 不变（自定位 + 读 env.json） |
| `settings.json` | `ANTHROPIC_AUTH_TOKEN` 等 | → 需重新配置或恢复 |