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
