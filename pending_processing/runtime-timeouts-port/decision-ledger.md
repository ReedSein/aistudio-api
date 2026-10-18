# 决策台账：运行参数前端可配置与默认端口 11451

> 状态：未采纳 · 讨论中（草案，不构成实施授权，不改任何正式状态）

## 状态

- 2026-10-18 建立；只读调查完成。
- 首轮重问题已决：主人选「方案甲」（单值静默超时），并额外要求支持「不超时」。
- 第二轮清单（T2）七项全部按推荐确认（2026-10-18 主人答复「都按你推荐来」）。
- 待共同理解补充确认后提交整体执行方案（T3）。

## 目标与边界

- ① 时间参数前端可配置：流式静默超时 `timeout_stream`（现 env `AISTUDIO_TIMEOUT_STREAM`，默认 120）、非流式请求超时 `timeout_replay`（`AISTUDIO_TIMEOUT_REPLAY`，默认 120）、浏览器空闲回收 `browser_idle_timeout`（`AISTUDIO_BROWSER_IDLE_TIMEOUT`，Termux 900 / 其他平台 1800）；并修掉两处绕过设置的 120 字面量（`client.py:329` 生图重放、`client.py:159` replay 门面默认值，门面当前无调用方）。
- ①附 支持「不超时」：主人明确要求，为「雷霆大思考」类长等待场景提供不设截止的选项（唯一新增行为）。
- ② 默认端口 11451：运行时默认（`config.py:257` 现 8080）、`.env.example`、Termux 安装器默认与帮助文本（`termux_runtime.py:69,84`、`termux_manager.py:95`，现 8000）、Docker（Dockerfile EXPOSE/HEALTHCHECK/CMD、compose ×2，现 8080）、文档（README.md、README_EN.md、docs/TERMUX.md、AGENT.md:43）、前端示例与 fallback（QuickApiExamples.vue、SettingsView.vue:57）。
- 边界：不动账号调度冷却值（`account_rotator.py:281-288` 的 60/300/600s、403 冷却 300/86400s），列入后续候选；不动浏览器调试端口 9222。

## 已查明事实（证据）

- 超时生效路径：`streaming.py:93` 读 `settings.timeout_stream`（每次调用实时读，可热生效）；`transport.py:226-258` 同一值先管「等 status」，每个 status/chunk 到达后重置截止，再管「块间静默」；`replay.py:24` 读 `settings.timeout_replay`；`session.py:170` 实时读 `browser_idle_timeout`。
- 浏览器侧 JS 也各有一个计时器，与 Python 截止值同源：
  - `streaming_init.js:27-44` 静默计时（收到响应与每个 chunk 时重置），超时推 `error:timeout` 事件并中止 fetch；
  - `hooked_request.js:3-8` 整次请求计时，响应头到达后清除（响应体读取仅受 Python `page.evaluate(timeout_s+5)` 约束）。
  - 两处均为 `(args.timeout || 60)`：**填 0 会被当成 60 秒**，不超时改造必须显式处理。
- `cdp_client.py:195-260` 的 `send` 以 deadline 轮询（0.2s 步进 + 浏览器存活检查）；`evaluate` 超时经此生效，传 `float('inf')` 即为不设截止。
- 0 语义现状：`browser_idle_timeout <= 0` 已表示「不自动回收」（`session.py:169-171`）；流式/重放 0 目前是立即失败且 JS 当 60，三处语义不统一。
- 不超时的兜底边界（已核实）：下游客户端断开→生成器取消→`STREAM_CLEANUP_JS` 中止 fetch；上游 error 事件→报错；页面/浏览器崩溃→每轮检查报错；账号切换→旧页排干等待上限 12s（`session.py:294 max_wait_s=12.0`）后强制关页；浏览器空闲回收仅在无在途请求时触发，不会误杀长请求；上游静默假死且页面存活→请求挂起直至下游断开（主人已表示接受）。
- config.yaml 机制：`model_defaults.py` `_load_yaml_config`（mtime 缓存 + `_default_config()` 合并）+ `invalidate_config_cache()`；`update_configured_logging_settings` 是「API 保存→写盘→失效缓存」先例；启动时 `app.py:39-40` 应用 logging.level；`PUT /config/yaml` 保存时同步应用 logging 段。
- 前端先例：`LogConfigCard.vue` + `PUT /config/logging`；`GET /config` 已返回 `timeout_stream`；SettingsView.vue:70 只读显示。
- Termux 安装：`~/.config/aistudio-termux/{installation.json,config.yaml}`；本机记录 project_root=本仓库、port=8000；服务当前未运行；更新器从 `ReedSein/aistudio-api` master 拉取（`termux_update.py`）。
- 质量门禁（AGENT.md §3.4）：`uv run ruff check .`、`uv run ruff format --check .`、`bun x pyright src tests`、`uv run pytest`（221 passed）、浏览器 JS 语法 `bun build … --no-bundle`、`cd web && bun run lint/type-check/build`（构建产物进 `src/aistudio_api/static/` 随仓库提交）。

## 决策与理由

