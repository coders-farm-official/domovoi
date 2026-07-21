"""Tests for SpokenAudioHandler (podcasts + audiobooks).

Covers the plan's verification list for the handler:
  * fast-path regexes parse the vocabulary AND don't shadow / aren't
    shadowed by MusicHandler's greedy `^play (.+)$`.
  * playback (play book / resume) drives MPD + writes a resume position.
  * `fallback_offline` serves local content but refuses subscribe.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from domovoi.clients.mpd import MPDStubClient
from domovoi.config import settings
from domovoi.handlers import HANDLER_BY_NAME, HANDLERS
from domovoi.handlers.music import MusicHandler
from domovoi.handlers.spoken_audio import (
    SpokenAudioHandler,
    _NEXT_CHAPTER_RE,
    _PLAY_BOOK_RE,
    _PLAY_LATEST_RE,
    _RESUME_RE,
    _SET_SPEED_RE,
    _SKIP_RE,
    _SUBSCRIBE_RE,
)
from domovoi.models import Context, Intent
from domovoi import spoken_audio as sa
from domovoi.tests.conftest import requires_db


# ─── Pure regex tests ───────────────────────────────────────────────────
def test_play_latest_regex() -> None:
    # Explicit podcast phrasing — "episode of X" or "X podcast/show".
    m = _PLAY_LATEST_RE.match("play the latest episode of the daily")
    assert m and m.group("show") == "the daily"
    m2 = _PLAY_LATEST_RE.match("play the newest episode of radiolab")
    assert m2 and m2.group("show") == "radiolab"
    m3 = _PLAY_LATEST_RE.match("play the latest radiolab podcast")
    assert m3 and m3.group("show2") == "radiolab"


def test_play_latest_only_claims_explicit_podcast_phrasing() -> None:
    """Bare/ambiguous "play the latest X" must NOT be claimed here — it falls
    through to MusicHandler, which cascades local → subscribed podcast →
    a streaming provider (music-first). Regression: 'play the latest from TI' was answered
    'no subscribed podcast matching from ti', and 'play the latest Wait What'
    jumped straight to podcasts instead of trying music first."""
    for phrase in (
        "play the latest from ti",
        "play the latest song from ti",
        "play the latest ti song",
        "play the newest ti track",
        "play the latest single by drake",
        "play the latest wait what",       # bare name → cascade, not fast path
        "play the latest the daily",
    ):
        assert _PLAY_LATEST_RE.match(phrase) is None, f"{phrase!r} should fall through to music"


def test_play_book_regex() -> None:
    m = _PLAY_BOOK_RE.match("play the audiobook dune")
    assert m and m.group("book") == "dune"
    assert _PLAY_BOOK_RE.match("read the book mistborn")


def test_resume_requires_noun() -> None:
    # Requires a spoken-audio noun so bare resume/continue drop to music.
    assert _RESUME_RE.match("resume my book")
    assert _RESUME_RE.match("continue the podcast")
    assert _RESUME_RE.match("keep reading")
    assert not _RESUME_RE.match("resume")
    assert not _RESUME_RE.match("continue the music")


def test_skip_and_chapter_regex() -> None:
    m = _SKIP_RE.match("skip forward 30 seconds")
    assert m and m.group("n") == "30" and m.group("dir") == "forward"
    b = _SKIP_RE.match("skip back 15 seconds")
    assert b and b.group("dir") == "back"
    assert _NEXT_CHAPTER_RE.match("next chapter")


def test_set_speed_regex() -> None:
    m = _SET_SPEED_RE.match("set speed to 1.5x")
    assert m and m.group("speed") == "1.5"
    assert _SET_SPEED_RE.match("playback speed 2")


def test_subscribe_regex() -> None:
    m = _SUBSCRIBE_RE.match("subscribe to the daily")
    assert m and m.group("show") == "the daily"


def test_does_not_shadow_music_play() -> None:
    """None of the spoken-audio fast paths may catch a plain "play X"."""
    h = SpokenAudioHandler()
    for pattern, _ in h.fast_paths:
        assert not pattern.match("play lofi hip hop")
        assert not pattern.match("play paranoid android by radiohead")


def test_ordering_before_music() -> None:
    spoken = HANDLER_BY_NAME["spoken_audio"]
    music = HANDLER_BY_NAME["music"]
    assert spoken.priority_band < music.priority_band
    # The bundled radio plugin declares band 280 — spoken_audio's anchored
    # phrasings must stay ahead of it too (design §4.2: 270 < 280).
    assert spoken.priority_band < 280


# ─── Behavior: play an audiobook + write a resume position ──────────────
@requires_db
@pytest.mark.asyncio
async def test_play_book_starts_and_records_position(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path))
    from domovoi.clients import mpd as mpd_module
    mpd_module._clients = {"kitchen": MPDStubClient()}

    # A real file inside audiobooks_dir so mpd_uri_for resolves.
    book_file = tmp_path / "dune.m4b"
    book_file.write_bytes(b"fake")
    await db_session.execute(
        text(
            "INSERT INTO audiobooks (title, file_path, duration_sec) "
            "VALUES ('Dune', :fp, 3600)"
        ),
        {"fp": str(book_file)},
    )
    await db_session.commit()

    h = SpokenAudioHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True, person_id=None)
    m = _PLAY_BOOK_RE.match("play the audiobook dune")
    resp = await h._play_book_from_match(m, ctx, db_session)
    await db_session.commit()

    assert resp.music_action == "start"
    assert "dune" in resp.text.lower()
    # A position row exists for (audiobook, kitchen, anon).
    pos = await sa.get_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=1,
        device_id="kitchen", person_id=None,
    )
    assert pos is not None and pos["position_sec"] == 0


@requires_db
@pytest.mark.asyncio
async def test_resume_seeks_to_saved_position(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path))
    from domovoi.clients import mpd as mpd_module
    stub = MPDStubClient()
    mpd_module._clients = {"kitchen": stub}

    book_file = tmp_path / "book.m4b"
    book_file.write_bytes(b"fake")
    await db_session.execute(
        text("INSERT INTO audiobooks (title, file_path) VALUES ('Book', :fp)"),
        {"fp": str(book_file)},
    )
    # Pre-existing position at 120s for this device (anon person).
    await sa.upsert_position(
        db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=1,
        device_id="kitchen", person_id=None, position_sec=120,
    )
    await db_session.commit()

    h = SpokenAudioHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True, person_id=None)
    m = _RESUME_RE.match("resume my book")
    resp = await h._resume_from_match(m, ctx, db_session)
    await db_session.commit()

    assert resp.music_action == "start"
    assert "resuming" in resp.text.lower()
    # Stub song carries the sought position.
    assert stub._song is not None
    assert stub._song.get("_elapsed") == 120.0


@requires_db
@pytest.mark.asyncio
async def test_fallback_offline_refuses_subscribe(db_session) -> None:
    h = SpokenAudioHandler()
    ctx = Context(session_id=None, room_id="kitchen", online=False)
    intent = Intent(transcript="subscribe to the daily", room_id="kitchen")
    resp = await h.fallback_offline(intent, ctx, db_session)
    assert "internet" in resp.text.lower() or "connection" in resp.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_fallback_offline_plays_local_book(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path))
    from domovoi.clients import mpd as mpd_module
    mpd_module._clients = {"kitchen": MPDStubClient()}
    book_file = tmp_path / "b.m4b"
    book_file.write_bytes(b"fake")
    await db_session.execute(
        text("INSERT INTO audiobooks (title, file_path) VALUES ('Offline Book', :fp)"),
        {"fp": str(book_file)},
    )
    await db_session.commit()

    h = SpokenAudioHandler()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=False)
    intent = Intent(transcript="play the audiobook offline book", room_id="kitchen")
    resp = await h.fallback_offline(intent, ctx, db_session)
    assert resp.music_action == "start"


# ─── Subscribe is a network-only path → offline_ok=False (router fallback) ──
def test_subscribe_fast_path_is_offline_gated() -> None:
    """The subscribe fast path (discovery + iTunes lookup) is the one
    network-only path on this degraded handler and MUST be registered
    offline_ok=False, so the router auto-falls-back instead of dispatching
    it into a doomed network call while offline."""
    h = SpokenAudioHandler()
    subs = [fp for fp in h.fast_paths if fp.pattern is _SUBSCRIBE_RE]
    assert subs and subs[0].offline_ok is False
    # Every OTHER path stays offline-capable (default None ⇒ True).
    for fp in h.fast_paths:
        if fp.pattern is not _SUBSCRIBE_RE:
            assert fp.offline_ok is None


@requires_db
@pytest.mark.asyncio
async def test_router_falls_back_subscribe_when_offline(db_session) -> None:
    """ctx.online=False + 'subscribe to the daily podcast' → the router
    routes to fallback_offline (matched_path 'fast_offline'), never into
    the network subscribe path."""
    from domovoi.router import route

    ctx = Context(room_id="kitchen", online=False)
    resp = await route(
        Intent(transcript="subscribe to the daily podcast", room_id="kitchen"),
        ctx, db_session,
    )
    await db_session.commit()
    assert resp.matched_handler == "spoken_audio"
    assert resp.matched_path == "fast_offline"
    assert "internet" in resp.text.lower() or "connection" in resp.text.lower()
