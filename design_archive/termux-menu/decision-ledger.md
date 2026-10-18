# aistudio-api Termux 优化版方案讨论记录

## 状态

已采纳并归档。正式说明、工程实现、本机接入及真实更新验证已完成；保留讨论、授权与验证依据。

## Confirmed plan

- 日期：2026-10-18。
- 确认消息：用户在完整执行方案后的独立消息中明确回复“确认按此方案执行”。
- 目标：建立由 ReedSein 维护的 aistudio-api Termux 优化版，先交付管理菜单及统一更新流程，再完成一键安装和说明。
- 代码与文档：仓库内 scripts 安装、菜单、浏览器适配及必要相关模块和测试；README.md、README_EN.md、docs/TERMUX.md；pyproject.toml、.gitignore、uv.lock。
- 本机接入：现有 ~/projects/aistudio-api 原地接入；安装 ~/.local/bin/aistudio，配置及状态目录使用 ~/.config/aistudio-termux/、~/.local/state/aistudio-termux/；新安装默认 ~/aistudio-api，--dir 指定父目录。
- 环境修改：按需安装必需 Termux 包，准备或补齐 proot Ubuntu 浏览器依赖；复用已有安装与容器，保留账号、Cookie、配置、历史数据和已有浏览器。
- Git 与外部动作：创建公开 fork ReedSein/aistudio-api；本地 origin 指向该 fork，原作者保留为开发用 upstream；隔离开发，按已验证逻辑单元本地提交，合入并推送自有 fork 的 master。
- 验证：隔离测试安装位置、安装续做、菜单及进程生命周期、静默输出、更新来源及失败分支，真实服务隔离健康检查，以及仓库要求的检查；不发起模型生成请求。
- 限制：不强制覆盖源码或改写历史，不创建 tag/release；不进行 API 业务重写、Android 伴侣 App 改造或开机自启。更新失败报告具体阶段和恢复方法。
- 补充确认：用户随后选择“按受影响范围验收（推荐）”，授权本次不执行未改动的前端和 Android App 检查；执行脚本检查、Python 全量测试、格式与类型检查、隔离安装和真实服务启动验证。原因是原仓库 AGENT.md 的全模块检查要求超出本次改动范围且本机缺少相应工具链。

## 实施进展

- 已创建公开 fork https://github.com/ReedSein/aistudio-api；本地 origin 已指向该 fork，原作者地址保留为 upstream。
- 隔离开发使用 termux-support 分支，基础提交 9bd5a20；交付后已核对并移除临时 worktree，保留本地开发分支。原浏览器缓存未删除。
- 已完成管理菜单、PID 身份核对及停止、静默策略、配置外置、自有 fork 更新器、统一浏览器版本、归档安全解压、按需容器依赖、一键安装及正式中英文说明的实现。
- 已验证：Python 全量 247 项通过；Pyright 分析 77 个文件，0 错误、0 警告；全库 Ruff check 通过；7 个新增 Python 文件格式检查通过；相关 Bash 语法检查通过。
- 已验证：冷缓存独立虚拟环境依赖安装成功；CloakBrowser --version 返回 Chromium 146.0.7680.177；真实后端隔离数据静默启动、HTTP /health、正常停止通过，未生成服务日志。
- 已验证：48 列真实终端菜单；静默启动后退出菜单服务仍健康，重新进入可停止；前台启动显示日志，Ctrl+C 正常停止并返回菜单。隔离浏览器使用阻断代理，未使用真实账号或发起模型生成。
- 依赖锁与原安装的 uv.lock 逐字节一致，未升级依赖；所有链接均为公开 HTTPS，无凭据或查询参数。TUR 的 8 个 pydantic-core 记录未提供哈希，保留上游生成结果，没有手工补造锁文件字段。
- 首装冷缓存构建证明 CFFI、PyYAML 需要本机编译，因此必要运行准备包含 clang、pkg-config；当前设备已安装，未进行额外系统工具链安装。Ruff、Bun 检查工具仅下载解包到 TMPDIR，未全局安装。
- 未执行未改动前端和 Android App 检查，依据补充确认按受影响范围验收。全库历史格式检查存在既有未格式化文件，本次未扩展为无关格式重写。
- 已提交并推送：bdedaf4 统一依赖与浏览器安装；3e7dd8c 交互菜单和更新管理；0455632 一键安装与正式说明。
- 已原地接入 ~/projects/aistudio-api 并登记 ~/.local/bin/aistudio，配置文件权限为 0600，默认端口 8000；交付时服务保持停止状态。
- 已通过真实已安装菜单检测 3e7dd8c 到 0455632 的更新，从自有 fork 直接向前更新，按锁文件检查依赖，复用未变化的浏览器，正常重新加载菜单；last-update.json 的阶段为 complete。
- 已从自有 fork 的公开 raw 地址下载安装脚本，与仓库版本逐字节比对后在 Home 目录执行 --dir "$HOME/projects"；安装续做成功，现有配置及数据保留。
- 默认 Home 位置完成静态核对；目录参数、空格、冲突、重复执行、源码修改与错误远端由隔离仓库及模拟命令验证，冷缓存依赖安装另行实测。未进行清空整机 Termux 的首装测试。
- 已审查本次源代码、脚本、测试与正式文档差异；生成的锁文件另以逐字节比较、全部版本与公开来源校验审查。没有未解决的本次阻断项。

