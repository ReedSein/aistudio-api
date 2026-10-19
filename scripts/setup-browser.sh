#!/usr/bin/env bash
# Cross-platform browser setup for aistudio-api.
# Installs or stages Chromium / CloakBrowser according to the current platform.
#
# Platforms supported:
#   - Android (Termux): Sets up proot-distro container (aistudio-api-termux) and stages CloakBrowser ARM64.
#   - Linux (x86_64 / aarch64): Downloads prebuilt stealth CloakBrowser into .cloakbrowser/
#     or verifies system Chromium / Chrome.
#   - macOS (Apple Silicon / Intel) / Windows: Verifies installed system Chrome/Chromium/Edge.
#
# Flags:
#   --project-root PATH      where to place .cloakbrowser (default: this project)
#   --proot-name NAME        container name for Termux (default: aistudio-api-termux)
#   --proot-distro IMAGE     OCI image for Termux (default: ubuntu:24.04)
#   --skip-proot-install     don't create/update proot container
#   --skip-browser-download  don't re-fetch CloakBrowser tarball

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROOT_NAME="${AISTUDIO_PROOT_NAME:-aistudio-api-termux}"
PROOT_IMAGE="ubuntu:24.04"
read -r CHROMIUM_VERSION < "${SCRIPT_DIR}/cloakbrowser.version"
SKIP_PROOT=0
SKIP_BROWSER=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --project-root|--proot-name|--proot-distro)
            [[ $# -ge 2 && -n "$2" ]] || { echo "missing value: $1" >&2; exit 2; }
            case "$1" in
                --project-root) PROJECT_ROOT="$2" ;;
                --proot-name) PROOT_NAME="$2" ;;
                --proot-distro) PROOT_IMAGE="$2" ;;
            esac
            shift 2 ;;
        --skip-proot-install) SKIP_PROOT=1; shift ;;
        --skip-browser-download) SKIP_BROWSER=1; shift ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

