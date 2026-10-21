"""Drafts in home-server chats: write, revise, edit, save and print (documents
plan, D4).

A home-server chat writes a resume, a cover letter or a letter when asked
("write a cover letter for the warehouse job at Acme"): the model writes
markdown in the kind's skeleton (:mod:`domovoi.drafts`) and the chat shows
it as a **draft card**. Follow-ups revise the latest version ("shorter",
"make it more formal"), and the reply says in one line what changed.
Every version is kept (V039: ``chat_drafts`` / ``chat_draft_versions``),
with what produced it: the instruction, "edited by you", or "first draft".
Only the version being revised goes to the model, never the others; a
version longer than one pass is revised section by section, and the reply
says so.

The send itself stays in :mod:`web.backend.api.chat` (``send_message``):
:func:`plan_turn` says whether a message is a draft turn, and the
:class:`DraftTurn` streams the model's draft and writes the version when it
is done. The routes here (under ``/api/chat``, on chat.py's router) need no
model, so they work with the household's AI features off — saving an
existing draft still works then:

* ``GET /threads/{id}/drafts``, ``GET /drafts`` (every chat's, newest
  first) and ``GET /drafts/{id}`` (every version, with what changed from
  the one before) — read tier, like the thread;
* ``POST /drafts/{id}/versions`` — a hand edit, saved as a version
  "edited by you" (device tier);
* ``POST /drafts/{id}/save`` — the version as .docx or PDF into Documents
  → "From Chat" (device tier, through the Documents write policy and the
  secret-name filter), then the ATS self-check: the saved file read back
  and compared with the draft. Only saved versions become files;
* ``GET /drafts/{id}/export`` — the file to download, nothing kept;
* ``POST /drafts/{id}/print/preview`` and ``/print/jobs`` — the version's
  PDF to the home's printer, through the printing API and its preview
  (the dashboard's Print dialog is the confirmation). "print it" typed in
  a chat with a draft answers with that card, never prints by itself.

Seams: :func:`heads_up` is where D3's honest heads-up plugs in before a
draft turn; :func:`guided_turn` is where D5's guided flows (a form's
fields) start a draft.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from domovoi.admin_auth import require_device, require_device_read
from domovoi.clients import ollama as ollama_client
from domovoi.config import settings as core_settings
from domovoi.drafts import ats, blocks, diff, docx_file, kinds, pdf_file, phrases, prompts, templates
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter()
DEVICE = [Depends(require_device)]
READ = [Depends(require_device_read)]

#: The folder in Documents saved drafts land in (plan Q5).
CHAT_FOLDER = "From Chat"
#: The longest draft the model writes in one pass, in tokens.
DRAFT_MAX_TOKENS = 1800
#: Past this many characters (about 1,600 tokens) a version is revised
#: section by section: at a 4K window the rewritten text must fit too.
ONE_PASS_CHARS = 6400
MAX_BODY_CHARS = 200_000
_MEDIA = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


async def _notify(s: Any, what: str) -> None:
    await s.execute(text("SELECT pg_notify('chat_changed', :w)"), {"w": what})


# ─── Reading ────────────────────────────────────────────────────────────
def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _draft_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k in ("created_at", "updated_at"):
        d[k] = _iso(d.get(k))
    d["format"] = kinds.default_format(d.get("kind") or "")
    d["kind_label"] = kinds.get(d.get("kind") or "").label
    return d


def _version_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["created_at"] = _iso(d.get("created_at"))
    d["saved"] = _json(d.get("saved")) or []
    d["words"] = blocks.word_count(d.get("body") or "")
    d["source"] = ("edited" if d.get("author") == "person"
                   else ("first" if not d.get("instruction") else "revision"))
    return d


async def _draft(s: Any, draft_id: int) -> dict[str, Any] | None:
    r = (await s.execute(text(
        "SELECT id, thread_id, kind, template, title, created_at, updated_at FROM chat_drafts WHERE id = :id"),
        {"id": draft_id})).mappings().first()
    return _draft_row(r) if r else None


_VERSION_COLS = ("v.version, v.body, v.instruction, v.author, v.model, v.saved, v.based_on, v.device_id, "
                 "v.person_id, v.created_at, dv.name AS device_name")
_VERSION_FROM = "chat_draft_versions v LEFT JOIN devices dv ON dv.device_id = v.device_id"


async def _versions(s: Any, draft_id: int) -> list[dict[str, Any]]:
    rows = (await s.execute(text(
        f"SELECT {_VERSION_COLS} FROM {_VERSION_FROM} WHERE v.draft_id = :id ORDER BY v.version"),
        {"id": draft_id})).mappings().all()
    return [_version_row(r) for r in rows]


async def _version(s: Any, draft_id: int, version: int | None) -> dict[str, Any] | None:
    q = (f"SELECT {_VERSION_COLS} FROM {_VERSION_FROM} "
         "WHERE v.draft_id = :id " + ("AND v.version = :v" if version else "ORDER BY v.version DESC LIMIT 1"))
    r = (await s.execute(text(q), {"id": draft_id, "v": version})).mappings().first()
    return _version_row(r) if r else None


async def latest_draft(thread_id: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The thread's newest draft and its latest version, or None."""
    async with session_scope() as s:
        r = (await s.execute(text(
            "SELECT id FROM chat_drafts WHERE thread_id = :t ORDER BY updated_at DESC, id DESC LIMIT 1"),
            {"t": thread_id})).first()
        if r is None:
            return None
        d = await _draft(s, r[0])
        v = await _version(s, r[0], None)
    if d is None or v is None:
        return None
    return d, v


