"""Away chat in the web process: a phone's request, through the home chat.

Away chat (1.5.0, ``domovoi/away/``): a paired phone away from home talks
to its house over Tor; the Friend Houses listener checks the stream and
hands the core each request, and the core passes it here
(``POST /api/away/bridge``, ``web/backend/api/away.py``). This module runs
it through **the same chat code** as the home routes
(``web/backend/api/chat.py``: ``list_threads``, ``list_messages``,
``create_thread``, ``send_message``), under the phone's own device, so the
answers can't drift: the private-chats rule (``domovoi/chat_access.py``),
the capability answers, memory, help and lookups all see the device as
they would at home, and an admin grant counts for one turn only.

How the request carries the device: the route functions are called with
the phone's :class:`~domovoi.chat_access.Viewer`, and with a request this
module builds itself, which holds the household token (the device tier, as
an app sends it) and, in its server-side state, the device the phone's
away Noise key proved (``chat_access.PROVEN_DEVICE_STATE``: a client can't
set request state). A valid ``admin_grant`` is swapped for an admin
session for that one turn (``admin_auth.exchange_phone_grant``, exactly as
the phone does at home) and that session is ended when the reply is.

Replies are driven by the house, not the connection: a send starts a
:class:`Run` that reads the reply to its end and stores it whether or not
anyone is listening. ``chat.stream`` reattaches to a run, ``chat.cancel``
stops one (the partial reply is kept), at most one runs per device, and a
``client_id`` seen in the last day answers the first send again
(``away_sends``, V087).

Nothing from a message's text or a picture is logged here.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from starlette.requests import Request

from domovoi import admin_auth, ai_features, chat_access, executors, image_safety
from domovoi.away import store as away_store
from domovoi.away import wire
from domovoi.chat_access import Viewer
from web.backend.db import session_scope

log = logging.getLogger(__name__)

#: The longest JSON an EVENT may carry (it is never split).
MAX_EVENT = wire.MAX_PLAINTEXT - wire.HEADER_SIZE
#: A RESP's JSON is kept under this (the listener's cap is 1 MiB).
MAX_RESP = wire.MAX_RESP - 4096
#: A delta longer than this many characters goes as several.
DELTA_CHARS = 6000
#: How long a stopped reply may take to end by itself before it is cut.
STOP_GRACE_SEC = 15.0
#: An admin turn's session ends with its reply, and after this at the latest.
ADMIN_TURN_MAX_SEC = 15 * 60
#: The largest picture from away that is decoded (a phone sends 1024 px).
AWAY_MAX_PIXELS = 16_000_000
#: A finished run is kept this long, for a chat.stream that comes late.
KEEP_FINISHED_SEC = 30.0
#: The question a picture sent with no words asks (the wire allows an
#: empty text with a picture; the home send needs words).
PICTURE_QUESTION = "What's in this photo? Describe it in two or three sentences."

_RUNS: dict[str, Run] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass
class Run:
    """One device's reply being written: its text so far, who is
    listening, and how it ended."""

    device_id: str
    chat_id: int
    message_id: int
    text: str = ""
    final: dict[str, Any] | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    listeners: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    admin_token: str | None = None

    @property
    def running(self) -> bool:
        return self.final is None

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self.listeners):
            q.put_nowait(event)

    def listen(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.listeners.add(q)
        return q


def running_run(device_id: str, chat_id: int | None = None) -> Run | None:
    run = _RUNS.get(device_id)
    if run is None or not run.running or (chat_id is not None and run.chat_id != chat_id):
        return None
    return run


def reset() -> None:
    """For tests: forget every run."""
    for run in _RUNS.values():
        if run.task is not None and not run.task.done():
            run.task.cancel()
    _RUNS.clear()
    _LOCKS.clear()


# ─── lines back to the core ──────────────────────────────────────────────


def line(kind: str, body: dict[str, Any], *, end: bool = False) -> str:
    return json.dumps({"k": kind, "b": body, "end": end}, separators=(",", ":"), default=str) + "\n"


def err(code: str, detail: str, **extra: Any) -> str:
    return line("err", {"code": code, "detail": detail, **extra}, end=True)


def _size(obj: Any) -> int:
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))


def away_message(row: dict[str, Any]) -> dict[str, Any]:
    """A message as the home routes give it, with one difference: its
    pictures are ``[{"kind": "picture"}]`` (they aren't downloadable away
    in v1)."""
    out = dict(row)
    images = out.get("images")
    if isinstance(images, list):
        out["images"] = [{"kind": "picture"} for _ in images]
    return out


def _fit_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """An event as one or more that each fit in an EVENT: a long delta goes
    as several deltas (the text joins up the same); a ``done``/``error``
    whose stored message is too large carries the message without its
    bulk and ``too_large: true`` (``chat.history`` has the whole row)."""
    if event.get("type") == "delta":
        text = str(event.get("text", ""))
        if len(text) <= DELTA_CHARS and _size(event) <= MAX_EVENT:
            return [event]
        return [{"type": "delta", "text": text[i:i + DELTA_CHARS]} for i in range(0, len(text), DELTA_CHARS)]
    if _size(event) <= MAX_EVENT:
        return [event]
    out = dict(event)
    message = out.get("message")
    if isinstance(message, dict):
        out["message"] = {k: message.get(k) for k in ("id", "role", "created_at", "model", "error", "mine")
                          } | {"content": None, "too_large": True}
    if _size(out) > MAX_EVENT:
        out = {k: out[k] for k in ("type", "code") if k in out} | {"too_large": True}
    return [out]


# ─── the request this module builds ──────────────────────────────────────


def build_request(device_id: str, household_token: str | None, bearer: str | None = None) -> Request:
    """A request as the phone's app would send it at home: the household
    token, the admin session when this turn has one, and (server-side
    state only) the device its away key proved."""
    headers: list[tuple[bytes, bytes]] = [(b"x-requested-with", b"domovoi-away")]
    if household_token:
        headers.append((admin_auth.DEVICE_TOKEN_HEADER.lower().encode("ascii"), household_token.encode("utf-8")))
    if bearer:
        headers.append((b"authorization", f"Bearer {bearer}".encode("ascii")))
    scope = {
        "type": "http", "method": "POST", "path": "/api/away/bridge", "raw_path": b"/api/away/bridge",
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 0), "scheme": "http", "http_version": "1.1",
        "state": {chat_access.PROVEN_DEVICE_STATE: device_id},
    }
    return Request(scope)


def _http_code(exc: HTTPException) -> tuple[str, str]:
    """A refused home route's answer as an away ERR code."""
    detail = exc.detail if isinstance(exc.detail, str) else "refused"
    code = getattr(exc, "code", None)
    if exc.status_code == 404:
        return "not_found", "that chat isn't here"
    if exc.status_code == 403:
        return "forbidden", detail
    if exc.status_code in (400, 422):
        return "bad_request", detail
    if exc.status_code == 409 and (exc.headers or {}).get(ai_features.REFUSAL_HEADER) == ai_features.REFUSAL_VALUE:
        return "ai_off", detail
    if exc.status_code == 409 and isinstance(code, str):
        return code, detail
    if exc.status_code in (502, 503, 504):
        return "unavailable", "Your house is busy. Try again shortly."
    return "internal", detail


