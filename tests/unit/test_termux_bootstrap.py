"""Exercise host repair and downloads without changing system packages."""

from __future__ import annotations

import errno
import hashlib
import os
import pty
import re
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "scripts/install-termux.sh"
COMPLETE_SHA256 = hashlib.sha256(b"complete").hexdigest()


@pytest.fixture
def bootstrap(tmp_path):
    binary = tmp_path / "bin"
    binary.mkdir()
    prefix = tmp_path / "prefix"
    (prefix / "etc/apt/sources.list.d").mkdir(parents=True)
    (prefix / "etc/apt/sources.list").write_text(
        "deb https://packages.termux.dev/apt/termux-main stable main\n"
    )
    (prefix / "etc/apt/sources.list.d/tur.list").write_text(
        "deb https://tur.kcubeterm.com tur-packages tur\n"
    )
    cn_mirrors = prefix / "etc/termux/mirrors/chinese_mainland"
    eu_mirrors = prefix / "etc/termux/mirrors/europe"
    cn_mirrors.mkdir(parents=True)
    eu_mirrors.mkdir(parents=True)
    (prefix / "etc/termux/chosen_mirrors").symlink_to(eu_mirrors)
    scripts = {
        "pkg": """
if test "$1" = --check-mirror && test "$2" = update; then
    echo "pkg:$*" >> "$TEST_ROOT/actions"
    test "${TEST_PKG_FAIL:-0}" != 1 || exit 1
    touch "$TEST_ROOT/mirror-switched"
    exit 0
fi
exit 99
""",
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
if test "${TEST_TUR_FAIL:-0}" = 1 && test "$1" = update; then
    echo 'W: Failed to fetch https://tur.kcubeterm.com/dists/tur-packages/InRelease  Could not connect' >&2
fi
if test "${TEST_REPO_UNREACHABLE:-}" = "$1" && ! test -f "$TEST_ROOT/mirror-switched"; then
    if test "$1" = update; then
        echo 'W: Failed to fetch https://packages.termux.dev/apt/termux-main/dists/stable/InRelease  Unable to connect' >&2
        exit "${TEST_UPDATE_STATUS:-0}"
    fi
    echo 'E: Failed to fetch https://packages.termux.dev/apt/termux-main/pool/main/u/uv/uv.deb  Unable to connect' >&2
    exit 100
fi
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
resumed=0
while test "$#" -gt 0; do
    if test "$1" = -o; then target=$2; shift; fi
    if test "$1" = -C && test "$2" = -; then resumed=1; shift; fi
    shift
done
test "$resumed" != 1 || echo "$count" >> "$TEST_ROOT/resumed"
if test "${TEST_DOWNLOAD:-}" = no-range && test "$resumed" = 1; then
    echo "curl: (33) HTTP server doesn't seem to support byte ranges. Cannot resume." >&2
    exit 33
fi
if test "${TEST_DOWNLOAD:-}" = tls || { test "${TEST_DOWNLOAD:-}" = once && test "$count" = 1; } || { test "${TEST_DOWNLOAD:-}" = no-range && test "$count" = 1; }; then
    test -z "$target" || printf comp > "$target"
    echo 'curl: (56) Recv failure: Software caused connection abort' >&2
    exit 56
fi
if test "${TEST_DOWNLOAD:-}" = missing; then
    echo 'curl: (22) The requested URL returned error: 404' >&2
    exit 22
fi
if test "${TEST_DOWNLOAD:-}" = corrupt; then
    printf corrupted > "$target"
    exit 0
fi
if test "$resumed" = 1; then
    printf lete >> "$target"
else
    printf complete > "$target"
fi
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
        for name in ("PATH", "TMPDIR", "LD_PRELOAD", "LD_LIBRARY_PATH")
        if name in os.environ
    }
    env.update(
        PATH=f"{binary}:{os.environ['PATH']}",
        PREFIX=str(prefix),
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


def test_unreachable_repo_switches_to_chinese_mainland_mirrors_and_retries_step(
    bootstrap,
):
    root, env = bootstrap
    env["TEST_PENDING"] = env["TEST_OLD"] = "1"
    env["TEST_REPO_UNREACHABLE"] = "dist-upgrade"
    code, output = invoke(env, "--prepare-host", answer="y\ny")
    assert code == 0, output
    chosen = Path(env["PREFIX"]) / "etc/termux/chosen_mirrors"
    assert (
        chosen.resolve() == Path(env["PREFIX"]) / "etc/termux/mirrors/chinese_mainland"
    )
    actions = (root / "actions").read_text().splitlines()
    assert "pkg:--check-mirror update" in actions
    assert actions.count("dist-upgrade -y") == 2
    assert (root / "repaired").exists()
    assert "termux-change-repo" in output


@pytest.mark.parametrize(
    ("step", "answer"), [("update", None), ("dist-upgrade", "y\nn")]
)
def test_unreachable_repo_without_mirror_consent_prints_hint_and_stops(
    bootstrap, step, answer
):
    root, env = bootstrap
    env["TEST_PENDING"] = env["TEST_OLD"] = "1"
    env["TEST_REPO_UNREACHABLE"] = step
    code, output = invoke(env, "--prepare-host", answer=answer)
    assert code != 0
    assert "termux-change-repo" in output
    chosen = Path(env["PREFIX"]) / "etc/termux/chosen_mirrors"
    assert chosen.resolve() == Path(env["PREFIX"]) / "etc/termux/mirrors/europe"
    assert not (root / "mirror-switched").exists()
    assert not (root / "repaired").exists()


def test_already_using_chinese_mirrors_does_not_prompt_again(bootstrap):
    root, env = bootstrap
    prefix = Path(env["PREFIX"])
    chosen = prefix / "etc/termux/chosen_mirrors"
    chosen.unlink()
    chosen.symlink_to(prefix / "etc/termux/mirrors/chinese_mainland")
    env["TEST_PENDING"] = env["TEST_OLD"] = "1"
    env["TEST_REPO_UNREACHABLE"] = "dist-upgrade"
    code, output = invoke(env, "--prepare-host", answer="y")
    assert code != 0
    assert "Switch Termux packages to mirrors in Chinese Mainland?" not in output
    assert "termux-change-repo" in output
    assert not (root / "mirror-switched").exists()


def test_third_party_repo_failure_does_not_trigger_mirror_switch(bootstrap):
    root, env = bootstrap
    env["TEST_PENDING"] = env["TEST_OLD"] = "1"
    env["TEST_TUR_FAIL"] = "1"
    code, output = invoke(env, "--prepare-host", answer="y")
    assert code == 0, output
    assert "Switch Termux packages to mirrors in Chinese Mainland?" not in output
    assert not (root / "mirror-switched").exists()
    assert (root / "repaired").exists()


def test_mirror_switch_stops_when_pkg_check_mirror_fails(bootstrap):
    root, env = bootstrap
    env["TEST_PENDING"] = env["TEST_OLD"] = "1"
    env["TEST_REPO_UNREACHABLE"] = "update"
    env["TEST_PKG_FAIL"] = "1"
    code, output = invoke(env, "--prepare-host", answer="y")
    assert code != 0
    assert "Failed to switch Termux mirrors" in output
    assert not (root / "repaired").exists()


def test_mirror_switch_stops_when_curl_is_broken(bootstrap):
    root, env = bootstrap
    env["TEST_BROKEN"] = env["TEST_OLD"] = "1"
    env["TEST_REPO_UNREACHABLE"] = "update"
    code, output = invoke(env, "--prepare-host", answer="y")
    assert code != 0
    assert (
        "Cannot switch Termux mirrors automatically because curl cannot run." in output
    )
    assert not (root / "mirror-switched").exists()
    assert not (root / "repaired").exists()


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
        env,
        "--download",
        "https://example.invalid/browser.tar.gz",
        str(target),
        COMPLETE_SHA256,
    )
    assert (code == 0) == success, output
    assert int((root / "downloads").read_text()) == attempts
    assert target.exists() == success
    if success:
        assert target.read_text() == "complete"
        assert (root / "resumed").read_text().splitlines() == ["2"]
        assert not (root / "download.tar.gz.part").exists()
    elif failure == "tls":
        assert (root / "download.tar.gz.part").read_text() == "comp"


