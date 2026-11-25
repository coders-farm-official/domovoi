"""The automatic update check: its gate and its clock
(domovoi/workers/update_notice_check.py; design note UPDATE-NOTICES §5).

The check itself is faked here (``update_notice.run_check`` records its
calls); the gate's own inputs are pinned. No network, no git, no DB.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from domovoi import connectivity, egress, git_version, self_restart, update_notice
from domovoi.config import settings
from domovoi.workers import update_notice_check as unc
from domovoi.workers.update_notice_check import UpdateNoticeChecker

HOUR = 3600.0


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.wall = 1_800_000_000.0

    def advance(self, sec: float) -> None:
        self.mono += sec
        self.wall += sec


@pytest.fixture
def env(monkeypatch):
    update_notice.reset_for_tests()
    calls: list[str] = []

    async def run_check(found_by):
        calls.append(found_by)
        state = await update_notice.load_state()
        at = update_notice._now_iso()
        state["last_auto"] = {"at": at, "ok": env_ns.ok, "error": None, "ok_at": at if env_ns.ok else None}
        return {}

    async def load():
        return None

    async def save(value):
        return None

    async def sha():
        return env_ns.sha

    async def frozen(*a, **k):
        return env_ns.frozen

    from domovoi.container_guard import freeze

    env_ns = SimpleNamespace(ok=True, sha="abc1234", frozen=False, underway=False, calls=calls)
    monkeypatch.setattr(update_notice, "run_check", run_check)
    monkeypatch.setattr(update_notice, "_db_load", load)
    monkeypatch.setattr(update_notice, "_db_save", save)
    monkeypatch.setattr(git_version, "current_sha", sha)
    monkeypatch.setattr(git_version, "read_last_update", lambda: None)
    monkeypatch.setattr(self_restart, "underway", lambda *a, **k: env_ns.underway)
    monkeypatch.setattr(freeze, "is_frozen", frozen)
    monkeypatch.setattr(connectivity, "_current_probe", None)
    monkeypatch.setattr(settings, "update_auto_check", True)
    monkeypatch.setattr(settings, "update_check_tick_sec", HOUR)
    # Each tick's wall time feeds the state's ISO times.
    clock = Clock()
    monkeypatch.setattr(update_notice, "_now_iso", lambda: _iso(clock.wall))
    env_ns.clock = clock
    yield env_ns
    update_notice.reset_for_tests()


def _iso(t: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _worker(env, jitter=0.0) -> UpdateNoticeChecker:
    return UpdateNoticeChecker(clock=lambda: env.clock.mono, wall=lambda: env.clock.wall, jitter_sec=jitter)


def _tick(w):
    return asyncio.run(w.tick())


def _started(env, w):
    env.clock.advance(unc.START_DELAY_SEC)
    return _tick(w)


# ─── the gate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("answer", ["", "always", "sometimes", "never"])
@pytest.mark.parametrize("ticked", [True, False])
@pytest.mark.parametrize("online", [True, False])
@pytest.mark.parametrize("underway", [True, False])
def test_the_gate_matrix(env, monkeypatch, answer, ticked, online, underway):
    monkeypatch.setattr(settings, "update_auto_check", ticked)
    monkeypatch.setattr(connectivity, "_current_probe", SimpleNamespace(online=online))
    env.underway = underway
    w = _worker(env)
    with egress.override_policy(answer):
        out = _started(env, w)
    should = ticked and answer == "always" and online and not underway
    assert env.calls == (["auto"] if should else []), out
    if not ticked:
        assert out["skipped"] == "setting_off"
    elif answer != "always":
        assert out["skipped"] == "internet_not_yes"


def test_the_freeze_and_a_checkout_without_git_skip(env):
    with egress.override_policy("always"):
        env.frozen = True
        assert _started(env, _worker(env))["skipped"] == "frozen"
        env.frozen, env.sha = False, "unknown"
        assert _started(env, _worker(env))["skipped"] == "no_git"
    assert env.calls == []


# ─── when ────────────────────────────────────────────────────────────────


def test_never_during_boot_then_two_minutes_after_start(env):
    w = _worker(env)
    with egress.override_policy("always"):
        assert _tick(w)["skipped"] == "starting"
        assert 1 <= w.update_check_tick_sec <= unc.START_DELAY_SEC
        env.clock.advance(unc.START_DELAY_SEC - 1)
        assert _tick(w)["skipped"] == "starting"
        env.clock.advance(1)
        assert _tick(w)["checked"] == 1
    assert env.calls == ["auto"]
    assert w.update_check_tick_sec == HOUR


def test_a_restart_within_six_hours_of_a_success_does_not_check_at_start(env):
    with egress.override_policy("always"):
        _started(env, _worker(env))
        assert env.calls == ["auto"]
        env.clock.advance(5 * HOUR)        # a crash loop: a new process each time
        assert _started(env, _worker(env))["skipped"] == "not_due"
        env.clock.advance(HOUR)            # past the 6 h floor
        assert _started(env, _worker(env))["checked"] == 1
    assert env.calls == ["auto", "auto"]


def test_a_failed_start_check_waits_an_hour_across_restarts(env):
    env.ok = False
    with egress.override_policy("always"):
        _started(env, _worker(env))
        env.clock.advance(10 * 60)
        assert _started(env, _worker(env))["skipped"] == "not_due"
        env.clock.advance(HOUR)
        assert _started(env, _worker(env))["checked"] == 1
    assert env.calls == ["auto", "auto"]


def test_daily_after_23_hours_plus_the_jitter(env):
    w = _worker(env, jitter=90 * 60)
    with egress.override_policy("always"):
        _started(env, w)
        for _ in range(23):
            env.clock.advance(HOUR)
            _tick(w)
        assert env.calls == ["auto"], "not before 23 h + jitter"
        env.clock.advance(HOUR)        # 24 h: 23 h + 90 min not yet
        _tick(w)
        assert env.calls == ["auto"]
        env.clock.advance(HOUR)        # 25 h
        _tick(w)
    assert env.calls == ["auto", "auto"]


def test_a_failure_is_tried_again_at_the_next_tick(env):
    w = _worker(env)
    with egress.override_policy("always"):
        _started(env, w)
        env.ok = False
        env.clock.advance(24 * HOUR)
        _tick(w)
        env.clock.advance(HOUR)
        _tick(w)
        env.ok = True
        env.clock.advance(HOUR)
        _tick(w)
        env.clock.advance(HOUR)
        _tick(w)
    # The start check, the failed daily one, its retry an hour later (failed
    # too), the next retry (ok); then nothing until the next day.
    assert env.calls == ["auto"] * 4


def test_the_jitter_is_picked_once_within_two_hours():
    jitters = {UpdateNoticeChecker().jitter_sec for _ in range(20)}
    assert all(0 <= j <= 120 * 60 for j in jitters) and len(jitters) > 1


def test_a_gated_worker_ticks_hourly_not_every_second(env):
    w = _worker(env)
    with egress.override_policy("sometimes"):
        _started(env, w)
    assert w.update_check_tick_sec == HOUR


# ─── nothing goes out when it mustn't ────────────────────────────────────


def test_no_request_at_all_under_never(monkeypatch, env):
    """The real check, not the fake: under No the worker asks nothing, and
    the egress gate would refuse it anyway."""
    monkeypatch.undo()
    update_notice.reset_for_tests()
    seen: list[str] = []
    monkeypatch.setattr(update_notice, "_TRANSPORT", httpx.MockTransport(
        lambda r: seen.append(str(r.url)) or httpx.Response(500)))
    fetched: list[int] = []

    async def no_fetch():
        fetched.append(1)
        return {}

    monkeypatch.setattr(git_version, "commits_behind", no_fetch)
    monkeypatch.setattr(settings, "update_auto_check", True)
    clock = Clock()
    w = UpdateNoticeChecker(clock=lambda: clock.mono, wall=lambda: clock.wall)
    with egress.override_policy("never"):
        clock.advance(unc.START_DELAY_SEC)
        assert asyncio.run(w.tick())["skipped"] == "internet_not_yes"
        for _ in range(30):
            clock.advance(HOUR)
            asyncio.run(w.tick())
        assert asyncio.run(update_notice.verify_release("v1.5.0", "a" * 40)) is None
    assert seen == [] and fetched == []


def test_the_kit_modules_never_import_the_update_check():
    code = (
        "import sys\n"
        "import domovoi.kit_update, domovoi.offline_kit, domovoi.offline_kit.checker\n"
        "import domovoi.kit_builder.service, domovoi.kit_builder.parts, domovoi.kit_builder.fetch\n"
        "bad = [m for m in ('domovoi.update_notice', 'domovoi.workers.update_notice_check') if m in sys.modules]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == ""


def test_the_worker_is_registered_after_the_plugin_update_check():
    import inspect

    from domovoi import main

    src = inspect.getsource(main)
    assert src.index("WORKERS.add_worker(PluginUpdateChecker()") < src.index("UpdateNoticeChecker()")
    assert "settings_source=_update_notice_worker" in src
    assert UpdateNoticeChecker.requires_online is False and UpdateNoticeChecker.stub_suppressed is True
