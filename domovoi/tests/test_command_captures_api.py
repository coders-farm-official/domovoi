"""The admin side of the opt-in command recordings: the /api/captures
routes, the opt-in rows (V016), and what the rest of the dashboard shows.

Two halves, like test_web_speech_reads:

* DB-FREE: every /api/captures route driven through the real web app with
  the auth primitives faked. The recordings are raw audio of whoever spoke
  near an opted-in satellite, so the whole surface is the admin SECURITY
  tier: 501 before first-run setup, 401 with no credential AND with the
  household token (the device tier that reads conversations does NOT read
  these), the dashboard cookie renders a read but never writes, a Bearer
  does both. "Gets through" is proved by reaching the handler.
* DB-BACKED: the real flow on a real database — off by default, an admin
  opts a room in, a routed command is kept, the list / audio / label /
  delete routes, opting out deletes everything, a room that is not opted
  in keeps nothing, ``--reset-admin`` pauses recording, retiring a room
  forgets it, and the satellites list carries the "recording" marker.
"""

from __future__ import annotations

import io
import types
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import command_captures as cc
from domovoi import streaming
from domovoi.config import settings
from domovoi.models import Response
from domovoi.streaming import StreamSession
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_client,
    db_device_token,
    install_fake_db,
    web_app,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.db.session import engine

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "domovoi" / "db" / "migrations"

ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"
CID = "20260928T101530123Z-3f9a1c2b"
XRW = {"X-Requested-With": "XMLHttpRequest"}

ROUTES: dict[str, tuple[str, str, dict | None]] = {
    "list": ("GET", "/api/captures", None),
    "audio": ("GET", f"/api/captures/clips/kitchen/{CID}/audio", None),
    "label": ("PATCH", f"/api/captures/clips/kitchen/{CID}", {"label": "fine"}),
    "delete": ("DELETE", f"/api/captures/clips/kitchen/{CID}", None),
    "opt-in": ("PUT", "/api/captures/rooms/kitchen", None),
    "opt-out": ("DELETE", "/api/captures/rooms/kitchen", None),
}
READS = {"list", "audio"}
_IDS = sorted(ROUTES)


class Reached(BaseException):
    """Raised once a request gets past the gate. A BaseException so no
    handler's ``except Exception`` can swallow the proof."""


class _Tripwire:
    def __getattr__(self, name):
        raise Reached(f"reached command_captures.{name}")


@pytest.fixture
def mark_reached(monkeypatch):
    from web.backend.api import captures as captures_api

    @asynccontextmanager
    async def tripped():
        raise Reached("reached the database")
        yield  # pragma: no cover

    def tripped_check(room_id):
        raise Reached("reached the handler")

    monkeypatch.setattr(captures_api, "cc", _Tripwire())
    monkeypatch.setattr(captures_api, "session_scope", tripped)
    monkeypatch.setattr(captures_api, "_check_room", tripped_check)


def _client(headers: dict[str, str] | None = None, **kw) -> AsyncClient:
    h = dict(XRW)
    h.update(headers or {})
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers=h, **kw)


async def _send(c: AsyncClient, name: str):
    method, path, body = ROUTES[name]
    return await c.request(method, path, json=body)


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN},
                           device_token=DEVICE_TOKEN)


# ─── the gate, DB-free ───────────────────────────────────────────────────


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_before_setup_everything_is_501(name, monkeypatch, mark_reached) -> None:
    """No pre-setup grace: nobody can turn recording on — or read a
    recording left from before a --reset-admin — until someone claims the
    server."""
    install_fake_db(monkeypatch, admin=False)
    async with _client() as c:
        r = await _send(c, name)
    assert r.status_code == 501, r.text


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_no_credential_is_401(name, claimed, mark_reached) -> None:
    async with _client() as c:
        assert (await _send(c, name)).status_code == 401


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_the_household_token_is_not_enough(name, claimed, mark_reached) -> None:
    """Never the device tier: a paired phone reads the house's conversations,
    not the audio of whoever spoke near an opted-in satellite."""
    async with _client({HEADER: DEVICE_TOKEN}) as c:
        assert (await _send(c, name)).status_code == 401
    async with _client() as c:
        method, path, body = ROUTES[name]
        r = await c.request(method, f"{path}?device_token={DEVICE_TOKEN}", json=body)
        assert r.status_code == 401


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_the_cookie_renders_reads_and_never_writes(name, claimed, mark_reached) -> None:
    async with _client(cookies={COOKIE: ADMIN_TOKEN}) as c:
        if name in READS:
            with pytest.raises(Reached):
                await _send(c, name)
        else:
            assert (await _send(c, name)).status_code == 403


