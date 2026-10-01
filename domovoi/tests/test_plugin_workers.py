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


# ─── stop_owner is bounded (2026-09-30: a stop that hung until SIGKILL) ───


class _SlowTick(Worker):
    """A tick that takes a while once started (a feed fetch, an ICY poll)."""

    interval_setting = "tick_interval"
    stub_suppressed = False

    def __init__(self, name: str, seconds: float) -> None:
        self.name = name
        self.seconds = seconds
        self.started = asyncio.Event()
        self.finished = 0

    async def tick(self) -> None:
        self.started.set()
        await asyncio.sleep(self.seconds)
        self.finished += 1


class _Stubborn(Worker):
    """A tick that swallows cancellation until told otherwise."""

    name = "stubborn"
    interval_setting = "tick_interval"
    stub_suppressed = False

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def tick(self) -> None:
        self.started.set()
        while not self.release.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue


async def test_busy_workers_are_waited_for_together_not_in_turn() -> None:
    """Three workers each mid-tick: the stop takes about one tick, not
    three. (Each used to be awaited in turn, up to 10 s apiece.)"""
    runner = WorkerRunner()
    ws = [_SlowTick(f"slow{i}", 0.3) for i in range(3)]
    for w in ws:
        runner.add_worker(w, owner="s1", settings_source=_Settings())
    await runner.start_owner("s1")
    for w in ws:
        await asyncio.wait_for(w.started.wait(), 1)
    t0 = asyncio.get_running_loop().time()
    await runner.stop_owner("s1", timeout=5)
    took = asyncio.get_running_loop().time() - t0
    assert took < 0.55, took
    assert [w.finished for w in ws] == [1, 1, 1]      # each tick completed
    assert {s["state"] for s in runner.status("s1")["workers"]} == {"stopped"}


async def test_a_tick_past_the_deadline_is_cancelled() -> None:
    runner = WorkerRunner()
    w = _SlowTick("glacial", 3600)
    runner.add_worker(w, owner="s2", settings_source=_Settings())
    await runner.start_owner("s2")
    await asyncio.wait_for(w.started.wait(), 1)
    t0 = asyncio.get_running_loop().time()
    await runner.stop_owner("s2", timeout=0.1)
    assert asyncio.get_running_loop().time() - t0 < 0.5
    assert w.finished == 0
    assert runner.status("s2")["workers"][0]["state"] == "stopped"


async def test_a_worker_that_swallows_the_cancel_is_left_behind(caplog, monkeypatch) -> None:
    monkeypatch.setattr(workers_mod, "_CANCEL_GRACE_SEC", 0.1)
    runner = WorkerRunner()
    w = _Stubborn()
    runner.add_worker(w, owner="s3", settings_source=_Settings())
    await runner.start_owner("s3")
    await asyncio.wait_for(w.started.wait(), 1)
    t0 = asyncio.get_running_loop().time()
    await runner.stop_owner("s3", timeout=0.1)
    assert asyncio.get_running_loop().time() - t0 < 0.6
    assert any("worker:s3:stubborn did not stop" in m and "left it running" in m
               for m in caplog.messages), caplog.messages
    w.release.set()                 # let the task end before the loop closes
    await asyncio.sleep(0.1)


async def test_running_startup_hooks_are_cancelled_with_their_owner() -> None:
    """A boot hook still running (a long library index) goes with the
    owner instead of outliving it."""
    runner = WorkerRunner()
    cancelled = asyncio.Event()

    async def _long_index() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner.add_startup_hook(_long_index, owner="s4", name="index")
    await runner.start_owner("s4")
    await asyncio.sleep(0.01)
    t0 = asyncio.get_running_loop().time()
    await runner.stop_owner("s4", timeout=1)
    assert asyncio.get_running_loop().time() - t0 < 0.5
    assert cancelled.is_set()
