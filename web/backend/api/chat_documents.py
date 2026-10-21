"""Documents in home-server chats: attach, read, ask (documents plan, D1).

A home-server chat takes a PDF, Word (.docx), OpenDocument (.odt), Excel
(.xlsx), CSV, text or markdown file. The routes (under ``/api/chat``, on
:mod:`web.backend.api.chat`'s router):

* ``POST /documents`` — stage a file for a message (device tier, AI
  features on). It is stored under ``~/.domovoi/chat_uploads/documents/``
  as ``<token><ext>`` (a server-made token, as pictures are), read once in
  the capped worker process (:mod:`domovoi.doc_extract.runner`) and its
  parts cached beside it as ``<token>-parts.json``. The answer is the
  document's metadata: kind, pages, words, the one-line summary, and the
  notes (scanned pages, garbled pages, formulas with no saved value, a
  long document).
* ``POST /threads/{id}/messages`` (in chat.py) carries ``documents:
  [{token}]``; a thread with documents answers from them
  (:func:`plan_reply`): find, then answer, with checked citations.
* ``GET /threads/{id}/documents``, ``GET /documents/{token}/parts`` and
  ``GET /documents/{token}/file`` — read tier, like the thread itself:
  the list, the text a citation points at (the page sheet), and the
  original (a PDF opens in a new tab at ``#page=N``).
* ``POST /threads/from-document`` — "Ask about this document" on the
  Documents page: a new chat about a file in the Documents library (read
  through the same containment and secret-name checks as the Documents
  routes), with the attachment already in it.
* ``POST /documents/extract`` — for a phone that can't read a PDF's text
  itself: the server reads it and sends the text back, and keeps nothing.
  Not a chat: no model is asked, so it works with AI features off.

Text from a file is data, never instructions: it reaches the model inside
a marked block (:func:`domovoi.doc_qa.build_prompt`) and the reply is
checked by the app (:func:`domovoi.doc_qa.check_reply`): only citations of
passages the app gave are kept, and when the passages don't answer, the
reply is "I couldn't find that in the document".
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import ai_features, doc_extract
from domovoi.admin_auth import require_device, require_device_read
from domovoi.doc_extract import runner
from domovoi.doc_qa import answer as qa
from domovoi.doc_qa import longdoc
from domovoi.doc_qa import notes as qa_notes
from web.backend.api.inline_serve import inert_headers
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter()

DOCS_DIR = Path.home() / ".domovoi" / "chat_uploads" / "documents"
MAX_DOCUMENTS_PER_MESSAGE = 4
_CHUNK = 1024 * 1024
_MEDIA = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "txt": "text/plain",
    "md": "text/markdown",
}
_META_KEYS = ("token", "name", "kind", "pages", "words", "parts", "scanned_pages", "garbled_pages",
              "summary", "notes", "size")


# ─── The household's AI switch ──────────────────────────────────────────
def ai_turned_off() -> bool:
    """The household's "AI features" switch (domovoi/ai_features.py) is off."""
    return ai_features.turned_off()


def _require_ai() -> None:
    """A document for a chat, while AI features are off: refused before
    anything is stored, as a message or a picture is (409 ai-off)."""
    if ai_turned_off():
        raise ai_features.http_exception("chat")


DEVICE = [Depends(require_device)]
READ = [Depends(require_device_read)]
SEND = [Depends(require_device), Depends(_require_ai)]


def _refused(status: int, code: str, detail: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code, **extra})


# ─── Files on disk ──────────────────────────────────────────────────────
def _valid(token: str) -> bool:
    return len(token or "") == 32 and all(c in "0123456789abcdef" for c in token)


def doc_path(token: str) -> Path | None:
    """The stored original for ``token``, or None."""
    if not _valid(token):
        return None
    for ext in doc_extract.KIND_BY_EXT:
        p = DOCS_DIR / f"{token}{ext}"
        if p.is_file():
            return p
    return None


def _parts_path(token: str) -> Path:
    return DOCS_DIR / f"{token}-parts.json"


def load_cache(token: str) -> dict[str, Any] | None:
    """``{"meta": {...}, "parts": [...]}`` for a stored document, or None."""
    if not _valid(token):
        return None
    try:
        data = json.loads(_parts_path(token).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("parts"), list):
        return None
    return data


def meta_of(token: str) -> dict[str, Any] | None:
    cache = load_cache(token)
    return dict(cache["meta"]) if cache and isinstance(cache.get("meta"), dict) else None


