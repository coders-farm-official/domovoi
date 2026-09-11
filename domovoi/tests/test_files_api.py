"""Endpoint tests for the Files API (design §2) — web/backend/api/files.py.

Lives under ``domovoi/tests`` for the test-DB conftest safety net (same as
test_music_upload_api / test_documents). The registry is stubbed to a
controlled set of libraries rooted in tmp dirs so each endpoint's behavior —
editable/importable rejection, ejected-drive 410, reindex fanout, upload
dedupe, recursive-delete confinement, import containment — is deterministic.
Admin gating passes via the pre-setup grace (fresh test DB has no admin
credential).
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web.backend.api.files as files_api
from domovoi.tests.conftest import requires_db
from web.backend.api.files_security import MediaLibrary
from web.backend.main import app


@pytest.fixture
def roots(tmp_path):
    """Create the on-disk roots each stub library points at."""
    music = tmp_path / "music"
    docs = tmp_path / "docs"
    ro = tmp_path / "readonly"
    usb = tmp_path / "usb"
    for d in (music, docs, ro, usb):
        d.mkdir()
    return {"music": music, "docs": docs, "ro": ro, "usb": usb}


def _library(**kw) -> MediaLibrary:
    base = dict(
        id="core:music", label="Music", kind="core", icon="music",
        kind_icon="folder", owner=None, editable=True, importable=True,
        doc_editing=False, reindex_kind="music", present=True,
    )
    base.update(kw)
    return MediaLibrary(**base)


@pytest.fixture
def registry(roots, monkeypatch):
    """Stub ``build_libraries`` with a controlled registry. Returns a mutable
    dict the test can edit (e.g. drop the removable to simulate an ejection)."""
    libs = {
        "core:music": _library(
            id="core:music", label="Music", root_path=roots["music"],
            editable=True, importable=True, reindex_kind="music",
        ),
        "core:documents": _library(
            id="core:documents", label="Documents", icon="file-text",
            root_path=roots["docs"], editable=True, importable=True,
            doc_editing=True, reindex_kind="documents",
        ),
        "plugin:jelly:videos": _library(
            id="plugin:jelly:videos", label="Jellyfin · Videos", kind="plugin",
            icon="clapperboard", kind_icon="puzzle", owner="jelly",
            root_path=roots["ro"], editable=False, importable=False,
            reindex_kind=None,
        ),
        "removable:E": _library(
            id="removable:E", label="USB (E:)", kind="removable",
            icon="hard-drive", kind_icon="hard-drive", owner="/dev/sdb1",
            root_path=roots["usb"], editable=False, importable=False,
            reindex_kind=None,
        ),
    }

    async def _build():
        return list(libs.values())

    monkeypatch.setattr(files_api, "build_libraries", _build)
    return libs


@pytest.fixture
def reindex_spy(monkeypatch):
    calls = []

    async def _post(path, body=None, headers=None):
        calls.append(path)
        return 200, {"queued": True}

    monkeypatch.setattr(files_api, "post_admin", _post)
    return calls


def _client():
    return TestClient(app)


# ─── GET /libraries ─────────────────────────────────────────────────────────
@requires_db
def test_libraries_strips_root_path(registry):
    with _client() as c:
        r = c.get("/api/files/libraries")
    assert r.status_code == 200
    libs = r.json()["libraries"]
    ids = [lib["id"] for lib in libs]
    assert ids == ["core:music", "core:documents", "plugin:jelly:videos", "removable:E"]
    for lib in libs:
        assert "root_path" not in lib
    docs = next(lib for lib in libs if lib["id"] == "core:documents")
    assert docs["doc_editing"] is True
    ro = next(lib for lib in libs if lib["id"] == "plugin:jelly:videos")
    assert ro["editable"] is False and ro["importable"] is False


# ─── GET /browse ────────────────────────────────────────────────────────────
@requires_db
def test_browse_lists_dirs_first_and_classifies(registry, roots):
    (roots["music"] / "Beatles").mkdir()
    (roots["music"] / "song.flac").write_bytes(b"aa")
    (roots["music"] / "notes.txt").write_text("hi", encoding="utf-8")
    with _client() as c:
        r = c.get("/api/files/browse", params={"library_id": "core:music", "path": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == ""
    names = [e["name"] for e in body["entries"]]
    assert names[0] == "Beatles"  # dirs first
    kinds = {e["name"]: e["kind"] for e in body["entries"]}
    assert kinds["Beatles"] == "folder"
    assert kinds["song.flac"] == "audio"
    assert kinds["notes.txt"] == "doc-text"


@requires_db
def test_browse_excludes_secret_shaped_names(registry, roots):
    (roots["music"] / ".env").write_text("SECRET=1", encoding="utf-8")
    (roots["music"] / "id_rsa.pem").write_text("key", encoding="utf-8")
    (roots["music"] / "ok.mp3").write_bytes(b"x")
    with _client() as c:
        r = c.get("/api/files/browse", params={"library_id": "core:music"})
    names = [e["name"] for e in r.json()["entries"]]
    assert ".env" not in names
    assert "id_rsa.pem" not in names
    assert "ok.mp3" in names


@requires_db
def test_browse_documents_rows_never_locked(registry, roots):
    """The engine-lock concept is retired with the office sidecars — the
    homegrown editors are last-write-wins, so locked_by is always None."""
    (roots["docs"] / "report.docx").write_bytes(b"d")
    with _client() as c:
        r = c.get("/api/files/browse", params={"library_id": "core:documents"})
    entry = next(e for e in r.json()["entries"] if e["name"] == "report.docx")
    assert entry["locked_by"] is None


@requires_db
def test_browse_traversal_rejected(registry):
    with _client() as c:
        r = c.get("/api/files/browse", params={"library_id": "core:music", "path": "../.."})
    assert r.status_code == 400


@requires_db
def test_browse_unknown_library_404(registry):
    with _client() as c:
        r = c.get("/api/files/browse", params={"library_id": "core:bogus"})
    assert r.status_code == 404


@requires_db
def test_browse_ejected_removable_410(registry):
    # Drop the removable from the registry to simulate an ejection mid-session.
    registry.pop("removable:E")
    with _client() as c:
        r = c.get("/api/files/browse", params={"library_id": "removable:E"})
    assert r.status_code == 410


@requires_db
def test_browse_per_entry_symlink_escape_dropped(registry, roots, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.txt").write_text("secret", encoding="utf-8")
    try:
        os.symlink(outside, roots["music"] / "escape")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    (roots["music"] / "real.mp3").write_bytes(b"x")
    with _client() as c:
        r = c.get("/api/files/browse", params={"library_id": "core:music"})
    names = [e["name"] for e in r.json()["entries"]]
    assert "escape" not in names  # symlink resolves outside → dropped
    assert "real.mp3" in names


# ─── GET /download ──────────────────────────────────────────────────────────
@requires_db
def test_download_file_attachment(registry, roots):
    (roots["music"] / "a.txt").write_text("hello", encoding="utf-8")
    with _client() as c:
        r = c.get("/api/files/download", params={"library_id": "core:music", "path": "a.txt"})
    assert r.status_code == 200
    assert r.content == b"hello"
    assert "attachment" in r.headers["content-disposition"]


@requires_db
def test_download_directory_zip(registry, roots):
    (roots["music"] / "album").mkdir()
    (roots["music"] / "album" / "1.mp3").write_bytes(b"one")
    (roots["music"] / "album" / "2.mp3").write_bytes(b"two")
    with _client() as c:
        r = c.get("/api/files/download", params={"library_id": "core:music", "path": "album"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(zf.namelist()) == {"1.mp3", "2.mp3"}


@requires_db
def test_download_missing_404(registry):
    with _client() as c:
        r = c.get("/api/files/download", params={"library_id": "core:music", "path": "nope.mp3"})
    assert r.status_code == 404


# ─── POST /upload ───────────────────────────────────────────────────────────
@requires_db
def test_upload_saves_dedupes_and_reindexes(registry, roots, reindex_spy):
    with _client() as c:
        r1 = c.post(
            "/api/files/upload",
            data={"library_id": "core:music", "path": ""},
            files=[("files", ("t.mp3", b"one", "audio/mpeg"))],
        )
        r2 = c.post(
            "/api/files/upload",
            data={"library_id": "core:music", "path": ""},
            files=[("files", ("t.mp3", b"two", "audio/mpeg"))],
        )
    assert r1.status_code == 200
    assert r1.json()["saved"] == ["t.mp3"]
    assert r1.json()["reindex_triggered"] is True
    assert r2.json()["saved"] == ["t (1).mp3"]
    assert (roots["music"] / "t.mp3").read_bytes() == b"one"
    assert (roots["music"] / "t (1).mp3").read_bytes() == b"two"
    assert reindex_spy == ["/v1/admin/library/reindex", "/v1/admin/library/reindex"]


@requires_db
def test_upload_rejected_on_non_editable(registry, reindex_spy):
    with _client() as c:
        r = c.post(
            "/api/files/upload",
            data={"library_id": "removable:E", "path": ""},
            files=[("files", ("x.mp3", b"x", "audio/mpeg"))],
        )
    assert r.status_code == 403
    assert reindex_spy == []


@requires_db
def test_upload_no_reindex_for_documents(registry, roots, reindex_spy):
    # documents reindex_kind is not in the indexed set → no core hop.
    with _client() as c:
        r = c.post(
            "/api/files/upload",
            data={"library_id": "core:documents", "path": ""},
            files=[("files", ("n.txt", b"hi", "text/plain"))],
        )
    assert r.status_code == 200
    assert r.json()["reindex_triggered"] is False
    assert reindex_spy == []


# ─── POST /delete ───────────────────────────────────────────────────────────
@requires_db
def test_delete_file(registry, roots, reindex_spy):
    (roots["music"] / "gone.mp3").write_bytes(b"x")
    with _client() as c:
        r = c.post("/api/files/delete", json={"library_id": "core:music", "paths": ["gone.mp3"]})
    assert r.status_code == 200
    assert r.json()["deleted"] == ["gone.mp3"]
    assert not (roots["music"] / "gone.mp3").exists()
    assert reindex_spy == ["/v1/admin/library/reindex"]


@requires_db
def test_delete_directory_needs_recursive(registry, roots):
    (roots["music"] / "d").mkdir()
    (roots["music"] / "d" / "f.mp3").write_bytes(b"x")
    with _client() as c:
        r = c.post("/api/files/delete", json={"library_id": "core:music", "paths": ["d"]})
    body = r.json()
    assert body["deleted"] == []
    assert any("recursive" in f for f in body["failed"])
    assert (roots["music"] / "d").exists()


@requires_db
def test_delete_recursive_confined(registry, roots):
    d = roots["music"] / "tree"
    (d / "sub").mkdir(parents=True)
    (d / "a.mp3").write_bytes(b"a")
    (d / "sub" / "b.mp3").write_bytes(b"b")
    with _client() as c:
        r = c.post(
            "/api/files/delete",
            json={"library_id": "core:music", "paths": ["tree"], "recursive": True},
        )
    assert r.status_code == 200
    assert r.json()["deleted"] == ["tree"]
    assert not d.exists()


@requires_db
def test_delete_refuses_library_root(registry):
    with _client() as c:
        r = c.post("/api/files/delete", json={"library_id": "core:music", "paths": [""]})
    body = r.json()
    assert body["deleted"] == []
    assert body["failed"]


@requires_db
def test_delete_recursive_does_not_follow_symlink_out(registry, roots, tmp_path):
    outside = tmp_path / "keepme"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep", encoding="utf-8")
    d = roots["music"] / "tree"
    d.mkdir()
    (d / "a.mp3").write_bytes(b"a")
    try:
        os.symlink(outside, d / "link")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    with _client() as c:
        r = c.post(
            "/api/files/delete",
            json={"library_id": "core:music", "paths": ["tree"], "recursive": True},
        )
    assert r.status_code == 200
    # The tree is gone but the symlink target's contents survive untouched.
    assert not d.exists()
    assert (outside / "precious.txt").read_text(encoding="utf-8") == "keep"


@requires_db
def test_delete_documents_file(registry, roots):
    (roots["docs"] / "gone.docx").write_bytes(b"d")
    with _client() as c:
        r = c.post("/api/files/delete", json={"library_id": "core:documents", "paths": ["gone.docx"]})
    assert r.status_code == 200
    assert r.json()["deleted"] == ["gone.docx"]
    assert not (roots["docs"] / "gone.docx").exists()


# ─── POST /import ───────────────────────────────────────────────────────────
@requires_db
def test_import_file_from_removable(registry, roots, reindex_spy):
    (roots["usb"] / "track.flac").write_bytes(b"audio")
    with _client() as c:
        r = c.post(
            "/api/files/import",
            json={
                "source_library_id": "removable:E",
                "source_path": "track.flac",
                "target_library_id": "core:music",
                "target_path": "",
            },
        )
    assert r.status_code == 200
    assert r.json()["copied"] == ["track.flac"]
    assert r.json()["reindex_triggered"] is True
    assert (roots["music"] / "track.flac").read_bytes() == b"audio"
    # Source is left in place (import only reads the removable).
    assert (roots["usb"] / "track.flac").exists()


@requires_db
def test_import_directory_confined(registry, roots):
    src = roots["usb"] / "album"
    (src / "inner").mkdir(parents=True)
    (src / "1.mp3").write_bytes(b"a")
    (src / "inner" / "2.mp3").write_bytes(b"b")
    with _client() as c:
        r = c.post(
            "/api/files/import",
            json={
                "source_library_id": "removable:E",
                "source_path": "album",
                "target_library_id": "core:music",
                "target_path": "",
            },
        )
    assert r.status_code == 200
    assert (roots["music"] / "album" / "1.mp3").read_bytes() == b"a"
    assert (roots["music"] / "album" / "inner" / "2.mp3").read_bytes() == b"b"


@requires_db
def test_import_rejects_non_importable_target(registry, roots):
    (roots["usb"] / "x.mp3").write_bytes(b"x")
    with _client() as c:
        r = c.post(
            "/api/files/import",
            json={
                "source_library_id": "removable:E",
                "source_path": "x.mp3",
                "target_library_id": "plugin:jelly:videos",  # importable=False
                "target_path": "",
            },
        )
    assert r.status_code == 409


@requires_db
def test_import_rejects_non_removable_source(registry, roots):
    (roots["music"] / "x.mp3").write_bytes(b"x")
    with _client() as c:
        r = c.post(
            "/api/files/import",
            json={
                "source_library_id": "core:music",  # not removable
                "source_path": "x.mp3",
                "target_library_id": "core:documents",
                "target_path": "",
            },
        )
    assert r.status_code == 409


@requires_db
def test_import_ejected_source_410(registry, roots):
    registry.pop("removable:E")
    with _client() as c:
        r = c.post(
            "/api/files/import",
            json={
                "source_library_id": "removable:E",
                "source_path": "x.mp3",
                "target_library_id": "core:music",
                "target_path": "",
            },
        )
    assert r.status_code == 410


# ═══ POST /move ════════════════════════════════════════════════════════════
#
# The containment rules are the whole risk surface here, and they need no
# database — so they're exercised by calling the route function DIRECTLY,
# outside the admin dependency, and therefore can't skip on a box without
# Postgres. The HTTP-surface tests at the end carry @requires_db like the
# rest of this file.


@pytest.fixture
def no_reindex(monkeypatch):
    """Record reindex fanout without a core hop, so the move tests need
    neither a request object nor a live Domovoi server."""
    calls = []

    async def _trigger(kind, request):
        calls.append(kind)
        return True

    monkeypatch.setattr(files_api, "_trigger_reindex", _trigger)
    return calls


def _move(**kw):
    """Call the move endpoint directly (no admin dependency, no DB)."""
    import asyncio

    req = files_api.MoveRequest(**kw)
    return asyncio.run(files_api.move(None, req))


def test_move_relocates_a_file_within_a_library(registry, roots, no_reindex):
    (roots["music"] / "Beatles").mkdir()
    (roots["music"] / "song.flac").write_bytes(b"aa")

    res = _move(source_library_id="core:music", paths=["song.flac"],
                target_library_id="core:music", target_path="Beatles")
    assert res.moved == ["Beatles/song.flac"]
    assert res.failed == [] and res.skipped == []
    assert not (roots["music"] / "song.flac").exists()
    assert (roots["music"] / "Beatles" / "song.flac").read_bytes() == b"aa"
    # An indexed library on either side gets a rescan.
    assert no_reindex == ["music"]


def test_move_relocates_a_folder_with_its_contents(registry, roots, no_reindex):
    src = roots["music"] / "Album"
    (src / "disc1").mkdir(parents=True)
    (src / "disc1" / "a.flac").write_bytes(b"a")
    (roots["music"] / "Archive").mkdir()

    res = _move(source_library_id="core:music", paths=["Album"],
                target_library_id="core:music", target_path="Archive")
    assert res.moved == ["Archive/Album"]
    assert (roots["music"] / "Archive" / "Album" / "disc1" / "a.flac").read_bytes() == b"a"
    assert not src.exists()


def test_move_across_libraries_when_both_are_editable(registry, roots, no_reindex):
    (roots["docs"] / "liner-notes.txt").write_text("hi", encoding="utf-8")

    res = _move(source_library_id="core:documents", paths=["liner-notes.txt"],
                target_library_id="core:music", target_path="")
    assert res.moved == ["liner-notes.txt"]
    assert (roots["music"] / "liner-notes.txt").exists()
    assert not (roots["docs"] / "liner-notes.txt").exists()
    # Only INDEXED_KINDS get a rescan, and documents isn't one of them (it's
    # read live off disk) — so the music side is told and the docs side isn't.
    assert no_reindex == ["music"]


def test_move_up_a_level_via_an_empty_target_path(registry, roots, no_reindex):
    (roots["music"] / "Beatles").mkdir()
    (roots["music"] / "Beatles" / "song.flac").write_bytes(b"aa")

    res = _move(source_library_id="core:music", paths=["Beatles/song.flac"],
                target_library_id="core:music", target_path="")
    assert res.moved == ["song.flac"]
    assert (roots["music"] / "song.flac").exists()


def test_move_refuses_to_put_a_folder_inside_itself(registry, roots, no_reindex):
    """shutil.move would happily start recursing into the copy it's creating.
    Both the folder itself and any descendant of it must be refused."""
    (roots["music"] / "Album" / "disc1").mkdir(parents=True)

    into_self = _move(source_library_id="core:music", paths=["Album"],
                      target_library_id="core:music", target_path="Album")
    assert into_self.moved == []
    assert "itself" in into_self.failed[0]

    into_child = _move(source_library_id="core:music", paths=["Album"],
                       target_library_id="core:music", target_path="Album/disc1")
    assert into_child.moved == []
    assert "itself" in into_child.failed[0]
    # Nothing was touched.
    assert (roots["music"] / "Album" / "disc1").is_dir()


def test_move_refuses_to_overwrite_an_existing_name(registry, roots, no_reindex):
    """The one unrecoverable operation on this page would be a silent
    overwrite, so a collision fails loudly and leaves both files alone."""
    (roots["music"] / "Beatles").mkdir()
    (roots["music"] / "song.flac").write_bytes(b"new")
    (roots["music"] / "Beatles" / "song.flac").write_bytes(b"old")

    res = _move(source_library_id="core:music", paths=["song.flac"],
                target_library_id="core:music", target_path="Beatles")
    assert res.moved == []
    assert "already exists" in res.failed[0]
    assert (roots["music"] / "song.flac").read_bytes() == b"new"
    assert (roots["music"] / "Beatles" / "song.flac").read_bytes() == b"old"


def test_dropping_into_the_folder_it_is_already_in_is_a_no_op(registry, roots, no_reindex):
    """Reported as `skipped`, not `failed` — an idle drag must not read as an
    error in the UI."""
    (roots["music"] / "song.flac").write_bytes(b"aa")

    res = _move(source_library_id="core:music", paths=["song.flac"],
                target_library_id="core:music", target_path="")
    assert res.moved == [] and res.failed == []
    assert "already in that folder" in res.skipped[0]
    assert (roots["music"] / "song.flac").exists()
    # No move ⇒ no rescan.
    assert no_reindex == []


def test_move_rejects_traversal_and_absolute_paths(registry, roots, no_reindex):
    (roots["music"] / "Beatles").mkdir()
    for bad in ("../docs/escape.txt", "/etc/passwd", "C:/Windows/win.ini"):
        res = _move(source_library_id="core:music", paths=[bad],
                    target_library_id="core:music", target_path="Beatles")
        assert res.moved == [], bad
        assert res.failed, bad


def test_move_rejects_a_traversing_target_path(registry, roots, no_reindex):
    import fastapi

    (roots["music"] / "song.flac").write_bytes(b"aa")
    with pytest.raises(fastapi.HTTPException):
        _move(source_library_id="core:music", paths=["song.flac"],
              target_library_id="core:music", target_path="../docs")
    assert (roots["music"] / "song.flac").exists()


def test_move_refuses_the_library_root(registry, roots, no_reindex):
    (roots["music"] / "Beatles").mkdir()
    res = _move(source_library_id="core:music", paths=[""],
                target_library_id="core:music", target_path="Beatles")
    assert res.moved == [] and res.failed


def test_move_out_of_a_read_only_library_is_refused(registry, roots, no_reindex):
    """A move DELETES from the source, so a read-only root can only ever be a
    destination — which is why removables stay copy-only through /import."""
    import fastapi

    (roots["ro"] / "clip.mkv").write_bytes(b"aa")
    with pytest.raises(fastapi.HTTPException) as e:
        _move(source_library_id="plugin:jelly:videos", paths=["clip.mkv"],
              target_library_id="core:music", target_path="")
    assert e.value.status_code == 403
    assert (roots["ro"] / "clip.mkv").exists()

    (roots["usb"] / "track.mp3").write_bytes(b"aa")
    with pytest.raises(fastapi.HTTPException) as e2:
        _move(source_library_id="removable:E", paths=["track.mp3"],
              target_library_id="core:music", target_path="")
    assert e2.value.status_code == 403
    assert (roots["usb"] / "track.mp3").exists()


def test_move_into_a_read_only_library_is_refused(registry, roots, no_reindex):
    import fastapi

    (roots["music"] / "song.flac").write_bytes(b"aa")
    with pytest.raises(fastapi.HTTPException) as e:
        _move(source_library_id="core:music", paths=["song.flac"],
              target_library_id="plugin:jelly:videos", target_path="")
    assert e.value.status_code == 403
    assert (roots["music"] / "song.flac").exists()


def test_move_404s_for_a_missing_target_directory(registry, roots, no_reindex):
    import fastapi

    (roots["music"] / "song.flac").write_bytes(b"aa")
    with pytest.raises(fastapi.HTTPException) as e:
        _move(source_library_id="core:music", paths=["song.flac"],
              target_library_id="core:music", target_path="NoSuchFolder")
    assert e.value.status_code == 404


def test_move_404s_when_the_target_is_a_file_not_a_folder(registry, roots, no_reindex):
    import fastapi

    (roots["music"] / "song.flac").write_bytes(b"aa")
    (roots["music"] / "cover.jpg").write_bytes(b"aa")
    with pytest.raises(fastapi.HTTPException) as e:
        _move(source_library_id="core:music", paths=["song.flac"],
              target_library_id="core:music", target_path="cover.jpg")
    assert e.value.status_code == 404


def test_move_refuses_a_secret_shaped_name(registry, roots, no_reindex):
    """Same guard upload and import use — a dotfile-shaped secret must not be
    relocatable into a library the dashboard serves."""
    (roots["music"] / "Beatles").mkdir()
    (roots["music"] / ".env").write_text("SECRET=1", encoding="utf-8")

    res = _move(source_library_id="core:music", paths=[".env"],
                target_library_id="core:music", target_path="Beatles")
    assert res.moved == []
    assert "bad name" in res.failed[0]
    assert (roots["music"] / ".env").exists()


def test_move_is_per_path_not_all_or_nothing(registry, roots, no_reindex):
    """One collision must not abandon the rest of the drag."""
    (roots["music"] / "Beatles").mkdir()
    (roots["music"] / "a.flac").write_bytes(b"a")
    (roots["music"] / "b.flac").write_bytes(b"b")
    (roots["music"] / "Beatles" / "b.flac").write_bytes(b"old")

    res = _move(source_library_id="core:music",
                paths=["a.flac", "b.flac", "gone.flac"],
                target_library_id="core:music", target_path="Beatles")
    assert res.moved == ["Beatles/a.flac"]
    assert len(res.failed) == 2
    assert any("already exists" in f for f in res.failed)
    assert any("not found" in f for f in res.failed)


def test_move_does_not_follow_a_symlink_out_of_the_library(registry, roots, no_reindex):
    """safe_join resolves before checking containment, so a symlinked path
    pointing outside the root is rejected rather than followed."""
    outside = roots["docs"] / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    (roots["music"] / "Beatles").mkdir()
    link = roots["music"] / "escape.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this host")

    res = _move(source_library_id="core:music", paths=["escape.txt"],
                target_library_id="core:music", target_path="Beatles")
    assert res.moved == []
    assert res.failed
    assert outside.read_text(encoding="utf-8") == "nope"


# ─── HTTP surface (admin gate + request validation) ────────────────────────


@requires_db
def test_move_endpoint_moves_over_http(registry, roots, reindex_spy):
    (roots["music"] / "Beatles").mkdir()
    (roots["music"] / "song.flac").write_bytes(b"aa")
    with _client() as c:
        r = c.post("/api/files/move", json={
            "source_library_id": "core:music", "paths": ["song.flac"],
            "target_library_id": "core:music", "target_path": "Beatles",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moved"] == ["Beatles/song.flac"]
    assert body["reindex_triggered"] is True
    assert reindex_spy == ["/v1/admin/library/reindex"]


@requires_db
def test_move_endpoint_requires_at_least_one_path(registry, roots):
    with _client() as c:
        r = c.post("/api/files/move", json={
            "source_library_id": "core:music", "paths": [],
            "target_library_id": "core:music", "target_path": "",
        })
    assert r.status_code == 422


@requires_db
def test_move_endpoint_404s_for_an_unknown_library(registry, roots):
    with _client() as c:
        r = c.post("/api/files/move", json={
            "source_library_id": "core:nope", "paths": ["x"],
            "target_library_id": "core:music", "target_path": "",
        })
    assert r.status_code == 404
