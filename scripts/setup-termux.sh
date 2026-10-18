#!/usr/bin/env bash
# Shared environment preparation for installation and updates.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${PROJECT_ROOT}"
SYNC_ARGS=(--locked --no-python-downloads --no-dev)
case "${1:-}" in
    --dev) SYNC_ARGS+=(--inexact) ;;
    '') ;;
    *) echo "Usage: bash scripts/setup-termux.sh [--dev]" >&2; exit 2 ;;
esac
bash scripts/setup-env.sh
uv sync "${SYNC_ARGS[@]}"
bash scripts/setup-browser.sh --project-root "${PROJECT_ROOT}"
