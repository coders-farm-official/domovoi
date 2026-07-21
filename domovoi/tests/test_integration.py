from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi.db.session import SessionLocal, engine
from domovoi.main import app
from domovoi.tests.conftest import TABLES_TO_TRUNCATE, requires_db


@pytest_asyncio.fixture(autouse=True)
async def _truncate_between_tests():
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE"))
    yield


@requires_db
@pytest.mark.asyncio
async def test_post_intent_creates_timer_and_logs() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/intent",
                json={"transcript": "set a timer for 5 minutes", "room_id": "kitchen"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["matched_handler"] == "timer"
            assert body["matched_path"] == "fast"
            assert "5 minutes" in body["text"]
            assert body["session_id"]

    async with SessionLocal() as s:
        timers = (await s.execute(text("SELECT label, message FROM timers"))).all()
        assert len(timers) == 1
        assert timers[0][0] is None

        logs = (await s.execute(text("SELECT matched_handler FROM intents_log"))).all()
        assert len(logs) == 1
        assert logs[0][0] == "timer"


@requires_db
@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"


@requires_db
@pytest.mark.asyncio
async def test_handlers_endpoint_lists_timer() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/handlers")
            assert r.status_code == 200
            names = {h["name"] for h in r.json()}
            assert "timer" in names


@requires_db
@pytest.mark.asyncio
async def test_connectivity_endpoint() -> None:
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/v1/connectivity")
            assert r.status_code == 200
            body = r.json()
            assert "online" in body
            assert "target" in body


@requires_db
@pytest.mark.asyncio
async def test_timer_watcher_fires_expired_timer() -> None:
    """End-to-end: POST a tiny timer and confirm the watcher pops it within ~3s."""
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/intent",
                json={"transcript": "timer for 1 second", "room_id": "kitchen"},
            )
            assert r.status_code == 200

            # Wait up to ~3s for the watcher to fire and delete the row.
            deadline = asyncio.get_event_loop().time() + 3.0
            remaining = 1
            while asyncio.get_event_loop().time() < deadline and remaining > 0:
                await asyncio.sleep(0.2)
                async with SessionLocal() as s:
                    row = (await s.execute(text("SELECT COUNT(*) FROM timers"))).scalar_one()
                    remaining = int(row)
            assert remaining == 0
