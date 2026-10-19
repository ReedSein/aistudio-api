#!/usr/bin/env bash
# Install the maintained Termux fork; --dir is the parent of aistudio-api-termux/.
set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")"
NETWORK_TMP=""
CLONE_STAGE=""

cleanup() {
    [[ -z "${NETWORK_TMP}" ]] || rm -rf -- "${NETWORK_TMP}"
    [[ -z "${CLONE_STAGE}" ]] || rm -rf -- "${CLONE_STAGE}"
    return 0
}
trap cleanup EXIT

network_tmp() {
    [[ -n "${TMPDIR:-}" && -w "${TMPDIR}" ]] || { echo "TMPDIR must be writable." >&2; exit 1; }
    NETWORK_TMP="$(mktemp -d "${TMPDIR%/}/aistudio-network.XXXXXX")"
}

curl_retry() {
    local attempt status output
    for attempt in 1 2 3; do
        if output=$(LC_ALL=C curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL --connect-timeout 15 "$@" 2>"${NETWORK_TMP}/error"); then
            printf '%s' "${output}"
            return 0
        else
            status=$?
        fi
        cat "${NETWORK_TMP}/error" >&2
        case "${status}" in
            5|6|7|18|28|35|52|55|56|92) ;;
            22) grep -Eq 'error: (408|429|50[0234]|52[24])' "${NETWORK_TMP}/error" || return "${status}" ;;
            *) return "${status}" ;;
        esac
        [[ "${attempt}" != 3 ]] || return "${status}"
        printf '[install] Download interrupted; retry %s/3.\n' "$((attempt + 1))" >&2
        sleep 2
    done
}

git_retry() {
    local attempt status operation="$1"
    [[ "${operation}" != -C ]] || operation="${3:-Git}"
    for attempt in 1 2 3; do
        printf '[install] Git %s: attempt %s/3.\n' "${operation}" "${attempt}" >&2
        if [[ "${attempt}" != 1 && "$1" == clone && -n "${CLONE_STAGE}" ]]; then
            rm -rf -- "${CLONE_STAGE}/source"
        fi
        if timeout 600 git -c http.lowSpeedLimit=1 -c http.lowSpeedTime=60 "$@" 2>"${NETWORK_TMP}/error"; then
            cat "${NETWORK_TMP}/error" >&2
            return 0
        else
            status=$?
        fi
        cat "${NETWORK_TMP}/error" >&2
        if [[ "${status}" != 124 ]] && ! grep -Eqi 'TLS|SSL|Could not resolve|Failed to connect|Connection.*(reset|timed out)|remote end hung up|early EOF|RPC failed' "${NETWORK_TMP}/error"; then
            return "${status}"
        fi
        if [[ "${attempt}" == 3 ]]; then
            printf '[install] Git %s failed after three attempts (exit %s).\n' "${operation}" "${status}" >&2
            return "${status}"
        fi
        printf '[install] Git %s interrupted; retry %s/3.\n' "${operation}" "$((attempt + 1))" >&2
        sleep 2
    done
}

HOST_PACKAGES=(python git ca-certificates curl proot-distro uv gum clang pkg-config)

require_termux() {
    command -v pkg >/dev/null 2>&1 || { echo 'This installer requires Termux.' >&2; exit 1; }
    [[ "$(uname -m)" == aarch64 ]] || { echo 'This installer supports ARM64 Termux.' >&2; exit 1; }
}

host_tool() {
    [[ "$(dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null)" == installed ]] || return 1
    case "$1" in
        ca-certificates) return 0 ;;
        proot-distro) proot-distro list --quiet >/dev/null ;;
        *) "$1" --version >/dev/null ;;
    esac
}

host_health() {
    local package failed=0
    for package in "${HOST_PACKAGES[@]}"; do
        if ! host_tool "${package}"; then
            printf '[install] Required Termux package is missing or cannot run: %s\n' "${package}" >&2
            failed=1
        fi
    done
    return "${failed}"
}

