"""XVF3800 microphone DSP presets on the satellite (satellite/mic_dsp.py).

A fake ``xvf_host`` stands in for the chip: it keeps the five parameters,
answers a read with ``PARAM v`` and records every write.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import types

import pytest

from satellite import mic_dsp
from satellite.tests._client_import import import_client

FACTORY = {
    "PP_AGCONOFF": "1",
    "PP_AGCMAXGAIN": "1000",
    "PP_AGCGAIN": "1",
    "PP_MIN_NS": "0.15",
    "PP_MIN_NN": "0.5",
}


class FakeXvf:
    def __init__(self, values=None, *, refuse_plain=True):
        self.values = dict(values or FACTORY)
        self.calls: list[list[str]] = []
        self.refuse_plain = refuse_plain

    @property
    def writes(self):
        return [c[3:] for c in self.calls if len(c) > 4]

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.refuse_plain and argv[0] != "sudo":
            return 1, ""
        assert argv[:3] == ["sudo", "-n", "/opt/xvf3800/xvf_host"], argv
        param, *vals = argv[3:]
        if param not in self.values:
            return 1, ""
        if vals:
            self.values[param] = " ".join(vals)
            return 0, ""
        return 0, f"some banner\n{param} {self.values[param]}\n"

    def replug(self):
        self.values = dict(FACTORY)


def _dsp(tmp_path, fake):
    return mic_dsp.XvfDsp("/opt/xvf3800/xvf_host", runner=fake, state_file=tmp_path / "mic_dsp.json")


def test_each_preset_writes_exactly_its_allowlisted_rows(tmp_path):
    expected = {
        "factory": [],
        "B": [["PP_AGCMAXGAIN", "31.6"]],
        "C": [["PP_AGCMAXGAIN", "31.6"], ["PP_MIN_NS", "0.05"], ["PP_MIN_NN", "0.05"]],
        "D": [["PP_AGCMAXGAIN", "31.6"], ["PP_MIN_NS", "0.05"], ["PP_MIN_NN", "0.05"],
              ["PP_AGCONOFF", "0"], ["PP_AGCGAIN", "10"]],
    }
    assert {k: [[p, *v] for p, v in rows] for k, rows in mic_dsp.PRESETS.items()} == expected
    for name, rows in expected.items():
        fake = FakeXvf()
        state = _dsp(tmp_path / name, fake).apply(name)
        assert fake.writes == rows, name
        assert state["ok"] and state["preset"] == name
    # Nothing ever saves to the chip's flash.
    assert not any("SAVE_CONFIGURATION" in c for c in fake.calls)


def test_presets_do_not_stack_factory_values_go_back_first(tmp_path):
    fake = FakeXvf()
    dsp = _dsp(tmp_path, fake)
    dsp.apply("D")
    fake.calls.clear()
    dsp.apply("B")
    restored = [[p, FACTORY[p]] for p in mic_dsp.PARAMS]
    assert fake.writes == restored + [["PP_AGCMAXGAIN", "31.6"]]
    assert fake.values["PP_AGCONOFF"] == "1" and fake.values["PP_MIN_NS"] == "0.15"


@pytest.mark.parametrize("name", ["E", "winner", "b", "", "PP_AGCGAIN 0", None, 3])
def test_an_unknown_preset_is_refused_and_nothing_is_written(tmp_path, name):
    fake = FakeXvf()
    with pytest.raises(ValueError):
        _dsp(tmp_path, fake).apply(name if name is not None else 0)
    assert fake.calls == []


def test_factory_restores_the_start_up_values_exactly(tmp_path):
    fake = FakeXvf()
    dsp = _dsp(tmp_path, fake)
    dsp.apply("D")
    assert fake.values != FACTORY
    state = dsp.apply("factory")
    assert fake.values == FACTORY
    assert state["readback"]["PP_AGCMAXGAIN"] == [1000.0]
    assert state["factory"] == {p: [v] for p, v in FACTORY.items()}


def test_a_restart_while_a_preset_is_on_keeps_the_real_factory_values(tmp_path):
    fake = FakeXvf()
    _dsp(tmp_path, fake).apply("C")
    # The service restarts; the chip still holds C. The new process must not
    # read C's values as the factory ones.
    again = _dsp(tmp_path, fake)
    assert again.preset == "C"
    again.reapply("reconnect")
    again.apply("factory")
    assert fake.values == FACTORY
    saved = json.loads((tmp_path / "mic_dsp.json").read_text())
    assert saved == {"preset": "factory", "factory": {p: [v] for p, v in FACTORY.items()},
                     "dirty": False}


def test_the_preset_goes_back_on_after_a_usb_replug(tmp_path):
    fake = FakeXvf()
    dsp = _dsp(tmp_path, fake)
    dsp.apply("D")
    fake.replug()  # the array comes back with its firmware values
    state = dsp.reapply("mic reopened")
    assert fake.values["PP_AGCONOFF"] == "0" and fake.values["PP_AGCGAIN"] == "10"
    assert state["preset"] == "D" and state["readback"]["PP_AGCGAIN"] == [10.0]


def test_factory_on_an_untouched_chip_writes_nothing(tmp_path):
    fake = FakeXvf()
    _dsp(tmp_path, fake).reapply("reconnect")
    assert fake.writes == []


def test_readback_reports_what_the_chip_holds_and_logs_one_line(tmp_path, caplog):
    fake = FakeXvf()
    real_call = fake.__call__

    def stubborn(argv):
        # The chip keeps its own PP_MIN_NN whatever is written.
        if argv[3:4] == ["PP_MIN_NN"] and len(argv) > 4:
            fake.calls.append(list(argv))
            return 0, ""
        return real_call(argv)

    dsp = mic_dsp.XvfDsp("/opt/xvf3800/xvf_host", runner=stubborn, state_file=tmp_path / "s.json")
    with caplog.at_level(logging.INFO, logger="satellite.mic_dsp"):
        state = dsp.apply("C")
    assert state["type"] == "mic_dsp_state"
    assert state["applied"]["PP_MIN_NN"] == ["0.05"]
    assert state["readback"]["PP_MIN_NN"] == [0.5]  # the truth, not the request
    assert state["readback"]["PP_AGCMAXGAIN"] == [31.6]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mic_dsp: at=")]
    assert len(lines) == 1
    assert "preset=C" in lines[0] and "PP_MIN_NN=0.5" in lines[0] and "PP_AGCMAXGAIN=31.6" in lines[0]


def test_an_array_that_does_not_answer_reports_an_error(tmp_path):
    dsp = mic_dsp.XvfDsp("/opt/xvf3800/xvf_host", runner=lambda argv: (1, ""),
                         state_file=tmp_path / "s.json")
    state = dsp.apply("B")
    assert state["ok"] is False and "did not answer" in state["error"]


def test_marker_is_escaped_capped_and_logged(tmp_path, caplog):
    assert mic_dsp.clean_mark("  block 2, 1m \n") == "block 2, 1m"
    assert mic_dsp.clean_mark("a\x1b[31mred\nb\rc") == "a\\x1b[31mred\\x0ab\\x0dc"
    assert mic_dsp.clean_mark("x\u2028y\x85z") == r"x\u2028y\x85z"
    assert len(mic_dsp.clean_mark("z" * 500)) == mic_dsp.MARK_MAX
    assert mic_dsp.clean_mark(None) == "" and mic_dsp.clean_mark(["a"]) == ""
    dsp = _dsp(tmp_path, FakeXvf())
    with caplog.at_level(logging.INFO, logger="satellite.mic_dsp"):
        assert dsp.mark("block 2\n1m") == "block 2\\x0a1m"
    assert any("mic_dsp mark: at=" in r.getMessage() and "text=block 2\\x0a1m" in r.getMessage()
               for r in caplog.records)


# ─── the client ─────────────────────────────────────────────────────────


client = import_client()


def _cfg(profile):
    return client.Config.from_toml({"device": {"profile": profile}})


def test_only_an_xvf3800_with_xvf_host_declares_mic_dsp(monkeypatch, tmp_path):
    binary = tmp_path / "xvf_host"
    binary.write_text("")
    cfg = _cfg("xvf3800_usb")
    monkeypatch.setattr(cfg, "leds_xvf_host_path", str(binary))
    sat = client.Satellite(cfg)
    assert sat._hello_frame()["mic_dsp"] is True
    monkeypatch.setattr(cfg, "leds_xvf_host_path", str(tmp_path / "missing"))
    assert client.Satellite(cfg)._hello_frame()["mic_dsp"] is False
    assert client.Satellite(_cfg("respeaker_2mic_hat"))._hello_frame()["mic_dsp"] is False


def _sat_with_fake(tmp_path, features=("mic_dsp",)):
    sat = client.Satellite(_cfg("respeaker_2mic_hat"))
    fake = FakeXvf()
    sat._mic_dsp = _dsp(tmp_path, fake)
    sat._core_features = frozenset(features)
    sent: list[dict] = []
    sat._emit_text = lambda payload: sent.append(payload) or True

    class Inline:
        def __init__(self, target, daemon=None, name=None):
            self.target = target

        def start(self):
            self.target()

    return sat, fake, sent, Inline


def test_set_mic_dsp_applies_and_reports_the_readback(tmp_path, monkeypatch):
    sat, fake, sent, inline = _sat_with_fake(tmp_path)
    monkeypatch.setattr(client.threading, "Thread", inline)
    sat._mic_dsp_set("B")
    assert fake.values["PP_AGCMAXGAIN"] == "31.6"
    assert sent and sent[-1]["type"] == "mic_dsp_state"
    assert sent[-1]["readback"]["PP_AGCMAXGAIN"] == [31.6]


def test_set_mic_dsp_refuses_an_unknown_name(tmp_path, monkeypatch):
    sat, fake, sent, inline = _sat_with_fake(tmp_path)
    monkeypatch.setattr(client.threading, "Thread", inline)
    sat._mic_dsp_set("PP_AGCONOFF 0")
    assert fake.calls == [] and sent == []


def test_an_older_core_is_not_sent_the_state(tmp_path, monkeypatch):
    sat, fake, sent, inline = _sat_with_fake(tmp_path, features=())
    monkeypatch.setattr(client.threading, "Thread", inline)
    sat._mic_dsp_set("B")
    assert fake.values["PP_AGCMAXGAIN"] == "31.6"
    assert sent == []


def test_a_reopened_mic_puts_the_preset_back(tmp_path, monkeypatch):
    sat, fake, sent, inline = _sat_with_fake(tmp_path)
    monkeypatch.setattr(client.threading, "Thread", inline)
    sat._mic_dsp_set("D")
    fake.replug()
    sat._voice_input_started = True
    monkeypatch.setattr(sat, "_stop_mic", lambda: None)
    monkeypatch.setattr(sat, "_start_mic", lambda: None)
    sat._mic_frames_total = 0
    sat._health_reopen_mic("capture went silent", 0.0)
    assert fake.values["PP_AGCONOFF"] == "0"
    assert sent[-1]["preset"] == "D"


def test_the_frames_are_routed(tmp_path, monkeypatch):
    sat, fake, sent, inline = _sat_with_fake(tmp_path)
    monkeypatch.setattr(client.threading, "Thread", inline)
    seen = types.SimpleNamespace(set=None, mark=None)
    monkeypatch.setattr(sat, "_mic_dsp_set", lambda p: setattr(seen, "set", p))
    monkeypatch.setattr(sat._mic_dsp, "mark", lambda t: setattr(seen, "mark", t))
    sat._handle_text_frame({"type": "set_mic_dsp", "preset": "C"})
    sat._handle_text_frame({"type": "mic_dsp_mark", "text": "block 2, 1m"})
    assert seen.set == "C" and seen.mark == "block 2, 1m"


# ─── follow-up: saving first, hung calls, inert at factory ───────────────


def test_a_failed_save_refuses_a_preset_and_writes_nothing(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")  # the state file's parent is a file: every save fails
    fake = FakeXvf()
    dsp = mic_dsp.XvfDsp("/opt/xvf3800/xvf_host", runner=fake, state_file=blocker / "mic_dsp.json")
    state = dsp.apply("B")
    assert fake.writes == [] and fake.values == FACTORY
    assert state["ok"] is False and "could not save" in state["error"]
    assert state["preset"] == "factory" and dsp.active is False


class _Crash(BaseException):
    pass


def test_a_crash_between_the_save_and_the_writes_still_restores_factory(tmp_path):
    fake = FakeXvf()
    writes = []

    def dies_mid_write(argv):
        if len(argv) > 4:
            writes.append(argv)
            if len(writes) == 3:
                raise _Crash()
        return fake(argv)

    first = mic_dsp.XvfDsp("/opt/xvf3800/xvf_host", runner=dies_mid_write,
                           state_file=tmp_path / "mic_dsp.json")
    with pytest.raises(_Crash):
        first.apply("D")
    assert fake.values != FACTORY  # part of D reached the chip
    # The restart: the saved D means the chip's values are not read in as factory.
    again = _dsp(tmp_path, fake)
    assert again.preset == "D" and again.active
    again.apply("factory")
    assert fake.values == FACTORY


def test_a_hung_call_has_its_process_group_killed(monkeypatch):
    seen = {}

    class HungProc:
        pid = 4242
        returncode = None

        def __init__(self, argv, **kw):
            seen["kw"] = kw

        def communicate(self, timeout=None):
            raise mic_dsp.subprocess.TimeoutExpired("xvf_host", timeout)

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(mic_dsp.subprocess, "Popen", HungProc)
    monkeypatch.setattr(mic_dsp.sys, "platform", "linux")
    killed = []
    monkeypatch.setattr(mic_dsp.os, "killpg", lambda pid, sig: killed.append((pid, sig)), raising=False)
    with pytest.raises(mic_dsp.subprocess.TimeoutExpired):
        mic_dsp._run(["sudo", "-n", "/opt/xvf3800/xvf_host", "PP_AGCGAIN"])
    assert seen["kw"]["start_new_session"] is True
    assert killed == [(4242, mic_dsp.signal.SIGTERM)]


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_a_real_hung_tool_and_its_child_are_gone_after_the_timeout(tmp_path, monkeypatch):
    pid_file = tmp_path / "child.pid"
    tool = tmp_path / "xvf_host"
    tool.write_text(
        "#!/bin/sh\nsleep 60 &\necho $! > " + str(pid_file) + "\nwait\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setattr(mic_dsp, "CALL_TIMEOUT", 0.5)
    with pytest.raises(mic_dsp.subprocess.TimeoutExpired):
        mic_dsp._run([str(tool), "PP_AGCGAIN"])
    child = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the hung tool's child is still running")


def test_a_second_change_while_one_holds_the_array_is_skipped(tmp_path, monkeypatch):
    fake = FakeXvf()
    dsp = _dsp(tmp_path, fake)
    monkeypatch.setattr(mic_dsp, "LOCK_TIMEOUT", 0.05)
    dsp._lock.acquire()
    try:
        started = time.monotonic()
        assert dsp.apply("B") is None
        assert time.monotonic() - started < 2
    finally:
        dsp._lock.release()
    assert fake.calls == []


def test_ready_at_factory_makes_no_xvf_host_call_and_writes_no_file(tmp_path, monkeypatch):
    sat, fake, sent, inline = _sat_with_fake(tmp_path)
    monkeypatch.setattr(client.threading, "Thread", inline)
    monkeypatch.setattr(client, "_promote_pending_server", lambda path: None)
    sat._sync_time_with_server = lambda: None
    sat._note_session_accepted = lambda: None
    sat._handle_text_frame({"type": "ready", "features": ["mic_dsp"]})
    assert fake.calls == []
    assert not (tmp_path / "mic_dsp.json").exists()
    assert sent and sent[-1]["type"] == "mic_dsp_state" and sent[-1]["preset"] == "factory"


def test_a_reopened_mic_at_factory_touches_nothing(tmp_path, monkeypatch):
    sat, fake, sent, inline = _sat_with_fake(tmp_path)
    monkeypatch.setattr(client.threading, "Thread", inline)
    sat._voice_input_started = True
    monkeypatch.setattr(sat, "_stop_mic", lambda: None)
    monkeypatch.setattr(sat, "_start_mic", lambda: None)
    sat._mic_frames_total = 0
    sat._health_reopen_mic("capture went silent", 0.0)
    assert fake.calls == [] and sent == []


# ─── follow-up 2: factory values are never replaced by a preset's ─────────


def test_a_crash_mid_restore_then_restart_then_b_keeps_factory(tmp_path):
    fake = FakeXvf()
    _dsp(tmp_path, fake).apply("C")
    writes = []

    def dies_mid_restore(argv):
        if len(argv) > 4:
            writes.append(argv)
            if len(writes) == 2:
                raise _Crash()
        return fake(argv)

    restoring = mic_dsp.XvfDsp("/opt/xvf3800/xvf_host", runner=dies_mid_restore,
                               state_file=tmp_path / "mic_dsp.json")
    with pytest.raises(_Crash):
        restoring.apply("factory")
    assert fake.values["PP_MIN_NN"] == "0.05"  # C's values are still on the chip
    again = _dsp(tmp_path, fake)
    assert again.preset == "factory" and again.active  # the restore is still owed
    again.apply("B")
    saved = json.loads((tmp_path / "mic_dsp.json").read_text())
    assert saved["factory"] == {p: [v] for p, v in FACTORY.items()}
    assert fake.values == {**FACTORY, "PP_AGCMAXGAIN": "31.6"}
    again.apply("factory")
    assert fake.values == FACTORY and not again.active


def test_a_failed_restore_stays_dirty_and_the_retry_restores_factory(tmp_path):
    fake = FakeXvf()
    refuse = {"on": True}

    def refuses_one_restore(argv):
        if refuse["on"] and argv[3:] == ["PP_MIN_NS", FACTORY["PP_MIN_NS"]]:
            fake.calls.append(list(argv))
            return 1, ""
        return fake(argv)

    dsp = mic_dsp.XvfDsp("/opt/xvf3800/xvf_host", runner=refuses_one_restore,
                         state_file=tmp_path / "mic_dsp.json")
    dsp.apply("C")
    state = dsp.apply("factory")
    assert state["ok"] is False and dsp.active
    assert json.loads((tmp_path / "mic_dsp.json").read_text())["dirty"] is True
    # A preset now must not take the chip's C values for factory.
    dsp.apply("B")
    refuse["on"] = False
    retry = _dsp(tmp_path, fake).reapply("reconnect")  # a restart, then ready
    assert retry["preset"] == "B"
    _dsp(tmp_path, fake).apply("factory")
    assert fake.values == FACTORY
    saved = json.loads((tmp_path / "mic_dsp.json").read_text())
    assert saved == {"preset": "factory", "factory": {p: [v] for p, v in FACTORY.items()},
                     "dirty": False}
