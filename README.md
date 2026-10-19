# aistudio-api · Termux

由 ReedSein 维护的 [aistudio-api](https://github.com/DeconstructedCube/aistudio-api) Termux 优化分支，将 Google AI Studio Web 会话转换为原生 Gemini API。

面向 Android ARM64 Termux，提供中文管理菜单、一键安装，以及项目、依赖和浏览器的统一更新。Python 在 Termux 中运行，CloakBrowser 使用 proot Ubuntu 提供的系统库。

[English](./README_EN.md) · [Termux 完整说明](./docs/TERMUX.md)

## 一键安装

默认安装到 `~/aistudio-api-termux`，与当前所在目录无关：

请在交互式 Termux 窗口执行。入口使用 Termux 自带的 APT 下载工具，curl 缺库时也能取得安装器。需要修复或升级系统软件时，会先显示影响并询问；这可能更新其他 Termux 项目共用的工具。

```bash
bash -c '
set -eu
test -w "$TMPDIR"
install_tmp=$(mktemp -d "$TMPDIR/aistudio-bootstrap.XXXXXX")
cleanup() { rm -rf -- "$install_tmp"; }
trap cleanup EXIT
"$PREFIX/lib/apt/apt-helper" -o Acquire::Retries=2 -o Acquire::https::Timeout=15 download-file \
  https://github.com/ReedSein/aistudio-api-termux/releases/latest/download/install-termux.sh "$install_tmp/install-termux.sh"
bash "$install_tmp/install-termux.sh" "$@"
' --
```

指定父目录，例如安装到 `~/projects/aistudio-api-termux`：

```bash
bash -c '
set -eu
test -w "$TMPDIR"
install_tmp=$(mktemp -d "$TMPDIR/aistudio-bootstrap.XXXXXX")
cleanup() { rm -rf -- "$install_tmp"; }
trap cleanup EXIT
"$PREFIX/lib/apt/apt-helper" -o Acquire::Retries=2 -o Acquire::https::Timeout=15 download-file \
  https://github.com/ReedSein/aistudio-api-termux/releases/latest/download/install-termux.sh "$install_tmp/install-termux.sh"
bash "$install_tmp/install-termux.sh" "$@"
' -- --dir "$HOME/projects"
```

安装和更新只使用维护者选定的最新正式发布，日常开发提交不会提供给使用者。正式版本使用 `termux-vX.Y.Z` 标签；首次正式发布前安装链接不可用，获取发布失败时会停止。

再次执行相同安装命令可以续做失败的安装，或在原位置重装并复用保留的数据。已有无关目录、源码修改或其他仓库会明确报错并停止。仓库更名为 aistudio-api-termux 后，旧地址仍被识别；旧命名的安装重跑新版安装脚本即可接续，原安装目录与浏览器容器继续沿用。

首次安装被中断时，重跑同一命令即可接续：可用的源码和浏览器复用，核实归属的下载半成品及损坏的项目运行环境定向重建。安装器下载、版本查询、源码获取和浏览器下载最多尝试三次；持续失败时显示实际错误。

## 本机开发与测试

在已有工作区直接使用当前代码，包括未提交修改：

```bash
cd ~/projects/aistudio-api
bash scripts/install-termux.sh --dev --dir "$HOME/projects"
aistudio
```

这会沿用现有配置、账号、数据、端口和浏览器容器。准备环境不拉取或切换源码，日常启动不要求先提交、push 或发布；开发用法下，菜单中的主动更新才跟随 `master`。新建开发安装的方法见 [Termux 说明](./docs/TERMUX.md#本机开发与测试)。

## 管理菜单

```bash
aistudio
```

- **普通启动**：前台显示日志，Ctrl+C 停止并返回菜单。
- **静默启动**：后台运行，退出菜单后继续运行，不保存服务日志和请求转储。
- **停止服务**：核对进程身份后正常停止。
- **检查更新**：检查维护者选定的正式版本；开发用法检查本仓库 `master`。
- **一键更新**：更新项目、管理脚本、锁定的 Python 依赖和指定浏览器；浏览器版本未变时复用。
- **清理原版安装**：可选清理原作者旧安装的程序与缓存，默认保留其账号、设置与浏览器登录资料；另有明确的清除资料选项。
- **卸载**：默认保留配置、账号和统计，也可选择清除本安装数据；显示范围并确认后执行。开发工作区的源码和 Git 记录保留，共享系统工具及浏览器容器保留。

默认访问 `http://127.0.0.1:11451`，在 Web 控制台的账号管理页面导入 Cookie 或凭据包。更新会短暂停机，成功后恢复原运行模式。打开菜单时会短超时检查一次正式版本，相同提示也显示在控制台顶部；检查失败不影响正常使用。

用户配置独立保存在 `~/.config/aistudio-termux/`，账号及统计沿用原有数据目录，更新不会覆盖。Android 仍可能清理后台进程。

## 维护与文档

使用端只跟随 `ReedSein/aistudio-api-termux`；原作者的改动由维护者评估、测试后再吸收。

- [安装路径、端口、配置、更新及恢复](./docs/TERMUX.md)
- [系统架构](./docs/ARCHITECTURE.md)
- [Wire 协议](./docs/WIRE_SPECIFICATION.md)
- [BotGuard 验证链](./docs/BOTGUARD_VERIFICATION_CHAIN.md)

支持 Gemini `/v1beta/...`、流式输出、多模态、工具调用、多账号轮换及 Web 管理控制台。运行时使用纯 Python CDP，不需要 Node.js、Playwright 或 Selenium。

## API 调用示例

支持以下鉴权方式：
- Query 参数：`?key=YOUR_API_KEY`
- 请求头：`x-goog-api-key: YOUR_API_KEY`、`x-api-key: YOUR_API_KEY` 或 `Authorization: Bearer YOUR_API_KEY`

### cURL 调用

<details>
<summary><b>1. 查询模型列表</b></summary>

```bash
curl http://localhost:11451/v1beta/models \
  -H "x-goog-api-key: your-api-key"
```
</details>

<details>
<summary><b>2. 文本生成 (Non-streaming)</b></summary>

```bash
curl http://localhost:11451/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "用简短的一句话介绍量子计算。"}]
      }
    ]
  }'
```
</details>

<details>
<summary><b>3. 流式生成 (Server-Sent Events)</b></summary>

```bash
curl http://localhost:11451/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "介绍一下 Python 协程的工作原理。"}]
      }
    ]
  }'
```
</details>

---

### Python SDK (google-genai)

使用 Google 官方 `google-genai` SDK 直连本代理服务：

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:11451",
    },
)