@pytest.mark.parametrize("name", _IDS)
@pytest.mark.asyncio
async def test_an_admin_bearer_gets_through(name, claimed, mark_reached) -> None:
    async with _client(bearer(ADMIN_TOKEN)) as c:
        with pytest.raises(Reached):
            await _send(c, name)


def test_no_other_web_route_serves_the_directory() -> None:
    """Only /api/captures reads it — and nothing mounts it as static files."""
    from domovoi.tests.route_walk import iter_route_contexts

    paths = {getattr(rc, "path", "") for rc in iter_route_contexts(web_app.routes)}
    assert {p for p in paths if "captures" in p} == {
        "/api/captures",
        "/api/captures/clips/{room_id}/{capture_id}/audio",
        "/api/captures/clips/{room_id}/{capture_id}",
        "/api/captures/rooms/{room_id}",
    }
    src = (REPO_ROOT / "web" / "backend" / "main.py").read_text(encoding="utf-8")
    assert "command_captures_dir" not in src
    for mod in (REPO_ROOT / "web" / "backend" / "api").glob("*.py"):
        if mod.name in ("captures.py",):
            continue
        src = mod.read_text(encoding="utf-8")
        if mod.name == "files_security.py":
            # Named there only to REFUSE it: safe_join's guard.
            assert src.count("command_captures_dir") == 1, mod.name
            assert "def _is_command_captures" in src and "if _is_command_captures(target)" in src
            continue
        assert "command_captures_dir" not in src, mod.name


# ─── the real flow ───────────────────────────────────────────────────────


async def _ensure_v016() -> None:
    sql = (MIGRATIONS / "V016__command_capture_rooms.sql").read_text(encoding="utf-8")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(sql)


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE command_capture_rooms"))
        await conn.execute(text("DELETE FROM satellites WHERE room_id IN ('kitchen', 'garage')"))
        await conn.execute(text("DELETE FROM mpd_rooms WHERE room_id IN ('kitchen', 'garage')"))


@pytest.fixture
async def rooms(_db):
    """Fresh auth (``_db``), V016 applied, two adopted rooms that never
    connected (so no MPD is touched)."""
    await _ensure_v016()
    await _reset()
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO satellites (room_id, adopted_via) VALUES "
            "('kitchen', 'manual'), ('garage', 'manual')"
        ))
    yield
    await _reset()


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(pcm)
    return buf.getvalue()


PCM = b"\x10\x00\xf0\xff" * 8000


@pytest.fixture
def fake_turn(monkeypatch):
    """Every stage of a voice turn faked EXCEPT the opt-in check, which
    asks the real database."""

    class _Whisper:
        async def transcribe(self, pcm):
            return "what time is it"

    class _TTS:
        async def synthesize(self, text, *, engine=None, voice=None):
            return _wav(b"\x01\x02" * 200)

    async def _identify(pcm):
        return None

    async def _route(intent, ctx, session):
        return Response(text="It's ten.", matched_handler="clock", matched_path="fast",
                        online=True)

    @asynccontextmanager
    async def _no_db():
        yield None

    async def _merge(s, row_id, patch):
        return None

    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "merge_post_route", _merge)
    monkeypatch.setattr(streaming, "session_scope", _no_db)
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(
            satellite_voice={}, wifi_status={}, satellite_volume={}, satellite_config={},
            greeting_phrases=[], resumable_music={}, current_playlist={},
            active_sessions={}, probe=types.SimpleNamespace(online=True),
        ))

    async def send_text(self, data):
        pass

    async def send_bytes(self, data):
        pass


