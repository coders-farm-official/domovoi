"""Music upload names are judged as they will be stored (WEB-17).

f4421ab taught every Files and Documents write to refuse a name the
filesystem would store as something else — on NTFS ``song:x.mp3`` is an
alternate data stream ``x.mp3`` hanging off a 0-byte file ``song``, and a
trailing dot or space is trimmed away. The music library upload only
basenamed the name and checked its suffix (``.mp3``), so the same shape
landed in ``MUSIC_DIR/uploads`` invisible to every listing, zip and the
indexer's walk, while the in-request index still put it in the library and
``/audio`` served it back.

DB-free: the endpoint is driven over HTTP on a pre-setup install (the auth
primitives are faked), with the post-write index and the MPD hop stubbed.
The Windows rules are switched on through ``files_security``'s own module
flag, so this runs the same on a Linux runner; the cases that would create
a stream on THIS box are only ever expected to be refused.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import domovoi.workers.library_indexer as library_indexer
import web.backend.api.files_security as files_security
import web.backend.api.music as music_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import install_fake_db
from web.backend.main import app

MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _pre_setup(monkeypatch):
    install_fake_db(monkeypatch, admin=False)


@pytest.fixture
def music_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "music_dir", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture
def stubs(monkeypatch):
    indexed: list[list[str]] = []

    async def _post(path, body=None, headers=None):
        return 200, {"ok": True}

    async def _index(paths):
        names = [p.name for p in paths]
        indexed.append(names)
        return {"scanned": len(names), "inserted": len(names), "skipped": 0, "errors": 0}

    monkeypatch.setattr(music_api, "post_admin", _post)
    monkeypatch.setattr(library_indexer, "index_paths", _index)
    return indexed


@pytest.fixture
def windows_rules(monkeypatch):
    monkeypatch.setattr(files_security, "WINDOWS_NAME_RULES", True)


def _upload(*names: str):
    files = [("files", (n, MP3, "audio/mpeg")) for n in names]
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"}).post(
        "/api/music/library/upload", files=files
    )


def _on_disk(music_dir) -> list[str]:
    up = music_dir / "uploads"
    return sorted(p.name for p in up.iterdir()) if up.exists() else []


def test_a_stream_name_is_refused_on_a_windows_host(music_dir, stubs, windows_rules):
    r = _upload("song:x.mp3")
    assert r.status_code == 400, r.text
    assert "alternate data stream" in r.json()["detail"]
    assert _on_disk(music_dir) == []
    assert stubs == []


def test_a_stream_name_in_a_zip_is_refused_too(music_dir, stubs, windows_rules):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("album/song:x.mp3", MP3)
        z.writestr("album/ok.mp3", MP3)
    r = TestClient(app, headers={"X-Requested-With": "domovoi-tests"}).post(
        "/api/music/library/upload",
        files={"files": ("album.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] == ["ok.mp3"]
    assert any("alternate data stream" in s for s in body["skipped"]), body
    assert _on_disk(music_dir) == ["ok.mp3"]
    assert stubs == [["ok.mp3"]]


def test_a_control_character_is_refused_everywhere(music_dir, stubs):
    """A zip member, because a multipart filename is percent-encoded on
    the way in and could not carry the byte."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("so\x01ng.mp3", MP3)
        z.writestr("fine.mp3", MP3)
    r = TestClient(app, headers={"X-Requested-With": "domovoi-tests"}).post(
        "/api/music/library/upload",
        files={"files": ("mix.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["files"] == ["fine.mp3"]
    assert any("control characters" in s for s in body["skipped"]), body
    assert _on_disk(music_dir) == ["fine.mp3"]


def test_an_ordinary_name_still_uploads(music_dir, stubs, windows_rules):
    r = _upload("Artist - Song.mp3")
    assert r.status_code == 200, r.text
    assert _on_disk(music_dir) == ["Artist - Song.mp3"]