# ─── the gate ────────────────────────────────────────────────────────────


async def gate(device_id: str, noise_hex: str) -> str | None:
    """None when this phone may be answered now, else the BYE code: away
    chat off (the setting, or the internet answer "never"), or the phone
    isn't a current one with exactly this Noise key."""
    try:
        noise = bytes.fromhex(noise_hex)
    except ValueError:
        return "revoked"
    async with session_scope() as s:
        if await away_store.house_state(s) != "on":
            return "away_off"
        if not await away_store.phone_for(s, device_id, noise):
            return "revoked"
    return None


async def handle(device_id: str, noise_hex: str, raw_request: dict[str, Any], picture_b64: str
                 ) -> AsyncIterator[str]:
    """Every line of one request's answer."""
    try:
        request = wire.parse_request(raw_request)
    except wire.BadRequest as e:
        yield err(e.code, e.detail)
        return
    why = await gate(device_id, noise_hex)
    if why is not None:
        yield line("bye", {"code": why}, end=True)
        return
    viewer = Viewer(device_id=device_id)
    async with session_scope() as s:
        household = await admin_auth.get_device_token(s)
    try:
        if request.op == "chat.list":
            yield await _list(viewer, request)
        elif request.op == "chat.history":
            yield await _history(viewer, request, household)
        elif request.op == "chat.stream":
            async for out in _stream(viewer, request):
                yield out
        elif request.op == "chat.cancel":
            yield await _cancel(viewer, request)
        else:
            async for out in _send(viewer, request, picture_b64, household):
                yield out
    except HTTPException as e:
        code, detail = _http_code(e)
        yield err(code, detail)
    except admin_auth.AuthCheckUnavailable:
        yield err("unavailable", "Your house is busy. Try again shortly.")