async def _speak(room: str) -> None:
    sess = StreamSession(_FakeWS(), room)  # type: ignore[arg-type]
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    await sess._on_audio(PCM)
    await sess._on_control({"type": "utterance_end", "end_reason": "vad_silence_after_speech"})
    await sess._response_task


async def _satellite(c: AsyncClient, room: str) -> dict:
    r = await c.get("/api/satellites")
    assert r.status_code == 200, r.text
    return next(s for s in r.json() if s["room_id"] == room)


@requires_db
@pytest.mark.asyncio
async def test_the_whole_life_of_a_room_s_recordings(rooms, fake_turn) -> None:
    async with web_client() as setup:
        admin = await claim_admin(setup)
    device = await db_device_token()
    admin_h = {**XRW, **bearer(admin)}

    async with _client() as anon:
        # Off by default, for every room, and the marker says so to anyone.
        assert (await _satellite(anon, "kitchen"))["capture_commands"] is False
        assert await cc.room_opted_in("kitchen") is False
        await _speak("kitchen")
        assert cc.list_captures() == []

    async with _client(admin_h) as c:
        # Opt in: only a known room, idempotent.
        assert (await c.put("/api/captures/rooms/nowhere")).status_code == 404
        r = await c.put("/api/captures/rooms/kitchen")
        assert r.status_code == 200 and r.json()["enabled"] is True
        first_at = r.json()["enabled_at"]
        again = await c.put("/api/captures/rooms/kitchen")
        assert again.json()["enabled_at"] == first_at

    async with _client() as anon:
        sat = await _satellite(anon, "kitchen")
        assert sat["capture_commands"] is True and sat["capture_since"]
        assert (await _satellite(anon, "garage"))["capture_commands"] is False

    # A routed command in the opted-in room is kept; in the other, nothing.
    await _speak("kitchen")
    await _speak("garage")
    (kept,) = cc.list_captures()
    assert kept["room_id"] == "kitchen" and kept["transcript"] == "what time is it"
    assert kept["end_reason"] == "vad_silence_after_speech"
    assert kept["matched_handler"] == "clock"

    # Reading back: the device tier never, the admin always.
    async with _client({HEADER: device}) as phone:
        assert (await phone.get("/api/captures")).status_code == 401
    async with _client(cookies={COOKIE: admin}) as browser:
        listing = (await browser.get("/api/captures")).json()
    assert [c["id"] for c in listing["captures"]] == [kept["id"]]
    assert listing["retention_days"] == 14 and listing["problem"] is None
    assert {r["room_id"]: r["enabled"] for r in listing["rooms"]} == {"kitchen": True}
    assert listing["rooms"][0]["count"] == 1
    assert set(listing["labels"]) == {"cut_off", "fine", "waited_too_long"}

    async with _client(admin_h) as c:
        audio = await c.get(f"/api/captures/clips/kitchen/{kept['id']}/audio")
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/wav"
        assert "no-store" in audio.headers["cache-control"]
        with wave.open(io.BytesIO(audio.content), "rb") as w:
            assert w.readframes(w.getnframes()) == PCM
        for bad in ("../x", "20260928T101530123Z-zzzzzzzz", "notes"):
            assert (await c.get(f"/api/captures/clips/kitchen/{bad}/audio")).status_code == 404
        assert (await c.get(f"/api/captures/clips/garage/{kept['id']}/audio")).status_code == 404

        r = await c.patch(f"/api/captures/clips/kitchen/{kept['id']}", json={"label": "cut_off"})
        assert r.status_code == 200 and r.json()["label"] == "cut_off"
        assert (await c.patch(f"/api/captures/clips/kitchen/{kept['id']}",
                              json={"label": "meh"})).status_code == 422
        assert cc.list_captures()[0]["label"] == "cut_off"

        assert (await c.delete(f"/api/captures/clips/kitchen/{kept['id']}")).status_code == 204
        assert (await c.delete(f"/api/captures/clips/kitchen/{kept['id']}")).status_code == 404
        assert cc.list_captures() == []

    # Opting out deletes everything kept, in the same request.
    await _speak("kitchen")
    await _speak("kitchen")
    assert len(cc.list_captures()) == 2
    async with _client(admin_h) as c:
        r = await c.delete("/api/captures/rooms/kitchen")
        assert r.status_code == 200
        assert r.json() == {"room_id": "kitchen", "enabled": False, "enabled_at": None, "deleted": 2}
    assert cc.list_captures() == []
    assert not cc.room_dir("kitchen").exists()
    assert await cc.room_opted_in("kitchen") is False
    await _speak("kitchen")
    assert cc.list_captures() == []
    async with _client() as anon:
        assert (await _satellite(anon, "kitchen"))["capture_commands"] is False


