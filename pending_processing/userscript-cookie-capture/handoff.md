# Handoff: userscript-cookie-capture

写于 2026-10-18 · 停下原因：等主人开启完整 Cookie 读取（油猴设置）并更新到 0.1.2 后重测（① 浏览器实测 + ② 真机抓取推送）

## Anchor

- `~/projects/aistudio-api`，分支 `master`，最后工作提交 `8bdd96510d61d65c868a450fd6c23f7ee1f93443`（文档：HttpOnly 开启指引与实测回填）；除本目录记录外无其他未提交改动。
- 服务：管理引擎静默运行中，进程 `11487`、端口 11451；脚本路由按请求读盘、已即时供应 0.1.2。探活链路核验：经本地代理可达 Google，未认证请求回 401（正常）。
- 工具环境：ruff 为临时目录静态二进制；pyright 经 npx 缓存；前端用 `web/node_modules` 本地工具；TT 复现与探子证据临时文件均已清理。

## Approved scope

- 台账 `Confirmed plan`（2026-10-18）：实施授权（脚本 + 托管路由 + 弹窗指引 + 文档 + 单测 + 本地提交与修复）；推送延至主人合并实测通过后（全部本地提交一并推送），随后两份台账一并归档。
- 已用限额：无外部配额类动作。
- 待主人推迟/重新确认：无。

## Done

- 实施与首版提交 — `e19c10c`、`d19ac30`；Trusted Types 修复 — `bae714c`、记录 `05132b2`；缺失项提示 — `c531317`。
- 探活失败定位 — 服务端链路/代理正常（Google 对不完整凭据回 401）；根因＝TM 稳定版默认过滤 HttpOnly（源码级核验）；解法＝油猴「允许脚本访问 Cookie＝全部」。文档指引与实测回填 — `8bdd96510d61d65c868a450fd6c23f7ee1f93443`。
- 本地验证 — TT 复现环境红→绿、截图目视、`node --check`、探活接口 dummy 实测（401 定性）。

## Next

1. 主人按 `docs/USERSCRIPT.md`「开启完整 Cookie 读取」操作（仪表板 → 配置模式=高级 → 安全 → 允许脚本访问 Cookie=全部），更新脚本到 0.1.2，刷新 aistudio.google.com 后重推 — 完成条件：面板不再提示缺少关键项且导入成功；若仍异常，把面板提示内容反馈。
2. 推送 — `git push origin master`（全部本地提交一并推送）— 由主人实测通过后发话触发。
3. 归档×2 — 两份台账更新为「已采纳并归档」、删除两份 handoff、整目录移入 `design_archive/`、提交并推送 — 完成条件：`pending_processing/` 两目录清空，归档提交已推送。

## Outstanding verification

- 主人真机验收（①②合并）；`docs/USERSCRIPT.md`「实测记录」在最终结果后补一笔。

## Dead ends

- v0.1.0 `wrap.innerHTML` 面板（Trusted Types 拦截，已弃用）；「页面可读子集可完成探活导入」的假设（实测否定，需 HttpOnly 完整集）。
