"""Exercise host repair and downloads without changing system packages."""

from __future__ import annotations

import errno
import os
import pty
import re
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "scripts/install-termux.sh"


@pytest.fixture
def bootstrap(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    scripts = {
        "pkg": "exit 99",
        "uname": "printf 'aarch64\\n'",
        "dpkg-query": "printf installed",
        "dpkg": """
case "$1" in
    --audit) test "${TEST_PENDING:-0}" != 1 || test -f "$TEST_ROOT/configured" || echo 'package unpacked but not configured' ;;
    --configure)
        echo configure >> "$TEST_ROOT/actions"
        if test "${TEST_CONFIG_FAIL:-0}" = 1 && ! test -f "$TEST_ROOT/dependencies-fixed"; then
            echo 'dependency configuration failed' >&2
            exit 1
        fi
        touch "$TEST_ROOT/configured" ;;
esac
""",
        "apt-get": """
printf '%s\\n' "$*" >> "$TEST_ROOT/actions"
test "${TEST_APT_FAIL:-}" != "$1" || exit 100
case "$1" in
    --simulate) test "${TEST_OLD:-0}" != 1 || test -f "$TEST_ROOT/repaired" || echo 'Inst libngtcp2 [old] (new)' ;;
    dist-upgrade) touch "$TEST_ROOT/repaired" ;;
    --fix-broken) touch "$TEST_ROOT/dependencies-fixed" ;;
esac
""",
        "curl": """
if test "$1" = --version; then
    if test "${TEST_BROKEN:-0}" = 1 && { test "${TEST_STILL_BROKEN:-0}" = 1 || ! test -f "$TEST_ROOT/repaired"; }; then
        echo 'CANNOT LINK EXECUTABLE "curl": cannot locate symbol "ngtcp2_conn_get_tls_early_data_rejected2"' >&2
        exit 127
    fi
    exit 0
fi
count=0
test ! -f "$TEST_ROOT/downloads" || count=$(cat "$TEST_ROOT/downloads")
count=$((count + 1))
echo "$count" > "$TEST_ROOT/downloads"
target=
while test "$#" -gt 0; do
    if test "$1" = -o; then target=$2; shift; fi
    shift
done
if test "${TEST_DOWNLOAD:-}" = tls || { test "${TEST_DOWNLOAD:-}" = once && test "$count" = 1; }; then
    test -z "$target" || printf partial > "$target"
    echo 'curl: (35) unexpected eof while reading' >&2
    exit 35
fi
if test "${TEST_DOWNLOAD:-}" = missing; then
    echo 'curl: (22) The requested URL returned error: 404' >&2
    exit 22
fi
printf complete > "$target"
""",
        "sleep": "exit 0",
    }
    for name in ("git", "uv", "python", "proot-distro", "gum", "clang", "pkg-config"):
        scripts[name] = "exit 0"
    for name, body in scripts.items():
        script = binary / name
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(0o755)
    env = {
        name: os.environ[name]
        for name in ("PATH", "TMPDIR", "PREFIX", "LD_PRELOAD", "LD_LIBRARY_PATH")
        if name in os.environ
    }
    env.update(
        PATH=f"{binary}:{os.environ['PATH']}",
        TEST_ROOT=str(tmp_path),
        HOME=str(tmp_path / "home"),
    )
    return tmp_path, env


def invoke(env, *arguments, answer=None):
    command = ["bash", str(INSTALLER), *arguments]
    if answer is None:
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=10, check=False
        )
        return result.returncode, result.stdout + result.stderr
    master, slave = pty.openpty()
    process = None
    try:
        process = subprocess.Popen(
            command, env=env, stdin=slave, stdout=slave, stderr=slave
        )
        os.close(slave)
        slave = -1
        os.write(master, (answer + "\n").encode())
        process.wait(timeout=10)
        output = bytearray()
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            output.extend(chunk)
        return process.returncode, output.decode(errors="replace")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        if slave != -1:
            os.close(slave)


def test_broken_curl_is_repaired_before_it_is_needed(bootstrap):
    root, env = bootstrap
    env["TEST_BROKEN"] = env["TEST_OLD"] = "1"
    code, output = invoke(env, "--prepare-host", answer="y")
    assert code == 0, output
    assert (root / "repaired").exists()
    assert "dist-upgrade" in (root / "actions").read_text()


