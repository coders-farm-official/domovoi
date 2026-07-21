"""Offline Office Suite API — Documents / Spreadsheets / Drawings.

Web-only feature (no domovoi handler, no ``requires_network``
contract). The web backend is the coordinator, exactly like the
music/MPD wiring already is: it lists files in a single flat
``documents_dir``, serves a file's bytes to a document-server
container, receives the save callback (OnlyOffice) or WOPI PutFile
(Collabora), and manages a per-file engine lock (``document_sessions``)
so two engines never edit the same file at once.

Three editing surfaces:

  * OnlyOffice CE — sidecar container. Browser loads its
    ``.../web-apps/apps/api/documents/api.js`` and instantiates
    ``new DocsAPI.DocEditor(...)`` with a config WE sign as a JWT
    (``onlyoffice_jwt_secret``). On save the container POSTs
    ``/api/documents/callback/onlyoffice`` with a status; we download the
    edited bytes from the URL it hands us and write them back.
  * Collabora CODE — sidecar container speaking WOPI. Browser POSTs a
    hidden form to the Collabora ``urlsrc`` (discovered from
    ``/hosting/discovery``) with ``WOPISrc`` pointing at our WOPI
    endpoints and an ``access_token`` WE sign (``collabora_jwt_secret``).
    Collabora then calls CheckFileInfo / GetFile / PutFile back on us.
  * Excalidraw — in-page React lib, NO container, NO lock (single-user,
    in-page). Plain JSON/SVG read+write into ``documents_dir``.

EVERY served/saved path is validated inside ``documents_dir`` via the
same realpath / ``relative_to()`` containment check ``music.py`` uses
(music.py:349-367) — path-traversal hygiene matters more here because
two containers fetch/write by path.

Invariants (see docs/FEATURE_PLAN_OFFICE_SUITE_2026-07-01.md):
  * LAN-routable hostnames, never localhost (iframe + callback both).
  * JWT secret non-optional — engines refuse to load blank.
  * RW, not RO — the office save path is writable.
  * Containment on every path.
  * One engine per file — ``document_sessions UNIQUE(rel_path)``.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import quote

import httpx
import jwt  # PyJWT
from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.config import settings as core_settings
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


# ─── File-type views ────────────────────────────────────────────────
# Storage is ONE flat dir; the three pages are filtered VIEWS by
# extension, not separate stores. New types ("and more") add a filter
# here, never a new directory.
#
# The Documents ("doc") view is the CATCH-ALL: it shows every file that
# isn't claimed by the Spreadsheets or Drawings views. That means the
# office word-processor types PLUS plain text (.txt/.md), PDFs, images,
# and anything unrecognized — each routed to the right open-action by
# ``_doc_category`` below. Sheet/drawing views stay narrow (exact-ext).
_DOC_EXTS = frozenset({".docx", ".doc", ".odt", ".rtf", ".txt", ".md"})
_SHEET_EXTS = frozenset({".xlsx", ".xls", ".ods", ".csv"})
_DRAWING_EXTS = frozenset({".excalidraw", ".svg"})

# Office word-processor types → the OnlyOffice/Collabora iframe flow.
# NOTE: .txt/.md are intentionally NOT here anymore — they open in the
# lightweight in-app text editor instead (still creatable as .docx via
# "New", which stays office-routed).
_OFFICE_WP_EXTS = frozenset({".docx", ".doc", ".odt", ".rtf"})

# Types that open raw in a NEW BROWSER TAB (PDFs + images) rather than an
# in-app viewer. Served by GET /raw with an inline Content-Disposition.
_IMAGE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".avif", ".svg"}
)
_NEWTAB_EXTS = _IMAGE_EXTS | {".pdf"}

# Known-text extensions the in-app text editor previews confidently. Any
# UNRECOGNIZED type also routes to "text" and is probed via GET /text —
# which falls back gracefully (415 binary/too_large) when it can't decode.
_TEXT_EXTS = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst",
        ".json", ".csv", ".tsv", ".log",
        ".yaml", ".yml", ".ini", ".toml", ".conf", ".cfg", ".env",
        ".xml", ".html", ".htm", ".css",
        ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".bash", ".ps1",
        ".sql", ".c", ".h", ".cpp", ".java", ".go", ".rs",
    }
)

_KIND_EXTS: dict[str, frozenset[str]] = {
    "doc": _DOC_EXTS,
    "sheet": _SHEET_EXTS,
    "drawing": _DRAWING_EXTS,
    # Collabora Draw native format — a separate "+ New" kind, opened in the
    # Collabora WOPI editor (NOT OnlyOffice, which has no Draw component).
    "collabora_drawing": frozenset({".odg"}),
}

# Exts claimed by the other two views — excluded from the doc catch-all.
_NON_DOC_EXTS = _SHEET_EXTS | _DRAWING_EXTS

# Cap on the in-app text editor's read size — bigger than this and we
# tell the UI to fall back to Download/Open-raw rather than stream MBs of
# text into a textarea.
_TEXT_MAX_BYTES = 2 * 1024 * 1024


def _doc_category(ext: str) -> str:
    """How the unified Documents page should open a file of this extension.

      * ``"office"``  → OnlyOffice iframe (word-processor + spreadsheet types).
      * ``"collabora_drawing"`` → Collabora Draw iframe (.odg vector drawings).
      * ``"drawing"`` → in-page Excalidraw editor (.excalidraw scenes).
      * ``"newtab"``  → open the raw endpoint in a new tab (PDFs, images,
                        incl. exported .svg).
      * ``"text"``    → in-app text editor (known text OR unrecognized;
                        the /text endpoint decides if it's really editable).
    """
    ext = ext.lower()
    if ext in _OFFICE_WP_EXTS or ext in _SHEET_EXTS:
        return "office"
    if ext == ".odg":
        return "collabora_drawing"
    if ext == ".excalidraw":
        return "drawing"
    if ext in _NEWTAB_EXTS:
        return "newtab"
    return "text"

# OnlyOffice documentType from extension. Drives which editor UI loads.
_ONLYOFFICE_DOCTYPE = {
    **{e: "word" for e in (".docx", ".doc", ".odt", ".rtf", ".txt", ".md")},
    **{e: "cell" for e in (".xlsx", ".xls", ".ods", ".csv")},
    **{e: "slide" for e in (".pptx", ".ppt", ".odp")},
}

_VALID_ENGINES = ("onlyoffice", "collabora")


# ─── Containment (reuse of the music.py:349-367 pattern) ────────────
def _documents_dir() -> Path:
    return Path(core_settings.documents_dir).expanduser().resolve(strict=False)


def _safe_target(rel_path: str) -> Path:
    """Resolve ``rel_path`` inside ``documents_dir`` or 400.

    Same realpath / ``relative_to()`` containment check music.py uses.
    Rejects absolute paths and ``..`` traversal: joining an absolute or
    drive-relative path escapes the base, and the post-resolve
    ``relative_to`` catches it. Belt-and-suspenders: reject obvious
    absolutes up front so the error message is clear.
    """
    if not rel_path or rel_path.strip() == "":
        raise HTTPException(status_code=400, detail="empty document path")
    # Normalise separators; a container/browser may hand us either.
    cleaned = rel_path.replace("\\", "/").lstrip("/")
    base = _documents_dir()
    target = (base / cleaned).resolve(strict=False)
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"refusing path {rel_path!r}: not inside DOCUMENTS_DIR "
                f"({core_settings.documents_dir!r})."
            ),
        )
    return target


def _rel_of(target: Path) -> str:
    """The POSIX-style rel_path (documents_dir-relative) for a resolved
    target. Stored in document_sessions and used as the lock key."""
    return target.relative_to(_documents_dir()).as_posix()


def _unique_path(dirpath: Path, name: str) -> Path:
    """A non-colliding path inside ``dirpath`` for ``name`` — appends
    `` (1)``, `` (2)`` … to the stem if the target already exists, so a
    second upload of "notes.txt" doesn't clobber the first. Mirrors the
    music.py upload dedupe strategy."""
    target = dirpath / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        cand = dirpath / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


# ─── JWT sign / verify (PyJWT) ──────────────────────────────────────
def _engine_secret(engine: str) -> str:
    secret = (
        core_settings.onlyoffice_jwt_secret
        if engine == "onlyoffice"
        else core_settings.collabora_jwt_secret
    )
    if not secret:
        # Non-optional: a blank secret means the engine would refuse to
        # load AND our tokens would be forgeable. Fail loud rather than
        # hand out an unsigned handshake.
        raise HTTPException(
            status_code=503,
            detail=(
                f"{engine} JWT secret is not configured "
                f"({engine}_jwt_secret is blank); refusing to mint a token."
            ),
        )
    return secret


def _sign(payload: dict[str, Any], engine: str) -> str:
    return jwt.encode(payload, _engine_secret(engine), algorithm="HS256")


def _verify(token: str, engine: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _engine_secret(engine), algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=403, detail=f"invalid {engine} token: {e}")


def _engine_enabled(engine: str) -> bool:
    return (
        core_settings.onlyoffice_enabled
        if engine == "onlyoffice"
        else core_settings.collabora_enabled
    )


# ─── Lock helpers (document_sessions) ───────────────────────────────
async def _current_lock(s, rel_path: str) -> Optional[str]:
    """The engine currently holding ``rel_path``, or None."""
    row = (
        await s.execute(
            text("SELECT engine FROM document_sessions WHERE rel_path = :p"),
            {"p": rel_path},
        )
    ).first()
    return row[0] if row else None


# ─── Schemas ────────────────────────────────────────────────────────
class DocumentRow(BaseModel):
    rel_path: str
    name: str
    ext: str
    size: int
    modified_at: float          # epoch seconds
    locked_by: Optional[str]    # 'onlyoffice' | 'collabora' | None
    # How the Documents page opens this file: 'office' | 'newtab' | 'text'.
    # Only meaningful for the doc view; sheet/drawing rows use their own
    # flow and leave this at the default.
    category: str = "office"


class OpenRequest(BaseModel):
    rel_path: str
    engine: Literal["onlyoffice", "collabora"]


class CloseRequest(BaseModel):
    rel_path: str


class CreateRequest(BaseModel):
    name: str
    # "text" is the free-form kind: the name is taken VERBATIM — no
    # extension is forced (an empty file with whatever name the user typed,
    # extension or not). The others get their default ext appended.
    kind: Literal["doc", "sheet", "drawing", "collabora_drawing", "text"]


class DrawingReadRequest(BaseModel):
    rel_path: str


class DrawingWriteRequest(BaseModel):
    rel_path: str
    content: str
    fmt: Literal["excalidraw", "svg"] = "excalidraw"


class TextWriteRequest(BaseModel):
    text: str


class UploadResult(BaseModel):
    saved: list[str]            # basenames actually written, in upload order
    skipped: list[str]          # "<name>: <reason>" for anything not saved


class DeleteRequest(BaseModel):
    # One or many — the row's Delete button sends a single rel_path, the bulk
    # toolbar sends the whole selection.
    rel_paths: list[str] = Field(..., min_length=1, max_length=1000)


class DeleteResult(BaseModel):
    deleted: list[str]
    failed: list[str]


class ZipRequest(BaseModel):
    rel_paths: list[str] = Field(..., min_length=1, max_length=500)


# ─── List ───────────────────────────────────────────────────────────
@router.get("", response_model=list[DocumentRow])
@router.get("/", response_model=list[DocumentRow])
async def list_documents(
    kind: Literal["all", "doc", "sheet", "drawing"] = Query("all"),
) -> list[DocumentRow]:
    """List files in the flat ``documents_dir`` filtered by extension for
    the requested view. Joins with ``document_sessions`` so the UI knows
    which engine (if any) holds each file's lock.

    The ``all`` view (the unified Documents page's source) lists EVERY file
    and tags each with a ``category`` telling the UI how to open it
    (office / drawing / newtab / text). ``doc`` is a narrower catch-all
    kept for compatibility — every file NOT claimed by the sheet/drawing
    views; ``sheet``/``drawing`` stay exact-extension filters."""
    base = _documents_dir()
    exts = _KIND_EXTS.get(kind)
    rows: list[DocumentRow] = []

    # Current locks, keyed by rel_path.
    async with session_scope() as s:
        locks = {
            r[0]: r[1]
            for r in (
                await s.execute(
                    text("SELECT rel_path, engine FROM document_sessions")
                )
            ).all()
        }

    if not base.exists():
        return rows

    # Flat listing — top-level files only (storage is a single flat dir).
    for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if kind == "all":
            pass  # unified view — every file appears, routed by category.
        elif kind == "doc":
            # Catch-all: everything except sheet/drawing-claimed types.
            if ext in _NON_DOC_EXTS:
                continue
        elif ext not in exts:
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        rel = entry.name
        rows.append(
            DocumentRow(
                rel_path=rel,
                name=entry.stem,
                ext=ext,
                size=st.st_size,
                modified_at=st.st_mtime,
                locked_by=locks.get(rel),
                category=_doc_category(ext) if kind in ("all", "doc") else "office",
            )
        )
    return rows


# ─── Create a blank file ────────────────────────────────────────────
def _blank_docx() -> bytes:
    """Minimal valid empty .docx (OOXML) so 'New document' opens in an
    office engine. Just the three parts a Word doc needs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p/></w:body></w:document>",
        )
    return buf.getvalue()


def _blank_xlsx() -> bytes:
    """Minimal valid empty .xlsx (OOXML) with a single blank sheet."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData/></worksheet>",
        )
    return buf.getvalue()


def _blank_odg() -> bytes:
    """Minimal valid empty .odg (ODF Drawing) so 'New Collabora Drawing' opens
    in Collabora Draw. ODF requires the ``mimetype`` part first and STORED
    (uncompressed); the rest is a one-page empty drawing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        mimetype = zipfile.ZipInfo("mimetype")
        mimetype.compress_type = zipfile.ZIP_STORED
        z.writestr(mimetype, "application/vnd.oasis.opendocument.graphics")
        z.writestr(
            "META-INF/manifest.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<manifest:manifest '
            'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
            'manifest:version="1.2">'
            '<manifest:file-entry manifest:full-path="/" '
            'manifest:media-type="application/vnd.oasis.opendocument.graphics"/>'
            '<manifest:file-entry manifest:full-path="content.xml" '
            'manifest:media-type="text/xml"/>'
            "</manifest:manifest>",
        )
        z.writestr(
            "content.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-content '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
            'office:version="1.2"><office:body><office:drawing>'
            '<draw:page draw:name="page1"/>'
            "</office:drawing></office:body></office:document-content>",
        )
    return buf.getvalue()


def _blank_content(ext: str) -> bytes:
    if ext == ".docx":
        return _blank_docx()
    if ext == ".xlsx":
        return _blank_xlsx()
    if ext == ".odg":
        return _blank_odg()
    if ext == ".excalidraw":
        return (
            '{"type":"excalidraw","version":2,"source":"domovoi",'
            '"elements":[],"appState":{},"files":{}}'
        ).encode("utf-8")
    if ext == ".svg":
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"></svg>'
        ).encode("utf-8")
    # .txt / .csv / .md — empty file is valid.
    return b""