prepare_host() {
    local force="${1:-false}" healthy=true audit plan plan_ok=true answer package
    local -a broken=()
    host_health >/dev/null 2>&1 || healthy=false
    audit="$(dpkg --audit)"
    if [[ "${force}" == false && "${healthy}" == true && -z "${audit}" ]]; then
        return 0
    fi
    apt-get update
    if plan="$(LC_ALL=C apt-get --simulate dist-upgrade 2>&1)"; then
        if [[ "${healthy}" == true && -z "${audit}" ]] && ! grep -Eq '^(Inst|Conf|Remv) ' <<<"${plan}"; then
            return 0
        fi
    else
        plan_ok=false
    fi
    printf '\n[install] Termux packages need repair or upgrade.\nThis updates shared tools used by other Termux projects.\nRequired packages: %s\n' "${HOST_PACKAGES[*]}" >&2
    printf '%s\n%s\n' "${audit}" "${plan}" >&2
    if [[ "${healthy}" == false ]] && ! host_health; then
        echo '[install] These tools must be repaired before downloading source.' >&2
    fi
    if [[ ! -t 0 ]]; then
        echo 'Run this installer in an interactive Termux terminal to confirm package repair.' >&2
        return 1
    fi
    read -r -p 'Repair and upgrade Termux packages? [y/N] ' answer || return 1
    case "${answer}" in y|Y|yes|YES) ;; *) echo 'Package repair cancelled.' >&2; return 1 ;; esac
    if [[ -n "${audit}" ]]; then
        if ! dpkg --configure -a; then
            echo '[install] Package configuration failed; repairing dependencies with APT.' >&2
            apt-get --fix-broken install -y
            dpkg --configure -a
        fi
    fi
    if [[ "${plan_ok}" == false || -n "${audit}" ]]; then
        apt-get --fix-broken install -y
    fi
    apt-get dist-upgrade -y
    apt-get install -y "${HOST_PACKAGES[@]}"
    for package in "${HOST_PACKAGES[@]}"; do
        host_tool "${package}" || broken+=("${package}")
    done
    if [[ ${#broken[@]} -gt 0 ]]; then
        printf '[install] Reinstalling packages that still cannot run: %s\n' "${broken[*]}" >&2
        apt-get install --reinstall -y "${broken[@]}"
    fi
    host_health || return 1
    audit="$(dpkg --audit)"
    [[ -z "${audit}" ]] || { printf '%s\n' "${audit}" >&2; return 1; }
}

# These entry points share preparation and transfer behavior with setup scripts.
case "${1:-}" in
    --prepare-host) require_termux; prepare_host; exit $? ;;
    --download)
        [[ $# == 3 && "$2" == https://* && ! -e "$3" && ! -L "$3" ]] || { echo 'Expected HTTPS URL and a new download destination.' >&2; exit 2; }
        network_tmp
        curl_retry --max-time 1800 --speed-limit 1024 --speed-time 60 -o "${NETWORK_TMP}/payload" "$2"
        mv -- "${NETWORK_TMP}/payload" "$3"
        exit 0 ;;
    --git) shift; [[ $# -gt 0 ]] || { echo 'Missing Git operation.' >&2; exit 2; }; network_tmp; git_retry "$@"; exit $? ;;
esac

REPOSITORY="https://github.com/ReedSein/aistudio-api-termux.git"
INSTALL_PARENT="${HOME}"
DEVELOPMENT=false
CLEAN_LEGACY=false
CLEAN_LEGACY_DATA=false

usage() {
    cat <<'EOF'
Usage: bash install-termux.sh [--dev] [--dir PARENT_DIRECTORY] [--clean-legacy [--clear-data]]

Default: ~/aistudio-api-termux
Example: --dir "$HOME/projects" installs ~/projects/aistudio-api-termux
Default: install the maintainer-selected public release.
--dev: use an existing local checkout as-is, or clone master for a new checkout.
--clean-legacy: prepare the checkout, then clean a failed original-author
  installation and exit without installing or registering.
--clear-data: with --clean-legacy, also remove the original installation's
  data; deletion is irreversible.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev) DEVELOPMENT=true; shift ;;
        --clean-legacy) CLEAN_LEGACY=true; shift ;;
        --clear-data) CLEAN_LEGACY_DATA=true; shift ;;
        --dir)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo "--dir requires a parent directory" >&2; exit 2; }
            INSTALL_PARENT="$2"
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "${CLEAN_LEGACY_DATA}" == true && "${CLEAN_LEGACY}" == false ]]; then
    echo "--clear-data requires --clean-legacy" >&2
    exit 2
fi