# 1. 流式响应
response = client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="解释一下什么是反应式编程？",
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 2. 非流式响应
result = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="用 Python 写一个快速排序算法。",
)
print(result.text)
```

---

### 工具调用 (Function Calling)

```python
from google import genai
from google.genai import types

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:11451",
    },
)

# 定义工具声明
weather_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_current_weather",
            description="获取指定地点的实时天气状况",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "location": types.Schema(
                        type="STRING",
                        description="城市或地区名称，例如 'Beijing' 或 'Tokyo'",
                    ),
                    "unit": types.Schema(
                        type="STRING",
                        enum=["celsius", "fahrenheit"],
                        description="温度单位",
                    ),
                },
                required=["location"],
            ),
        )
    ]
)

response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="今天杭州天气怎么样？",
    config=types.GenerateContentConfig(tools=[weather_tool]),
)

if response.function_calls:
    for call in response.function_calls:
        print(f"触发工具调用: {call.name}")
        print(f"调用参数: {call.args}")
```

---

## Web 管理控制台

系统内置静态打包的 Web 前端管理控制台（访问 `http://localhost:11451`）：

- **仪表盘 (Dashboard)**：实时展示服务健康状况、各模型请求量、成功率及 429 冷却指标。
- **账号管理 (Accounts)**：支持 Cookie 单条与凭据包批量导入、自动探测多账号、手动切换当前活跃账号或重置冷却状态。
- **系统设置 (Settings)**：在线编辑 `config.yaml`（支持配置 profiles 与 models 默认规则），保存后服务端即时热重载。
- **访问鉴权**：设置环境变量 `AISTUDIO_WEB_PASSWORD` 后自动开启登录鉴权守卫。
- **油猴脚本**：在浏览器登录 aistudio.google.com 后一键抓取 Cookie 推送到本服务，自动探活导入多账号（[使用说明](./docs/USERSCRIPT.md)）。

---

## 开源协议

基于 [MIT License](./LICENSE) 开源，保留原项目许可与作者信息。
