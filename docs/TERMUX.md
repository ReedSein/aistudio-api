# Termux 安装与管理

此分支由 ReedSein 维护，优化 Android ARM64 Termux 的安装、启动和更新。Python 及其虚拟环境运行在 Termux，CloakBrowser 通过 proot Ubuntu 获得所需系统库。

## 安装位置

安装脚本的 `--dir` 表示项目文件夹的父目录，与运行安装命令时所在目录无关。

| 参数 | 项目位置 |
|---|---|
| 默认 | `$HOME/aistudio-api-termux` |
| `--dir "$HOME/projects"` | `$HOME/projects/aistudio-api-termux` |

目录名称可以包含空格。proot 的路径绑定语法使用冒号分隔，因此安装路径不能包含冒号或换行。使用 Termux 私有目录，避免 Android 共享存储的执行权限限制。

完整安装命令：

```bash
pkg update -y && pkg install -y curl && curl -fsSL https://github.com/ReedSein/aistudio-api-termux/releases/latest/download/install-termux.sh -o "$TMPDIR/aistudio-api-install.sh" && bash "$TMPDIR/aistudio-api-install.sh"
```

指定其他父目录时，直接选择下面这一行安装命令：

```bash
pkg update -y && pkg install -y curl && curl -fsSL https://github.com/ReedSein/aistudio-api-termux/releases/latest/download/install-termux.sh -o "$TMPDIR/aistudio-api-install.sh" && bash "$TMPDIR/aistudio-api-install.sh" --dir "$HOME/projects"
```

安装器按需安装 Python、Git、uv、curl、ca-certificates、proot-distro、gum，以及构建 CFFI、PyYAML 所需的 clang、pkg-config；创建或复用 `aistudio-api-termux` 浏览器容器（已登记的旧安装继续沿用其原有容器），按锁文件同步依赖，并安装 `aistudio` 命令。已有安装可再次运行相同命令续做；它不会自动覆盖源码修改，也不会替换其他仓库。

普通安装只取本 fork 最新正式 Release 对应的代码，安装脚本也来自正式发布附件。首次正式发布前，以上链接不可用；没有发布或联网失败时明确停止。

## 本机开发与测试

已有工作区可以直接接入，包含当前未提交修改：

```bash
cd ~/projects/aistudio-api
bash scripts/install-termux.sh --dev --dir "$HOME/projects"
aistudio
```

`--dev` 使用同一套安装、环境准备和管理流程，沿用现有配置、账号、数据、端口及浏览器容器，并保留环境中额外安装的开发工具。已有源码不拉取、不切分支，普通启动也不检查源码是否已提交；修改后重启服务即可使用当前 Python 代码。涉及依赖或浏览器准备时，再运行上述开发安装命令；准备环境需要先停止服务。

需要新建开发安装时，从 `master` 获取安装器并显式带上 `--dev`：

```bash
pkg update -y && pkg install -y curl && curl -fsSL https://raw.githubusercontent.com/ReedSein/aistudio-api-termux/master/scripts/install-termux.sh -o "$TMPDIR/aistudio-api-install.sh" && bash "$TMPDIR/aistudio-api-install.sh" --dev --dir "$HOME/projects"
```

只有目录尚不存在时才克隆 `master`。菜单会显示“本地开发”；主动更新跟随本 fork 的 `master`，存在源码修改、本地领先提交或分叉时保留本地工作并停止更新。在其他开发分支中自行用 Git 管理源码，启动和停止不受影响。

已有安装只需登记为开发用法、无需重做环境时，可运行 `aistudio --register --dev`；配置、数据和端口保持原值。

## 管理菜单

```bash
aistudio
```

| 操作 | 行为 |
|---|---|
| 普通启动 | 在当前窗口显示日志；Ctrl+C 停止服务并返回菜单 |
| 静默启动 | 后台运行；丢弃标准输出和错误，不写服务日志或请求转储 |
| 停止服务 | 核对进程身份和启动时间后停止；过期记录不会触发误杀 |
| 检查更新 | 普通安装轻量检查维护者选定的正式发布，不拉取源码；开发用法检查本 fork 的 `master` |
| 一键更新 | 更新应用、菜单、安装适配、锁定依赖及指定浏览器 |
| 清理原版安装 | 可选清理原作者旧安装的程序与缓存，默认保留其资料；先预览再确认 |
| 清理原版安装（清除资料） | 在上者基础上删除账号、设置与浏览器登录资料，删除不可恢复 |
| 卸载（保留数据） | 移除程序、运行环境、浏览器缓存和入口，保留配置、账号及统计 |
| 卸载（清除数据） | 额外清除明确列出的本安装配置和数据，确认后执行 |

