"""Tests for save-to-device downloads (music / podcasts / audiobooks).

Covers the additions for downloading media from the web UI / Android app to
the requesting device:
  * ``audio_serve.safe_download_name`` / ``attachment_headers`` — filename
    scrubbing + Content-Disposition building (pure unit).
  * ``serve_audio_range(..., download_name=...)`` — attachment header on
    both 200 and 206 responses (Request built by hand, no DB).
  * ``GET /api/music/library/{id}/audio?download=1`` — attachment variant
    of the existing Range route (``_library_file_path`` monkeypatched, same
    trick as test_browser_player_api.py).
  * ``GET /api/podcasts/subscriptions/{id}/episodes`` — exposes ``file_ext``
    but never the server ``file_path`` (real DB rows).
  * ``GET /api/podcasts/episodes/{id}/audio?download=1`` — attachment with
    a title-derived filename (real DB rows).
  * ``GET /api/audiobooks/{id}/download`` — single-file books come back as
    an attachment; folder books come back as a zip of the chapter files.

Lives under ``domovoi/tests`` for the test-DB fixtures + conftest
safety net (same rationale as ``test_browser_player_api.py``).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import web.backend.api.music as music_api
from domovoi.config import settings
from domovoi.tests.conftest import requires_db
from web.backend.api import audio_serve
from web.backend.main import app


# ─── safe_download_name (pure unit) ─────────────────────────────────────────


def test_safe_download_name_scrubs_unsafe_chars():
    assert audio_serve.safe_download_name('a/b\\c:d*e?f"g<h>i|j') == "a b c d e f g h i j"
    # Control chars collapse into the same single spaces.
    assert audio_serve.safe_download_name("x\x00\x1fy") == "x y"


def test_safe_download_name_trims_and_falls_back():
    assert audio_serve.safe_download_name("  .. ") == "audio"
    assert audio_serve.safe_download_name("", fallback="episode") == "episode"
    # Trailing dots/spaces (Windows-hostile) are stripped.
    assert audio_serve.safe_download_name("song. ") == "song"


def test_safe_download_name_caps_length():
    assert len(audio_serve.safe_download_name("x" * 500)) == 150


def test_attachment_headers_ascii_and_utf8():
    h = audio_serve.attachment_headers("naïve song.mp3")
    cd = h["Content-Disposition"]
    assert cd.startswith("attachment; ")
    # ASCII fallback replaces the non-ASCII char…
    assert 'filename="na?ve song.mp3"' in cd
    # …and the RFC 5987 form carries the real name.
    assert "filename*=UTF-8''na%C3%AFve%20song.mp3" in cd


# ─── serve_audio_range attachment mode (no DB) ──────────────────────────────


def _request(headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


@pytest.fixture
def audio_file(tmp_path) -> Path:
    f = tmp_path / "ep.mp3"
    f.write_bytes(bytes(range(256)) * 4)
    return f


def test_serve_audio_range_inline_has_no_disposition(audio_file):
    resp = audio_serve.serve_audio_range(audio_file, _request())
    assert resp.status_code == 200
    assert "content-disposition" not in resp.headers


def test_serve_audio_range_download_sets_attachment(audio_file):
    resp = audio_serve.serve_audio_range(audio_file, _request(), download_name="ep.mp3")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment; ")
    assert 'filename="ep.mp3"' in resp.headers["content-disposition"]


def test_serve_audio_range_download_survives_range_request(audio_file):
    resp = audio_serve.serve_audio_range(
        audio_file, _request({"Range": "bytes=0-9"}), download_name="ep.mp3"
    )
    assert resp.status_code == 206
    assert resp.headers["content-disposition"].startswith("attachment; ")


# ─── Music: ?download=1 on the existing Range route ─────────────────────────


@pytest.fixture
def fake_track_file(tmp_path, monkeypatch):
    data = bytes(range(256)) * 8
    f = tmp_path / "Artist - Song.mp3"
    f.write_bytes(data)

    async def _fake(track_id: int) -> Path:
        return f

    monkeypatch.setattr(music_api, "_library_file_path", _fake)
    return f, data


@requires_db
def test_music_audio_download_flag(fake_track_file):
    f, data = fake_track_file
    with TestClient(app) as client:
        inline = client.get("/api/music/library/1/audio")
        dl = client.get("/api/music/library/1/audio", params={"download": 1})
    assert "content-disposition" not in inline.headers
    assert dl.status_code == 200
    assert dl.headers["content-disposition"].startswith("attachment; ")
    assert 'filename="Artist - Song.mp3"' in dl.headers["content-disposition"]
    assert dl.content == data


# ─── Podcasts: file_ext exposure + download variant (real DB rows) ──────────


async def _insert_episode(file_path: str | None, title: str = "Ep 1: The/Pilot") -> tuple[int, int]:
    import uuid

    from sqlalchemy import text

    from web.backend.db import session_scope

    async with session_scope() as s:
        sub_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO podcast_subscriptions (feed_url, title)
                    VALUES (:url, 'Test Show')
                    RETURNING id
                    """
                ),
                # feed_url is UNIQUE and rows persist across tests in a run.
                {"url": f"https://example.test/feed-{uuid.uuid4().hex}.xml"},
            )
        ).scalar_one()
        ep_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO podcast_episodes
                        (subscription_id, guid, title, file_path, download_status)
                    VALUES (:sid, 'guid-1', :title, :fp, :status)
                    RETURNING id
                    """
                ),
                {
                    "sid": sub_id,
                    "title": title,
                    "fp": file_path,
                    "status": "pending" if file_path is None else "downloaded",
                },
            )
        ).scalar_one()
        return int(sub_id), int(ep_id)


@requires_db
def test_episode_list_exposes_ext_not_path(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))
    f = tmp_path / "show" / "ep1.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    sub_id, _ = asyncio.run(_insert_episode(str(f)))

    with TestClient(app) as client:
        rows = client.get(f"/api/podcasts/subscriptions/{sub_id}/episodes").json()
    assert rows[0]["file_ext"] == ".mp3"
    assert rows[0]["has_file"] is True
    assert "file_path" not in rows[0]


@requires_db
def test_episode_download_attachment_filename(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))
    f = tmp_path / "show" / "ep1.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"episode-bytes")
    _, ep_id = asyncio.run(_insert_episode(str(f)))

    with TestClient(app) as client:
        inline = client.get(f"/api/podcasts/episodes/{ep_id}/audio")
        dl = client.get(f"/api/podcasts/episodes/{ep_id}/audio", params={"download": 1})
    assert "content-disposition" not in inline.headers
    assert dl.status_code == 200
    # Title-derived, scrubbed ("/" is unsafe), with the on-disk extension.
    assert 'filename="Ep 1 The Pilot.mp3"' in dl.headers["content-disposition"]
    assert dl.content == b"episode-bytes"


# ─── Audiobooks: whole-book download (real DB rows) ─────────────────────────


async def _insert_book(file_path: str, is_folder: bool, title: str = "Dune") -> int:
    from sqlalchemy import text

    from web.backend.db import session_scope

    async with session_scope() as s:
        book_id = (
            await s.execute(
                text(
                    """
                    INSERT INTO audiobooks (title, file_path, is_folder, added_via)
                    VALUES (:title, :fp, :folder, 'index')
                    RETURNING id
                    """
                ),
                {"title": title, "fp": file_path, "folder": is_folder},
            )
        ).scalar_one()
        return int(book_id)


@requires_db
def test_audiobook_single_file_download(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path))
    f = tmp_path / "dune.m4b"
    f.write_bytes(b"book-bytes")
    book_id = asyncio.run(_insert_book(str(f), is_folder=False))

    with TestClient(app) as client:
        r = client.get(f"/api/audiobooks/{book_id}/download")
        rows = client.get("/api/audiobooks").json()
    assert r.status_code == 200
    assert 'filename="Dune.m4b"' in r.headers["content-disposition"]
    assert r.content == b"book-bytes"
    book = next(b for b in rows if b["id"] == book_id)
    assert book["file_ext"] == ".m4b"
    assert "file_path" not in book


@requires_db
def test_audiobook_folder_download_zips_chapters(tmp_path, monkeypatch):
    import asyncio
    import glob
    import tempfile

    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path))
    folder = tmp_path / "dune"
    folder.mkdir()
    (folder / "ch1.mp3").write_bytes(b"one")
    (folder / "ch2.mp3").write_bytes(b"two")
    (folder / "notes.txt").write_bytes(b"not audio")
    book_id = asyncio.run(_insert_book(str(folder), is_folder=True))

    tmp_glob = str(Path(tempfile.gettempdir()) / "domovoi-book-*.zip")
    before = set(glob.glob(tmp_glob))
    with TestClient(app) as client:
        r = client.get(f"/api/audiobooks/{book_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="Dune.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["Dune/ch1.mp3", "Dune/ch2.mp3"]
        assert zf.read("Dune/ch1.mp3") == b"one"
    # The temp zip is cleaned up by the response's background task.
    assert set(glob.glob(tmp_glob)) == before


@requires_db
def test_audiobook_folder_outside_dir_refused(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path / "books"))
    (tmp_path / "books").mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "ch1.mp3").write_bytes(b"x")
    book_id = asyncio.run(_insert_book(str(outside), is_folder=True))

    with TestClient(app) as client:
        r = client.get(f"/api/audiobooks/{book_id}/download")
    assert r.status_code == 400