require_termux
[[ -n "${TMPDIR:-}" && -w "${TMPDIR}" ]] || { echo "TMPDIR must be writable." >&2; exit 1; }
case "${INSTALL_PARENT}" in
    '~') INSTALL_PARENT="${HOME}" ;;
    '~/'*) INSTALL_PARENT="${HOME}/${INSTALL_PARENT#\~/}" ;;
esac
if [[ "${INSTALL_PARENT}" == *:* || "${INSTALL_PARENT}" == *$'\n'* ]]; then
    echo "Installation paths cannot contain ':' or a newline (proot bind syntax)." >&2
    exit 2
fi

prepare_host true
network_tmp
mkdir -p -- "${INSTALL_PARENT}"
INSTALL_PARENT="$(cd "${INSTALL_PARENT}" && pwd -P)"
PROJECT_ROOT="${INSTALL_PARENT}/aistudio-api-termux"
if [[ -L "${PROJECT_ROOT}" ]]; then
    echo "Refusing to install over a symbolic link: ${PROJECT_ROOT}" >&2
    exit 1
fi

REFERENCE=master
if [[ "${DEVELOPMENT}" == false ]]; then
    RELEASE_URL="$(curl_retry --max-time 60 -o /dev/null -w '%{url_effective}' https://github.com/ReedSein/aistudio-api-termux/releases/latest)" || {
        echo "Cannot obtain the public release. Check the network error above; HTTP 404 means no public release is available." >&2
        exit 1
    }
    REFERENCE="${RELEASE_URL#https://github.com/ReedSein/aistudio-api-termux/releases/tag/}"
    [[ "${REFERENCE}" =~ ^termux-v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
        echo "Public releases must use a termux-vX.Y.Z tag; no source was changed." >&2
        exit 1
    }
fi

if [[ -d "${PROJECT_ROOT}" && -z "$(find "${PROJECT_ROOT}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    rmdir -- "${PROJECT_ROOT}"
fi
if [[ -d "${PROJECT_ROOT}/.git" ]] && ! git -C "${PROJECT_ROOT}" rev-parse --verify HEAD >/dev/null 2>&1; then
    if [[ -L "${PROJECT_ROOT}/.git" ]] || \
        ! PARTIAL_REMOTE="$(git -C "${PROJECT_ROOT}" remote get-url --all origin)" || \
        ! PARTIAL_REFS="$(git -C "${PROJECT_ROOT}" for-each-ref)" || \
        ! PARTIAL_FILES="$(git -C "${PROJECT_ROOT}" ls-files)" || \
        ! PARTIAL_CONTENTS="$(find "${PROJECT_ROOT}" -mindepth 1 -maxdepth 1 ! -name .git -print -quit)"; then
        echo "Cannot verify incomplete repository contents; nothing was removed: ${PROJECT_ROOT}" >&2
        exit 1
    fi
    if [[ "${PARTIAL_REMOTE}" != "${REPOSITORY}" || -n "${PARTIAL_REFS}" || -n "${PARTIAL_FILES}" || -n "${PARTIAL_CONTENTS}" ]]; then
        echo "Incomplete repository cannot be identified as a first-install download: ${PROJECT_ROOT}" >&2
        exit 1
    fi
    printf '[install] Replacing incomplete source download: %s\n' "${PROJECT_ROOT}"
    rm -rf -- "${PROJECT_ROOT}/.git"
    rmdir -- "${PROJECT_ROOT}"
fi

if [[ ! -e "${PROJECT_ROOT}" ]]; then
    CLONE_STAGE="$(mktemp -d "${INSTALL_PARENT}/.aistudio-clone.XXXXXX")"
    git_retry clone --branch "${REFERENCE}" --single-branch "${REPOSITORY}" "${CLONE_STAGE}/source"
    mv -T --no-clobber -- "${CLONE_STAGE}/source" "${PROJECT_ROOT}"
    [[ ! -e "${CLONE_STAGE}/source" ]] || { echo 'Installation destination appeared during download; nothing was overwritten.' >&2; exit 1; }
elif [[ ! -d "${PROJECT_ROOT}/.git" && ! -f "${PROJECT_ROOT}/.git" ]]; then
    # A keep-data uninstall retains its registration, but removes program files.
    uv run --no-project --no-python-downloads python - "${PROJECT_ROOT}" <<'PY'
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
record = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "aistudio-termux/installation.json"
try:
    saved = json.loads(record.read_text())
except (OSError, ValueError):
    saved = {}
if not isinstance(saved, dict) or saved.get("project_root") != str(root):
    sys.exit("Existing directory is not an installation; nothing was overwritten.")
PY
    git -C "${PROJECT_ROOT}" init --initial-branch=master
    git -C "${PROJECT_ROOT}" remote add origin "${REPOSITORY}"
    if [[ "${DEVELOPMENT}" == true ]]; then
        git_retry -C "${PROJECT_ROOT}" fetch --no-tags origin master
        git -C "${PROJECT_ROOT}" checkout --no-overwrite-ignore -B master FETCH_HEAD
        git -C "${PROJECT_ROOT}" branch --set-upstream-to=origin/master master
    else
        git_retry -C "${PROJECT_ROOT}" fetch --no-tags origin "refs/tags/${REFERENCE}:refs/tags/${REFERENCE}"
        git -C "${PROJECT_ROOT}" checkout --no-overwrite-ignore --detach FETCH_HEAD
    fi
fi

cd "${PROJECT_ROOT}"
[[ "$(git rev-parse --show-toplevel)" == "${PROJECT_ROOT}" ]] || { echo "Unexpected repository root." >&2; exit 1; }
REMOTE="$(git remote get-url --all origin)"
[[ "${REMOTE}" == "${REPOSITORY}" || "${REMOTE}" == git@github.com:ReedSein/aistudio-api-termux.git || "${REMOTE}" == https://github.com/ReedSein/aistudio-api.git || "${REMOTE}" == git@github.com:ReedSein/aistudio-api.git ]] || {
    echo "Existing repository is not ReedSein/aistudio-api-termux; nothing was overwritten." >&2
    exit 1
}
if [[ "${DEVELOPMENT}" == false ]]; then
    # The bootstrap must also work with installations predating release support.
    uv run --no-project --no-python-downloads python - "${PROJECT_ROOT}" "${REFERENCE}" "${SCRIPT_PATH}" <<'PY'
import subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from termux_runtime import Installation, ManagerError, Service, operation_lock

def git(*args):
    command = ["bash", sys.argv[3], "--git", *args] if args[0] == "fetch" else ["git", *args]
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()

try:
    installation = Installation.load(Path(sys.argv[1]))
    with operation_lock(installation):
        if Service(installation).running():
            raise ManagerError("Stop the running service before reinstalling; use the menu to update it.")
        if git("status", "--porcelain", "--untracked-files=no"):
            raise ManagerError("Uncommitted source changes found; installation stopped. Use --dev for local development.")
        if git("branch", "--show-current") not in ("", "master"):
            raise ManagerError("Use --dev to reuse a development branch.")
        reference = "refs/tags/" + sys.argv[2]
        git("fetch", "--no-tags", "origin", reference + ":" + reference)
        target = git("rev-parse", "FETCH_HEAD^{commit}")
        if git("rev-list", "--count", target + "..HEAD") != "0":
            raise ManagerError("Local source is ahead of the public release or has diverged; use --dev to keep using it.")
        git("merge", "--ff-only", "--no-overwrite-ignore", target)
except (ManagerError, OSError, subprocess.SubprocessError) as exc:
    sys.exit(str(exc))
PY
fi

if [[ "${CLEAN_LEGACY}" == true ]]; then
    command -v proot-distro >/dev/null 2>&1 || pkg install -y proot-distro
    CLEAN_ARGS=()
    if [[ "${CLEAN_LEGACY_DATA}" == true ]]; then
        CLEAN_ARGS+=(--clear-data)
    fi
    uv run --no-project --no-python-downloads python scripts/termux_legacy.py "${CLEAN_ARGS[@]}"
    exit $?
fi

printf 'Preparing Termux installation: %s\n' "${PROJECT_ROOT}"
MODE=--stable
[[ "${DEVELOPMENT}" == false ]] || MODE=--dev
uv run --no-project --no-python-downloads python scripts/termux_manager.py --install "${MODE}" --project-root "${PROJECT_ROOT}"
printf '\nInstallation complete. Run: aistudio\n'
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) printf 'If the command is not found, run: %s/.local/bin/aistudio\nAdd ~/.local/bin to PATH in your shell configuration.\n' "${HOME}" ;;
esac
