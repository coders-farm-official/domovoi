"""The installer's Python half (satellite/pc_install.py) and the shape of
satellite/scripts/install-pc.sh.

The config an enrolled laptop runs on, the wake-word models (from the
core's signed channel or openWakeWord's release, always held to the
pins), and the record the uninstaller reads. The real install, in fresh
Debian and Ubuntu containers, is run by the maintainers' functional test
harness, kept outside this repo.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import urllib.error
from pathlib import Path

import pytest

from satellite import _ed25519, enrolment, pc_install, server_identity
from satellite.tests._client_import import import_client

client = import_client()
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "satellite" / "config.toml.example"
SEED = bytes([9]) * 32
PUBLIC = _ed25519.public_key(SEED)
FP = server_identity.fingerprint_for(PUBLIC)
PAYLOAD = enrolment.build("http://192.168.1.20:6370", FP, "Kid's Room")


def test_the_model_pins_are_the_core_s():
    """Held equal to domovoi/satellite_media/fetchers.py OWW_MODEL_FILES
    (read as text: the satellite tests never import the core)."""
    src = (ROOT / "domovoi" / "satellite_media" / "fetchers.py").read_text(encoding="utf-8")
    for name, (sha, size) in pc_install.OWW_MODELS.items():
        m = re.search(rf'"{re.escape(name)}": \("([0-9a-f]{{64}})", (\d+)\)', src)
        assert m and (m.group(1), int(m.group(2))) == (sha, size), name
    assert f'OWW_RELEASE_URL = "{pc_install.OWW_RELEASE_URL}"' in src


# ─── the config ───────────────────────────────────────────────────────────

def test_a_fresh_config_is_an_enrolled_pinned_laptop(tmp_path):
    cfg_path = tmp_path / ".domovoi" / "config.toml"
    report = pc_install.write_config(cfg_path, EXAMPLE, enrolment.parse(PAYLOAD), "ask")
    assert report["backup"] is None
    cfg = client.Config.load(cfg_path)
    assert cfg.device.name == "generic_pc" and cfg.room_id == "kids_room" and cfg.room_name == "Kid's Room"
    assert cfg.domovoi_url == "ws://192.168.1.20:6370" and cfg.server_fingerprint == FP
    assert cfg.permissions == "ask" and not cfg.wifi_enabled and not cfg.leds_enabled
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(cfg_path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(cfg_path.parent).st_mode) == 0o700


def test_re_enrolling_keeps_the_owners_settings_and_a_backup(tmp_path):
    cfg_path = tmp_path / ".domovoi" / "config.toml"
    e = enrolment.parse(PAYLOAD)
    pc_install.write_config(cfg_path, EXAMPLE, e, "automatic")
    text = cfg_path.read_text(encoding="utf-8").replace("vad_aggressiveness = 2", "vad_aggressiveness = 3")
    cfg_path.write_text(text, encoding="utf-8")
    (cfg_path.parent / "approved").write_text("approved\n")
    report = pc_install.write_config(cfg_path, EXAMPLE, e, "ask", now=lambda: 0)
    assert report["backup"] and Path(report["backup"]).exists()
    assert report["reset_approval"] is False, "same core, same room: still approved"
    cfg = client.Config.load(cfg_path)
    assert cfg.vad_aggressiveness == 3 and cfg.permissions == "ask"
    assert (cfg_path.parent / "approved").exists()


def test_moving_to_another_core_or_room_starts_unapproved(tmp_path):
    cfg_path = tmp_path / ".domovoi" / "config.toml"
    pc_install.write_config(cfg_path, EXAMPLE, enrolment.parse(PAYLOAD), "ask")
    d = cfg_path.parent
    for name in ("approved", "approval_code", "pairing_token"):
        (d / name).write_text("x\n")
    other = enrolment.parse(enrolment.build("http://192.168.1.20:6370", FP, "Office"))
    report = pc_install.write_config(cfg_path, EXAMPLE, other, "ask", now=lambda: 0)
    assert report["reset_approval"] is True
    assert not any((d / n).exists() for n in ("approved", "approval_code", "pairing_token"))
    aside = list(d.glob("previous-enrolment-*"))
    assert aside and (aside[0] / "approved").exists()
    assert client.Config.load(cfg_path).room_id == "office"


def test_a_bad_permission_is_refused(tmp_path):
    with pytest.raises(ValueError):
        pc_install.write_config(tmp_path / "c.toml", EXAMPLE, enrolment.parse(PAYLOAD), "sometimes")


# ─── the models ───────────────────────────────────────────────────────────

BODIES = {name: (b"%s-body" % name.encode()) for name in pc_install.OWW_MODELS}


@pytest.fixture
def pinned(monkeypatch):
    """Pins that match fake bodies (the real files are megabytes)."""
    import hashlib

    pins = {n: (hashlib.sha256(b).hexdigest(), len(b)) for n, b in BODIES.items()}
    monkeypatch.setattr(pc_install, "OWW_MODELS", pins)
    return pins


class Resp:
    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status = body, status

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def signed_list(pins, *, seed=SEED):
    manifest = {n: sha for n, (sha, _) in pins.items()}
    public = _ed25519.public_key(seed)
    sig = _ed25519.sign(seed, server_identity.manifest_message_v2(
        pc_install.WAKE_BASE_CHANNEL, manifest, 1760000000, 3))
    return {"algorithm": "ed25519", "channel": pc_install.WAKE_BASE_CHANNEL, "manifest": manifest,
            "public_key": base64.b64encode(public).decode(), "issued_at": 1760000000, "serial": 3,
            "signature_v2": base64.b64encode(sig).decode()}


def opener_for(routes, seen):
    def opener(url, timeout=None):
        seen.append(url)
        for prefix, answer in routes.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return Resp(answer)
        raise urllib.error.URLError("no route")
    return opener


def test_models_come_from_the_core_when_it_serves_them(tmp_path, pinned):
    seen = []
    routes = {"http://192.168.1.20:6370/v1/wake-base/manifest.sig": json.dumps(signed_list(pinned)).encode()}
    for name, body in BODIES.items():
        routes[f"http://192.168.1.20:6370/v1/wake-base/{name}"] = body
    got = pc_install.fetch_models(tmp_path, core="http://192.168.1.20:6370", fp=FP,
                                  opener=opener_for(routes, seen), log=lambda m: None)
    assert set(got.values()) == {"core"}
    assert not any("github" in u for u in seen)
    assert all((tmp_path / n).read_bytes() == b for n, b in BODIES.items())


def test_a_list_signed_by_another_key_is_not_used(tmp_path, pinned):
    seen = []
    routes = {"http://192.168.1.20:6370/v1/wake-base/manifest.sig":
              json.dumps(signed_list(pinned, seed=bytes([1]) * 32)).encode()}
    for name, body in BODIES.items():
        routes[pc_install.OWW_RELEASE_URL + name] = body
    got = pc_install.fetch_models(tmp_path, core="http://192.168.1.20:6370", fp=FP,
                                  opener=opener_for(routes, seen), log=lambda m: None)
    assert set(got.values()) == {"openwakeword"}


def test_without_the_core_s_channel_the_release_is_used_and_held_to_the_pins(tmp_path, pinned):
    routes = {pc_install.OWW_RELEASE_URL + n: b for n, b in BODIES.items()}
    got = pc_install.fetch_models(tmp_path, core="http://192.168.1.20:6370", fp=FP,
                                  opener=opener_for(routes, []), log=lambda m: None)
    assert set(got.values()) == {"openwakeword"}
    assert pc_install.fetch_models(tmp_path, core=None, fp=None,
                                   opener=opener_for({}, []), log=lambda m: None) == {
        n: "present" for n in BODIES}


def test_a_tampered_download_is_refused_and_not_placed(tmp_path, pinned):
    name = next(iter(BODIES))
    routes = {pc_install.OWW_RELEASE_URL + n: b for n, b in BODIES.items()}
    routes[pc_install.OWW_RELEASE_URL + name] = b"something else"
    with pytest.raises(OSError, match="not the pinned file"):
        pc_install.fetch_models(tmp_path, core=None, fp=None, opener=opener_for(routes, []),
                                log=lambda m: None)
    assert not (tmp_path / name).exists()


# ─── --purge: the satellite's files, never a server's ─────────────────────
#
# satellite/scripts/purge-satellite-files.py: root's copy of it runs with
# the system's python3 -I at uninstall, never the satellite's own code
# (review round 2: that code could use the terminal's sudo ticket).

def _load_purger():
    import importlib.util

    path = ROOT / "satellite" / "scripts" / "purge-satellite-files.py"
    spec = importlib.util.spec_from_file_location("purge_satellite_files", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


purger = _load_purger()


def _satellite_files(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for name in ("config.toml", "approved", "server-fingerprint.json", "muted", "install-pc.json"):
        (d / name).write_text("x", encoding="utf-8")
    (d / "config.toml.bak-20261013").write_text("x", encoding="utf-8")
    (d / "previous-enrolment-new").mkdir()
    (d / "previous-enrolment-new" / "approved").write_text("x", encoding="utf-8")
    (d / "sounds" / "greetings").mkdir(parents=True)
    (d / "sounds" / "greetings" / "hello.mp3").write_bytes(b"ours")
    (d / "sounds" / "network_issues.mp3").write_bytes(b"ours")
    (d / "sounds" / purger.SOUNDS_RECORD).write_text(
        json.dumps(["greetings/hello.mp3", "network_issues.mp3"]), encoding="utf-8")
    (d / "wake_models").mkdir()
    (d / "wake_models" / "hey_room.onnx").write_bytes(b"ours")


def test_purge_removes_the_satellite_s_files(tmp_path):
    d = tmp_path / ".domovoi"
    _satellite_files(d)
    (d / "notes.txt").write_text("the owner's", encoding="utf-8")
    gone = purger.purge(d)
    assert {"config.toml", "approved", "install-pc.json", "config.toml.bak-20261013",
            "previous-enrolment-new", "sounds", "wake_models"} <= set(gone)
    assert sorted(p.name for p in d.iterdir()) == ["notes.txt"]


def test_purge_leaves_a_server_s_files_in_a_shared_directory_alone(tmp_path):
    """S2 (review): ~/.domovoi on a core's account holds its key, its
    voices (sounds/voices/...) and its wake models."""
    d = tmp_path / ".domovoi"
    _satellite_files(d)
    (d / "server-identity.json").write_text("{key}", encoding="utf-8")
    (d / "device-token.txt").write_text("t", encoding="utf-8")
    voices = d / "sounds" / "voices" / "amy"
    voices.mkdir(parents=True)
    (voices / "hello.mp3").write_bytes(b"the server's")
    (d / "sounds" / "network_issues_v2.mp3").write_bytes(b"the server's")
    gone = purger.purge(d)
    assert "sounds/greetings/hello.mp3" in gone and "sounds/network_issues.mp3" in gone
    assert (d / "server-identity.json").read_text(encoding="utf-8") == "{key}"
    assert (d / "device-token.txt").exists()
    assert (voices / "hello.mp3").read_bytes() == b"the server's"
    assert (d / "sounds" / "network_issues_v2.mp3").exists()
    assert (d / "wake_models" / "hey_room.onnx").exists()
    assert not (d / "config.toml").exists() and not (d / "sounds" / "greetings").exists()


def test_purge_without_a_sounds_record_keeps_a_server_s_folders_whole(tmp_path):
    """Before the record existed, nothing says which clips are the
    satellite's: with a server in the same directory, its folders stay."""
    d = tmp_path / ".domovoi"
    _satellite_files(d)
    (d / "sounds" / purger.SOUNDS_RECORD).unlink()
    (d / "setup-code.txt").write_text("123", encoding="utf-8")
    purger.purge(d)
    assert (d / "sounds" / "greetings" / "hello.mp3").exists() and (d / "wake_models" / "hey_room.onnx").exists()
    assert not (d / "config.toml").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks: the Linux container runs this")