@requires_db
@pytest.mark.asyncio
async def test_reset_admin_pauses_every_room(rooms, fake_turn) -> None:
    async with web_client() as setup:
        admin = await claim_admin(setup)
    async with _client({**XRW, **bearer(admin)}) as c:
        assert (await c.put("/api/captures/rooms/kitchen")).status_code == 200
    assert await cc.room_opted_in("kitchen") is True
    async with _client() as anon:
        assert (await _satellite(anon, "kitchen"))["capture_commands"] is True
    async with engine.begin() as conn:       # what --reset-admin does to the credential
        await conn.execute(text("DELETE FROM admin_auth"))
    assert await cc.room_opted_in("kitchen") is False
    await _speak("kitchen")
    assert cc.list_captures() == []
    async with _client() as anon:
        assert (await anon.get("/api/captures")).status_code == 501
        # The marker says what the core does: paused, not recording.
        assert (await _satellite(anon, "kitchen"))["capture_commands"] is False


@requires_db
@pytest.mark.asyncio
async def test_the_pruner_sweeps_a_room_whose_row_went_away(rooms, fake_turn) -> None:
    async with web_client() as setup:
        admin = await claim_admin(setup)
    async with _client({**XRW, **bearer(admin)}) as c:
        await c.put("/api/captures/rooms/kitchen")
    await _speak("kitchen")
    assert len(cc.list_captures()) == 1
    assert await cc.opted_in_rooms() != {}
    async with engine.begin() as conn:       # removed by hand, files left behind
        await conn.execute(text("TRUNCATE command_capture_rooms"))
    result = await cc.prune()
    assert result["opted_out"] == 1
    assert cc.list_captures() == []


@requires_db
@pytest.mark.asyncio
async def test_retiring_a_room_forgets_its_recordings(rooms, fake_turn) -> None:
    async with web_client() as setup:
        admin = await claim_admin(setup)
    async with _client({**XRW, **bearer(admin)}) as c:
        await c.put("/api/captures/rooms/kitchen")
    await _speak("kitchen")
    assert len(cc.list_captures()) == 1
    async with core_client() as core:
        r = await core.delete("/v1/admin/satellites/kitchen", headers=bearer(admin))
        assert r.status_code == 200, r.text
    assert cc.list_captures() == []
    assert await cc.opted_in_rooms() == {}


@requires_db
@pytest.mark.asyncio
async def test_v016_applies_again_cleanly_and_starts_empty(rooms) -> None:
    await _ensure_v016()
    async with engine.begin() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM command_capture_rooms"))).scalar_one()
        cols = (await conn.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'command_capture_rooms' ORDER BY ordinal_position"
        ))).all()
    assert n == 0
    assert [tuple(c) for c in cols] == [("room_id", "NO"), ("enabled_at", "NO")]


@requires_db
@pytest.mark.asyncio
async def test_an_unsafe_directory_refuses_the_opt_in(rooms, monkeypatch, tmp_path) -> None:
    async with web_client() as setup:
        admin = await claim_admin(setup)
    monkeypatch.setattr(settings, "music_dir", str(tmp_path / "Music"))
    monkeypatch.setattr(settings, "command_captures_dir", str(tmp_path / "Music" / "caps"))
    async with _client({**XRW, **bearer(admin)}) as c:
        r = await c.put("/api/captures/rooms/kitchen")
    assert r.status_code == 409 and "music_dir" in r.text
    assert await cc.opted_in_rooms() == {}
