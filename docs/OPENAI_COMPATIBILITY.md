# OpenAI 兼容接口说明 (`/v1`)

除原生 Gemini 接口（`/v1beta/...`）外，`aistudio-api` 还提供面向聊天客户端与 OpenAI SDK 的兼容入口。OpenAI 兼容接口与原生 Gemini 接口**共用同一套生成生命周期**（账号轮换、429/403 故障转移、单次流式不重放、用量统计与脱敏诊断日志），仅在接入层转换请求与响应格式。

客户端图形界面配置步骤见 [客户端接入指南（SillyTavern / Luker）](./CLIENTS.md)。

---

## 1. 端点与鉴权

| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/chat/completions` | `POST` | 创建聊天回复（支持非流式 JSON 与 `stream=true` 的 SSE 流式输出） |
| `/v1/models` | `GET` | 获取当前可用模型列表（OpenAI `list` 结构，`id` 保持 Gemini 原名如 `gemini-3.8-flash`，不带 `models/` 前缀） |
| `/v1/models/{model_id}` | `GET` | 查询单个模型信息（传入 `gemini-3.8-flash` 或 `models/gemini-3.8-flash` 均可，不存在时返回 404） |

### 鉴权方式

与原生接口一致，使用本服务配置的 API Key（`config.yaml` 中的 `api_keys` 或环境变量 `AISTUDIO_API_KEY`）：
- `Authorization: Bearer <YOUR_API_KEY>`（OpenAI 客户端默认方式）
- `x-api-key: <YOUR_API_KEY>` 或 `x-goog-api-key: <YOUR_API_KEY>`
- Query 参数：`?key=<YOUR_API_KEY>`

### 请求体大小限制

- `/v1/chat/completions` 原始请求体上限为 **64 MiB**，超出时返回 `413`（`invalid_request_error`）。
- JSON 解析拒绝 `NaN`、`Infinity` 与 `-Infinity` 等非标准常量。

---

## 2. 设计原则：调节参数可忽略，正文内容不静默丢弃

在跨协议转换中，本服务严格区分**「非正文调节参数」**与**「对话正文 / 附件 / 工具历史」**：

1. **正文、媒体与工具历史保真**：任何影响模型所见上下文的内容（消息角色、文本部件、图片/音频/文件附件、函数调用与工具结果配对、工具签名）如果无法转换或超出限制，立即返回 **HTTP 400 (`invalid_request_error`)** 并指出具体 `param` 路径，绝不静默丢弃附件或工具结果后假装模型已读。
2. **无等价能力的调节项显式降级**：对于 AI Studio 上游没有等价能力的采样或平台调节参数（如 `seed`、`logit_bias`、`logprobs` 等），按明确规则忽略或归一化，并在当前请求的诊断日志中写入一条 `openai_compatibility` 事件记录每项决策。

---

## 3. 请求参数映射矩阵

| OpenAI 字段 / 扩展字段 | 转换目标与优先级 | 边界与降级行为 |
|---|---|---|
| `model` | 归一化为 `models/<model>` | 必填非空字符串；模型名保持 Gemini 原名，不将 `gpt-*` 伪装映射为固定模型 |
| `messages` | `contents` + `systemInstruction` | 必填非空数组；支持 `system`、`developer`、`user`、`assistant`、`tool`、`function` 角色，相邻同角色消息自动合并 `parts` |
| `stream` | 非流式 JSON / 流式 SSE | 布尔值，默认 `false` |
| `stream_options.include_usage` | 流式末尾附加 usage chunk | 为 `true` 时，在 `finish_reason` 帧之后、`data: [DONE]` 之前发送一个 `choices: []` 且含 `usage` 的结算帧 |
| `temperature` | `generationConfig.temperature` | 合法范围 `[0, 2]`（显式 `0` 保留）；超出范围或非数值时忽略并记录日志 |
| `top_p` | `generationConfig.topP` | 合法范围 `[0, 1]`；超出范围或非数值时忽略并记录日志 |
| `top_k` | `generationConfig.topK` | 合法范围 `>= 0` 的整数；超出范围或非整数时忽略并记录日志 |
| `presence_penalty` | `generationConfig.presencePenalty` | 合法范围 `[-2, 2]`；超出范围时忽略并记录日志 |
| `frequency_penalty` | `generationConfig.frequencyPenalty` | 合法范围 `[-2, 2]`；超出范围时忽略并记录日志 |
| `max_completion_tokens` / `max_tokens` | `generationConfig.maxOutputTokens` | `>= 1` 的整数；两者并存时 **`max_completion_tokens` 优先**，`max_tokens` 记为忽略 |
| `stop` | `generationConfig.stopSequences` | 支持字符串或字符串数组（单个字符串自动转为单元素数组） |
| `response_format` | `responseMimeType` / `responseSchema` | `{"type": "json_object"}` 转为 `application/json`；`{"type": "json_schema", "json_schema": {"schema": ...}}` 同时写入 `responseSchema`（`strict` 标记会记日志说明按 Gemini schema 约束执行） |
| `reasoning_effort` / `reasoning` / `thinking` | `generationConfig.thinkingConfig.thinkingLevel` | 优先级：`reasoning_effort` > `reasoning.effort` > `thinking.thinkingLevel`（或 `thinking.level`）。`minimal`→`MINIMAL`、`low`→`LOW`、`medium`→`MEDIUM`、`high`/`xhigh`/`max`→`HIGH`；`thinking.budget_tokens` 因上游无已验证映射会被忽略并记录 |
| `tools` / `functions` | `tools[].functionDeclarations` | 两者并存时 **`tools` 优先**；仅支持 `type: "function"`，其他工具类型返回 400 |
| `tool_choice` / `function_call` | `toolConfig.functionCallingConfig` | `tool_choice` 优先：`auto`→`AUTO`、`none`→`NONE`、`required`→`ANY`、指定函数名→`ANY` + `allowedFunctionNames`；引用未声明函数或无工具声明时设 `required` 返回 400 |
| `generationConfig` / `generation_config` | 原生 `generationConfig` 基底 | 允许客户端透传原生字段，顶层同名转换字段会覆盖其中的对应项 |
| `safetySettings` / `safety_settings` | 原生 `safetySettings` | 校验并透传给 Gemini 请求 |
| `n` | 归一化为单候选 | `n > 1` 时不会发起多次上游生成，按单候选返回并记入兼容日志 |
| `seed`, `logit_bias`, `logprobs`, `top_logprobs`, `user`, `store`, `service_tier` 等 | 忽略 | 记入诊断日志 `openai_compatibility`（`ignored: no equivalent upstream capability`） |

---

## 4. 消息正文、多模态媒体与网络读取限制

### 4.1 角色与文本部件

- `system` 与 `developer` 消息合并为 Gemini 的 `systemInstruction`（仅允许文本，包含内嵌二进制媒体时返回 400）。
- 消息 `content` 支持纯字符串或部件数组（`type` 支持 `text`、`input_text`、`output_text`、`refusal`、`image_url`、`input_image`、`input_audio`、`file`、`input_file`）。
- 消息携带 `name` 时，会在该消息文本前插入 `[name: "..."]` 标记以保留说话者区分。
- 消息顶层若包含无法直接转换的 `attachments`、`audio`、`video`、`function_responses` 字段，明确返回 400。

### 4.2 内嵌媒体与附件配额

- **单附件大小上限**：解码后 **20 MiB**（`MAX_MEDIA_BYTES`）。
- **单次请求附件总上限**：累计解码后 **32 MiB**（`MAX_TOTAL_MEDIA_BYTES`），且最多 **32 个附件**（`MAX_ATTACHMENTS`）。
- **校验规则**（按真实字节魔数/编码校验，不盲信声明的 MIME）：
  - 图片（`image/png`、`image/jpeg`、`image/gif`、`image/webp`）：支持 `data:<mime>;base64,...` Data URL 或受限的远程 HTTP(S) URL。
  - 音频（`input_audio`）：支持 `format` 为 `wav`（`audio/wav`）或 `mp3`（`audio/mpeg`）的内嵌 Base64 数据。
  - 文件（`file` / `input_file`）：仅支持 `file_data` Base64 Data URL（支持图片、`application/pdf`、`text/plain` UTF-8）；传入远程 `file_id` 或 `file_url` 会明确返回 400。

### 4.3 远程图片 URL (`image_url`) 安全读取边界

当 `image_url` 为 `http://` 或 `https://` 地址时，服务端将其下载并转换为 `inlineData`，同时执行严格的 SSRF 与凭据隔离保护：