def _summary(d: dict[str, Any], latest: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(d)
    if latest is not None:
        out |= {"version": latest["version"], "words": latest["words"],
                "first_lines": first_lines(latest["body"]), "source": latest["source"]}
    return out


def first_lines(markdown: str, n: int = 3) -> str:
    """The first lines a card shows."""
    lines = [ln for b in blocks.parse(markdown) for ln in b.lines]
    return "\n".join(lines[:n])


# ─── A draft turn in the chat ───────────────────────────────────────────
@dataclass
class DraftTurn:
    """How a chat message becomes a draft version (or the print card)."""
    action: str                              # create | revise | print
    thread_id: int
    kind: str
    template: str
    title: str
    request: str
    draft_id: int | None = None
    base: dict[str, Any] | None = None       # the version revised / printed
    #: The reply when no model is asked (the print card's line).
    immediate: str | None = None
    #: One or more passes, each the messages for one model call.
    passes: list[list[dict[str, str]]] = field(default_factory=list)
    #: The section split of a long version (revise in parts).
    sections: list[str] = field(default_factory=list)
    model: str = ""
    #: The device that asked (kept on the version).
    device_id: str | None = None
    #: What each pass wrote (stream).
    outputs: list[str] = field(default_factory=list)

    def card(self, version: int, words: int, change: str = "", based_on: int | None = None,
             newer: int | None = None) -> dict[str, Any]:
        return {"id": self.draft_id, "version": version, "kind": self.kind, "title": self.title,
                "template": self.template, "words": words, "action": self.action, "change": change,
                "parts": len(self.passes) if len(self.passes) > 1 else None,
                "based_on": based_on, "newer": newer}

    def immediate_card(self) -> dict[str, Any]:
        b = self.base or {}
        return self.card(int(b.get("version") or 1), int(b.get("words") or 0))

    async def stream(self, *, model: str, stats: dict[str, Any]) -> AsyncIterator[str]:
        """The model's draft as it is written: pass by pass, a blank line
        between the parts of a section-by-section revision."""
        self.model = model
        self.outputs = []
        for i, messages in enumerate(self.passes):
            if i:
                yield "\n\n"
            self.outputs.append("")
            async for delta in ollama_client.chat_stream(
                messages, model=model, stats=stats, num_predict=DRAFT_MAX_TOKENS,
            ):
                self.outputs[-1] += delta
                yield delta

    async def finish(self, content: str) -> tuple[str, dict[str, Any]]:
        """Writes the new version; the reply's one line and the row's
        ``draft``. Raises ValueError when the model wrote nothing usable."""
        if len(self.passes) > 1 and len(self.outputs) == len(self.passes):
            body = "\n\n".join(p for p in (prompts.clean(o) for o in self.outputs) if p)
        else:
            body = prompts.clean(content)
        body = blocks.xml_safe(body)[:MAX_BODY_CHARS]
        if not blocks.parse(body):
            raise ValueError("empty draft")
        based_on = int((self.base or {}).get("version") or 0) or None if self.action == "revise" else None
        newer: int | None = None
        for attempt in range(3):
            try:
                async with session_scope() as s:
                    if self.action == "create" and self.draft_id is None:
                        self.draft_id = (await s.execute(text(
                            "INSERT INTO chat_drafts (thread_id, kind, template, title) VALUES (:t, :k, :tp, :ti) "
                            "RETURNING id"),
                            {"t": self.thread_id, "k": self.kind, "tp": self.template,
                             "ti": self.title[:200]})).scalar_one()
                    else:
                        # One writer at a time per draft (review round 1: two
                        # replies, or a reply and a hand edit, landing at once).
                        if (await s.execute(text("SELECT id FROM chat_drafts WHERE id = :d FOR UPDATE"),
                                            {"d": self.draft_id})).first() is None:
                            raise ValueError("draft gone")
                    latest = int((await s.execute(text(
                        "SELECT COALESCE(MAX(version), 0) FROM chat_draft_versions WHERE draft_id = :d"),
                        {"d": self.draft_id})).scalar_one())
                    version = latest + 1
                    newer = latest if based_on is not None and latest > based_on else None
                    await s.execute(text("UPDATE chat_drafts SET updated_at = now() WHERE id = :d"),
                                    {"d": self.draft_id})
                    await s.execute(text(
                        "INSERT INTO chat_draft_versions "
                        "(draft_id, version, body, instruction, author, model, based_on, device_id) "
                        "VALUES (:d, :v, :b, :i, 'model', :m, :bo, :dev)"),
                        {"d": self.draft_id, "v": version, "b": body,
                         "i": self.request[:500] if self.action == "revise" else None, "m": self.model or None,
                         "bo": based_on, "dev": self.device_id})
                    await s.commit()
                break
            except IntegrityError:
                # The same number taken meanwhile: the next one.
                if attempt == 2:
                    raise
        words = blocks.word_count(body)
        k = kinds.get(self.kind)
        if self.action == "create":
            line = f"First draft of your {k.label.lower()}: {words} words."
            change = ""
        else:
            change = diff.change_line((self.base or {}).get("body") or "", body)
            line = f"Version {version}: {change}."
            if newer is not None:
                line = (f"Version {version}, based on version {based_on}: {change}. Version {newer} came in "
                        f"while this was written and isn't in it; both are in the version picker.")
            if len(self.passes) > 1:
                line += (f" Revised section by section ({len(self.passes)} parts), so the style can drift a "
                         "little between them.")
        return line, self.card(version, words, change, based_on=based_on, newer=newer)


def heads_up(turn: DraftTurn) -> None:
    """D3's seam: the honest heads-up before a shaky draft turn (a long
    rewrite done section by section) plugs in here. Nothing yet."""
    return None


def _today() -> str:
    try:
        from domovoi import household_time

        now = household_time.now()
    except Exception:  # noqa: BLE001 — the box's own clock then
        now = dt.datetime.now()
    return f"{now.strftime('%B')} {now.day}, {now.year}"


def split_sections(markdown: str, limit: int | None = None) -> list[str]:
    """``markdown`` cut at its ``##`` headings (or between paragraphs when
    it has none) into pieces of at most about ``limit`` characters
    (default :data:`ONE_PASS_CHARS`)."""
    limit = limit or ONE_PASS_CHARS
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    pieces: list[list[str]] = [[]]
    has_heads = any(ln.startswith("## ") for ln in lines)
    for ln in lines:
        start = ln.startswith("## ") if has_heads else (not ln.strip() and pieces[-1] and
                                                         sum(len(x) for x in pieces[-1]) > limit // 2)
        if start and pieces[-1]:
            pieces.append([])
        pieces[-1].append(ln)
    out: list[str] = []
    for p in pieces:
        chunk = "\n".join(p).strip()
        if not chunk:
            continue
        if out and len(out[-1]) + len(chunk) + 2 <= limit:
            out[-1] = out[-1] + "\n\n" + chunk
        else:
            out.append(chunk)
    return out or [markdown]


def _revise_passes(body: str, instruction: str) -> list[list[dict[str, str]]]:
    if len(body) <= ONE_PASS_CHARS:
        p = prompts.revision(body, instruction)
        return [[{"role": "system", "content": p["system"]}, {"role": "user", "content": p["user"]}]]
    pieces = split_sections(body)
    out = []
    for i, piece in enumerate(pieces):
        p = prompts.revision(piece, instruction)
        user = p["user"] + f"\n\nThis is part {i + 1} of {len(pieces)} of the document: revise only this part."
        out.append([{"role": "system", "content": p["system"]}, {"role": "user", "content": user}])
    return out


async def _after_draft(thread_id: int) -> bool:
    """The thread's last reply wrote or revised a draft (an edit-only
    instruction like "shorter" then means that draft)."""
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT draft FROM chat_messages WHERE thread_id = :t AND role = 'assistant' ORDER BY id DESC LIMIT 1"),
            {"t": thread_id})).first()
    d = _json(row[0]) if row is not None else None
    return isinstance(d, dict) and d.get("action") in ("create", "revise")