# ─── chat.list / chat.history ────────────────────────────────────────────


def _cursor(thread: dict[str, Any]) -> str:
    raw = json.dumps([str(thread.get("updated_at") or ""), int(thread["id"])]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _read_cursor(cursor: str) -> tuple[str, int] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        when, tid = json.loads(raw)
        if isinstance(when, str) and isinstance(tid, int) and not isinstance(tid, bool):
            return when, tid
    except (ValueError, TypeError, binascii.Error):
        pass
    raise HTTPException(status_code=400, detail="bad cursor")


async def _list(viewer: Viewer, request: wire.Request) -> str:
    from web.backend.api import chat as chat_api

    out = await chat_api.list_threads(archived=request.archived, q=None, scope="mine", viewer=viewer)
    threads = sorted(out["threads"], key=lambda t: (str(t.get("updated_at") or ""), int(t["id"])), reverse=True)
    if request.cursor is not None:
        after = _read_cursor(request.cursor)
        threads = [t for t in threads if (str(t.get("updated_at") or ""), int(t["id"])) < after]
    page = threads[:request.limit]
    more = len(threads) > len(page)
    while page and _size({"threads": page, "next": None}) > MAX_RESP:
        page = page[:-1]
        more = True
    return line("resp", {"threads": page, "next": _cursor(page[-1]) if more and page else None}, end=True)


async def _history(viewer: Viewer, request: wire.Request, household: str | None) -> str:
    from web.backend.api import chat as chat_api

    assert request.chat_id is not None
    req = build_request(viewer.device_id or "", household)
    out = await chat_api.list_messages(request.chat_id, request=req, viewer=viewer)
    messages = [m for m in out["messages"] if request.before_id is None or int(m["id"]) < request.before_id]
    page = [away_message(m) for m in messages[-request.limit:]]
    more = len(messages) > len(page)
    body = {"thread": out["thread"], "messages": page, "more": more}
    while page and _size(body) > MAX_RESP:
        page = page[1:]
        body = {"thread": out["thread"], "messages": page, "more": True}
    return line("resp", body, end=True)


async def _visible(viewer: Viewer, chat_id: int) -> None:
    """404 (``not_found``) unless the device may see this chat."""
    async with session_scope() as s:
        await chat_access.thread_for(s, chat_id, viewer, chat_access.Action.READ, lock=False)


# ─── chat.stream / chat.cancel ───────────────────────────────────────────


async def _follow(run: Run, q: asyncio.Queue) -> AsyncIterator[str]:
    """The run's events from ``q`` until its last one."""
    try:
        while True:
            event = await q.get()
            last = event.get("type") in ("done", "error") and event is run.final
            for piece in _fit_event(event):
                yield line("event", piece, end=last)
            if last:
                return
    finally:
        run.listeners.discard(q)


async def _stream(viewer: Viewer, request: wire.Request) -> AsyncIterator[str]:
    assert request.chat_id is not None and viewer.device_id is not None
    await _visible(viewer, request.chat_id)
    run = running_run(viewer.device_id, request.chat_id)
    if run is None:
        yield line("resp", {"streaming": False}, end=True)
        return
    q = run.listen()
    yield line("resp", {"streaming": True, "message_id": run.message_id})
    for piece in _fit_event({"type": "delta", "text": run.text}):
        yield line("event", piece)
    async for out in _follow(run, q):
        yield out


async def _cancel(viewer: Viewer, request: wire.Request) -> str:
    assert request.chat_id is not None and viewer.device_id is not None
    await _visible(viewer, request.chat_id)
    run = running_run(viewer.device_id, request.chat_id)
    if run is None:
        return line("resp", {"cancelled": False}, end=True)
    stop(run)
    return line("resp", {"cancelled": True}, end=True)


def stop(run: Run) -> None:
    """Ask the run to stop: the model's stream ends at its next piece and
    the partial reply is stored (chat.REPLY_STOP); one that doesn't end
    within :data:`STOP_GRACE_SEC` is cut."""
    run.stop.set()

    def cut() -> None:
        if run.running and run.task is not None and not run.task.done():
            run.task.cancel()

    try:
        asyncio.get_running_loop().call_later(STOP_GRACE_SEC, cut)
    except RuntimeError:  # pragma: no cover — no loop
        pass


# ─── chat.send ───────────────────────────────────────────────────────────


def _reencode(data: bytes, kind: str) -> bytes:
    """The picture decoded and written again as a JPEG, upright, with no
    metadata at all (EXIF, location, comments): nothing the phone sent is
    kept as it came. Only the format it said is decoded. Blocking: the
    ``pictures`` pool."""
    from PIL import ImageOps

    fmt = "JPEG" if kind == "jpeg" else "WEBP"
    with image_safety.open_image(io.BytesIO(data), (fmt,), max_pixels=AWAY_MAX_PIXELS) as im:
        if (im.format or "").upper() != fmt:
            raise image_safety.PictureRefused("not the format it said")
        upright = ImageOps.exif_transpose(im)
        rgb = upright.convert("RGB")
        # Pillow writes what ``info`` carries (a comment, an ICC profile,
        # EXIF) back out on save: nothing of the original goes with it.
        rgb.info.clear()
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=90)
    return out.getvalue()


