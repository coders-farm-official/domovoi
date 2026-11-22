"""The open acquisition listings (core ``GET /v1/acquisitions`` and the
web's ``GET /api/acquisitions``): a caller who is not admin reads no
``requested_by`` or ``origin_ref`` and an error from a fixed set of
sentences; an admin reads the row whole."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import main as core_main
from domovoi.acquisitions import redact_for_household
from domovoi.db.session import session_scope
from domovoi.tests.auth_testkit import bearer, install_fake_db, web_client
from domovoi.tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.asyncio]

MARK = "open-read-probe"
RAW_ERROR = "could not write /srv/example/out.part"


@pytest.fixture
async def row(monkeypatch):
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO media_acquisitions (kind, text, requested_by, origin_ref, status, error) "
            "VALUES ('query', :t, 'http:192.0.2.7', 'plugin_x:t:1', 'failed', :e)"
        ), {"t": MARK, "e": RAW_ERROR})
    yield
    async with session_scope() as s:
        await s.execute(text("DELETE FROM media_acquisitions WHERE text = :t"), {"t": MARK})


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_main.app), base_url="http://test")


def _mine(body: dict) -> dict:
    return next(a for a in body["acquisitions"] if a["text"] == MARK)


async def test_redaction_is_a_fixed_sentence() -> None:
    out = redact_for_household({"requested_by": "x", "origin_ref": "y", "status": "weird", "error": "z"})
    assert out["requested_by"] is None and out["origin_ref"] is None and out["error"] != "z"


async def test_open_listings_hide_the_requester_and_raw_error(row, monkeypatch) -> None:
    install_fake_db(monkeypatch, admin=True, sessions={"live"})
    for client, path in ((_core(), "/v1/acquisitions"), (web_client(), "/api/acquisitions")):
        async with client as c:
            open_row = _mine((await c.get(path)).json())
            assert open_row["requested_by"] is None and open_row["origin_ref"] is None
            assert open_row["error"] == "The download failed."
            admin_row = _mine((await c.get(path, headers=bearer("live"))).json())
            assert admin_row["requested_by"] == "http:192.0.2.7"
            assert admin_row["error"] == RAW_ERROR
