# aistudio-api · Termux

A Termux-focused fork of [aistudio-api](https://github.com/DeconstructedCube/aistudio-api), maintained by ReedSein. It exposes Google AI Studio web sessions through the native Gemini API.

Targets Android ARM64 Termux with an interactive Chinese management menu, one-command installation, and coordinated updates. Python runs in Termux; CloakBrowser uses an Ubuntu proot container for its system libraries.

[中文](./README.md) · [Detailed Termux guide](./docs/TERMUX.md)

## Install

The default location is `~/aistudio-api`, independent of the current directory:

```bash
pkg update -y && pkg install -y curl && curl -fsSL https://raw.githubusercontent.com/ReedSein/aistudio-api/master/scripts/install-termux.sh -o "$TMPDIR/aistudio-api-install.sh" && bash "$TMPDIR/aistudio-api-install.sh"
```

Choose a different parent directory, for example `~/projects/aistudio-api`:

```bash
pkg update -y && pkg install -y curl && curl -fsSL https://raw.githubusercontent.com/ReedSein/aistudio-api/master/scripts/install-termux.sh -o "$TMPDIR/aistudio-api-install.sh" && bash "$TMPDIR/aistudio-api-install.sh" --dir "$HOME/projects"
```

Rerun the same command to resume installation. Unrelated directories, foreign repositories, and local source edits are preserved and reported.

## Manage

```bash
aistudio
```

The menu offers foreground start, silent background start, stop, check for updates, and update. Ctrl+C stops a foreground service and returns to the menu. A silent service keeps running after the menu exits and writes no service logs or request dumps.

The default console address is `http://127.0.0.1:11451`. Import Cookies or an account bundle in the Accounts page. Updates cover application and management code, locked Python dependencies, and the declared browser version; an unchanged browser is reused. A running service is stopped briefly and restarted in its previous mode after a successful update.

User configuration lives in `~/.config/aistudio-termux/`; existing account and statistics data remain in their original locations. Android may still terminate background processes.

## Maintenance and documentation

Installations follow `ReedSein/aistudio-api` only. Maintainers evaluate and test changes from the original project before bringing them into this fork.

- [Paths, ports, configuration, updates and recovery](./docs/TERMUX.md)
- [Architecture](./docs/ARCHITECTURE.md)
- [Wire protocol](./docs/WIRE_SPECIFICATION.md)
- [BotGuard verification](./docs/BOTGUARD_VERIFICATION_CHAIN.md)

The gateway supports Gemini `/v1beta/...`, streaming, multimodal requests, function calling, account rotation, and a web console. The runtime uses pure Python CDP without Node.js, Playwright, or Selenium.

## API Usage Examples

Supported authentication methods:
- Query param: `?key=YOUR_API_KEY`
- Header: `x-goog-api-key: YOUR_API_KEY`, `x-api-key: YOUR_API_KEY`, or `Authorization: Bearer YOUR_API_KEY`

### cURL

<details>
<summary><b>1. List Models</b></summary>

```bash
curl http://localhost:11451/v1beta/models \
  -H "x-goog-api-key: your-api-key"
```
</details>

<details>
<summary><b>2. Generate Content (Non-streaming)</b></summary>

```bash
curl http://localhost:11451/v1beta/models/gemini-3.7-flash:generateContent \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "Explain quantum computing in one sentence."}]
      }
    ]
  }'
```
</details>

<details>
<summary><b>3. Stream Generate Content (SSE)</b></summary>

```bash
curl http://localhost:11451/v1beta/models/gemini-3.7-flash:streamGenerateContent?alt=sse \
  -H "x-goog-api-key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {
        "role": "user",
        "parts": [{"text": "Explain how Python asyncio works."}]
      }
    ]
  }'
```
</details>

---

### Python SDK (google-genai)

Using the official `google-genai` Python SDK:

```python
from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "http://localhost:11451",
    },
)

# 1. Streaming response
response = client.models.generate_content_stream(
    model="gemini-3.7-flash",
    contents="What is reactive programming?",
)
for chunk in response:
    print(chunk.text, end="", flush=True)

# 2. Non-streaming response
result = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="Write a quicksort implementation in Python.",
)
print(result.text)
```

---

### Function Calling

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

# Tool declaration
weather_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_current_weather",
            description="Get real-time weather conditions for a given location",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "location": types.Schema(
                        type="STRING",
                        description="City name, e.g. 'San Francisco' or 'Tokyo'",
                    ),
                    "unit": types.Schema(
                        type="STRING",
                        enum=["celsius", "fahrenheit"],
                        description="Temperature unit",
                    ),
                },
                required=["location"],
            ),
        )
    ]
)

response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="What is the weather in Tokyo right now?",
    config=types.GenerateContentConfig(tools=[weather_tool]),
)

if response.function_calls:
    for call in response.function_calls:
        print(f"Tool called: {call.name}")
        print(f"Arguments: {call.args}")
```

---

## Web Management Console

The service includes a built-in static Web Console served at `http://localhost:11451`:

- **Dashboard**: Real-time view of service health, per-model request stats, success rates, and rate-limit cooldown status.
- **Accounts**: Import Cookie credentials (single text or bundle files), auto-probe multiple accounts, switch the active account, or reset rate limits.
- **Settings**: Online editor for `config.yaml` with instant hot-reloading on save.
- **Access Control**: Enable authentication by setting `AISTUDIO_WEB_PASSWORD`.

---

## License

Distributed under the [MIT License](./LICENSE), retaining the original project's license and attribution.
