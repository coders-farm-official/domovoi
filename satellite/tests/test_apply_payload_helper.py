"""domovoi-apply-payload — the root helper behind the plugin-payload
sudoers line (SAT-2, and A5-01's second half).

The request file the satellite account writes names WHICH slugs to apply;
every path the helper writes to or executes from is fixed in the helper
itself. On a device with the root-owned server pin the helper also
re-verifies the signed payload manifest the sync saved beside its mirror —
with the root-owned verifier, against that pin — and takes each slug's
declared work and file hashes from the SIGNED list, not the request. Run
for real under the shell with the privileged commands stubbed (apt-get,
dpkg, getent) and the fixed root paths pointed into tmp, so the same
assertions can be replayed unmodified on the container satellite where the
paths are real.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from satellite import _ed25519, server_identity

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "domovoi-apply-payload"
VERIFIER_SOURCE = Path(__file__).resolve().parents[1] / "_ed25519.py"

# The paths the helper owns. A request file cannot move any of them.
FIXED_PATHS = {
    "STATE_DIR": "/var/lib/domovoi",
    "STATE_FILE": "/var/lib/domovoi/plugin_payload_state.json",
    "SERIAL_FILE": "/var/lib/domovoi/plugin_payload_serial.json",
    "STAGING": "/var/lib/domovoi/payload-staging",
    "LOGFILE": "/var/log/domovoi-payload-apply.log",
    "DEB_CACHE": "/opt/domovoi-payload/debs",
    "ROOT_PIN": "/etc/domovoi/server-identity.json",
    "VERIFIER": "/usr/local/lib/domovoi/domovoi_ed25519.py",
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
        assert value.startswith(("/var/", "/opt/", "/etc/", "/usr/local/"))


def test_the_verifier_and_the_pin_are_roots_not_the_accounts():
    """The helper runs as root on the account's request: it must import
    nothing from, and trust nothing in, that account's home."""
    code = "\n".join(_code_lines(HELPER.read_text(encoding="utf-8")))
    assert "from satellite" not in code and "import satellite" not in code
    assert 'VERIFIER=/usr/local/lib/domovoi/domovoi_ed25519.py' in code
    assert 'ROOT_PIN=/etc/domovoi/server-identity.json' in code
    # The envelope is the one thing read from the mirror, and it is checked
    # against root's pin before anything in it is believed.
    assert 'envelope_path = os.path.join(mirror, ".manifest.sig")' in code


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
        "SERIAL_FILE": rootfs / "var" / "lib" / "domovoi" / "plugin_payload_serial.json",
        "STAGING": rootfs / "var" / "lib" / "domovoi" / "payload-staging",
        "LOGFILE": rootfs / "var" / "log" / "domovoi-payload-apply.log",
        "DEB_CACHE": rootfs / "opt" / "domovoi-payload" / "debs",
        "ROOT_PIN": rootfs / "etc" / "domovoi" / "server-identity.json",
        "VERIFIER": rootfs / "usr" / "local" / "lib" / "domovoi" / "domovoi_ed25519.py",
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


def test_an_offline_request_installs_from_the_local_caches_only(device):
    """Under the server's INTERNET_ACCESS=never the request marks a slug
    offline: apt runs with --no-download (cache only), and a package that
    would need a download fails the run and keeps the request for later."""
    (device["mirror"] / "radio").mkdir()
    device["pending"].write_text(json.dumps({
        "slugs": {"radio": {"apt_packages": ["libfoo2"], "post_install": None,
                            "version": "1.0.0", "offline": True}},
    }), encoding="utf-8")
    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (proc.stdout, proc.stderr, log)
    calls = device["calls"].read_text(encoding="utf-8")
    assert "apt-get install -y --no-download libfoo2" in calls
    assert "local package caches only" in log


def test_without_the_flag_apt_runs_as_before(device):
    (device["mirror"] / "radio").mkdir()
    device["pending"].write_text(json.dumps({
        "slugs": {"radio": {"apt_packages": ["libfoo2"], "post_install": None, "version": "1.0.0"}},
    }), encoding="utf-8")
    proc = device["run"]()
    assert proc.returncode == 0, proc.stderr
    calls = device["calls"].read_text(encoding="utf-8")
    assert "apt-get install -y libfoo2" in calls and "--no-download" not in calls


