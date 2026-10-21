#!/usr/bin/env bash
# Install the maintained Termux fork; --dir is the parent of aistudio-api-termux/.
set -euo pipefail

# Kept in the standalone release asset so first-install failures need no Python.
aistudio_redact_stream() {
    sed -u -E \
        -e 's#(https?://)[^/@[:space:]]+:[^/@[:space:]]+@#\1[REDACTED]@#gI' \
        -e 's/([?&](key|api[_-]?key|token|access_token|auth|password)=)[^&[:space:]]+/\1[REDACTED]/gI' \
        -e 's/("(authorization|proxy-authorization|cookies?|set-cookie|api[_-]?keys?|x-goog-api-key|x-api-key|access_token|refresh_token|token|password|secret|snapshot|botguard)"[[:space:]]*:[[:space:]]*).*/\1"[REDACTED]"/gI' \
        -e 's/((authorization|proxy-authorization|cookie|set-cookie|x-goog-api-key|x-api-key|password|secret|AISTUDIO_WEB_PASSWORD|AISTUDIO_ADMIN_PASSWORD)[[:space:]]*[:=][[:space:]]*).*/\1[REDACTED]/gI' \
        -e 's/(ya29\.[A-Za-z0-9_.-]+|SAPISIDHASH[[:space:]]+[A-Za-z0-9_ -]+)/[REDACTED]/g'
}

aistudio_log_directory() {
    if [[ -n "${AISTUDIO_LOG_DIR:-}" ]]; then
        printf '%s\n' "${AISTUDIO_LOG_DIR}"
    elif [[ -n "${XDG_STATE_HOME:-}" ]]; then
        printf '%s/aistudio-termux/logs\n' "${XDG_STATE_HOME}"
    elif [[ -n "${HOME:-}" ]]; then
        printf '%s/.local/state/aistudio-termux/logs\n' "${HOME}"
    else
        return 1
    fi
}

