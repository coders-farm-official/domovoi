"""Paging and the cached walk on the filesystem lists: ``GET /api/videos/list``
and ``GET /api/files/browse`` (paging.py, listing_cache.py, media_walk.py).

What each pins: cursor pages visit every match once, in the unpaged order,
for every sort; search / kind / library filters cover the whole list and
``total``; a file appearing or vanishing between pages repeats nothing and
skips nothing else; old clients (no paging params) get every entry with
the new fields alongside; the walk is NOT repeated per page, and IS
repeated when a folder changes, a write through the server lands, the
snapshot ages out or ``refresh=true`` asks; a junction / symlink that
escapes the root never appears.

Same harness as test_videos_api / test_files_api: the registry is stubbed
to tmp-dir libraries; the gates pass via the pre-setup grace.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web.backend.api.files as files_api
import web.backend.api.videos as videos_api
from domovoi.tests.conftest import requires_db
from web.backend.api import listing_cache
from web.backend.api.files_security import MediaLibrary
from web.backend.main import app

pytestmark = requires_db


def _library(**kw) -> MediaLibrary:
    base = dict(
        id="core:videos", label="Videos", kind="core", icon="clapperboard",
        kind_icon="folder", owner=None, editable=True, importable=True,
        doc_editing=False, reindex_kind=None, present=True,
    )
    base.update(kw)
    return MediaLibrary(**base)


@pytest.fixture(autouse=True)
def fresh_cache():
    listing_cache.clear()
    videos_api._combined.update(parts=(), snap=None)
    yield
    listing_cache.clear()
    videos_api._combined.update(parts=(), snap=None)


@pytest.fixture
def roots(tmp_path):
    out = {k: (tmp_path / k) for k in ("videos", "docs", "usb", "outside")}
    for d in out.values():
        d.mkdir()
    return out


@pytest.fixture
def registry(roots, monkeypatch):
    libs = {
        "core:videos": _library(id="core:videos", label="Videos", root_path=roots["videos"]),
        "core:documents": _library(id="core:documents", label="Documents",
                                   root_path=roots["docs"], doc_editing=True),
        "removable:E": _library(id="removable:E", label="USB (E:)", kind="removable",
                                root_path=roots["usb"], editable=False, importable=False),
    }

    async def _build():
        return list(libs.values())

    monkeypatch.setattr(videos_api, "build_libraries", _build)
    monkeypatch.setattr(files_api, "build_libraries", _build)
    return libs


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _touch(p: Path, size: int = 8, mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00" * size)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _follow(c, path, params, limit, key="videos"):
    rows, answers, cursor = [], [], None
    for _ in range(500):
        q = dict(params, limit=limit)
        if cursor:
            q["cursor"] = cursor
        r = c.get(path, params=q)
        assert r.status_code == 200, r.text
        body = r.json()
        answers.append(body)
        rows.extend(body[key])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            return rows, answers
        cursor = body["next_cursor"]
    raise AssertionError("never reached the last page")


@pytest.fixture
def videos(roots):
    """133 videos over 7 folders in two libraries, distinct sizes and
    mtimes (with ties), plus non-videos that must never appear."""
    t0 = 1_760_000_000.0
    made = []
    for i in range(120):
        made.append(_touch(roots["videos"] / f"show-{i % 7}" / f"Ep {i:03d}.mp4",
                           size=100 + (i * 13) % 97, mtime=t0 + (i // 3) * 60))
    for i in range(13):
        made.append(_touch(roots["usb"] / f"trip {i:02d}.mkv", size=50 + i, mtime=t0 + i))
    _touch(roots["videos"] / "notes.txt")
    _touch(roots["videos"] / ".hidden" / "secret.mp4")
    return made


# ─── Videos ─────────────────────────────────────────────────────────────────
def test_old_clients_get_every_video_and_the_new_fields(registry, videos):
    with _client() as c:
        body = c.get("/api/videos/list").json()
    assert len(body["videos"]) == body["total"] == 133
    assert body["has_more"] is False and body["next_cursor"] is None
    assert {(lb["library_id"], lb["total"]) for lb in body["libraries"]} == {
        ("core:videos", 120), ("core:documents", 0), ("removable:E", 13)}
    assert body["videos"][0]["library_id"] == "core:videos"   # library order


@pytest.mark.parametrize("sort", ["library", "name", "newest", "oldest", "largest"])
def test_video_cursor_pages_match_the_unpaged_order(registry, videos, sort):
    with _client() as c:
        whole = c.get("/api/videos/list", params={"sort": sort}).json()["videos"]
        paged, answers = _follow(c, "/api/videos/list", {"sort": sort}, limit=20)
    assert [(v["library_id"], v["rel"]) for v in paged] == \
        [(v["library_id"], v["rel"]) for v in whole]
    assert len(answers) == 7 and all(a["total"] == 133 for a in answers)


def test_video_sorts_mean_what_they_say(registry, videos):
    with _client() as c:
        newest = c.get("/api/videos/list", params={"sort": "newest", "limit": 200}).json()["videos"]
        largest = c.get("/api/videos/list", params={"sort": "largest", "limit": 200}).json()["videos"]
    assert [v["mtime"] for v in newest] == sorted((v["mtime"] for v in newest), reverse=True)
    assert [v["size"] for v in largest] == sorted((v["size"] for v in largest), reverse=True)


def test_video_search_and_library_filter_span_every_page(registry, videos):
    with _client() as c:
        hits, answers = _follow(c, "/api/videos/list", {"q": "EP 01"}, limit=3)
        usb = c.get("/api/videos/list",
                    params={"library_id": "removable:E", "limit": 5}).json()
        folder = c.get("/api/videos/list", params={"q": "show-3/", "limit": 50}).json()
    assert sorted(v["name"] for v in hits) == [f"Ep {i:03d}.mp4" for i in range(10, 20)]
    assert answers[0]["total"] == 10
    lib_counts = {lb["library_id"]: lb["total"] for lb in answers[0]["libraries"]}
    assert lib_counts == {"core:videos": 10, "core:documents": 0, "removable:E": 0}
    assert usb["total"] == 13 and all(v["library_id"] == "removable:E" for v in usb["videos"])
    assert folder["total"] == len([i for i in range(120) if i % 7 == 3])


def test_videos_appearing_and_vanishing_between_pages(registry, videos, roots):
    with _client() as c:
        first = c.get("/api/videos/list", params={"limit": 30}).json()
        shown = [v["rel"] for v in first["videos"]]
        _touch(roots["videos"] / "show-0" / "AAA first.mp4")        # sorts before the cut
        _touch(roots["videos"] / "show-6" / "zzz last.mp4")         # sorts after it
        (roots["videos"] / shown[-1]).unlink()                      # the row the cursor names
        (roots["videos"] / "show-6" / "Ep 006.mp4").unlink()        # not yet shown
        rest, _ = _follow(c, "/api/videos/list", {"cursor": first["next_cursor"]}, limit=30)
    rest_rels = [v["rel"] for v in rest if v["library_id"] == "core:videos"]
    assert not set(rest_rels) & set(shown)
    expected = {f"show-{i % 7}/Ep {i:03d}.mp4" for i in range(120)} - set(shown) \
        - {"show-6/Ep 006.mp4"} | {"show-6/zzz last.mp4"}
    assert set(rest_rels) == expected


def test_the_walk_is_cached_between_pages(registry, videos):
    with _client() as c:
        c.get("/api/videos/list", params={"limit": 20})
        builds = listing_cache.stats["builds"]
        _follow(c, "/api/videos/list", {}, limit=20)
        c.get("/api/videos/list", params={"sort": "largest", "limit": 20})
    assert listing_cache.stats["builds"] == builds      # no library re-walked
    assert listing_cache.stats["hits"] >= 7 * 3


def test_a_new_file_in_a_walked_folder_rewalks(registry, videos, roots):
    with _client() as c:
        assert c.get("/api/videos/list", params={"limit": 1}).json()["total"] == 133
        builds = listing_cache.stats["builds"]
        time.sleep(0.05)
        _touch(roots["videos"] / "show-2" / "fresh.webm")
        body = c.get("/api/videos/list", params={"q": "fresh", "limit": 5}).json()
    assert body["total"] == 1
    assert listing_cache.stats["builds"] == builds + 1  # only the library that changed


def test_a_new_folder_rewalks(registry, videos, roots):
    with _client() as c:
        c.get("/api/videos/list", params={"limit": 1})
        _touch(roots["videos"] / "brand new" / "deep" / "x.mp4")
        body = c.get("/api/videos/list", params={"q": "brand new", "limit": 5}).json()
    assert body["total"] == 1


def test_in_place_edits_wait_for_refresh_or_age(registry, videos, roots, monkeypatch):
    """A file rewritten in place changes no folder mtime: the cached size
    stands until refresh=true, a write through the server, or MAX_AGE."""
    target = roots["videos"] / "show-1" / "Ep 001.mp4"
    with _client() as c:
        def size():
            r = c.get("/api/videos/list", params={"q": "Ep 001.mp4", "limit": 5}).json()
            return r["videos"][0]["size"]
        before = size()
        d = os.stat(target.parent).st_mtime_ns
        target.write_bytes(b"\x01" * 5000)
        os.utime(target.parent, ns=(d, d))      # make sure the folder looks untouched
        assert size() == before
        assert c.get("/api/videos/list",
                     params={"q": "Ep 001.mp4", "limit": 5, "refresh": "true"}
                     ).json()["videos"][0]["size"] == 5000
        target.write_bytes(b"\x01" * 6000)
        os.utime(target.parent, ns=(d, d))
        monkeypatch.setattr(listing_cache, "MAX_AGE_SEC", -1.0)
        assert size() == 6000


def test_an_upload_through_the_server_invalidates(registry, videos, roots, monkeypatch):
    async def _no_reindex(*a, **k):
        return False

    monkeypatch.setattr(files_api, "_trigger_reindex", _no_reindex)
    with _client() as c:
        c.get("/api/videos/list", params={"limit": 1})
        r = c.post("/api/files/upload",
                   data={"library_id": "core:videos", "path": "show-0", "device_id": "dev-pg"},
                   files=[("files", ("upload.mp4", b"\x00" * 10, "video/mp4"))])
        assert r.status_code == 200, r.text
        assert c.get("/api/videos/list", params={"q": "upload.mp4", "limit": 5}).json()["total"] == 1


def test_video_bounds(registry, videos):
    with _client() as c:
        assert c.get("/api/videos/list", params={"limit": 201}).status_code == 422
        assert c.get("/api/videos/list", params={"cursor": "%%%"}).status_code == 400
        cut = c.get("/api/videos/list", params={"limit": 3}).json()["next_cursor"]
        assert c.get("/api/videos/list",
                     params={"sort": "newest", "cursor": cut}).status_code == 400
        assert c.get("/api/videos/list", params={"sort": "bogus"}).status_code == 422


# ─── Files browse ───────────────────────────────────────────────────────────
@pytest.fixture
def big_folder(roots):
    d = roots["docs"] / "big"
    for i in range(25):
        (d / f"dir {i:02d}").mkdir(parents=True)
    for i in range(160):
        ext = (".txt", ".pdf", ".mp3", ".png")[i % 4]
        _touch(d / f"File {i:03d}{ext}", size=10 + (i * 31) % 200, mtime=1_760_000_000 + i)
    return d


def _browse(c, **params):
    r = c.get("/api/files/browse", params={"library_id": "core:documents", "path": "big", **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_browse_old_clients_get_the_whole_folder(registry, big_folder):
    with _client() as c:
        body = _browse(c)
    assert len(body["entries"]) == body["total"] == body["folder_total"] == 185
    assert body["has_more"] is False and body["next_cursor"] is None
    assert [e["is_dir"] for e in body["entries"][:25]] == [True] * 25   # folders first


@pytest.mark.parametrize("sort", ["name", "newest", "oldest", "largest"])
def test_browse_cursor_pages_match_the_unpaged_order(registry, big_folder, sort):
    with _client() as c:
        whole = _browse(c, sort=sort)["entries"]
        paged, answers = _follow(c, "/api/files/browse",
                                 {"library_id": "core:documents", "path": "big", "sort": sort},
                                 limit=40, key="entries")
    assert [e["rel"] for e in paged] == [e["rel"] for e in whole]
    assert len(answers) == 5
    assert all(a["breadcrumb"] == ["big"] and a["editable"] for a in answers)


def test_browse_filters_span_the_folder(registry, big_folder):
    with _client() as c:
        folders = _browse(c, kind="folder", limit=10)
        pdfs, answers = _follow(c, "/api/files/browse", {
            "library_id": "core:documents", "path": "big", "kind": "pdf"}, limit=7, key="entries")
        named = _browse(c, q="file 01", limit=50)
    assert folders["total"] == 25 and folders["folder_total"] == 185
    assert all(e["is_dir"] for e in folders["entries"])
    assert len(pdfs) == answers[0]["total"] == 40 and all(e["kind"] == "pdf" for e in pdfs)
    assert named["total"] == 10


def test_browse_an_edited_document_shows_at_once(registry, big_folder, roots, monkeypatch):
    """A document saved in place moves no folder mtime; the documents door
    drops the cached listing so the Files page shows the new size."""
    from domovoi.config import settings

    monkeypatch.setattr(settings, "documents_dir", str(roots["docs"]))
    (big_folder / "File 000.txt").write_text("hello", encoding="utf-8")
    with _client() as c:
        before = {e["name"]: e["size"] for e in _browse(c, q="File 000.txt", limit=5)["entries"]}
        r = c.put("/api/documents/text/big/File 000.txt", json={"text": "x" * 777})
        if r.status_code == 404:
            pytest.skip("documents text route not reachable for this layout")
        assert r.status_code == 200, r.text
        after = {e["name"]: e["size"] for e in _browse(c, q="File 000.txt", limit=5)["entries"]}
    assert after["File 000.txt"] == 777 != before["File 000.txt"]


def test_browse_is_cached_and_rereads_on_change(registry, big_folder):
    with _client() as c:
        _browse(c, limit=10)
        builds = listing_cache.stats["builds"]
        _follow(c, "/api/files/browse", {"library_id": "core:documents", "path": "big"},
                limit=40, key="entries")
        assert listing_cache.stats["builds"] == builds
        _touch(big_folder / "late arrival.txt")
        assert _browse(c, q="late arrival", limit=5)["total"] == 1
        assert listing_cache.stats["builds"] == builds + 1


def test_browse_entries_vanishing_between_pages(registry, big_folder):
    with _client() as c:
        first = _browse(c, limit=60)
        shown = [e["name"] for e in first["entries"]]
        (big_folder / shown[-1]).unlink()
        (big_folder / "File 150.mp3").unlink()
        _touch(big_folder / "AAA early.txt")
        rest, _ = _follow(c, "/api/files/browse",
                          {"library_id": "core:documents", "path": "big",
                           "cursor": first["next_cursor"]}, limit=60, key="entries")
    names = [e["name"] for e in rest]
    assert not set(names) & set(shown)
    every = {f"File {i:03d}{('.txt', '.pdf', '.mp3', '.png')[i % 4]}" for i in range(160)}
    assert set(names) == every - set(shown) - {"File 150.mp3"}


# ─── Links that escape the root ─────────────────────────────────────────────
def _escape_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_junction_out_of_the_root_never_lists(registry, roots, videos):
    _touch(roots["outside"] / "stolen.mp4")
    _touch(roots["outside"] / "private.txt")
    try:
        _escape_link(roots["videos"] / "escape", roots["outside"])
        _escape_link(roots["docs"] / "escape", roots["outside"])
    except OSError:
        pytest.skip("link creation not permitted on this host")
    with _client() as c:
        vids = c.get("/api/videos/list", params={"q": "stolen", "limit": 5}).json()
        top = c.get("/api/files/browse", params={"library_id": "core:documents"}).json()
    assert vids["total"] == 0
    assert "escape" not in [e["name"] for e in top["entries"]]


def test_a_junction_inside_the_root_is_walked_once(registry, roots, videos):
    try:
        _escape_link(roots["videos"] / "loop", roots["videos"] / "show-0")
    except OSError:
        pytest.skip("link creation not permitted on this host")
    with _client() as c:
        body = c.get("/api/videos/list", params={"limit": 200}).json()
    rels = [v["rel"] for v in body["videos"] if v["library_id"] == "core:videos"]
    # show-0's 18 episodes appear once — under whichever path the walk met first.
    assert len(rels) == 120