def remove(tokens: set[str]) -> None:
    """Delete the stored files of ``tokens`` (thread deleted, and nothing
    else refers to them)."""
    for token in tokens:
        if not _valid(token):
            continue
        for p in DOCS_DIR.glob(f"{token}*"):
            p.unlink(missing_ok=True)


def _meta(token: str, name: str, size: int, result: dict[str, Any]) -> dict[str, Any]:
    kind = result.get("kind") or ""
    pages = result.get("pages") if kind == "pdf" else None
    sheets = len(result.get("sheets") or []) or None
    return {
        "token": token,
        "name": name,
        "kind": kind,
        "pages": pages,
        "words": int(result.get("words") or 0),
        "parts": len(result.get("parts") or []),
        "scanned_pages": list(result.get("scanned_pages") or []),
        "garbled_pages": list(result.get("garbled_pages") or []),
        "summary": qa_notes.summary_line(name, kind, pages, int(result.get("words") or 0), sheets),
        "notes": list(result.get("notes") or []),
        "size": size,
    }


def _clean_name(name: str | None, kind_ext: str) -> str:
    base = Path((name or "").replace("\\", "/")).name.strip()
    base = "".join(ch for ch in base if ch >= " " and ch != "\x7f")[:200]
    return base or f"document{kind_ext}"


async def _copy_upload(file: UploadFile, dest: Path) -> int:
    """Copy the upload into ``dest`` in chunks, refusing past the byte cap."""
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > doc_extract.MAX_FILE_BYTES:
                raise _TooLarge()
            out.write(chunk)
    return size


class _TooLarge(Exception):
    pass


def _too_large() -> JSONResponse:
    mb = doc_extract.MAX_FILE_BYTES // 2 ** 20
    return _refused(413, "file_too_large", f"That file is too large for the chat: more than {mb} MB.", max_mb=mb)


def _kind_or_refusal(name: str) -> tuple[str | None, JSONResponse | None]:
    kind = doc_extract.kind_for(name)
    if kind:
        return kind, None
    advice = doc_extract.old_format_advice(name)
    ext = Path(name).suffix.lower() or "that kind of"
    if advice:
        return None, _refused(415, "old_format", f"Chat can't read {ext} files. {advice}")
    return None, _refused(415, "unsupported_format", f"Chat reads {doc_extract.ACCEPTS} files.")


async def _read_stored(token: str, path: Path, name: str, size: int) -> dict[str, Any]:
    """Read a stored file and cache its parts; its metadata. On a refusal
    the file goes and :class:`runner.ExtractFailed` is raised."""
    kind = doc_extract.kind_for(path.name) or ""
    try:
        result = await runner.extract(path, kind)
    except runner.ExtractFailed:
        path.unlink(missing_ok=True)
        raise
    meta = _meta(token, name, size, result)
    data = {"version": doc_extract.FORMAT_VERSION, "meta": meta, "parts": result["parts"]}
    part = _parts_path(token).with_suffix(f".{uuid.uuid4().hex[:8]}.part")
    try:
        part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        part.replace(_parts_path(token))
    finally:
        # A write that failed leaves no half-written copy (the sweep removes
        # one a crash left: chat_doc_jobs.sweep).
        part.unlink(missing_ok=True)
    return meta


# ─── Routes ─────────────────────────────────────────────────────────────
_REFUSALS = {
    413: {"description": 'Too large: {"detail", "code": "file_too_large", "max_mb": 100}'},
    415: {"description": 'Not a kind Chat reads: {"detail", "code": "unsupported_format" | "old_format"}'},
    422: {"description": 'Couldn\'t be read: {"detail": the reason in a sentence, "code": "unreadable"}'},
}


#: A staged document no message took is removed after this long.
UNSENT_TTL_SEC = 3600


async def sweep_unsent(now: float | None = None) -> int:
    """Remove staged documents older than :data:`UNSENT_TTL_SEC` that no
    message refers to (picked in the composer, never sent). Returns how
    many went. Run at each upload, so the folder can't fill with them."""
    if not DOCS_DIR.is_dir():
        return 0
    now = time.time() if now is None else now
    old: set[str] = set()
    for p in DOCS_DIR.glob("*-parts.json"):
        token = p.name[:32]
        try:
            if _valid(token) and now - p.stat().st_mtime > UNSENT_TTL_SEC:
                old.add(token)
        except OSError:
            continue
    if not old:
        return 0
    async with session_scope() as s:
        used = set((await s.execute(text(
            "SELECT jsonb_array_elements(documents)->>'token' FROM chat_messages WHERE documents IS NOT NULL"
        ))).scalars().all())
    gone = old - used
    remove(gone)
    return len(gone)