async def draft_in_thread(thread_id: int, draft_id: int) -> bool:
    async with session_scope() as s:
        return (await s.execute(text("SELECT 1 FROM chat_drafts WHERE id = :d AND thread_id = :t"),
                                {"d": draft_id, "t": thread_id})).first() is not None


async def plan_turn(thread_id: int, question: str, revise_draft: int | None = None,
                    device_id: str | None = None) -> DraftTurn | None:
    """How ``question`` in ``thread_id`` is a draft turn, or None: "print
    it" and revisions need a draft in the thread, and a revision must
    clearly mean it (:func:`domovoi.drafts.phrases.revision_request`); a
    draft request starts a new one. ``revise_draft``: the card's Revise box
    (the message is a revision of that draft, whatever it says)."""
    q = (question or "").strip()
    if not q:
        return None
    if revise_draft is not None:
        async with session_scope() as s:
            d = await _draft(s, revise_draft)
            v = await _version(s, revise_draft, None) if d else None
        if d is None or v is None or d["thread_id"] != thread_id:
            return None
        turn = DraftTurn(action="revise", thread_id=thread_id, kind=d["kind"], template=d["template"],
                         title=d["title"], request=q, draft_id=d["id"], base=v, device_id=device_id,
                         passes=_revise_passes(v["body"], q))
        heads_up(turn)
        return turn
    latest = await latest_draft(thread_id)
    if latest is not None:
        d, v = latest
        if phrases.print_request(q):
            return DraftTurn(action="print", thread_id=thread_id, kind=d["kind"], template=d["template"],
                             title=d["title"], request=q, draft_id=d["id"], base=v,
                             immediate=(f"Ready to print “{d['title']}”, version {v['version']}. "
                                        "Check the preview, then press Print."))
        if phrases.revision_request(q, after_draft=await _after_draft(thread_id)):
            turn = DraftTurn(action="revise", thread_id=thread_id, kind=d["kind"], template=d["template"],
                             title=d["title"], request=q, draft_id=d["id"], base=v, device_id=device_id,
                             passes=_revise_passes(v["body"], q))
            heads_up(turn)
            return turn
    ask = phrases.draft_request(q)
    if ask is None:
        return None
    turn = guided_turn(thread_id, ask.kind, ask.template, phrases.title_for(kinds.get(ask.kind).label, ask.topic), q)
    turn.device_id = device_id
    return turn


