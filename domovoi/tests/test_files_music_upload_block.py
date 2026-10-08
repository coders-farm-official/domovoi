"""The Files write block covers the music library's own upload door
(WEB-12).

``files_device_blocks`` reads "block a device from uploading, moving or
importing ANYWHERE", and the Files and Documents doors both consult it.
``POST /api/music/library/upload`` writes into the same ``core:music``
library ``POST /api/files/upload`` does, and asked nobody: a tablet an admin
had blocked under Settings → Files blocks kept filling the music library
through it (phase-2: 200, file landed and indexed, while the Files door
answered 403 for the same device).

DB-free: the auth primitives are faked, the one block read is stubbed on
``files`` (so the test also proves the music door asks THAT module), and
the post-write index and the MPD hop are stubbed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import domovoi.workers.library_indexer as library_indexer
import web.backend.api.files as files_api
import web.backend.api.music as music_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import HEADER, install_fake_db
from web.backend.api.devices import DEVICE_ID_COOKIE
from web.backend.main import app

DEVICE_TOKEN = "device-token"
BLOCKED_ID = "tablet-blocked-01"
OTHER_ID = "phone-allowed-01"
BLOCK_MESSAGE = "Kids tablet isn't allowed to change files"

MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, device_token=DEVICE_TOKEN)


@pytest.fixture
def music_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "music_dir", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture
def one_device_is_blocked(monkeypatch):
    async def _status(device_id):
        return BLOCK_MESSAGE if device_id == BLOCKED_ID else None

    monkeypatch.setattr(files_api, "_block_status", _status)


@pytest.fixture
def hops(monkeypatch):
    calls: list[str] = []

    async def _post(path, body=None, headers=None):
        calls.append(path)
        return 200, {"ok": True}

    async def _index(paths):
        n = len(list(paths))
        return {"scanned": n, "inserted": n, "skipped": 0, "errors": 0}

    monkeypatch.setattr(music_api, "post_admin", _post)
    monkeypatch.setattr(library_indexer, "index_paths", _index)
    return calls


def _client(cookies=None, headers=None) -> TestClient:
    c = TestClient(
        app,
        headers={"X-Requested-With": "domovoi-tests", HEADER: DEVICE_TOKEN, **(headers or {})},
    )
    for k, v in (cookies or {}).items():
        c.cookies.set(k, v)
    return c


def _written(music_dir) -> list[str]:
    up = music_dir / "uploads"
    return sorted(p.name for p in up.iterdir()) if up.exists() else []


@pytest.mark.parametrize(
    "how",
    ["cookie", "header", "form"],
)
def test_a_blocked_device_cannot_upload_into_the_music_library(
    claimed, music_dir, one_device_is_blocked, hops, how
):
    """The phase-2 repro, closed on every identity a caller can carry."""
    cookies = {DEVICE_ID_COOKIE: BLOCKED_ID} if how == "cookie" else None
    headers = {"X-Device-Id": BLOCKED_ID} if how == "header" else None
    data = {"device_id": BLOCKED_ID} if how == "form" else None
    r = _client(cookies=cookies, headers=headers).post(
        "/api/music/library/upload",
        files={"files": ("song.mp3", MP3, "audio/mpeg")},
        data=data,
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == BLOCK_MESSAGE
    assert _written(music_dir) == []
    assert hops == []          # no MPD rescan for an upload that never happened


def test_a_device_that_is_not_blocked_still_uploads(
    claimed, music_dir, one_device_is_blocked, hops
):
    r = _client(cookies={DEVICE_ID_COOKIE: OTHER_ID}).post(
        "/api/music/library/upload",
        files={"files": ("song.mp3", MP3, "audio/mpeg")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["files"] == ["song.mp3"]
    assert _written(music_dir) == ["song.mp3"]


def test_an_unidentified_caller_is_not_blocked(claimed, music_dir, one_device_is_blocked, hops):
    """The limit ``caller_device_id`` states, kept honest: a caller that
    names itself nowhere (curl; today's Android app, which keeps no cookie
    jar) is not identified, so no block can match it."""
    r = _client().post(
        "/api/music/library/upload",
        files={"files": ("song.mp3", MP3, "audio/mpeg")},
    )
    assert r.status_code == 200, r.text
