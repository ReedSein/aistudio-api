# 客户端接入指南（SillyTavern / Luker）

本文说明如何将 **酒馆 (SillyTavern)** 与 **Luker** 接入 `aistudio-api`。两个客户端均支持 **原生 Gemini (`v1beta`)** 与 **OpenAI 兼容 (`/v1`)** 两种接法；Web 控制台侧边栏的「使用教程」（`/guides`）也提供交互式切换与一键复制。

- 协议字段映射与边界矩阵见 [OpenAI 兼容接口说明](./OPENAI_COMPATIBILITY.md)。
- 排错日志与诊断包导出见 [日志采集与诊断包](./DIAGNOSTICS.md)。

---

## 1. 连接前准备

1. **确认至少有一个可用账号**：打开 Web 控制台（默认 `http://127.0.0.1:11451`）的「账号管理」页面，确认已导入有效 Google AI Studio Cookie 且账号状态可用。
2. **确认本服务 API Key**：在「系统与模型配置」页面查看是否启用了 API Key 鉴权（`config.yaml` 的 `api_keys` 或环境变量 `AISTUDIO_API_KEY`）。
   - 客户端里填写的是**本服务自身的 API Key**，不是 Google AI Studio 官方的 `AIza...` 密钥。
   - 若本服务未开启 API Key 鉴权，在客户端的反向代理密码或自定义密钥框中也建议填入任意非空占位符（如 `your-api-key`），以确保客户端启用代理鉴权分支。
3. **区分「同机运行」与「跨设备局域网运行」**：
   - 连接地址由**酒馆 / Luker 后台进程（Node.js）运行在哪台设备上**决定，而不是由打开网页的浏览器所在设备决定。
   - **同机运行**（客户端后台与 `aistudio-api` 在同一部手机 Termux 或同一台电脑）：使用 `http://127.0.0.1:11451`。
   - **跨设备运行**（例如 `aistudio-api` 在手机 Termux 运行，酒馆 / Luker 后台在另一台电脑或平板运行）：使用**运行 `aistudio-api` 那台手机/主机的局域网 IP**（如 `http://192.168.1.100:11451`），不能填写 `127.0.0.1`。

---

## 2. 接法总览与路径对照

| 客户端 | 接入协议 | Chat Completion Source | 地址输入框 | 填写地址示例（同机） | 客户端自动追加后缀 |
|---|---|---|---|---|---|
| **酒馆 (SillyTavern)** | **原生 Gemini（推荐）** | `Google AI Studio` | `Reverse Proxy` (`#openai_reverse_proxy`) | `http://127.0.0.1:11451` | `/v1beta/models/{model}:generateContent` 或 `:streamGenerateContent` |
| **酒馆 (SillyTavern)** | **OpenAI 兼容** | `Custom (OpenAI-compatible)` | `Custom Endpoint` (`#custom_api_url_text`) | `http://127.0.0.1:11451/v1` | `/chat/completions`、`/models` |
| **Luker** | **原生 Gemini（推荐）** | `Google AI Studio` | `Reverse Proxy` (`#openai_reverse_proxy`) | `http://127.0.0.1:11451` | `/v1beta/models/{model}:generateContent` 或 `:streamGenerateContent` |
| **Luker** | **OpenAI 兼容** | `Custom (OpenAI-compatible)` | `Custom Endpoint` (`#custom_api_url_text`) | `http://127.0.0.1:11451/v1` | `/chat/completions`、`/models` |

> **版本参考**：本文字段与路径对照基于 SillyTavern 上游源码 `06bde939fb1e9c4c8d8641d810f0a916b5bce127`、Luker 上游源码 `3c9294f03c0366448192149ce79ccc39d377f694`，并在本机 Luker `f18f73ca006318beacac5db0eed762a32f02a561` 隔离环境中完成原生 Gemini 与 OpenAI 兼容的普通/流式双轮真实模型收发验收。不同客户端版本的界面翻译可能略有差异，可对照表中的底层元素选择器确认。

---

## 3. 酒馆 (SillyTavern) 接入步骤

打开 SillyTavern 顶部导航栏的 **插头图标（API Connections）**：

### 3.1 接法 A：原生 Gemini（推荐）

原生接法直接调用 `/v1beta`，无需经过跨协议字段折算，完整保留系统指令、多模态内嵌数据、思考配置与 `thoughtSignature`。

1. **API**（`#main_api`）：选择 `Chat Completion`。
2. **Chat Completion Source**（`#chat_completion_source`）：选择 `Google AI Studio`。
3. **Reverse Proxy**（`#openai_reverse_proxy`）：填写本服务**根地址**：
   - 同机：`http://127.0.0.1:11451`
   - 跨设备：`http://<运行本服务的局域网IP>:11451`
   - **注意**：不要在末尾加 `/v1` 或 `/v1beta`。SillyTavern 的 `gemini.apiVersion` 默认为 `v1beta`，请保持默认（不要改为 `v1alpha`）。
4. **Reverse Proxy Key**（`#openai_proxy_access_key`）：填写本服务配置的 API Key（未启用鉴权时可填 `your-api-key` 等非空占位符；**不要填进上方的 Google API Key 输入框**）。
5. 点击 **Connect（连接）**（`#api_button_openai`），等待状态灯变绿。
6. **Google AI Studio Model**（`#model_google_select`）：从下拉列表选择模型（如 `gemini-3.8-flash`、`gemini-3.1-flash-lite` 等）。
7. **Streaming**（`#stream_toggle`）：按需开启或关闭。