def test_a_unit_with_no_root_pin_says_it_is_not_checking(device):
    """The compatibility promise, said out loud in the log: a hand-built
    unit or a card from before server identities has nothing to verify
    against and keeps the request-driven behaviour."""
    device["pending"].write_text(json.dumps({"slugs": {}}), encoding="utf-8")
    assert device["run"]().returncode == 0
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert "payload authenticity is not checked" in log


# ─── pinned: root believes the signed list, not the request ──────────────
#
# A5-01. The account that writes the request also used to be able to
# choose the server whose payloads got mirrored (config.toml's fingerprint
# beat the root pin), and this helper ran whatever that mirror held. Now the
# helper verifies the envelope the sync saved, with root's own verifier
# against root's own pin, and the request is only a list of slugs.


def _keypair(seed_byte: int = 1):
    seed = bytes([seed_byte]) * 32
    public = _ed25519.public_key(seed)
    return seed, public, server_identity.fingerprint_for(public)


def _pin(device, public) -> None:
    """Make the pretend satellite a prepared card: the root pin names the
    key, and the root-owned verifier is installed."""
    pin = device["fixed"]["ROOT_PIN"]
    pin.parent.mkdir(parents=True, exist_ok=True)
    pin.write_text(json.dumps({
        "algorithm": "ed25519",
        "fingerprint": server_identity.fingerprint_for(public),
        "public_key": base64.b64encode(public).decode("ascii"),
    }), encoding="utf-8")
    verifier = device["fixed"]["VERIFIER"]
    verifier.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(VERIFIER_SOURCE, verifier)


def _envelope(seed, public, files: dict[str, bytes], meta: dict, *, serial: int = 5,
              issued_at: int = 1_760_000_000) -> dict:
    """A payload manifest.sig the way the core signs it."""
    manifest = {
        "files": {rel: hashlib.sha256(body).hexdigest() for rel, body in files.items()},
        "meta": meta,
    }
    channel = server_identity.PLUGIN_CHANNEL
    return {
        "algorithm": "ed25519",
        "fingerprint": server_identity.fingerprint_for(public),
        "public_key": base64.b64encode(public).decode("ascii"),
        "channel": channel,
        "manifest": manifest,
        "issued_at": issued_at,
        "serial": serial,
        "signature": base64.b64encode(
            _ed25519.sign(seed, server_identity.manifest_message(channel, manifest))
        ).decode("ascii"),
        "signature_v2": base64.b64encode(
            _ed25519.sign(seed, server_identity.manifest_message_v2(
                channel, manifest, issued_at, serial))
        ).decode("ascii"),
    }


def _mirror(device, files: dict[str, bytes]) -> None:
    for rel, body in files.items():
        p = device["mirror"] / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)


def _save_envelope(device, envelope: dict) -> None:
    (device["mirror"] / ".manifest.sig").write_text(json.dumps(envelope), encoding="utf-8")


def _listing_script(marker: Path) -> bytes:
    """A post-install that records what root staged beside it."""
    return (
        "#!/bin/sh\n"
        f'ls -1 "$DOMOVOI_PLUGIN_DIR" >"{marker.as_posix()}"\n'
    ).encode("utf-8")