def test_download_reuses_already_verified_part_file_without_network(bootstrap):
    root, env = bootstrap
    target = root / "download.tar.gz"
    part = root / "download.tar.gz.part"
    part.write_text("complete")
    code, output = invoke(
        env,
        "--download",
        "https://example.invalid/browser.tar.gz",
        str(target),
        COMPLETE_SHA256,
    )
    assert code == 0, output
    assert target.read_text() == "complete"
    assert not part.exists()
    assert not (root / "downloads").exists()


def test_download_restarts_from_scratch_when_server_rejects_range(bootstrap):
    root, env = bootstrap
    env["TEST_DOWNLOAD"] = "no-range"
    target = root / "download.tar.gz"
    code, output = invoke(
        env,
        "--download",
        "https://example.invalid/browser.tar.gz",
        str(target),
        COMPLETE_SHA256,
    )
    assert code == 0, output
    assert int((root / "downloads").read_text()) == 3
    assert (root / "resumed").read_text().splitlines() == ["2"]
    assert target.read_text() == "complete"
    assert not (root / "download.tar.gz.part").exists()


def test_download_deletes_part_file_on_sha256_mismatch(bootstrap):
    root, env = bootstrap
    env["TEST_DOWNLOAD"] = "corrupt"
    target = root / "download.tar.gz"
    code, output = invoke(
        env,
        "--download",
        "https://example.invalid/browser.tar.gz",
        str(target),
        COMPLETE_SHA256,
    )
    assert code != 0
    assert "SHA-256 verification" in output
    assert not target.exists()
    assert not (root / "download.tar.gz.part").exists()


def test_download_cannot_replace_an_existing_destination(bootstrap):
    root, env = bootstrap
    target = root / "keep"
    target.write_text("existing file")
    code, _ = invoke(
        env, "--download", "https://example.invalid/file", str(target), COMPLETE_SHA256
    )
    assert code != 0
    assert target.read_text() == "existing file"
    assert not (root / "downloads").exists()


@pytest.mark.parametrize("document", ["README.md", "README_EN.md", "docs/TERMUX.md"])
@pytest.mark.parametrize("failure", [False, True])
def test_documented_entry_does_not_need_curl_or_execute_a_partial_script(
    bootstrap, document, failure
):
    root, env = bootstrap
    prefix = Path(env["PREFIX"])
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