# Default extension a "New <kind>" gets.
_KIND_NEW_EXT = {
    "doc": ".docx",
    "sheet": ".xlsx",
    "drawing": ".excalidraw",
    "collabora_drawing": ".odg",
}


@router.post("/create", response_model=DocumentRow)
async def create_document(req: CreateRequest) -> DocumentRow:
    """Create a new blank file for a view. Name is a bare filename (any
    directory component is stripped).

    For doc/sheet/drawing the kind's default extension is appended when the
    user didn't type one recognized for that kind. For the ``text`` kind
    the name is used VERBATIM — no extension is forced, so a user can make
    a bare ``notes`` or ``script.sh`` or anything else."""
    raw = os.path.basename(req.name.replace("\\", "/")).strip()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file name")
    ext = Path(raw).suffix.lower()
    if req.kind != "text" and ext not in _KIND_EXTS[req.kind]:
        ext = _KIND_NEW_EXT[req.kind]
        raw = f"{raw}{ext}"
    target = _safe_target(raw)
    if target.exists():
        raise HTTPException(status_code=409, detail=f"{raw} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_blank_content(ext))
    st = target.stat()
    return DocumentRow(
        rel_path=target.name,
        name=target.stem,
        ext=ext,
        size=st.st_size,
        modified_at=st.st_mtime,
        locked_by=None,
        category=_doc_category(ext),
    )


# ─── Delete (one or many) ───────────────────────────────────────────
@router.post("/delete", response_model=DeleteResult)
async def delete_documents(req: DeleteRequest) -> DeleteResult:
    """Delete one or more files from ``documents_dir``. Each path is
    containment-checked; any editor lock on a deleted file is released so a
    stale ``document_sessions`` row can't wedge a later re-create of the same
    name. Best-effort per file — a bad/missing path lands in ``failed`` rather
    than aborting the whole batch."""
    deleted: list[str] = []
    failed: list[str] = []
    for rp in req.rel_paths:
        try:
            target = _safe_target(rp)
        except HTTPException:
            failed.append(rp)
            continue
        try:
            if target.exists() and target.is_file():
                target.unlink()
                deleted.append(_rel_of(target))
            else:
                failed.append(rp)
        except OSError:
            failed.append(rp)
    if deleted:
        async with session_scope() as s:
            await s.execute(
                text("DELETE FROM document_sessions WHERE rel_path = ANY(:rels)"),
                {"rels": deleted},
            )
    return DeleteResult(deleted=deleted, failed=failed)


# ─── Bulk download (zip) ────────────────────────────────────────────
@router.post("/download-zip")
async def download_zip(req: ZipRequest) -> Response:
    """Zip the requested files and return the archive. The dashboard uses this
    for multi-select downloads (a single file downloads directly via /raw).
    Built in memory — fine for a LAN dashboard's file counts — with an
    explicit Content-Length so the browser can show real download progress.
    Every path is containment-checked; missing ones are skipped."""
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rp in req.rel_paths:
            try:
                target = _safe_target(rp)
            except HTTPException:
                continue
            if target.exists() and target.is_file():
                z.write(target, arcname=target.name)
                added += 1
    if not added:
        raise HTTPException(
            status_code=404, detail="none of the requested files exist"
        )
    data = buf.getvalue()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="documents.zip"',
            "Content-Length": str(len(data)),
        },
    )