def guided_turn(thread_id: int, kind: str, template: str, title: str, request: str,
                facts: list[str] | None = None) -> DraftTurn:
    """A first draft of ``kind`` (D5's guided flows call this with the
    form's fields as ``facts``)."""
    p = prompts.first_draft(kind, template, request, _today(), facts)
    turn = DraftTurn(action="create", thread_id=thread_id, kind=kind, template=template, title=title,
                     request=request,
                     passes=[[{"role": "system", "content": p["system"]}, {"role": "user", "content": p["user"]}]])
    heads_up(turn)
    return turn


# ─── Routes ─────────────────────────────────────────────────────────────
@router.get("/drafts", dependencies=READ)
async def list_all_drafts(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Every chat's drafts, newest first (the Chat page lists them even with
    AI features off, so a draft can still be saved)."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT d.id, d.thread_id, d.kind, d.template, d.title, d.created_at, d.updated_at, "
            "t.title AS thread_title FROM chat_drafts d JOIN chat_threads t ON t.id = d.thread_id "
            "ORDER BY d.updated_at DESC, d.id DESC LIMIT :n"), {"n": limit})).mappings().all()
        out = []
        for r in rows:
            d = _draft_row(r)
            out.append(_summary(d, await _version(s, d["id"], None)))
    return {"drafts": out}


