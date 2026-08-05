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
