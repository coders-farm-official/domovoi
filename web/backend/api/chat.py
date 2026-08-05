"""Text-chat web API — the dashboard's ``/api/chat`` surface.

Claude-desktop-style threads (V007: ``chat_threads`` / ``chat_messages``)
answered directly by the local Ollama over ``clients/ollama.chat_stream``.
Deliberately independent of the voice pipeline's chat mode (Letta, per-room
sessions); ``chat_threads.letta_agent_id`` is the parked bridge for backing
a thread with a stateful agent later.

Send flow (``POST /threads/{id}/messages``): persist the user row, then
stream the assistant reply as **SSE** (``text/event-stream`` — ``delta``
events per token chunk, one final ``done`` event carrying the persisted
message). The full assistant row is written when the stream ends, so an
interrupted stream loses nothing but the tail. Vision: a message carrying
image uploads is answered by ``ollama_vision_model`` instead of the QA
model; images ride base64 in the Ollama chat payload.

Uploads land in ``~/.domovoi/chat_uploads/<token><ext>`` (token = uuid hex,
server-generated — the client never names files here) and are referenced
from ``chat_messages.images`` as ``[{token, name}]``.

Thread/message reads and writes are open like the podcasts surface; thread
history contains only what the user typed here. Mutations fire
``chat_changed`` NOTIFY → the ``chat.changed`` WS event so a second open
dashboard's thread list stays fresh.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi.clients import ollama as ollama_client
from domovoi.config import settings as core_settings
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

UPLOADS_DIR = Path.home() / ".domovoi" / "chat_uploads"
_UPLOAD_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_MAX_IMAGES_PER_MESSAGE = 4
# How much history rides along to the model per turn (messages, not tokens —
# local models have modest contexts; the UI shows everything from the DB).
_HISTORY_LIMIT = 30

_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


async def _notify_chat(s: Any, what: str) -> None:
    await s.execute(text("SELECT pg_notify('chat_changed', :w)"), {"w": what})


# ─── Threads ────────────────────────────────────────────────────────────────
class ThreadCreate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)


class ThreadPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    archived: Optional[bool] = None


@router.get("/threads")
async def list_threads(archived: bool = False) -> dict[str, Any]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT t.id, t.title, t.archived, t.created_at, t.updated_at,
                           COUNT(m.id) AS message_count,
                           (SELECT LEFT(content, 120) FROM chat_messages
                             WHERE thread_id = t.id ORDER BY id DESC LIMIT 1) AS last_snippet
                      FROM chat_threads t
                      LEFT JOIN chat_messages m ON m.thread_id = t.id
                     WHERE t.archived = :arch
                     GROUP BY t.id
                     ORDER BY t.updated_at DESC
                    """
                ),
                {"arch": archived},
            )
        ).mappings().all()
    return {"threads": [dict(r) | {
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    } for r in rows]}


@router.post("/threads")
async def create_thread(body: ThreadCreate) -> dict[str, Any]:
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    INSERT INTO chat_threads (title) VALUES (:t)
                    RETURNING id, title, archived, created_at, updated_at
                    """
                ),
                {"t": body.title},
            )
        ).mappings().first()
        await _notify_chat(s, "thread-created")
        await s.commit()
    return dict(row) | {
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "message_count": 0,
        "last_snippet": None,
    }


@router.patch("/threads/{thread_id}")
async def patch_thread(thread_id: int, body: ThreadPatch) -> dict[str, Any]:
    sets, params = [], {"id": thread_id}
    if body.title is not None:
        sets.append("title = :t")
        params["t"] = body.title
    if body.archived is not None:
        sets.append("archived = :a")
        params["a"] = body.archived
    if not sets:
        raise HTTPException(status_code=400, detail="nothing to change")
    async with session_scope() as s:
        r = await s.execute(
            text(f"UPDATE chat_threads SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="thread not found")
        await _notify_chat(s, "thread-patched")
        await s.commit()
    return {"ok": True}


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: int) -> dict[str, Any]:
    """Delete a thread and its messages (CASCADE). Upload files referenced by
    the thread are removed too when no other message references them."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text("SELECT images FROM chat_messages WHERE thread_id = :id AND images IS NOT NULL"),
                {"id": thread_id},
            )
        ).scalars().all()
        r = await s.execute(text("DELETE FROM chat_threads WHERE id = :id"), {"id": thread_id})
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="thread not found")
        still_used = set(
            (
                await s.execute(
                    text(
                        """
                        SELECT jsonb_array_elements(images)->>'token'
                          FROM chat_messages WHERE images IS NOT NULL
                        """
                    )
                )
            ).scalars().all()
        )
        await _notify_chat(s, "thread-deleted")
        await s.commit()
    for images in rows:
        for img in images or []:
            token = img.get("token")
            if token and token not in still_used and _TOKEN_RE.match(token):
                for p in UPLOADS_DIR.glob(f"{token}.*"):
                    p.unlink(missing_ok=True)
    return {"deleted": thread_id}


