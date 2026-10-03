from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.connectivity import ConnectivityProbe, _parse_target
from domovoi.tests.conftest import requires_db


def test_parse_target() -> None:
    host, port = _parse_target("1.1.1.1:443")
    assert host == "1.1.1.1" and port == 443

    host, port = _parse_target("localhost:8080")
    assert host == "localhost" and port == 8080


def test_parse_target_invalid() -> None:
    with pytest.raises(ValueError):
        _parse_target("no-port-here")


@requires_db
@pytest.mark.asyncio
async def test_transitions_logged_once_per_change(db_session) -> None:
    probe = ConnectivityProbe(target="127.0.0.1:1", interval_sec=60.0, timeout_sec=0.3)
    # First check (cold start) logs whatever state it lands in.
    await probe.check_now()
    # Second check with the same state — no new row.
    await probe.check_now()

    rows = (await db_session.execute(text("SELECT connectivity_state FROM connectivity_events"))).all()
    assert len(rows) == 1
    # 127.0.0.1:1 is almost certainly refused → offline.
    assert rows[0][0] == "offline"


@requires_db
@pytest.mark.asyncio
async def test_online_to_offline_transition_logs_both(db_session) -> None:
    probe = ConnectivityProbe(target="127.0.0.1:1", interval_sec=60.0, timeout_sec=0.3)
    # Force an initial "online" state without probing.
    probe.online = True
    probe._started_once = True

    await probe.check_now()  # should flip to offline and log the transition

    rows = (await db_session.execute(text("SELECT connectivity_state FROM connectivity_events"))).all()
    assert len(rows) == 1
    assert rows[0][0] == "offline"
    assert probe.online is False


# ─── INTERNET_ACCESS=never: no dial at all ─────────────────────────────────


@pytest.fixture
def no_dial(monkeypatch):
    """Fail the test on any connection attempt."""
    import asyncio

    async def boom(*a, **k):  # pragma: no cover — the assertion is that it never runs
        raise AssertionError(f"the probe dialled {a!r} under never")

    monkeypatch.setattr(asyncio, "open_connection", boom)


@pytest.fixture
def events(monkeypatch):
    from domovoi import connectivity

    seen: list[tuple[str, dict]] = []

    class Bus:
        def emit(self, event, payload=None):
            seen.append((event, dict(payload or {})))
            return 0

    monkeypatch.setattr(connectivity, "EVENTS", Bus())
    return seen


@pytest.mark.asyncio
async def test_under_never_the_probe_never_dials(no_dial, events, monkeypatch) -> None:
    from domovoi import connectivity, egress

    rows: list[tuple[str, str]] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def log_transition(self, *, connectivity_state, target):
            rows.append((connectivity_state, target))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def scope():
        yield object()

    monkeypatch.setattr(connectivity, "ConnectivityEventRepository", Repo)
    monkeypatch.setattr(connectivity, "session_scope", scope)

    probe = ConnectivityProbe(target="1.1.1.1:443", interval_sec=60.0, timeout_sec=0.3)
    assert probe.reason == "connected"
    with egress.override_policy("never"):
        assert await probe.check_now() is False
        assert probe.online is False and probe.reason == "turned_off"
        assert probe.last_checked_at is not None and probe.last_online_at is None
        await probe.check_now()                       # no transition: nothing more
    assert events == [("core.connectivity_changed", {"online": False, "reason": "turned_off"})]
    assert rows == [("offline", "turned_off")]


@pytest.mark.asyncio
async def test_offline_to_turned_off_logs_but_writes_no_row(events, monkeypatch, caplog) -> None:
    import logging

    from domovoi import connectivity, egress

    rows: list = []

    class Repo:
        def __init__(self, _s):
            pass

        async def log_transition(self, **kw):
            rows.append(kw)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def scope():
        yield object()

    monkeypatch.setattr(connectivity, "ConnectivityEventRepository", Repo)
    monkeypatch.setattr(connectivity, "session_scope", scope)
    probe = ConnectivityProbe(target="127.0.0.1:1", interval_sec=60.0, timeout_sec=0.3)
    probe.online, probe.reason, probe._started_once = False, "offline", True
    with egress.override_policy("never"), caplog.at_level(logging.INFO, logger="domovoi.connectivity"):
        await probe.check_now()
    assert probe.reason == "turned_off"
    assert rows == [] and events == []
    assert any("turned_off" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_unanswered_the_probe_dials_as_before(events, monkeypatch) -> None:
    from domovoi import connectivity

    dialled: list = []

    async def fake_open(host, port):
        dialled.append((host, port))
        raise OSError("refused")

    import asyncio

    monkeypatch.setattr(asyncio, "open_connection", fake_open)

    class Repo:
        def __init__(self, _s):
            pass

        async def log_transition(self, **kw):
            pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def scope():
        yield object()

    monkeypatch.setattr(connectivity, "ConnectivityEventRepository", Repo)
    monkeypatch.setattr(connectivity, "session_scope", scope)
    probe = ConnectivityProbe(target="1.1.1.1:443", interval_sec=60.0, timeout_sec=0.3)
    assert await probe.check_now() is False
    assert dialled == [("1.1.1.1", 443)]
    assert probe.reason == "offline"
    assert events == [("core.connectivity_changed", {"online": False, "reason": "offline"})]


@requires_db
@pytest.mark.asyncio
async def test_a_turned_off_transition_is_stored_as_offline_with_its_target(db_session, no_dial) -> None:
    from domovoi import egress

    probe = ConnectivityProbe(target="1.1.1.1:443", interval_sec=60.0, timeout_sec=0.3)
    probe.online, probe._started_once = True, True
    with egress.override_policy("never"):
        await probe.check_now()
    rows = (await db_session.execute(
        text("SELECT connectivity_state, target FROM connectivity_events")
    )).all()
    assert [tuple(r) for r in rows] == [("offline", "turned_off")]
