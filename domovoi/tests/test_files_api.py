"""Endpoint tests for the Files API (design §2) — web/backend/api/files.py.

Lives under ``domovoi/tests`` for the test-DB conftest safety net (same as
test_music_upload_api / test_documents). The registry is stubbed to a
controlled set of libraries rooted in tmp dirs so each endpoint's behavior —
editable/importable rejection, ejected-drive 410, reindex fanout, upload
dedupe, recursive-delete confinement, import containment — is deterministic.

Trust posture under test: reads and the open writes (upload / move / import)
need no admin at all; only ``/delete`` is admin-gated, and passes here via the
pre-setup grace (fresh test DB has no admin credential) except in the tests
that deliberately claim the admin tier to prove the split. Every open write
names a ``device_id`` — the ``files_device_blocks`` tests at the bottom are
what that field is for.
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
            data={"library_id": "core:music", "path": "", "device_id": "browser-test"},
            files=[("files", ("t.mp3", b"one", "audio/mpeg"))],
        )
        r2 = c.post(
            "/api/files/upload",
            data={"library_id": "core:music", "path": "", "device_id": "browser-test"},
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
            data={"library_id": "removable:E", "path": "", "device_id": "browser-test"},
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
            data={"library_id": "core:documents", "path": "", "device_id": "browser-test"},
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
                "device_id": "browser-test",
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
                "device_id": "browser-test",
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
                "device_id": "browser-test",
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
                "device_id": "browser-test",
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
                "device_id": "browser-test",
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

    # The direct-call tests below exercise the pure move logic with no DB, so
    # the device-block lookup (which needs one) is stubbed to "not blocked".
    # Enforcement itself is covered by the HTTP tests under @requires_db.
    async def _allow(device_id):
        return None

    monkeypatch.setattr(files_api, "_assert_can_write", _allow)
    return calls


def _move(**kw):
    """Call the move endpoint directly (no admin dependency, no DB)."""
    import asyncio

    kw.setdefault("device_id", "browser-test")
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
            "device_id": "browser-test",
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
            "device_id": "browser-test",
        })
    assert r.status_code == 422


@requires_db
def test_move_endpoint_404s_for_an_unknown_library(registry, roots):
    with _client() as c:
        r = c.post("/api/files/move", json={
            "source_library_id": "core:nope", "paths": ["x"],
            "target_library_id": "core:music", "target_path": "",
            "device_id": "browser-test",
        })
    assert r.status_code == 404


# ─── Trust posture: open reads/writes, admin-only delete ───────────────────
#
# The pre-setup grace makes every gate pass on a fresh test DB, which is
# exactly the condition under which the old "everything is admin" gating
# looked fine in tests and broke on a real install. So these claim the admin
# tier first and prove the split from the OTHER side: no credential sent.


@pytest.fixture
def admin_claimed():
    """Set an admin credential (so the pre-setup grace no longer applies) and
    clear it afterwards. The token is never sent — these tests are about what
    an UNAUTHENTICATED device may do once an admin exists."""
    import asyncio

    from sqlalchemy import text as sql

    from domovoi import admin_auth
    from web.backend.db import session_scope

    async def _claim():
        async with session_scope() as s:
            await admin_auth.set_password(s, "correct-horse-battery")

    async def _clear():
        async with session_scope() as s:
            await s.execute(sql("TRUNCATE admin_auth, admin_sessions CASCADE"))

    asyncio.run(_clear())
    asyncio.run(_claim())
    yield
    asyncio.run(_clear())


@requires_db
def test_reads_and_open_writes_need_no_admin_once_one_exists(
    registry, roots, reindex_spy, admin_claimed
):
    (roots["music"] / "a.mp3").write_bytes(b"a")
    (roots["music"] / "Beatles").mkdir()
    with _client() as c:
        assert c.get("/api/files/libraries").status_code == 200
        assert c.get(
            "/api/files/browse", params={"library_id": "core:music", "path": ""}
        ).status_code == 200
        assert c.get(
            "/api/files/download", params={"library_id": "core:music", "path": "a.mp3"}
        ).status_code == 200
        up = c.post(
            "/api/files/upload",
            data={"library_id": "core:music", "path": "", "device_id": "browser-test"},
            files=[("files", ("u.mp3", b"u", "audio/mpeg"))],
        )
        assert up.status_code == 200, up.text
        mv = c.post("/api/files/move", json={
            "source_library_id": "core:music", "paths": ["a.mp3"],
            "target_library_id": "core:music", "target_path": "Beatles",
            "device_id": "browser-test",
        })
        assert mv.status_code == 200, mv.text


@requires_db
def test_delete_is_the_one_verb_that_needs_admin(registry, roots, admin_claimed):
    (roots["music"] / "keep.mp3").write_bytes(b"k")
    with _client() as c:
        r = c.post(
            "/api/files/delete",
            json={"library_id": "core:music", "paths": ["keep.mp3"]},
        )
    assert r.status_code == 401
    assert "admin" in r.json()["detail"]
    # Nothing happened.
    assert (roots["music"] / "keep.mp3").exists()


@requires_db
def test_open_writes_must_name_a_device(registry, roots):
    """The blocklist would be trivially evaded by omitting the field, which is
    far easier than claiming someone else's id — so every open write must name
    a device. Reading still doesn't have to."""
    (roots["usb"] / "x.mp3").write_bytes(b"x")
    with _client() as c:
        up = c.post(
            "/api/files/upload",
            data={"library_id": "core:music", "path": ""},
            files=[("files", ("u.mp3", b"u", "audio/mpeg"))],
        )
        assert up.status_code == 422
        mv = c.post("/api/files/move", json={
            "source_library_id": "core:music", "paths": ["u.mp3"],
            "target_library_id": "core:music", "target_path": "",
        })
        assert mv.status_code == 422
        im = c.post("/api/files/import", json={
            "source_library_id": "removable:E", "source_path": "x.mp3",
            "target_library_id": "core:music", "target_path": "",
        })
        assert im.status_code == 422
        assert c.get(
            "/api/files/browse", params={"library_id": "core:music", "path": ""}
        ).status_code == 200


