"""``/api/voice`` (web/backend/api/voice.py): the three voices, the server's
voice and the "say it like" list, which Settings → Voice edits and the phone
reads to sound the same.

DB-FREE: the gates (device tier to read, an admin Bearer to change).
DB-BACKED (``requires_db``): the contract the phone builds to, and every
write through the real routes, against V027's table, with the hops to the
core recorded instead of made.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    install_fake_db,
    web_client,
)
from domovoi.tests.conftest import requires_db
from web.backend.api import voice as voice_api

WRITES = (
    ("PUT", "/api/voice/server", {"voice": "en_US-kristin-medium"}),
    ("POST", "/api/voice/pronunciations", {"word": "Siobhan", "say_like": "shiv-AWN"}),
    ("DELETE", "/api/voice/pronunciations/Nguyen", None),
    ("POST", "/api/voice/pronunciations/defaults", {}),
)


# ─── The gates (DB-free) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reading_needs_the_device_tier(monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-tok")
    async with web_client() as c:
        assert (await c.get("/api/voice")).status_code == 401
        assert (await c.get("/api/voice/sample/en_US-norman-medium")).status_code == 401
        r = await c.post("/api/voice/pronunciations/preview", json={"word": "x", "say_like": "ex"})
        assert r.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
@pytest.mark.asyncio
async def test_changing_needs_an_admin(monkeypatch, method, path, body):
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="dev-tok")
    async with web_client() as c:
        assert (await c.request(method, path, json=body)).status_code == 401
        # The household token reads; it does not change the voice.
        r = await c.request(method, path, json=body, headers={HEADER: "dev-tok", "X-Requested-With": "t"})
        assert r.status_code == 401


# ─── Through the real routes (DB) ─────────────────────────────────────────


@pytest.fixture
def core_hops(monkeypatch):
    """The web→core calls, recorded (the core isn't running here)."""
    calls: list[tuple[str, dict | None]] = []

    async def post_admin(path, body=None, timeout=30.0, headers=None):
        calls.append((path, body))
        return 200, {"started": True}

    async def post_admin_bytes(path, body=None, timeout=30.0, headers=None):
        calls.append((path, body))
        return 200, b"RIFFwav", {"content-type": "audio/wav", "x-phonemes": "w%CB%88%C9%AAn."}

    monkeypatch.setattr(voice_api, "post_admin", post_admin)
    monkeypatch.setattr(voice_api, "post_admin_bytes", post_admin_bytes)
    return calls


@pytest.fixture
async def fresh_lists():
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE pronunciations, pronunciations_changed"))
        await conn.execute(text("DELETE FROM voices"))
        await conn.execute(text(
            "INSERT INTO voices (name, engine, model_ref, is_default) VALUES "
            "('Norman', 'piper', 'en_US-norman-medium', TRUE), "
            "('Kristin', 'piper', 'en_US-kristin-medium', FALSE)"))
    yield


@requires_db
@pytest.mark.asyncio
async def test_the_contract_the_phone_reads(_db, fresh_lists, core_hops):  # noqa: F811
    async with web_client() as c:
        token = await claim_admin(c)
        device = await db_device_token()
        r = await c.get("/api/voice", headers={HEADER: device})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"voices", "server_voice", "pronunciations", "updated_at"}
        assert [v["id"] for v in body["voices"]] == [
            "en_US-norman-medium", "en_US-kristin-medium", "en_US-ljspeech-medium"]
        norman = body["voices"][0]
        assert norman == {
            "id": "en_US-norman-medium", "name": "Norman", "sex": "male", "sample_rate": 22050,
            "size_bytes": 63531379,
            "sha256": "b9739443232a80a59c7d18810dd856899bf16a7964725f5ab81ea49b1351cb71",
        }
        assert body["server_voice"] == "en_US-norman-medium"
        assert body["pronunciations"] == [
            {"word": "Domovoi", "say_like": "DOH-moe-voy"},
            {"word": "Domo", "say_like": "DOH-moe"},
            {"word": "Nguyen", "say_like": "win"},
        ]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["updated_at"])
        # An admin Bearer reads it too (the dashboard).
        assert (await c.get("/api/voice", headers=bearer(token))).status_code == 200