def test_purge_removes_links_and_never_follows_one(tmp_path):
    d = tmp_path / ".domovoi"
    _satellite_files(d)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "keep.mp3").write_bytes(b"keep")
    (outside / "config.toml").write_text("keep", encoding="utf-8")
    (d / "config.toml").unlink()
    (d / "config.toml").symlink_to(outside / "config.toml")
    (d / "sounds" / "linked").symlink_to(outside, target_is_directory=True)
    (d / "sounds" / purger.SOUNDS_RECORD).write_text(
        json.dumps(["linked/keep.mp3", "../elsewhere/keep.mp3", "network_issues.mp3"]), encoding="utf-8")
    purger.purge(d)
    assert not (d / "config.toml").is_symlink()
    assert (outside / "keep.mp3").read_bytes() == b"keep"
    assert (outside / "config.toml").read_text(encoding="utf-8") == "keep"


def test_purge_shares_its_names_with_the_engine_the_sound_sync_and_the_script():
    from satellite import pc_engine, sound_sync

    assert purger.CORE_MARKERS == pc_engine.CORE_MARKERS
    assert f'CORE_MARKERS="{" ".join(purger.CORE_MARKERS)}"' in SCRIPT.read_text(encoding="utf-8")
    assert purger.SOUNDS_RECORD == sound_sync.RECORD_NAME
    assert purger.main([str(ROOT / "no-such-dir")]) == 0


