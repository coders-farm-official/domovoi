"""Who may see, read, write, share and delete a home-server chat: the ONE
place the rule lives (design-notes/PRIVATE-CHATS.md §2.3), used by both
processes (the web's ``/api/chat*`` and ``/api/help/deliveries``, the
core's ``/v1/phone/voice``).

Every read and write of a chat goes through here:

* a route about one thread calls :func:`thread_for` (404 when the caller
  can't see it, 403 when it can but may not do this) instead of a bare
  ``SELECT 1 FROM chat_threads``;
* a list (threads, search, every draft) puts :func:`visible_sql` in its
  ``WHERE``;
* a route about a child (a draft, a document job, a video job, an upload
  token) resolves the child's thread and calls :func:`thread_for`.

``domovoi/tests/test_chat_access_enumeration.py`` fails when a SQL
statement on a chat table, or a chat route, gets past this module without
a reason on its allow-list.

**Viewers.** :class:`Viewer` is resolved from the request alone, never
from an id the caller names:

* the device, from its **device key** (V086: ``devices.device_key_hash``):
  the ``X-Domovoi-Device-Key`` header (the apps) or the HttpOnly
  ``domovoi-device-key`` cookie (browsers). ``X-Device-Id``, the
  ``domovoi-device-id`` cookie, a body ``device_id`` and ``phone_id`` are
  all self-asserted and are ignored here. A wrong or missing key is a
  **keyless** household caller. Away chat (1.5.0) is the one other way
  in: a request the web's away bridge builds itself carries, in its
  server-side state, the device the phone's away Noise key proved
  (:data:`PROVEN_DEVICE_STATE`, :func:`request_device`); a client can't
  set request state;
* **admin**: a live admin Bearer (``check_admin_request == "ok"``, a
  phone's day grant included), or the admin cookie on a GET. Never the
  pre-setup grace: after ``--reset-admin`` anyone on the LAN would
  otherwise read every private chat.

**Threads.** A chat is **private** (an owning device, not shared),
**shared** (with the household, with or without an owner) or
**admin-only** (no owner, not shared: chats nobody could be matched to at
the migration). Before V086 there are no such columns and every thread is
**household**: everyone with the household token may do everything, as
in 1.4.x.

The table (§2.3), for a database with V086:

=====================  ===============  =================  ==============  ===========
viewer / chat          private, mine    private, another   shared          admin-only
=====================  ===============  =================  ==============  ===========
owning device          all              404                all             404
other device, keyless  404              404                read, write     404
shared screen          (never owns)     404                read, write     404
admin                  as owner         read share delete  all             read share delete
=====================  ===============  =================  ==============  ===========

A device marked a shared screen owns nothing while it is one: its earlier
private chats read as admin-only until it is unmarked (no row is
rewritten).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import HTTPException, Request, params
from sqlalchemy import text

log = logging.getLogger(__name__)

#: The tables whose rows are a chat's content (or point at one). The
#: enumeration test scans for SQL naming any of these.
CHAT_TABLES = (
    "chat_threads",
    "chat_messages",
    "chat_drafts",
    "chat_draft_versions",
    "chat_doc_jobs",
    "chat_video_jobs",
    "chat_video_segments",
    "chat_video_frames",
    "help_deliveries",
)

#: The header an app sends its device key in (never logged, and forwarded
#: to the core only on the phone voice hop).
DEVICE_KEY_HEADER = "X-Domovoi-Device-Key"
#: The browser's copy: HttpOnly, SameSite=Strict, set by POST
#: /api/devices/register when the key is minted.
DEVICE_KEY_COOKIE = "domovoi-device-key"

#: The one 404 body for a thread the caller can't see, byte-identical to
#: one that doesn't exist.
THREAD_NOT_FOUND = "thread not found"


class Action(str, Enum):
    """What a caller wants with a thread. ``READ`` covers seeing it in a
    list too; ``WRITE`` covers sending, retry, heads-up, lookups, voice,
    documents, videos, drafts, rename and archive."""

    READ = "read"
    WRITE = "write"
    SHARE = "share"
    DELETE = "delete"


@dataclass(frozen=True)
class Viewer:
    """Who is asking. ``device_id`` is the id its device KEY resolved to
    (None: keyless). ``system`` is a server path acting for nobody in
    particular (a worker writing into a thread it was given)."""

    device_id: str | None = None
    admin: bool = False
    shared_screen: bool = False
    system: bool = False

    @property
    def keyed(self) -> bool:
        return self.device_id is not None


#: A server path with no caller: a worker, a voice handler writing into a
#: thread a checked request handed it.
SYSTEM = Viewer(system=True)


@dataclass(frozen=True)
class Thread:
    """A thread as the rule sees it. ``private_columns`` false: a database
    without V086, where every thread is the household's."""

    id: int
    person_id: int | None = None
    title: str | None = None
    owner_device_id: str | None = None
    shared: bool = True
    private_columns: bool = False

    @property
    def state(self) -> str:
        if not self.private_columns:
            return "household"
        if self.shared:
            return "shared"
        if self.owner_device_id:
            return "private"
        return "admin-only"