- D1 已决：流式超时保持**单值静默超时**语义（首包等待与块间空闲共用，收到数据即重置），并新增**不超时**选项。
- D7 已确认：「不超时」统一约定为**数值填 0**；流式与重放都支持；浏览器空闲超时沿用 0=不自动回收。实现要点：两个 JS 钩子显式识别 0（跳过计时器，修掉 `||60` 坑）；Python 侧 0 时不设截止；hooked 的 `evaluate` 超时传 `inf`；界面值为 0 时显示「不超时」徽标。
- D2 已确认：新旋钮落 config.yaml 新段 `runtime:`；新增 `PUT /config/runtime`；`GET /config` 扩展返回 `timeout_replay`、`browser_idle_timeout` 及 env 覆盖标志；`PUT /config/yaml` 保存时同步应用 runtime 段；前端新增运行时配置卡片（仿 LogConfigCard），保存热生效。
- D3 已确认：优先级 env 显式设置 > config.yaml > 内置默认（启动时解析）；界面保存即时生效并落 config.yaml，env 生效时界面显示提示徽标。
- D4 已确认：默认值保持 120/120 与平台空闲默认（Termux 900）；要更长或不超时由界面调整。
- D5 已确认：Docker 全部切 11451（含 compose 宿主映射 11451:11451）；文档注明既有 Docker 部署宿主端口变化。
- D6 已确认：本机已登记实例（8000）在改造完成后 re-register 到 11451 并重启。
- D8 已确认：本次改动本地提交并推送 origin master。

## 未决问题（按主题）

- T3 实施授权：待主人对共同理解小结无补充后，提交整体执行方案并等待确认。

## 验证与风险

- 计划验证：新增/更新单测（runtime 配置读写与热生效、`PUT /config/runtime`、0=不超时在 transport 的分支、termux 默认端口）；`uv run ruff check .`；`ruff format --check`；`bun x pyright src tests`；`uv run pytest`；JS 语法校验（bun build --no-bundle）；`cd web && bun run lint && bun run type-check && bun run build`；手工冒烟（改超时值→界面即时反映→config.yaml 落盘→重启后保持；0 值→长思考请求不中断）。
- 风险：既有 Docker 部署升级 compose 后宿主端口变化（文档注明）；本机 8000→11451 迁移需停机重启（服务当前未运行）；旧 Termux 安装的 config.yaml 无 `runtime` 段，靠 `_default_config()` 合并提供默认，无需迁移；不超时模式下上游静默假死会挂起请求（已确认接受）。

## 拟提升目标

- 实施时（另待整体确认）：`src/aistudio_api/config.py`、`api/{app.py,routes_system.py}`、`infrastructure/gateway/{client.py,model_defaults.py,transport.py}`、`infrastructure/browser/js/{streaming_init.js,hooked_request.js}`、`web/`（types/api/新卡片/SettingsView/QuickApiExamples 及 static 构建产物）、`scripts/{termux_runtime.py,termux_manager.py}`、`config.yaml` 模板、`.env.example`、`Dockerfile`、`docker-compose*.yml`、`README.md`、`README_EN.md`、`docs/TERMUX.md`、`AGENT.md`、`tests/`。
- 台账归档：收束并完成后整体移入 `design_archive/runtime-timeouts-port/`。

## 后续记录

- 2026-10-18：主人将请求类超时默认值从 120/120 改为 **240/240**（流式静默、非流式；浏览器空闲保持平台默认 Termux 900）。D4 据此修订，其余清单项维持原确认。
- 2026-10-18：共同理解小结获补充后收束；整体执行方案已提交，等待主人确认（T3）。
- 2026-10-18：主人补充要求「启动时返回一条可打开的控制台网址」。已并入整体执行方案（归入提交②，与端口对齐同批）：
  - 服务端启动日志在「正在启动服务」旁输出「控制台地址: http://localhost:<实际生效端口>/」，端口随实际值，不写死；
  - Termux 菜单「静默启动」在健康检查通过后打印同样一行（静默模式丢弃服务日志，须由菜单侧补上）；
  - 现状证据：`app.py main()` 已有「正在启动服务，监听端口」日志；`termux_runtime.py:405`（普通启动）与 `termux_manager.py:51`（静默启动）为菜单输出点。

## Confirmed plan

- 2026-10-18 确认消息：主人「确认方案，开始做吧，等做完我测好就可以准备提交我们的fork里了」。
- 已授权：全部工程实建（超时三件套 + 0=不超时、修两处死值、端口 11451 全对齐、启动打印控制台网址、本机端口迁移与服务重启、上述验证运行）；本地按两个逻辑单元提交。
- 推送时序按主人本次消息调整：推送 origin master 改为主人测试通过后进行，不在本次实施阶段执行。
- 排除项：调度冷却参数、浏览器调试端口 9222、Android 端、两个 JS 的 60 秒内部兜底数值。
- 实施完成、推送待办的状态：implementing。
- 2026-10-18：实现完成并本地提交。质量门禁：pytest 253 通过、ruff lint 全过（全库格式漂移 21 个文件与原始基线一致，未触碰）、pyright 0 错、eslint 0 告警、前端构建成功；vue-tsc 两处报错为 master 既有（ProfileCard.vue，原始检出同样复现，未顺手修改）。剩余：主人测试 → 推送 origin master → 台账归档。
- 2026-10-18：本机迁移与冒烟完成。installation.json 端口 8000→11451；临时配置冒烟验证：240 默认值、PUT /config/runtime（0=不超时）落盘并热生效、启动日志逐字打印「控制台地址: http://localhost:11451/」、主人真实配置未被触碰；服务经管理引擎以静默模式在 11451 运行（进程 6724），等待主人测试。本条台账更新暂未提交，将随推送阶段一并入库。
- 2026-10-18：主人发起会话交接；按 grill-with-docs 写入 handoff.md（本目录），剩余步骤（主人测试 → 推送 → 归档）见 handoff 的 Next 节。本文件与 handoff 随后一并提交。
