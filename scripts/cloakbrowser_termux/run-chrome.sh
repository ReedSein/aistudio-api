#!/data/data/com.termux/files/usr/bin/bash
# Termux launcher for CloakBrowser (anti-bot Chromium runtime).
#
# Why this script exists:
#   CloakBrowser is a glibc-linked ELF binary. Termux ships bionic libc,
#   which cannot exec glibc binaries (icu/nss/gbm fail to load). We bypass
#   that by running chrome inside a proot-distro Linux container, which
#   provides the full glibc stack.
#
# Layout (created by scripts/setup-browser.sh):
#   <project_root>/.cloakbrowser/chromium-<ver>/chrome + icudtl.dat + *.pak
#   <proot-rootfs>/usr/bin/chrome dependencies installed via apt
#
# Usage:
#   ./scripts/cloakbrowser_termux/run-chrome.sh --version
#   ./scripts/cloakbrowser_termux/run-chrome.sh --remote-debugging-port=9222 \
#       --user-data-dir=<abs path on host> --headless=new about:blank
#
# Env:
#   AISTUDIO_PROOT_NAME   proot-distro container name (default: aistudio-api-termux)
#   CLOAKBROWSER_PROJECT_DIR   override project root (default: dirname-of-script/../..)

set -euo pipefail

: "${AISTUDIO_PROOT_NAME:=aistudio-api-termux}"
: "${CLOAKBROWSER_PROJECT_DIR:=}"
if [[ -z "${CLOAKBROWSER_PROJECT_DIR}" ]]; then
    CLOAKBROWSER_PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
fi
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)/install-termux.sh" --logging-only run-chrome

CLOAK_ROOT="${CLOAKBROWSER_PROJECT_DIR}/.cloakbrowser"

read -r CHROMIUM_VERSION < "${CLOAKBROWSER_PROJECT_DIR}/scripts/cloakbrowser.version"
[[ "${CHROMIUM_VERSION}" =~ ^[0-9]+(\.[0-9]+)+$ ]] || { echo "invalid browser version" >&2; exit 2; }
CHROME_BIN="${CLOAK_ROOT}/chromium-${CHROMIUM_VERSION}/chrome"

if [[ ! -x "${CHROME_BIN}" ]]; then
    cat >&2 <<EOF
[run-chrome] No CloakBrowser binary found in ${CLOAK_ROOT}.

Install it once:
    bash scripts/setup-browser.sh --project-root "${CLOAKBROWSER_PROJECT_DIR}"
EOF
    exit 127
fi

if ! command -v proot-distro >/dev/null 2>&1; then
    cat >&2 <<EOF
[run-chrome] proot-distro not found. Install it once:
    pkg install -y proot-distro
EOF
    exit 127
fi

CONTAINERS="$(proot-distro list --quiet)"
if ! grep -Fxq -- "${AISTUDIO_PROOT_NAME}" <<< "${CONTAINERS}"; then
    cat >&2 <<EOF
[run-chrome] proot container '${AISTUDIO_PROOT_NAME}' is missing. Install once:
    bash scripts/setup-browser.sh --project-root "${CLOAKBROWSER_PROJECT_DIR}"
EOF
    exit 127
fi
CHROME_DIR_HOST="$(dirname "${CHROME_BIN}")"
PROOT_CHROME_DIR="/opt/cloakbrowser"
PROOT_CHROME_BIN="${PROOT_CHROME_DIR}/chrome"
PROOT_HOME="/root"

# Rewrite --user-data-dir to a proot-visible path; bind-mount host dir in.
ARGS=()
UDD_HOST=""
for ((i=1; i<=$#; i++)); do
    arg="${!i}"
    case "${arg}" in
        --user-data-dir=*)
            UDD_HOST="${arg#--user-data-dir=}"
            ;;
        *)
            ARGS+=("${arg}")
            ;;
    esac
done

BIND_ARGS=(
    "--bind" "${CHROME_DIR_HOST}:${PROOT_CHROME_DIR}"
    "--bind" "${CLOAKBROWSER_PROJECT_DIR}:/workspace"
)
PROOT_UDD=""
if [[ -n "${UDD_HOST}" ]]; then
    PROOT_UDD="${PROOT_HOME}/.chrome-profile"
    BIND_ARGS+=("--bind" "${UDD_HOST}:${PROOT_UDD}")
    ARGS=("--user-data-dir=${PROOT_UDD}" "${ARGS[@]}")
fi
exec proot-distro login "${AISTUDIO_PROOT_NAME}" \
    --work-dir "${PROOT_CHROME_DIR}" \
    "${BIND_ARGS[@]}" \
    --env HOME="${PROOT_HOME}" \
    -- "${PROOT_CHROME_BIN}" \
        "${ARGS[@]}"