@router.post("/documents", dependencies=SEND, responses=_REFUSALS)
async def upload_document(file: UploadFile = File(...)) -> Any:
    """Stage one document for a chat message: stored, read in the worker,
    its parts cached. The answer is its metadata (``token`` for the send).
    Staged documents never sent go after an hour (:func:`sweep_unsent`)."""
    try:
        await sweep_unsent()
    except Exception as e:  # noqa: BLE001 — tidying never stops an upload
        log.warning("chat documents: the unsent sweep failed: %s", type(e).__name__)
    name = _clean_name(file.filename, "")
    kind, refusal = _kind_or_refusal(name)
    if refusal is not None:
        return refusal
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    ext = Path(name).suffix.lower()
    dest = DOCS_DIR / f"{token}{ext}"
    try:
        size = await _copy_upload(file, dest)
    except _TooLarge:
        dest.unlink(missing_ok=True)
        return _too_large()
    try:
        return await _read_stored(token, dest, name, size)
    except runner.ExtractFailed as e:
        return _refused(422, "unreadable", str(e))


@router.post("/documents/extract", dependencies=DEVICE, responses=_REFUSALS)
async def extract_document(file: UploadFile = File(...)) -> Any:
    """Read a document's text for a phone that can't, and keep nothing: the
    file is read in a temporary folder that is removed before the answer
    goes. No model is asked, so this works with AI features off."""
    name = _clean_name(file.filename, "")
    kind, refusal = _kind_or_refusal(name)
    if refusal is not None:
        return refusal
    work = Path(tempfile.mkdtemp(prefix="domovoi-docx-"))
    try:
        dest = work / f"input{Path(name).suffix.lower()}"
        try:
            await _copy_upload(file, dest)
        except _TooLarge:
            return _too_large()
        try:
            result = await runner.extract(dest, kind or "")
        except runner.ExtractFailed as e:
            return _refused(422, "unreadable", str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    keep = ("kind", "pages", "parts", "scanned_pages", "garbled_pages", "words", "chars", "notes",
            "sheets", "formulas_without_value")
    return {k: result.get(k) for k in keep if k in result}


@router.get("/documents/{token}/parts", dependencies=READ)
async def document_parts(token: str) -> dict[str, Any]:
    cache = load_cache(token)
    if cache is None:
        raise HTTPException(status_code=404, detail="document not found")
    parts = [{"label": p.get("label"), "page": p.get("page"), "text": p.get("text"), "head": p.get("head")}
             for p in cache["parts"]]
    return {"meta": cache.get("meta"), "parts": parts}


@router.get("/documents/{token}/file", dependencies=READ)
async def document_file(token: str):
    """The original file: a PDF inline (the dashboard opens it at
    ``#page=N``), anything else as a download. Household content, read on
    the same tier as the thread; an ``<a href>`` carries ``?device_token=``."""
    p = doc_path(token)
    if p is None:
        raise HTTPException(status_code=404, detail="document not found")
    meta = meta_of(token) or {}
    kind = doc_extract.kind_for(p.name) or ""
    media = _MEDIA.get(kind, "application/octet-stream")
    name = str(meta.get("name") or p.name)
    headers = {"Cache-Control": "private, max-age=3600", **inert_headers(media, name)}
    return FileResponse(p, media_type=media, filename=name, headers=headers,
                        content_disposition_type="inline" if kind == "pdf" else "attachment")


async def thread_documents(thread_id: int, s: Any = None) -> list[dict[str, Any]]:
    """The documents attached in a thread, in the order attached (each
    token once)."""
    q = text("SELECT documents FROM chat_messages WHERE thread_id = :id AND documents IS NOT NULL ORDER BY id")

    async def run(sess: Any) -> list[Any]:
        return list((await sess.execute(q, {"id": thread_id})).scalars().all())

    if s is None:
        async with session_scope() as sess:
            rows = await run(sess)
    else:
        rows = await run(s)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for docs in rows:
        if isinstance(docs, str):
            docs = json.loads(docs)
        for d in docs or []:
            t = d.get("token") if isinstance(d, dict) else None
            if t and t not in seen:
                seen.add(t)
                out.append(d)
    return out


@router.get("/threads/{thread_id}/documents", dependencies=READ)
async def list_thread_documents(thread_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        if (await s.execute(text("SELECT 1 FROM chat_threads WHERE id = :id"), {"id": thread_id})).first() is None:
            raise HTTPException(status_code=404, detail="thread not found")
        return {"documents": await thread_documents(thread_id, s)}


def resolve(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The metadata of the staged documents a message names (unknown tokens
    dropped, each once, at most :data:`MAX_DOCUMENTS_PER_MESSAGE`)."""
    out: list[dict[str, Any]] = []
    for item in items[:MAX_DOCUMENTS_PER_MESSAGE]:
        token = str((item or {}).get("token") or "")
        if any(d["token"] == token for d in out):
            continue
        meta = meta_of(token)
        if meta and doc_path(token) is not None:
            out.append({k: meta.get(k) for k in _META_KEYS})
    return out


def attach_reply(docs: list[dict[str, Any]]) -> str:
    """What the chat says to an attachment sent with no question (the
    phone's ``DocQa`` says the same)."""
    def one(d: dict[str, Any]) -> str:
        if d.get("kind") == "pdf" and d.get("pages"):
            n = int(d["pages"])
            return f"{d['name']} ({n} page{'' if n == 1 else 's'})"
        w = int(d.get("words") or 0)
        return f"{d['name']} ({'about ' + qa._about(w) if w >= 1000 else w} word{'' if w == 1 else 's'})"
    names = [one(d) for d in docs]
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"I've read {joined}. Ask me anything about {'it' if len(docs) == 1 else 'them'}."


class FromDocument(BaseModel):
    rel_path: str = Field(..., min_length=1, max_length=4096)


@router.post("/threads/from-document", dependencies=SEND, responses=_REFUSALS)
async def thread_from_document(body: FromDocument) -> Any:
    """"Ask about this document" on the Documents page: a new chat ("About
    lease.pdf") with the file attached, read through the Documents routes'
    own checks (inside the library, no secret-shaped names), copied into
    the chat's uploads so the chat doesn't change when the file does."""
    from web.backend.api import documents as documents_api

    target = documents_api._readable_target(body.rel_path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"{body.rel_path} not found")
    name = _clean_name(target.name, "")
    kind, refusal = _kind_or_refusal(name)
    if refusal is not None:
        return refusal
    try:
        size = target.stat().st_size
    except OSError:
        raise HTTPException(status_code=404, detail=f"{body.rel_path} not found")
    if size > doc_extract.MAX_FILE_BYTES:
        return _too_large()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    dest = DOCS_DIR / f"{token}{Path(name).suffix.lower()}"
    try:
        shutil.copyfile(target, dest)
    except OSError:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail=f"{body.rel_path} couldn't be read")
    try:
        meta = await _read_stored(token, dest, name, size)
    except runner.ExtractFailed as e:
        return _refused(422, "unreadable", str(e))
    docs = [{k: meta.get(k) for k in _META_KEYS}]
    reply = attach_reply(docs)
    doc = {"mode": "attached", "found": True, "sources": [], "notes": list(meta.get("notes") or [])}
    async with session_scope() as s:
        thread = (await s.execute(text(
            "INSERT INTO chat_threads (title) VALUES (:t) RETURNING id, title, archived, created_at, updated_at"),
            {"t": f"About {name}"[:200]})).mappings().first()
        await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content, documents) "
            "VALUES (:t, 'user', '', CAST(:d AS JSONB))"), {"t": thread["id"], "d": json.dumps(docs)})
        await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content, doc, stats) "
            "VALUES (:t, 'assistant', :c, CAST(:doc AS JSONB), CAST(:st AS JSONB))"),
            {"t": thread["id"], "c": reply, "doc": json.dumps(doc), "st": json.dumps({"model_role": "documents"})})
        await s.execute(text("SELECT pg_notify('chat_changed', 'thread-created')"))
        await s.commit()
    row = dict(thread) | {"created_at": thread["created_at"].isoformat(),
                          "updated_at": thread["updated_at"].isoformat(),
                          "message_count": 2, "last_snippet": reply[:120]}
    return {"thread": row, "document": docs[0]}


