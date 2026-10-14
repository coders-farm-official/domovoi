"""A spare Linux laptop as a satellite: the ``generic_pc`` engine (S1).

What changes for such a device and only for it: its profile, the hello's
``board`` and ``supports`` (held to the shared fixture), the pin it may not
run without, the approval code on its terminal and never in its log, mute,
the "ask me" permission, settings the core may not change, no root plugin
payloads and no time sync, and the CLI. The Pi profiles are checked
alongside, to stay exactly as they were.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import types
from pathlib import Path

import pytest

from satellite import control, devices, engine_platform, pc_engine
from satellite.tests._client_import import import_client

client = import_client()

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((ROOT / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
CONTROL = json.loads((ROOT / "docs" / "uml" / "satellite-control-fixture.json").read_text(encoding="utf-8"))
FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"


def pc_cfg(extra: str = "", **sections) -> client.Config:
    doc = {
        "device": {"profile": "generic_pc"},
        "satellite": {"room_id": "study", "room_name": "Study", "domovoi_url": "ws://127.0.0.1:6370",
                      "server_fingerprint": FP},
    }
    for name, values in sections.items():
        doc.setdefault(name, {}).update(values)
    return client.Config.from_toml(doc)


def pc_sat(**sections) -> client.Satellite:
    return client.Satellite(pc_cfg(**sections))


# ─── the profile ──────────────────────────────────────────────────────────

def test_the_generic_pc_profile_is_a_plain_laptop():
    p = devices.resolve("generic_pc")
    assert (p.capture_dtype, p.capture_channels, p.capture_select_channel) == ("int16", 1, None)
    assert p.supports_full_duplex is False and p.mic_gain_enabled is False
    assert p.leds_enabled_default is False and p.wifi_enabled_default is False
    assert p.music_alsa_device == "default" and p.noise_gate_auto_calibrate is True
    assert p.audio_device_match == "" and p.board == "linux_pc"


def test_a_laptop_config_has_no_leds_wifi_watcher_or_mic_gain():
    cfg = pc_cfg()
    assert not cfg.leds_enabled and not cfg.wifi_enabled and not cfg.mic_gain_enabled
    assert cfg.music_alsa_device == "default"
    assert cfg.permissions == "automatic" and cfg.room_name == "Study"


@pytest.mark.parametrize("profile", ["respeaker_2mic_hat", "xvf3800_usb", "radxa_zero3w_video"])
def test_every_pi_profile_keeps_its_wifi_watcher_and_no_board(profile):
    cfg = client.Config.from_toml({"device": {"profile": profile}})
    assert cfg.wifi_enabled is True
    assert devices.resolve(profile).board == ""
    assert not engine_platform.is_pc(cfg)


def test_the_permission_and_the_volume_backend_fail_loud_on_a_typo():
    assert pc_cfg(pc={"permissions": "ask"}).permissions == "ask"
    with pytest.raises(ValueError, match="permissions"):
        pc_cfg(pc={"permissions": "sometimes"})
    assert pc_cfg(audio={"volume_backend": "pactl"}).volume_backend == "pactl"
    with pytest.raises(ValueError, match="volume_backend"):
        pc_cfg(audio={"volume_backend": "alsa"})


def test_full_duplex_is_an_owner_s_override_on_a_laptop_only():
    assert pc_engine.full_duplex(pc_cfg()) is False
    assert pc_engine.full_duplex(pc_cfg(device={"full_duplex": True})) is True
    hat = client.Config.from_toml({"device": {"profile": "respeaker_2mic_hat", "full_duplex": True}})
    assert pc_engine.full_duplex(hat) is False, "a HAT's missing AEC is a hardware fact"


# ─── the hello ────────────────────────────────────────────────────────────

def test_the_laptop_hello_is_the_fixtures_linux_pc_hello():
    want = FIXTURES["devices"]["linux_pc"]["hello"]
    got = pc_sat()._hello_frame()
    assert set(got) == set(want)
    for key in ("type", "sat_type", "mic_enabled", "speech_pause", "capture_control",
                "announce_after_session_end", "board", "supports_full_duplex"):
        assert got[key] == want[key], key
    assert got["supports"] == want["supports"]
    expect = FIXTURES["devices"]["linux_pc"]["expect"]
    assert not set(got["supports"]) & set(expect["not_allowed"])


def test_the_laptop_never_declares_what_needs_root():
    supports = set(pc_sat()._hello_frame()["supports"])
    assert not supports & {"plugin_payloads", "wifi", "leds", "mic_gain", "time_sync", "display"}


def test_a_speakerphone_laptop_declares_full_duplex_and_chat():
    hello = pc_sat(device={"full_duplex": True})._hello_frame()
    assert hello["supports_full_duplex"] is True and "chat_mode" in hello["supports"]


def test_a_pi_hello_has_no_board_and_no_list():
    sat = client.Satellite(client.Config.from_toml({"device": {"profile": "xvf3800_usb"}}))
    hello = sat._hello_frame()
    assert "board" not in hello and "supports" not in hello
    assert set(hello) == set(FIXTURES["devices"]["pi_xvf3800"]["hello"])


# ─── the pin ──────────────────────────────────────────────────────────────

def test_an_unpinned_laptop_does_not_start(tmp_path, capsys):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[device]\nprofile = "generic_pc"\n[satellite]\nroom_id = "study"\n'
                        'domovoi_url = "ws://127.0.0.1:6370"\n', encoding="utf-8")
    assert client.main(["--config", str(cfg_path)]) == engine_platform.EXIT_UNPINNED
    assert "no pinned Domovoi server identity" in capsys.readouterr().err


def test_a_pinned_laptop_passes_the_check():
    ok, detail = pc_engine.require_pin(pc_cfg())
    assert ok and FP in detail


def test_an_unpinned_laptop_never_records_the_first_server_it_meets(monkeypatch):
    """A laptop's check always has a pin to hold the core to, so trust on
    first use can never be what decides."""
    seen = {}

    def verify(http_base, *, expected_fingerprint, opener=None):
        seen["expected"] = expected_fingerprint
        return expected_fingerprint
    monkeypatch.setattr(client.server_identity if hasattr(client, "server_identity") else
                        __import__("satellite.server_identity", fromlist=["x"]), "verify_server", verify)
    assert client._verify_server_identity(pc_cfg())
    assert seen["expected"] == FP


# ─── the approval code: on the screen, never in the log ───────────────────

class Tty(io.StringIO):
    def __init__(self, tty: bool):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def test_the_code_goes_to_a_terminal_and_nowhere_else():
    tty, pipe = Tty(True), Tty(False)
    assert pc_engine.show_code_on_tty("482913", "Study", stream=tty) is True
    assert "482913" in tty.getvalue() and "Settings → Satellites" in tty.getvalue()
    assert pc_engine.show_code_on_tty("482913", "Study", stream=pipe) is False
    assert pipe.getvalue() == ""


def test_awaiting_approval_shows_the_code_and_logs_it_masked(monkeypatch, caplog):
    sat = pc_sat()
    shown = []
    monkeypatch.setattr(pc_engine, "show_code_on_tty", lambda code, room, **kw: shown.append((code, room)))
    monkeypatch.setattr(client, "_setup_status", lambda *a: None)
    caplog.set_level(logging.DEBUG)
    sat._handle_text_frame({"type": "error", "reason": "awaiting_approval", "code": "482913"})
    assert shown == [("482913", "Study")]
    assert sat.control_approval_code() == (True, "482913")
    assert sat.control_status()["connection"] == "awaiting_approval"
    assert "482913" not in caplog.text, "the log keeps the code masked"


def test_a_parked_laptop_stays_parked_and_keeps_its_code_through_its_retries(monkeypatch):
    """A parked device redials every few seconds; the tray must not flicker
    and --approval-code must not miss the code between two attempts."""
    sat = pc_sat()
    monkeypatch.setattr(client, "_setup_status", lambda *a: None)
    monkeypatch.setattr(pc_engine, "show_code_on_tty", lambda *a, **k: None)
    sat._handle_text_frame({"type": "error", "reason": "awaiting_approval", "code": "482913"})
    sat._set_conn("connecting")          # the next attempt
    assert sat.control_status()["connection"] == "awaiting_approval"
    sat._conn_state = "connecting"       # even if something else said so
    assert sat.control_approval_code() == (True, "482913")
    sat._set_conn("offline", "connection refused")
    assert sat.control_status()["connection"] == "offline"
    assert sat.control_approval_code()[0] is True, "still waiting: the code is still the code"


def test_a_refused_laptop_says_so_in_its_status(monkeypatch):
    sat = pc_sat()
    sat._handle_text_frame({"type": "error", "reason": "pairing_rejected",
                            "message": "this room is paired to another device"})
    st = sat.control_status()
    assert st["connection"] == "refused" and "another device" in st["reason"]


def test_ready_clears_the_code():
    sat = pc_sat()
    sat._approval_code = "482913"
    sat._conn_state = "awaiting_approval"
    sat._start_voice_input = lambda fatal: False
    sat._reopen_voice_gate = lambda: None
    sat._note_session_accepted()
    assert sat.control_approval_code() == (False, None) or sat.control_approval_code()[0] is False
    assert sat.control_status()["connection"] == "connected"


# ─── mute ─────────────────────────────────────────────────────────────────

class Stream:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True

    def close(self):
        pass


def test_mute_closes_the_stream_survives_a_restart_and_unmute_reopens_it():
    sat = pc_sat()
    stream = Stream()
    sat._input_stream = stream
    sat._voice_input_started = True
    opened = []
    sat._start_mic = lambda: opened.append(1) or setattr(sat, "_input_stream", Stream())
    sat._set_muted(True)
    assert stream.stopped and sat._input_stream is None
    assert pc_engine.MUTED_SIDECAR.exists()
    assert sat._health_mic_expected() is False, "a muted mic is not a dead one"
    assert sat.control_status()["mic"] == "muted"
    again = pc_sat()
    assert again._muted is True, "kept across restarts"
    sat._set_muted(False)
    assert opened == [1] and not pc_engine.MUTED_SIDECAR.exists()
    assert sat._health_mic_expected() is True


def test_a_muted_laptop_starts_its_mic_thread_but_not_the_stream(monkeypatch):
    pc_engine.write_muted(True)
    sat = pc_sat()
    opened, threads = [], []
    sat._start_mic = lambda: opened.append(1)
    monkeypatch.setattr(client.threading, "Thread",
                        lambda **kw: types.SimpleNamespace(start=lambda: threads.append(kw["name"])))
    assert sat._start_voice_input(fatal=True) is True
    assert opened == [] and threads == ["mic"]


def test_a_pi_never_reads_the_mute_sidecar():
    pc_engine.write_muted(True)
    sat = client.Satellite(client.Config.from_toml({"device": {"profile": "xvf3800_usb"}}))
    assert sat._muted is False


# ─── "ask me" ─────────────────────────────────────────────────────────────

def _no_threads(monkeypatch, started):
    class T:
        def __init__(self, target=None, args=(), kwargs=None, **kw):
            self.target, self.args, self.name = target, args, kw.get("name")

        def start(self):
            # threading.Thread is one object everywhere: the approval's own
            # worker (satellite/control.py) runs; the client's are recorded.
            if (self.name or "").startswith("approved-"):
                self.target(*self.args)
                return
            started.append(self.name)
    monkeypatch.setattr(client.threading, "Thread", T)


@pytest.mark.parametrize("frame,kind", [
    ({"type": "upgrade", "expected_sha": "9c1d2aa"}, "update"),
    ({"type": "restart"}, "restart"),
    ({"type": "set_config", "changes": {"wake.threshold": 0.6}}, "config"),
    ({"type": "set_wake_word", "slug": "hello_domo"}, "wake_word"),
])
def test_ask_me_holds_what_the_core_pushes_until_the_person_says_yes(monkeypatch, frame, kind):
    sat = pc_sat(pc={"permissions": "ask"})
    started: list = []
    _no_threads(monkeypatch, started)
    ran = []
    monkeypatch.setattr(sat, "_apply_upgrade", lambda p: ran.append("update"))
    monkeypatch.setattr(sat, "_deferred_pi_action", lambda a, arg=None: ran.append("restart"))
    monkeypatch.setattr(sat, "_apply_config_changes", lambda c: ran.append("config"))
    monkeypatch.setattr(sat, "_apply_wake_switch", lambda s, t=None: ran.append("wake_word"))
    sat._handle_text_frame(frame)
    assert started == [] and ran == []
    pending = sat.control_status()["pending"]
    assert [p["kind"] for p in pending] == [kind]
    got = control.handle(sat, {"verb": "approve", "action": pending[0]["id"]})
    assert got["ok"] and got["done"][0]["kind"] == kind
    deadline = time.monotonic() + 3
    while not ran and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ran == [kind], "approved: the very action the core asked for runs"


def test_automatic_runs_what_the_core_pushes_at_once(monkeypatch):
    sat = pc_sat()
    started: list = []
    _no_threads(monkeypatch, started)
    sat._handle_text_frame({"type": "upgrade", "expected_sha": "9c1d2aa"})
    assert started == ["self-upgrade"] and sat.control_status()["pending"] == []


def test_a_spoken_restart_is_held_too(monkeypatch):
    sat = pc_sat(pc={"permissions": "ask"})
    started: list = []
    _no_threads(monkeypatch, started)
    sat.stop_playback.set()
    sat._handle_text_frame({"type": "response_end", "pi_action": "restart"})
    assert "pi-action-restart" not in started
    assert [p["kind"] for p in sat.control_status()["pending"]] == ["restart"]


def test_a_pi_is_never_held(monkeypatch):
    sat = client.Satellite(client.Config.from_toml({"device": {"profile": "xvf3800_usb"},
                                                    "pc": {"permissions": "ask"}}))
    started: list = []
    _no_threads(monkeypatch, started)
    sat._handle_text_frame({"type": "restart"})
    assert started == ["self-restart"]


# ─── what the core may not change, and what a laptop never runs ───────────

def test_the_core_may_not_change_a_laptop_s_trust_settings(monkeypatch):
    sat = pc_sat()
    client.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    client.CONFIG_PATH.write_text(
        f'[device]\nprofile = "generic_pc"\n[satellite]\nroom_id = "study"\n'
        f'server_fingerprint = "{FP}"\n[pc]\npermissions = "ask"\n', encoding="utf-8")
    restarts = []
    sat._restart_service = lambda: restarts.append(1) or True
    sat._apply_config_changes({
        "satellite.server_fingerprint": "SHA256:" + "A" * 43,
        "pc.permissions": "automatic",
        "device.profile": "xvf3800_usb",
        "wake.threshold": 0.6,
    })
    saved = client.CONFIG_PATH.read_text(encoding="utf-8")
    assert FP in saved and 'permissions = "ask"' in saved and 'profile = "generic_pc"' in saved
    assert "threshold = 0.6" in saved and restarts == [1]


def test_nothing_but_protected_keys_changes_nothing():
    kept, refused = pc_engine.filter_config_changes({"pc.permissions": "automatic"})
    assert kept == {} and refused == ["pc.permissions"]
    kept, refused = pc_engine.filter_config_changes({"device.full_duplex": True, "wake.threshold": 0.6})
    assert kept == {"wake.threshold": 0.6} and refused == ["device.full_duplex"]


def test_a_laptop_never_syncs_its_clock_through_a_root_helper(monkeypatch):
    sat = pc_sat()
    monkeypatch.setattr(client.threading, "Thread", lambda **kw: pytest.fail("no time sync on a laptop"))
    sat._time_synced_at = None
    sat._sync_time_with_server()


def test_a_laptop_restarts_itself_by_exiting_75():
    sat = pc_sat()
    assert sat._restart_service() is True
    assert sat.exit_code == engine_platform.EXIT_RESTART and sat.shutdown_event.is_set()


def test_an_upgrade_applies_no_plugin_payloads_on_a_laptop(monkeypatch):
    sat = pc_sat()
    from satellite import code_sync, plugin_sync

    monkeypatch.setattr(code_sync, "backup_tree", lambda *a: None)
    monkeypatch.setattr(code_sync, "sync_code", lambda *a, **k: {"manifest": {}, "downloaded": 0, "pruned": []})
    monkeypatch.setattr(client.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stderr="", stdout=""))
    monkeypatch.setattr(plugin_sync, "sync_plugin_payloads", lambda *a, **k: pytest.fail("no payloads"))
    restarts = []
    sat._restart_service = lambda: restarts.append(1) or True
    sat._apply_upgrade({"expected_sha": "9c1d2aa", "plugin_payloads": False})
    assert restarts == [1]


# ─── the control channel's view of the engine ─────────────────────────────

def test_the_status_has_exactly_the_fixture_s_fields():
    st = pc_sat().control_status()
    assert set(st) == set(CONTROL["status"])
    assert st["board"] == "linux_pc" and st["room"] == {"id": "study", "name": "Study"}
    assert st["server"]["pinned"] and st["dashboard"] == "http://127.0.0.1:6369"
    assert st["connection"] in control.CONNECTIONS
    assert st["mic"] in control.MIC_STATES and st["output"] in control.OUTPUT_STATES


def test_the_mic_live_indicator_follows_a_capture():
    sat = pc_sat()
    sat._voice_input_started = True
    sat._input_stream = Stream()
    assert sat.control_status()["mic"] == "listening"
    sat._leds.set_state("listening")     # the ring's word for "capturing"
    assert sat.control_status()["mic"] == "capturing"
    sat._leds.set_state("speaking")
    st = sat.control_status()
    assert st["mic"] == "listening" and st["output"] == "speaking"


def test_every_led_change_notifies_the_watchers():
    sat = pc_sat()
    notes = []
    sat._control_server = types.SimpleNamespace(notify=lambda: notes.append(1))
    sat._leds.set_state("listening")
    sat._leds.set_state_unless("listening", "idle")
    sat._leds.set_state("idle")
    assert len(notes) == 2


def test_logs_path_names_the_journal_the_unit_writes_to(monkeypatch):
    monkeypatch.setenv("DOMOVOI_SERVICE_SCOPE", "user")
    got = pc_sat().control_logs_path()
    assert got["kind"] == "journal" and "--user" in got["hint"]


# ─── the command line ─────────────────────────────────────────────────────

class FakeClient:
    answers: dict = {}
    calls: list = []

    def __init__(self, path, timeout=5.0):
        self.path = path

    def call(self, verb, **args):
        FakeClient.calls.append((verb, args))
        a = FakeClient.answers.get(verb)
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def fake_client(monkeypatch):
    FakeClient.calls = []
    FakeClient.answers = {}
    monkeypatch.setattr(control, "ControlClient", FakeClient)
    return FakeClient


def test_cli_status_prints_a_summary(fake_client, capsys):
    status = dict(CONTROL["engine"]["status"], ok=True, verb="status")
    fake_client.answers["status"] = status
    assert client.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert "Kid's Room" in out and "awaiting_approval" in out and FP in out


def test_cli_approval_code_prints_only_the_code(fake_client, capsys):
    fake_client.answers["code"] = {"ok": True, "verb": "code", "waiting": True, "code": "482913"}
    assert client.main(["--approval-code"]) == 0
    assert capsys.readouterr().out.strip() == "482913"
    fake_client.answers["code"] = {"ok": True, "verb": "code", "waiting": False, "code": None}
    assert client.main(["--approval-code"]) == 2


def test_cli_mute_and_approve(fake_client, capsys):
    fake_client.answers["mute"] = {"ok": True, "verb": "mute", "mic": "muted"}
    assert client.main(["--mute"]) == 0
    fake_client.answers["approve"] = {"ok": True, "verb": "approve",
                                      "done": [{"id": "a1", "kind": "update", "summary": "s", "received_at": 1}]}
    assert client.main(["--approve", "all"]) == 0
    assert ("approve", {"action": "all"}) in fake_client.calls


def test_cli_without_an_engine_says_so(fake_client, capsys):
    fake_client.answers["status"] = ConnectionError("the satellite engine isn't answering")
    assert client.main(["--status"]) == 3
    assert "Is the satellite running?" in capsys.readouterr().err


def test_the_installer_waits_and_prints_the_code_once(fake_client):
    states = iter([
        ConnectionError("not yet"),
        {"ok": True, "connection": "connecting"},
        {"ok": True, "connection": "awaiting_approval"},
        {"ok": True, "connection": "awaiting_approval"},
        {"ok": True, "connection": "connected", "room": {"id": "study", "name": "Study"}},
    ])

    def call(self, verb, **args):
        if verb == "code":
            return {"ok": True, "waiting": True, "code": "482913"}
        nxt = next(states)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
    fake_client.call = call
    out = io.StringIO()
    clock = iter(range(1000))
    got = pc_engine.wait_for_approval(Path("x"), timeout=100, out=out, poll=0,
                                      clock=lambda: next(clock), sleep=lambda s: None)
    assert got == "connected"
    text = out.getvalue()
    assert text.count("482913") == 1 and "now the Study" in text


# ─── the requirements install on an x86-64 laptop ─────────────────────────

def _requirements_for(machine: str) -> list[str]:
    from packaging.markers import Marker

    out = []
    for line in (ROOT / "satellite" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        spec, _, marker = line.partition(";")
        if marker and not Marker(marker).evaluate({"platform_machine": machine}):
            continue
        out.append(spec.strip())
    return out


def test_an_x86_64_laptop_gets_no_spidev_and_no_pi_onnxruntime_pin():
    reqs = _requirements_for("x86_64")
    assert not any(r.startswith("spidev") for r in reqs)
    assert "onnxruntime==1.20.0" not in reqs and any(r.startswith("onnxruntime>=") for r in reqs)


def test_a_pi_keeps_spidev_and_the_pin():
    reqs = _requirements_for("aarch64")
    assert "spidev>=3.6" in reqs and "onnxruntime==1.20.0" in reqs
    assert sum(r.startswith("onnxruntime") for r in reqs) == 1