@dataclass(frozen=True)
class Refusal:
    """Why not: ``status`` 404 (the caller can't see it) or 403/409 with a
    ``code`` the clients branch on."""

    status: int
    code: str
    detail: str

    def http(self, not_found: str = THREAD_NOT_FOUND) -> HTTPException:
        if self.status == 404:
            return HTTPException(status_code=404, detail=not_found)
        return ChatAccessRefused(self.status, self.code, self.detail)


class ChatAccessRefused(HTTPException):
    """403/409 with ``{"detail", "code"}`` (both apps' exception handlers
    write it that way: :func:`refusal_response`)."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(status_code=status, detail=detail)
        self.code = code


def refusal_response(exc: ChatAccessRefused) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})


_HIDDEN = Refusal(404, "thread_not_found", THREAD_NOT_FOUND)
NOT_YOURS = Refusal(403, "not_yours", "only the phone or browser that started this chat, or an admin, can do that")
ADMIN_READ_ONLY = Refusal(
    403, "admin_read_only",
    "this is another phone or browser's private chat: an admin can read, share or delete it, not write in it",
)
ADMIN_REQUIRED = Refusal(403, "admin_required", "only an admin can list every chat in the house")


def is_mine(viewer: Viewer, thread: Thread) -> bool:
    """The caller is the thread's owning device (and not, right now, a
    shared screen)."""
    return (
        thread.private_columns
        and viewer.device_id is not None
        and not viewer.shared_screen
        and thread.owner_device_id == viewer.device_id
    )


def decide(viewer: Viewer, thread: Thread, action: Action) -> Refusal | None:
    """None when ``viewer`` may do ``action`` to ``thread``; else why not."""
    if viewer.system or not thread.private_columns:
        # A worker writing where it was sent, or a database before V086
        # (1.4.x's rule: every chat is the household's).
        return None
    if is_mine(viewer, thread):
        return None
    state = thread.state
    if state == "shared":
        if viewer.admin or action in (Action.READ, Action.WRITE):
            return None
        return NOT_YOURS
    # Private to another device, or admin-only: an admin's to read, share
    # or delete (oversight, never speaking as the owner); nobody else's.
    if viewer.admin:
        return ADMIN_READ_ONLY if action is Action.WRITE else None
    return _HIDDEN


def can(viewer: Viewer, thread: Thread, action: Action) -> bool:
    return decide(viewer, thread, action) is None


def flags(viewer: Viewer, thread: Thread) -> dict[str, Any]:
    """The thread JSON's access fields (§2.6)."""
    mine = is_mine(viewer, thread)
    household_view = thread.state in ("household", "shared")
    return {
        "shared": thread.state in ("household", "shared"),
        "mine": mine,
        "can_write": can(viewer, thread, Action.WRITE),
        "can_share": thread.private_columns and can(viewer, thread, Action.SHARE),
        "can_delete": can(viewer, thread, Action.DELETE),
        # Visible only because the caller is an admin.
        "admin_view": bool(viewer.admin and not mine and not household_view),
    }


