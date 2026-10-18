# 决策台账：油猴脚本抓取 Cookie 与有头转发打底

> 状态：未采纳 · 讨论中（草案，不构成实施授权，不改任何正式状态）

## 状态

- 2026-10-18 建立；只读调查完成（本地探子拆解参考脚本全 2087 行；联网探子核实手机端平台事实）。
- 首轮重问题（施工范围分期）已发出，待主人回答；轻问题清单待第二轮。

## 目标与边界

- 目标：为 aistudio-api 配套自研油猴脚本：在 aistudio.google.com 抓取 Cookie 并推送到自家服务（本机/局域网），适配手机端；以脚本为基座为后续「有头浏览器转发」（用户浏览器作执行端，参考 catiecli WS Executor 模式）留好结构。
- 边界（暂定）：服务端现有导入接口语义不动（需要时只做增量扩展）；不引入云端依赖。

## 已查明事实（证据）

### 项目侧（服务与现有导入链）

- 三条导入通道：`POST /api/accounts/import-cookies`（任意格式串→解析→存账号+注入浏览器+引导会话）；`/probe-import`（一份 Cookie 探活 u/0..u/N 多账号批量导入）；`/import-bundle`（Android 端 Bundle：`{version, exported_at, accounts:[{id,name,email,cookies,auth_user}]}`，cookies 为 `k=v; k=v` 串）。
- 鉴权：管理接口 `require_web_auth`，Bearer = `AISTUDIO_WEB_PASSWORD`，未设则开放（dependencies.py）。
- 解析宽容（JSON/对象/Netscape/KV），导入自动丢弃遥测与易失效项（SIDCC/DV/NID/OSID 等）、保留 SAPISID 家族与 SID；`_AUTH_COOKIE_NAMES` 注入时强制 httpOnly=False 供页面 JS 算 SAPISIDHASH。
- 服务无 CORS 中间件 → 脚本须用 GM_xmlhttpRequest；服务监听 0.0.0.0（局域网可达）。
- 现有手机抓取先例：Android App 用 WebView `CookieManager.getCookie(aistudio.google.com / .google.com)`（LoginScreen.kt:67-69）。
- Web 导入弹窗（CookieImportModal）：文本粘贴（默认探活导入）+ 文件 Bundle。

### 参考脚本（catiecli v2.4.6，`$TMPDIR/ref-catiecli/script.user.js`）

- Cookie 推送：只用 GM_cookie（无降级）、硬校验 8 件（SAPISID/1P/3PAPISID/1P/SID 版本/HSID/SSID）、POST 云端 `{email, cookies:[{name,value,domain,path,expirationDate,httpOnly,secure,sameSite}]}`、X-API-Key。
- WS Executor：`register{apiKey,executorType}`；下行 `request{requestId,model,stream,requestBody:"{wire,binding}"}`、`cancel`；上行 `stream-chunk/stream-end/error{status,error{code,message}}`；引导=等 `W.default_MakerSuite`+wrap snapshot 捕获 service（刷新失效）+fetch/XHR 捕获公共头（可持久 24h）；proof=`sha256(binding)`→wire[4]；串行队列+60s 过期+10min 硬超时+断线 outbox；保活=PiP+30Hz 静音音频（无协议心跳）。
- UI：Shadow DOM 面板、GM 键 4 个、移动端 CSS（48px 按钮/16px 输入/touch-action/`min(340px,100vw-16px)`）、Pointer Events 拖动；无 UA 嗅探。

### 手机端平台事实（联网核实，2026-10 现状）

- 可装油猴管理器的手机浏览器：Firefox for Android（官方上架、在维护，TM 5.5.0）与 Edge for Android（稳定版扩展商店，TM 在列）；Kiwi 已归档停更；Orion 无 Android 版；Violentmonkey 亦可作备选。
- GM_cookie：读 HttpOnly 官方仍标 BETA、稳定版默认只给非 HttpOnly（需 Advanced→Security→All）；Chrome 系需开发者模式或「Allow User Scripts」。**Firefox Android 上 GM_cookie 可用性无一手资料，须实机验证。**
- GM_xmlhttpRequest：手机端无平台限制；`@connect` 合法值含 localhost、IP、`*`；未入白名单会弹确认、拖延会被拉黑。
- **Google Cookie 标志（关键）**：2021 年 Chromium 真实夹具 + 多个开源项目佐证，`SID`、`SAPISID`、`APISID`、`__Secure-1PAPISID`、`__Secure-3PAPISID` **非 HttpOnly**，页面 document.cookie 可读；`HSID`、`SSID`、`__Secure-1PSID`、`__Secure-1PSIDTS` 为 HttpOnly（需 GM_cookie）。旧合成夹具矛盾，以实机实测为准。
- `@updateURL` 指向 http:// 无文档禁令，手动检查更新已实证；自动更新有已知限制（需浏览器保持开启）。

## 决策与理由

- D-候选（推荐·待清单确认）：**document.cookie 为主数据源，GM_cookie 为可选增强**（有则合并补齐 HttpOnly 集，无则只用可读子集；服务端导入后会在自家浏览器里重新协商其余凭据）。理由：让脚本在 Firefox Android / Edge / 桌面全平台可用且无需 BETA 开关；可读子集含 SID+SAPISID 等核心认证件，可行性实机可验。
- D-候选（推荐·待清单确认）：脚本由服务托管（`.user.js` + @updateURL 指向自身实例，借鉴 catiecli 的 `script.user.js` 模式）。
- D-候选（推荐·待清单确认）：默认走 probe-import（一次推送探活导入该浏览器登录的全部 u/N 账号）。
- 首轮重问题（待答）：施工范围分期（仅 Cookie 脚本+结构打底 / 连 WS 转发一起做 / 协议设计先行）。

