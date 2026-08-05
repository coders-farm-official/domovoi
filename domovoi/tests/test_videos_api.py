"""Endpoint tests for the Videos API — web/backend/api/videos.py.

Same harness as test_files_api: lives under ``domovoi/tests`` for the
test-DB conftest safety net, registry stubbed to tmp-dir libraries so the
walk/stream/position behavior is deterministic. Poster extraction is
stubbed (no ffmpeg dependency in CI); the cache/sentinel logic is what's
under test. Admin gating passes via the pre-setup grace.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web.backend.api.videos as videos_api
from domovoi.tests.conftest import requires_db
from web.backend.api.files_security import MediaLibrary
from web.backend.main import app


@pytest.fixture
def roots(tmp_path):
    movies = tmp_path / "movies"
    usb = tmp_path / "usb"
    for d in (movies, usb):
        d.mkdir()
    return {"movies": movies, "usb": usb}


def _library(**kw) -> MediaLibrary:
    base = dict(
        id="core:documents", label="Documents", kind="core", icon="file-text",
        kind_icon="folder", owner=None, editable=True, importable=True,
        doc_editing=False, reindex_kind=None, present=True,
    )
    base.update(kw)
    return MediaLibrary(**base)


@pytest.fixture
def registry(roots, monkeypatch):
    libs = {
        "core:documents": _library(
            id="core:documents", label="Documents", root_path=roots["movies"],
        ),
        "removable:E": _library(
            id="removable:E", label="USB (E:)", kind="removable",
            icon="hard-drive", kind_icon="hard-drive", owner="/dev/sdb1",
            root_path=roots["usb"], editable=False, importable=False,
        ),
    }

    async def _build():
        return list(libs.values())

    monkeypatch.setattr(videos_api, "build_libraries", _build)
    return libs


@pytest.fixture
def poster_dir(tmp_path, monkeypatch):
    d = tmp_path / "posters"
    monkeypatch.setattr(videos_api.core_settings, "video_posters_dir", str(d))
    return d


def _client():
    return TestClient(app)


def _mkvideo(root: Path, rel: str, data: bytes = b"\x00" * 64) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ─── GET /list ──────────────────────────────────────────────────────────────
@requires_db
def test_list_walks_all_libraries_and_filters(registry, roots):
    _mkvideo(roots["movies"], "Movies/night.mkv")
    _mkvideo(roots["movies"], "clip.mp4")
    _mkvideo(roots["usb"], "trip.webm")
    _mkvideo(roots["movies"], "song.mp3")            # not a video
    _mkvideo(roots["movies"], ".hidden/secret.mp4")  # hidden dir pruned
    with _client() as c:
        r = c.get("/api/videos/list")
    assert r.status_code == 200
    vids = r.json()["videos"]
    keys = {(v["library_id"], v["rel"]) for v in vids}
    assert keys == {
        ("core:documents", "Movies/night.mkv"),
        ("core:documents", "clip.mp4"),
        ("removable:E", "trip.webm"),
    }
    night = next(v for v in vids if v["rel"] == "Movies/night.mkv")
    assert night["name"] == "night.mkv"
    assert night["library_label"] == "Documents"
    assert night["size"] == 64


# ─── GET /stream ────────────────────────────────────────────────────────────
@requires_db
def test_stream_full_and_range_and_mime(registry, roots):
    _mkvideo(roots["movies"], "clip.mkv", b"0123456789")
    with _client() as c:
        full = c.get("/api/videos/stream",
                     params={"library_id": "core:documents", "path": "clip.mkv"})
        part = c.get("/api/videos/stream",
                     params={"library_id": "core:documents", "path": "clip.mkv"},
                     headers={"Range": "bytes=2-5"})
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("video/x-matroska")
    assert full.content == b"0123456789"
    assert part.status_code == 206
    assert part.content == b"2345"
    assert part.headers["content-range"] == "bytes 2-5/10"


@requires_db
def test_stream_rejects_non_video_and_escape(registry, roots):
    _mkvideo(roots["movies"], "notes.txt")
    with _client() as c:
        bad_ext = c.get("/api/videos/stream",
                        params={"library_id": "core:documents", "path": "notes.txt"})
        escape = c.get("/api/videos/stream",
                       params={"library_id": "core:documents", "path": "../../x.mp4"})
        unknown = c.get("/api/videos/stream",
                        params={"library_id": "nope", "path": "a.mp4"})
        ejected = c.get("/api/videos/stream",
                        params={"library_id": "removable:F", "path": "a.mp4"})
    assert bad_ext.status_code == 400
    assert escape.status_code == 400
    assert unknown.status_code == 404
    assert ejected.status_code == 410


# ─── GET /poster ────────────────────────────────────────────────────────────
@requires_db
def test_poster_caches_and_sentinels(registry, roots, poster_dir, monkeypatch):
    _mkvideo(roots["movies"], "clip.mp4")
    calls = []

    async def _fake_extract(src, dest):
        calls.append(src)
        dest.write_bytes(b"jpegdata")
        return True

    monkeypatch.setattr(videos_api, "_extract_poster", _fake_extract)
    with _client() as c:
        first = c.get("/api/videos/poster",
                      params={"library_id": "core:documents", "path": "clip.mp4"})
        second = c.get("/api/videos/poster",
                       params={"library_id": "core:documents", "path": "clip.mp4"})
    assert first.status_code == 200
    assert first.content == b"jpegdata"
    assert second.status_code == 200
    assert len(calls) == 1  # second hit served from cache

    # Extraction failure → 204 + sentinel; the sentinel suppresses re-probing.
    _mkvideo(roots["movies"], "broken.webm")
    fails = []

    async def _fail_extract(src, dest):
        fails.append(src)
        return False

    monkeypatch.setattr(videos_api, "_extract_poster", _fail_extract)
    with _client() as c:
        p1 = c.get("/api/videos/poster",
                    params={"library_id": "core:documents", "path": "broken.webm"})
        p2 = c.get("/api/videos/poster",
                    params={"library_id": "core:documents", "path": "broken.webm"})
    assert p1.status_code == 204 and p2.status_code == 204
    assert len(fails) == 1


# ─── Positions ──────────────────────────────────────────────────────────────
@requires_db
def test_position_roundtrip_upsert_and_clear(registry, roots):
    _mkvideo(roots["movies"], "clip.mp4")
    key = {"library_id": "core:documents", "path": "clip.mp4", "device_id": "dev-1"}
    with _client() as c:
        empty = c.get("/api/videos/position", params=key)
        assert empty.json() == {"position_sec": 0, "duration_sec": None}

        r1 = c.post("/api/videos/position",
                    json={**key, "position_sec": 90, "duration_sec": 600, "title": "Clip"})
        assert r1.json() == {"saved": True}
        r2 = c.post("/api/videos/position", json={**key, "position_sec": 120})
        assert r2.json() == {"saved": True}

        got = c.get("/api/videos/position", params=key).json()
        # Upsert kept the earlier duration (COALESCE) and took the new position.
        assert got == {"position_sec": 120, "duration_sec": 600}

        # A different device is a separate row.
        other = c.get("/api/videos/position", params={**key, "device_id": "dev-2"}).json()
        assert other["position_sec"] == 0

        cleared = c.request("DELETE", "/api/videos/position", json=key)
        assert cleared.json() == {"cleared": True}
        assert c.get("/api/videos/position", params=key).json()["position_sec"] == 0


@requires_db
def test_recent_lists_existing_files_only(registry, roots):
    _mkvideo(roots["movies"], "keep.mp4")
    _mkvideo(roots["movies"], "gone.mp4")
    base = {"device_id": "dev-r"}
    with _client() as c:
        c.post("/api/videos/position",
               json={"library_id": "core:documents", "path": "keep.mp4",
                     **base, "position_sec": 10, "duration_sec": 100, "title": "Keep"})
        c.post("/api/videos/position",
               json={"library_id": "core:documents", "path": "gone.mp4",
                     **base, "position_sec": 20})
        c.post("/api/videos/position",
               json={"library_id": "removable:F", "path": "lost.mp4",
                     **base, "position_sec": 30})
        (roots["movies"] / "gone.mp4").unlink()

        r = c.get("/api/videos/recent", params=base)
    assert r.status_code == 200
    rows = r.json()["recent"]
    # gone.mp4 (file deleted) and removable:F (library absent) are skipped.
    assert [row["rel"] for row in rows] == ["keep.mp4"]
    row = rows[0]
    assert row["title"] == "Keep"
    assert row["position_sec"] == 10
    assert row["duration_sec"] == 100
    assert row["library_label"] == "Documents"
