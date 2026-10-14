"""The Windows supervisor (satellite/win_service.py): the restart rules the
Linux unit carries (RestartForceExitStatus=75, RestartPreventExitStatus=78,
Restart=on-failure with a pause), the engine's environment, the rotating
log. No real engine is started: the spawn is a fake."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from satellite import win_service as ws


@pytest.mark.parametrize("code,stop,want", [
    (75, False, "restart"), (78, False, "stop"), (0, False, "stop"),
    (1, False, "crashed"), (-1, False, "crashed"), (75, True, "stop"), (1, True, "stop"),
])
def test_what_an_exit_means(code, stop, want):
    assert ws.decide(code, stop_asked=stop) == want


def test_the_crash_pause_grows_then_holds():
    assert [ws.crash_delay(n) for n in range(1, 7)] == [5.0, 10.0, 30.0, 60.0, 60.0, 60.0]


def test_the_engine_lives_in_the_install_folder_not_the_person_s_profile(tmp_path):
    base = {"USERPROFILE": r"C:\Users\kid", "HOMEDRIVE": "C:", "HOMEPATH": r"\Users\kid",
            "PATH": "x", "DOMOVOI_DEBUG_MIC_DIR": "m", "DOMOVOI_DEBUG_SOMETHING": "y"}
    env = ws.engine_env(tmp_path, base)
    assert env["USERPROFILE"] == str(tmp_path)
    assert "HOMEDRIVE" not in env and "HOMEPATH" not in env
    assert env["DOMOVOI_SERVICE_SCOPE"] == "windows" and env["DOMOVOI_LOG_DIR"] == str(tmp_path / "logs")
    assert env["PYTHONUNBUFFERED"] == "1" and env["PYTHONIOENCODING"] == "utf-8"
    # Only the two test hooks pass through, and only if they were set.
    assert env["DOMOVOI_DEBUG_MIC_DIR"] == "m" and "DOMOVOI_DEBUG_SOMETHING" not in env
    assert "DOMOVOI_DEBUG_AUDIO_SINK" not in ws.engine_env(tmp_path, {"PATH": "x"})


def test_the_log_rotates_and_keeps_three(tmp_path):
    tmp_path = tmp_path / "logs"
    log = ws.RotatingLog(tmp_path / "satellite.log", max_bytes=100, backups=3)
    for i in range(20):
        log.write(f"line {i:03d} {'x' * 30}\n".encode())
    log.close()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["satellite.log", "satellite.log.1", "satellite.log.2", "satellite.log.3"]
    assert all(p.stat().st_size <= 100 for p in tmp_path.iterdir())


class FakeStop:
    def __init__(self, after_waits=None):
        self.set_ = False
        self.waits = 0
        self.after = after_waits
        self.slept = []

    def is_set(self):
        return self.set_

    def wait(self, timeout=None):
        self.waits += 1
        if timeout and timeout > 1:
            self.slept.append(timeout)
        if self.after is not None and self.waits >= self.after:
            self.set_ = True
        return self.set_


class FakeProc:
    pid = 4242

    def __init__(self, code, polls=1):
        self.code, self.left, self.terminated = code, polls, False
        self.stdout = None

    def poll(self):
        if self.left > 0:
            self.left -= 1
            return None
        return self.code

    def wait(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.left = 0


def _sup(tmp_path, codes, stop, clock=None):
    procs = [FakeProc(c) for c in codes]
    spawned = []

    def spawn():
        p = procs[len(spawned)]
        spawned.append(p)
        return p

    log = ws.RotatingLog(tmp_path / "logs" / "supervisor.log")
    elog = ws.RotatingLog(tmp_path / "logs" / "satellite.log")
    sup = ws.Supervisor(tmp_path, stop=stop, log=log, engine_log=elog, spawn=spawn,
                        clock=clock or (lambda: 0.0))
    return sup, spawned


def test_a_restart_asked_for_is_at_once_and_a_crash_waits(tmp_path):
    stop = FakeStop()
    sup, spawned = _sup(tmp_path, [75, 1, 1, 0], stop)
    assert sup.run() == 0
    assert len(spawned) == 4
    assert stop.slept == [5.0, 10.0], "75 restarts at once; two crashes in a row pause 5 then 10 s"
    assert (tmp_path / "logs" / "supervisor-status.json").exists()


def test_unpinned_stays_stopped_and_says_why(tmp_path):
    import json

    sup, spawned = _sup(tmp_path, [78], FakeStop())
    assert sup.run() == 0 and len(spawned) == 1
    status = json.loads((tmp_path / "logs" / "supervisor-status.json").read_text())
    assert status["state"] == "stopped" and "pinned" in status["reason"]


def test_a_stop_asked_for_ends_the_engine_and_the_supervisor(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "STOP_GRACE_SEC", 0.0)
    stop = FakeStop(after_waits=1)
    proc = FakeProc(0, polls=10_000)
    sup = ws.Supervisor(tmp_path, stop=stop, log=ws.RotatingLog(tmp_path / "s.log"),
                        engine_log=ws.RotatingLog(tmp_path / "e.log"), spawn=lambda: proc)
    assert sup.run() == 0
    assert proc.terminated, "an engine that ignores the stop is ended after the grace"


def test_a_healthy_run_resets_the_pause(tmp_path):
    t = {"now": 0.0}

    def clock():
        t["now"] += 400.0                     # each engine runs 400 s
        return t["now"]

    stop = FakeStop()
    sup, _ = _sup(tmp_path, [1, 1, 0], stop, clock=clock)
    sup.run()
    assert stop.slept == [5.0, 5.0]


def test_the_install_folder_is_the_python_s_parent(monkeypatch, tmp_path):
    exe = tmp_path / "python" / "pythonw.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(ws.sys, "executable", str(exe))
    assert ws.default_root() == tmp_path.resolve()
    assert ws.interpreter(tmp_path, windowed=False) == str(tmp_path / "python" / "python.exe")