aistudio_log_prune() {
    local directory="$1" keep="$2" path modified size total=0 oldest oldest_time now days maximum
    [[ -d "${directory}" && ! -L "${directory}" && -f "${directory}/.aistudio-diagnostics-v1" ]] || return 0
    days="${AISTUDIO_LOG_RETENTION_DAYS:-7}"
    maximum=536870912
    if [[ -f "${directory}/.policy" && ! -L "${directory}/.policy" ]]; then
        read -r days maximum < "${directory}/.policy" || return 1
    fi
    days="${AISTUDIO_LOG_RETENTION_DAYS:-${days}}"
    if [[ -n "${AISTUDIO_LOG_MAX_MIB:-}" ]]; then
        [[ "${AISTUDIO_LOG_MAX_MIB}" =~ ^[0-9]{1,5}$ ]] || return 1
        maximum=$((10#${AISTUDIO_LOG_MAX_MIB} * 1048576))
    fi
    [[ "${days}" =~ ^[0-9]{1,4}$ && "${maximum}" =~ ^[0-9]{1,11}$ ]] || return 1
    days=$((10#${days})) maximum=$((10#${maximum}))
    [[ ${days} -ge 1 && ${days} -le 3650 && ${maximum} -ge 1 && ${maximum} -le 34359738368 ]] || return 1
    now=$(date +%s)
    for path in "${directory}"/events-*.jsonl "${directory}"/script-*.log "${directory}"/browser-*.log; do
        [[ -f "${path}" && ! -L "${path}" ]] || continue
        modified=$(stat -c %Y -- "${path}" 2>/dev/null) || continue
        if [[ "${path}" != "${keep}" && $((now - modified)) -gt $((days * 86400)) ]]; then
            size=$(stat -c %s -- "${path}" 2>/dev/null) || continue
            rm -f -- "${path}"
            printf '{"timestamp":"%s","reason":"shell_age","removed_files":1,"removed_bytes":%s}\n' "$(date -u +%FT%TZ)" "${size}" >> "${directory}/.retention.jsonl"
        fi
    done
    while :; do
        total=0 oldest="" oldest_time=${now}
        for path in "${directory}"/events-*.jsonl "${directory}"/script-*.log "${directory}"/browser-*.log; do
            [[ -f "${path}" && ! -L "${path}" ]] || continue
            size=$(stat -c %s -- "${path}" 2>/dev/null) || continue
            total=$((total + size))
            modified=$(stat -c %Y -- "${path}" 2>/dev/null) || continue
            if [[ "${path}" != "${keep}" && ${modified} -le ${oldest_time} ]]; then
                oldest="${path}" oldest_time=${modified}
            fi
        done
        [[ ${total} -gt ${maximum} && -n "${oldest}" ]] || break
        size=$(stat -c %s -- "${oldest}" 2>/dev/null) || continue
        rm -f -- "${oldest}"
        printf '{"timestamp":"%s","reason":"shell_capacity","removed_files":1,"removed_bytes":%s}\n' "$(date -u +%FT%TZ)" "${size}" >> "${directory}/.retention.jsonl"
    done
}

aistudio_log_sink() {
    local directory="$1" label="$2" line file bytes=0
    umask 077
    file=$(mktemp "${directory}/script-${label}-XXXXXXXX.log") || return 1
    aistudio_log_prune "${directory}" "${file}" || printf '[diagnostics] Log rotation failed; policy may not be enforced.\n' >&2
    while IFS= read -r line || [[ -n "${line}" ]]; do
        if ! printf '%(%Y-%m-%dT%H:%M:%S%z)T [%s] %s\n' -1 "${label}" "${line}" >> "${file}"; then
            printf '[diagnostics] Script log write failed; evidence may be missing.\n' >&2
            return 1
        fi
        bytes=$((bytes + ${#line} + 64))
        if [[ ${bytes} -gt 1048576 ]]; then
            file=$(mktemp "${directory}/script-${label}-XXXXXXXX.log") || return 1
            aistudio_log_prune "${directory}" "${file}" || printf '[diagnostics] Log rotation failed; policy may not be enforced.\n' >&2
            bytes=0
        fi
    done
}

aistudio_log_init() {
    local label="$1" directory
    [[ "${AISTUDIO_SHELL_CAPTURE_ACTIVE:-}" != 1 ]] || return 0
    if ! directory=$(aistudio_log_directory); then
        printf '[diagnostics] Cannot resolve a script log directory; evidence may be missing.\n' >&2
        return 0
    fi
    if [[ "${directory}" == / || "${directory}" == "${HOME:-/}" || -L "${directory}" ]] || ! (umask 077; mkdir -p -- "${directory}"); then
        printf '[diagnostics] Cannot create private script log directory; evidence may be missing.\n' >&2
        return 0
    fi
    [[ ! -L "${directory}/.aistudio-diagnostics-v1" && ! -L "${directory}/.retention.jsonl" ]] || {
        printf '[diagnostics] Unsafe diagnostic metadata path; script collection disabled.\n' >&2
        return 0
    }
    if [[ -f "${directory}/.aistudio-diagnostics-v1" ]]; then
        [[ "$(cat "${directory}/.aistudio-diagnostics-v1")" == 'aistudio-api diagnostics v1' ]] || {
            printf '[diagnostics] Unrecognized log ownership marker; collection disabled.\n' >&2
            return 0
        }
    else
        if [[ -e "${directory}/.aistudio-diagnostics-v1" ]] || ! (
            shopt -s nullglob dotglob
            entries=("${directory}"/*)
            [[ ${#entries[@]} -eq 0 ]]
        ); then
            printf '[diagnostics] Refusing an unowned nonempty log directory; collection disabled.\n' >&2
            return 0
        fi
        (umask 077; set -C; printf 'aistudio-api diagnostics v1\n' > "${directory}/.aistudio-diagnostics-v1") || {
            printf '[diagnostics] Cannot mark script log ownership; collection disabled.\n' >&2
            return 0
        }
    fi
    chmod 700 -- "${directory}" || {
        printf '[diagnostics] Cannot protect script log directory; collection disabled.\n' >&2
        return 0
    }
    export AISTUDIO_LOG_DIR="${directory}" AISTUDIO_SHELL_CAPTURE_ACTIVE=1
    # tee mirrors unterminated prompts immediately; only the file branch is line based.
    exec > >(tee >(aistudio_redact_stream | aistudio_log_sink "${directory}" "${label}-stdout"))
    exec 2> >(tee >(aistudio_redact_stream | aistudio_log_sink "${directory}" "${label}-stderr") >&2)
    printf '[diagnostics] Script logs: %s\n' "${directory}" >&2
}

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    if [[ "${1:-}" != --diagnostics-functions ]]; then
        aistudio_log_init "${2:-script}"
    fi
    return 0
fi
if [[ "${1:-}" == --diagnostics-exec ]]; then
    shift
    aistudio_log_init service
    exec "$@"
fi
aistudio_log_init install-termux

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")"
NETWORK_TMP=""
CLONE_STAGE=""
PART_FILE=""
PROGRESS_PID=""
TERMUX_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
CHOSEN_MIRRORS="${TERMUX_PREFIX}/etc/termux/chosen_mirrors"
CN_MIRRORS="${TERMUX_PREFIX}/etc/termux/mirrors/chinese_mainland"
MIRROR_PROMPTED=false

cleanup() {
    local status=$?
    stop_download_progress
    printf '[install] finished exit=%s\n' "${status}" >&2
    [[ -z "${NETWORK_TMP}" ]] || rm -rf -- "${NETWORK_TMP}"
    [[ -z "${CLONE_STAGE}" ]] || rm -rf -- "${CLONE_STAGE}"
    return "${status}"
}
trap cleanup EXIT

network_tmp() {
    [[ -n "${TMPDIR:-}" && -w "${TMPDIR}" ]] || { echo "TMPDIR must be writable." >&2; exit 1; }
    [[ -n "${NETWORK_TMP}" ]] || NETWORK_TMP="$(mktemp -d "${TMPDIR%/}/aistudio-network.XXXXXX")"
}

# Callers pipe this script through log filters, so curl's own progress meter
# never reaches a terminal; report the partial file's growth as plain lines.
start_download_progress() {
    local file="$1" interval="${AISTUDIO_DOWNLOAD_PROGRESS_INTERVAL:-10}"
    [[ "${interval}" =~ ^[1-9][0-9]*$ ]] || interval=10
    (
        local previous=0 current started=${SECONDS} elapsed
        [[ ! -f "${file}" ]] || previous=$(wc -c < "${file}")
        while sleep "${interval}"; do
            [[ -f "${file}" ]] || continue
            current=$(wc -c < "${file}")
            elapsed=$((SECONDS - started))
            ((elapsed > 0)) || elapsed=1
            started=${SECONDS}
            printf '[install] Downloaded %d.%d MiB (%d.%d MiB/s).\n' \
                $((current / 1048576)) $((current % 1048576 * 10 / 1048576)) \
                $(((current - previous) / elapsed / 1048576)) \
                $(((current - previous) / elapsed % 1048576 * 10 / 1048576)) >&2
            previous=${current}
        done
    ) &
    PROGRESS_PID=$!
}

stop_download_progress() {
    [[ -n "${PROGRESS_PID}" ]] || return 0
    kill "${PROGRESS_PID}" 2>/dev/null || true
    wait "${PROGRESS_PID}" 2>/dev/null || true
    PROGRESS_PID=""
}

verify_sha256() {
    local digest _
    read -r digest _ < <(sha256sum < "$1") || return 1
    [[ "${digest}" == "$2" ]]
}

curl_retry() {
    local attempt status output
    local -a resume_args=()
    for attempt in 1 2 3; do
        resume_args=()
        if [[ -n "${PART_FILE:-}" && -s "${PART_FILE}" ]]; then
            resume_args=(-C -)
        fi
        if output=$(LC_ALL=C curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL --connect-timeout 15 "${resume_args[@]}" "$@" 2>"${NETWORK_TMP}/error"); then
            printf '%s' "${output}"
            return 0
        else
            status=$?
        fi
        cat "${NETWORK_TMP}/error" >&2
        case "${status}" in
            5|6|7|18|28|35|52|55|56|92) ;;
            33)
                [[ -n "${PART_FILE:-}" && -e "${PART_FILE}" ]] || return "${status}"
                rm -f -- "${PART_FILE}" ;;
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

termux_repo_urls() {
    local file found=false url
    for file in \
        "${TERMUX_PREFIX}/etc/apt/sources.list" \
        "${TERMUX_PREFIX}/etc/apt/sources.list.d/x11.list" \
        "${TERMUX_PREFIX}/etc/apt/sources.list.d/root.list"; do
        [[ -f "${file}" ]] || continue
        while read -r url; do
            [[ -n "${url}" ]] || continue
            found=true
            printf '%s\n' "${url%/}"
        done < <(grep -E '^[[:space:]]*deb[[:space:]]+' "${file}" | grep -oE 'https?://[^[:space:]]+' || true)
    done
    if [[ "${found}" == false ]]; then
        printf '%s\n' \
            "https://packages.termux.dev/apt/termux-main" \
            "https://packages-cf.termux.dev/apt/termux-main"
    fi
}

apt_fetch_failed() {
    local log_file="$1" url
    while read -r url; do
        if grep -Fq "Failed to fetch ${url}" "${log_file}"; then
            return 0
        fi
    done < <(termux_repo_urls)
    return 1
}

using_chinese_mirrors() {
    local target
    target="$(realpath "${CHOSEN_MIRRORS}" 2>/dev/null || readlink "${CHOSEN_MIRRORS}" 2>/dev/null || true)"
    [[ "${target}" == "${CN_MIRRORS}" || "${target}" == "${CN_MIRRORS}/"* ]]
}

run_apt() {
    network_tmp
    local apt_log="${NETWORK_TMP}/apt.log" status=0 answer
    if LC_ALL=C apt-get "$@" >"${apt_log}" 2>&1; then
        status=0
    else
        status=$?
    fi
    cat "${apt_log}" >&2
    if ! apt_fetch_failed "${apt_log}"; then
        return "${status}"
    fi
    if [[ "${MIRROR_PROMPTED}" == false && -d "${CN_MIRRORS}" && ! -L "${CN_MIRRORS}" && -t 0 ]] && ! using_chinese_mirrors; then
        MIRROR_PROMPTED=true
        printf '\n[install] Cannot reach the current Termux package repository.\n' >&2
        read -r -p 'Switch Termux packages to mirrors in Chinese Mainland? [y/N] ' answer || answer=""
        case "${answer}" in
            y|Y|yes|YES)
                if ! host_tool curl; then
                    echo '[install] Cannot switch Termux mirrors automatically because curl cannot run. Run termux-change-repo or repair curl first.' >&2
                    return 1
                fi
                if [[ -e "${CHOSEN_MIRRORS}" && ! -L "${CHOSEN_MIRRORS}" ]]; then
                    printf '[install] Cannot update mirror link because %s is not a symbolic link.\n' "${CHOSEN_MIRRORS}" >&2
                    return 1
                fi
                mkdir -p -- "$(dirname "${CHOSEN_MIRRORS}")"
                rm -f -- "${CHOSEN_MIRRORS}"
                ln -s -- "${CN_MIRRORS}" "${CHOSEN_MIRRORS}"
                if ! TERMUX_APP_PACKAGE_MANAGER=apt pkg --check-mirror update; then
                    echo '[install] Failed to switch Termux mirrors with pkg --check-mirror update.' >&2
                    return 1
                fi
                echo '[install] Switched Termux package mirrors to Chinese Mainland (run termux-change-repo to change later).' >&2
                run_apt "$@"
                return $? ;;
        esac
    fi
    MIRROR_PROMPTED=true
    echo '[install] Termux package repository is unreachable. Run termux-change-repo (Mirrors in Chinese Mainland) or check your VPN/proxy, then rerun the installer.' >&2
    return "${status}"
}

prepare_host() {
    local force="${1:-false}" healthy=true audit plan plan_ok=true answer package
    local -a broken=()
    host_health >/dev/null 2>&1 || healthy=false
    audit="$(dpkg --audit)"
    if [[ "${force}" == false && "${healthy}" == true && -z "${audit}" ]]; then
        return 0
    fi
    run_apt update
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
            run_apt --fix-broken install -y
            dpkg --configure -a
        fi
    fi
    if [[ "${plan_ok}" == false || -n "${audit}" ]]; then
        run_apt --fix-broken install -y
    fi
    run_apt dist-upgrade -y
    run_apt install -y "${HOST_PACKAGES[@]}"
    for package in "${HOST_PACKAGES[@]}"; do
        host_tool "${package}" || broken+=("${package}")
    done
    if [[ ${#broken[@]} -gt 0 ]]; then
        printf '[install] Reinstalling packages that still cannot run: %s\n' "${broken[*]}" >&2
        run_apt install --reinstall -y "${broken[@]}"
    fi
    host_health || return 1
    audit="$(dpkg --audit)"
    [[ -z "${audit}" ]] || { printf '%s\n' "${audit}" >&2; return 1; }
}

# These entry points share preparation and transfer behavior with setup scripts.
case "${1:-}" in
    --prepare-host) require_termux; prepare_host; exit $? ;;
    --download)
        [[ $# == 4 && "$2" == https://* && ! -e "$3" && ! -L "$3" && "$4" =~ ^[0-9a-f]{64}$ ]] || {
            echo 'Expected HTTPS URL, a new download destination, and a 64-character lowercase SHA-256.' >&2
            exit 2
        }
        PART_FILE="$3.part"
        [[ ! -L "${PART_FILE}" && ( ! -e "${PART_FILE}" || -f "${PART_FILE}" ) ]] || {
            echo "Refusing to use non-regular partial download file: ${PART_FILE}" >&2
            exit 1
        }
        mkdir -p -- "$(dirname "$3")"
        if [[ -f "${PART_FILE}" ]] && verify_sha256 "${PART_FILE}" "$4"; then
            mv -- "${PART_FILE}" "$3"
            exit 0
        fi
        network_tmp
        start_download_progress "${PART_FILE}"
        curl_retry --max-time 1800 --speed-limit 1024 --speed-time 60 -o "${PART_FILE}" "$2"
        stop_download_progress
        if ! verify_sha256 "${PART_FILE}" "$4"; then
            rm -f -- "${PART_FILE}"
            echo "Downloaded file failed SHA-256 verification: $3" >&2
            exit 1
        fi
        mv -- "${PART_FILE}" "$3"
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