## 目标与边界

- 项目定位为由用户维护的 aistudio-api Termux 优化版，首版以当前 Android ARM64 Termux 环境为验证目标。
- 顺序：先建立自有 fork 作为维护和更新来源，再优先完成中文终端管理菜单，随后补齐一键安装及正式说明。
- 菜单包含普通启动、静默启动、停止、检查更新、一键更新，并显示当前服务状态。
- 使用端的应用和管理脚本仅从自有 fork 更新；是否吸收原作者改动属于维护者的开发工作。
- 新安装默认在 Termux Home 下建立 `aistudio-api` 文件夹；允许通过参数指定放置这个文件夹的父目录。
- 当前 `~/projects/aistudio-api` 原地接入，不因新安装默认位置变化而迁移已有安装。

## 决策与理由

### 已确认

- 普通启动为前台模式，静默启动为后台模式。确认来源：本次用户对“普通前台、静默后台”的选择。
- 使用终端交互菜单作为主要入口。确认来源：用户提出“一行指令就能进入交互界面”。
- 更新范围包括项目、Python 依赖、项目指定的浏览器版本及必要的 Ubuntu 依赖。确认来源：用户选择“项目、Python 依赖、浏览器一起更新”。浏览器版本未变化时复用已安装版本。
- 先建立自有 fork，并计划逐步独立维护。确认来源：用户明确提出“到时候我们先建 fork”“打算未来独立出来”。这项方向确认不构成当前创建远端仓库或推送的授权。
- 使用端只跟随自有 fork，包含管理脚本本身；追踪原作者项目由维护者在开发中处理。确认来源：用户明确指出“我们的脚本更新就是要追随我们的 fork 仓库了”“追上游是我们开发才考虑的问题”。不能把任意当前远端当成产品更新来源。
- 默认新安装位置为 `~/aistudio-api`，可指定其他父目录。确认来源：用户要求默认安装在 Termux Home，并允许加参数选择目录。
- 产品定位为 Termux 优化版。确认来源：用户最新明确说明“我们的项目就是 termux 优化版”。

### 调查阶段事实

- 应用仓库：`/data/data/com.termux/files/home/projects/aistudio-api`，分支 `master`，当前提交 `9bd5a20`。
- 调查开始时应用仓库没有已跟踪文件改动；远端 `origin` 指向 `DeconstructedCube/aistudio-api`，远端 master 与本地一致。
- 2026-10-18 复查仍仅有未跟踪的 `pending_processing/` 草案目录，没有已跟踪文件改动。
- GitHub CLI 已能以 `ReedSein` 查询账号；查询 `ReedSein/aistudio-api` 返回 HTTP 404，目前没有查到该目标仓库。未创建仓库、修改远端或推送。
- 端口 8000、8080 当前均没有检测到监听者。
- 本机已有 `gum 0.17.0`、Python、uv、Git 和 curl；`~/.local/bin` 已在 PATH 中。
- 已找到 SillyTavern 原版 `start.sh`，其行为是安装 Node 依赖后直接启动，没有菜单；尚未定位用户记忆中的外层管理入口。
- 应用提供不需账号鉴权的 `/health`，返回 `status=ok`；它只能证明 HTTP 服务就绪，不能证明 Google 会话可用。
- 应用日志默认写终端，另外支持独立的请求转储文件开关。静默模式必须同时处理终端输出和请求转储，不能只隐藏屏幕输出。
- 现有 Termux 浏览器启动器按版本升序取第一个目录；当缓存包含多个版本时可能选到旧版本。本机管理工具应明确使用项目指定的浏览器，避免缓存顺序决定实际运行版本。
- `~/.local/bin/aistudio` 及候选实现、配置、状态目录当前均不存在，没有检测到同名命令。
- 当前 `pyproject.toml` 多数依赖使用版本下限，`uv.lock` 被 `.gitignore` 忽略。仅同步这些宽泛声明不能保证不同新安装得到相同的依赖版本；需要把经过 Termux 验证的锁文件纳入自有 fork 的维护范围。
- 已复现的安装问题包括容器安装依赖前未刷新 apt 索引、浏览器归档目录结构与脚本假设不一致；浏览器安装与启动也未统一按声明版本选择。