# ─── Answering from documents ───────────────────────────────────────────
@dataclass
class DocPlan:
    """How a message in a thread with documents is answered."""
    mode: str
    #: The reply when no model is asked ("I couldn't find that ...", an
    #: attachment with no question).
    immediate: str | None = None
    #: The user turn for the model (passages and question), when one is.
    user: str = ""
    rules: str = ""
    question: str = ""
    names: list[str] = field(default_factory=list)
    docs: list[dict[str, Any]] = field(default_factory=list)
    given: list[qa.Given] = field(default_factory=list)
    notes: list[dict[str, str]] = field(default_factory=list)
    found: bool = True
    #: A summary of documents longer than one pass: read in parts by a job
    #: (chat_doc_jobs.py, D2), not one model call.
    long: bool = False

    def doc_json(self, checked: qa.Checked | None = None) -> dict[str, Any]:
        sources = []
        if checked is not None:
            for s in checked.sources:
                d = self.docs[s["doc"]]
                parts = d.get("_parts") or []
                part = parts[s["part"]] if 0 <= s["part"] < len(parts) else {}
                sources.append({"token": d.get("token"), "name": d.get("name"), "label": s["label"],
                                "page": part.get("page"), "text": s["text"], "match": s["match"],
                                "cited": s["cited"]})
        return {"mode": self.mode, "found": checked.found if checked is not None else self.found,
                "sources": sources, "notes": (checked.notes if checked is not None else []) + self.notes}

    def finish(self, content: str) -> tuple[str, dict[str, Any]]:
        """The model's reply after the app's check, and the row's ``doc``."""
        checked = qa.check_reply(content, self.given, self.question, self.names)
        return checked.content, self.doc_json(checked)