@router.get("/threads/{thread_id}/drafts", dependencies=READ)
async def list_thread_drafts(thread_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        if (await s.execute(text("SELECT 1 FROM chat_threads WHERE id = :id"), {"id": thread_id})).first() is None:
            raise HTTPException(status_code=404, detail="thread not found")
        rows = (await s.execute(text(
            "SELECT id, thread_id, kind, template, title, created_at, updated_at FROM chat_drafts "
            "WHERE thread_id = :t ORDER BY id"), {"t": thread_id})).mappings().all()
        out = []
        for r in rows:
            d = _draft_row(r)
            out.append(_summary(d, await _version(s, d["id"], None)))
    return {"drafts": out}


def _with_changes(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev = None
    for v in versions:
        v["display"] = diff.display_text(v["body"])
        v["changes"] = diff.diff(prev["display"], v["display"]) if prev is not None else None
        prev = v
    return versions


@router.get("/drafts/{draft_id}", dependencies=READ)
async def get_draft(draft_id: int) -> dict[str, Any]:
    """The draft and every version (oldest first): its markdown, the plain
    text the changes view shows (``display``), and ``changes`` against the
    version before (``[[op, text]]``: ``=`` kept, ``+`` added, ``-``
    removed; null for v1)."""
    async with session_scope() as s:
        d = await _draft(s, draft_id)
        if d is None:
            raise HTTPException(status_code=404, detail="draft not found")
        versions = await _versions(s, draft_id)
    tpl = [{"id": t.id, "label": t.label} for t in templates.for_kind(d["kind"])]
    return {"draft": d, "versions": _with_changes(versions), "templates": tpl, "formats": list(kinds.FORMATS)}


class EditBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_BODY_CHARS)
    #: The version the edit started from: a newer one written meanwhile is
    #: a 409, not a silent overwrite.
    base_version: int = Field(..., ge=1)
    #: The device editing (kept on the version: "edited by you" there,
    #: "edited on <device>" anywhere else).
    device_id: Optional[str] = Field(None, min_length=3, max_length=64)


def _stale(latest: int) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=409, content={
        "detail": f"version {latest} was written meanwhile", "code": "stale_version", "latest": latest})


@router.post("/drafts/{draft_id}/versions", dependencies=DEVICE,
             responses={409: {"description": '{"detail", "code": "stale_version", "latest": n}'}})