def test_the_purger_needs_nothing_but_the_standard_library():
    """It runs as `python3 -I` from root's copy: no satellite import."""
    text = (ROOT / "satellite" / "scripts" / "purge-satellite-files.py").read_text(encoding="utf-8")
    imports = [ln for ln in text.splitlines() if ln.startswith(("import ", "from "))]
    assert all(ln.split()[1].split(".")[0] in ("__future__", "json", "os", "shutil", "sys", "pathlib")
               for ln in imports), imports


def test_no_satellite_code_runs_at_uninstall():
    """The uninstaller never runs the satellite's own code or venv (the
    account can change them, and a sudo ticket may be live): its purge is
    root's copy of the purger, under the system's python3 -I."""
    body = _function("uninstall") + _function("purge_satellite_files")
    assert "$PREFIX" not in body.replace('rm -rf "$PREFIX"', "")
    assert "$VENV" not in body and "PYTHONPATH" not in body and "pc_install" not in body
    assert 'as_sat $PY -I "$PURGER" "$CONFIG_DIR"' in body and "PY=/usr/bin/python3" in SCRIPT.read_text(encoding="utf-8")
    assert '$SUDO /usr/bin/install -o root -g root -m 0644 "$HERE/purge-satellite-files.py" "$PURGER"' in SCRIPT.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        pc_install._main(["purge", "--config-dir", "x"])


