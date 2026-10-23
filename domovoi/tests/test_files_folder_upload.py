"""Folder upload on the Files page — web/backend/api/files.py
``/upload-stream``, ``/upload-check``, ``/upload-done`` (wf/folder-upload).

A folder dropped on the page (or picked with Upload → Folder…) is sent one
file per request with its place inside the folder (``rel_path``), streamed
to disk, with one conflict rule for the whole batch and one reindex at the
end. These tests pin the three things that can go wrong on the server:

* path safety — every segment of ``rel_path`` is checked, missing folders
  are made inside the target only, never through a link out of it;
* memory — a large body is written a chunk at a time, so what the process
  holds does not grow with the file (WEB-19, deferred in round 4);
* conflicts — keep both / replace / skip, and a cancelled upload leaves
  nothing behind under any name.

DB-free: the block lookup is stubbed (as in test_files_api's
``blocked_kid``) and no lifespan is entered, so nothing here can hide
behind a ``requires_db`` skip.
"""

from __future__ import annotations

import asyncio
import tracemalloc
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

import web.backend.api.files as files_api
import web.backend.api.files_security as files_security
from domovoi.tests.auth_testkit import install_fake_db, make_request
from web.backend.api.files_security import MediaLibrary
from web.backend.main import app

XRW = {"X-Requested-With": "XMLHttpRequest"}


def _library(lib_id: str, root: Path, **kw) -> MediaLibrary:
    base = dict(
        id=lib_id, label=lib_id, kind="core", icon="folder", kind_icon="folder",
        owner=None, editable=True, importable=True, doc_editing=False,
        reindex_kind=None, present=True, root_path=root,
    )
    base.update(kw)
    return MediaLibrary(**base)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    books = tmp_path / "audiobooks"
    ro = tmp_path / "readonly"
    outside = tmp_path / "outside"
    for d in (books, ro, outside):
        d.mkdir()
    libs = {
        "core:audiobooks": _library("core:audiobooks", books.resolve(), reindex_kind="audiobooks"),
        "plugin:x:ro": _library("plugin:x:ro", ro.resolve(), editable=False, kind="plugin"),
    }

    async def _build():
        return list(libs.values())

    monkeypatch.setattr(files_api, "build_libraries", _build)

    async def _status(device_id):
        return "Kids iPad isn't allowed to change files" if device_id == "android-kid" else None

    monkeypatch.setattr(files_api, "_block_status", _status)
    reindexed: list[str] = []

    async def _trigger(kind, request, *, removed=()):
        reindexed.append(kind)
        return True

    monkeypatch.setattr(files_api, "_trigger_reindex", _trigger)
    install_fake_db(monkeypatch, admin=False)
    return {"books": books.resolve(), "ro": ro, "outside": outside, "reindexed": reindexed}


def _client() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _put(c, rel, body=b"x", **params):
    q = {"library_id": "core:audiobooks", "path": "", "device_id": "browser-test",
         "rel_path": rel}
    q.update(params)
    return c.post("/api/files/upload-stream", params=q, content=body, headers=XRW)


# ─── Path safety ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", [
    "../escape.mp3",
    "Book/../../escape.mp3",
    "Book/./01.mp3",
    "/etc/passwd",
    "\\\\server\\share\\x.mp3",
    "C:/Windows/x.mp3",
    "c:x.mp3",
    "Book\\..\\..\\x.mp3",
    "Book//01.mp3",
    "Book/",
    "CON/01.mp3",
    "Book/nul.mp3",
    "Book/COM1.txt",
    "Book/LPT9",
    "Book./01.mp3",
    "Book /01.mp3",
    "Book/01.mp3 ",
    "Book/a\x00b.mp3",
    "Book/a\tb.mp3",
    ".ssh/authorized_keys",
    "Book/.env",
    "Book/id_rsa",
    "/".join(["d"] * 40) + "/x.mp3",
    "Book/" + "a" * 256,
])
def test_split_refuses_unsafe_paths(rel):
    with pytest.raises(HTTPException) as e:
        files_api.split_upload_rel(rel)
    assert e.value.status_code == 400


def test_split_keeps_ordinary_book_paths():
    assert files_api.split_upload_rel("Book Title/Disc 1/01 - Intro.mp3") == [
        "Book Title", "Disc 1", "01 - Intro.mp3"]
    # Unicode and punctuation that every filesystem stores are fine.
    assert files_api.split_upload_rel("Мастер и Маргарита/01.m4b")[-1] == "01.m4b"
    assert files_api.split_upload_rel("Ender's Game (Unabridged)/01.mp3")[0] == "Ender's Game (Unabridged)"


