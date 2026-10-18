#!/usr/bin/env bash
# Shared environment preparation for installation and updates.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${PROJECT_ROOT}"
bash scripts/setup-env.sh
uv sync --locked --no-python-downloads --no-dev
bash scripts/setup-browser.sh --project-root "${PROJECT_ROOT}"