# ─── The columns (V086) ─────────────────────────────────────────────────

_HAS_PRIVATE = False
_HAS_DEVICE_KEYS = False


async def _column_exists(s: Any, table: str, column: str) -> bool:
    row = (
        await s.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        )
    ).first()
    return row is not None


async def has_private_columns(s: Any) -> bool:
    """Whether V086's ``chat_threads.owner_device_id`` / ``shared`` are
    there. Only a yes is cached: a process started before the migration
    sees it on the next request."""
    global _HAS_PRIVATE
    if not _HAS_PRIVATE:
        _HAS_PRIVATE = await _column_exists(s, "chat_threads", "shared")
    return _HAS_PRIVATE


async def has_device_keys(s: Any) -> bool:
    global _HAS_DEVICE_KEYS
    if not _HAS_DEVICE_KEYS:
        _HAS_DEVICE_KEYS = await _column_exists(s, "devices", "device_key_hash")
    return _HAS_DEVICE_KEYS


def reset_column_cache() -> None:
    """For tests that drop or add the columns."""
    global _HAS_PRIVATE, _HAS_DEVICE_KEYS
    _HAS_PRIVATE = False
    _HAS_DEVICE_KEYS = False


# ─── Device keys ────────────────────────────────────────────────────────


def device_key_hash(key: str) -> str:
    """What is stored of a device key: its SHA-256, hex."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def presented_device_key(request: Request | None) -> str | None:
    """The key the caller presents: the header, else the cookie."""
    if request is None:
        return None
    raw = (request.headers.get(DEVICE_KEY_HEADER) or "").strip()
    if not raw:
        raw = (request.cookies.get(DEVICE_KEY_COOKIE) or "").strip()
    return raw[:200] or None


#: Request state (server-side only: ``request.scope["state"]``) naming the
#: device a request acts for, set by the away bridge on the request it
#: builds for a phone's turn (web/backend/away_bridge.py). Nothing a client
#: sends can set it.
PROVEN_DEVICE_STATE = "domovoi_proven_device"


def proven_device(request: Any) -> str | None:
    """The device a server-built request names in its state, or None."""
    scope = getattr(request, "scope", None)
    state = scope.get("state") if isinstance(scope, dict) else None
    value = state.get(PROVEN_DEVICE_STATE) if isinstance(state, dict) else None
    return value if isinstance(value, str) and value else None


async def request_device(s: Any, request: Any) -> tuple[str, bool] | None:
    """``(device_id, shared_screen)`` of the caller's device: the one a
    server-built request names (:func:`proven_device`, only while it still
    holds a key), else the one whose key the request presents."""
    proven = proven_device(request)
    if proven is not None:
        if not await has_device_keys(s):
            return None
        row = (
            await s.execute(
                text("SELECT device_id, COALESCE(shared_screen, false) FROM devices "
                     "WHERE device_id = :d AND device_key_hash IS NOT NULL"),
                {"d": proven},
            )
        ).first()
        return (str(row[0]), bool(row[1])) if row is not None else None
    return await device_for_key(s, presented_device_key(request))


def key_matches(stored_hash: str | None, presented: str | None) -> bool:
    """Constant time: does ``presented`` hash to ``stored_hash``?"""
    if not stored_hash or not presented:
        return False
    return hmac.compare_digest(stored_hash, device_key_hash(presented))


async def device_for_key(s: Any, key: str | None) -> tuple[str, bool] | None:
    """``(device_id, shared_screen)`` of the device whose key ``key`` is;
    None for no key, a wrong one, or a database without V086."""
    if not key or not await has_device_keys(s):
        return None
    row = (
        await s.execute(
            text(
                "SELECT device_id, COALESCE(shared_screen, false) FROM devices WHERE device_key_hash = :h"
            ),
            {"h": device_key_hash(key)},
        )
    ).first()
    if row is None:
        return None
    return str(row[0]), bool(row[1])


async def viewer_from_request(request: Request, s: Any) -> Viewer:
    """Resolve the caller: its device from the key it presents, and
    whether it is an admin (a live Bearer; the admin cookie on GET/HEAD
    only; never the pre-setup grace). The route's own tier dependency
    has already let it in."""
    from domovoi import admin_auth

    state = await admin_auth.check_admin_request(request, s)
    admin = state == "ok" or (state == "cookie-only" and request.method in ("GET", "HEAD"))
    found = await request_device(s, request)
    if found is None:
        return Viewer(admin=admin)
    return Viewer(device_id=found[0], admin=admin, shared_screen=found[1])


async def chat_viewer(request: Request) -> Viewer:
    """The FastAPI dependency every chat route carries (the enumeration
    test looks for it). Resolved once per request."""
    cached = getattr(request.state, "chat_viewer", None)
    if isinstance(cached, Viewer):
        return cached
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        viewer = await viewer_from_request(request, s)
    request.state.chat_viewer = viewer
    return viewer


#: The enumeration test's marker: a route whose dependencies include a
#: callable carrying it goes through this module.
chat_viewer.__chat_access__ = True  # type: ignore[attr-defined]


class _ViewerParam(params.Depends):
    """``viewer: Viewer = VIEWER`` in a route: FastAPI resolves it with
    :func:`chat_viewer`. When the route function is called directly (a
    test, or server code calling a route's function), this default is
    what arrives: it reads as :data:`SYSTEM` (trusted server code, no
    caller to check)."""

    device_id = None
    admin = False
    shared_screen = False
    system = True
    keyed = False


#: The default every chat route's ``viewer`` parameter takes.
VIEWER = _ViewerParam(chat_viewer)


# ─── One thread ─────────────────────────────────────────────────────────


def _thread_columns(private: bool) -> str:
    if private:
        return "id, person_id, title, owner_device_id, shared"
    return "id, person_id, title, NULL AS owner_device_id, TRUE AS shared"


async def load_thread(s: Any, thread_id: int, *, lock: str | None = None) -> Thread | None:
    """The thread row as the rule sees it (no check). ``lock``: a row
    lock clause (``"FOR NO KEY UPDATE"``)."""
    private = await has_private_columns(s)
    row = (
        await s.execute(
            text(f"SELECT {_thread_columns(private)} FROM chat_threads WHERE id = :id {lock or ''}"),
            {"id": int(thread_id)},
        )
    ).first()
    if row is None:
        return None
    return Thread(
        id=int(row[0]), person_id=row[1], title=row[2], owner_device_id=row[3],
        shared=bool(row[4]), private_columns=private,
    )


async def check_thread(
    s: Any, thread_id: int, viewer: Viewer, action: Action, *, lock: bool | None = None,
) -> tuple[Thread | None, Refusal | None]:
    """:func:`thread_for` without raising: ``(thread, None)`` or ``(None,
    refusal)``. Writes lock the row (``FOR NO KEY UPDATE``) for the
    caller's transaction, so a share switch waits for a send that started
    before it, and the send never deadlocks with another send (§2.7)."""
    if lock is None:
        lock = action is Action.WRITE
    if not 1 <= int(thread_id) <= 2**63 - 1:
        return None, _HIDDEN
    thread = await load_thread(s, thread_id, lock="FOR NO KEY UPDATE" if lock else None)
    if thread is None:
        return None, _HIDDEN
    refusal = decide(viewer, thread, action)
    if refusal is not None:
        return None, refusal
    return thread, None


async def thread_for(
    s: Any, thread_id: int, viewer: Viewer, action: Action, *,
    not_found: str = THREAD_NOT_FOUND, lock: bool | None = None,
) -> Thread:
    """The thread, when ``viewer`` may do ``action`` to it. Raises 404
    (``not_found``, the body a missing one gets) when it can't see it,
    403 ``{detail, code}`` when it can but may not do this."""
    thread, refusal = await check_thread(s, thread_id, viewer, action, lock=lock)
    if refusal is not None:
        raise refusal.http(not_found)
    assert thread is not None
    return thread


async def thread_for_child(
    s: Any, thread_id: int | None, viewer: Viewer, action: Action, *, not_found: str,
) -> Thread | None:
    """For a child row (a draft, a job): its thread, checked; 404 with the
    CHILD's own not-found body when the caller can't see the thread. A
    child with no thread (a phone's private document job) returns None."""
    if thread_id is None:
        return None
    return await thread_for(s, int(thread_id), viewer, action, not_found=not_found)


# ─── Lists ──────────────────────────────────────────────────────────────


async def visible_sql(s: Any, alias: str, viewer: Viewer, *, scope: str = "mine") -> tuple[str, dict[str, Any]]:
    """The ``WHERE`` fragment (and its parameters) that keeps a list to
    the threads ``viewer`` may see. ``scope="all"`` is every chat in the
    house, for an admin (raise :data:`ADMIN_REQUIRED` for anyone else
    first); the default is the caller's own and the shared ones."""
    if viewer.system or not await has_private_columns(s):
        return "TRUE", {}
    if scope == "all" and viewer.admin:
        return "TRUE", {}
    if viewer.device_id is None or viewer.shared_screen:
        return f"{alias}.shared", {}
    return f"({alias}.shared OR {alias}.owner_device_id = :ca_dev)", {"ca_dev": viewer.device_id}


async def thread_flags_sql(s: Any, alias: str) -> str:
    """The select-list columns :func:`thread_from_row` reads back."""
    if await has_private_columns(s):
        return (
            f"{alias}.owner_device_id AS ca_owner, {alias}.shared AS ca_shared, TRUE AS ca_private, "
            f"(SELECT d.name FROM devices d WHERE d.device_id = {alias}.owner_device_id) AS ca_owner_name"
        )
    return "NULL::text AS ca_owner, TRUE AS ca_shared, FALSE AS ca_private, NULL::text AS ca_owner_name"


def thread_from_row(r: Any) -> Thread:
    """A :class:`Thread` from a list row carrying :func:`thread_flags_sql`."""
    return Thread(
        id=int(r["id"]), owner_device_id=r.get("ca_owner"), shared=bool(r.get("ca_shared", True)),
        private_columns=bool(r.get("ca_private", False)),
    )


# ─── Token-addressed files ──────────────────────────────────────────────


async def token_threads(s: Any, token: str, column: str) -> list[int]:
    """The threads a message there names upload / document ``token`` in
    (``column`` ``images`` or ``documents``)."""
    if column not in ("images", "documents"):
        raise ValueError(column)
    rows = (
        await s.execute(
            text(
                f"SELECT DISTINCT thread_id FROM chat_messages WHERE {column} @> CAST(:j AS JSONB)"
            ),
            {"j": json.dumps([{"token": token}])},
        )
    ).scalars().all()
    return [int(r) for r in rows]


async def token_readable(s: Any, token: str, column: str, viewer: Viewer) -> bool:
    """Whether a token-addressed file may be served (§2.4): the token is a
    capability (128 random bits, learned only from a thread the caller
    could read), so a KEYLESS caller (an ``<img>``, a 1.4.x app) is
    served. A caller that carries an identity (a key or an admin
    session) must be able to read one of the threads that names it. A
    staged file no message names yet is served."""
    if viewer.system or (viewer.device_id is None and not viewer.admin):
        return True
    if not await has_private_columns(s):
        return True
    ids = await token_threads(s, token, column)
    if not ids:
        return True
    for tid in ids:
        _, refusal = await check_thread(s, tid, viewer, Action.READ, lock=False)
        if refusal is None:
            return True
    return False


# ─── New threads ────────────────────────────────────────────────────────

SHARED_SCREEN_PRIVATE = Refusal(
    409, "shared_screen", "chats on a shared screen are shared with the household; this one can't be private",
)
KEY_MISSING = Refusal(
    409, "device_key_missing",
    "this phone or browser has no device key, so a private chat couldn't be kept private: register again first",
)


def new_thread(viewer: Viewer, shared: bool | None = None) -> tuple[str | None, bool]:
    """``(owner_device_id, shared)`` for a chat ``viewer`` starts (§2.3):
    private to its device by default; shared when asked, on a shared
    screen (always), and for a keyless caller (a 1.4.x app, a script),
    whose UI already says "shared with the household". Asking for a
    private chat where one can't be kept raises 409 ``shared_screen`` /
    ``device_key_missing``, so a client that lost its key fails loudly
    instead of sharing by surprise."""
    if viewer.device_id is not None and viewer.shared_screen:
        if shared is False:
            raise SHARED_SCREEN_PRIVATE.http()
        return viewer.device_id, True
    if viewer.device_id is None:
        if shared is False:
            raise KEY_MISSING.http()
        return None, True
    return viewer.device_id, bool(shared)


async def create_columns(
    s: Any, owner_device_id: str | None, shared: bool,
) -> tuple[str, str, dict[str, Any]]:
    """The extra ``(columns, values, params)`` an ``INSERT INTO
    chat_threads`` adds for a new thread's owner and sharing; empty on a
    database without V086."""
    if not await has_private_columns(s):
        return "", "", {}
    return (
        ", owner_device_id, shared",
        ", :ca_owner, :ca_shared",
        {"ca_owner": owner_device_id, "ca_shared": bool(shared)},
    )


async def device_has_key(s: Any, device_id: str | None) -> bool:
    """Whether ``device_id`` holds a device key (V086)."""
    if not device_id or not await has_device_keys(s):
        return False
    row = (
        await s.execute(
            text("SELECT 1 FROM devices WHERE device_id = :d AND device_key_hash IS NOT NULL"),
            {"d": device_id},
        )
    ).first()
    return row is not None


async def device_key_state(s: Any, device_id: str, request: Request) -> str:
    """``"unkeyed"`` (no such device, it holds no key yet, or no V086),
    ``"match"`` (the caller presents that device's key) or ``"mismatch"``.
    For anything bound to one device (a phone's admin grant)."""
    if not device_id or not await has_device_keys(s):
        return "unkeyed"
    stored = (
        await s.execute(
            text("SELECT device_key_hash FROM devices WHERE device_id = :d"), {"d": device_id},
        )
    ).scalar_one_or_none()
    if not stored:
        return "unkeyed"
    return "match" if key_matches(stored, presented_device_key(request)) else "mismatch"


# ─── The share switch ───────────────────────────────────────────────────


async def set_shared(s: Any, thread: Thread, shared: bool) -> bool:
    """Share ``thread`` with the household or stop sharing it (the caller
    holds :func:`thread_for` with ``Action.SHARE`` and the row lock).
    Returns whether it changed. A chat started on a shared screen, or
    owned by a device that is one now, stays shared (409
    ``shared_screen``); a chat with no owner that stops being shared is
    admin-only."""
    if not thread.private_columns or thread.shared == shared:
        return False
    if not shared and thread.owner_device_id:
        row = (
            await s.execute(
                text("SELECT COALESCE(shared_screen, false) FROM devices WHERE device_id = :d"),
                {"d": thread.owner_device_id},
            )
        ).first()
        if row is not None and row[0]:
            raise SHARED_SCREEN_PRIVATE.http()
    await s.execute(
        text("UPDATE chat_threads SET shared = :sh, shared_changed_at = now() WHERE id = :id"),
        {"sh": bool(shared), "id": thread.id},
    )
    return True