async def edit_draft(draft_id: int, body: EditBody, request: Request = None) -> Any:  # type: ignore[assignment]
    """A hand edit: saved as the next version, "edited by you" on the device
    that made it. Characters XML can't hold are taken out first. Two edits
    from the same version at once: one is saved, the other is a 409
    ``stale_version`` (the draft's row is locked while the version is
    written)."""
    text_ = blocks.xml_safe(body.text)
    if not blocks.parse(text_):
        raise HTTPException(status_code=422, detail="a draft needs some words")
    from web.backend.api.devices import valid_device_id

    device = valid_device_id(body.device_id)
    if device is None and request is not None:
        from web.backend.api.files import caller_device_id

        device = valid_device_id(caller_device_id(request))
    try:
        async with session_scope() as s:
            if (await s.execute(text("SELECT id FROM chat_drafts WHERE id = :d FOR UPDATE"),
                                {"d": draft_id})).first() is None:
                raise HTTPException(status_code=404, detail="draft not found")
            latest = await _version(s, draft_id, None)
            if latest is not None and latest["version"] != body.base_version:
                return _stale(latest["version"])
            if latest is not None and latest["body"] == text_:
                return {"version": latest, "unchanged": True}
            version = (latest["version"] if latest else 0) + 1
            await s.execute(text(
                "INSERT INTO chat_draft_versions (draft_id, version, body, instruction, author, device_id) "
                "VALUES (:d, :v, :b, NULL, 'person', :dev)"),
                {"d": draft_id, "v": version, "b": text_, "dev": device})
            await s.execute(text("UPDATE chat_drafts SET updated_at = now() WHERE id = :d"), {"d": draft_id})
            await _notify(s, "draft-edited")
            await s.commit()
            v = await _version(s, draft_id, version)
    except IntegrityError:
        async with session_scope() as s:
            latest = await _version(s, draft_id, None)
        return _stale(latest["version"] if latest else body.base_version)
    v["change"] = diff.change_line(latest["body"] if latest else "", text_)
    return {"version": v}


def render(markdown: str, template_id: str, fmt: str, title: str) -> bytes:
    tpl = templates.get(template_id)
    if fmt == "pdf":
        return pdf_file.render(markdown, tpl, title=title)
    return docx_file.render(markdown, tpl, title=title)


def file_name(title: str, version: int, fmt: str) -> str:
    stem = " ".join(_BAD_NAME.sub(" ", title or "").split()).strip(" .")[:100] or "Draft"
    return f"{stem} v{version}.{fmt}"


class SaveBody(BaseModel):
    version: Optional[int] = Field(None, ge=1)
    format: Optional[Literal["docx", "pdf"]] = None
    template: Optional[str] = Field(None, max_length=40)
    device_id: Optional[str] = Field(None, min_length=3, max_length=64)


async def _target(s: Any, draft_id: int, version: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
    d = await _draft(s, draft_id)
    if d is None:
        raise HTTPException(status_code=404, detail="draft not found")
    v = await _version(s, draft_id, version)
    if v is None:
        raise HTTPException(status_code=404, detail="version not found")
    return d, v


def _template_for(d: dict[str, Any], wanted: str | None) -> str:
    if wanted:
        if wanted not in templates.TEMPLATE_IDS or d["kind"] not in templates.get(wanted).kinds:
            raise HTTPException(status_code=422, detail=f"{wanted} isn't a template for this kind of document")
        return wanted
    return d["template"]


def _write_into_chat_folder(name: str, data: bytes) -> str:
    """Writes ``data`` as ``Documents/From Chat/<name>`` (a new name when
    taken) and returns its Documents-relative path."""
    from web.backend.api import documents as documents_api
    from web.backend.api.files_security import is_sensitive_name, unstorable_reason

    if is_sensitive_name(name) or unstorable_reason(name) is not None:
        name = "Draft" + Path(name).suffix
    base = documents_api._documents_dir()
    base.mkdir(parents=True, exist_ok=True)
    folder = base / CHAT_FOLDER
    if folder.is_symlink():
        raise HTTPException(status_code=409, detail="Documents/From Chat is a link; nothing is written through it")
    folder.mkdir(exist_ok=True)
    real = folder.resolve()
    try:
        real.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=409, detail="Documents/From Chat leads outside Documents") from None
    target = documents_api._unique_path(real, name)
    tmp = real / f".{uuid.uuid4().hex}.part"
    tmp.write_bytes(data)
    os.replace(tmp, target)
    documents_api._listing_changed()
    return target.relative_to(base).as_posix()