@pytest.mark.parametrize("answer", [None, "n"])
def test_host_repair_needs_interactive_consent(bootstrap, answer):
    root, env = bootstrap
    env["TEST_BROKEN"] = env["TEST_OLD"] = "1"
    code, _ = invoke(env, "--prepare-host", answer=answer)
    assert code != 0
    assert not (root / "repaired").exists()
    assert not (root / "configured").exists()


def test_interrupted_package_configuration_is_completed(bootstrap):
    root, env = bootstrap
    env["TEST_PENDING"] = "1"
    code, output = invoke(env, "--prepare-host", answer="y")
    assert code == 0, output
    assert (root / "configured").exists()


def test_failed_upgrade_does_not_report_success(bootstrap):
    root, env = bootstrap
    env["TEST_BROKEN"] = env["TEST_OLD"] = "1"
    env["TEST_APT_FAIL"] = "dist-upgrade"
    code, _ = invoke(env, "--prepare-host", answer="y")
    assert code != 0
    assert not (root / "repaired").exists()


def test_failed_configuration_is_repaired_before_upgrade(bootstrap):
    root, env = bootstrap
    env["TEST_PENDING"] = env["TEST_CONFIG_FAIL"] = "1"
    code, output = invoke(env, "--prepare-host", answer="y")
    assert code == 0, output
    assert (root / "configured").exists()
    assert (root / "dependencies-fixed").exists()


def test_successful_package_commands_do_not_hide_a_still_broken_tool(bootstrap):
    root, env = bootstrap
    env["TEST_BROKEN"] = env["TEST_STILL_BROKEN"] = "1"
    code, output = invoke(env, "--prepare-host", answer="y")
    assert code != 0
    assert "cannot locate symbol" in output
    assert "--reinstall" in (root / "actions").read_text()


@pytest.mark.parametrize(
    ("failure", "attempts", "success"),
    [("once", 2, True), ("tls", 3, False), ("missing", 1, False)],
)
def test_download_retries_transport_errors_and_never_publishes_partial_files(
    bootstrap, failure, attempts, success
):
    root, env = bootstrap
    env["TEST_DOWNLOAD"] = failure
    target = root / "download.tar.gz"
    code, output = invoke(
        env, "--download", "https://example.invalid/browser.tar.gz", str(target)
    )
    assert (code == 0) == success, output
    assert int((root / "downloads").read_text()) == attempts
    assert target.exists() == success
    if success:
        assert target.read_text() == "complete"


def test_download_cannot_replace_an_existing_destination(bootstrap):
    root, env = bootstrap
    target = root / "keep"
    target.write_text("existing file")
    code, _ = invoke(env, "--download", "https://example.invalid/file", str(target))
    assert code != 0
    assert target.read_text() == "existing file"
    assert not (root / "downloads").exists()


@pytest.mark.parametrize("document", ["README.md", "README_EN.md", "docs/TERMUX.md"])
@pytest.mark.parametrize("failure", [False, True])
def test_documented_entry_does_not_need_curl_or_execute_a_partial_script(
    bootstrap, document, failure
):
    root, env = bootstrap
    prefix = root / "prefix"
    helper = prefix / "lib/apt/apt-helper"
    helper.parent.mkdir(parents=True)
    helper.write_text(
        '#!/bin/sh\nfor target do :; done\nprintf \'%s\\n\' \'printf done > "$TEST_ROOT/bootstrap-ran"\' > "$target"\ntest "${TEST_DOWNLOAD:-}" != bootstrap-fail\n'
    )
    helper.chmod(0o755)
    temporary = root / "temporary"
    temporary.mkdir()
    env.update(
        PREFIX=str(prefix),
        TMPDIR=str(temporary),
        TEST_BROKEN="1",
        TEST_DOWNLOAD="bootstrap-fail" if failure else "",
    )
    content = (INSTALLER.parents[1] / document).read_text()
    match = re.search(r"```bash\n(.*?)```", content, re.S)
    assert match is not None
    result = subprocess.run(
        ["bash", "-c", match[1]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert (result.returncode == 0) != failure, result.stderr
    assert (root / "bootstrap-ran").exists() != failure
    assert not list(temporary.iterdir())