async def _store_picture(picture_b64: str, kind: str) -> list[dict[str, str]]:
    from web.backend.api import chat as chat_api

    try:
        data = base64.b64decode(picture_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _Refused("unsupported_picture", "That picture couldn't be sent.") from exc
    if len(data) > wire.MAX_PICTURE:
        raise _Refused("too_large", "the picture is larger than 1 MiB")
    if wire.sniff(data) != kind:
        raise _Refused("unsupported_picture", "That picture couldn't be sent.")
    try:
        clean = await executors.run("pictures", _reencode, data, kind)
    except image_safety.PictureRefused as exc:
        if exc.too_large:
            raise _Refused("too_large", "That picture has too many pixels.") from exc
        raise _Refused("unsupported_picture", "That picture couldn't be sent.") from exc
    except Exception as exc:  # noqa: BLE001 — a picture Pillow chokes on
        log.info("away chat: a picture couldn't be decoded (%s)", type(exc).__name__)
        raise _Refused("unsupported_picture", "That picture couldn't be sent.") from exc
    chat_api.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    (chat_api.UPLOADS_DIR / f"{token}.jpg").write_bytes(clean)
    return [{"token": token, "name": "picture.jpg"}]


class _Refused(Exception):
    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.code, self.detail, self.extra = code, detail, extra


async def _admin_session(device_id: str, grant: str) -> str:
    """The admin session for this turn, from the phone's grant (as the
    phone's own exchange at home does), or :class:`_Refused`."""
    async with session_scope() as s:
        if not await admin_auth.has_admin_auth(s):
            raise _Refused("admin_grant_invalid", "no admin is set up on this house")
        outcome = await admin_auth.exchange_phone_grant(s, grant, device_id,
                                                        max_session_sec=ADMIN_TURN_MAX_SEC)
    if outcome == "expired":
        raise _Refused("admin_grant_expired", "the admin sign-in on this phone has ended; sign in again at home")
    if not isinstance(outcome, tuple):
        raise _Refused("admin_grant_invalid", "the admin sign-in on this phone isn't valid; sign in again at home")
    log.info("away chat: an admin grant counted for one turn (device %r)", device_id)
    return outcome[0]


async def _end_admin_session(token: str | None) -> None:
    if not token:
        return
    try:
        async with session_scope() as s:
            await admin_auth.revoke_session(s, admin_auth.token_sha256(token))
    except Exception as e:  # noqa: BLE001 — it ends with its grant anyway
        log.warning("away chat: ending a turn's admin session failed (%s)", type(e).__name__)


@dataclass
class Repeat:
    """A send whose ``client_id`` was seen in the last day."""

    chat_id: int
    message_id: int
    user: dict[str, Any]
    reply: dict[str, Any] | None


async def _turn(chat_id: int, message_id: int, viewer: Viewer, req: Request
                ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """A stored user turn and the assistant row that answered it (the
    first assistant row after it), each None when it isn't there."""
    from web.backend.api import chat as chat_api

    out = await chat_api.list_messages(chat_id, request=req, viewer=viewer)
    user: dict[str, Any] | None = None
    for m in out["messages"]:
        if user is None:
            if m.get("role") == "user" and int(m["id"]) == message_id:
                user = m
        elif m.get("role") == "assistant":
            return user, m
        elif m.get("role") == "user":
            break
    return user, None


async def _send(viewer: Viewer, request: wire.Request, picture_b64: str, household: str | None
                ) -> AsyncIterator[str]:
    """A send's setup runs in a task of its own, so a connection that drops
    while the user turn is being stored doesn't stop its reply: the reply
    then starts, and is stored, with nobody listening."""
    setup = asyncio.get_running_loop().create_task(_start_send(viewer, request, picture_b64, household))
    result = await asyncio.shield(setup)
    if isinstance(result, str):
        yield result
        return
    if isinstance(result, Repeat):
        # The same client_id again: the first send's RESP, then its reply as
        # chat.stream would give it (still being written), or as stored.
        yield line("resp", {"chat_id": result.chat_id, "message_id": result.message_id,
                            "user_message": away_message(result.user)})
        run = _RUNS.get(viewer.device_id or "")
        if run is not None and run.running and run.message_id == result.message_id:
            q = run.listen()
            for piece in _fit_event({"type": "delta", "text": run.text}):
                yield line("event", piece)
            async for out in _follow(run, q):
                yield out
            return
        if result.reply is not None:
            event = {"type": "done", "message": away_message(result.reply)}
            if result.reply.get("error"):
                stats = result.reply.get("stats") if isinstance(result.reply.get("stats"), dict) else {}
                event = {"type": "error", "code": stats.get("error_code") or "internal",
                         "detail": result.reply.get("error"), "message": away_message(result.reply)}
        else:
            event = {"type": "error", "code": "internal", "detail": "the reply to this message wasn't stored"}
        for piece in _fit_event(event):
            yield line("event", piece, end=True)
        return
    started, q, user = result
    yield line("resp", {"chat_id": started.chat_id, "message_id": started.message_id,
                        "user_message": away_message(user)})
    async for out in _follow(started, q):
        yield out


async def _start_send(viewer: Viewer, request: wire.Request, picture_b64: str, household: str | None
                      ) -> str | tuple[Run, asyncio.Queue, dict[str, Any]]:
    """The send up to its reply starting: the whole answer as a line (the
    first send's answer again, or a refusal), or the run, a listener on it
    and the stored user turn. Refusals of the home routes raise
    ``HTTPException`` (:func:`handle` words them)."""
    from web.backend.api import chat as chat_api

    device_id = viewer.device_id
    assert device_id is not None and request.client_id is not None
    lock = _LOCKS.setdefault(device_id, asyncio.Lock())
    admin_token: str | None = None
    async with lock:
        try:
            # The same client_id within a day: the first send's answer.
            async with session_scope() as s:
                earlier = await away_store.earlier_send(s, device_id, request.client_id)
            if earlier is not None:
                chat_id, message_id = earlier
                req = build_request(device_id, household)
                user, reply = await _turn(chat_id, message_id, viewer, req)
                if user is None:
                    raise HTTPException(status_code=404, detail=chat_access.THREAD_NOT_FOUND)
                return Repeat(chat_id, message_id, user, reply)
            busy = running_run(device_id)
            if busy is not None:
                raise _Refused("busy", f"chat {busy.chat_id} is still answering", chat_id=busy.chat_id)
            if request.admin_grant is not None:
                admin_token = await _admin_session(device_id, request.admin_grant)
                viewer = Viewer(device_id=device_id, admin=True)
            chat_api._require_ai()
            images: list[dict[str, str]] = []
            if request.picture is not None:
                images = await _store_picture(picture_b64, request.picture.type)
            req = build_request(device_id, household, admin_token)
            chat_id = request.chat_id
            if chat_id is None:
                created = await chat_api.create_thread(chat_api.ThreadCreate(), viewer=viewer)
                chat_id = int(created["id"])
            # The home send needs words; a picture sent with none asks what
            # the Android app asks for one (PhoneVision.DEFAULT_QUESTION).
            content = request.text if request.text.strip() or not images else PICTURE_QUESTION
            response = await chat_api.send_message(
                chat_id, chat_api.SendBody(content=content, images=images, device_id=device_id),
                req, viewer=viewer)
            # The id send_message stored the user turn under (it says so
            # in the request's state), never a look-up after the fact.
            message_id = getattr(req.state, "chat_user_message_id", None)
            if not isinstance(message_id, int):  # pragma: no cover — send_message sets it
                raise _Refused("internal", "the message was stored but can't be read back")
            user, _reply = await _turn(chat_id, message_id, viewer, req)
            if user is None:  # pragma: no cover — it was just stored
                raise _Refused("internal", "the message was stored but can't be read back")
            async with session_scope() as s:
                await away_store.record_send(s, device_id, request.client_id, chat_id, message_id)
            started = Run(device_id, chat_id, message_id, admin_token=admin_token)
            admin_token = None  # the run ends it now
            q = started.listen()
            _RUNS[device_id] = started
            started.task = asyncio.get_running_loop().create_task(
                _drive(started, response), name="away-chat-reply")
            return started, q, user
        except _Refused as e:
            await _end_admin_session(admin_token)
            return err(e.code, e.detail, **e.extra)
        except BaseException:
            await _end_admin_session(admin_token)
            raise


def _sse(buffer: str) -> tuple[list[tuple[str, Any]], str]:
    """Complete SSE events in ``buffer`` as ``(name, data)``, and the rest."""
    events: list[tuple[str, Any]] = []
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        name, data = "message", []
        for raw in block.split("\n"):
            if raw.startswith("event:"):
                name = raw[6:].strip()
            elif raw.startswith("data:"):
                data.append(raw[5:].lstrip())
        try:
            events.append((name, json.loads("\n".join(data)) if data else {}))
        except ValueError:
            continue
    return events, buffer


async def _drive(run: Run, response: Any) -> None:
    """Read the home send's reply to its end, whoever is listening: each
    SSE event as an away event (``delta``; ``done``; ``error`` then
    ``done`` as one ``error`` carrying the stored row), stored by the chat
    code as at home. A stop ends it as ``error`` ``cancelled`` with the
    partial reply."""
    from web.backend.api import chat as chat_api

    token = chat_api.REPLY_STOP.set(run.stop)
    final: dict[str, Any] | None = None
    pending_error: dict[str, Any] | None = None
    buffer = ""
    try:
        async for chunk in response.body_iterator:
            buffer += chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk)
            events, buffer = _sse(buffer)
            for name, data in events:
                if not isinstance(data, dict):
                    data = {"data": data}
                if name == "delta":
                    text = str(data.get("text", ""))
                    run.text += text
                    run.publish({"type": "delta", "text": text})
                elif name == "error":
                    pending_error = data
                elif name == "done":
                    message = away_message(data)
                    if pending_error is not None:
                        final = {"type": "error", "code": pending_error.get("code") or "internal",
                                 "detail": pending_error.get("detail") or "", "message": message}
                    else:
                        final = {"type": "done", "message": message}
                else:
                    run.publish({**data, "type": name})
        if final is None and pending_error is not None:
            final = {"type": "error", "code": pending_error.get("code") or "internal",
                     "detail": pending_error.get("detail") or ""}
        if final is None:
            final = {"type": "error", "code": "internal", "detail": "the reply ended early"}
    except asyncio.CancelledError:
        final = {"type": "error", "code": "cancelled", "detail": "the reply was stopped"}
    except Exception as e:  # noqa: BLE001 — said to the phone, logged without content
        log.warning("away chat: a reply failed (%s)", type(e).__name__)
        final = {"type": "error", "code": "internal", "detail": "the reply failed"}
    finally:
        chat_api.REPLY_STOP.reset(token)
        if final is None:
            final = {"type": "error", "code": "internal", "detail": "the reply ended early"}
        if run.stop.is_set() and final.get("type") == "done":
            final = {"type": "error", "code": "cancelled", "detail": "the reply was stopped",
                     "message": final.get("message")}
        run.final = final
        run.publish(final)
        await _end_admin_session(run.admin_token)
        run.admin_token = None
        _forget_later(run)


def _forget_later(run: Run) -> None:
    def forget() -> None:
        if _RUNS.get(run.device_id) is run:
            del _RUNS[run.device_id]

    try:
        asyncio.get_running_loop().call_later(KEEP_FINISHED_SEC, forget)
    except RuntimeError:  # pragma: no cover
        forget()