[[ "${CHROMIUM_VERSION}" =~ ^[0-9]+(\.[0-9]+)+$ ]] || { echo "invalid browser version" >&2; exit 2; }
[[ "${PROOT_NAME}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || { echo "invalid container name" >&2; exit 2; }

PROJECT_ROOT="$(cd "${PROJECT_ROOT}" && pwd -P)"
CLOAK_ROOT="${PROJECT_ROOT}/.cloakbrowser"
CHROME_DIR_HOST="${CLOAK_ROOT}/chromium-${CHROMIUM_VERSION}"

log() { printf '[setup-browser] %s\n' "$*" >&2; }

is_termux() {
    [[ -n "${PREFIX:-}" && "${PREFIX}" == *"/com.termux"* ]] || command -v pkg >/dev/null 2>&1
}

# ==============================================================================
# Platform Branch 1: Android (Termux with proot-distro container)
# ==============================================================================
setup_termux() {
    log "Configuring Termux CloakBrowser environment (proot-distro)..."

    [[ ! -L "${CLOAK_ROOT}" ]] || { log 'Browser cache root is a symbolic link; stopped.'; exit 1; }
    mkdir -p "${CLOAK_ROOT}"
    local owner_file="${CLOAK_ROOT}/.proot-installing"
    local owner="${PROOT_NAME}"$'\n'"${PROOT_IMAGE}"
    [[ ! -L "${owner_file}" ]] || { log 'Container ownership record is a symbolic link.'; exit 1; }
    if [[ -e "${owner_file}" && "$(cat "${owner_file}")" != "${owner}" ]]; then
        log "A different unfinished container is recorded in ${owner_file}; stopped."
        exit 1
    fi

    if ! command -v proot-distro >/dev/null 2>&1; then
        if command -v pkg >/dev/null 2>&1; then
            log "Installing proot-distro on Termux host..."
            pkg install -y proot-distro
        else
            echo "proot-distro is required. Install it via 'pkg install proot-distro'." >&2
            exit 1
        fi
    fi

    # 1. proot container
    CONTAINERS="$(proot-distro list --quiet)"
    if grep -Fxq -- "${PROOT_NAME}" <<< "${CONTAINERS}"; then
        if proot-distro login "${PROOT_NAME}" -- /bin/true; then
            log "Container '${PROOT_NAME}' can log in; reusing it."
        elif [[ -f "${owner_file}" && ${SKIP_PROOT} -eq 0 ]]; then
            log "Rebuilding this installation's incomplete container: ${PROOT_NAME}"
            proot-distro remove "${PROOT_NAME}"
            proot-distro install "${PROOT_IMAGE}" --name "${PROOT_NAME}"
        else
            log "Container '${PROOT_NAME}' cannot log in and cannot be identified as this first install; stopped."
            exit 1
        fi
    elif [[ ${SKIP_PROOT} -eq 1 ]]; then
        log "--skip-proot-install set but container missing; aborting."
        exit 1
    else
        log "Installing proot container '${PROOT_NAME}' from ${PROOT_IMAGE}..."
        printf '%s\n' "${owner}" > "${owner_file}"
        proot-distro install "${PROOT_IMAGE}" --name "${PROOT_NAME}"
    fi

    # 2. runtime deps inside container
    log "Ensuring runtime libs inside container '${PROOT_NAME}'..."
    proot-distro login "${PROOT_NAME}" -- bash -c '
        set -euo pipefail
        export DEBIAN_FRONTEND=noninteractive
        export TZ=UTC
        audit="$(dpkg --audit)"
        if [[ -n "${audit}" ]]; then
            if ! dpkg --configure -a; then
                apt-get update
                apt-get --fix-broken install -y
                dpkg --configure -a
            fi
        fi
        packages=( \
            ca-certificates curl \
            libnss3 libnspr4 libgbm1 libasound2t64 \
            libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
            libxfixes3 libxext6 libxrender1 libx11-6 libxcb1 \
            libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
            libgtk-3-0t64 libnotify4 fonts-liberation libpangocairo-1.0-0 \
            fonts-noto-color-emoji fonts-freefont-ttf fonts-unifont fonts-wqy-zenhei )
        missing=()
        for package in "${packages[@]}"; do
            if [[ "$(dpkg-query -W -f="\${db:Status-Status}" "$package" 2>/dev/null || true)" != installed ]]; then
                missing+=("$package")
            fi
        done
        if [[ ${#missing[@]} -gt 0 ]]; then
            apt-get update
            apt-get install -y --no-install-recommends "${missing[@]}"
            apt-get clean
        fi
        mkdir -p /opt/cloakbrowser
    '

    # 3. CloakBrowser download
    mkdir -p "${CLOAK_ROOT}"
    ARCHIVE_NAME="cloakbrowser-linux-arm64.tar.gz"
    DOWNLOAD_URL="https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v${CHROMIUM_VERSION}/${ARCHIVE_NAME}"
    local sha256_file="${SCRIPT_DIR}/cloakbrowser-linux-arm64.sha256"
    local archive_sha256=""
    if [[ ! -f "${sha256_file}" || -L "${sha256_file}" ]] || ! read -r archive_sha256 < "${sha256_file}" || [[ ! "${archive_sha256}" =~ ^[0-9a-f]{64}$ ]]; then
        log "Missing or invalid browser SHA-256 file: ${sha256_file}"
        exit 1
    fi

    [[ ! -L "${CHROME_DIR_HOST}" ]] || { log 'Browser version directory is a symbolic link.'; exit 1; }
    if [[ -x "${CHROME_DIR_HOST}/chrome" && -f "${CHROME_DIR_HOST}/icudtl.dat" ]] && CLOAKBROWSER_PROJECT_DIR="${PROJECT_ROOT}" AISTUDIO_PROOT_NAME="${PROOT_NAME}" bash "${SCRIPT_DIR}/cloakbrowser_termux/run-chrome.sh" --version; then
        log "CloakBrowser ${CHROMIUM_VERSION} already cached at ${CHROME_DIR_HOST}"
    elif [[ ${SKIP_BROWSER} -eq 1 ]]; then
        log "--skip-browser-download set but no chrome binary; aborting."
        exit 1
    else
        if [[ -e "${CHROME_DIR_HOST}" ]]; then
            if [[ ! -d "${CHROME_DIR_HOST}" ]] || { [[ ! -f "${CHROME_DIR_HOST}/chrome" && ! -f "${CHROME_DIR_HOST}/icudtl.dat" ]] && [[ -n "$(find "${CHROME_DIR_HOST}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; }; then
                log "Unknown files in browser cache ${CHROME_DIR_HOST}; stopped."
                exit 1
            fi
            log "Removing incomplete browser cache: ${CHROME_DIR_HOST}"
            rm -rf -- "${CHROME_DIR_HOST}"
        fi
        log "Downloading CloakBrowser ARM64 (~200MB)..."
        [[ -n "${TMPDIR:-}" && -w "${TMPDIR}" ]] || { log "TMPDIR must be writable."; exit 1; }
        local download_dir="${CLOAK_ROOT}/downloads"
        local archive_path="${download_dir}/cloakbrowser-${CHROMIUM_VERSION}-linux-arm64.tar.gz"
        [[ ! -L "${download_dir}" ]] || { log 'Browser download directory is a symbolic link.'; exit 1; }
        mkdir -p "${download_dir}"
        if [[ -f "${archive_path}" && ! -L "${archive_path}" && ! -e "${archive_path}.part" ]]; then
            mv -- "${archive_path}" "${archive_path}.part"
        fi
        bash "${SCRIPT_DIR}/install-termux.sh" --download "${DOWNLOAD_URL}" "${archive_path}" "${archive_sha256}"
        uv run --no-sync --no-python-downloads --project "${PROJECT_ROOT}" python \
            "${SCRIPT_DIR}/unpack_browser.py" "${archive_path}" "${CHROME_DIR_HOST}"
        rm -f -- "${archive_path}"
        log "CloakBrowser extracted to ${CHROME_DIR_HOST}"
    fi

    # The launcher binds this exact cached version into /opt/cloakbrowser.
    # No second browser copy inside the container can become stale.
    CLOAKBROWSER_PROJECT_DIR="${PROJECT_ROOT}" AISTUDIO_PROOT_NAME="${PROOT_NAME}" \
        bash "${SCRIPT_DIR}/cloakbrowser_termux/run-chrome.sh" --version
    [[ ! -f "${owner_file}" ]] || rm -- "${owner_file}"
}

# ==============================================================================
# Platform Branch 2: Standard Linux (x86_64 / aarch64 native)
# ==============================================================================
setup_linux() {
    local arch="$(uname -m)"
    log "Configuring Linux native browser (${arch})..."

    # Enforce CloakBrowser only; system Chrome/Edge/Brave are strictly ignored

    # If no system browser, provide standalone CloakBrowser
    mkdir -p "${CLOAK_ROOT}"
    local archive=""
    case "${arch}" in
        x86_64|amd64) archive="cloakbrowser-linux-x64.tar.gz" ;;
        aarch64|arm64) archive="cloakbrowser-linux-arm64.tar.gz" ;;
        *)
            log "Architecture ${arch} does not have a precompiled CloakBrowser bundle."
            log "Please install chromium via your system package manager (e.g. apt-get install chromium)."
            return 0
            ;;
    esac

    local download_url="https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v${CHROMIUM_VERSION}/${archive}"
    if [[ -x "${CHROME_DIR_HOST}/chrome" ]]; then
        log "CloakBrowser ${CHROMIUM_VERSION} already installed at ${CHROME_DIR_HOST}"
    else
        log "Downloading CloakBrowser (${arch}) from ${download_url}..."
        TMP="$(mktemp -d)"
        trap 'rm -rf "${TMP}"' EXIT
        curl -fSL --retry 3 -o "${TMP}/${archive}" "${download_url}"
        mkdir -p "${CHROME_DIR_HOST}"
        tar -xzf "${TMP}/${archive}" -C "${CHROME_DIR_HOST}"
        if [[ ! -f "${CHROME_DIR_HOST}/chrome" ]]; then
            INNER="$(find "${CHROME_DIR_HOST}" -mindepth 1 -maxdepth 2 -type f -name chrome | head -1 || true)"
            if [[ -n "${INNER}" ]]; then
                INNER_DIR="$(dirname "${INNER}")"
                if [[ "${INNER_DIR}" != "${CHROME_DIR_HOST}" ]]; then
                    mv "${INNER_DIR}"/* "${CHROME_DIR_HOST}/"
                    rmdir "${INNER_DIR}" 2>/dev/null || true
                fi
            fi
        fi
        chmod +x "${CHROME_DIR_HOST}/chrome" 2>/dev/null || true
        log "CloakBrowser ready at ${CHROME_DIR_HOST}/chrome"
    fi
}

# ==============================================================================
# Platform Branch 3: macOS / Windows
# ==============================================================================
setup_desktop() {
    local os_type="$(uname -s)"
    log "Configuring CloakBrowser for ${os_type}..."

    mkdir -p "${CLOAK_ROOT}"

    if [[ "${os_type}" =~ (MINGW|MSYS|CYGWIN|Windows) ]]; then
        # Windows via Git Bash / MSYS
        local archive="cloakbrowser-windows-x64.zip"
        local download_url="https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v${CHROMIUM_VERSION}/${archive}"
        if [[ -x "${CHROME_DIR_HOST}/chrome.exe" ]]; then
            log "CloakBrowser ${CHROMIUM_VERSION} already installed at ${CHROME_DIR_HOST}/chrome.exe"
            return 0
        fi
        log "Downloading CloakBrowser Windows x64 from ${download_url}..."
        TMP="$(mktemp -d)"
        trap 'rm -rf "${TMP}"' EXIT
        curl -fSL --retry 3 -o "${TMP}/${archive}" "${download_url}"
        unzip -q -o "${TMP}/${archive}" -d "${CHROME_DIR_HOST}"
        log "CloakBrowser ready at ${CHROME_DIR_HOST}/chrome.exe"
        return 0
    elif [[ "${os_type}" == "Darwin" ]]; then
        local arch="$(uname -m)"
        local archive=""
        case "${arch}" in
            arm64) archive="cloakbrowser-darwin-arm64.tar.gz" ;;
            x86_64) archive="cloakbrowser-darwin-x64.tar.gz" ;;
            *) archive="cloakbrowser-darwin-arm64.tar.gz" ;;
        esac
        if [[ -x "${CHROME_DIR_HOST}/chrome" || -d "${CHROME_DIR_HOST}/Chromium.app" ]]; then
            log "CloakBrowser ${CHROMIUM_VERSION} already installed at ${CHROME_DIR_HOST}"
            return 0
        fi
        local download_url="https://github.com/CloakHQ/cloakbrowser/releases/download/chromium-v${CHROMIUM_VERSION}/${archive}"
        log "Downloading CloakBrowser macOS (${arch}) from ${download_url}..."
        TMP="$(mktemp -d)"
        trap 'rm -rf "${TMP}"' EXIT
        if curl -fSL --retry 3 -o "${TMP}/${archive}" "${download_url}" 2>/dev/null; then
            tar -xzf "${TMP}/${archive}" -C "${CLOAK_ROOT}"
            INNER="$(find "${CLOAK_ROOT}" -mindepth 2 -maxdepth 2 -type d -name 'chromium-*' | head -1 || true)"
            if [[ -n "${INNER}" && "${INNER}" != "${CHROME_DIR_HOST}" ]]; then
                mv "${INNER}" "${CHROME_DIR_HOST}"
            fi
            log "CloakBrowser ready at ${CHROME_DIR_HOST}"
        else
            log "Notice: CloakBrowser macOS release asset was not found for version ${CHROMIUM_VERSION}."
            log "Please place CloakBrowser in .cloakbrowser/ or set AISTUDIO_BROWSER_EXECUTABLE."
        fi
        return 0
    fi

    log "Notice: Unknown desktop platform ${os_type}. Please run scripts/install-browser.ps1 on Windows or setup-browser.sh on Linux."
}

# ---------- Main Dispatch ----------
if is_termux; then
    setup_termux
elif [[ "$(uname -s)" == "Linux" ]]; then
    setup_linux
else
    setup_desktop
fi

if command -v ruff >/dev/null 2>&1 && [[ -d "${PROJECT_ROOT}/.venv/bin" && ! -e "${PROJECT_ROOT}/.venv/bin/ruff" ]]; then
    ln -sf "$(command -v ruff)" "${PROJECT_ROOT}/.venv/bin/ruff"
fi

log "Browser setup check complete."