### 3.2 接法 B：OpenAI 兼容 (`/v1`)

适合需要复用 OpenAI 预设或自定义扩展字段的场景。

1. **API**（`#main_api`）：选择 `Chat Completion`。
2. **Chat Completion Source**（`#chat_completion_source`）：选择 `Custom (OpenAI-compatible)`。
3. **Custom Endpoint (Base URL)**（`#custom_api_url_text`）：填写带 `/v1` 的地址：
   - 同机：`http://127.0.0.1:11451/v1`
   - 跨设备：`http://<运行本服务的局域网IP>:11451/v1`
   - **注意**：填到 `/v1` 为止，不要附加 `/chat/completions`。
4. **Custom API Key**（`#api_key_custom`）：填写本服务配置的 API Key。
5. 点击 **Connect（连接）**（`#api_button_openai`），从 `/v1/models` 拉取模型目录，或在 **Enter a model ID**（`#custom_model_id`）手动填入 Gemini 模型原名（如 `gemini-3.8-flash`，不带 `models/` 前缀）。
6. **Streaming**（`#stream_toggle`）：按需开启或关闭。

---

## 4. Luker 接入步骤

打开 Luker 顶部的 **API Connections（连接设置）**：

### 4.1 接法 A：原生 Gemini（推荐）

1. **API**（`#main_api`）：选择 `Chat Completion`。
2. **Chat Completion Source**（`#chat_completion_source`）：选择 `Google AI Studio`（内部值 `makersuite`）。
3. **Reverse Proxy**（`#openai_reverse_proxy`）：填写 `http://127.0.0.1:11451`（跨设备填 `http://<局域网IP>:11451`），不要加 `/v1` 或 `/v1beta`。
4. **Reverse Proxy Key**（`#openai_proxy_access_key`）：填写本服务 API Key（或非空占位符）。
5. 点击 **Connect**（`#api_button_openai`），在模型下拉框（`#model_google_select`）中选择目标 Gemini 模型（如 `gemini-3.1-flash-lite`、`gemini-3.8-flash`）。
6. **Streaming**（`#stream_toggle`）：开启或关闭均可。

### 4.2 接法 B：OpenAI 兼容 (`/v1`)

1. **API**（`#main_api`）：选择 `Chat Completion`。
2. **Chat Completion Source**（`#chat_completion_source`）：选择 `Custom (OpenAI-compatible)`（内部值 `custom`）。
3. **Custom Endpoint**（`#custom_api_url_text`）：填写 `http://127.0.0.1:11451/v1`（跨设备填 `http://<局域网IP>:11451/v1`）。
4. **Custom API Key**（`#api_key_custom`）：填写本服务 API Key。
5. **Custom Model ID**（`#custom_model_id`）：点击 **Connect**（`#api_button_openai`）拉取列表后选择，或直接输入 `gemini-3.8-flash`、`gemini-3.1-flash-lite` 等模型 ID。
6. **思考与工具**：
   - Luker 的思考强度（`#openai_reasoning_effort`）会映射为 Gemini 的 `thinkingConfig.thinkingLevel`；模型返回的思考内容存放在 `reasoning_content`，与回复正文分离。
   - 多轮工具调用通过 `call_gemini_...` 工具编号自动携带签名，无需额外部署服务端持久化缓存。

---

## 5. 最小连通与双轮验证

首次配置完成后，建议按以下顺序验证：

1. **第一轮（普通非流式）**：关闭 `Streaming`，发送一条简短消息（如「请记住本轮暗号是 Rosa-1，并用一句话问好」），确认聊天窗口正常渲染模型回复，且未把协议 JSON 当作正文显示。
2. **第二轮（流式增量与上下文）**：开启 `Streaming`，继续追问「刚才约定的暗号是什么？只回复暗号」，确认文字逐段显示、能准确复述 `Rosa-1`，且回复结束后发送按钮恢复可用状态。

---

## 6. 常见错误排查

| 现象 | 常见原因 | 排查与修正 |
|---|---|---|
| **404 Not Found** | 原生 Gemini 反向代理多填了 `/v1` 或 `/v1beta`；或者 OpenAI 自定义端点漏了 `/v1`、多填了 `/chat/completions` | 原生 `Google AI Studio` 只填根地址 `http://host:11451`；OpenAI `Custom` 填到 `http://host:11451/v1` |
| **401 Unauthorized / API key not valid** | 本服务 API Key 填错；或在原生 Gemini 模式下填进了 Google 官方 Key 输入框而未填 `Reverse Proxy Key` | 在控制台「系统与模型配置」核对本服务 API Key，原生 Gemini 模式务必填入 `Reverse Proxy Key` |
| **连接超时 / 模型列表为空** | 酒馆 / Luker 后台与本服务不在同一台设备，却填了 `127.0.0.1` | 改为运行 `aistudio-api` 的设备局域网 IP，并确认两台设备处于同一局域网 |
| **400 invalid_request_error** | 正文、媒体或工具历史无法转换（如远程图片 URL 为私网/含重定向/超过 20 MiB，或 `tool_calls` 缺少配对的 `tool` 结果） | 查看响应 `error.param` 与 `error.message` 定位字段；参考 [OpenAI 兼容接口说明](./OPENAI_COMPATIBILITY.md) |
| **502 / 503 或流式中断** | 无可用 Google 账号、Cookie 过期，或上游流在完成标记到达前中断（本服务不会补假 `[DONE]`） | 检查控制台「账号管理」中的账号状态；在「系统与模型配置」导出诊断包或运行 `aistudio --export-logs` 进一步排查 |