# ─── Serve bytes to a document server (container-facing) ────────────
@router.get("/file/{rel_path:path}")
async def serve_file(rel_path: str, token: str = Query(...)) -> FileResponse:
    """Serve a file's bytes to a document-server container. Guarded by a
    signed ``token`` (minted in /open) so this isn't an open read
    primitive across the LAN — the token names the exact rel_path it may
    read and which engine it was signed for."""
    target = _safe_target(rel_path)
    # Token may be signed by either engine's secret; try both. The claim
    # must name this exact rel_path.
    claims = _verify_read_token(token, expected_rel=_rel_of(target))
    if claims is None:
        raise HTTPException(status_code=403, detail="invalid or mismatched file token")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"{rel_path} not found")
    return FileResponse(str(target), filename=target.name)


def _verify_read_token(token: str, *, expected_rel: str) -> Optional[dict[str, Any]]:
    for engine in _VALID_ENGINES:
        secret = (
            core_settings.onlyoffice_jwt_secret
            if engine == "onlyoffice"
            else core_settings.collabora_jwt_secret
        )
        if not secret:
            continue
        try:
            claims = jwt.decode(token, secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            continue
        if claims.get("scope") == "read" and claims.get("rel") == expected_rel:
            return claims
    return None


# ─── In-app text editor (.txt / .md / unrecognized) ─────────────────
@router.get("/text/{rel_path:path}")
async def read_text_file(rel_path: str) -> Any:
    """Read a file as UTF-8 text for the in-app editor.

    Falls back gracefully instead of choking on non-text input: a file
    over ``_TEXT_MAX_BYTES`` or that isn't valid UTF-8 returns 415 with
    ``{editable:false, reason:"too_large"|"binary"}`` so the UI can offer
    Download / Open-raw instead. On success returns the text plus the
    detected newline so the editor can preserve line endings on save."""
    target = _safe_target(rel_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"{rel_path} not found")
    st = target.stat()
    if st.st_size > _TEXT_MAX_BYTES:
        return JSONResponse(
            status_code=415,
            content={
                "editable": False,
                "reason": "too_large",
                "size": st.st_size,
                "max": _TEXT_MAX_BYTES,
            },
        )
    data = target.read_bytes()
    try:
        body = data.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse(
            status_code=415, content={"editable": False, "reason": "binary"}
        )
    newline = "\r\n" if "\r\n" in body else "\n"
    return {
        "rel_path": _rel_of(target),
        "text": body,
        "newline": newline,
        "size": st.st_size,
    }


@router.put("/text/{rel_path:path}", response_model=DocumentRow)
async def write_text_file(rel_path: str, req: TextWriteRequest) -> DocumentRow:
    """Write text back to a file as UTF-8 (RW — this dir is read-write,
    unlike the MPD ``:ro`` music mount). Containment-checked. Newlines in
    the payload are preserved verbatim (no translation)."""
    target = _safe_target(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so we don't rewrite the caller's line endings.
    target.write_text(req.text, encoding="utf-8", newline="")
    st = target.stat()
    ext = target.suffix.lower()
    return DocumentRow(
        rel_path=target.name,
        name=target.stem,
        ext=ext,
        size=st.st_size,
        modified_at=st.st_mtime,
        locked_by=None,
        category=_doc_category(ext),
    )


# ─── Upload files into documents_dir ────────────────────────────────
@router.post("/upload", response_model=UploadResult)
async def upload_documents(files: list[UploadFile] = File(...)) -> UploadResult:
    """Upload one or more files straight into ``documents_dir`` from the
    browser. Filenames are sanitized to a bare basename (defanging
    ``../`` / zip-slip traversal across both separators), then deduped so
    a second upload of the same name doesn't clobber the first. Every
    resolved target is containment-checked inside ``documents_dir``."""
    base = _documents_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"could not create documents_dir {base}: {e}"
        ) from e

    saved: list[str] = []
    skipped: list[str] = []

    for up in files:
        raw = up.filename or "upload"
        # Strip any directory component — the only traversal defense that
        # matters for an uploaded name. Both separators, then basename.
        name = os.path.basename(raw.replace("\\", "/")).strip()
        if not name or name in (".", ".."):
            skipped.append(f"{raw!r}: bad filename")
            continue
        data = await up.read()
        target = _unique_path(base, name)
        # Belt-and-suspenders containment: the dedupe target must still
        # resolve inside documents_dir.
        try:
            target.resolve(strict=False).relative_to(base)
        except ValueError:
            skipped.append(f"{name}: refused (escapes documents_dir)")
            continue
        try:
            target.write_bytes(data)
        except OSError as e:
            skipped.append(f"{name}: write failed ({e})")
            continue
        saved.append(target.name)

    if not saved:
        raise HTTPException(
            status_code=400,
            detail="no files saved" + (f"; {skipped}" if skipped else ""),
        )
    return UploadResult(saved=saved, skipped=skipped)


# ─── Browser-facing raw serve (PDFs / images / open-raw fallback) ───
@router.get("/raw/{rel_path:path}")
async def serve_raw(rel_path: str) -> FileResponse:
    """Serve a file's bytes to the BROWSER (so a plain
    ``<a target=_blank>`` / ``window.open`` works) with the right
    ``Content-Type`` and an inline ``Content-Disposition``.

    Unlike the token-guarded ``/file`` serve (which feeds the office
    *container*), this is an unauthenticated LAN-local read — consistent
    with the dashboard's other browser-facing file serves (e.g. music
    audio). Its read scope is bounded entirely by the containment check:
    it can only serve files that resolve inside ``documents_dir``."""
    target = _safe_target(rel_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"{rel_path} not found")
    mime, _ = mimetypes.guess_type(target.name)
    return FileResponse(
        str(target),
        media_type=mime or "application/octet-stream",
        filename=target.name,
        content_disposition_type="inline",
    )


# ─── Open (acquire lock, return iframe config) ──────────────────────
@router.post("/open")
async def open_document(req: OpenRequest) -> dict[str, Any]:
    """Acquire the per-file engine lock and return the iframe config the
    frontend needs to embed the editor.

    The ``document_sessions UNIQUE(rel_path)`` constraint makes a second
    engine's open on a locked file fail fast (409). Re-opening in the SAME
    engine that already holds the lock is idempotent (reuses the row).
    """
    engine = req.engine
    if engine not in _VALID_ENGINES:
        raise HTTPException(status_code=400, detail=f"unknown engine {engine!r}")
    if not _engine_enabled(engine):
        raise HTTPException(status_code=409, detail=f"{engine} is disabled")

    target = _safe_target(req.rel_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"{req.rel_path} not found")
    rel = _rel_of(target)
    ext = target.suffix.lower()

    # Acquire (or reuse) the lock.
    async with session_scope() as s:
        held_by = await _current_lock(s, rel)
        if held_by is not None and held_by != engine:
            raise HTTPException(
                status_code=409,
                detail=f"{rel} is being edited in {held_by}; close it there first.",
            )
        if held_by == engine:
            # Idempotent re-open: reuse the existing key, bump heartbeat.
            row = (
                await s.execute(
                    text(
                        "UPDATE document_sessions SET last_seen_at = now() "
                        "WHERE rel_path = :p RETURNING editor_key"
                    ),
                    {"p": rel},
                )
            ).first()
            editor_key = row[0] if row and row[0] else uuid.uuid4().hex
        else:
            editor_key = uuid.uuid4().hex
            await s.execute(
                text(
                    "INSERT INTO document_sessions (rel_path, engine, editor_key) "
                    "VALUES (:p, :e, :k)"
                ),
                {"p": rel, "e": engine, "k": editor_key},
            )

    if engine == "onlyoffice":
        return _onlyoffice_config(rel, target, ext, editor_key)
    return await _collabora_config(rel, target, editor_key)


def _read_token(rel: str, engine: str, ttl_sec: int = 3600) -> str:
    return _sign(
        {"scope": "read", "rel": rel, "exp": int(time.time()) + ttl_sec}, engine
    )


def _onlyoffice_config(
    rel: str, target: Path, ext: str, editor_key: str
) -> dict[str, Any]:
    doc_type = _ONLYOFFICE_DOCTYPE.get(ext, "word")
    file_type = ext.lstrip(".")
    base = core_settings.office_callback_base.rstrip("/")
    read_tok = _read_token(rel, "onlyoffice")
    cb_tok = _sign(
        {"scope": "callback", "rel": rel, "key": editor_key}, "onlyoffice"
    )
    config: dict[str, Any] = {
        "document": {
            "fileType": file_type,
            "key": editor_key,
            "title": target.name,
            "url": f"{base}/api/documents/file/{quote(rel)}?token={read_tok}",
            "permissions": {"edit": True, "download": True},
        },
        "documentType": doc_type,
        "editorConfig": {
            "mode": "edit",
            "lang": "en",
            "callbackUrl": (
                f"{base}/api/documents/callback/onlyoffice"
                f"?key={editor_key}&token={cb_tok}"
            ),
        },
    }
    # OnlyOffice validates the whole config as a JWT (JWT_IN_BODY).
    config["token"] = _sign(config, "onlyoffice")
    return {
        "engine": "onlyoffice",
        "rel_path": rel,
        "editor_key": editor_key,
        # Browser loads DocsAPI from here, then `new DocsAPI.DocEditor`.
        "script_url": (
            core_settings.onlyoffice_base_url.rstrip("/")
            + "/web-apps/apps/api/documents/api.js"
        ),
        "config": config,
    }


async def _collabora_config(rel: str, target: Path, editor_key: str) -> dict[str, Any]:
    """Build the Collabora WOPI handshake. We discover the ``urlsrc`` for
    the file's extension from the container's ``/hosting/discovery``, then
    hand the frontend a form-POST target (``action_url``) plus a signed
    ``access_token`` Collabora will echo back to our WOPI endpoints."""
    ext = target.suffix.lower()
    urlsrc = await _collabora_urlsrc(ext)
    base = core_settings.office_callback_base.rstrip("/")
    # WOPI file id = editor_key; our WOPI endpoints resolve it back to
    # rel_path via document_sessions.
    wopi_src = f"{base}/api/documents/wopi/files/{editor_key}"
    action_url = f"{urlsrc}WOPISrc={quote(wopi_src, safe='')}"
    access_token = _sign(
        {
            "scope": "wopi",
            "rel": rel,
            "key": editor_key,
            "exp": int(time.time()) + 36000,
        },
        "collabora",
    )
    return {
        "engine": "collabora",
        "rel_path": rel,
        "editor_key": editor_key,
        # Frontend POSTs a hidden form to this URL, target=<iframe>, with
        # access_token as a hidden field.
        "action_url": action_url,
        "access_token": access_token,
    }


async def _collabora_urlsrc(ext: str) -> str:
    """Fetch the Collabora ``urlsrc`` for an extension from
    ``/hosting/discovery`` (an XML mime→urlsrc map, version-hashed so it
    can't be hardcoded). Falls back to the well-known cool.html path if
    discovery is unreachable."""
    disco_url = core_settings.collabora_base_url.rstrip("/") + "/hosting/discovery"
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            r = await client.get(disco_url)
            r.raise_for_status()
        import xml.etree.ElementTree as ET

        root = ET.fromstring(r.text)
        # Prefer an exact extension match; else the writer/calc default.
        want = ext.lstrip(".")
        fallback = None
        for app in root.iter("app"):
            for action in app.findall("action"):
                src = action.get("urlsrc")
                if not src:
                    continue
                fallback = fallback or src
                if action.get("ext") == want:
                    return src
        if fallback:
            return fallback
    except Exception as e:  # noqa: BLE001 — discovery is best-effort
        log.warning("collabora discovery failed (%s); using default urlsrc", e)
    return core_settings.collabora_base_url.rstrip("/") + "/browser/dist/cool.html?"


# ─── OnlyOffice save callback ───────────────────────────────────────
@router.post("/callback/{engine}")
async def editor_callback(engine: str, request: Request) -> dict[str, int]:
    """OnlyOffice status callback. The body carries a ``status`` and, when
    the doc is ready to persist, a ``url`` to download the edited bytes.
    JWT (``token`` in the body, JWT_IN_BODY) is verified. On save we
    download and write the file back; on the editing session ending we
    release the lock.

    Collabora never hits this path (it saves via WOPI PutFile); if it's
    routed here we just no-op successfully.

    Must return ``{"error": 0}`` — anything else makes OnlyOffice retry.
    """
    if engine == "collabora":
        return {"error": 0}
    if engine != "onlyoffice":
        raise HTTPException(status_code=400, detail=f"unknown engine {engine!r}")

    body = await request.json()
    # Verify the JWT the document server signs the callback body with.
    tok = body.get("token")
    if tok:
        _verify(tok, "onlyoffice")
    else:
        # Also accept an Authorization: Bearer header.
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            _verify(auth[len("Bearer "):], "onlyoffice")
        else:
            raise HTTPException(status_code=403, detail="missing onlyoffice token")

    status = int(body.get("status", 0))
    editor_key = body.get("key") or request.query_params.get("key")

    # Resolve rel_path. Prefer the signed `rel` claim in the callback token WE
    # minted into the callbackUrl query string — it SURVIVES the lock being
    # released. This matters because the frontend's Close releases the lock
    # immediately, while OnlyOffice posts its final save callback (status 2) a
    # few seconds LATER: by then the document_sessions row is gone, so a
    # DB-only lookup returns None and the edited bytes get silently dropped
    # (the "edited, closed, content gone" bug). Fall back to the lock row for
    # any callback that arrives without our token.
    rel: Optional[str] = None
    cb_token = request.query_params.get("token")
    if cb_token:
        try:
            claims = _verify(cb_token, "onlyoffice")
            if claims.get("scope") == "callback":
                rel = claims.get("rel")
        except HTTPException:
            rel = None
    if rel is None and editor_key:
        async with session_scope() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT rel_path FROM document_sessions "
                        "WHERE editor_key = :k"
                    ),
                    {"k": editor_key},
                )
            ).first()
            rel = row[0] if row else None

    # status: 1 editing, 2 ready-to-save, 3 save error, 4 closed no-change,
    #         6 force-save, 7 force-save error.
    if status in (2, 6):
        url = body.get("url")
        if rel and url:
            await _download_and_write(url, rel)
        else:
            # A save that can't be persisted must be loud — this is where
            # edits would otherwise vanish without a trace.
            log.warning(
                "onlyoffice save callback status=%s could NOT persist "
                "(rel=%r, url_present=%s, key=%r) — edited bytes dropped",
                status, rel, bool(url), editor_key,
            )
    if status in (2, 3, 4):
        # Editing session ended — release the lock.
        if rel:
            async with session_scope() as s:
                await s.execute(
                    text("DELETE FROM document_sessions WHERE rel_path = :p"),
                    {"p": rel},
                )
    else:
        # Still editing / force-save mid-session — heartbeat the lock.
        if rel:
            async with session_scope() as s:
                await s.execute(
                    text(
                        "UPDATE document_sessions SET last_seen_at = now() "
                        "WHERE rel_path = :p"
                    ),
                    {"p": rel},
                )
    return {"error": 0}