## 未决问题（按主题）

- T1 施工分期：【首轮重问题，已发出】。
- T2 轻清单（第二轮）：默认探活导入；服务地址与可选密码的存放（GM 存储）；GM_cookie 增强开关默认值；分发与自动更新；邮箱识别（DOM 抓取）做不做；脚本在仓库的落点（如 `userscript/`）与服务托管路由。
- T3 有头转发（若后置）：下一任务单独立项时定协议与调度改造。

## 验证与风险

- 计划验证：脚本 JS 语法校验；真实 aistudio.google.com 页面手工安装验证（桌面 + 手机 Firefox/Edge）；推送到本机服务的导入成功与账号可用性（探活接口返回）；无 GM_cookie 环境下的可读子集导入实测（重点风险点）。
- 风险：可读子集是否足够导入取决于 Google 会话校验（实机先验，失败则退回 GM_cookie 方案并接受其平台限制）；Cookie 敏感数据提示与密码存放需明确文案；http:// 更新地址的自动更新触发不稳定（文档注明手动检查路径）。

## 拟提升目标

- 脚本本体（仓库 `userscript/` 或等效）+ 服务托管路由与 @updateURL、README / docs 使用说明、台账归档 `design_archive/userscript-cookie-capture/`。

## 后续记录

- 2026-10-18：主人选「方案甲」。施工范围定案：本次只交付 Cookie 抓取+推送脚本（手机友好悬浮面板、可配服务地址与可选管理密码、服务托管脚本与自动更新、GM_cookie 可选增强、document.cookie 为主数据源）；有头转发后置为独立任务，本轮仅保留台账中的协议事实与后续任务位，不在脚本里为假设需求预建结构。T1 关闭。
- 2026-10-18：第二轮轻清单已发出（默认探活导入、服务地址默认值、管理密码存放、GM_cookie 自动策略、邮箱识别、脚本落点与服务托管路由、@connect 白名单、文档落点），待回复。
- 2026-10-18：第二轮轻清单八项全部按推荐认可（默认探活导入、服务地址默认 127.0.0.1:11451、密码存油猴本地、GM_cookie 自动增强、邮箱识别、userscript/ 落点+服务托管路由+自动更新、@connect 白名单、docs/USERSCRIPT.md）。共同理解与整体执行方案已提交，等待确认。
- 2026-10-18：主人发起会话交接；按 grill-with-docs 写入 handoff.md（本目录）。整体执行方案仍未获确认，实施授权自确认起才产生。

## Confirmed plan

- 2026-10-18 确认消息：主人对本方案的复述回复「第一个实测需要等第二个做好了才行啊」——本任务先行实施；①（runtime-timeouts-port）的实机实测、推送与归档顺延至本任务完成后合并进行。
- 已授权：按复述方案实施——新增 `userscript/aistudio-cookie.user.js`；服务端只读托管路由 `/userscript/aistudio-cookie.user.js`（按访问地址改写更新地址）与 SPA 放行；CookieImportModal 安装指引并重建静态产物；新增 `docs/USERSCRIPT.md` 与 README×2 / TERMUX / AGENT 各一句；补路由单测；运行自动门禁；本地一笔提交 `Design-Record: userscript-cookie-capture`。
- 推送与归档时序：主人合并实测（① 浏览器实测 + ② 真机抓取推送）通过后推送 origin master（可与 `1338801`、`4ca9947` 同行），随后两份台账一并归档。
- 排除项：有头转发（独立任务，不预建结构）、云端依赖、现有导入接口语义、Android 端。

- 2026-10-18：实施完成并本地提交 `e19c10c825d60c5eaee439af12430cf17ad6150f`。质量门禁：pytest 255 通过（含新增路由测试 2 项）、ruff lint 全过、新文件格式干净（app.py 全库既有格式漂移未触碰）、pyright 0 错、eslint 0 告警、vue-tsc 仅 ProfileCard.vue 两处 master 既有报错、vite build 成功、node --check 通过；服务经管理引擎静默重启（旧 6724 → 新 11487），脚本路由冒烟：200 JS、更新地址按访问地址改写、`Accept: text/html` 不被 SPA 兜底；部署产包含弹窗安装入口。审查修正：GM_cookie 被拒时的误报提示已补「读取失败」分支。剩余：主人合并实测（①+②）→ 推送 → 归档；本 handoff 随后一并提交。

- 2026-10-18：主人实测报告「脚本已安装并启用，但 aistudio.google.com 页面上看不到面板、圆钮也无痕迹」；同页 sukaka 参考脚本正常显示（排除环境）。排查：aistudio.google.com 响应头启用 Trusted Types（`require-trusted-types-for 'script'`）；v0.1.0 面板以 `wrap.innerHTML` 构建，赋值被拦截抛 TrustedHTML 错误、脚本静默中止，仅余空宿主 div，与现象吻合；本地以同策略复现（mounted=false + 报错），参考脚本全用 createElement、零 innerHTML，印证成因。修复 v0.1.1（提交 `bae714c651cbe151390dd4e74207814ea117a0b0`）：createElement 构建；初始化 try/catch，失败 console.error + alert 明示（不再静默）；`applyOpen` 空守卫；新增 GM_registerMenuCommand「打开 Cookie 抓取面板」。验证：同策略复现红→绿、无策略不回归、截图目视正常、`node --check` 通过、线上即时供应 0.1.1。待主人更新脚本后重测。
