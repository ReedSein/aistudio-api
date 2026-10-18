# Termux 安装与管理

此分支由 ReedSein 维护，优化 Android ARM64 Termux 的安装、启动和更新。Python 及其虚拟环境运行在 Termux，CloakBrowser 通过 proot Ubuntu 获得所需系统库。

## 安装位置

安装脚本的 `--dir` 表示项目文件夹的父目录，与运行安装命令时所在目录无关。

| 参数 | 项目位置 |
|---|---|
| 默认 | `$HOME/aistudio-api` |
| `--dir "$HOME/projects"` | `$HOME/projects/aistudio-api` |

目录名称可以包含空格。proot 的路径绑定语法使用冒号分隔，因此安装路径不能包含冒号或换行。使用 Termux 私有目录，避免 Android 共享存储的执行权限限制。

完整安装命令：

```bash
pkg update -y && pkg install -y curl && curl -fsSL https://raw.githubusercontent.com/ReedSein/aistudio-api/master/scripts/install-termux.sh -o "$TMPDIR/aistudio-api-install.sh" && bash "$TMPDIR/aistudio-api-install.sh"
```

指定其他父目录时，直接选择下面这一行安装命令：

```bash
pkg update -y && pkg install -y curl && curl -fsSL https://raw.githubusercontent.com/ReedSein/aistudio-api/master/scripts/install-termux.sh -o "$TMPDIR/aistudio-api-install.sh" && bash "$TMPDIR/aistudio-api-install.sh" --dir "$HOME/projects"
```

安装器按需安装 Python、Git、uv、curl、ca-certificates、proot-distro、gum，以及构建 CFFI、PyYAML 所需的 clang、pkg-config；创建或复用 `aistudio-api` 浏览器容器，按锁文件同步依赖，并安装 `aistudio` 命令。已有安装可再次运行相同命令续做；它不会自动覆盖源码修改，也不会替换其他仓库。

## 管理菜单

```bash
aistudio
```

| 操作 | 行为 |
|---|---|
| 普通启动 | 在当前窗口显示日志；Ctrl+C 停止服务并返回菜单 |
| 静默启动 | 后台运行；丢弃标准输出和错误，不写服务日志或请求转储 |
| 停止服务 | 核对进程身份和启动时间后停止；过期记录不会触发误杀 |
| 检查更新 | 检查 `ReedSein/aistudio-api` 的 `master` 分支 |
| 一键更新 | 更新应用、菜单、安装适配、锁定依赖及指定浏览器 |

默认端口是 11451，访问地址为 `http://127.0.0.1:11451/`。普通启动时终端会打印控制台地址；静默启动丢弃服务日志，该地址由菜单在启动成功后打印。端口可以在停止服务后修改：

```bash
aistudio --register --port 11451
```

退出菜单会保留已启动的静默服务。Android 仍可能清理后台进程；本工具不会自动更改系统电池策略或设置开机自启。

`aistudio` 入口位于 `~/.local/bin/aistudio`。若当前 shell 没有将此目录加入 PATH，可直接使用该完整路径启动。

## 配置与数据

| 内容 | 默认位置 |
|---|---|
| 安装路径和菜单端口 | `~/.config/aistudio-termux/installation.json` |
| 应用配置 | `~/.config/aistudio-termux/config.yaml` |
| 进程记录和操作锁 | `~/.local/state/aistudio-termux/` |
| 账号、Cookie、统计和历史 | 项目已有的 `data/`，或原先配置的数据路径 |

配置和状态目录遵循 `XDG_CONFIG_HOME`、`XDG_STATE_HOME`。首次接入复制当前配置，后续不会覆盖；Web 控制台编辑的是这个独立配置。静默启动仍正常保存账号和统计等应用数据。

## 更新规则与失败恢复

产品更新来源固定为此 fork，原作者的改动由维护者评估、测试后再吸收。菜单不会自动同步原作者分支。

Python 依赖版本由仓库内的 `uv.lock` 维护，使用端执行锁定同步。浏览器版本在 `scripts/cloakbrowser.version` 中维护；版本未变化时不重复下载，旧版本缓存保留。

更新仅接受直接向前的提交；源码有未提交修改、本地提交未发布、分支分叉、远端身份不符或网络出错时都会停止。运行中的服务会正常停止，更新成功后按原模式启动，因此更新会造成短暂停机。

更新失败会显示阶段和更新前提交；详细阶段记录位于状态目录的 `last-update.json`，上一次依赖锁位于 `previous-uv.lock`。失败不会强制重置 Git 历史或删除用户数据，也不保证自动恢复旧服务。环境准备失败可以再次运行安装命令续做；源码有问题时由维护者提交修复或撤销提交，再更新安装。

`/health` 就绪检查只证明 HTTP 服务可用，Google 登录及模型调用是否可用仍取决于已导入的账号。

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