1. **仅限公网地址**：解析目标域名全部记录，拒绝回环地址（`127.0.0.0/8`、`::1`）、私网网段、链路本地、多播、保留网段及 IPv6 转换前缀（`64:ff9b::/96`、`2002::/16`、`2001::/32` 等）。
2. **DNS 绑定（DNS Pinning）**：使用 `CURLOPT_RESOLVE` 将实际连接固定到已通过公网校验的 IP，同时保留原始 Host 供 HTTPS 证书与 SNI 校验，防止 DNS 重绑定攻击。
3. **零凭据与禁代理/重定向**：下载会话禁用环境代理（`trust_env=False`、`NOPROXY=*`），不携带 Google Cookie、API Key 或任何服务端凭据；拒绝 URL 内嵌用户名/密码与 Fragment，**禁止 HTTP 重定向**（非 HTTP 200 直接报 400）。
4. **超时与流式限额**：DNS 解析限时 10 秒，下载限时 20 秒，流式接收一旦超过 20 MiB 立即中止并返回 400。

---

## 5. 工具调用与无状态签名往返

Gemini 模型在工具调用（尤其是开启思考时）依赖 `thoughtSignature` 与原生调用编号。许多 OpenAI 客户端（如 Luker、SillyTavern）在序列化历史消息时只保留标准的 `tool_calls[].id`、`type`、`function`，会丢弃未知扩展字段。