@requires_db
@pytest.mark.asyncio
async def test_editing_the_list(_db, fresh_lists, core_hops):  # noqa: F811
    async with web_client() as c:
        token = await claim_admin(c)
        h = {**bearer(token), "X-Requested-With": "t"}
        first = (await c.get("/api/voice", headers=h)).json()

        r = await c.post("/api/voice/pronunciations", json={"word": "Siobhan", "say_like": "shiv-AWN"}, headers=h)
        assert r.status_code == 200, r.text
        assert {"word": "Siobhan", "say_like": "shiv-AWN"} in r.json()["pronunciations"]
        assert r.json()["updated_at"] >= first["updated_at"]
        assert ("/v1/admin/pronunciations/reload", None) in core_hops

        # A shipped entry changed, then removed, then the defaults put back.
        r = await c.post("/api/voice/pronunciations", json={"word": "nguyen", "say_like": "NOO-yen"}, headers=h)
        assert {"word": "nguyen", "say_like": "NOO-yen"} in r.json()["pronunciations"]
        r = await c.delete("/api/voice/pronunciations/Nguyen", headers=h)
        assert r.status_code == 200
        words = [p["word"].lower() for p in r.json()["pronunciations"]]
        assert "nguyen" not in words and "siobhan" in words
        assert (await c.delete("/api/voice/pronunciations/Nguyen", headers=h)).status_code == 404
        r = await c.post("/api/voice/pronunciations/defaults", headers=h)
        assert {"word": "Nguyen", "say_like": "win"} in r.json()["pronunciations"]
        assert {"word": "Siobhan", "say_like": "shiv-AWN"} in r.json()["pronunciations"]

        # What the converter can't use is refused with its reason, and not saved.
        for bad in ({"word": "two words", "say_like": "x"}, {"word": "Bob", "say_like": "b@b"},
                    {"word": "Bob", "say_like": "shh"}, {"word": "Bob", "say_like": "/w€n/"}):
            r = await c.post("/api/voice/pronunciations", json=bad, headers=h)
            assert r.status_code == 400, (bad, r.text)
            assert r.json()["detail"]
        words = [p["word"] for p in (await c.get("/api/voice", headers=h)).json()["pronunciations"]]
        assert "Bob" not in words


