"""domovoi-apply-payload — the root helper behind the plugin-payload
sudoers line (SAT-2).

The request file the satellite account writes names WHICH slugs to apply;
every path the helper writes to or executes from is fixed in the helper
itself. Run for real under the shell with the privileged commands stubbed
(apt-get, dpkg, getent) and the fixed root paths pointed into tmp, so the
same assertions can be replayed unmodified on the container satellite
where the paths are real.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "domovoi-apply-payload"

# The paths the helper owns. A request file cannot move any of them.
FIXED_PATHS = {
    "STATE_DIR": "/var/lib/domovoi",
    "STATE_FILE": "/var/lib/domovoi/plugin_payload_state.json",
    "STAGING": "/var/lib/domovoi/payload-staging",
    "LOGFILE": "/var/log/domovoi-payload-apply.log",
    "DEB_CACHE": "/opt/domovoi-payload/debs",
}


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


# ─── static: the paths are the helper's, not the request's ───────────────


def test_every_path_the_helper_uses_is_fixed_in_the_helper():
    text = HELPER.read_text(encoding="utf-8")
    for name, value in FIXED_PATHS.items():
        assert f"{name}={value}\n" in text, f"{name} is not fixed to {value}"
    # Root-owned, all of them.
    for value in FIXED_PATHS.values():
        assert value.startswith(("/var/", "/opt/"))


def test_the_request_file_is_read_for_slugs_only():
    """The keys an older request carried for the helper's paths are not
    consulted: they appear nowhere in the code, only in the comment that
    says so."""
    code = "\n".join(_code_lines(HELPER.read_text(encoding="utf-8")))
    assert "payloads_root" not in code
    assert "state_file" not in code
    assert 'doc.get("slugs")' in code


def test_the_post_install_runs_from_the_staged_copy_not_the_mirror():
    code = "\n".join(_code_lines(HELPER.read_text(encoding="utf-8")))
    # The RUN line python emits carries the STAGED path.
    assert 'dest_root = posixpath.join(staging, slug)' in code
    assert 'script = posixpath.join(dest_root, post)' in code
    assert 'print(f"RUN\\t{slug}\\t{script}")' in code
    # ...and nothing in the shell reaches back into the mirror to execute.
    for line in code.splitlines():
        if "$MIRROR" in line:
            assert "python3" in line or '"$PY"' in line or "MIRROR=" in line, line


def test_the_staging_directory_is_fresh_and_private_per_run():
    code = "\n".join(_code_lines(HELPER.read_text(encoding="utf-8")))
    assert 'rm -rf "$STAGING"\nmkdir "$STAGING" || exit 1\nchmod 0700 "$STAGING"' in code


def test_symlinks_in_the_mirror_are_never_followed():
    code = HELPER.read_text(encoding="utf-8")
    assert "O_NOFOLLOW" in code
    assert "os.path.islink(src_root)" in code
    assert "not os.path.islink(os.path.join(dirpath, d))" in code


# ─── dynamic: run it ──────────────────────────────────────────────────────


def _bash() -> str:
    b = shutil.which("bash")
    if b is None:
        pytest.skip("no bash to run the helper under")
    return b


def _shell_path(p: Path) -> str:
    """The path as the shell sees it. Under Git Bash a drive letter carries
    a colon, which the helper's `cut -d:` on the passwd line cannot
    survive, so the stub hands it the /c/... spelling instead."""
    s = p.as_posix()
    if len(s) > 1 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]
    return s


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    p.chmod(0o755)


@pytest.fixture
def device(tmp_path):
    """A pretend satellite: a home with a mirror, stub privileged commands,
    and a copy of the helper whose fixed paths point into tmp."""
    home = tmp_path / "home"
    (home / ".domovoi" / "plugin_payloads").mkdir(parents=True)
    rootfs = tmp_path / "rootfs"
    (rootfs / "var" / "lib").mkdir(parents=True)
    (rootfs / "var" / "log").mkdir(parents=True)

    fixed = {
        "STATE_DIR": rootfs / "var" / "lib" / "domovoi",
        "STATE_FILE": rootfs / "var" / "lib" / "domovoi" / "plugin_payload_state.json",
        "STAGING": rootfs / "var" / "lib" / "domovoi" / "payload-staging",
        "LOGFILE": rootfs / "var" / "log" / "domovoi-payload-apply.log",
        "DEB_CACHE": rootfs / "opt" / "domovoi-payload" / "debs",
    }
    text = HELPER.read_text(encoding="utf-8")
    for name, value in FIXED_PATHS.items():
        text = text.replace(f"{name}={value}\n", f"{name}={fixed[name].as_posix()}\n", 1)
    helper = tmp_path / "domovoi-apply-payload"
    helper.write_text(text, encoding="utf-8", newline="\n")
    helper.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _stub(bin_dir, "getent", f'printf \'%s\\n\' "domovoi:x:1000:1000::{_shell_path(home)}:/bin/bash"\n')
    _stub(bin_dir, "apt-get", f'printf \'apt-get %s\\n\' "$*" >>"{calls.as_posix()}"\n')
    _stub(bin_dir, "dpkg", f'printf \'dpkg %s\\n\' "$*" >>"{calls.as_posix()}"\n')
    _stub(bin_dir, "python3", f'exec "{Path(sys.executable).as_posix()}" "$@"\n')

    def run(env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = bin_dir.as_posix() + os.pathsep + env.get("PATH", "")
        env["SUDO_USER"] = "domovoi"
        env.pop("DOMOVOI_APPLY_UNSANDBOXED", None)
        env.update(env_extra or {})
        return subprocess.run(
            [_bash(), helper.as_posix()], capture_output=True, text=True,
            env=env, timeout=120,
        )

    return {
        "home": home, "mirror": home / ".domovoi" / "plugin_payloads",
        "pending": home / ".domovoi" / "pending_payload.json",
        "fixed": fixed, "calls": calls, "run": run, "tmp": tmp_path,
    }


def _marker_script(marker: Path) -> str:
    """A post-install that records where it ran from and as what."""
    return (
        "#!/bin/sh\n"
        f'printf \'%s\\n%s\\n%s\\n\' "$DOMOVOI_PLUGIN_SLUG" "$DOMOVOI_PLUGIN_DIR" "$0" '
        f'>"{marker.as_posix()}"\n'
    )


def test_the_helper_executes_only_from_its_own_staging_path(device):
    """A request that names another directory for the files and another
    file for the state changes nothing: the script that runs is the staged
    copy of the mirror's, and the state lands in the helper's own file."""
    mirror = device["mirror"]
    (mirror / "radio").mkdir()
    ran_from_mirror = device["tmp"] / "ran-mirror"
    (mirror / "radio" / "post.sh").write_text(
        _marker_script(ran_from_mirror), encoding="utf-8", newline="\n")
    (mirror / "radio" / "stations.json").write_text("{}", encoding="utf-8")

    elsewhere = device["tmp"] / "elsewhere"
    (elsewhere / "radio").mkdir(parents=True)
    ran_from_elsewhere = device["tmp"] / "ran-elsewhere"
    (elsewhere / "radio" / "post.sh").write_text(
        _marker_script(ran_from_elsewhere), encoding="utf-8", newline="\n")
    other_state = device["tmp"] / "other-state.json"

    device["pending"].write_text(json.dumps({
        "slugs": {"radio": {"apt_packages": ["libfoo2"], "post_install": "post.sh",
                            "version": "1.0.0"}},
        "payloads_root": elsewhere.as_posix(),
        "state_file": other_state.as_posix(),
    }), encoding="utf-8")

    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (proc.stdout, proc.stderr, log)

    # The mirror's script ran - from the staging copy, not from the mirror.
    assert ran_from_mirror.is_file(), log
    slug, plugin_dir, argv0 = ran_from_mirror.read_text(encoding="utf-8").splitlines()
    assert slug == "radio"
    staging = device["fixed"]["STAGING"].as_posix()
    assert plugin_dir.replace("\\", "/").rstrip("/") == f"{staging}/radio"
    assert argv0.replace("\\", "/").startswith(staging)
    assert mirror.as_posix() not in plugin_dir.replace("\\", "/")
    # The other directory's script did not run, and the other state file
    # was never written.
    assert not ran_from_elsewhere.exists()
    assert not other_state.exists()
    # State went to the helper's own file...
    state = json.loads(device["fixed"]["STATE_FILE"].read_text(encoding="utf-8"))
    assert state["radio"]["apt_packages"] == ["libfoo2"]
    assert state["radio"]["version"] == "1.0.0"
    assert len(state["radio"]["post_install_sha"]) == 64
    # ...the packages went through apt, the request was consumed, and the
    # staging tree did not outlive the run.
    assert "apt-get install -y libfoo2" in device["calls"].read_text(encoding="utf-8")
    assert not device["pending"].exists()
    assert not device["fixed"]["STAGING"].exists()


def test_names_that_fail_their_pattern_are_skipped_not_run(device):
    mirror = device["mirror"]
    (mirror / "ok").mkdir()
    ran = device["tmp"] / "ran-ok"
    (mirror / "ok" / "post.sh").write_text(_marker_script(ran), encoding="utf-8", newline="\n")
    device["pending"].write_text(json.dumps({
        "slugs": {
            "ok": {"apt_packages": ["libfoo2", "bad name; touch x", "-flag"],
                   "post_install": "post.sh"},
            "../escape": {"apt_packages": ["libbar"], "post_install": "post.sh"},
            "Bad Slug": {"apt_packages": [], "post_install": "post.sh"},
            "dots": {"apt_packages": [], "post_install": "../post.sh"},
        },
    }), encoding="utf-8")
    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (proc.stdout, proc.stderr, log)
    assert ran.is_file()
    calls = device["calls"].read_text(encoding="utf-8")
    assert "apt-get install -y libfoo2\n" in calls
    assert "bad name" not in calls and "-flag" not in calls and "libbar" not in calls
    assert "skipped:" in log
    state = json.loads(device["fixed"]["STATE_FILE"].read_text(encoding="utf-8"))
    assert set(state) == {"ok", "dots"}
    assert state["dots"]["post_install_sha"] is None


def test_a_missing_script_fails_the_run_and_keeps_the_request(device):
    (device["mirror"] / "radio").mkdir()
    device["pending"].write_text(json.dumps({
        "slugs": {"radio": {"apt_packages": [], "post_install": "post.sh"}},
    }), encoding="utf-8")
    proc = device["run"]()
    assert proc.returncode == 1
    assert device["pending"].exists()
    assert not device["fixed"]["STATE_FILE"].exists()
    assert not device["fixed"]["STAGING"].exists()


def test_a_symlinked_script_is_not_staged(device):
    mirror = device["mirror"]
    (mirror / "radio").mkdir()
    real = device["tmp"] / "outside.sh"
    ran = device["tmp"] / "ran-outside"
    real.write_text(_marker_script(ran), encoding="utf-8", newline="\n")
    try:
        os.symlink(real, mirror / "radio" / "post.sh")
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    device["pending"].write_text(json.dumps({
        "slugs": {"radio": {"apt_packages": [], "post_install": "post.sh"}},
    }), encoding="utf-8")
    proc = device["run"]()
    assert proc.returncode == 1
    assert not ran.exists()


def test_the_log_is_world_readable_and_in_a_root_path(device):
    device["pending"].write_text(json.dumps({"slugs": {}}), encoding="utf-8")
    proc = device["run"]()
    assert proc.returncode == 0, proc.stderr
    logfile = device["fixed"]["LOGFILE"]
    assert logfile.is_file()
    text = logfile.read_text(encoding="utf-8", errors="replace")
    assert "apply-payload start (invoker=domovoi)" in text
    assert "apply-payload done" in text
    # The helper creates it 0644 so the client can read its own report.
    assert re.search(r'chmod 0644 "\$LOGFILE"', HELPER.read_text(encoding="utf-8"))


def test_no_request_is_a_quiet_success(device):
    proc = device["run"]()
    assert proc.returncode == 0
    assert "no pending payload" in proc.stderr
