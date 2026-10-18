# Handoff: runtime-timeouts-port

写于 2026-10-18 · 停下原因：主人切换会话；推送在等主人测试，归档在等推送

## Anchor

- `~/projects/aistudio-api`，分支 `master`，最后工作提交 `1338801`；本目录（台账 + handoff）随后一并提交，除台账/handoff 外无其他未提交改动。
- 服务：管理引擎静默运行中，进程 6724、端口 11451（`~/.local/state/aistudio-termux/service.json`）；`curl 127.0.0.1:11451/health` → ok（本次核对）。安装记录 `~/.config/aistudio-termux/installation.json` 端口已为 11451。
- 工具环境：`.venv` 已含 test extra（pytest 9.1.1）；ruff 未安装（本次用官方 musl 静态二进制，位于 `$TMPDIR/ruff-bin/`，临时目录可能被清理）；前端用 npm（bun 未安装），`web/node_modules` 已装（gitignored）。

## Approved scope

- 台账 `Confirmed plan`（2026-10-18）：超时三件套前端可配置（0=不超时）、端口 11451 全对齐、启动打印控制台地址、本机迁移；原计划两个提交合并为一个。
- 已用限额：无外部配额类动作。
- 待主人推迟/重新确认：**推送 origin master**（主人明确改为「测试通过后推送」）；台账归档随推送后进行。

## Done

- 实现与本地提交 — commit `1338801`（39 文件：超时/端口/启动网址/配置模板/文档/测试/静态产物/台账）。
- 质量门禁 — `pytest` 253 通过、`pyright` 0 错、ruff lint 全过（全库格式漂移 21 文件与原始基线一致）、eslint 0 告警、`vite build` 成功（均为本会话末次运行结果）。
- 本机迁移与冒烟 — 登记端口 11451；临时配置冒烟：240 默认值、`PUT /config/runtime`（0=不超时）落盘并热生效、启动日志逐字打印「控制台地址: http://localhost:11451/」；主人真实配置未被触碰。
- 已知遗留（未修）— vue-tsc 在 `ProfileCard.vue` 两处报错为 master 既有（原始检出入同样复现）。

## Next

1. 主人在浏览器实测（打开 `http://localhost:11451/`，设置页「运行参数」改值、填 0 发长思考请求、菜单重启后配置保持）— 完成条件：主人确认功能可用。
2. 推送 — `git push origin master`（`1338801` 与后续台账/交接提交一并推送）— 完成条件：远端 master 出现 `1338801`；由主人测试通过后发话触发。
3. 归档 — 更新台账状态为「已采纳并归档」、删除本 handoff、整目录移入 `design_archive/runtime-timeouts-port/`、提交并推送 — 完成条件：`pending_processing/` 中不再有本目录，归档提交已推送。

## Outstanding verification

- 主人真机功能验收（改值与 0 值行为、重启保持、菜单静默启动打印网址）— 见台账「验证与风险」。

## Dead ends

- 无（两处死值修复与前端构建路径偏差已记录在台账）。
