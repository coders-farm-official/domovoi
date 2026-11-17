"""The scan reads in the database (V065): queue, look up, the text.

Shared by the core (the runner, the routes, voice) and by the document
reader in either process (:func:`text_by_sha`, through
:mod:`domovoi.doc_extract.runner`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi.db.session import session_scope

log = logging.getLogger(__name__)

#: How a page the picture model read is introduced wherever its text is
#: shown or searched: never as the page's own words.
MIGHT_SAY = "(This page was hard to read. The picture model thinks it might say:)"
#: What a page nobody could read says.
COULDNT_READ = "(Domovoi couldn't read this page.)"

COLS = ("id, scan_id, path, sha256, pages, languages, state, waiting_for_voice, attempts, summary, error, "
        "error_code, created_at, started_at, updated_at, finished_at")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


#: The hashes of files as they are now, by (path, size, times, inode): a
#: lookup that shows a read's text hashes the file again only when it
#: changed.
_SHA_CACHE: dict[tuple, str] = {}
_SHA_CACHE_MAX = 512


def current_sha(path: Path) -> str:
    st = os.stat(path)
    key = (str(path), st.st_size, st.st_mtime_ns, getattr(st, "st_ctime_ns", 0), st.st_ino)
    got = _SHA_CACHE.get(key)
    if got is None:
        got = sha256_of(path)
        if len(_SHA_CACHE) >= _SHA_CACHE_MAX:
            _SHA_CACHE.clear()
        _SHA_CACHE[key] = got
    return got


async def matches_file(row: dict[str, Any] | None) -> bool:
    """A finished read still belongs to the scan at its path: the file is
    there, through no link, and its bytes hash to the ones read (security
    audit round 6, DOC-12). A read of a file replaced since then is not
    shown for it. A read not finished yet is left to the runner, which
    hashes what it reads."""
    if row is None or row.get("state") != "done":
        return True
    from domovoi.scan_reader.runner import resolve

    path = resolve(str(row.get("path") or ""))
    if path is None:
        return False
    try:
        return await asyncio.to_thread(current_sha, path) == row.get("sha256")
    except OSError:
        return False


async def current_or_none(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """``row``, or None when it is a finished read of bytes no longer there."""
    return row if await matches_file(row) else None


def valid_id(read_id: str) -> bool:
    return len(read_id or "") == 32 and all(c in "0123456789abcdef" for c in read_id)


async def notify(read_id: str) -> None:
    async with session_scope() as s:
        await s.execute(text("SELECT pg_notify('scan_read_changed', :id)"), {"id": read_id})


async def queue(path: str, *, sha256: str | None, scan_id: str | None = None,
                languages: str = "eng") -> str:
    """A new read of ``path`` (Documents-relative); its id."""
    read_id = uuid.uuid4().hex
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO scan_reads (id, scan_id, path, sha256, languages) VALUES (:id, :scan, :path, :sha, :langs)"),
            {"id": read_id, "scan": scan_id, "path": path, "sha": sha256, "langs": languages})
        await s.execute(text("SELECT pg_notify('scan_read_changed', :id)"), {"id": read_id})
    return read_id


async def get(read_id: str) -> dict[str, Any] | None:
    if not valid_id(read_id):
        return None
    async with session_scope() as s:
        row = (await s.execute(text(f"SELECT {COLS} FROM scan_reads WHERE id = :id"),
                               {"id": read_id})).mappings().first()
    return dict(row) if row else None


async def latest_for_path(path: str) -> dict[str, Any] | None:
    """The newest read of ``path`` that didn't fail or stop, else the newest."""
    async with session_scope() as s:
        row = (await s.execute(text(
            f"SELECT {COLS} FROM scan_reads WHERE path = :p "
            f"ORDER BY (state IN ('failed', 'cancelled')), created_at DESC LIMIT 1"),
            {"p": path})).mappings().first()
    return dict(row) if row else None


