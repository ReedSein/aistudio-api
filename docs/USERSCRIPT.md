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
3. 桌面 Chrome / Edge 的可选增强：在扩展管理页开启 Tampermonkey 的「开发者模式」，脚本才能读取 HttpOnly Cookie（HSID、SSID 等）；不开也能用页面可读项推送。

Web 控制台的「导入 Google 账号凭据」弹窗里有同样的安装入口。

## 使用

1. 先手动打开并登录 `https://aistudio.google.com`（脚本只在该站点运行）。
2. 点右下角圆形按钮展开面板，确认「服务地址」：
   - 浏览器与服务在同一设备：默认 `http://127.0.0.1:11451` 即可；
   - 电脑浏览器访问手机上的服务：填手机的局域网地址，例如 `http://192.168.x.x:11451`。
3. 若服务设置了 `AISTUDIO_WEB_PASSWORD`，把管理密码填入「管理密码」一栏；未设置则留空。
4. 点「抓取并推送」，等待探活完成；面板会显示导入的账号数，导入结果也可以在控制台的账号管理页查看。

## 数据与安全

- 脚本抓取的 Cookie 只会发送到面板中填写的服务地址；管理密码保存在油猴本地存储，不会随脚本导出。
- Cookie 等同于登录凭据：不要把服务直接暴露到公网；局域网内为 http 明文传输，请仅在可信网络中使用。
- HttpOnly 增强需要扩展的「开发者模式」；不需要时可以关闭该开关，脚本会自动退回使用页面可读项。

## 更新

脚本的 `@updateURL` 指向安装时使用的服务地址；服务侧更新脚本后，在油猴扩展页对脚本执行「检查更新」即可。自动更新依赖浏览器保持开启、触发不稳定，手动检查可以覆盖。

## 已知限制与排错

- Firefox for Android 上 GM_cookie（HttpOnly 增强）的可用性尚未实机验证；无增强时以页面可读 Cookie 推送，是否足够导入取决于 Google 的会话校验（服务端导入后会在自家浏览器里重新协商其余凭据）。
- 「无法连接服务」：确认地址正确、服务已启动；跨设备时用局域网 IP 而不是 127.0.0.1。
- 登录鉴权失败（401）：服务开启了管理鉴权，请填入正确的管理密码。
- 「未探测到有效已登录 Google 账号」：先在浏览器里手动登录 aistudio.google.com 后重试。
- 桌面 Chrome / Edge 提示 GM_cookie 不可用：按「安装」第 3 步开启扩展的「开发者模式」。

## 实测记录

尚未实测：桌面与手机 Firefox / Edge 的真机验证待补充，重点是「无 GM_cookie 增强时页面可读子集是否足以完成导入」。结论确认后回填本节。