def test_a_pinned_device_runs_what_its_server_signed_and_only_that(device):
    """The request names the slug; what the slug installs and runs comes
    from the signed list. A request that asks for other packages, or a
    file in the mirror the list never named, changes nothing."""
    seed, public, _fp = _keypair()
    _pin(device, public)
    ran = device["tmp"] / "ran"
    files = {
        "radio/post.sh": _listing_script(ran),
        "radio/stations.json": b"{}",
    }
    _mirror(device, files)
    # Planted by the account beside the signed files: must not be staged
    # where root's script could source it.
    (device["mirror"] / "radio" / "lib.sh").write_text("evil() { :; }\n", encoding="utf-8")
    meta = {"radio": {"apt_packages": ["libfoo2"], "post_install": "post.sh",
                      "version": "1.0.0"}}
    _save_envelope(device, _envelope(seed, public, files, meta, serial=5))
    device["pending"].write_text(json.dumps({
        "slugs": {"radio": {"apt_packages": ["openssh-server", "libfoo2"],
                            "post_install": "post.sh", "version": "1.0.0"}},
    }), encoding="utf-8")

    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (proc.stdout, proc.stderr, log)
    assert ran.is_file(), log
    staged = set(ran.read_text(encoding="utf-8").split())
    assert staged == {"post.sh", "stations.json"}, "only the signed files were staged"
    calls = device["calls"].read_text(encoding="utf-8")
    assert "apt-get install -y libfoo2\n" in calls
    assert "openssh-server" not in calls, "the request's packages are not root's to install"
    assert "differs from the signed manifest" in log
    assert "lib.sh is not in the signed list" in log
    assert json.loads(device["fixed"]["SERIAL_FILE"].read_text(encoding="utf-8")) == {"serial": 5}
    state = json.loads(device["fixed"]["STATE_FILE"].read_text(encoding="utf-8"))
    assert state["radio"]["apt_packages"] == ["libfoo2"]


def test_a_pinned_device_refuses_a_payload_signed_by_another_key(device):
    """The A5-01 exploit as root sees it: a mirror and an envelope from a
    core the ACCOUNT chose. Root's pin names a different key; nothing runs."""
    _seed_ours, public_ours, _fp = _keypair(1)
    seed_rogue, public_rogue, _ = _keypair(2)
    _pin(device, public_ours)
    ran = device["tmp"] / "ran"
    files = {"a5evil/post_install.sh": _listing_script(ran)}
    _mirror(device, files)
    meta = {"a5evil": {"apt_packages": [], "post_install": "post_install.sh", "version": "1"}}
    _save_envelope(device, _envelope(seed_rogue, public_rogue, files, meta))
    device["pending"].write_text(json.dumps({"slugs": meta}), encoding="utf-8")

    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 1, (proc.stdout, proc.stderr, log)
    assert not ran.exists(), "the rogue's post_install did not run as root"
    assert "refused: payload not signed by this device's server" in log
    assert not device["fixed"]["STATE_FILE"].exists()
    assert not device["fixed"]["SERIAL_FILE"].exists()
    assert not device["fixed"]["STAGING"].exists()
    assert device["pending"].exists(), "kept for the next verified sync"


def test_a_pinned_device_refuses_a_request_with_no_envelope(device):
    seed, public, _fp = _keypair()
    _pin(device, public)
    ran = device["tmp"] / "ran"
    _mirror(device, {"radio/post.sh": _listing_script(ran)})
    device["pending"].write_text(json.dumps({
        "slugs": {"radio": {"apt_packages": [], "post_install": "post.sh"}},
    }), encoding="utf-8")
    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 1
    assert not ran.exists()
    assert "refused: no signed payload manifest" in log


def test_a_script_whose_bytes_differ_from_the_signed_list_does_not_run(device):
    """The list names the file; the file in the mirror is not the one the
    list hashed. Root runs nothing and says the script is missing."""
    seed, public, _fp = _keypair()
    _pin(device, public)
    ran = device["tmp"] / "ran"
    signed_files = {"radio/post.sh": b"#!/bin/sh\ntrue\n"}
    meta = {"radio": {"apt_packages": [], "post_install": "post.sh", "version": "1.0.0"}}
    _save_envelope(device, _envelope(seed, public, signed_files, meta))
    _mirror(device, {"radio/post.sh": _listing_script(ran)})      # swapped bytes
    device["pending"].write_text(json.dumps({"slugs": meta}), encoding="utf-8")
    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 1, log
    assert not ran.exists()
    assert "does not hash to what the signed list says" in log


def test_a_slug_the_signed_list_does_not_declare_is_ignored(device):
    seed, public, _fp = _keypair()
    _pin(device, public)
    ran = device["tmp"] / "ran"
    _mirror(device, {"evil/post.sh": _listing_script(ran)})
    _save_envelope(device, _envelope(seed, public, {}, {}))
    device["pending"].write_text(json.dumps({
        "slugs": {"evil": {"apt_packages": ["libbar"], "post_install": "post.sh"}},
    }), encoding="utf-8")
    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, log
    assert not ran.exists()
    assert "libbar" not in device["calls"].read_text(encoding="utf-8") \
        if device["calls"].exists() else True
    assert "not in the signed payload manifest" in log


