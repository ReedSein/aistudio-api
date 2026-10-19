# aistudio-api · Termux

A Termux-focused fork of [aistudio-api](https://github.com/DeconstructedCube/aistudio-api), maintained by ReedSein. It exposes Google AI Studio web sessions through the native Gemini API.

Targets Android ARM64 Termux with an interactive Chinese management menu, one-command installation, and coordinated updates. Python runs in Termux; CloakBrowser uses an Ubuntu proot container for its system libraries.

[中文](./README.md) · [Detailed Termux guide](./docs/TERMUX.md)

## Install

The default location is `~/aistudio-api-termux`, independent of the current directory:

Run in an interactive Termux terminal. The bootstrap uses Termux's bundled APT downloader, so a broken curl does not prevent fetching the installer. If the Termux package repository is unreachable, the installer can switch to mirrors in Chinese Mainland on confirmation; package repair or upgrades also require confirmation and affect tools shared with other Termux projects.

```bash
bash -c '
set -euo pipefail
umask 077
log_dir="${AISTUDIO_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/aistudio-termux/logs}"
test ! -L "$log_dir"
mkdir -p -- "$log_dir"
test ! -L "$log_dir/.aistudio-diagnostics-v1"
if test -f "$log_dir/.aistudio-diagnostics-v1"; then
  test "$(cat "$log_dir/.aistudio-diagnostics-v1")" = "aistudio-api diagnostics v1"
else
  test -z "$(ls -A -- "$log_dir")"
  (set -C; printf "aistudio-api diagnostics v1\n" > "$log_dir/.aistudio-diagnostics-v1")
fi
chmod 700 -- "$log_dir"
bootstrap_log=$(mktemp "$log_dir/script-bootstrap-XXXXXXXX.log")
printf "Bootstrap log: %s\n" "$bootstrap_log"
test -w "$TMPDIR"
install_tmp=$(mktemp -d "$TMPDIR/aistudio-bootstrap.XXXXXX")
cleanup() { status=$?; printf "bootstrap exit=%s\n" "$status" >> "$bootstrap_log"; rm -rf -- "$install_tmp"; return "$status"; }
trap cleanup EXIT
"$PREFIX/lib/apt/apt-helper" -o Acquire::Retries=2 -o Acquire::https::Timeout=15 download-file \
  https://github.com/ReedSein/aistudio-api-termux/releases/latest/download/install-termux.sh "$install_tmp/install-termux.sh" 2>&1 | \
  sed -u -E "s#(https?://)[^/@[:space:]]+:[^/@[:space:]]+@#\\1[REDACTED]@#gI" | tee -a "$bootstrap_log"
bash "$install_tmp/install-termux.sh" "$@"
' --
```

Choose a different parent directory, for example `~/projects/aistudio-api-termux`:

```bash
bash -c '
set -euo pipefail
umask 077
log_dir="${AISTUDIO_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/aistudio-termux/logs}"
test ! -L "$log_dir"
mkdir -p -- "$log_dir"
test ! -L "$log_dir/.aistudio-diagnostics-v1"
if test -f "$log_dir/.aistudio-diagnostics-v1"; then
  test "$(cat "$log_dir/.aistudio-diagnostics-v1")" = "aistudio-api diagnostics v1"
else
  test -z "$(ls -A -- "$log_dir")"
  (set -C; printf "aistudio-api diagnostics v1\n" > "$log_dir/.aistudio-diagnostics-v1")
fi
chmod 700 -- "$log_dir"
bootstrap_log=$(mktemp "$log_dir/script-bootstrap-XXXXXXXX.log")
printf "Bootstrap log: %s\n" "$bootstrap_log"
test -w "$TMPDIR"
install_tmp=$(mktemp -d "$TMPDIR/aistudio-bootstrap.XXXXXX")
cleanup() { status=$?; printf "bootstrap exit=%s\n" "$status" >> "$bootstrap_log"; rm -rf -- "$install_tmp"; return "$status"; }
trap cleanup EXIT
"$PREFIX/lib/apt/apt-helper" -o Acquire::Retries=2 -o Acquire::https::Timeout=15 download-file \
  https://github.com/ReedSein/aistudio-api-termux/releases/latest/download/install-termux.sh "$install_tmp/install-termux.sh" 2>&1 | \
  sed -u -E "s#(https?://)[^/@[:space:]]+:[^/@[:space:]]+@#\\1[REDACTED]@#gI" | tee -a "$bootstrap_log"
bash "$install_tmp/install-termux.sh" "$@"
' -- --dir "$HOME/projects"
```

Public installs and updates use the maintainer-selected latest release, tagged `termux-vX.Y.Z`. Development commits do not change the public version. The install link becomes available after the first formal release; release lookup failures stop installation.

Rerun the same command to resume installation or reinstall at the same location using retained data. Unrelated directories, foreign repositories, and local source edits are preserved and reported. After the repository rename to aistudio-api-termux the previous address is still recognised; rerunning the new installer continues an older installation and keeps its existing directory and browser container.

After an interrupted first install, rerun the same command. Usable source and browser files are reused; incomplete browser downloads resume from existing bytes and are verified with SHA-256 before extraction, while broken project environments and invalid caches are rebuilt. Installer downloads, release lookups, source transfers, and browser downloads make at most three attempts per run and report the original error on persistent failure.

## Local development and testing

Use the current checkout, including uncommitted edits:

```bash
cd ~/projects/aistudio-api
bash scripts/install-termux.sh --dev --dir "$HOME/projects"
aistudio
```

This reuses existing configuration, accounts, data, ports, and the browser container. Environment preparation does not fetch or switch source code. Starting the service does not require committing, pushing, or publishing. Only an explicit menu update follows `master` in development mode. See the [Termux guide](./docs/TERMUX.md) for a fresh development install.

## Manage

```bash
aistudio
```

The menu offers foreground start, silent background start, stop, check for updates, update, uninstall, and an optional cleanup for the original author's installation. Public updates follow formal releases; development updates follow this fork's `master`. Ctrl+C stops a foreground service and returns to the menu. A silent service keeps running after the menu exits and retains runtime diagnostics and full request/response content. Export one support archive from the menu, the console Settings page, or `aistudio --export-logs`; authentication credentials are redacted. The default retention is seven days with a 512 MiB budget. Opening the menu briefly checks the latest formal release, and the console top bar shows the same notice; a failed check never blocks normal use.

Uninstall keeps configuration, accounts, and statistics by default, with a separate option to clear installation data. The original-author cleanup likewise keeps accounts, settings, and browser login data unless its explicit clear-data choice is used. Both show their scope and require confirmation. Development source and Git history are retained, as are shared system packages and browser containers.

The default console address is `http://127.0.0.1:11451`. Import Cookies or an account bundle in the Accounts page. Updates cover application and management code, locked Python dependencies, and the declared browser version; an unchanged browser is reused. A running service is stopped briefly and restarted in its previous mode after a successful update.

User configuration lives in `~/.config/aistudio-termux/`; existing account and statistics data remain in their original locations. Android may still terminate background processes.

## Maintenance and documentation

Installations follow `ReedSein/aistudio-api-termux` only. Maintainers evaluate and test changes from the original project before bringing them into this fork.

- [Paths, ports, configuration, updates and recovery](./docs/TERMUX.md)
- [Diagnostics, offline export and privacy](./docs/DIAGNOSTICS.md)
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
- **Userscript**: Capture Cookies on aistudio.google.com and push them to this service with one tap; multi-account auto-probe import ([guide](./docs/USERSCRIPT.md)).

---

## License

Distributed under the [MIT License](./LICENSE), retaining the original project's license and attribution.
