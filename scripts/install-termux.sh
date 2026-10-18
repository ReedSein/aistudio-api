#!/usr/bin/env bash
# Install the maintained Termux fork; --dir is the parent of aistudio-api/.
set -euo pipefail

REPOSITORY="https://github.com/ReedSein/aistudio-api.git"
INSTALL_PARENT="${HOME}"

usage() {
    cat <<'EOF'
Usage: bash install-termux.sh [--dir PARENT_DIRECTORY]

Default: ~/aistudio-api
Example: --dir "$HOME/projects" installs ~/projects/aistudio-api
Existing installations are reused without overwriting local source changes.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo "--dir requires a parent directory" >&2; exit 2; }
            INSTALL_PARENT="$2"
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

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

if ! command -v git >/dev/null 2>&1; then
    pkg update -y
    pkg install -y git
fi
mkdir -p -- "${INSTALL_PARENT}"
INSTALL_PARENT="$(cd "${INSTALL_PARENT}" && pwd -P)"
PROJECT_ROOT="${INSTALL_PARENT}/aistudio-api"
if [[ -L "${PROJECT_ROOT}" ]]; then
    echo "Refusing to install over a symbolic link: ${PROJECT_ROOT}" >&2
    exit 1
elif [[ ! -e "${PROJECT_ROOT}" ]]; then
    git clone --branch master --single-branch "${REPOSITORY}" "${PROJECT_ROOT}"
elif [[ ! -d "${PROJECT_ROOT}/.git" && ! -f "${PROJECT_ROOT}/.git" ]]; then
    echo "Existing directory is not an installation; nothing was overwritten: ${PROJECT_ROOT}" >&2
    exit 1
fi

cd "${PROJECT_ROOT}"
[[ "$(git rev-parse --show-toplevel)" == "${PROJECT_ROOT}" ]] || { echo "Unexpected repository root." >&2; exit 1; }
REMOTE="$(git remote get-url --all origin)"
[[ "${REMOTE}" == "${REPOSITORY}" || "${REMOTE}" == git@github.com:ReedSein/aistudio-api.git ]] || {
    echo "Existing repository is not ReedSein/aistudio-api; nothing was overwritten." >&2
    exit 1
}
[[ "$(git branch --show-current)" == master && "$(git rev-parse --abbrev-ref '@{upstream}')" == origin/master ]] || {
    echo "Use the master branch tracking origin/master for installation." >&2
    exit 1
}
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "Uncommitted source changes found; installation stopped." >&2; exit 1; }

printf 'Preparing Termux installation: %s\n' "${PROJECT_ROOT}"
bash scripts/setup-env.sh
uv run --no-project --no-python-downloads python scripts/termux_manager.py --install --project-root "${PROJECT_ROOT}"
printf '\nInstallation complete. Run: aistudio\n'
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) printf 'If the command is not found, run: %s/.local/bin/aistudio\nAdd ~/.local/bin to PATH in your shell configuration.\n' "${HOME}" ;;
esac
