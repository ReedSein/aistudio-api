#!/usr/bin/env bash
# Shared environment preparation for installation and updates.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
source "${PROJECT_ROOT}/scripts/install-termux.sh" --logging-only setup-termux
trap 'status=$?; printf "[setup-termux] finished exit=%s\n" "$status" >&2' EXIT
cd "${PROJECT_ROOT}"
SYNC_ARGS=(--locked --no-python-downloads --no-dev)
case "${1:-}" in
    --dev) SYNC_ARGS+=(--inexact) ;;
    '') ;;
    *) echo "Usage: bash scripts/setup-termux.sh [--dev]" >&2; exit 2 ;;
esac
bash scripts/setup-env.sh

rebuild_venv() {
    if [[ -L .venv || ! -d .venv || -L .venv/pyvenv.cfg ]]; then
        echo "Cannot identify the project Python environment: ${PROJECT_ROOT}/.venv" >&2
        exit 1
    fi
    if [[ ! -f .venv/pyvenv.cfg && -n "$(find .venv -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        echo "Unknown files in ${PROJECT_ROOT}/.venv; nothing was removed." >&2
        exit 1
    fi
    printf 'Rebuilding broken project Python environment: %s/.venv\n' "${PROJECT_ROOT}"
    rm -rf -- "${PROJECT_ROOT}/.venv"
}

if [[ -e .venv || -L .venv ]]; then
    [[ ! -L .venv ]] || { echo 'The project Python environment is a symbolic link.' >&2; exit 1; }
    if ! uv run --no-project --no-python-downloads --python "${PROJECT_ROOT}/.venv/bin/python" python -c 'pass'; then
        rebuild_venv
    fi
fi
UV_HTTP_RETRIES=2 uv sync "${SYNC_ARGS[@]}"
IMPORT_CHECK='import curl_cffi, dotenv, fastapi, httpx, multipart, pydantic, uvicorn, websockets, yaml'
if ! uv run --no-sync --no-python-downloads python -c "${IMPORT_CHECK}"; then
    rebuild_venv
    UV_HTTP_RETRIES=2 uv sync "${SYNC_ARGS[@]}"
    uv run --no-sync --no-python-downloads python -c "${IMPORT_CHECK}"
fi
bash scripts/setup-browser.sh --project-root "${PROJECT_ROOT}"
