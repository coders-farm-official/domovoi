"""Tests for the Office Suite backend (web/backend/api/documents.py).

Three concerns, matching the plan's verification list:
  * Containment — every path resolves inside documents_dir; traversal /
    absolute paths are rejected (the music.py:349-367 pattern).
  * JWT — sign/verify round-trips; a wrong secret / tampered token is
    rejected; a blank secret refuses to mint (non-optional).
  * Lock contention — document_sessions UNIQUE(rel_path) makes a second
    engine's open on a locked file fail fast (409).

The DB tests use the ``requires_db`` marker + the test DB only (conftest
forces the ``_test`` suffix). They clean up their own document_sessions
rows since that table isn't in conftest's TRUNCATE list.
"""

from __future__ import annotations

import io
import json

import jwt
import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.tests.conftest import requires_db
from web.backend.api import documents as docs


# ─── Containment ────────────────────────────────────────────────────
def test_safe_target_accepts_plain_name(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    target = docs._safe_target("report.docx")
    assert target == (tmp_path / "report.docx").resolve()


def test_safe_target_rejects_parent_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    with pytest.raises(HTTPException) as ei:
        docs._safe_target("../../etc/passwd")
    assert ei.value.status_code == 400


def test_safe_target_rejects_absolute_escape(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    # Leading slash is stripped, so this stays inside; an absolute path to
    # a *different* root must be refused. Use a sibling dir.
    outside = tmp_path.parent / "somewhere_else" / "x.docx"
    with pytest.raises(HTTPException) as ei:
        docs._safe_target(str(outside))
    assert ei.value.status_code == 400


def test_safe_target_rejects_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    with pytest.raises(HTTPException):
        docs._safe_target("")


def test_rel_of_roundtrips(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    t = docs._safe_target("a.docx")
    assert docs._rel_of(t) == "a.docx"


# ─── JWT sign / verify ──────────────────────────────────────────────
def test_jwt_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "onlyoffice_jwt_secret", "s3cret")
    tok = docs._sign({"scope": "read", "rel": "a.docx"}, "onlyoffice")
    claims = docs._verify(tok, "onlyoffice")
    assert claims["rel"] == "a.docx"


def test_jwt_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr(settings, "onlyoffice_jwt_secret", "right")
    tok = docs._sign({"rel": "a.docx"}, "onlyoffice")
    # Verifying with a different secret must 403.
    monkeypatch.setattr(settings, "onlyoffice_jwt_secret", "wrong")
    with pytest.raises(HTTPException) as ei:
        docs._verify(tok, "onlyoffice")
    assert ei.value.status_code == 403


def test_jwt_blank_secret_refuses_to_mint(monkeypatch):
    monkeypatch.setattr(settings, "onlyoffice_jwt_secret", "")
    with pytest.raises(HTTPException) as ei:
        docs._sign({"rel": "a.docx"}, "onlyoffice")
    assert ei.value.status_code == 503


def test_read_token_binds_to_rel(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(settings, "onlyoffice_jwt_secret", "sekret")
    monkeypatch.setattr(settings, "collabora_jwt_secret", "")
    tok = docs._read_token("a.docx", "onlyoffice")
    assert docs._verify_read_token(tok, expected_rel="a.docx") is not None
    # Same token must not authorize a different file.
    assert docs._verify_read_token(tok, expected_rel="b.docx") is None


def test_read_token_rejects_foreign_signature(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(settings, "onlyoffice_jwt_secret", "sekret")
    monkeypatch.setattr(settings, "collabora_jwt_secret", "")
    forged = jwt.encode({"scope": "read", "rel": "a.docx"}, "attacker", algorithm="HS256")
    assert docs._verify_read_token(forged, expected_rel="a.docx") is None


# ─── Blank-file generation ──────────────────────────────────────────
def test_blank_docx_is_a_zip():
    import zipfile, io
    data = docs._blank_docx()
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    assert "word/document.xml" in names
    assert "[Content_Types].xml" in names


def test_blank_xlsx_is_a_zip():
    import zipfile, io
    data = docs._blank_xlsx()
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert "xl/workbook.xml" in zf.namelist()


def test_blank_odg_is_valid_odf():
    import zipfile, io
    data = docs._blank_odg()
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    # ODF requires the mimetype entry FIRST and stored uncompressed.
    assert names[0] == "mimetype"
    assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    assert zf.read("mimetype") == b"application/vnd.oasis.opendocument.graphics"
    assert "content.xml" in names
    assert "META-INF/manifest.xml" in names


# ─── WOPI token binding ─────────────────────────────────────────────
def test_wopi_rel_requires_matching_key(monkeypatch):
    monkeypatch.setattr(settings, "collabora_jwt_secret", "wopisecret")
    tok = docs._sign({"scope": "wopi", "rel": "a.docx", "key": "KEY1"}, "collabora")
    assert docs._wopi_rel("KEY1", tok) == "a.docx"
    with pytest.raises(HTTPException):
        docs._wopi_rel("OTHERKEY", tok)


# ─── Open-action categorisation (pure) ──────────────────────────────
def test_doc_category_routing():
    assert docs._doc_category(".docx") == "office"
    assert docs._doc_category(".rtf") == "office"
    assert docs._doc_category(".xlsx") == "office"          # sheets → office engine
    assert docs._doc_category(".csv") == "office"
    assert docs._doc_category(".excalidraw") == "drawing"   # Excalidraw canvas
    assert docs._doc_category(".odg") == "collabora_drawing"  # Collabora Draw
    assert docs._doc_category(".svg") == "newtab"           # exported svg → new tab
    assert docs._doc_category(".pdf") == "newtab"
    assert docs._doc_category(".png") == "newtab"
    assert docs._doc_category(".JPG") == "newtab"          # case-insensitive
    assert docs._doc_category(".txt") == "text"
    assert docs._doc_category(".md") == "text"
    assert docs._doc_category(".yaml") == "text"
    # Unrecognized types fall through to the text editor (it probes /text).
    assert docs._doc_category(".xyz") == "text"
    assert docs._doc_category("") == "text"


# ─── In-app text editor: read (GET /text) ───────────────────────────
@pytest.mark.asyncio
async def test_read_text_utf8_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    # write_bytes (not write_text) so the newline bytes are exact on any
    # platform — Path.write_text translates \n on Windows.
    (tmp_path / "notes.md").write_bytes(b"# Hello\nworld\n")
    out = await docs.read_text_file("notes.md")
    assert out["text"] == "# Hello\nworld\n"
    assert out["newline"] == "\n"
    assert out["rel_path"] == "notes.md"


@pytest.mark.asyncio
async def test_read_text_detects_crlf(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "win.txt").write_bytes(b"a\r\nb\r\n")
    out = await docs.read_text_file("win.txt")
    assert out["newline"] == "\r\n"


@pytest.mark.asyncio
async def test_read_text_binary_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    # Invalid UTF-8 (lone 0xff / 0xfe) — a stand-in for a binary blob.
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x01BINARY")
    resp = await docs.read_text_file("blob.bin")
    assert resp.status_code == 415
    body = json.loads(resp.body)
    assert body == {"editable": False, "reason": "binary"}


@pytest.mark.asyncio
async def test_read_text_too_large_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(docs, "_TEXT_MAX_BYTES", 8)
    (tmp_path / "big.txt").write_text("way more than eight bytes", encoding="utf-8")
    resp = await docs.read_text_file("big.txt")
    assert resp.status_code == 415
    assert json.loads(resp.body)["reason"] == "too_large"


@pytest.mark.asyncio
async def test_read_text_missing_404(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    with pytest.raises(HTTPException) as ei:
        await docs.read_text_file("nope.txt")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_read_text_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    with pytest.raises(HTTPException) as ei:
        await docs.read_text_file("../../etc/passwd")
    assert ei.value.status_code == 400


# ─── In-app text editor: write (PUT /text) ──────────────────────────
@pytest.mark.asyncio
async def test_write_text_roundtrips(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    row = await docs.write_text_file("out.txt", docs.TextWriteRequest(text="line1\nline2\n"))
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "line1\nline2\n"
    assert row.category == "text"
    # Newlines preserved verbatim (no CRLF translation on write).
    await docs.write_text_file("crlf.txt", docs.TextWriteRequest(text="a\r\nb"))
    assert (tmp_path / "crlf.txt").read_bytes() == b"a\r\nb"


@pytest.mark.asyncio
async def test_write_text_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    with pytest.raises(HTTPException) as ei:
        await docs.write_text_file("../evil.txt", docs.TextWriteRequest(text="x"))
    assert ei.value.status_code == 400
    assert not (tmp_path.parent / "evil.txt").exists()


# ─── Upload (POST /upload) ──────────────────────────────────────────
def _upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


@pytest.mark.asyncio
async def test_upload_lands_in_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    res = await docs.upload_documents([_upload("hello.txt", b"hi there")])
    assert res.saved == ["hello.txt"]
    assert (tmp_path / "hello.txt").read_bytes() == b"hi there"


@pytest.mark.asyncio
async def test_upload_strips_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    res = await docs.upload_documents([_upload("../../evil.txt", b"pwn")])
    # Directory components are stripped → the file lands INSIDE the dir,
    # never escapes it.
    assert res.saved == ["evil.txt"]
    assert (tmp_path / "evil.txt").exists()
    assert not (tmp_path.parent / "evil.txt").exists()


@pytest.mark.asyncio
async def test_upload_dedupes_collisions(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    await docs.upload_documents([_upload("dup.txt", b"first")])
    res = await docs.upload_documents([_upload("dup.txt", b"second")])
    assert res.saved == ["dup (1).txt"]
    assert (tmp_path / "dup.txt").read_bytes() == b"first"
    assert (tmp_path / "dup (1).txt").read_bytes() == b"second"


@pytest.mark.asyncio
async def test_upload_all_skipped_400(monkeypatch, tmp_path):
    # A name that sanitizes away entirely ("..") is skipped; when nothing
    # is saved the endpoint 400s rather than silently succeeding.
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    with pytest.raises(HTTPException) as ei:
        await docs.upload_documents([_upload("..", b"x")])
    assert ei.value.status_code == 400


# ─── Browser-facing raw serve (GET /raw) ────────────────────────────
@pytest.mark.asyncio
async def test_raw_serve_mime_and_inline(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    resp = await docs.serve_raw("pic.png")
    assert resp.media_type == "image/png"
    assert resp.headers["content-disposition"].startswith("inline")


@pytest.mark.asyncio
async def test_raw_serve_unknown_ext_octet_stream(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "thing.xyz").write_bytes(b"data")
    resp = await docs.serve_raw("thing.xyz")
    assert resp.media_type == "application/octet-stream"


@pytest.mark.asyncio
async def test_raw_serve_missing_404(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    with pytest.raises(HTTPException) as ei:
        await docs.serve_raw("ghost.pdf")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_raw_serve_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    with pytest.raises(HTTPException) as ei:
        await docs.serve_raw("../../secret")
    assert ei.value.status_code == 400


# ─── Lock contention (DB) ───────────────────────────────────────────
async def _clean_locks():
    async with session_scope() as s:
        await s.execute(text("DELETE FROM document_sessions"))


@requires_db
@pytest.mark.asyncio
async def test_open_lock_contention(monkeypatch, tmp_path):
    """Opening a file in onlyoffice then attempting collabora on the same
    file must 409 (UNIQUE(rel_path) + the pre-check), and the 409 fires
    before any engine-specific network call."""
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(settings, "onlyoffice_enabled", True)
    monkeypatch.setattr(settings, "collabora_enabled", True)
    monkeypatch.setattr(settings, "onlyoffice_jwt_secret", "oo")
    monkeypatch.setattr(settings, "collabora_jwt_secret", "co")
    (tmp_path / "report.docx").write_bytes(docs._blank_docx())

    await _clean_locks()
    try:
        cfg = await docs.open_document(docs.OpenRequest(rel_path="report.docx", engine="onlyoffice"))
        assert cfg["engine"] == "onlyoffice"

        with pytest.raises(HTTPException) as ei:
            await docs.open_document(docs.OpenRequest(rel_path="report.docx", engine="collabora"))
        assert ei.value.status_code == 409

        # Re-opening in the SAME engine is idempotent (reuses the row/key).
        cfg2 = await docs.open_document(docs.OpenRequest(rel_path="report.docx", engine="onlyoffice"))
        assert cfg2["editor_key"] == cfg["editor_key"]

        # Exactly one lock row exists for the file.
        async with session_scope() as s:
            n = (await s.execute(
                text("SELECT count(*) FROM document_sessions WHERE rel_path = :p"),
                {"p": "report.docx"},
            )).scalar()
        assert n == 1
    finally:
        await _clean_locks()


@requires_db
@pytest.mark.asyncio
async def test_close_releases_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    monkeypatch.setattr(settings, "onlyoffice_enabled", True)
    monkeypatch.setattr(settings, "onlyoffice_jwt_secret", "oo")
    (tmp_path / "notes.docx").write_bytes(docs._blank_docx())

    await _clean_locks()
    try:
        await docs.open_document(docs.OpenRequest(rel_path="notes.docx", engine="onlyoffice"))
        await docs.close_document(docs.CloseRequest(rel_path="notes.docx"))
        async with session_scope() as s:
            n = (await s.execute(
                text("SELECT count(*) FROM document_sessions WHERE rel_path = :p"),
                {"p": "notes.docx"},
            )).scalar()
        assert n == 0
    finally:
        await _clean_locks()


@requires_db
@pytest.mark.asyncio
async def test_sweeper_clears_stale_lock(monkeypatch, tmp_path):
    """The stale-lock sweeper removes rows idle past the timeout."""
    from domovoi.workers.document_lock_sweeper import DocumentLockSweeper

    monkeypatch.setattr(settings, "document_lock_stale_sec", 0.0)  # everything is stale
    await _clean_locks()
    try:
        async with session_scope() as s:
            await s.execute(text(
                "INSERT INTO document_sessions (rel_path, engine, editor_key) "
                "VALUES ('stale.docx', 'onlyoffice', 'k')"
            ))
        swept = await DocumentLockSweeper().tick()
        assert swept >= 1
        async with session_scope() as s:
            n = (await s.execute(text("SELECT count(*) FROM document_sessions"))).scalar()
        assert n == 0
    finally:
        await _clean_locks()