# ─── Messages ───────────────────────────────────────────────────────────────
def _msg_dict(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["created_at"] = d["created_at"].isoformat()
    return d


@router.get("/threads/{thread_id}/messages")
async def list_messages(thread_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        exists = (
            await s.execute(text("SELECT 1 FROM chat_threads WHERE id = :id"), {"id": thread_id})
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="thread not found")
        rows = (
            await s.execute(
                text(
                    """
                    SELECT id, role, content, images, model, error, created_at
                      FROM chat_messages WHERE thread_id = :id ORDER BY id
                    """
                ),
                {"id": thread_id},
            )
        ).mappings().all()
    return {"messages": [_msg_dict(r) for r in rows]}


class SendBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=32000)
    images: list[dict[str, str]] = Field(default_factory=list, max_length=_MAX_IMAGES_PER_MESSAGE)
    model: Optional[str] = Field(None, max_length=200)


def _upload_path(token: str) -> Path | None:
    if not _TOKEN_RE.match(token or ""):
        return None
    for p in UPLOADS_DIR.glob(f"{token}.*"):
        if p.is_file():
            return p
    return None


async def _history_for_model(thread_id: int) -> list[dict[str, Any]]:
    """The last N persisted turns in Ollama chat shape, images inlined as
    base64 (vision context survives across turns within the window)."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT role, content, images FROM chat_messages
                     WHERE thread_id = :id AND error IS NULL
                     ORDER BY id DESC LIMIT :lim
                    """
                ),
                {"id": thread_id, "lim": _HISTORY_LIMIT},
            )
        ).all()
    out: list[dict[str, Any]] = []
    for role, content, images in reversed(rows):
        msg: dict[str, Any] = {"role": role, "content": content}
        b64s = []
        for img in images or []:
            p = _upload_path(img.get("token", ""))
            if p is not None:
                try:
                    b64s.append(base64.b64encode(p.read_bytes()).decode())
                except OSError:
                    continue
        if b64s:
            msg["images"] = b64s
        out.append(msg)
    return out


@router.post("/threads/{thread_id}/messages")
async def send_message(thread_id: int, body: SendBody) -> StreamingResponse:
    """Persist the user turn, stream the assistant reply as SSE, persist the
    assistant turn at stream end. Events:

      ``event: delta``  data: {"text": "..."}
      ``event: done``   data: {<the persisted assistant message row>}
      ``event: error``  data: {"detail": "..."}    (also persisted on the row)
    """
    images = [
        {"token": i.get("token", ""), "name": str(i.get("name", ""))[:200]}
        for i in body.images
        if _upload_path(i.get("token", "")) is not None
    ]
    has_images = bool(images)
    model = (body.model or "").strip() or (
        core_settings.ollama_vision_model if has_images else core_settings.ollama_model
    )

    async with session_scope() as s:
        exists = (
            await s.execute(text("SELECT 1 FROM chat_threads WHERE id = :id"), {"id": thread_id})
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="thread not found")
        await s.execute(
            text(
                """
                INSERT INTO chat_messages (thread_id, role, content, images)
                VALUES (:t, 'user', :c, CAST(:imgs AS JSONB))
                """
            ),
            {"t": thread_id, "c": body.content,
             "imgs": json.dumps(images) if images else None},
        )
        # First user message titles the thread (cheap heuristic, editable).
        await s.execute(
            text(
                """
                UPDATE chat_threads
                   SET updated_at = now(),
                       title = COALESCE(title, LEFT(:c, 60))
                 WHERE id = :t
                """
            ),
            {"t": thread_id, "c": body.content.strip().splitlines()[0]},
        )
        await _notify_chat(s, "message")
        await s.commit()

    history = await _history_for_model(thread_id)

    async def sse() -> AsyncIterator[str]:
        chunks: list[str] = []
        error: str | None = None
        try:
            async for delta in ollama_client.chat_stream(history, model=model):
                chunks.append(delta)
                yield f"event: delta\ndata: {json.dumps({'text': delta})}\n\n"
        except Exception as e:  # noqa: BLE001 — surfaced as an SSE error event
            log.warning("chat stream failed (thread %s, model %s): %s", thread_id, model, e)
            error = str(e)[:500]

        content = "".join(chunks)
        if not content and error is None:
            error = "the model returned nothing"
        async with session_scope() as s:
            row = (
                await s.execute(
                    text(
                        """
                        INSERT INTO chat_messages (thread_id, role, content, model, error)
                        VALUES (:t, 'assistant', :c, :m, :e)
                        RETURNING id, role, content, images, model, error, created_at
                        """
                    ),
                    {"t": thread_id, "c": content, "m": model, "e": error},
                )
            ).mappings().first()
            await s.execute(
                text("UPDATE chat_threads SET updated_at = now() WHERE id = :t"),
                {"t": thread_id},
            )
            await _notify_chat(s, "message")
            await s.commit()
        if error is not None:
            yield f"event: error\ndata: {json.dumps({'detail': error})}\n\n"
        yield f"event: done\ndata: {json.dumps(_msg_dict(row))}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Uploads ────────────────────────────────────────────────────────────────
@router.post("/uploads")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """Stage one image for a chat message. Returns the server-generated
    token the send call references; files are capped and extension-checked."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _UPLOAD_EXTS:
        raise HTTPException(status_code=415, detail=f"unsupported image type {ext or '(none)'}")
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="image too large (20 MB cap)")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    (UPLOADS_DIR / f"{token}{ext}").write_bytes(data)
    return {"token": token, "name": file.filename or f"image{ext}"}


@router.get("/uploads/{token}")
async def get_upload(token: str):
    """Serve a staged/persisted chat image inline (for rendering in the
    transcript). Token-addressed — the client never names paths."""
    p = _upload_path(token)
    if p is None:
        raise HTTPException(status_code=404, detail="upload not found")
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(p.suffix.lower(), "application/octet-stream")
    return FileResponse(p, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


# ─── Models for the picker ──────────────────────────────────────────────────
@router.get("/models")
async def chat_models() -> dict[str, Any]:
    """Installed Ollama models + the configured defaults, for the composer's
    model picker."""
    tags = await ollama_client.list_models()
    return {
        "installed": sorted(
            {m.get("name") or m.get("model") for m in tags if (m.get("name") or m.get("model"))}
        ),
        "default_model": core_settings.ollama_model,
        "vision_model": core_settings.ollama_vision_model,
    }
