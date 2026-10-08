"""The open music library reads never serialise an absolute server path
(WEB-16).

``GET /api/music/library`` and ``/library/{id}`` are open, and each track's
``file_path`` was ``library_tracks.file_path`` verbatim — ``/home/<user>/
Music/...`` or ``C:\\Users\\<user>\\Music\\...`` — so any LAN caller learnt the
operator's username and directory layout. The podcasts and audiobooks
routers already return only an extension. What the dashboard and the
Android app use the field for is a display line and its last segment (a
title fallback, the save-to-device name), so it is now library-relative:
every client keeps working and nothing above ``MUSIC_DIR`` is shown. The
``/audio`` / ``/cover`` refusals stop echoing the path too.

DB-free: the library query is answered by a fake session.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import web.backend.api.music as music_api
import web.backend.api.playlists as playlists_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import install_fake_db
from web.backend.main import app

ADDED = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _row(track_id: int, file_path: str) -> tuple:
    return (track_id, file_path, "Song", "Artist", "Album", 200, None, None, None,
            ADDED, None, None, False)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one(self):
        return len(self._rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def library(monkeypatch, tmp_path):
    root = tmp_path / "Music"
    (root / "Artist").mkdir(parents=True)
    monkeypatch.setattr(settings, "music_dir", str(root), raising=False)
    rows = [
        _row(1, str(root / "Artist" / "01 Song.mp3")),
        _row(2, str(root / "uploads" / "song.mp3")),
        _row(3, str(tmp_path / "old-library" / "stray.flac")),   # outside MUSIC_DIR
    ]

    class _Session:
        async def execute(self, *_a, **_kw):
            return _Result(rows)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(music_api, "session_scope", _scope)
    install_fake_db(monkeypatch, admin=True)
    return root, rows


def _serve_only(monkeypatch, row) -> None:
    """The /audio lookup selects ``file_path`` alone."""

    class _One:
        async def execute(self, *_a, **_kw):
            return _Result([(row[1],)])

    @asynccontextmanager
    async def _scope():
        yield _One()

    monkeypatch.setattr(music_api, "session_scope", _scope)


def test_the_library_page_shows_library_relative_paths(library):
    root, _rows = library
    r = TestClient(app).get("/api/music/library", params={"limit": 3})
    assert r.status_code == 200, r.text
    paths = [t["file_path"] for t in r.json()["items"]]
    assert paths == ["Artist/01 Song.mp3", "uploads/song.mp3", "stray.flac"]
    assert str(root) not in r.text
    assert str(root.parent) not in r.text


def test_one_track_reads_the_same(library):
    r = TestClient(app).get("/api/music/library/1")
    assert r.status_code == 200, r.text
    assert r.json()["file_path"] == "Artist/01 Song.mp3"


def test_a_missing_file_does_not_echo_the_path(library, monkeypatch):
    """``/audio`` (open) answered "file missing on disk: /home/<user>/...";
    the refusal names the track now and nothing else."""
    root, rows = library
    _serve_only(monkeypatch, rows[0])
    r = TestClient(app).get("/api/music/library/1/audio")
    assert r.status_code == 404, r.text
    assert str(root) not in r.text and "Music" not in r.text


def test_a_row_outside_the_library_is_refused_without_its_path(library, monkeypatch):
    root, rows = library
    _serve_only(monkeypatch, rows[2])
    r = TestClient(app).get("/api/music/library/3/audio")
    assert r.status_code == 400, r.text
    assert "old-library" not in r.text and str(root) not in r.text


@pytest.mark.parametrize(
    ("music_dir", "stored", "shown"),
    [
        ("/home/kamron/Music", "/home/kamron/Music/A/B/c.mp3", "A/B/c.mp3"),
        ("/home/kamron/Music/", "/home/kamron/Music/c.mp3", "c.mp3"),
        ("/home/kamron/Music", "/mnt/elsewhere/c.mp3", "c.mp3"),
        (r"C:\Users\Kamron\Music", r"C:\Users\Kamron\Music\A\c.mp3", "A/c.mp3"),
        (r"C:\Users\Kamron\Music", r"c:\users\kamron\music\A\c.mp3", "A/c.mp3"),
        ("C:/Users/Kamron/Music", r"C:\Users\Kamron\Music\A\c.mp3", "A/c.mp3"),
        (r"C:\Users\Kamron\Music", r"D:\Other\c.mp3", "c.mp3"),
        ("/home/kamron/Music", "", ""),
    ],
)
def test_the_public_path_rule(monkeypatch, music_dir, stored, shown):
    monkeypatch.setattr(settings, "music_dir", music_dir, raising=False)
    assert music_api.public_track_path(stored) == shown


def test_playlist_tracks_use_the_same_rule(monkeypatch):
    monkeypatch.setattr(settings, "music_dir", "/home/kamron/Music", raising=False)
    t = playlists_api._row_to_track(_row(9, "/home/kamron/Music/A/c.mp3"))
    assert t.file_path == "A/c.mp3"
