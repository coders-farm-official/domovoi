"""Friend Houses: replays of the round 6 security audit's proofs of concept.

Each test is named for its card (FH-n) and does what the card's PoC did,
against the fixed code. Pairing's FH-1 replay is in
test_friend_houses_pairing.py, next to the protocol it attacks.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from domovoi.friend_houses import service as svc
from domovoi.friend_houses import tor_config as tc
from domovoi.tests.friend_houses_testkit import no_tasks_left_behind  # noqa: F401  (autouse)

# ─── FH-2: one large message ended every inbound event ───────────────────


def _printer(*lines: str) -> list[str]:
    """A stand-in listener: prints these lines on stdout, then exits. Each
    is a Python expression, so a long line is built in that process (a
    command line is capped at 32 K on Windows)."""
    script = "import json, sys\n" + "".join(
        f"sys.stdout.write({line} + '\\n'); sys.stdout.flush()\n" for line in lines)
    return [sys.executable, "-c", script]


@pytest.fixture
def listener(monkeypatch, tmp_path):
    got: list[dict] = []
    monkeypatch.setitem(svc._LISTENER, "proc", None)
    monkeypatch.setitem(svc._LISTENER, "task", None)
    monkeypatch.setitem(svc._LISTENER, "stopping", False)
    monkeypatch.setattr(svc, "handle_event", lambda e: got.append(e))
    return got, tc.TorFolder(tmp_path / "fh")


async def _run_until(got, n, folder, timeout=20.0):
    task = asyncio.get_running_loop().create_task(svc._run_listener(folder))
    try:
        for _ in range(int(timeout / 0.05)):
            if len(got) >= n:
                break
            await asyncio.sleep(0.05)
    finally:
        svc._LISTENER["stopping"] = True
        proc = svc._LISTENER["proc"]
        if proc is not None and proc.returncode is None:
            proc.kill()
        await asyncio.wait_for(task, 10)


async def test_fh2_a_shared_list_sized_event_is_delivered(listener, monkeypatch) -> None:
    # The card's honest case: a 200-item list of 200-character items is a
    # 48,598-byte envelope, a ~97 KB line. It used to raise "chunk is
    # longer than limit" and end the reader for good.
    got, folder = listener
    big = 'json.dumps({"event": "message", "envelope": "ab" * 63 * 1024})'
    small = 'json.dumps({"event": "message", "envelope": "cd"})'
    monkeypatch.setattr(svc, "_listener_command", lambda folder: _printer(big, small))
    await _run_until(got, 2, folder)
    assert [len(e["envelope"]) for e in got[:2]] == [2 * 63 * 1024, 2]


async def test_fh2_a_line_over_any_limit_restarts_the_listener(listener, monkeypatch, caplog) -> None:
    got, folder = listener
    starts: list[int] = []

    def command(folder):
        starts.append(1)
        if len(starts) == 1:
            return _printer(f'"x" * {svc.EVENT_LINE_MAX + 10}')
        return _printer(repr(json.dumps({"event": "after"})))

    monkeypatch.setattr(svc, "_listener_command", command)
    monkeypatch.setattr(svc.asyncio, "sleep", _fast_sleep)
    await _run_until(got, 1, folder)
    assert got[0] == {"event": "after"} and len(starts) >= 2
    assert "events stopped" in caplog.text  # the reader hit the limit, and recovered


_real_sleep = asyncio.sleep


async def _fast_sleep(delay, *a, **k):
    return await _real_sleep(min(delay, 0.01), *a, **k)


async def test_fh2_a_failed_listener_task_doesnt_stop_off_from_working(monkeypatch) -> None:
    async def died():
        raise ValueError("Separator is found, but chunk is longer than limit")

    task = asyncio.get_running_loop().create_task(died())
    await asyncio.sleep(0)
    monkeypatch.setitem(svc._LISTENER, "proc", None)
    monkeypatch.setitem(svc._LISTENER, "task", task)
    await svc._stop_listener()  # used to re-raise the task's error, every time
    assert svc._LISTENER["task"] is None


@pytest.mark.skipif(sys.platform == "win32", reason="Friend Houses runs on Linux")
async def test_fh2_off_stops_tor_even_if_stopping_the_listener_fails(monkeypatch, tmp_path) -> None:
    from domovoi import egress
    from domovoi.config import settings
    from domovoi.friend_houses import tor_runtime as rt

    calls: list[str] = []

    async def boom():
        raise ValueError("listener task failed")

    async def stop():
        calls.append("stop")

    async def running():
        return "running"

    monkeypatch.setattr(svc, "_root", lambda: tmp_path / "friend-houses")
    (tmp_path / "friend-houses").mkdir()
    monkeypatch.setattr(svc, "_stop_listener", boom)
    monkeypatch.setattr(svc, "_stop_retries", lambda: calls.append("retries"))
    monkeypatch.setattr(rt, "stop", stop)
    monkeypatch.setattr(rt, "state", running)
    monkeypatch.setitem(svc._STATE, "applied", None)
    monkeypatch.setattr(settings, "friend_houses_enabled", False, raising=False)
    monkeypatch.setattr(egress, "policy", lambda: "never")
    await svc.reconcile()
    assert calls == ["retries", "stop"]
