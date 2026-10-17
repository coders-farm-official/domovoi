"""``files.changed``: the filesystem libraries say when they changed.

Videos, Pictures and Documents have no database index (they are walked
from disk, ``listing_cache``), so no NOTIFY ever told a page on screen that
a phone had uploaded into them (owner report 2026-10-17: videos uploaded
from the app did not show until he left the page). The web now counts
every change per library (``listing_cache.changes``) — a write through
this process (``invalidate``) or a change on disk under a listing someone
is viewing (``sweep_in_use``, on the state poll's tick) — and the ``files``
realtime channel pushes the counts as ``files.changed``.

DB-free: a real folder under ``tmp_path``, the real Videos walk
(``videos._library_snapshot``) and the real ``StatePollLoop``.
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock

import pytest

from web.backend.api import listing_cache
from web.backend.api import videos as videos_api
from web.backend.api.files_security import MediaLibrary
from web.backend.realtime import StateBroadcaster, StatePollLoop


@pytest.fixture(autouse=True)
def fresh_cache():
    listing_cache.clear()
    videos_api._combined.update(parts=(), snap=None)
    yield
    listing_cache.clear()
    videos_api._combined.update(parts=(), snap=None)


@pytest.fixture
def lib(tmp_path) -> MediaLibrary:
    root = tmp_path / "videos"
    (root / "clips").mkdir(parents=True)
    (root / "clips" / "old.mp4").write_bytes(b"x")
    return MediaLibrary(
        id="core:videos", label="Videos", kind="core", icon="clapperboard",
        kind_icon="folder", owner=None, root_path=root, editable=True, importable=True,
        doc_editing=False, reindex_kind="videos", present=True,
    )


def _changes() -> dict[str, int]:
    """The per-library counts (``clear()`` between tests bumps ``"*"``)."""
    return {k: v for k, v in listing_cache.changes().items() if k != "*"}


def _land(folder, name: str) -> None:
    """A file lands in ``folder``, and the folder's mtime moves for sure (a
    coarse filesystem clock can leave it equal within one tick)."""
    (folder / name).write_bytes(b"new")
    st = os.stat(folder)
    os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_a_write_here_counts_as_a_change_of_that_library() -> None:
    assert _changes() == {}
    listing_cache.invalidate("core:videos")
    listing_cache.invalidate("core:videos")
    listing_cache.invalidate("core:pictures")
    assert _changes() == {"core:videos": 2, "core:pictures": 1}


def test_a_file_landing_under_a_viewed_listing_is_seen_on_the_next_sweep(lib) -> None:
    snap = videos_api._library_snapshot(lib, False)
    assert [r["name"] for r in snap.rows] == ["old.mp4"]
    assert listing_cache.sweep_in_use() == []           # nothing moved yet
    _land(lib.root_path / "clips", "from-the-phone.mp4")
    assert listing_cache.sweep_in_use() == ["core:videos"]
    assert _changes() == {"core:videos": 1}
    # The stale listing went with it: the next read walks the folder again.
    again = videos_api._library_snapshot(lib, False)
    assert sorted(r["name"] for r in again.rows) == ["from-the-phone.mp4", "old.mp4"]
    assert listing_cache.sweep_in_use() == []


def test_a_listing_nobody_is_viewing_is_not_checked(lib) -> None:
    snap = videos_api._library_snapshot(lib, False)
    snap.used_at = time.monotonic() - listing_cache.IN_USE_SEC - 1
    _land(lib.root_path / "clips", "late.mp4")
    assert listing_cache.sweep_in_use() == []
    assert _changes() == {}


def test_age_alone_is_not_a_change(lib, monkeypatch) -> None:
    """A listing past MAX_AGE_SEC is re-walked on its next read, but an idle
    page must not be told to re-read every few minutes for nothing."""
    videos_api._library_snapshot(lib, False)
    monkeypatch.setattr(listing_cache, "MAX_AGE_SEC", -1.0)
    assert listing_cache.sweep_in_use() == []
    assert _changes() == {}


def test_a_big_listing_is_checked_no_more_often_than_a_request_would(lib, monkeypatch) -> None:
    snap = videos_api._library_snapshot(lib, False)
    monkeypatch.setattr(listing_cache, "VALIDATE_ALWAYS_DIRS", 0)
    snap.validated_at = time.monotonic()
    _land(lib.root_path / "clips", "soon.mp4")
    assert listing_cache.sweep_in_use() == []           # checked moments ago
    snap.validated_at = time.monotonic() - listing_cache.VALIDATE_EVERY_SEC - 1
    assert listing_cache.sweep_in_use() == ["core:videos"]


@pytest.mark.asyncio
async def test_the_files_channel_pushes_files_changed(lib) -> None:
    assert "files" in StatePollLoop._CHANNEL_HELPERS
    broadcaster = StateBroadcaster()
    broadcaster.broadcast = AsyncMock()  # type: ignore[method-assign]
    loop = StatePollLoop(broadcaster, interval_sec=0.1)
    await loop.emit_for_channel("files")                 # the first value
    broadcaster.broadcast.reset_mock()
    await loop.emit_for_channel("files")                 # nothing changed: quiet
    assert broadcaster.broadcast.call_count == 0

    # A phone's upload through /api/files/upload ends in invalidate().
    listing_cache.invalidate("core:videos")
    await loop.emit_for_channel("files")
    broadcaster.broadcast.assert_called_once()
    args, _ = broadcaster.broadcast.call_args
    assert args[0] == "files.changed" and args[1]["data"]["core:videos"] == 1

    # A file written by another process under a listing on screen: the
    # tick's own sweep finds it.
    broadcaster.broadcast.reset_mock()
    videos_api._library_snapshot(lib, False)
    _land(lib.root_path / "clips", "plugin-wrote-this.mp4")
    await loop.emit_for_channel("files")
    broadcaster.broadcast.assert_called_once()
    args, _ = broadcaster.broadcast.call_args
    assert args[0] == "files.changed" and args[1]["data"]["core:videos"] == 2
