"""domovoi/lifecycle.py: the shutdown event, the signal wiring and the
bounds on a stop.

The signal tests pin the 2026-09-30 root cause: the lifespan used to call
``loop.add_signal_handler(SIGTERM, signal_shutdown)``, which REPLACED
uvicorn's own handler, so a SIGTERM flipped the event and nothing else —
uvicorn never began shutting down, the lifespan teardown never ran, and
systemd SIGKILLed the core after 90 s on every update. The wrapper must
keep the server's handler in the chain. DB-free: none of this may skip.
(The end-to-end version — a real uvicorn in a subprocess, signalled —
lives in test_shutdown.py.)
"""

from __future__ import annotations

import asyncio
import signal
import threading
import time

import pytest

from domovoi import lifecycle


@pytest.fixture(autouse=True)
def _fresh_state_and_handlers():
    """Every test starts outside a shutdown, and whatever it does to the
    process's signal handlers is undone."""
    saved = {sig: signal.getsignal(sig) for sig in lifecycle.HANDLED_SIGNALS}
    lifecycle.reset()
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)
    lifecycle.reset()


class _ServerHandler:
    """Stands in for uvicorn's Server.handle_exit."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.event_set_when_called: list[bool] = []

    def __call__(self, sig, frame) -> None:
        self.calls.append(sig)
        self.event_set_when_called.append(lifecycle.shutting_down())


# ─── the event ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_or_shutdown_times_out_when_no_signal() -> None:
    fired = await lifecycle.wait_or_shutdown(0.1)
    assert fired is False


@pytest.mark.asyncio
async def test_wait_or_shutdown_returns_true_when_signaled() -> None:
    async def _fire_later() -> None:
        await asyncio.sleep(0.05)
        lifecycle.signal_shutdown()

    task = asyncio.create_task(_fire_later())
    try:
        fired = await lifecycle.wait_or_shutdown(2.0)
    finally:
        await task
    assert fired is True
    assert lifecycle.shutdown_event.is_set()


@pytest.mark.asyncio
async def test_wait_or_shutdown_returns_immediately_if_already_set() -> None:
    lifecycle.signal_shutdown()
    fired = await lifecycle.wait_or_shutdown(2.0)
    assert fired is True


def test_signal_shutdown_idempotent() -> None:
    lifecycle.signal_shutdown()
    lifecycle.signal_shutdown()  # second call: no-op, no exception
    assert lifecycle.shutdown_event.is_set()


def test_signal_shutdown_records_when_and_why_and_reset_clears_it() -> None:
    assert not lifecycle.shutting_down()
    before = time.monotonic()
    lifecycle.signal_shutdown("SIGTERM")
    lifecycle.signal_shutdown("lifespan teardown")   # the first reason stays
    assert lifecycle.shutting_down()
    assert before <= lifecycle.shutdown_began_at() <= time.monotonic()
    assert lifecycle._began_by == "SIGTERM"
    lifecycle.reset()
    assert not lifecycle.shutting_down()
    assert lifecycle.shutdown_began_at() is None
    assert not lifecycle.shutdown_event.is_set()


# ─── the signal wiring ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sigterm_reaches_the_servers_handler_after_flipping_the_event() -> None:
    """THE regression: the server's own handler is still called. Before the
    fix, install_signal_handlers() swapped it out and uvicorn never heard
    the SIGTERM."""
    server = _ServerHandler()
    signal.signal(signal.SIGTERM, server)
    signal.signal(signal.SIGINT, server)

    lifecycle.install_signal_handlers()

    installed = signal.getsignal(signal.SIGTERM)
    assert installed is not server
    assert installed._domovoi_previous is server
    installed(signal.SIGTERM, None)
    # The server heard it, and the shutdown had already begun when it did.
    assert server.calls == [signal.SIGTERM]
    assert server.event_set_when_called == [True]
    # The event itself flips through the loop, not inside the handler.
    await asyncio.sleep(0)
    assert lifecycle.shutdown_event.is_set()
    assert lifecycle._began_by == "SIGTERM"

    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
    assert server.calls == [signal.SIGTERM, signal.SIGINT]


@pytest.mark.asyncio
async def test_a_real_signal_goes_through_the_chain() -> None:
    """Delivered by the OS (raise_signal), not by calling the handler."""
    server = _ServerHandler()
    signal.signal(signal.SIGTERM, server)
    lifecycle.install_signal_handlers()
    signal.raise_signal(signal.SIGTERM)
    for _ in range(50):
        if server.calls:
            break
        await asyncio.sleep(0.01)
    assert server.calls == [signal.SIGTERM]
    await asyncio.sleep(0)
    assert lifecycle.shutdown_event.is_set()


def test_without_a_loop_the_event_flips_at_once() -> None:
    server = _ServerHandler()
    signal.signal(signal.SIGTERM, server)
    lifecycle.install_signal_handlers()
    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    assert lifecycle.shutdown_event.is_set()
    assert server.calls == [signal.SIGTERM]


def test_a_signal_nobody_handles_is_left_alone() -> None:
    """SIG_DFL must stay SIG_DFL: a handler there would swallow the SIGTERM
    that is supposed to end the process."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    lifecycle.install_signal_handlers()
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


