"""Tests for the browser music player's web-backend endpoints.

Covers the additions in ``web/backend/api/music.py``:
  * ``GET /api/music/library/{id}/audio`` — HTTP Range / 206 serving and
    the MUSIC_DIR containment guard.
  * ``GET /api/music/library/{id}/cover`` — negative-cache behavior when a
    file has no embedded art.
  * ``POST /api/music/play-tracks`` — the cast proxy to the core.

Lives under ``domovoi/tests`` for the test-DB fixtures + conftest
safety net (same rationale as ``test_music_upload_api.py``). Range
mechanics are exercised with ``_library_file_path`` monkeypatched to a
tmp file so they don't need a DB row; the containment / not-found tests
insert a real row via ``session_scope`` (committed) so they exercise the
guard end to end.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web.backend.api.music as music_api
from domovoi.tests.conftest import requires_db
from web.backend.main import app


# ─── _parse_range (pure unit, no DB) ────────────────────────────────────────


def test_parse_range_basic():
    assert music_api._parse_range("bytes=0-4", 100) == (0, 4)
    assert music_api._parse_range("bytes=10-", 100) == (10, 99)
    # Suffix range: last 20 bytes.
    assert music_api._parse_range("bytes=-20", 100) == (80, 99)
    # End past EOF clamps.
    assert music_api._parse_range("bytes=90-500", 100) == (90, 99)


def test_parse_range_rejects():
    assert music_api._parse_range(None, 100) is None
    assert music_api._parse_range("", 100) is None
    # Multi-range not supported → whole-file fallback.
    assert music_api._parse_range("bytes=0-1,5-6", 100) is None
    # start past EOF is unsatisfiable.
    assert music_api._parse_range("bytes=200-300", 100) is None
    # start > end.
    assert music_api._parse_range("bytes=50-10", 100) is None
    # Non-bytes unit.
    assert music_api._parse_range("items=0-4", 100) is None


# ─── Range serving mechanics (file monkeypatched, no DB row) ────────────────


@pytest.fixture
def fake_track_file(tmp_path, monkeypatch):
    """Point ``_library_file_path`` at a known tmp file so the streaming
    tests exercise range math without a DB row."""
    data = bytes(range(256)) * 8  # 2048 deterministic bytes
    f = tmp_path / "song.mp3"
    f.write_bytes(data)

    async def _fake(track_id: int) -> Path:
        return f

    monkeypatch.setattr(music_api, "_library_file_path", _fake)
    return f, data


@requires_db
def test_stream_audio_full_response(fake_track_file):
    f, data = fake_track_file
    with TestClient(app) as client:
        r = client.get("/api/music/library/1/audio")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == str(len(data))
    assert r.content == data


@requires_db
def test_stream_audio_range_206(fake_track_file):
    f, data = fake_track_file
    with TestClient(app) as client:
        r = client.get("/api/music/library/1/audio", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-9/{len(data)}"
    assert r.headers["content-length"] == "10"
    assert r.content == data[0:10]


@requires_db
def test_stream_audio_suffix_range(fake_track_file):
    f, data = fake_track_file
    with TestClient(app) as client:
        r = client.get("/api/music/library/1/audio", headers={"Range": "bytes=-16"})
    assert r.status_code == 206
    start = len(data) - 16
    assert r.headers["content-range"] == f"bytes {start}-{len(data) - 1}/{len(data)}"
    assert r.content == data[start:]


# ─── Containment + not-found (real DB row) ──────────────────────────────────


async def _insert_track(file_path: str) -> int:
    from sqlalchemy import text

    from web.backend.db import session_scope

    async with session_scope() as s:
        row = await s.execute(
            text(
                """
                INSERT INTO library_tracks (file_path, title, added_via)
                VALUES (:fp, :title, 'manual')
                RETURNING id
                """
            ),
            {"fp": file_path, "title": "test track"},
        )
        return int(row.scalar_one())


@requires_db
def test_stream_audio_rejects_path_outside_music_dir(tmp_path, monkeypatch):
    """A row whose file_path escapes MUSIC_DIR must be refused (400), so a
    corrupted row can't turn the endpoint into an arbitrary-file read."""
    from domovoi.config import settings

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music_dir), raising=False)

    # A real file, but OUTSIDE the configured music_dir.
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"nope")

    track_id = asyncio.run(_insert_track(str(outside)))
    with TestClient(app) as client:
        r = client.get(f"/api/music/library/{track_id}/audio")
    assert r.status_code == 400
    assert "MUSIC_DIR" in r.json()["detail"]


@requires_db
def test_stream_audio_missing_row_404(monkeypatch):
    with TestClient(app) as client:
        r = client.get("/api/music/library/999999/audio")
    assert r.status_code == 404


@requires_db
def test_cover_no_embedded_art_404_and_negative_cache(tmp_path, monkeypatch):
    """A file with no embedded art returns 404 and drops a ``.none``
    sentinel so it isn't re-probed."""
    from domovoi.config import settings

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    cover_dir = tmp_path / "covers"
    monkeypatch.setattr(settings, "music_dir", str(music_dir), raising=False)
    monkeypatch.setattr(settings, "cover_art_dir", str(cover_dir), raising=False)

    audio = music_dir / "plain.mp3"
    audio.write_bytes(b"no id3 art here")  # not a valid tagged file
    track_id = asyncio.run(_insert_track(str(audio)))

    with TestClient(app) as client:
        r = client.get(f"/api/music/library/{track_id}/cover")
    assert r.status_code == 404
    assert (cover_dir / f"{track_id}.none").is_file()


# ─── play-tracks cast proxy ─────────────────────────────────────────────────


@requires_db
def test_play_tracks_proxies_to_domovoi(monkeypatch):
    captured = {}

    async def _fake_post_admin(path, body=None):
        captured["path"] = path
        captured["body"] = body
        return 200, {"played": True, "queued": 2, "requested": 2}

    monkeypatch.setattr(music_api, "post_admin", _fake_post_admin)
    with TestClient(app) as client:
        r = client.post(
            "/api/music/play-tracks",
            json={"room_id": "kitchen", "track_ids": [3, 7]},
        )
    assert r.status_code == 200
    assert r.json()["queued"] == 2
    assert captured["path"] == "/v1/admin/music/play-tracks"
    assert captured["body"] == {"room_id": "kitchen", "track_ids": [3, 7]}


@requires_db
def test_play_tracks_domovoi_unreachable_502(monkeypatch):
    async def _down(path, body=None):
        return 0, None

    monkeypatch.setattr(music_api, "post_admin", _down)
    with TestClient(app) as client:
        r = client.post(
            "/api/music/play-tracks",
            json={"room_id": "kitchen", "track_ids": [1]},
        )
    assert r.status_code == 502
