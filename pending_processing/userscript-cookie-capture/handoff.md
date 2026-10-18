# Handoff: userscript-cookie-capture

写于 2026-10-18 · 停下原因：等主人合并实测（① 浏览器实测 + ② 真机抓取推送）

## Anchor

- `~/projects/aistudio-api`，分支 `master`，最后工作提交 `e19c10c825d60c5eaee439af12430cf17ad6150f`（实现+文档+静态产物单笔入库）；除本目录与 ① 的台账/handoff 记录外无其他未提交改动。
- 服务：管理引擎静默运行中，进程 `11487`、端口 11451（本日重启载入脚本路由；脚本按请求实时读盘，改文件即时生效无需再重启）；`curl 127.0.0.1:11451/health` → ok；`/userscript/aistudio-cookie.user.js` → 200 JS、更新地址按访问地址改写（本次核对）。
- 工具环境：ruff 为临时目录静态二进制（`$TMPDIR/ruff-bin/ruff-aarch64-unknown-linux-musl/ruff`，临时目录可能被清理）；pyright 经 npx 缓存可用；前端用 `web/node_modules` 本地工具（bun 未安装）。

## Approved scope

- 台账 `Confirmed plan`（2026-10-18）：全部实施授权（脚本 + 托管路由 + 弹窗指引 + 文档 + 单测 + 本地提交）；推送延至主人合并实测通过后（与 `1338801`、`4ca9947` 同行），随后两份台账一并归档。
- 已用限额：无外部配额类动作。
- 待主人推迟/重新确认：无。

## Done

- 实施并本地提交 — commit `e19c10c825d60c5eaee439af12430cf17ad6150f`（脚本 467 行、托管路由 30 行、SPA 放行、弹窗安装指引、`docs/USERSCRIPT.md` 与 README×2 / TERMUX / AGENT 各一句、路由单测 2 项、静态产物刷新）。
- 质量门禁 — pytest 255 通过（含新增 2 项）、ruff lint 全过、新文件格式干净（app.py 全库既有格式漂移未触碰）、pyright 0 错、eslint 0 告警、vue-tsc 仅 ProfileCard.vue 两处 master 既有报错、`vite build` 成功、`node --check` 通过。
- 服务重启与冒烟 — 管理引擎静默重启（继承原启动环境），旧进程 6724 → 新进程 `11487`；`/health` ok；脚本路由 200（`application/javascript`）、更新地址改写正确、占位符 0、`Accept: text/html` 不再被 SPA 兜底；部署产包含弹窗安装入口。
- 审查修正 — GM_cookie 存在但调用被拒时面板曾会误报「未读到额外项」，已补「读取失败」提示分支（随 `e19c10c825d60c5eaee439af12430cf17ad6150f` 入库，线上即时生效）。

## Next

1. 主人合并实测 — ② 桌面与手机 Firefox/Edge：装 Tampermonkey → 打开 `http://127.0.0.1:11451/userscript/aistudio-cookie.user.js` 安装 → 登录 aistudio.google.com → 面板「抓取并推送」；重点：无 GM_cookie 增强时可读子集是否足够导入；同时看控制台弹窗安装指引。① 见 ① handoff — 完成条件：主人确认可用；② 结论回填 `docs/USERSCRIPT.md`「实测记录」。
2. 推送 — `git push origin master`（`1338801`、`4ca9947`、`e19c10c825d60c5eaee439af12430cf17ad6150f` 及记录提交一并推送）— 由主人实测通过后发话触发。
3. 归档×2 — 两份台账更新为「已采纳并归档」、删除两份 handoff、整目录移入 `design_archive/`、提交并推送 — 完成条件：`pending_processing/` 两目录清空，归档提交已推送。

## Outstanding verification

- 主人真机验收（①②合并）— 见台账「验证与风险」；含 Firefox Android GM_cookie 可用性实测。
- `docs/USERSCRIPT.md`「实测记录」回填。

## Dead ends

- 无。