def test_colons_refused_only_where_ntfs_cant_store_them(monkeypatch):
    monkeypatch.setattr(files_security, "WINDOWS_NAME_RULES", False)
    assert files_api.split_upload_rel("Dune: Messiah/01.mp3")[0] == "Dune: Messiah"
    monkeypatch.setattr(files_security, "WINDOWS_NAME_RULES", True)
    with pytest.raises(HTTPException):
        files_api.split_upload_rel("Dune: Messiah/01.mp3")


def test_nested_folders_are_made_inside_the_target(setup):
    with _client() as c:
        r = _put(c, "Book Title/Disc 1/01.mp3", b"one")
        r2 = _put(c, "Book Title/Disc 2/01.mp3", b"two")
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == "Book Title/Disc 1/01.mp3"
    assert r2.json()["saved"] == "Book Title/Disc 2/01.mp3"
    assert (setup["books"] / "Book Title" / "Disc 1" / "01.mp3").read_bytes() == b"one"
    assert (setup["books"] / "Book Title" / "Disc 2" / "01.mp3").read_bytes() == b"two"


def test_upload_into_a_subfolder_on_screen(setup):
    (setup["books"] / "Shelf").mkdir()
    with _client() as c:
        r = _put(c, "Book/01.mp3", path="Shelf")
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == "Shelf/Book/01.mp3"


def test_traversal_over_http_writes_nothing(setup):
    with _client() as c:
        for rel in ("../outside/x.mp3", "Book/../../outside/x.mp3", "C:/x.mp3", "Book\\..\\x.mp3"):
            r = _put(c, rel)
            assert r.status_code == 400, (rel, r.text)
    assert list(setup["outside"].iterdir()) == []
    assert list(setup["books"].iterdir()) == []