async def _download_and_write(url: str, rel: str) -> None:
    """Download edited bytes from the document server's temp URL and write
    them into ``documents_dir`` (containment-checked)."""
    target = _safe_target(rel)
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            r = await client.get(url)
            r.raise_for_status()
        target.write_bytes(r.content)
        log.info("office save: wrote %d bytes to %s", len(r.content), rel)
    except Exception as e:  # noqa: BLE001
        log.error("office save failed for %s: %s", rel, e)
        raise HTTPException(status_code=502, detail=f"couldn't fetch edited file: {e}")


# ─── Close (explicit lock release) ──────────────────────────────────
@router.post("/close")
async def close_document(req: CloseRequest) -> dict[str, bool]:
    """Release a file's lock. Idempotent — closing an unlocked file is a
    no-op success (the sweeper or a prior callback may have cleared it)."""
    target = _safe_target(req.rel_path)
    rel = _rel_of(target)
    async with session_scope() as s:
        await s.execute(
            text("DELETE FROM document_sessions WHERE rel_path = :p"), {"p": rel}
        )
    return {"released": True}


# ─── Collabora WOPI endpoints ───────────────────────────────────────
# Collabora calls these back on us. file_id == editor_key; the signed
# access_token (query param) carries the rel_path claim we trust.
def _wopi_rel(file_id: str, access_token: str) -> str:
    claims = _verify(access_token, "collabora")
    if claims.get("scope") != "wopi" or claims.get("key") != file_id:
        raise HTTPException(status_code=403, detail="WOPI token/file mismatch")
    return claims["rel"]