@requires_db
@pytest.mark.asyncio
async def test_choosing_the_server_voice(_db, fresh_lists, core_hops):  # noqa: F811
    async with web_client() as c:
        token = await claim_admin(c)
        h = {**bearer(token), "X-Requested-With": "t"}
        before = (await c.get("/api/voice", headers=h)).json()["updated_at"]
        r = await c.put("/api/voice/server", json={"voice": "en_US-ljspeech-medium"}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["server_voice"] == "en_US-ljspeech-medium"
        assert r.json()["updated_at"] >= before
        assert ("/v1/admin/sounds/regenerate", None) in core_hops
        r = await c.get("/api/voices", headers=h)
        default = next(v for v in r.json() if v["is_default"])
        assert (default["name"], default["model_ref"]) == ("Linda", "en_US-ljspeech-medium")
        r = await c.put("/api/voice/server", json={"voice": "en_US-lessac-medium"}, headers=h)
        assert r.status_code == 400


@requires_db
@pytest.mark.asyncio
async def test_preview_and_samples_go_to_the_core(_db, fresh_lists, core_hops):  # noqa: F811
    async with web_client() as c:
        await claim_admin(c)
        device = await db_device_token()
        h = {HEADER: device, "X-Requested-With": "t"}
        r = await c.post("/api/voice/pronunciations/preview",
                         json={"word": "Nguyen", "say_like": "win", "voice": "en_US-kristin-medium"}, headers=h)
        assert r.status_code == 200 and r.content == b"RIFFwav"
        assert r.headers["x-phonemes"] == "w%CB%88%C9%AAn."
        assert core_hops[-1] == ("/v1/admin/voice/say-like",
                                 {"word": "Nguyen", "say_like": "win", "voice": "en_US-kristin-medium"})
        r = await c.post("/api/voice/pronunciations/preview", json={"word": "Bob", "say_like": "shh"}, headers=h)
        assert r.status_code == 400
        r = await c.get("/api/voice/sample/en_US-kristin-medium", headers=h)
        assert r.status_code == 200
        assert core_hops[-1] == ("/v1/admin/voices/sample", {"name": "Kristin"})
        assert (await c.get("/api/voice/sample/en_US-ryan-high", headers=h)).status_code == 404


async def _rows():
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        return (await conn.execute(text("SELECT key, say_like FROM pronunciations ORDER BY key"))).all()


@requires_db
@pytest.mark.asyncio
async def test_the_table_stays_bounded(_db, fresh_lists, core_hops):  # noqa: F811
    """Removing the owner's own word deletes its row; a default gets one row
    at most; putting the defaults back deletes the default words' rows, so a
    later release's defaults apply."""
    async with web_client() as c:
        token = await claim_admin(c)
        h = {**bearer(token), "X-Requested-With": "t"}
        await c.post("/api/voice/pronunciations", json={"word": "Siobhan", "say_like": "shiv-AWN"}, headers=h)
        before = (await c.get("/api/voice", headers=h)).json()["updated_at"]
        r = await c.delete("/api/voice/pronunciations/Siobhan", headers=h)
        assert r.status_code == 200
        assert await _rows() == []                          # deleted, not marked
        assert r.json()["updated_at"] >= before             # a deletion still moves updated_at
        await c.post("/api/voice/pronunciations", json={"word": "Nguyen", "say_like": "NOO-yen"}, headers=h)
        await c.delete("/api/voice/pronunciations/Nguyen", headers=h)
        assert await _rows() == [("nguyen", None)]          # a removed default: one row
        await c.post("/api/voice/pronunciations", json={"word": "Domo", "say_like": "DOH-mo"}, headers=h)
        await c.post("/api/voice/pronunciations", json={"word": "Kamron", "say_like": "CAM-ron"}, headers=h)
        r = await c.post("/api/voice/pronunciations/defaults", headers=h)
        assert await _rows() == [("kamron", "CAM-ron")]     # only the owner's own word is left

        # A later release ships a different default: it reaches this box.
        from domovoi import voice_settings

        newer = [("Domovoi", "DOH-moe-voy"), ("Domo", "DOH-moh"), ("Nguyen", "win")]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(voice_settings, "default_pronunciations", lambda: newer)
            body = (await c.get("/api/voice", headers=h)).json()
        assert {"word": "Domo", "say_like": "DOH-moh"} in body["pronunciations"]


@requires_db
@pytest.mark.asyncio
async def test_the_cap_counts_the_owners_words(_db, fresh_lists, core_hops, monkeypatch):  # noqa: F811
    from domovoi import voice_settings
    from domovoi.db.session import engine

    monkeypatch.setattr(voice_settings, "MAX_PRONUNCIATIONS", 3)
    monkeypatch.setattr(voice_api, "MAX_PRONUNCIATIONS", 3)
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO pronunciations (key, word, say_like) VALUES "
            "('aa', 'Aa', 'ay'), ('bb', 'Bb', 'bee'), ('nguyen', 'Nguyen', NULL)"))
    async with web_client() as c:
        token = await claim_admin(c)
        h = {**bearer(token), "X-Requested-With": "t"}
        ok = await c.post("/api/voice/pronunciations", json={"word": "Cc", "say_like": "see"}, headers=h)
        assert ok.status_code == 200                        # 2 own words + a default's row: room for one
        full = await c.post("/api/voice/pronunciations", json={"word": "Dd", "say_like": "dee"}, headers=h)
        assert full.status_code == 409 and "at most 3" in full.json()["detail"]
        again = await c.post("/api/voice/pronunciations", json={"word": "Cc", "say_like": "SEE"}, headers=h)
        assert again.status_code == 200                     # changing a listed word is always allowed
        default = await c.post("/api/voice/pronunciations", json={"word": "Nguyen", "say_like": "win"}, headers=h)
        assert default.status_code == 200                   # a default word doesn't count
        await c.delete("/api/voice/pronunciations/Cc", headers=h)
        r = await c.post("/api/voice/pronunciations", json={"word": "Dd", "say_like": "dee"}, headers=h)
        assert r.status_code == 200                         # removing one makes room
