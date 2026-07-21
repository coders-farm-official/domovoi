"""Tests for the web backend's POST /api/music/library/upload endpoint.

Lives under ``domovoi/tests`` for the test-DB fixtures + conftest
safety net (same rationale as ``test_radio_api.py``). The upload
endpoint touches the filesystem (``MUSIC_DIR/uploads``) and the
core admin HTTP — not the DB — so these monkeypatch
``settings.music_dir`` to a tmp dir and stub ``post_admin`` so no live
domovoi is needed.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import web.backend.api.music as music_api
from domovoi.tests.conftest import requires_db
from web.backend.main import app


@pytest.fixture
def music_dir(tmp_path, monkeypatch):
    """Point MUSIC_DIR at a throwaway tmp dir so uploads don't touch the
    real library. The endpoint reads ``settings.music_dir`` fresh on each
    call, so patching the singleton is enough."""
    from domovoi.config import settings

    monkeypatch.setattr(settings, "music_dir", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture
def stub_reindex_ok(monkeypatch):
    async def _ok(path, body=None):
        return 200, {"queued": True, "worker": "library_indexer"}

    monkeypatch.setattr(music_api, "post_admin", _ok)


@pytest.fixture
def stub_reindex_down(monkeypatch):
    async def _down(path, body=None):
        return 0, None  # 0 = domovoi unreachable

    monkeypatch.setattr(music_api, "post_admin", _down)


@requires_db
def test_upload_single_audio_saves_and_triggers_reindex(music_dir, stub_reindex_ok):
    with TestClient(app) as client:
        r = client.post(
            "/api/music/library/upload",
            files=[("files", ("song.mp3", b"ID3fakeaudio", "audio/mpeg"))],
        )
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 1
    assert body["files"] == ["song.mp3"]
    assert body["skipped"] == []
    assert body["reindex_triggered"] is True
    assert (music_dir / "uploads" / "song.mp3").read_bytes() == b"ID3fakeaudio"


@requires_db
def test_upload_zip_extracts_audio_and_skips_non_audio(music_dir, stub_reindex_ok):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.mp3", b"aaa")
        zf.writestr("b.flac", b"bbb")
        zf.writestr("cover.jpg", b"img")        # ignored (non-audio)
        zf.writestr("nested/c.wav", b"ccc")     # flattened into uploads/
    buf.seek(0)

    with TestClient(app) as client:
        r = client.post(
            "/api/music/library/upload",
            files=[("files", ("album.zip", buf.read(), "application/zip"))],
        )
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 3
    up = music_dir / "uploads"
    assert (up / "a.mp3").exists()
    assert (up / "b.flac").exists()
    assert (up / "c.wav").exists()              # directory component stripped
    assert not (up / "cover.jpg").exists()


@requires_db
def test_upload_unsupported_only_returns_400(music_dir, stub_reindex_ok):
    with TestClient(app) as client:
        r = client.post(
            "/api/music/library/upload",
            files=[("files", ("notes.txt", b"hello", "text/plain"))],
        )
    assert r.status_code == 400
    # Nothing should have been written.
    assert not (music_dir / "uploads" / "notes.txt").exists()


@requires_db
def test_upload_dedupes_colliding_filenames(music_dir, stub_reindex_ok):
    with TestClient(app) as client:
        r1 = client.post(
            "/api/music/library/upload",
            files=[("files", ("dup.mp3", b"one", "audio/mpeg"))],
        )
        r2 = client.post(
            "/api/music/library/upload",
            files=[("files", ("dup.mp3", b"two", "audio/mpeg"))],
        )
    assert r1.json()["files"] == ["dup.mp3"]
    assert r2.json()["files"] == ["dup (1).mp3"]
    up = music_dir / "uploads"
    assert (up / "dup.mp3").read_bytes() == b"one"
    assert (up / "dup (1).mp3").read_bytes() == b"two"


@requires_db
def test_upload_saves_even_when_domovoi_unreachable(music_dir, stub_reindex_down):
    with TestClient(app) as client:
        r = client.post(
            "/api/music/library/upload",
            files=[("files", ("x.mp3", b"data", "audio/mpeg"))],
        )
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 1
    assert body["reindex_triggered"] is False
    assert (music_dir / "uploads" / "x.mp3").exists()