def test_an_older_signed_list_than_the_last_applied_is_refused(device):
    """A recording of a genuine envelope from before: its serial is behind
    the one root last applied."""
    seed, public, _fp = _keypair()
    _pin(device, public)
    serial_file = device["fixed"]["SERIAL_FILE"]
    serial_file.parent.mkdir(parents=True, exist_ok=True)
    serial_file.write_text(json.dumps({"serial": 9}), encoding="utf-8")
    ran = device["tmp"] / "ran"
    files = {"radio/post.sh": _listing_script(ran)}
    _mirror(device, files)
    meta = {"radio": {"apt_packages": [], "post_install": "post.sh", "version": "0.9"}}
    _save_envelope(device, _envelope(seed, public, files, meta, serial=3))
    device["pending"].write_text(json.dumps({"slugs": meta}), encoding="utf-8")
    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 1, log
    assert not ran.exists()
    assert "older than the one last applied" in log
    assert json.loads(serial_file.read_text(encoding="utf-8")) == {"serial": 9}


def test_the_same_serial_applied_again_is_fine(device):
    """A post_install that changed under the same signed list cannot
    happen (the hash is in the list); the same list re-applied is just a
    re-run of what was already vetted."""
    seed, public, _fp = _keypair()
    _pin(device, public)
    serial_file = device["fixed"]["SERIAL_FILE"]
    serial_file.parent.mkdir(parents=True, exist_ok=True)
    serial_file.write_text(json.dumps({"serial": 5}), encoding="utf-8")
    ran = device["tmp"] / "ran"
    files = {"radio/post.sh": _listing_script(ran)}
    _mirror(device, files)
    meta = {"radio": {"apt_packages": [], "post_install": "post.sh", "version": "1.0.0"}}
    _save_envelope(device, _envelope(seed, public, files, meta, serial=5))
    device["pending"].write_text(json.dumps({"slugs": meta}), encoding="utf-8")
    assert device["run"]().returncode == 0
    assert ran.is_file()


def test_a_pinned_device_without_the_verifier_runs_nothing(device):
    """A pin and no verifier is not a shape a prepared card has (first boot
    installs both from the same payload); if it ever happens, fail closed."""
    seed, public, _fp = _keypair()
    _pin(device, public)
    device["fixed"]["VERIFIER"].unlink()
    ran = device["tmp"] / "ran"
    files = {"radio/post.sh": _listing_script(ran)}
    _mirror(device, files)
    meta = {"radio": {"apt_packages": [], "post_install": "post.sh"}}
    _save_envelope(device, _envelope(seed, public, files, meta))
    device["pending"].write_text(json.dumps({"slugs": meta}), encoding="utf-8")
    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 1
    assert not ran.exists()
    assert "verifier is missing" in log


def test_the_helpers_canonical_form_matches_the_trees_that_sign(device):
    """The helper carries its own copy of the canonical-JSON and message
    construction (it imports nothing from the account's tree). The envelope
    above was signed with the satellite tree's builder; the helper accepted
    it — this pins that agreement explicitly, with a list that exercises key
    order and non-ASCII."""
    seed, public, _fp = _keypair()
    _pin(device, public)
    files = {"radio/post.sh": b"#!/bin/sh\ntrue\n", "radio/zü.txt": b"x", "radio/a.txt": b"y"}
    _mirror(device, files)
    meta = {"radio": {"apt_packages": [], "post_install": "post.sh", "version": "ü1"}}
    _save_envelope(device, _envelope(seed, public, files, meta, serial=11))
    device["pending"].write_text(json.dumps({"slugs": meta}), encoding="utf-8")
    proc = device["run"]()
    log = device["fixed"]["LOGFILE"].read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, log
    assert json.loads(device["fixed"]["SERIAL_FILE"].read_text(encoding="utf-8")) == {"serial": 11}