@pytest.mark.asyncio
async def test_installing_twice_wraps_once() -> None:
    """A re-entered lifespan must not wrap the wrapper."""
    server = _ServerHandler()
    signal.signal(signal.SIGTERM, server)
    lifecycle.install_signal_handlers()
    first = signal.getsignal(signal.SIGTERM)
    lifecycle.install_signal_handlers()
    assert signal.getsignal(signal.SIGTERM) is first
    first(signal.SIGTERM, None)
    assert server.calls == [signal.SIGTERM]


def test_off_the_main_thread_nothing_changes() -> None:
    """TestClient runs the lifespan on a portal thread; signal.signal would
    raise there."""
    server = _ServerHandler()
    signal.signal(signal.SIGTERM, server)
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            lifecycle.install_signal_handlers()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=_run)
    t.start()
    t.join()
    assert errors == []
    assert signal.getsignal(signal.SIGTERM) is server


@pytest.mark.asyncio
async def test_the_asyncio_loop_is_never_given_the_signal() -> None:
    """The old wiring registered the signal with the loop
    (loop.add_signal_handler), which replaces the Python-level handler
    with asyncio's no-op. Nothing may do that again."""
    server = _ServerHandler()
    signal.signal(signal.SIGTERM, server)
    loop = asyncio.get_running_loop()
    lifecycle.install_signal_handlers()
    assert not getattr(loop, "_signal_handlers", {})
    assert callable(signal.getsignal(signal.SIGTERM))
    assert signal.getsignal(signal.SIGTERM)._domovoi_previous is server


# ─── the bounded teardown ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_teardown_runs_every_step_in_order() -> None:
    ran: list[str] = []

    async def _a() -> None:
        ran.append("a")

    def _b() -> None:
        ran.append("b")

    async def _c() -> None:
        await asyncio.sleep(0.01)
        ran.append("c")

    left = await lifecycle.run_teardown(
        [("a", _a, 1.0), ("b", _b, 1.0), ("c", _c, 1.0)], budget_sec=5.0)
    assert ran == ["a", "b", "c"]
    assert left == []


