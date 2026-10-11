"""The container guard's debounce and incident lifecycle
(domovoi/container_guard/engine.py, store.py).

The debounce half is pure. The lifecycle half drives a real
:class:`ContainerGuard` — a fake Docker, a fixed clock and its own
expected set — against the test database's ``container_incidents``
(V024), so open / touch / resolve / acknowledge / clear and the NOTIFY
are the real SQL.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.container_guard import engine as cg_engine
from domovoi.container_guard import freeze, holds, store
from domovoi.container_guard.detect import DOCKER_KEY, Detection
from domovoi.container_guard.engine import ContainerGuard, Debouncer, GuardConfig
from domovoi.container_guard.expected import ExpectedSet
from domovoi.container_guard.model import (
    FAILURE,
    RUNNING,
    TAMPER,
    DockerView,
    Expected,
    Finding,
    Listed,
    Observed,
)
from domovoi.db.session import engine as db_engine
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db

CFG = GuardConfig(failure_grace_sec=120, daemon_grace_sec=300, tamper_confirm_sec=25,
                  boot_grace_sec=0, interval_sec=30)
D_PG = "sha256:" + "1" * 64
D_OTHER = "sha256:" + "2" * 64


def pg_expected(**kw) -> Expected:
    base = dict(name="cgtest-db", owner="core", role="postgres", label="the database", state=RUNNING,
                required=True, image_ref=f"postgres:16@{D_PG}", image_digests=frozenset({D_PG}))
    base.update(kw)
    return Expected(**base)


def failure(name="cgtest-db", kind="missing") -> Finding:
    return Finding(key=f"failure:{name}", cls=FAILURE, kind=kind, severity="error", container=name,
                   owner="core", label="the database", summary=f"The database {kind}.")


def tamper(name="cgtest-db", image=D_OTHER) -> Finding:
    return Finding(key=f"tamper:image:{name}:{image}", cls=TAMPER, kind="image", severity="error",
                   container=name, owner="core", label="the database",
                   summary="The database is running a different image than the one Domovoi expects.")


def exp_set(*items) -> ExpectedSet:
    e = ExpectedSet()
    e.add(items or [pg_expected()])
    return e


def det(*findings, evaluated=("cgtest-db",), daemon_ok=True) -> Detection:
    return Detection(daemon_ok=daemon_ok, findings=list(findings), evaluated=set(evaluated))


def row(iid, f: Finding, **kw):
    base = {"id": iid, "incident_key": f.key, "class": f.cls, "kind": f.kind, "container": f.container}
    base.update(kw)
    return base


# ─── debounce (pure) ──────────────────────────────────────────────────────

def test_a_failure_needs_two_checks_and_the_grace() -> None:
    d = Debouncer(CFG)
    assert d.plan(det(failure()), [], exp_set(), now=0).to_open == []
    # A second check inside the grace: still nothing (a restart heals here).
    assert d.plan(det(failure()), [], exp_set(), now=30).to_open == []
    plan = d.plan(det(failure()), [], exp_set(), now=121)
    assert [f.key for f in plan.to_open] == ["failure:cgtest-db"]


def test_a_restart_that_heals_inside_the_grace_never_opens() -> None:
    d = Debouncer(CFG)
    d.plan(det(failure("cgtest-db", "stopped")), [], exp_set(), now=0)
    d.plan(det(), [], exp_set(), now=30)          # back up
    assert d.pending == {}
    plan = d.plan(det(failure("cgtest-db", "stopped")), [], exp_set(), now=150)
    assert plan.to_open == []                     # the grace starts again


def test_one_long_gap_is_still_two_checks() -> None:
    d = Debouncer(CFG)
    assert d.plan(det(failure()), [], exp_set(), now=0).to_open == []
    assert d.plan(det(failure()), [], exp_set(), now=500).to_open != []


def test_docker_itself_needs_five_minutes() -> None:
    d = Debouncer(CFG)
    daemon = Finding(key=DOCKER_KEY, cls=FAILURE, kind="daemon", severity="error", container=None,
                     owner="core", label="Docker", summary="Docker isn't answering.")
    d.plan(det(daemon, evaluated=(), daemon_ok=False), [], exp_set(), now=0)
    assert d.plan(det(daemon, evaluated=(), daemon_ok=False), [], exp_set(), now=200).to_open == []
    assert d.plan(det(daemon, evaluated=(), daemon_ok=False), [], exp_set(), now=301).to_open != []


def test_tamper_needs_two_checks_25_seconds_apart() -> None:
    d = Debouncer(CFG)
    assert d.plan(det(tamper()), [], exp_set(), now=0).to_open == []
    assert d.plan(det(tamper()), [], exp_set(), now=10).to_open == []
    assert d.plan(det(tamper()), [], exp_set(), now=30).to_open != []


def test_an_open_failure_resolves_after_two_clean_checks() -> None:
    d = Debouncer(CFG)
    f = failure()
    open_rows = [row(7, f)]
    assert d.plan(det(), open_rows, exp_set(), now=0).to_resolve == []
    assert d.plan(det(), open_rows, exp_set(), now=30).to_resolve == [(7, "recovered")]


def test_a_still_failing_container_is_touched_and_its_clean_count_reset() -> None:
    d = Debouncer(CFG)
    f = failure()
    d.plan(det(), [row(7, f)], exp_set(), now=0)                 # one clean check
    plan = d.plan(det(failure(kind="stopped")), [row(7, f)], exp_set(), now=30)
    assert plan.to_touch == [(7, failure(kind="stopped"), True)]  # a different problem now
    assert d.plan(det(), [row(7, f)], exp_set(), now=60).to_resolve == []


def test_a_held_container_s_incident_is_left_alone() -> None:
    d = Debouncer(CFG)
    plan = d.plan(det(evaluated=()), [row(7, failure())], exp_set(pg_expected(hold="starting")), now=0)
    assert plan.empty()


def test_a_container_no_longer_expected_resolves_at_once() -> None:
    d = Debouncer(CFG)
    plan = d.plan(det(evaluated=()), [row(7, failure())], exp_set(pg_expected(state="known")), now=0)
    assert plan.to_resolve == [(7, "no_longer_expected")]
    plan = d.plan(det(evaluated=()), [row(8, failure("gone"))], exp_set(), now=0)
    assert plan.to_resolve == [(8, "no_longer_expected")]


def test_nothing_resolves_while_docker_is_down() -> None:
    d = Debouncer(CFG)
    plan = d.plan(det(evaluated=(), daemon_ok=False), [row(7, failure())], ExpectedSet(), now=0)
    assert plan.to_resolve == []


def test_tamper_never_resolves_by_itself() -> None:
    d = Debouncer(CFG)
    for now in (0, 30, 60, 90):
        assert d.plan(det(), [row(9, tamper())], exp_set(), now=now).to_resolve == []


def test_a_cleared_finding_still_there_stays_accepted() -> None:
    d = Debouncer(CFG)
    t = tamper()
    plan = d.plan(det(t), [], exp_set(), now=0, accepted={t.key})
    assert plan.accepted == [t.key] and plan.to_open == [] and d.pending == {}


# ─── the guard against the test database ──────────────────────────────────

class FakeDocker:
    """Hands the guard a prepared DockerView, recording what it asked."""

    def __init__(self) -> None:
        self.view = DockerView(ok=True)
        self.calls = 0

    async def observe(self, *, claim, image_refs=()):
        self.calls += 1
        return self.view

    def show(self, *observed: Observed, ok=True, error=None) -> None:
        self.view = DockerView(ok=ok, error=error)
        for o in observed:
            self.view.listed[o.name] = Listed(id=o.id, name=o.name)
            self.view.containers[o.name] = o


def running_db(**kw) -> Observed:
    base = dict(id="c" * 64, name="cgtest-db", status="running", running=True,
                config_image=f"postgres:16@{D_PG}", image_id=D_PG)
    base.update(kw)
    return Observed(**base)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest_asyncio.fixture
async def guard_env(monkeypatch):
    holds.clear()
    freeze.forget()
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE container_incidents, container_guard_baselines RESTART IDENTITY"))
    docker = FakeDocker()
    clock = Clock()
    expected = {"set": exp_set()}

    async def gather(_guard, _cfg):
        return expected["set"]

    async def no_hold(_guard, _cfg):
        return None

    guard = ContainerGuard(docker=docker, config=lambda: CFG, clock=clock, wall=lambda: 1.8e9,
                           gather=gather, global_hold=no_hold)
    yield guard, docker, clock, expected
    holds.clear()
    freeze.forget()
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE container_incidents, container_guard_baselines RESTART IDENTITY"))


async def _check(guard, clock, seconds=30):
    clock.t += seconds
    return await guard.check(force=True)


async def _open_rows():
    async with session_scope() as s:
        return await store.open_incidents(s)


@requires_db
async def test_a_stopped_container_opens_a_failure_and_resolves_when_restarted(guard_env) -> None:
    guard, docker, clock, _ = guard_env
    docker.show(running_db())
    await _check(guard, clock)
    assert await _open_rows() == []
    docker.show(running_db(status="exited", running=False, exit_code=137))
    for _ in range(4):                                  # 4 checks, 90 s: inside the grace
        await _check(guard, clock)
    assert await _open_rows() == []
    await _check(guard, clock)                          # 120 s: opens
    (inc,) = await _open_rows()
    assert (inc["class"], inc["kind"], inc["container"]) == ("failure", "stopped", "cgtest-db")
    assert "exit code 137" in inc["summary"] and inc["found"]["exit_code"] == 137
    docker.show(running_db())
    await _check(guard, clock)
    assert len(await _open_rows()) == 1                 # one clean check is not enough
    await _check(guard, clock)
    assert await _open_rows() == []
    async with session_scope() as s:
        (resolved,) = await store.recent_incidents(s)
    assert resolved["resolution"] == "recovered" and resolved["resolved_at"] is not None


@requires_db
async def test_a_swapped_image_opens_tamper_freezes_and_only_a_clear_ends_it(guard_env) -> None:
    guard, docker, clock, _ = guard_env
    docker.show(running_db(image_id=D_OTHER, config_image="postgres:16"))
    await _check(guard, clock)
    assert not await freeze.is_frozen()
    await _check(guard, clock)
    freeze.forget()
    (inc,) = await _open_rows()
    assert inc["class"] == "tamper" and inc["kind"] == "image"
    assert await freeze.is_frozen()
    # Put right: still open, still frozen — an admin reviews and clears it.
    docker.show(running_db())
    await _check(guard, clock)
    await _check(guard, clock)
    freeze.forget()
    assert await freeze.is_frozen()
    async with session_scope() as s:
        cleared = await store.clear(s, inc["id"], "admin via test", "checked: our own upgrade")
    assert cleared["resolution"] == "cleared" and cleared["cleared_by"] == "admin via test"
    assert cleared["clear_note"] == "checked: our own upgrade"
    freeze.forget()
    assert not await freeze.is_frozen()


@requires_db
async def test_a_cleared_finding_still_present_does_not_reopen_but_a_new_one_does(guard_env) -> None:
    guard, docker, clock, _ = guard_env
    docker.show(running_db(image_id=D_OTHER, config_image="postgres:16"))
    await _check(guard, clock)
    await _check(guard, clock)
    (inc,) = await _open_rows()
    async with session_scope() as s:
        await store.clear(s, inc["id"], "admin via test", None)
    for _ in range(3):
        await _check(guard, clock)
    assert await _open_rows() == []
    third = "sha256:" + "3" * 64
    docker.show(running_db(image_id=third, config_image="postgres:16"))
    await _check(guard, clock)
    await _check(guard, clock)
    (again,) = await _open_rows()
    assert again["id"] != inc["id"] and third in again["incident_key"]


@requires_db
async def test_acknowledge_is_for_failures_and_clear_for_tamper(guard_env) -> None:
    guard, docker, clock, _ = guard_env
    docker.show()
    for _ in range(5):
        await _check(guard, clock)
    (inc,) = await _open_rows()
    async with session_scope() as s:
        with pytest.raises(store.IncidentError, match="resolves by itself"):
            await store.clear(s, inc["id"], "admin", None)
        acked = await store.acknowledge(s, inc["id"], "admin via test")
        assert acked["acknowledged_by"] == "admin via test"
        with pytest.raises(store.IncidentError, match="no incident"):
            await store.acknowledge(s, 999999, "admin")


@requires_db
async def test_an_acknowledged_failure_that_turns_into_another_shows_again(guard_env) -> None:
    guard, docker, clock, _ = guard_env
    docker.show(running_db(status="exited", running=False))
    for _ in range(5):
        await _check(guard, clock)
    (inc,) = await _open_rows()
    async with session_scope() as s:
        await store.acknowledge(s, inc["id"], "admin")
    docker.show()                                       # now removed altogether
    await _check(guard, clock)
    (inc2,) = await _open_rows()
    assert inc2["id"] == inc["id"] and inc2["kind"] == "missing"
    assert inc2["acknowledged_at"] is None


@requires_db
async def test_disabling_the_plugin_resolves_its_container_s_failure(guard_env) -> None:
    guard, docker, clock, expected = guard_env
    docker.show()
    for _ in range(5):
        await _check(guard, clock)
    assert len(await _open_rows()) == 1
    expected["set"] = exp_set(pg_expected(state="known"))
    await _check(guard, clock)
    assert await _open_rows() == []


@requires_db
async def test_a_room_starting_never_pages_anyone(guard_env) -> None:
    guard, docker, clock, expected = guard_env
    docker.show()
    expected["set"] = exp_set(pg_expected(hold="the music player is starting"))
    for _ in range(10):
        await _check(guard, clock)
    assert await _open_rows() == []
    assert guard.status["held"] == {"cgtest-db": "the music player is starting"}


@requires_db
async def test_a_global_hold_judges_nothing(guard_env) -> None:
    guard, docker, clock, _ = guard_env

    async def updating(_g, _c):
        return "a restart or update is under way"

    guard._global_hold = updating
    docker.show()
    for _ in range(10):
        await _check(guard, clock)
    assert await _open_rows() == []
    assert guard.status["state"] == "holding"


@requires_db
async def test_the_boot_grace(guard_env, monkeypatch) -> None:
    guard, docker, clock, _ = guard_env
    guard._global_hold = cg_engine.default_global_hold
    guard._config = lambda: GuardConfig(boot_grace_sec=180, failure_grace_sec=0, interval_sec=30)
    guard.started_mono = clock.t
    docker.show()
    status = await _check(guard, clock, seconds=60)
    assert status["hold"].startswith("the core started")


@requires_db
async def test_docker_down_opens_one_incident_and_backs_off(guard_env) -> None:
    guard, docker, clock, _ = guard_env
    docker.show(ok=False, error="Cannot connect to the Docker daemon")
    for _ in range(12):
        await _check(guard, clock)
    (inc,) = await _open_rows()
    assert inc["incident_key"] == DOCKER_KEY and inc["kind"] == "daemon"
    calls = docker.calls
    clock.t += 1
    await guard.check()                                  # backing off: no look
    assert docker.calls == calls
    # Docker back: the daemon incident resolves; containers get a grace.
    docker.show(running_db())
    await _check(guard, clock, 600)
    await _check(guard, clock)
    assert await _open_rows() == []
    assert holds.held("docker") == "Docker just came back"


@requires_db
async def test_a_database_that_fails_mid_check_loses_no_debounce_progress(guard_env) -> None:
    guard, docker, clock, _ = guard_env
    docker.show()
    await _check(guard, clock)
    pending_before = dict(guard.debouncer.pending)

    @asynccontextmanager
    async def broken():
        raise RuntimeError("database away")
        yield  # pragma: no cover

    real = guard.session_factory
    guard.session_factory = broken
    await _check(guard, clock)
    assert guard.status["database_ok"] is False
    assert set(guard.debouncer.pending) == set(pending_before)
    guard.session_factory = real


@requires_db
async def test_open_incident_is_one_per_key_and_notifies(guard_env) -> None:
    import asyncpg

    from domovoi.config import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    got: list[str] = []
    conn = await asyncpg.connect(dsn)
    try:
        await conn.add_listener(store.NOTIFY_CHANNEL, lambda *a: got.append(a[3]))
        async with session_scope() as s:
            first, created = await store.open_incident(s, failure())
            again, created_again = await store.open_incident(s, failure())
        assert first == again and created and not created_again
        await asyncio.sleep(0.3)
    finally:
        await conn.close()
    assert any(p.startswith("opened:") for p in got)


@requires_db
async def test_the_music_player_image_baseline(guard_env, monkeypatch) -> None:
    guard, *_ = guard_env
    cg_engine.reset_guard(guard)
    try:
        img_a, img_b = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        assert await cg_engine.note_mpd_image(img_a, built=False) == "adopted"
        assert await cg_engine.note_mpd_image(img_a, built=False) == "same"
        # Rebuilt outside the core with no update-unit record: reported.
        assert await cg_engine.note_mpd_image(img_b, built=False) == "changed"
        # The update unit rebuilt it after the baseline: adopted.
        monkeypatch.setattr(cg_engine, "_update_unit_rebuilt_after", lambda _at: True)
        assert await cg_engine.note_mpd_image(img_b, built=False) == "update"
        assert await cg_engine.note_mpd_image(img_a, built=True) == "built"
        async with session_scope() as s:
            b = await store.get_baseline(s, cg_engine.MPD_BASELINE_KEY)
        assert (b["value"], b["source"]) == (img_a, "core-build")
    finally:
        cg_engine.reset_guard(None)


def test_the_update_unit_record_is_read_strictly(monkeypatch) -> None:
    from datetime import datetime, timezone

    from domovoi import git_version

    at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    cases = [
        ({"status": "ok", "mpd_changed": True, "finished_at": "2026-10-02T00:00:00Z"}, True),
        ({"status": "ok", "mpd_changed": True, "finished_at": "2026-09-30T00:00:00Z"}, False),
        ({"status": "ok", "mpd_changed": False, "finished_at": "2026-10-02T00:00:00Z"}, False),
        ({"status": "running", "mpd_changed": True, "finished_at": "2026-10-02T00:00:00Z"}, False),
        (None, False),
    ]
    for last, want in cases:
        monkeypatch.setattr(git_version, "read_last_update", lambda last=last: last)
        assert cg_engine._update_unit_rebuilt_after(at) is want, last
