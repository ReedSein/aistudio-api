#!/usr/bin/env bash
# Install the maintained Termux fork; --dir is the parent of aistudio-api-termux/.
set -euo pipefail

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

command -v pkg >/dev/null 2>&1 || { echo "This installer requires Termux." >&2; exit 1; }
[[ "$(uname -m)" == aarch64 ]] || { echo "This installer supports ARM64 Termux." >&2; exit 1; }
[[ -n "${TMPDIR:-}" && -w "${TMPDIR}" ]] || { echo "TMPDIR must be writable." >&2; exit 1; }
case "${INSTALL_PARENT}" in
    '~') INSTALL_PARENT="${HOME}" ;;
    '~/'*) INSTALL_PARENT="${HOME}/${INSTALL_PARENT#\~/}" ;;
esac
if [[ "${INSTALL_PARENT}" == *:* || "${INSTALL_PARENT}" == *$'\n'* ]]; then
    echo "Installation paths cannot contain ':' or a newline (proot bind syntax)." >&2
    exit 2
fi

MISSING=()
for TOOL in git curl uv python; do
    command -v "${TOOL}" >/dev/null 2>&1 || MISSING+=("${TOOL}")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    pkg update -y
    pkg install -y "${MISSING[@]}"
fi
mkdir -p -- "${INSTALL_PARENT}"
INSTALL_PARENT="$(cd "${INSTALL_PARENT}" && pwd -P)"
PROJECT_ROOT="${INSTALL_PARENT}/aistudio-api-termux"
if [[ -L "${PROJECT_ROOT}" ]]; then
    echo "Refusing to install over a symbolic link: ${PROJECT_ROOT}" >&2
    exit 1
fi

REFERENCE=master
if [[ "${DEVELOPMENT}" == false ]]; then
    RELEASE_URL="$(curl -fsSL --connect-timeout 15 --max-time 60 -o /dev/null -w '%{url_effective}' https://github.com/ReedSein/aistudio-api-termux/releases/latest)" || {
        echo "Cannot obtain a public release; no source was changed. The maintainer must publish a release first." >&2
        exit 1
    }
    REFERENCE="${RELEASE_URL#https://github.com/ReedSein/aistudio-api-termux/releases/tag/}"
    [[ "${REFERENCE}" =~ ^termux-v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
        echo "Public releases must use a termux-vX.Y.Z tag; no source was changed." >&2
        exit 1
    }
fi

if [[ ! -e "${PROJECT_ROOT}" ]]; then
    git clone --branch "${REFERENCE}" --single-branch "${REPOSITORY}" "${PROJECT_ROOT}"
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
        git -C "${PROJECT_ROOT}" fetch --no-tags origin master
        git -C "${PROJECT_ROOT}" checkout --no-overwrite-ignore -B master FETCH_HEAD
        git -C "${PROJECT_ROOT}" branch --set-upstream-to=origin/master master
    else
        git -C "${PROJECT_ROOT}" fetch --no-tags origin "refs/tags/${REFERENCE}:refs/tags/${REFERENCE}"
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
    uv run --no-project --no-python-downloads python - "${PROJECT_ROOT}" "${REFERENCE}" <<'PY'
import subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from termux_runtime import Installation, ManagerError, Service, operation_lock

def git(*args):
    return subprocess.run(["git", *args], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()

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
bash scripts/setup-env.sh
MODE=--stable
[[ "${DEVELOPMENT}" == false ]] || MODE=--dev
uv run --no-project --no-python-downloads python scripts/termux_manager.py --install "${MODE}" --project-root "${PROJECT_ROOT}"
printf '\nInstallation complete. Run: aistudio\n'
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) printf 'If the command is not found, run: %s/.local/bin/aistudio\nAdd ~/.local/bin to PATH in your shell configuration.\n' "${HOME}" ;;
esac