为此，本服务采用**无状态工具编号编码**：

- **出站编码**：当模型返回工具调用时，若存在原生调用 ID 或 `thoughtSignature`，服务端将 `[native_id, signature, random_nonce]` 编码为 URL-safe Base64 并加上 `call_gemini_` 前缀作为 `tool_calls[].id` 返回，同时在顶层与工具项附带显式 `signature` / `thought_signature`。
- **入站还原**：下一轮对话客户端原样回传 `tool_calls[].id` 与对应的 `role: "tool"`（`tool_call_id`）时，服务端自动从 `call_gemini_...` 编号中解码还原 `native_id` 与 `thoughtSignature`，无需在服务端维护有状态会话缓存。
- **配对校验**：
  - 每个 `assistant` 的 `tool_calls` 必须在进入下一个非 `tool` 轮次前提供全部配对的 `tool`（或旧版 `function`）结果，否则返回 400。
  - 若客户端同时回传了编码在 `id` 中的签名与显式 `signature` 字段，两者不一致时返回 400。
  - `tool` 消息正文若为合法 JSON 对象则直接作为 `functionResponse.response`，若为纯文本或其他 JSON 值则包装为 `{"result": ...}`。

---

## 6. 响应结构、流式 SSE 与错误格式

### 6.1 非流式响应 (`chat.completion`)

- 标准字段：`id`（`chatcmpl-...`）、`object`（`"chat.completion"`）、`created`、`model`、`choices[0].message`、`choices[0].finish_reason`、`usage`。
- `finish_reason` 映射：
  - 含工具调用且正常结束 → `"tool_calls"`
  - 正常结束 → `"stop"`
  - `MAX_TOKENS` → `"length"`
  - 安全/背诵/策略拦截 → `"content_filter"`
  - 缺少完成原因或未知中断 → 返回 **HTTP 502**，不会伪造 `"stop"`。
- 扩展输出字段：
  - 思考内容：`message.reasoning_content`
  - 生成图片：`message.images` / `message.reasoning_images`（以 `data:<mime>;base64,...` 形式封装在 `image_url.url` 中）
  - 签名：`message.thought_signature`

### 6.2 流式响应 (`chat.completion.chunk`)

- 同一流内所有 chunk 保持相同的 `id` 与 `model`。
- 首包发送 `{"role": "assistant", "content": ""}`；随后按真实增量推送 `delta.content`、`delta.reasoning_content`、`delta.images` 等。
- 若存在工具调用，在流完成校验通过后按顺序输出 `delta.tool_calls`（确保迟到的 `thought_signature` 已编入首个工具调用的 `id`），再发送带 `finish_reason` 的结束 chunk。
- 若请求设置了 `stream_options: {"include_usage": true}`，在 `finish_reason` 之后发送 `choices: []` 的 `usage` chunk，最后发送 `data: [DONE]\n\n`。
- **流中断不补假 `[DONE]`**：如果上游未同时给出 `finish_reason` 与流完成信号，服务端会推送一条 OpenAI 错误帧并结束流，绝不发送 `data: [DONE]`，也不会在已向客户端输出增量后再重放整段生成。

### 6.3 错误响应格式

`/v1` 与 `/v1/...` 路径下的所有异常（包括 400 参数校验、401 鉴权失败、404 模型不存在、413 超限、429 额度耗尽、502/503 上游错误）统一返回 OpenAI 错误结构：

```json
{
  "error": {
    "message": "错误说明",
    "type": "invalid_request_error",
    "param": "messages.0.content",
    "code": "invalid_request_error"
  }
}
```

原生 `/v1beta` 与管理接口 `/api` 仍保持各自的原有错误格式不变。
