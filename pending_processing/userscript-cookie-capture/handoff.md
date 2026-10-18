# Handoff: userscript-cookie-capture

写于 2026-10-18 · 停下原因：等主人更新脚本（0.1.1）后重测（① 浏览器实测 + ② 真机抓取推送）

## Anchor

- `~/projects/aistudio-api`，分支 `master`，最后工作提交 `bae714c651cbe151390dd4e74207814ea117a0b0`（v0.1.1 Trusted Types 修复）；除本目录记录外无其他未提交改动。
- 服务：管理引擎静默运行中，进程 `11487`、端口 11451；脚本路由按请求读盘，已即时供应 0.1.1（本次核对）。
- 工具环境：ruff 为临时目录静态二进制；pyright 经 npx 缓存可用；前端用 `web/node_modules` 本地工具（bun 未安装）。TT 复现用的无界面 Chromium 与临时静态服务已清理。

## Approved scope

- 台账 `Confirmed plan`（2026-10-18）：实施授权（脚本 + 托管路由 + 弹窗指引 + 文档 + 单测 + 本地提交与修复）；推送延至主人合并实测通过后（与 `1338801`、`4ca9947`、`e19c10c`、`d19ac30` 同行），随后两份台账一并归档。
- 已用限额：无外部配额类动作。
- 待主人推迟/重新确认：无。

## Done

- 实施与首版提交 — commits `e19c10c`（实现）、`d19ac30`（记录）。
- 实测故障与修复 — 主人报告「已装、启用正常，但页面上无面板」；定位为 aistudio.google.com 的 Trusted Types 策略拦截 v0.1.0 的 `wrap.innerHTML` 赋值（静默中止、无痕迹）；参考脚本零 innerHTML、全 createElement，印证成因。修复提交 `bae714c651cbe151390dd4e74207814ea117a0b0`（v0.1.1）：createElement 构建 + try/catch 失败弹窗 + 「打开 Cookie 抓取面板」菜单命令 + `applyOpen` 空守卫。
- 修复验证 — 同策略本地复现：旧版 mounted=false + TrustedHTML 报错 → 新版 mounted=true、零未捕获错误；无策略环境无回归；截图目视面板与按钮渲染正常；`node --check` 通过；线上路由已供应 0.1.1。

## Next

1. 主人更新脚本并重测 — 在 Edge 打开 `http://127.0.0.1:11451/userscript/aistudio-cookie.user.js`（安装页会提示更新到 0.1.1）→ 刷新 aistudio.google.com → 右下角面板「抓取并推送」；若仍异常，脚本现在会自行弹出错误提示，转告提示内容即可 — 完成条件：主人确认可用。
2. 推送 — `git push origin master`（`1338801`、`4ca9947`、`e19c10c`、`d19ac30`、`bae714c651cbe151390dd4e74207814ea117a0b0` 及记录提交一并推送）— 由主人实测通过后发话触发。
3. 归档×2 — 两份台账更新为「已采纳并归档」、删除两份 handoff、整目录移入 `design_archive/`、提交并推送 — 完成条件：`pending_processing/` 两目录清空，归档提交已推送。

## Outstanding verification

- 主人真机验收（①②合并）— 见台账「验证与风险」；含 Firefox Android GM_cookie 可用性实测。
- `docs/USERSCRIPT.md`「实测记录」回填。

## Dead ends

- v0.1.0 的 `wrap.innerHTML` 面板构建 — 被页面 Trusted Types 拦截且静默失败，已弃用（见修复提交）。
