"""A listener's place in a podcast is the household's read: the positions
route takes the device-read tier, and the open episode list carries
``position_sec`` only for a caller that passes it."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi import spoken_audio as sa
from domovoi.db.session import session_scope
from domovoi.tests.auth_testkit import HEADER, install_fake_db, web_client
from domovoi.tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.asyncio]

FEED = "https://feeds.example.com/position-tier.xml"


@pytest.fixture
async def episode():
    async with session_scope() as s:
        sid = (await s.execute(text(
            "INSERT INTO podcast_subscriptions (feed_url, title) VALUES (:u, 'Tier Show') RETURNING id"
        ), {"u": FEED})).scalar_one()
        eid = (await s.execute(text(
            "INSERT INTO podcast_episodes (subscription_id, guid, title) VALUES (:s, 'g-tier', 'e') RETURNING id"
        ), {"s": sid})).scalar_one()
        await s.execute(text(
            "INSERT INTO playback_positions (item_type, item_id, device_id, person_id, position_sec) "
            "VALUES (:t, :e, 'tier-room', NULL, 321)"
        ), {"t": sa.ITEM_PODCAST, "e": eid})
    yield sid, eid
    async with session_scope() as s:
        await s.execute(text("DELETE FROM playback_positions WHERE item_type = :t AND item_id = :e"),
                        {"t": sa.ITEM_PODCAST, "e": eid})
        await s.execute(text("DELETE FROM podcast_subscriptions WHERE id = :s"), {"s": sid})


async def test_a_listeners_place_needs_a_household_credential(episode, monkeypatch) -> None:
    sid, eid = episode
    install_fake_db(monkeypatch, admin=True, device_token="household")
    async with web_client() as c:
        assert (await c.get(f"/api/podcasts/positions/{eid}?device_id=tier-room")).status_code == 401
        rows = (await c.get(f"/api/podcasts/subscriptions/{sid}/episodes?device_id=tier-room")).json()
        assert rows and rows[0]["position_sec"] is None
        tok = {HEADER: "household"}
        r = await c.get(f"/api/podcasts/positions/{eid}?device_id=tier-room", headers=tok)
        assert r.status_code == 200 and r.json()["position_sec"] == 321
        rows = (await c.get(f"/api/podcasts/subscriptions/{sid}/episodes?device_id=tier-room",
                            headers=tok)).json()
        assert rows[0]["position_sec"] == 321