def test_a_link_out_of_the_library_is_not_followed(setup):
    link = setup["books"] / "Book"
    try:
        link.symlink_to(setup["outside"], target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this box can't make symlinks")
    with _client() as c:
        r = _put(c, "Book/01.mp3")
    assert r.status_code in (400, 404), r.text
    assert list(setup["outside"].iterdir()) == []


def test_a_file_where_a_folder_should_go_is_409(setup):
    (setup["books"] / "Book").write_bytes(b"not a folder")
    with _client() as c:
        r = _put(c, "Book/01.mp3")
    assert r.status_code == 409


def test_gates_block_readonly_and_admin_write(setup):
    with _client() as c:
        blocked = _put(c, "Book/01.mp3", device_id="android-kid")
        ro = _put(c, "Book/01.mp3", library_id="plugin:x:ro")
        check = c.post("/api/files/upload-check", json={
            "library_id": "core:audiobooks", "rel_paths": ["a.mp3"], "device_id": "android-kid"})
        done = c.post("/api/files/upload-done", json={
            "library_id": "core:audiobooks", "device_id": "android-kid"})
        no_xrw = TestClient(app).post(
            "/api/files/upload-stream", content=b"x",
            params={"library_id": "core:audiobooks", "rel_path": "a.mp3", "device_id": "browser-test"})
    assert blocked.status_code == 403 and "Kids iPad" in blocked.json()["detail"]
    assert ro.status_code == 403
    assert check.status_code == 403 and done.status_code == 403
    assert no_xrw.status_code == 403
    assert list(setup["books"].iterdir()) == []
    assert list(setup["ro"].iterdir()) == []


# ─── Conflicts ──────────────────────────────────────────────────────────────


def test_conflict_rules(setup):
    book = setup["books"] / "Book"
    book.mkdir()
    (book / "01.mp3").write_bytes(b"old")
    with _client() as c:
        check = c.post("/api/files/upload-check", json={
            "library_id": "core:audiobooks", "device_id": "browser-test",
            "rel_paths": ["Book/01.mp3", "Book/02.mp3", "../x.mp3"]})
        keep = _put(c, "Book/01.mp3", b"keep")
        skip = _put(c, "Book/01.mp3", b"skip", on_conflict="skip")
        repl = _put(c, "Book/01.mp3", b"new", on_conflict="replace")
    assert check.status_code == 200, check.text
    assert check.json()["existing"] == ["Book/01.mp3"]
    assert list(check.json()["refused"]) == ["../x.mp3"]
    assert keep.json()["saved"] == "Book/01 (1).mp3"
    assert (book / "01 (1).mp3").read_bytes() == b"keep"
    assert skip.json()["skipped"] == "already there" and skip.json()["saved"] == ""
    assert repl.json()["replaced"] is True and repl.json()["saved"] == "Book/01.mp3"
    assert (book / "01.mp3").read_bytes() == b"new"
    assert sorted(p.name for p in book.iterdir()) == ["01 (1).mp3", "01.mp3"]


def test_replace_never_overwrites_a_folder(setup):
    (setup["books"] / "Book").mkdir()
    with _client() as c:
        r = _put(c, "Book", b"x", on_conflict="replace")
    assert r.status_code == 200
    assert r.json()["skipped"]
    assert (setup["books"] / "Book").is_dir()


def test_reindex_once_per_batch(setup):
    with _client() as c:
        for i in range(3):
            assert _put(c, f"Book/{i:02}.mp3", reindex="false").status_code == 200
        assert setup["reindexed"] == []
        done = c.post("/api/files/upload-done", json={
            "library_id": "core:audiobooks", "device_id": "browser-test"})
    assert done.json() == {"reindex_triggered": True, "reindex_kind": "audiobooks"}
    assert setup["reindexed"] == ["audiobooks"]


def test_part_files_are_hidden_from_the_listing(setup):
    (setup["books"] / f"{files_api.PART_PREFIX}abc.part").write_bytes(b"x")
    (setup["books"] / "real.mp3").write_bytes(b"x")
    snap = files_api._read_folder(setup["books"], setup["books"])
    assert [r["name"] for r in snap.rows] == ["real.mp3"]


# ─── Size and memory ────────────────────────────────────────────────────────


def test_declared_size_over_the_cap_is_a_plain_413(setup, monkeypatch):
    monkeypatch.setattr(files_api, "MAX_UPLOAD_FILE_BYTES", 1000)
    with _client() as c:
        r = _put(c, "Book/big.m4b", b"x" * 2000)
    assert r.status_code == 413
    assert "one file can be at most" in r.json()["detail"]
    assert not (setup["books"] / "Book" / "big.m4b").exists()


class _Req:
    """Just enough of a Request for the route: headers and a body stream
    that yields ``total`` bytes in 64 KiB chunks without ever holding them,
    and optionally breaks part-way like a cancelled upload."""

    def __init__(self, total: int, fail_after: int | None = None):
        self.headers = {"content-length": str(total)}
        self.total = total
        self.fail_after = fail_after
        self._inner = make_request()

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def stream(self):
        chunk = b"\0" * 65536
        sent = 0
        while sent < self.total:
            if self.fail_after is not None and sent >= self.fail_after:
                raise ClientDisconnect()
            n = min(len(chunk), self.total - sent)
            sent += n
            yield chunk[:n]


def _stream(req, rel, **kw):
    kw.setdefault("path", "")
    kw.setdefault("on_conflict", "keep_both")
    kw.setdefault("reindex", False)
    return asyncio.run(files_api.upload_stream(
        req, library_id="core:audiobooks", rel_path=rel, device_id="browser-test", **kw))


def test_a_200_mb_upload_is_streamed_in_bounded_memory(setup):
    total = 200 * 1024 * 1024
    tracemalloc.start()
    try:
        res = _stream(_Req(total), "Big Book/whole.m4b")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert res.saved == "Big Book/whole.m4b"
    assert (setup["books"] / "Big Book" / "whole.m4b").stat().st_size == total
    # A buffered upload would peak at >= 200 MB; streamed it is about one
    # write chunk (1 MiB) plus the generator's 64 KiB, whatever the size.
    assert peak < 8 * 1024 * 1024, f"peak {peak / 2**20:.1f} MiB for a 200 MiB body"


def test_a_cancelled_upload_leaves_nothing_behind(setup):
    (setup["books"] / "Book").mkdir()
    (setup["books"] / "Book" / "01.mp3").write_bytes(b"keep me")
    with pytest.raises(ClientDisconnect):
        _stream(_Req(5 * 1024 * 1024, fail_after=2 * 1024 * 1024), "Book/01.mp3",
                on_conflict="replace")
    assert sorted(p.name for p in (setup["books"] / "Book").iterdir()) == ["01.mp3"]
    assert (setup["books"] / "Book" / "01.mp3").read_bytes() == b"keep me"


def test_multipart_upload_no_longer_reads_files_whole(setup, monkeypatch):
    """The old ``/upload`` (files and the Android app) copied each part with
    ``write_bytes(await up.read())``; it now copies from the spool in chunks."""
    calls = []

    async def _no_read(self, size=-1):  # pragma: no cover — must not run
        calls.append(size)
        return b""

    from starlette.datastructures import UploadFile
    monkeypatch.setattr(UploadFile, "read", _no_read)
    with _client() as c:
        r = c.post("/api/files/upload",
                   data={"library_id": "core:audiobooks", "path": "", "device_id": "browser-test"},
                   files=[("files", ("a.mp3", b"abc" * 1000, "audio/mpeg"))], headers=XRW)
    assert r.status_code == 200, r.text
    assert calls == []
    assert (setup["books"] / "a.mp3").read_bytes() == b"abc" * 1000