### 已纳入确认方案的设计

- 命令名暂定 `aistudio`，默认端口暂定 8000。
- 自有仓库采用公开 fork `ReedSein/aistudio-api`，沿用默认分支 `master`。本地 `origin` 指向自有 fork；原作者地址保留为开发用的 `upstream`。
- 管理菜单和安装脚本作为产品代码放进自有 fork，候选入口为 `scripts/termux_manager.py` 与 `scripts/install-termux.sh`。本机 `~/.local/bin/aistudio` 仅负责找到已登记的安装路径并进入菜单；配置及运行状态分别放在 `~/.config/aistudio-termux/`、`~/.local/state/aistudio-termux/`。
- 安装目录参数建议使用 `--dir` 表示父目录：不传参数安装到 `$HOME/aistudio-api`；`--dir "$HOME/projects"` 安装到 `$HOME/projects/aistudio-api`。目录、空格和参数边界应正确处理；已有无关目录或源码修改时不覆盖。
- 使用现有 gum 绘制菜单，使用 Python 标准库管理进程、状态和更新步骤，不额外安装终端界面框架。
- 保留 Python 在 Termux 宿主、CloakBrowser 在 proot Ubuntu 中运行的现有分工；安装器、更新器和启动器共用同一套依赖准备及浏览器版本要求。
- 安装器负责按需安装必需的 Termux 包（Python、Git、uv、curl、ca-certificates、proot-distro、gum），配置或复用浏览器容器，并登记菜单入口。重复执行能识别已有安装并续做；不自动删除旧安装。
- 将适用于 Termux 的 `uv.lock` 及对应软件源要求纳入自有 fork；安装和更新按锁文件同步，不在用户端主动升级到依赖仓库的最新版本。浏览器版本在一个权威位置维护，运行时准确使用该版本。
- 应用可变配置使用现有配置路径机制放到用户配置目录；账号、Cookie 和历史数据沿用已有位置并保护，不作为源码提交或更新覆盖对象。
- 停止前核对进程身份和启动时间；重复启动、端口冲突、失效的进程记录都应给出明确结果，不按名称批量结束进程。
- 普通启动在前台显示日志，Ctrl+C 正常停止服务后返回菜单；静默启动在后台，关闭菜单后继续运行。静默模式关闭服务的终端日志及文件转储；运行所需的数据正常保存。
- 更新前检查原有受 Git 管理的文件是否存在未提交修改；仅接受远端分支的直接向前更新，遇到本地修改或分叉时停止，不自动覆盖、暂存或重写历史。
- 检查与一键更新的产品来源明确绑定 `ReedSein/aistudio-api` 的 `master`。远端身份或跟踪关系不符时报告并停止；不回退到原作者或其他远端。
- 一键更新涵盖应用代码、管理菜单、安装适配、锁定的 Python 依赖和项目指定浏览器。浏览器版本未变化时复用缓存，更新完成后重新加载新菜单代码。
- 正在运行时更新，会先正常停止服务，更新并验证成功后按原来的模式启动；失败明确报告阶段与恢复办法，不伪报成功。
- 浏览器更新采用临时下载、检查、整理目录后再使用；保留已有版本。项目指定版本是唯一版本来源，并通过项目现有自定义浏览器入口接入明确的启动适配。
- 更新前记录当前提交与依赖锁；失败保留诊断和恢复依据，不自动强制重置历史。修复 apt 索引准备、归档目录整理和版本选择问题，不复制另一套冲突的浏览器安装逻辑。
- 在隔离开发分支实现；按“菜单及更新适配”“一键安装及说明”等可独立验证的逻辑单元进行本地提交，验证后合入并仅推送到自有 fork 的 `master`。不改写已有历史；不创建 tag 或发布 release。
- 上述整体工程、本机接入、系统包及容器准备、创建 fork、本地提交和向自有仓库推送已由 Confirmed plan 记录的独立确认授权。

## 未决问题

- 无。本次已确认范围内的交付和验证完成；后续实质范围变化按新的任务处理。

## 验证与风险

### 已完成