@router.get("/wopi/files/{file_id}")
async def wopi_check_file_info(
    file_id: str, access_token: str = Query(...)
) -> dict[str, Any]:
    """WOPI CheckFileInfo — Collabora fetches file metadata + permissions
    before loading. UserCanWrite=True: the office save path is RW (unlike
    the MPD :ro music mount)."""
    rel = _wopi_rel(file_id, access_token)
    target = _safe_target(rel)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"{rel} not found")
    st = target.stat()
    return {
        "BaseFileName": target.name,
        "Size": st.st_size,
        "OwnerId": "domovoi",
        "UserId": "domovoi-web",
        "UserFriendlyName": "Domovoi",
        "UserCanWrite": True,
        "UserCanNotWriteRelative": True,
        "SupportsUpdate": True,
        "SupportsLocks": False,
        "Version": str(int(st.st_mtime)),
        "LastModifiedTime": _iso(st.st_mtime),
    }


@router.get("/wopi/files/{file_id}/contents")
async def wopi_get_file(file_id: str, access_token: str = Query(...)) -> FileResponse:
    """WOPI GetFile — Collabora downloads the current bytes."""
    rel = _wopi_rel(file_id, access_token)
    target = _safe_target(rel)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"{rel} not found")
    return FileResponse(str(target), filename=target.name)


