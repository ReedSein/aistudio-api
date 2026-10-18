# 油猴脚本：浏览器端 Cookie 抓取

服务自带一枚油猴脚本（userscript）。在浏览器里打开并登录 aistudio.google.com 后，页面右下角会出现「AI Studio Cookie 抓取」面板，一键把当前登录 Cookie 推送到本服务，由服务端探活导入 u/0, u/1, ... 全部 Google 账号——适合手机浏览器与不便手动导出 Cookie 的场景。

脚本源文件位于仓库 `userscript/aistudio-cookie.user.js`，由服务在 `/userscript/aistudio-cookie.user.js` 路径下托管，更新地址按实际访问地址自动生成。

## 安装

1. 在浏览器中安装油猴扩展，推荐 Tampermonkey：
   - 桌面：Chrome / Edge / Firefox 均可从扩展商店安装。
   - Android：Firefox for Android（官方扩展商店）或 Edge for Android。
2. 打开服务地址下的安装链接（浏览器与服务在同一台设备时用 `127.0.0.1`）：
   `http://127.0.0.1:11451/userscript/aistudio-cookie.user.js`
   油猴会弹出安装页，确认安装即可。
3. 服务端探活导入需要 HttpOnly 关键项：在油猴设置里开启完整读取（见「开启完整 Cookie 读取」一节）。浏览器层面的「开发者模式 / 允许用户脚本」只决定脚本能否运行，不是这个开关。

Web 控制台的「导入 Google 账号凭据」弹窗里有同样的安装入口。

## 使用

1. 先手动打开并登录 `https://aistudio.google.com`（脚本只在该站点运行）。
2. 点右下角圆形按钮展开面板，确认「服务地址」：
   - 浏览器与服务在同一设备：默认 `http://127.0.0.1:11451` 即可；
   - 电脑浏览器访问手机上的服务：填手机的局域网地址，例如 `http://192.168.x.x:11451`。
3. 若服务设置了 `AISTUDIO_WEB_PASSWORD`，把管理密码填入「管理密码」一栏；未设置则留空。
4. 点「抓取并推送」，等待探活完成；面板会显示导入的账号数，导入结果也可以在控制台的账号管理页查看。

## 开启完整 Cookie 读取（含 HttpOnly）

Tampermonkey 稳定版默认只把「非 HttpOnly」的 Cookie 交给脚本，而服务端探活导入需要 HSID、SSID、__Secure-1PSID 这类只有扩展才能读到的关键项。开启方法在油猴自己的设置里（该开关自 Tampermonkey 5.3 起提供），与浏览器的开发者模式无关：

1. 打开油猴仪表板：桌面点扩展图标选「仪表板」；Edge 安卓走浏览器菜单 →「扩展」→「Tampermonkey」→ 仪表板，打不开时可在地址栏直接访问 `chrome-extension://iikmkjmpaadaobahmlepeloendndfphd/options.html#nav=settings`（Edge 版油猴的地址）。
2. 设置页把「配置模式」改为「高级」。
3. 在出现的「安全」一栏里，把「允许脚本访问 Cookie」改为「全部」（界面显示英文时选 All），保存。
4. 刷新 aistudio.google.com 后再点面板「抓取并推送」；面板提示行会列出仍缺少的关键项，不再提示缺少即为读取完整。

同一个浏览器实在读不到完整集合时，可以改用本仓库配套的 Android 提取器（系统级接口可取全量 Cookie），或换用能读取完整集合的浏览器。

## 数据与安全

- 脚本抓取的 Cookie 只会发送到面板中填写的服务地址；管理密码保存在油猴本地存储，不会随脚本导出。
- Cookie 等同于登录凭据：不要把服务直接暴露到公网；局域网内为 http 明文传输，请仅在可信网络中使用。
- 服务端探活导入需要 HttpOnly 完整集合；读取方法是油猴设置里的「允许脚本访问 Cookie = 全部」（见「开启完整 Cookie 读取」）。

## 更新

脚本的 `@updateURL` 指向安装时使用的服务地址；服务侧更新脚本后，在油猴扩展页对脚本执行「检查更新」即可。自动更新依赖浏览器保持开启、触发不稳定，手动检查可以覆盖。

## 已知限制与排错

- Firefox for Android：设置项相同、机制上可用；GM_cookie 读 HttpOnly 尚无公开的安卓实机报告（未实测），失败时同样按面板提示排查。
- 「无法连接服务」：确认地址正确、服务已启动；跨设备时用局域网 IP 而不是 127.0.0.1。
- 登录鉴权失败（401）：服务开启了管理鉴权，请填入正确的管理密码。
- 「未探测到有效已登录 Google 账号」：多数是 Cookie 集不完整（缺 HttpOnly 关键项），按「开启完整 Cookie 读取」设置后重试；同时确认浏览器里已登录 aistudio.google.com。
- 面板提示「扩展增强不可用」：脚本管理器没有提供 GM_cookie 或调用被拒，只能推送页面可读项、探活导入会失败。桌面 Chrome / Edge 先确认允许油猴运行脚本（扩展详情页「允许用户脚本」或开发者模式），再做「开启完整 Cookie 读取」。

## 实测记录

2026-10-18，Android Edge + Tampermonkey（脚本 0.1.2）：

- 面板与推送链路正常。0.1.0 在 AI Studio 页面上不显示面板，原因是页面启用了 Trusted Types 安全策略、拦截了面板旧版的构建方式，0.1.1 起已修复。
- 该组合下 GM_cookie 可用，但返回的 Cookie 只包含非 HttpOnly 项，HSID、SSID、__Secure-1PSID、__Secure-3PSID 等关键项读不到。
- 用「页面可读子集」推送后，服务端探活导入被 Google 拒绝（HTTP 400，未探测到有效已登录 Google 账号）。服务端到 Google 的链路与代理已单独核验正常，失败原因是认证用的 Cookie 不完整。
- 结论：页面可读子集不足以完成探活导入，需要能读取 HttpOnly 的完整集合。开启方式见「开启完整 Cookie 读取」一节。
- 开启完整读取后重推：导入成功，账号进入服务端列表并被自动设为当前活跃账号，端到端调用（长思考请求）验证通过。整条链路（面板抓取 → 探活导入 → 账号可用）均已实测。