默认端口是 11451，访问地址为 `http://127.0.0.1:11451/`。普通启动时终端会打印控制台地址；静默启动丢弃服务日志，该地址由菜单在启动成功后打印。端口可以在停止服务后修改：

```bash
aistudio --register --port 11451
```

在手机或桌面浏览器中导入 Cookie 时，可以安装服务自带的油猴脚本：浏览器装好 Tampermonkey 后，打开 `http://127.0.0.1:11451/userscript/aistudio-cookie.user.js` 安装，用法见 [USERSCRIPT.md](./USERSCRIPT.md)。

退出菜单会保留已启动的静默服务。Android 仍可能清理后台进程；本工具不会自动更改系统电池策略或设置开机自启。

`aistudio` 入口位于 `~/.local/bin/aistudio`。若当前 shell 没有将此目录加入 PATH，可直接使用该完整路径启动。

## 配置与数据

| 内容 | 默认位置 |
|---|---|
| 安装路径、菜单端口、开发用法与浏览器容器 | `~/.config/aistudio-termux/installation.json` |
| 应用配置 | `~/.config/aistudio-termux/config.yaml` |
| 进程记录和操作锁 | `~/.local/state/aistudio-termux/` |
| 账号、Cookie、统计和历史 | 项目已有的 `data/`，或原先配置的数据路径 |

配置和状态目录遵循 `XDG_CONFIG_HOME`、`XDG_STATE_HOME`。首次接入复制当前配置，后续不会覆盖；Web 控制台编辑的是这个独立配置。静默启动仍正常保存账号和统计等应用数据。

## 更新规则与失败恢复

产品更新来源固定为此 fork，原作者的改动由维护者评估、测试后再吸收。菜单不会自动同步原作者分支。仓库已改名为 `aistudio-api-termux`：安装器与更新器继续识别旧地址，旧命名的安装重跑新版安装脚本即可接续，原源码目录、浏览器容器及 `aistudio` 命令不变；新安装使用 `aistudio-api-termux` 源码目录与同名浏览器容器，不复用原作者遗留环境。

公开安装和更新以维护者标为最新正式版的 GitHub Release 为准，标签格式为 `termux-vX.Y.Z`；普通标签、草稿和预发布不作为正式版。一次更新选定版本后固定到其具体 Git 提交，日常推送 `master` 不影响公开版本。旧的 `master` 安装仅在能安全向前更新到正式版本时迁移；领先于正式版时不会自动降级或重置，可继续登记为开发用法。

打开管理菜单时会用约 3 秒的短超时检查一次最新正式版本，网页控制台进入时也会异步检查并显示在顶部；两处使用同一版本判断，只查询发布信息，不拉取源码、不要求本地干净、不参与模型请求。查询失败时提示暂时没查到，不影响正常使用；开发用法显示为本地开发，不按正式版判断。

Python 依赖版本由仓库内的 `uv.lock` 维护，使用端执行锁定同步。浏览器版本在 `scripts/cloakbrowser.version` 中维护；版本未变化时不重复下载，旧版本缓存保留。

更新仅接受直接向前的提交；源码有未提交修改、本地领先于目标版本、分支分叉、远端身份不符或网络出错时都会停止。新版本要写入的路径上已有本地文件（包括被 Git 忽略的文件）时同样会停止，不会覆盖它们；把报错中列出的文件移到别处后再更新。运行中的服务会正常停止，更新成功后按原模式启动，因此更新会造成短暂停机。

更新失败会显示阶段和更新前提交；详细阶段记录位于状态目录的 `last-update.json`，上一次依赖锁位于 `previous-uv.lock`。失败不会强制重置 Git 历史或删除用户数据，也不保证自动恢复旧服务。环境准备失败可以再次运行安装命令续做；源码有问题时由维护者提交修复或撤销提交，再更新安装。