@router.post("/wopi/files/{file_id}/contents")
async def wopi_put_file(
    file_id: str, request: Request, access_token: str = Query(...)
) -> dict[str, Any]:
    """WOPI PutFile — Collabora saves edited bytes back. Writes into
    ``documents_dir`` (containment-checked) and heartbeats the lock."""
    rel = _wopi_rel(file_id, access_token)
    target = _safe_target(rel)
    data = await request.body()
    target.write_bytes(data)
    async with session_scope() as s:
        await s.execute(
            text(
                "UPDATE document_sessions SET last_seen_at = now() "
                "WHERE rel_path = :p"
            ),
            {"p": rel},
        )
    log.info("WOPI save: wrote %d bytes to %s", len(data), rel)
    st = target.stat()
    return {"LastModifiedTime": _iso(st.st_mtime)}


def _iso(epoch: float) -> str:
    import datetime

    return (
        datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


# ─── Excalidraw (no engine, no lock) ────────────────────────────────
@router.post("/drawings/read")
async def read_drawing(req: DrawingReadRequest) -> dict[str, str]:
    """Load an Excalidraw scene (.excalidraw JSON) or .svg for editing.
    Single-user, in-page — no lock."""
    target = _safe_target(req.rel_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"{req.rel_path} not found")
    if target.suffix.lower() not in _DRAWING_EXTS:
        raise HTTPException(status_code=400, detail="not a drawing file")
    return {
        "rel_path": _rel_of(target),
        "content": target.read_text(encoding="utf-8"),
    }


@router.post("/drawings/write")
async def write_drawing(req: DrawingWriteRequest) -> DocumentRow:
    """Save an Excalidraw scene / exported SVG into ``documents_dir``.
    Creates or overwrites. No lock (single-user)."""
    target = _safe_target(req.rel_path)
    if target.suffix.lower() not in _DRAWING_EXTS:
        raise HTTPException(
            status_code=400, detail="drawing must be .excalidraw or .svg"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    st = target.stat()
    return DocumentRow(
        rel_path=target.name,
        name=target.stem,
        ext=target.suffix.lower(),
        size=st.st_size,
        modified_at=st.st_mtime,
        locked_by=None,
    )


# ─── Engine availability (frontend gates buttons on this) ───────────
@router.get("/engines")
async def engines() -> dict[str, Any]:
    """Which engines the operator has enabled + their browser base URLs.
    The pages disable an engine's "Open in …" button when its flag is off
    (and when the other engine holds the file lock)."""
    return {
        "onlyoffice": {
            "enabled": core_settings.onlyoffice_enabled,
            "base_url": core_settings.onlyoffice_base_url,
        },
        "collabora": {
            "enabled": core_settings.collabora_enabled,
            "base_url": core_settings.collabora_base_url,
        },
    }