def test_the_satellite_keeps_no_record_root_reads():
    """B3 (review): the uninstaller ran as root on paths from
    ~/.domovoi/install-pc.json, a file the satellite's account writes. The
    record is root's now, written and read by the script alone."""
    with pytest.raises(SystemExit):
        pc_install._main(["state", "--config-dir", "x", "--set", 'charge_rule="/etc/x"'])
    assert not hasattr(pc_install, "note_state")


# ─── the script ───────────────────────────────────────────────────────────

SCRIPT = ROOT / "satellite" / "scripts" / "install-pc.sh"


def _bash():
    if sys.platform != "win32":
        return shutil.which("bash")
    git = shutil.which("git")
    for c in ([Path(git).parent.parent / "bin" / "bash.exe"] if git else []) + [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"]:
        if c.is_file():
            return str(c)
    return None


@pytest.mark.skipif(_bash() is None, reason="needs bash")
def test_the_script_parses_and_refuses_bad_arguments():
    bash = _bash()
    assert subprocess.run([bash, "-n", str(SCRIPT)]).returncode == 0
    r = subprocess.run([bash, str(SCRIPT), "--permissions", "sometimes", PAYLOAD], capture_output=True, text=True)
    assert r.returncode == 2 and "automatic or ask" in r.stderr


def test_the_script_never_installs_a_root_helper_or_a_sudoers_line():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "/etc/sudoers.d" not in text and "visudo" not in text
    for helper in ("domovoi-apply-payload", "domovoi-sync-time", "domovoi-reboot", "wpa_cli"):
        assert helper not in text
    assert "--permissions" in text and "loginctl enable-linger" in text


def test_the_units_take_no_privilege_and_restart_on_75_not_78():
    for unit in ("domovoi-satellite-user.service", "domovoi-satellite-system.service"):
        text = (ROOT / "satellite" / "scripts" / unit).read_text(encoding="utf-8")
        assert "NoNewPrivileges=yes" in text
        assert "RestartForceExitStatus=75" in text and "RestartPreventExitStatus=78" in text
        assert "sudo" not in text.split("[Unit]", 1)[1]
    system = (ROOT / "satellite" / "scripts" / "domovoi-satellite-system.service").read_text(encoding="utf-8")
    assert "ProtectSystem=strict" in system and "User=@USER@" in system


# ─── what root may touch on uninstall (review B3) ─────────────────────────

def _function(name: str) -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(rf"^{name}\(\) \{{.*?^\}}$", text, re.S | re.M)
    assert m, name
    return m.group(0)


def test_uninstall_reads_root_s_record_and_acts_on_fixed_paths_only():
    text = SCRIPT.read_text(encoding="utf-8")
    body = _function("uninstall")
    # Nothing under the satellite's home is read as a list of things to do.
    assert "install-pc.json" not in body and "$CONFIG_DIR/install" not in body
    assert "RECORD=$RECORD_DIR/install.json" in text and "RECORD_DIR=/var/lib/domovoi-satellite" in text
    # Root removes only fixed paths, and nothing recursively.
    removals = [line for line in body.splitlines() if "$SUDO /usr/bin/rm" in line]
    assert len(removals) >= 6, removals
    for line in removals:
        if "$SUDO /usr/bin/rm" in line:
            target = line.split("$SUDO /usr/bin/rm", 1)[1]
            assert "-r" not in target.split()[0], line
            assert any(v in target for v in ('"$LOGIND_DROPIN"', '"$CHARGE_RULE"', '"/etc/systemd/system/$UNIT"', '"$RECORD_DIR"', '"$ROOT_LIB"',
                                             '"$RECORD"', '"$UNINSTALLER"', '"$PURGER"')), line
    # Units: the fixed sleep targets, whatever the record lists.
    assert 'case " $SLEEP_TARGETS " in' in body
    # The account's files go as the account.
    assert 'as_sat /usr/bin/rm -rf "$PREFIX"' in body
    assert "userdel -r" in body


def test_record_get_trusts_only_a_root_owned_record():
    body = _function("record_get")
    assert '[ ! -L "$RECORD" ]' in body and 'stat -c %u "$RECORD"' in body and 'stat -c %u "$RECORD_DIR"' in body


def test_the_uninstall_lines_name_root_s_copy():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "UNINSTALLER=$ROOT_LIB/install-pc.sh" in text and "ROOT_LIB=/usr/local/lib/domovoi-satellite" in text
    assert 'say "  uninstall: bash $UNINSTALLER --uninstall"' in text
    assert 'say "  uninstall: sudo bash $UNINSTALLER --uninstall"' in text
    assert "$PREFIX/satellite/scripts/install-pc.sh" not in text
    assert '$SUDO /usr/bin/install -o root -g root -m 0755 "$HERE/install-pc.sh" "$UNINSTALLER"' in text
    guide = (ROOT / "docs" / "SPARE_DEVICE_SATELLITES.md").read_text(encoding="utf-8")
    assert "\nbash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall" in guide
    assert "sudo bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall --purge --account" in guide
    assert "satellite/scripts/install-pc.sh --uninstall" not in guide


def test_root_never_works_inside_the_satellite_s_home():
    """Install as well as uninstall: root creates no directory in, and
    changes no owner under, a home the account controls."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert '$SUDO /usr/bin/install -d -o "$SAT_ACCOUNT"' not in text and '$SUDO install' not in text
    assert 'as_sat /usr/bin/mkdir -p "$PREFIX"' in text
    assert "chown" not in text.replace("# ", "") and "$SUDO /usr/bin/rm -rf" not in text


# ─── as_sat (review S3: root + headless) ──────────────────────────────────

@pytest.mark.skipif(_bash() is None, reason="needs bash")
@pytest.mark.parametrize("uid,user,run_as,home,sudo,expect", [
    # root (a headless install, or any uninstall): runuser, a clean environment
    (0, "root", "domovoi-sat", "/var/lib/domovoi-sat", "",
     "runuser -u domovoi-sat -- /usr/bin/setsid -w /usr/bin/env -i HOME=/var/lib/domovoi-sat LANG=C.UTF-8 "
     "PATH=/usr/bin:/bin id -un"),
    (0, "root", "kid", "/home/kid", "",
     "runuser -u kid -- /usr/bin/setsid -w /usr/bin/env -i HOME=/home/kid LANG=C.UTF-8 PATH=/usr/bin:/bin id -un"),
    # a sudoer installing a headless box
    (1000, "admin", "domovoi-sat", "/var/lib/domovoi-sat", "sudo",
     "sudo -u domovoi-sat -H -- /usr/bin/setsid -w id -un"),
    # a desktop install, in the account itself
    (1000, "kid", "kid", "/home/kid", "sudo", "setsid -w id -un"),
])
def test_as_sat_runs_as_the_account_in_a_session_of_its_own(uid, user, run_as, home, sudo, expect):
    """S3 (root + headless), and rounds 2 and 3: the satellite's code never
    gets this terminal (setsid, stdin from /dev/null), so it can neither
    borrow a sudo ticket from it nor type into it; from root it gets a clean
    environment; every tool by its full path."""
    script = ("/usr/bin/id() { case $1 in -u) echo " + str(uid) + ";; -un) echo " + user + ";; esac; }\n"
              + "/usr/sbin/runuser() { echo runuser \"$@\"; }\nsudo() { echo sudo \"$@\"; }\n"
              + "/usr/bin/setsid() { echo setsid \"$@\"; }\n"
              + _function("as_sat") + "\n"
              + f'RUN_AS={run_as} SUDO="{sudo}" HOME_DIR={home}\n'
              + "as_sat id -un; echo rc=$?\n")
    r = subprocess.run([_bash(), "-c", script], capture_output=True, text=True)
    assert r.stdout.splitlines() == [expect, "rc=0"], (r.stdout, r.stderr)
    assert "</dev/null" in _function("as_sat").replace(" </dev/null", "</dev/null")


def test_a_fixed_path_and_no_caller_functions_before_anything_runs():
    """Round 3: a python3 in ~/.local/bin (on PATH via Debian's stock
    ~/.profile) ran with a live sudo ticket. PATH is fixed, the caller's
    functions and shell hooks dropped, before the first command; and every
    tool is named by its full path besides."""
    text = SCRIPT.read_text(encoding="utf-8")
    head = text[:text.index("# ─── arguments")]
    for needle in ("PATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n", "unset BASH_ENV ENV CDPATH GLOBIGNORE",
                   'for _f in $(compgen -A function); do unset -f "$_f"; done', "PY=/usr/bin/python3"):
        assert needle in head, needle
    assert head.index("set -euo pipefail") < head.index("PATH=/usr/sbin")
    assert "SUDO=/usr/bin/sudo" in text
    code = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    tools = ("sudo|python3|setsid|runuser|install|apt-get|env|useradd|usermod|userdel|stat|chmod|mkdir|tee|"
             "systemctl|loginctl|rm|rmdir|cp|cat|cut|date|dirname|basename|dpkg-query|getent|grep|id|ls|mktemp|"
             "sed|tr|awk|sleep|pkill|find|uname|sh|test")
    bare = re.compile(r"(^|[;&|({]|\$\(|\bthen|\bdo|\bas_sat|\$SUDO|\|\||&&)\s*(" + tools + r")\s")
    offenders = [ln for ln in code if bare.search(ln.split(' say "')[0].split(' die "')[0])
                 and not ln.lstrip().startswith(("say ", "die "))]
    assert offenders == [], offenders


def test_a_desktop_install_is_done_with_sudo_before_the_satellite_s_code_runs():
    """Round 2: every sudo step, then `sudo -k`, then the account's code
    (in a fresh venv: an old one is never run)."""
    text = SCRIPT.read_text(encoding="utf-8")
    tail = text[text.index("# ─── 3. root's copy of the uninstaller"):]
    forget = tail.index("  forget_sudo\n")
    first_account_code = tail.index("as_sat ", tail.index("# ─── 7."))
    assert forget < first_account_code
    desktop_after = tail[forget:]
    # After that point a desktop install uses no sudo (the headless unit's are in its own branch).
    for line in desktop_after[:desktop_after.index('elif [ "$MODE" = desktop ]; then')].splitlines():
        assert "$SUDO" not in line, line
    assert 'as_sat /usr/bin/rm -rf "$VENV"\nas_sat $PY -I -m venv "$VENV"' in text
    assert '[ -x "$VENV/bin/python" ] ||' not in text


# ─── the installer proves the core before anything (review S4, M8) ────────

@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="runs the script on Linux")
@pytest.mark.skipif(sys.version_info < (3, 11), reason="the installer needs Python 3.11")
def test_a_core_that_does_not_prove_its_fingerprint_stops_the_install_before_any_write(tmp_path):
    payload = enrolment.build("http://no-such-core.home.arpa:6370", FP, "Kid's Room")
    home = tmp_path / "laptop"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "DOMOVOI_TEST_SYSTEMD_RUNTIME": str(tmp_path / "no-systemd")}
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    mode = "headless" if os.geteuid() == 0 else "desktop"
    r = subprocess.run(["bash", str(SCRIPT), "--yes", "--permissions", "automatic", "--mode", mode,
                        "--skip-apt", "--wait", "0", payload],
                       capture_output=True, text=True, env=env, timeout=120, stdin=subprocess.DEVNULL)
    assert r.returncode == 3, (r.returncode, r.stdout[-800:], r.stderr[-800:])
    assert "did not prove" in r.stderr and "nothing was installed" in r.stderr
    assert list(home.iterdir()) == []
    if os.geteuid() == 0:
        assert not Path("/usr/local/lib/domovoi-satellite").exists()


# ─── the Python packages: hash-pinned (review S1) ─────────────────────────

LOCKS = ROOT / "satellite" / "locks"


def _lock_pins(path: Path) -> dict[str, str]:
    pins, current = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^ \\]+) \\$", line)
        if m:
            current = m.group(1).lower().replace("_", "-")
            pins[current] = m.group(2)
            continue
        if line.startswith("    --hash=sha256:"):
            assert current, line
            assert re.fullmatch(r"    --hash=sha256:[0-9a-f]{64}( \\)?", line), line
    return pins


def test_every_supported_python_has_a_hash_pinned_list():
    names = sorted(p.name for p in LOCKS.glob("pc-*.txt"))
    for tag in ("cp311", "cp312", "cp313", "cp314"):
        assert f"pc-{tag}-x86_64.txt" in names
    for p in LOCKS.glob("pc-*.txt"):
        text = p.read_text(encoding="utf-8")
        blocks = re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text)
        for block in blocks[1:]:
            assert "--hash=sha256:" in block, (p.name, block[:80])
        pins = _lock_pins(p)
        assert pins["openwakeword"] == "0.6.0" and "spidev" not in pins and "tflite-runtime" not in pins


def test_the_lists_hold_what_the_requirements_ask_for():
    from packaging.requirements import Requirement
    from packaging.version import Version

    reqs = []
    for f in ("requirements.txt", "requirements-pc.txt"):
        for line in (ROOT / "satellite" / f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                reqs.append(Requirement(line))
    for p in LOCKS.glob("pc-*.txt"):
        machine = p.stem.split("-", 2)[2]
        pins = _lock_pins(p)
        for req in reqs:
            if req.marker and not req.marker.evaluate({"platform_machine": machine}):
                continue
            name = req.name.lower().replace("_", "-")
            if name == "spidev":
                continue
            assert name in pins, (p.name, name)
            assert req.specifier.contains(Version(pins[name]), prereleases=True), (p.name, name, pins[name])


def test_the_installer_installs_only_from_the_list_with_hashes():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--require-hashes --only-binary=:all: --no-deps -r \"$PREFIX/$LOCK_REL\"" in text
    assert "LOCK_REL=satellite/locks/pc-$PYTAG-$(/usr/bin/uname -m).txt" in text
    pip_lines = [ln for ln in text.splitlines() if "pip\" install" in ln or "/pip install" in ln]
    assert len(pip_lines) == 1, pip_lines