async def previous_question(thread_id: int, before_id: int | None = None) -> str | None:
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT content FROM chat_messages WHERE thread_id = :t AND role = 'user' AND content <> '' "
            "AND id < COALESCE(CAST(:b AS BIGINT), 9223372036854775807) ORDER BY id DESC LIMIT 1"),
            {"t": thread_id, "b": before_id})).first()
    return row[0] if row else None


async def plan_reply(thread_id: int, question: str, new_docs: list[dict[str, Any]],
                     previous: str | None) -> DocPlan | None:
    """How to answer ``question`` in a thread, or None when the thread has
    no documents (an ordinary chat turn). ``new_docs``: the documents sent
    with this message (already in the thread's list)."""
    docs = await thread_documents(thread_id)
    if not docs:
        return None
    # Reading the cached parts and searching them is CPU work proportional
    # to the documents (review round 1: 6.6 s for a 40 MB text file, with
    # the event loop blocked), so it runs in a worker thread.
    return await asyncio.to_thread(_plan, docs, question, new_docs, previous)


def _plan(docs: list[dict[str, Any]], question: str, new_docs: list[dict[str, Any]],
          previous: str | None) -> DocPlan:
    loaded = []
    for d in docs:
        cache = load_cache(str(d.get("token") or ""))
        if cache is None:
            continue
        loaded.append({**d, "parts": cache["parts"], "_parts": cache["parts"]})
    names = [str(d.get("name") or "the document") for d in loaded]
    q = (question or "").strip()
    if not q:
        return DocPlan(mode="attached", immediate=attach_reply(new_docs or docs), names=names, docs=loaded)
    if not loaded:
        return DocPlan(mode="question", immediate=qa.NOT_FOUND, names=names, docs=loaded, found=False,
                       notes=[{"code": "missing", "text": "The attached document is no longer on this Domovoi."}])
    mode = qa.choose_mode(q)
    longdoc.heads_up(mode, loaded, q)       # D3: the heads-up card plugs in here
    if mode == "summary":
        given = qa.retrieve(loaded, q, mode="summary")
        if not given:
            # Longer than one pass: read in parts, then combined (D2).
            return DocPlan(mode="summary", question=q, names=names, docs=loaded, long=True)
    else:
        given = qa.retrieve(loaded, q, previous)
        if not given:
            return DocPlan(mode="question", immediate=qa.NOT_FOUND, names=names, docs=loaded, found=False,
                           notes=[{"code": "not_found", "text": qa.note_not_found(names)}])
    prompt = qa.build_prompt(q, given, names, mode=mode, previous=previous if mode == "question" else None)
    return DocPlan(mode=mode, user=prompt["user"], rules=prompt["rules"], question=q, names=names,
                   docs=loaded, given=given)