@router.post("/drafts/{draft_id}/save", dependencies=DEVICE)
async def save_draft(draft_id: int, body: SaveBody, request: Request) -> dict[str, Any]:
    """Saves a version (the latest by default) as .docx or PDF (the kind's
    default by default) into Documents → From Chat, then checks the file:
    ``check`` is ``{ok, line}`` — ``text readable · single column`` when
    the saved file's text reads back word for word."""
    from web.backend.api import documents as documents_api

    await documents_api._assert_files_policy(request, body.device_id)
    async with session_scope() as s:
        d, v = await _target(s, draft_id, body.version)
    fmt = body.format or kinds.default_format(d["kind"])
    tpl = _template_for(d, body.template)
    data = render(v["body"], tpl, fmt, d["title"])
    check = ats.check(data, fmt, v["body"])
    rel = _write_into_chat_folder(file_name(d["title"], v["version"], fmt), data)
    entry = {"format": fmt, "rel_path": rel, "check": check.as_json(), "template": tpl,
             "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE chat_draft_versions SET saved = COALESCE(saved, '[]'::jsonb) || CAST(:e AS JSONB) "
            "WHERE draft_id = :d AND version = :v"), {"e": json.dumps([entry]), "d": draft_id, "v": v["version"]})
        await _notify(s, "draft-saved")
        await s.commit()
    return {"rel_path": rel, "name": rel.rsplit("/", 1)[-1], "format": fmt, "version": v["version"],
            "check": check.as_json(), "library_id": "core:documents"}


@router.get("/drafts/{draft_id}/export", dependencies=READ)
async def export_draft(draft_id: int, version: Optional[int] = Query(None, ge=1),
                       format: Optional[Literal["docx", "pdf"]] = Query(None),
                       template: Optional[str] = Query(None, max_length=40)) -> Response:
    """The version as a file to download; nothing is kept on the server."""
    async with session_scope() as s:
        d, v = await _target(s, draft_id, version)
    fmt = format or kinds.default_format(d["kind"])
    data = render(v["body"], _template_for(d, template), fmt, d["title"])
    name = file_name(d["title"], v["version"], fmt)
    from urllib.parse import quote

    return Response(content=data, media_type=_MEDIA[fmt], headers={
        "Content-Disposition": f"attachment; filename=\"draft.{fmt}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class PrintDraftBody(BaseModel):
    version: Optional[int] = Field(None, ge=1)
    template: Optional[str] = Field(None, max_length=40)
    printer_id: Optional[int] = None
    options: dict[str, Any] = Field(default_factory=dict)


async def _print(core_path: str, draft_id: int, body: PrintDraftBody, request: Request):
    from web.backend.api import printers

    async with session_scope() as s:
        d, v = await _target(s, draft_id, body.version)
    data = render(v["body"], _template_for(d, body.template), "pdf", d["title"])
    return await printers.forward_print_bytes(
        core_path, request, data, file_name(d["title"], v["version"], "pdf"),
        title=f"{d['title']} v{v['version']}", printer_id=body.printer_id, options=body.options, timeout=120.0)


@router.post("/drafts/{draft_id}/print/preview", dependencies=DEVICE)
async def print_draft_preview(draft_id: int, body: PrintDraftBody, request: Request):
    """The print preview of a version's PDF (the Print dialog's)."""
    return await _print("/v1/print/preview", draft_id, body, request)


@router.post("/drafts/{draft_id}/print/jobs", dependencies=DEVICE, status_code=202)
async def print_draft(draft_id: int, body: PrintDraftBody, request: Request):
    """Prints a version's PDF on the home's printer (after its preview)."""
    return await _print("/v1/print/jobs", draft_id, body, request)