@pytest.mark.asyncio
async def test_a_step_that_raises_does_not_stop_the_rest(caplog) -> None:
    ran: list[str] = []

    async def _boom() -> None:
        raise RuntimeError("plugin exploded")

    def _sync_boom() -> None:
        raise ValueError("bad")

    async def _after() -> None:
        ran.append("after")

    left = await lifecycle.run_teardown(
        [("boom", _boom, 1.0), ("sync boom", _sync_boom, 1.0), ("after", _after, 1.0)],
        budget_sec=5.0,
    )
    assert ran == ["after"]
    assert left == []
    assert any("boom raised: plugin exploded" in m for m in caplog.messages)
    assert any("sync boom raised: bad" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_a_hung_step_that_swallows_the_cancel_cannot_hold_the_stop() -> None:
    """A plugin's on_disable that never returns, and eats CancelledError:
    the teardown moves on after its timeout plus the cancel grace, and the
    steps after it still run."""
    release = asyncio.Event()
    ran: list[str] = []

    async def _stubborn() -> None:
        while not release.is_set():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                continue          # the bug being defended against

    async def _after() -> None:
        ran.append("after")

    t0 = time.monotonic()
    left = await lifecycle.run_teardown(
        [("stubborn", _stubborn, 0.2), ("after", _after, 1.0)],
        budget_sec=5.0, cancel_grace_sec=0.1,
    )
    took = time.monotonic() - t0
    assert left == ["stubborn"]
    assert ran == ["after"]
    assert took < 1.5, took
    release.set()


@pytest.mark.asyncio
async def test_the_budget_caps_every_step_but_each_still_gets_a_try() -> None:
    ran: list[str] = []

    async def _slow() -> None:
        await asyncio.sleep(3600)

    async def _quick() -> None:
        ran.append("quick")

    t0 = time.monotonic()
    left = await lifecycle.run_teardown(
        [("slow", _slow, 10.0), ("slow too", _slow, 10.0), ("quick", _quick, 10.0)],
        budget_sec=0.3, cancel_grace_sec=0.1,
    )
    took = time.monotonic() - t0
    assert left == ["slow", "slow too"]
    assert ran == ["quick"]
    assert took < 1.5, took


# ─── the exit watchdog ────────────────────────────────────────────────────


def _wait_for(pred, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_the_watchdog_exits_once_the_deadline_after_a_signal_passes() -> None:
    exits: list[int] = []
    with lifecycle.exit_watchdog(0.2, exit_fn=exits.append, poll_sec=0.02):
        t0 = time.monotonic()
        lifecycle.signal_shutdown("SIGTERM")
        assert _wait_for(lambda: exits)
        took = time.monotonic() - t0
    assert exits == [lifecycle.WATCHDOG_EXIT_CODE]
    assert 0.15 <= took < 2.0, took


def test_the_watchdog_counts_from_the_signal_not_from_the_loop() -> None:
    """A signal whose event flip is still queued on a stuck loop already
    starts the clock: the handler records the time itself."""
    exits: list[int] = []
    server = _ServerHandler()
    signal.signal(signal.SIGTERM, server)
    with lifecycle.exit_watchdog(0.2, exit_fn=exits.append, poll_sec=0.02):
        lifecycle.install_signal_handlers()
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert _wait_for(lambda: exits)
    assert server.calls == [signal.SIGTERM]


def test_the_watchdog_does_nothing_without_a_shutdown() -> None:
    exits: list[int] = []
    with lifecycle.exit_watchdog(0.1, exit_fn=exits.append, poll_sec=0.02):
        time.sleep(0.3)
    time.sleep(0.2)
    assert exits == []


def test_leaving_the_block_without_a_shutdown_disarms_it() -> None:
    """main() under a test that fakes uvicorn.run: returning must not leave
    a watchdog behind that a later test's lifespan teardown would trigger."""
    exits: list[int] = []
    with lifecycle.exit_watchdog(0.1, exit_fn=exits.append, poll_sec=0.02):
        pass
    lifecycle.signal_shutdown("lifespan teardown")
    time.sleep(0.4)
    assert exits == []


def test_a_shutdown_begun_inside_keeps_it_watching_after_the_block() -> None:
    """Ctrl+C on the dev box: uvicorn.run returns, and interpreter exit can
    still hang on a worker thread. The watchdog stays armed for that."""
    exits: list[int] = []
    with lifecycle.exit_watchdog(0.3, exit_fn=exits.append, poll_sec=0.02):
        lifecycle.signal_shutdown("SIGINT")
    assert exits == []
    assert _wait_for(lambda: exits)


def test_a_zero_deadline_turns_it_off() -> None:
    exits: list[int] = []
    with lifecycle.exit_watchdog(0, exit_fn=exits.append, poll_sec=0.02):
        lifecycle.signal_shutdown("SIGTERM")
        time.sleep(0.2)
    assert exits == []