# ─── Device blocks ─────────────────────────────────────────────────────────


def test_blocked_message_names_the_device_and_the_note() -> None:
    assert files_api._blocked_message(
        {"device_name": "Kids iPad", "device_id": "android-kid", "note": "bedtime"}
    ) == "Kids iPad isn't allowed to change files (bedtime)"
    # Falls back to the id when the block was made by id alone, and drops
    # the parenthetical when there's no note.
    assert files_api._blocked_message(
        {"device_name": None, "device_id": "android-kid", "note": None}
    ) == "android-kid isn't allowed to change files"


@pytest.fixture
def clean_block_tables():
    import asyncio

    from sqlalchemy import text as sql

    from web.backend.db import session_scope

    async def _truncate():
        async with session_scope() as s:
            await s.execute(
                sql("TRUNCATE files_device_blocks, devices RESTART IDENTITY CASCADE")
            )

    asyncio.run(_truncate())
    yield
    asyncio.run(_truncate())


@requires_db
def test_files_blocks_crud_and_duplicate_conflict(clean_block_tables):
    with _client() as c:
        made = c.post(
            "/api/files/device-blocks",
            json={"device_id": "android-kid", "device_name": "Kids iPad",
                  "note": "bedtime"},
        )
        assert made.status_code == 201, made.text
        block_id = made.json()["id"]
        assert [b["id"] for b in c.get("/api/files/device-blocks").json()] == [block_id]

        again = c.post(
            "/api/files/device-blocks", json={"device_id": "android-kid"}
        )
        assert again.status_code == 409

        assert c.post("/api/files/device-blocks", json={"note": "x"}).status_code == 400

        assert c.delete(f"/api/files/device-blocks/{block_id}").status_code == 204
        assert c.get("/api/files/device-blocks").json() == []
        assert c.delete(f"/api/files/device-blocks/{block_id}").status_code == 404


@requires_db
def test_a_blocked_device_can_read_but_not_write(
    registry, roots, reindex_spy, clean_block_tables
):
    (roots["music"] / "song.flac").write_bytes(b"aa")
    (roots["music"] / "Beatles").mkdir()
    (roots["usb"] / "x.mp3").write_bytes(b"x")
    with _client() as c:
        c.post(
            "/api/devices/register",
            json={"device_id": "android-kid", "name": "Kids iPad"},
        )
        c.post(
            "/api/files/device-blocks",
            json={"device_id": "android-kid", "device_name": "Kids iPad",
                  "note": "bedtime"},
        )

        # Reading is never blocked — and the read says why writes will be.
        view = c.get(
            "/api/files/browse",
            params={"library_id": "core:music", "path": "", "device_id": "android-kid"},
        )
        assert view.status_code == 200
        assert view.json()["writable"] is False
        assert "Kids iPad" in view.json()["blocked_reason"]
        assert "bedtime" in view.json()["blocked_reason"]

        up = c.post(
            "/api/files/upload",
            data={"library_id": "core:music", "path": "", "device_id": "android-kid"},
            files=[("files", ("u.mp3", b"u", "audio/mpeg"))],
        )
        assert up.status_code == 403
        assert "Kids iPad" in up.json()["detail"]
        mv = c.post("/api/files/move", json={
            "source_library_id": "core:music", "paths": ["song.flac"],
            "target_library_id": "core:music", "target_path": "Beatles",
            "device_id": "android-kid",
        })
        assert mv.status_code == 403
        im = c.post("/api/files/import", json={
            "source_library_id": "removable:E", "source_path": "x.mp3",
            "target_library_id": "core:music", "target_path": "",
            "device_id": "android-kid",
        })
        assert im.status_code == 403
        # Nothing moved, nothing copied, nothing reindexed.
        assert (roots["music"] / "song.flac").exists()
        assert not (roots["music"] / "x.mp3").exists()
        assert reindex_spy == []

        # Another device is unaffected.
        other = c.get(
            "/api/files/browse",
            params={"library_id": "core:music", "path": "", "device_id": "browser-ok"},
        ).json()
        assert other["writable"] is True and other["blocked_reason"] is None
        ok = c.post("/api/files/move", json={
            "source_library_id": "core:music", "paths": ["song.flac"],
            "target_library_id": "core:music", "target_path": "Beatles",
            "device_id": "browser-ok",
        })
        assert ok.status_code == 200, ok.text


@requires_db
def test_a_name_block_survives_a_reinstall(registry, roots, clean_block_tables):
    """Block by NAME only; a fresh install (new id, same household label)
    is still caught because the server resolves the id to its registered
    name before matching."""
    (roots["music"] / "song.flac").write_bytes(b"aa")
    with _client() as c:
        c.post("/api/files/device-blocks", json={"device_name": "Kids iPad"})
        c.post(
            "/api/devices/register",
            json={"device_id": "android-newinstall", "name": "Kids iPad"},
        )
        mv = c.post("/api/files/move", json={
            "source_library_id": "core:music", "paths": ["song.flac"],
            "target_library_id": "core:music", "target_path": "",
            "device_id": "android-newinstall",
        })
    assert mv.status_code == 403