- 查看仓库状态、近期提交、现有启动入口、日志控制和健康检查代码。
- 查看本机工具、命令目录、端口占用和远端分支版本。
- 核对 gum 0.17.0 的命令帮助与官方示例。
- 只读核对 GitHub 登录账号及目标仓库查询结果。
- 核对 uv 官方关于提交锁文件、锁定同步和禁止运行时自动同步的说明。

### 工程验证结果

- 通过：临时目录、临时 Git 仓库和真实测试进程覆盖启动、静默输出、停止、过期身份、重复启动、端口冲突、元数据写入失败清理及重启环境保留。
- 通过：有更新、无更新、网络失败、本地修改、分叉和依赖准备失败；真实菜单额外验证从自有 fork 检查并更新。
- 通过：指定浏览器版本选择、缓存复用及模拟归档布局、安全路径检查；真实 Chromium 版本检查。
- 通过：自选父目录、空格、目录冲突、重复执行及失败续做的相关检查；默认位置静态核对，真实原目录重新安装成功。
- 通过：隔离账号数据的真实 HTTP 启动、/health 和停止，静默服务在退出菜单后继续健康；未发起模型生成请求。
- 通过：48 列真实终端显示、取消、菜单停止、普通启动与 Ctrl+C 返回。
- 通过：Python 247 项、Ruff check、77 文件 Pyright、7 个新增 Python 文件格式检查、相关 Bash 语法和源代码审查。全库既有格式偏差未扩展处理。

### 风险与回退原则

- 更新会短暂中断正在进行的请求，应在入口和使用说明中清楚表达。
- 创建公开 fork 和推送会使已提交代码对外可见；只提交本次明确路径，排除凭据、账号数据、本机配置及日志。
- 安装宿主包和准备浏览器容器会占用流量及磁盘，并改变相应环境；需要在整体方案单列。已存在的安装和容器优先复用。
- 静默模式失败仍需报告启动失败，后台运行期间不保存服务日志；账号、配置和用量数据属于应用数据，不能当作日志清理。
- Android 系统仍可能结束后台进程，本工具不改变系统后台策略。
- 代码缺陷使用新修复提交或 revert 回退，不重写历史；更新失败保留旧浏览器和更新前版本记录，提供源码及依赖恢复步骤，不承诺失败后自动恢复服务。
- 停用本工具的入口并恢复原启动命令可退回手动管理；回退不删除账号、配置、历史数据或共享依赖。

## 正式提升目标

- 正式使用说明：自有 fork 中的 `README.md`、`README_EN.md` 及 `docs/TERMUX.md`，写明 Termux 优化版定位、安装位置、菜单和更新来源。
- 产品实现：自有 fork 中的 `scripts/install-termux.sh`、`scripts/termux_manager.py`，以及 `scripts/setup-env.sh`、`scripts/setup-browser.sh`、`scripts/cloakbrowser_termux/run-chrome.sh` 的相关适配；必要的相关模块及行为测试。
- 依赖约束：`pyproject.toml`、`.gitignore` 与新纳入版本管理的 `uv.lock`；检查配置 `pyrightconfig.json` 增加脚本导入路径。
- 本机接入：现有 `~/projects/aistudio-api`，入口 `~/.local/bin/aistudio`，配置和状态目录 `~/.config/aistudio-termux/`、`~/.local/state/aistudio-termux/`。
- 外部写入：在 GitHub 创建公开 fork `ReedSein/aistudio-api`，并将验证后的本次改动推送到其 `master`。
- 正式说明已独立提升，本记录作为决策与验证记录归档，未整篇复制到产品使用说明。

## 证据

- `src/aistudio_api/main.py`：现有服务启动参数。
- `src/aistudio_api/api/routes_system.py`：`/health` 及日志配置接口。
- `src/aistudio_api/infrastructure/utils/logger.py`：终端日志和请求转储优先级。
- gum 官方 0.17.0 文档：https://github.com/charmbracelet/gum/tree/v0.17.0#choose
- uv 信号处理：https://docs.astral.sh/uv/concepts/projects/run/#signal-handling
- Git 直接向前更新：https://git-scm.com/docs/git-pull
- uv 项目锁文件：https://docs.astral.sh/uv/concepts/projects/layout/#the-lockfile
- uv 锁定与同步：https://docs.astral.sh/uv/concepts/projects/sync/
- Python 子进程与独立会话：https://docs.python.org/3.14/library/subprocess.html#popen-constructor
- Python 归档提取过滤器：https://docs.python.org/3.14/library/tarfile.html#extraction-filters
