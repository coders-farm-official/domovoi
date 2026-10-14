"""The installer's Python half (satellite/pc_install.py) and the shape of
satellite/scripts/install-pc.sh.

The config an enrolled laptop runs on, the wake-word models (from the
core's signed channel or openWakeWord's release, always held to the
pins), and the record the uninstaller reads. The real install, in fresh
Debian and Ubuntu containers, is functional-testing/sat-pc/.
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
PAYLOAD = enrolment.build("http://192.168.0.117:6370", FP, "Kid's Room")


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
    assert cfg.domovoi_url == "ws://192.168.0.117:6370" and cfg.server_fingerprint == FP
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
    other = enrolment.parse(enrolment.build("http://192.168.0.117:6370", FP, "Office"))
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
    routes = {"http://192.168.0.117:6370/v1/wake-base/manifest.sig": json.dumps(signed_list(pinned)).encode()}
    for name, body in BODIES.items():
        routes[f"http://192.168.0.117:6370/v1/wake-base/{name}"] = body
    got = pc_install.fetch_models(tmp_path, core="http://192.168.0.117:6370", fp=FP,
                                  opener=opener_for(routes, seen), log=lambda m: None)
    assert set(got.values()) == {"core"}
    assert not any("github" in u for u in seen)
    assert all((tmp_path / n).read_bytes() == b for n, b in BODIES.items())


def test_a_list_signed_by_another_key_is_not_used(tmp_path, pinned):
    seen = []
    routes = {"http://192.168.0.117:6370/v1/wake-base/manifest.sig":
              json.dumps(signed_list(pinned, seed=bytes([1]) * 32)).encode()}
    for name, body in BODIES.items():
        routes[pc_install.OWW_RELEASE_URL + name] = body
    got = pc_install.fetch_models(tmp_path, core="http://192.168.0.117:6370", fp=FP,
                                  opener=opener_for(routes, seen), log=lambda m: None)
    assert set(got.values()) == {"openwakeword"}


def test_without_the_core_s_channel_the_release_is_used_and_held_to_the_pins(tmp_path, pinned):
    routes = {pc_install.OWW_RELEASE_URL + n: b for n, b in BODIES.items()}
    got = pc_install.fetch_models(tmp_path, core="http://192.168.0.117:6370", fp=FP,
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


# ─── the record ───────────────────────────────────────────────────────────

def test_the_record_keeps_what_the_installer_changed(tmp_path):
    pc_install.note_state(tmp_path, mode="desktop", linger_enabled_by_installer=True)
    pc_install.note_state(tmp_path, masked_targets=["sleep.target"])
    assert pc_install.read_state(tmp_path) == {
        "mode": "desktop", "linger_enabled_by_installer": True, "masked_targets": ["sleep.target"]}
    assert pc_install._main(["state", "--config-dir", str(tmp_path), "--set", 'charge_rule="/x"']) == 0
    assert pc_install.read_state(tmp_path)["charge_rule"] == "/x"


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