async def for_scans(scan_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The newest read of each scan session in ``scan_ids``."""
    ids = [i for i in scan_ids if i]
    if not ids:
        return {}
    async with session_scope() as s:
        rows = (await s.execute(text(
            f"SELECT DISTINCT ON (scan_id) {COLS} FROM scan_reads WHERE scan_id = ANY(:ids) "
            f"ORDER BY scan_id, created_at DESC"), {"ids": ids})).mappings().all()
    return {r["scan_id"]: dict(r) for r in rows}


async def pages(read_id: str) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT page, method, confidence, words, text FROM scan_read_pages WHERE read_id = :id ORDER BY page"),
            {"id": read_id})).mappings().all()
    return [dict(r) for r in rows]


async def page_counts(read_ids: list[str]) -> dict[str, dict[str, int]]:
    """``{read_id: {"done": n, "vision": n, "none": n, "text": n}}``."""
    if not read_ids:
        return {}
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT read_id, count(*) AS done, "
            "count(*) FILTER (WHERE method = 'vision') AS vision, "
            "count(*) FILTER (WHERE method = 'none') AS none, "
            "count(*) FILTER (WHERE text <> '') AS with_text "
            "FROM scan_read_pages WHERE read_id = ANY(:ids) GROUP BY read_id"), {"ids": read_ids})).mappings().all()
    return {r["read_id"]: {"done": int(r["done"]), "vision": int(r["vision"]), "none": int(r["none"]),
                           "text": int(r["with_text"])} for r in rows}


def iso(v: Any) -> str | None:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else None


def read_json(row: dict[str, Any], counts: dict[str, int] | None = None) -> dict[str, Any]:
    """A read as the dashboard and the apps see it. ``readable`` is true
    once the read is done and at least one page has text."""
    c = counts or {}
    done = int(c.get("done") or 0)
    return {
        "id": row["id"], "path": row["path"], "state": row["state"],
        "pages": int(row.get("pages") or 0), "pages_done": done,
        "pages_guessed": int(c.get("vision") or 0), "pages_unread": int(c.get("none") or 0),
        "waiting_for_voice": bool(row.get("waiting_for_voice")),
        "readable": row["state"] == "done" and int(c.get("text") or 0) > 0,
        "reading": row["state"] in ("queued", "running"),
        "error": row.get("error"), "error_code": row.get("error_code"),
        "created_at": iso(row.get("created_at")), "finished_at": iso(row.get("finished_at")),
    }


async def read_json_for(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    counts = await page_counts([row["id"]])
    return read_json(row, counts.get(row["id"]))


def page_text(p: dict[str, Any]) -> str:
    """A page's text as it is shown and searched: the picture model's
    reading introduced as a guess, an unread page said so."""
    t = str(p.get("text") or "").strip()
    if p.get("method") == "vision" and t:
        return MIGHT_SAY + "\n" + t
    if p.get("method") == "none" or not t:
        return COULDNT_READ
    return t


def full_text(page_rows: list[dict[str, Any]]) -> str:
    many = len(page_rows) > 1
    out = []
    for p in page_rows:
        out.append((f"Page {p['page']}\n" if many else "") + page_text(p))
    return "\n\n".join(out)


async def text_by_sha(sha: str) -> dict[int, str] | None:
    """``{page: text}`` of the newest finished read of the file whose bytes
    hash to ``sha`` (the document reader's way in: a copy finds it, an
    edited file doesn't). Pages with no text are left out."""
    if not sha:
        return None
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT id FROM scan_reads WHERE sha256 = :h AND state = 'done' ORDER BY finished_at DESC NULLS LAST "
            "LIMIT 1"), {"h": sha})).first()
        if row is None:
            return None
        rows = (await s.execute(text(
            "SELECT page, method, text FROM scan_read_pages WHERE read_id = :id ORDER BY page"),
            {"id": row[0]})).mappings().all()
    out = {int(r["page"]): page_text(dict(r)) for r in rows if str(r["text"] or "").strip()}
    return out or None


async def save_summary(read_id: str, summary: dict[str, Any]) -> None:
    async with session_scope() as s:
        await s.execute(text("UPDATE scan_reads SET summary = CAST(:s AS JSONB), updated_at = now() WHERE id = :id"),
                        {"s": json.dumps(summary), "id": read_id})
