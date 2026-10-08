"""The open library search matches the path a client is shown, never the
server's absolute one (WEB-16, the search half).

``GET /api/music/library`` serialises a library-relative ``file_path``
since WEB-16, but its ``q`` filter still ran ``file_path ILIKE '%q%'``
against the STORED absolute path. The route is open, so ``total`` was an
oracle: ``q=/home/k`` -> 1, ``q=/home/j`` -> 0, and the operator's username
and directory layout came back a character at a time. The path half of the
search now sees exactly what :func:`public_track_path` shows — the part
below ``MUSIC_DIR`` (``/`` separators), or the file name for a row outside
it — so searching by folder or file name still works and nothing above the
library is searchable.

Needs the database: the filter is SQL. Rows are tagged with a per-run
marker and removed afterwards.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi.config import settings
from domovoi.tests.conftest import requires_db
from web.backend.main import app

pytestmark = requires_db

RUN = uuid.uuid4().hex[:10]


async def _insert(paths: list[str]) -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        for fp in paths:
            await s.execute(
                text(
                    "INSERT INTO library_tracks (file_path, title, added_via) "
                    "VALUES (:fp, :title, 'manual')"
                ),
                {"fp": fp, "title": f"b2-search-{RUN}"},
            )


async def _remove() -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        await s.execute(
            text("DELETE FROM library_tracks WHERE title = :title"),
            {"title": f"b2-search-{RUN}"},
        )


@pytest.fixture
def rows(monkeypatch):
    posix_root = f"/srv/zzoracle{RUN}/Music"
    win_root = f"C:\\Users\\Zzwin{RUN}\\Music"
    paths = [
        f"{posix_root}/Artist Q{RUN}/01 zzsong{RUN}.mp3",
        f"/mnt/zzelsewhere{RUN}/zzstray{RUN}.flac",          # outside MUSIC_DIR
        # Stored lower-cased with backslashes, the way a Windows indexer can
        # leave it; the root below is spelled differently on purpose.
        f"c:\\users\\zzwin{RUN}\\music\\Folder W{RUN}\\zzwin{RUN}.mp3",
    ]
    asyncio.run(_insert(paths))
    try:
        yield posix_root, win_root
    finally:
        asyncio.run(_remove())


def _total(music_dir: str, monkeypatch, q: str) -> int:
    monkeypatch.setattr(settings, "music_dir", music_dir, raising=False)
    with TestClient(app) as client:
        r = client.get("/api/music/library", params={"q": q, "limit": 5})
    assert r.status_code == 200, r.text
    return int(r.json()["total"])


@pytest.mark.parametrize(
    "probe",
    [
        "/srv/zzoracle{run}",       # the prefix as typed
        "zzoracle{run}",            # any piece of it
        "zzoracle{run}/Music",
        "/Music/Artist",            # the root's last segment and what follows
        "zzelsewhere{run}",         # a stray row's folder
    ],
)
def test_nothing_above_the_library_is_searchable(rows, monkeypatch, probe):
    posix_root, _ = rows
    assert _total(posix_root, monkeypatch, probe.format(run=RUN)) == 0


@pytest.mark.parametrize(
    "probe",
    [
        "Artist Q{run}",                     # a folder under the library
        "Artist Q{run}/01 zzsong{run}",      # shown with / separators
        "zzsong{run}",                       # the file name
        "zzstray{run}",                      # a stray row: its file name only
    ],
)
def test_what_a_client_is_shown_is_still_searchable(rows, monkeypatch, probe):
    posix_root, _ = rows
    assert _total(posix_root, monkeypatch, probe.format(run=RUN)) == 1


def test_a_windows_root_is_matched_like_the_path_it_shows(rows, monkeypatch):
    """Case- and separator-blind, as ``PureWindowsPath`` is: the user's
    folder never matches, the folder below the library does — with the
    ``/`` the client is shown."""
    _, win_root = rows
    assert _total(win_root, monkeypatch, f"Zzwin{RUN}\\Music") == 0
    assert _total(win_root, monkeypatch, f"users/zzwin{RUN}") == 0
    assert _total(win_root, monkeypatch, f"Folder W{RUN}/zzwin{RUN}") == 1
