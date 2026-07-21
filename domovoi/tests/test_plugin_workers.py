"""Declarative worker registry (design §4.5): the poll loop, stop
semantics, LongRunWorker restart-with-backoff, startup-hook ordering,
and the §4.14 status shape — tested ONCE against WorkerRunner (plugin
and core workers only test tick())."""

from __future__ import annotations

import asyncio

import pytest

from domovoi.plugins_runtime import workers as workers_mod
from domovoi.plugins_runtime.workers import LongRunWorker, Worker, WorkerRunner

pytestmark = pytest.mark.asyncio


class _Settings:
    tick_interval = 0.01
    ticker_enabled = True


class _Ticker(Worker):
    name = "ticker"
    interval_setting = "tick_interval"
    enabled_setting = "ticker_enabled"
    stub_suppressed = False          # the suite runs USE_STUBS=true

    def __init__(self) -> None:
        self.ticks = 0

    async def tick(self) -> None:
        self.ticks += 1


class _FailingTicker(_Ticker):
    name = "failing_ticker"

    async def tick(self) -> None:
        self.ticks += 1
        raise RuntimeError("boom")


class _CrashyLongRun(LongRunWorker):
    name = "crashy"
    stub_suppressed = False

    def __init__(self) -> None:
        self.runs = 0

    async def run(self, shutdown: asyncio.Event) -> None:
        self.runs += 1
        if self.runs < 3:
            raise ConnectionError("lost upstream")
        await shutdown.wait()


async def test_poll_worker_ticks_and_stops() -> None:
    runner = WorkerRunner()
    w = _Ticker()
    runner.add_worker(w, owner="t1", settings_source=_Settings())
    await runner.start_owner("t1")
    await asyncio.sleep(0.08)
    await runner.stop_owner("t1")
    assert w.ticks >= 2
    ticks_after_stop = w.ticks
    await asyncio.sleep(0.05)
    assert w.ticks == ticks_after_stop
    status = runner.status("t1")["workers"][0]
    assert status["state"] == "stopped"
    assert status["last_error"] is None
    assert status["last_tick_at"] is not None


async def test_tick_exceptions_never_kill_the_loop() -> None:
    runner = WorkerRunner()
    w = _FailingTicker()
    runner.add_worker(w, owner="t2", settings_source=_Settings())
    await runner.start_owner("t2")
    await asyncio.sleep(0.06)
    await runner.stop_owner("t2")
    assert w.ticks >= 2                    # kept ticking through the raises
    status = runner.status("t2")["workers"][0]
    assert status["last_error"] == "boom"
    assert status["consecutive_failures"] >= 2


async def test_enabled_setting_gates_start() -> None:
    runner = WorkerRunner()
    settings_obj = _Settings()
    settings_obj.ticker_enabled = False
    w = _Ticker()
    runner.add_worker(w, owner="t3", settings_source=settings_obj)
    await runner.start_owner("t3")
    await asyncio.sleep(0.03)
    assert w.ticks == 0
    assert runner.status("t3")["workers"][0]["state"] == "stopped"


async def test_poll_worker_requires_interval_setting() -> None:
    class _Bad(Worker):
        name = "bad"

        async def tick(self) -> None: ...

    runner = WorkerRunner()
    with pytest.raises(ValueError, match="interval_setting"):
        runner.add_worker(_Bad(), owner="t4")


async def test_longrun_crash_policy_restarts_with_backoff(monkeypatch) -> None:
    monkeypatch.setattr(workers_mod, "_LONGRUN_BACKOFF_INITIAL", 0.01)
    monkeypatch.setattr(workers_mod, "_LONGRUN_BACKOFF_CAP", 0.02)
    runner = WorkerRunner()
    w = _CrashyLongRun()
    runner.add_worker(w, owner="t5")
    await runner.start_owner("t5")
    await asyncio.sleep(0.2)
    # Crashed twice, restarted each time, third run holds until shutdown.
    assert w.runs == 3
    status = runner.status("t5")["workers"][0]
    assert status["state"] == "running"
    assert status["consecutive_failures"] == 2
    await runner.stop_owner("t5")
    assert runner.status("t5")["workers"][0]["state"] == "stopped"


async def test_startup_hooks_order_and_status() -> None:
    runner = WorkerRunner()
    fired: list[str] = []

    async def first() -> None:
        await asyncio.sleep(0.02)
        fired.append("first")

    async def second() -> None:
        fired.append("second")

    async def broken() -> None:
        raise RuntimeError("hook exploded")

    runner.add_startup_hook(first, owner="t6", name="first")
    # `after=` waits on the FULL "<slug>.<name>" key (§4.5).
    runner.add_startup_hook(second, owner="t6", name="second", after="t6.first")
    runner.add_startup_hook(broken, owner="t6", name="broken")
    await runner.start_owner("t6")
    await asyncio.sleep(0.1)
    assert fired == ["first", "second"]
    hooks = {h["name"]: h for h in runner.status("t6")["startup_hooks"]}
    assert hooks["t6.first"]["state"] == "fired"
    assert hooks["t6.second"]["state"] == "fired"
    assert hooks["t6.broken"]["state"] == "failed"
    assert "hook exploded" in hooks["t6.broken"]["error"]
    # Names exposed for the §13.2 manifest cross-check.
    assert sorted(runner.hook_names("t6")) == ["broken", "first", "second"]
    await runner.stop_owner("t6")
