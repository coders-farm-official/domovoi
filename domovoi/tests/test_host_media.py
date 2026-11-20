"""POST /v1/host/media-changed (domovoi/host_media.py): the host's media
watcher (FSEvents on a Mac host, mac-installer PLAN 4.6 / R9) says which
paths in the shared media folders changed, because virtiofs raises no
inotify event in the VM for them.

What is pinned here: only a loopback peer with no forwarding header is
answered (a LAN phone that pf's redirect brings in keeps its own address);
only paths under a library folder are taken (``..``, relative paths and links
out are refused); music paths index the files that exist and make every
room's player read their folders; a library folder itself is a whole sweep,
at most once per interval; pictures, videos and documents are read live.
The indexer and the rooms are replaced by recorders: the database work they
do is the upload path's own, tested in test_library_indexer and
test_mpd_library_sync.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domovoi import host_media
from domovoi import mpd_library_sync
from domovoi.config import settings
from domovoi.main import app as core_app
# Imported before any patching: each binds update_rooms_for at import.
from domovoi.workers import audiobook_indexer, library_indexer

LOOPBACK = ("127.0.0.1", 50123)
LAN = ("192.168.1.40", 50123)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    dirs = {}
    for name in host_media.LIBRARY_SETTINGS:
        d = tmp_path / name.removesuffix("_dir")
        d.mkdir()
        monkeypatch.setattr(settings, name, str(d))
        dirs[name.removesuffix("_dir")] = d.resolve()
    return dirs


@pytest.fixture
def calls(monkeypatch):
    seen: dict[str, list] = {"index_paths": [], "index_music_dir": 0, "rooms": [], "audiobooks": []}

    async def index_paths(paths):
        seen["index_paths"].append(sorted(str(p) for p in paths))
        return {"scanned": len(seen["index_paths"][-1]), "inserted": len(seen["index_paths"][-1])}

    async def index_music_dir():
        seen["index_music_dir"] += 1
        return {"scanned": 0, "inserted": 0}

    async def update_rooms_for(paths=(), *, tops=(), reason="indexer"):
        seen["rooms"].append((sorted(str(p) for p in paths), list(tops)))
        return 2

    async def index_audiobooks_dir(*, removed=()):
        seen["audiobooks"].append(sorted(removed))
        return {"inserted": 1}

    monkeypatch.setattr(library_indexer, "index_paths", index_paths)
    monkeypatch.setattr(library_indexer, "index_music_dir", index_music_dir)
    monkeypatch.setattr(mpd_library_sync, "update_rooms_for", update_rooms_for)
    monkeypatch.setattr(audiobook_indexer, "index_audiobooks_dir", index_audiobooks_dir)
    monkeypatch.setattr(host_media, "_runner", host_media._Runner())
    return seen


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"ID3")
    return p


# ─── which paths are taken ──────────────────────────────────────────────


def test_only_library_paths_are_taken(lib, tmp_path) -> None:
    song = _touch(lib["music"] / "Artist" / "Album" / "01.mp3")
    outside = _touch(tmp_path / "elsewhere" / "x.mp3")
    link = lib["music"] / "out"
    try:
        link.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
        link_path = str(link / "x.mp3")
    except (OSError, NotImplementedError):
        link_path = str(outside)  # no symlinks here (Windows without the right): the plain outside path
    groups, refused = host_media.classify([
        str(song), str(lib["music"]), str(outside), "relative/x.mp3",
        str(lib["music"]) + "/../elsewhere/x.mp3", link_path, "/" + "a" * 5000, str(lib["videos"] / "film.mkv"),
    ])
    assert groups["music_dir"] == {song.resolve(), lib["music"]}
    assert groups["videos_dir"] == {(lib["videos"] / "film.mkv").resolve()}
    assert refused == 5


def test_the_most_specific_library_wins(lib, monkeypatch) -> None:
    inner = lib["music"] / "audiobooks"
    inner.mkdir()
    monkeypatch.setattr(settings, "audiobooks_dir", str(inner))
    book = _touch(inner / "Book" / "ch1.mp3")
    groups, _ = host_media.classify([str(book)])
    assert set(groups) == {"audiobooks_dir"}


# ─── what is done ───────────────────────────────────────────────────────


def test_music_files_are_indexed_and_the_rooms_read_their_folders(lib, calls) -> None:
    song = _touch(lib["music"] / "A" / "01.mp3")
    gone = lib["music"] / "B" / "02.mp3"  # removed on the host: not indexed, its folder is read
    out = asyncio.run(host_media.apply({"music_dir": {song.resolve(), gone.resolve(strict=False)}}))
    assert calls["index_paths"] == [[str(song.resolve())]]
    assert calls["rooms"] == [(sorted([str(song.resolve()), str(gone.resolve(strict=False))]), [])]
    assert calls["index_music_dir"] == 0
    assert out["music"] == {"whole": False, "indexed": 1, "rooms": 2}


def test_the_library_folder_itself_is_a_whole_sweep_at_most_once_per_interval(lib, calls, monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(host_media, "_clock", lambda: now[0])
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)
        now[0] += s

    monkeypatch.setattr(host_media, "_sleep", fake_sleep)
    runner = host_media._Runner()
    asyncio.run(host_media.apply({"music_dir": {lib["music"]}}, runner))
    assert calls["index_music_dir"] == 1
    assert calls["rooms"] == [([], [""])], "every room reads the whole library"
    now[0] += 10
    asyncio.run(host_media.apply({"music_dir": {lib["music"]}}, runner))
    assert calls["index_music_dir"] == 2
    assert slept == [pytest.approx(host_media.FULL_SWEEP_MIN_INTERVAL_SEC - 10)], "the second sweep waited"


def test_audiobooks_prune_what_went_and_media_read_live_needs_nothing(lib, calls) -> None:
    book = _touch(lib["audiobooks"] / "Book.m4b")
    gone = (lib["audiobooks"] / "Old.m4b").resolve(strict=False)
    out = asyncio.run(host_media.apply({
        "audiobooks_dir": {book.resolve(), gone},
        "pictures_dir": {(lib["pictures"] / "a.jpg").resolve(strict=False)},
    }))
    assert calls["audiobooks"] == [[str(gone)]]
    assert out["pictures"] == {"whole": False, "read_live": True}
    assert calls["index_paths"] == []


def test_the_audiobook_indexer_off_means_only_the_rooms_read(lib, calls, monkeypatch) -> None:
    monkeypatch.setattr(settings, "audiobook_indexer_enabled", False)
    book = _touch(lib["audiobooks"] / "Book.m4b")
    asyncio.run(host_media.apply({"audiobooks_dir": {book.resolve()}}))
    assert calls["audiobooks"] == []
    assert len(calls["rooms"]) == 1


def test_requests_merge_into_one_run_at_a_time(lib, calls) -> None:
    a = _touch(lib["music"] / "a.mp3")
    b = _touch(lib["music"] / "b.mp3")

    async def go():
        host_media.accept([str(a)])
        host_media.accept([str(b)])
        await host_media._runner.task

    asyncio.run(go())
    assert calls["index_paths"] == [sorted([str(a.resolve()), str(b.resolve())])], "one run, both paths"


def test_too_many_paths_is_refused(lib) -> None:
    with pytest.raises(ValueError):
        host_media.accept(["/x"] * (host_media.MAX_PATHS + 1))


# ─── the route ──────────────────────────────────────────────────────────


def _core(peer=LOOPBACK) -> TestClient:
    return TestClient(core_app, base_url="http://127.0.0.1:6370", client=peer,
                      headers={"X-Requested-With": "DomovoiHelper"})


def test_the_route_answers_this_machine_only(lib, calls) -> None:
    song = _touch(lib["music"] / "01.mp3")
    r = _core(LAN).post("/v1/host/media-changed", json={"paths": [str(song)]})
    assert r.status_code == 403 and r.json()["reason"] == "not_local"
    for h in ("X-Forwarded-For", "Forwarded", "X-Real-IP", "X-Forwarded-Host"):
        r = _core().post("/v1/host/media-changed", json={"paths": [str(song)]}, headers={h: "127.0.0.1"})
        assert r.status_code == 403, h
    r = _core().post("/v1/host/media-changed", json={"paths": [str(song), "/etc/passwd"]})
    assert r.status_code == 202
    assert r.json() == {"queued": 1, "refused": 1, "libraries": ["music"]}
    assert r.headers["cache-control"] == "no-store"


def test_the_route_refuses_a_batch_too_big(lib) -> None:
    r = _core().post("/v1/host/media-changed", json={"paths": ["/x"] * (host_media.MAX_PATHS + 1)})
    assert r.status_code == 422
