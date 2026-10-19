#!/usr/bin/env bash
# Export without uv, application dependencies or a running HTTP server.
set -euo pipefail
DIAGNOSTICS_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
if command -v python >/dev/null 2>&1 && python -c 'import sys; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
    exec python "${DIAGNOSTICS_ROOT}/scripts/termux_diagnostics.py" "$@"
fi

source "${DIAGNOSTICS_ROOT}/scripts/install-termux.sh" --diagnostics-functions
output="${HOME}/aistudio-diagnostics-$(date -u +%Y%m%dT%H%M%S)-$$.tar.gz"
case "${1:-}" in
    --output) [[ $# == 2 ]] || exit 2; output="$2" ;;
    '') ;;
    *) printf 'Usage: bash scripts/export-diagnostics.sh [--output FILE]\n' >&2; exit 2 ;;
esac
[[ -n "${TMPDIR:-}" && -w "${TMPDIR}" ]] || { echo 'TMPDIR must be writable.' >&2; exit 1; }
[[ ! -e "${output}" && ! -L "${output}" ]] || { echo 'Output already exists; nothing overwritten.' >&2; exit 1; }
umask 077
diagnostic_stage=$(mktemp -d "${TMPDIR%/}/aistudio-export.XXXXXX")
trap 'rm -rf -- "$diagnostic_stage"' EXIT
mkdir "${diagnostic_stage}/logs"
printf '%s\n' 'Basic support archive: system Python could not run.' \
    'Only sanitized retained script/application logs are included.' \
    'Dependency, runtime configuration and process probes are unavailable.' \
    'This archive contains conversation content. Send only to a trusted maintainer.' > "${diagnostic_stage}/README.txt"
directory=$(aistudio_log_directory)
[[ ! -L "${directory}" ]] || { echo 'Refusing a symlink log directory.' >&2; exit 1; }
[[ -f "${directory}/.aistudio-diagnostics-v1" && ! -L "${directory}/.aistudio-diagnostics-v1" && "$(cat "${directory}/.aistudio-diagnostics-v1")" == 'aistudio-api diagnostics v1' ]] || {
    echo 'Cannot confirm ownership of the diagnostic log directory.' >&2
    exit 1
}
printf 'schema=basic-shell-1\ncreated_at=%s\nmissing=python_environment_and_runtime_probes\n' "$(date -u +%FT%TZ)" > "${diagnostic_stage}/manifest.txt"
for path in "${directory}"/events-*.jsonl "${directory}"/script-*.log "${directory}"/browser-*.log; do
    [[ -f "${path}" && ! -L "${path}" ]] || continue
    name="${path##*/}"
    [[ "${name}" =~ ^(events|script|browser)-[A-Za-z0-9_.-]+\.(log|jsonl)$ ]] || continue
    aistudio_redact_stream < "${path}" > "${diagnostic_stage}/logs/${name}.txt"
    (cd "${diagnostic_stage}" && sha256sum "logs/${name}.txt") >> "${diagnostic_stage}/manifest.txt"
done
set -C
exec {archive_fd}> "${output}"
set +C
tar -czf - -C "${diagnostic_stage}" README.txt manifest.txt logs >&"${archive_fd}"
exec {archive_fd}>&-
printf '基础诊断包已保存：%s\n系统 Python 无法运行，包内已标注未能采集的环境信息。\n' "${output}"