`/health` 就绪检查只证明 HTTP 服务可用，Google 登录及模型调用是否可用仍取决于已导入的账号。

## 卸载与重装

在菜单中选择“卸载（保留数据）”或“卸载（清除数据）”，也可以运行：

```bash
aistudio --uninstall
# 清除数据：
aistudio --uninstall --clear-data
```

卸载会先显示范围，输入 `UNINSTALL` 才执行；取消不会停止服务。确认后核对并停止属于此安装的服务，再删除文件。

- 两种卸载都会移除项目的 `.venv/`、`.cloakbrowser/`、`aistudio` 命令入口和管理器的进程／更新记录。
- 普通安装额外删除 Git 跟踪的程序文件及项目 `.git`；未接管的本地文件保留，存在源码修改或仅保存在本地的提交／暂存工作时停止卸载。开发用法保留所有源码、未提交修改及 Git 记录。
- 默认保留独立配置、安装路径登记，以及项目的 `data/`、`dumps/`、`server-logs/` 和 `.env`。在原安装位置重跑同一种安装命令，可以继续使用这些配置和数据。
- 清除数据选项额外删除上述项目内数据，以及管理器的 `config.yaml` 和 `installation.json`。删除不可自动恢复；开发工作区中作为源码保留的配置模板不受影响。
- 共享 Termux 系统包、proot 浏览器容器和自定义外部数据路径保留。目录若是符号链接，只移除链接本身，不递归删除外部目标；未接管文件和操作锁可能使项目／状态目录继续存在。

## 清理原版安装

原作者脚本安装失败、改用本分支时，可以清理旧安装：

```bash
bash scripts/install-termux.sh --clean-legacy
# 连资料一起删除：
bash scripts/install-termux.sh --clean-legacy --clear-data
```

菜单中的「清理原版安装」两项与此共用同一套清理逻辑。

- 默认删除原安装的程序文件、Python 环境、浏览器缓存、自动生成的 uv.toml 与 Git 记录，保留 `config.yaml`、账号与统计、浏览器登录资料及未接管的本地文件。
- 「清除资料」选项额外删除 `data/`（含账号、统计与浏览器登录资料）、`dumps/`、`server-logs/`、`.env` 与 `config.yaml`，删除不可恢复；配置在项目外的数据目录不会被触碰。
- 原名 `aistudio-api` 的浏览器容器单独列入预览：本分支仍在使用或有进程占用时保留，其他情况确认后删除；其他容器、共享镜像与全局缓存不动。
- 执行前核对目录的 Git 来源必须是原作者仓库，并列出实际删除与保留范围，输入 CLEAN 才执行；目录不存在、来源不符、疑似仍在运行或指向已登记安装时会停止并说明；取消不会删除任何内容。

## 发布正式版本

维护者在本 fork 中选定并验证一个提交，使用 `termux-vX.Y.Z` 标签创建正式 GitHub Release，并将其设为最新版本。不要勾选草稿或预发布；从同一提交提取 `scripts/install-termux.sh`，以 `install-termux.sh` 为名上传为发布附件。完成这些步骤后，公开安装链接才可用。

推送源码或创建普通 Git 标签不会发布给使用者。发布时应在 ARM64 Termux 验证安装、启动和升级；原作者的 `v0.1.0` 等标签不属于本 fork 的正式 Termux 发布。版本标签发布后保持不变，修复通过新的正式版本提供。

GitHub 的最新正式发布与附件链接规则见 [链接到发布](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)。

## 维护与验证

开发时在单独分支评估原作者改动，验证后合入此 fork 的 `master`。维护者更新依赖锁后，应在 ARM64 Termux 验证；使用端不运行 `uv lock --upgrade`。

```bash
uv sync --locked --extra test
uv run --locked --extra test pytest
uv run --no-sync ruff check .
bun x pyright src scripts tests
bash -n scripts/install-termux.sh scripts/setup-termux.sh scripts/setup-env.sh scripts/setup-browser.sh scripts/cloakbrowser_termux/run-chrome.sh
```

开发检查工具需要单独准备；普通运行不需要 Bun、Java 或 Android 构建工具。
