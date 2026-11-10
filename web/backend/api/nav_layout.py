"""The household's web menu: Settings → System → App layout's "Menu".

The sidebar's default shape (Home · Chat · Media ▸ · Tools ▸ · Plugins) is
``NAV_GROUPS`` in ``web/static/components.jsx``; ``resolveNavTree`` there
already takes a household override. This module keeps that override on the
server, so it is the same in every browser that opens this Domovoi's
dashboard (sidebar, Home's "everything" grid, the phone strip's rows).
The Android app has its own layout and never reads this.

Routes:

* ``GET /api/nav-layout`` (open, like ``/api/plugins/manifest``, which the
  shell reads before anyone signs in): ``{layout, updated_at, limits}``;
  ``layout`` is null for the default.
* ``PUT /api/nav-layout`` (admin, Bearer; the pre-setup grace like the
  other daily-surface admin writes): save a layout, validated here.
* ``DELETE /api/nav-layout`` (admin, Bearer): back to the default.

A save or a reset fires ``pg_notify('nav_layout_changed')``; the web's
LISTEN task turns it into a ``nav_layout.changed`` event on ``/ws/state``
(``web/backend/realtime.py``), and every open dashboard holding a household
credential redraws its menu at once. A browser without the socket (a guest)
reads the layout again when its tab comes back into view, or on the next
load.

The layout (stored in ``household_settings``, key ``nav_layout``, V045)::

    {"version": 1,
     "groups": [{"id": "media", "label": "Media", "items": ["music", ...]}, ...],
     "top":    ["chat", "plugins", ...],          # top-level pages
     "order":  ["chat", "group:media", ...]}      # top-level order

Validation: a bounded size; group ids unique and slug-shaped; labels plain
text (no ``<`` or ``>``, no control characters), 1 to ``LABEL_MAX``
characters after trimming; every route in the manifest's route charset
(``WEB_ROUTE_RE``), never ``home`` (the brand row is fixed), at most once
across all groups and ``top``; order tokens unique and naming a ``top``
page or a declared group. A route this server doesn't offer (a plugin not
installed) is accepted and ignored by the resolver, so a layout survives a
plugin being removed and put back. Shared-screen and AI-off hiding are
applied by the browser on top of it: a hidden page never shows, wherever
the layout puts it.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text

from domovoi.admin_auth import require_admin_mutation
from domovoi.plugins_runtime.manifest import WEB_ROUTE_RE
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(tags=["nav-layout"])

ADMIN = [Depends(require_admin_mutation)]

KEY = "nav_layout"
NOTIFY_CHANNEL = "nav_layout_changed"
VERSION = 1

MAX_BYTES = 8192          # the saved document, as JSON
MAX_GROUPS = 12
MAX_GROUP_ITEMS = 40
MAX_TOP = 60
LABEL_MAX = 24
FIXED_ROUTES = frozenset({"home"})   # the brand row: never placed
GROUP_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
GROUP_TOKEN = "group:"

LIMITS = {
    "max_groups": MAX_GROUPS,
    "max_group_items": MAX_GROUP_ITEMS,
    "max_top": MAX_TOP,
    "label_max": LABEL_MAX,
    "max_bytes": MAX_BYTES,
}


class LayoutError(ValueError):
    """A layout this server won't keep; the message says why, in words."""


def _label(raw: Any, where: str) -> str:
    if not isinstance(raw, str):
        raise LayoutError(f"{where}: the name must be text")
    label = " ".join(raw.split())
    if not label:
        raise LayoutError(f"{where}: the name can't be empty")
    if len(label) > LABEL_MAX:
        raise LayoutError(f"{where}: the name is longer than {LABEL_MAX} characters")
    if "<" in label or ">" in label:
        raise LayoutError(f"{where}: the name can't contain < or >")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") for ch in label):
        raise LayoutError(f"{where}: the name has a character that can't be shown")
    return label


def _route(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not WEB_ROUTE_RE.match(raw):
        raise LayoutError(f"{where}: {str(raw)[:40]!r} is not a page address")
    if raw in FIXED_ROUTES:
        raise LayoutError(f"{where}: Home stays at the top and can't be moved")
    return raw


def _list(raw: Any, where: str, most: int) -> list:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LayoutError(f"{where} must be a list")
    if len(raw) > most:
        raise LayoutError(f"{where}: at most {most}")
    return raw


def validate_layout(body: Any) -> dict[str, Any]:
    """The layout as stored, or :class:`LayoutError`. Pure."""
    if not isinstance(body, dict):
        raise LayoutError("the layout must be an object")
    unknown = set(body) - {"version", "groups", "top", "order"}
    if unknown:
        raise LayoutError(f"unknown field {sorted(unknown)[0]!r}")
    try:
        size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as e:
        raise LayoutError("the layout is not plain JSON") from e
    if size > MAX_BYTES:
        raise LayoutError(f"the layout is too large ({MAX_BYTES} bytes at most)")
    if body.get("version", VERSION) != VERSION:
        raise LayoutError(f"unknown layout version (this server keeps version {VERSION})")

    placed: set[str] = set()

    def place(route: str, where: str) -> None:
        if route in placed:
            raise LayoutError(f"{where}: {route!r} is in the menu twice")
        placed.add(route)

    groups: list[dict[str, Any]] = []
    ids: set[str] = set()
    for i, g in enumerate(_list(body.get("groups"), "groups", MAX_GROUPS)):
        where = f"group {i + 1}"
        if not isinstance(g, dict):
            raise LayoutError(f"{where} must be an object")
        extra = set(g) - {"id", "label", "items"}
        if extra:
            raise LayoutError(f"{where}: unknown field {sorted(extra)[0]!r}")
        gid = g.get("id")
        if not isinstance(gid, str) or not GROUP_ID_RE.match(gid):
            raise LayoutError(f"{where}: the id must be lowercase letters, digits and dashes")
        if gid in ids:
            raise LayoutError(f"{where}: two groups share the id {gid!r}")
        ids.add(gid)
        label = _label(g.get("label"), where)
        items = []
        for r in _list(g.get("items"), f"{where} pages", MAX_GROUP_ITEMS):
            route = _route(r, where)
            place(route, where)
            items.append(route)
        groups.append({"id": gid, "label": label, "items": items})

    top = []
    for r in _list(body.get("top"), "top", MAX_TOP):
        route = _route(r, "top level")
        place(route, "top level")
        top.append(route)

    order = []
    seen: set[str] = set()
    tops = set(top)
    for tok in _list(body.get("order"), "order", MAX_TOP + MAX_GROUPS):
        if not isinstance(tok, str):
            raise LayoutError("order: every entry must be text")
        if tok in seen:
            raise LayoutError(f"order: {tok[:40]!r} is listed twice")
        seen.add(tok)
        if tok.startswith(GROUP_TOKEN):
            if tok[len(GROUP_TOKEN):] not in ids:
                raise LayoutError(f"order: no group {tok[len(GROUP_TOKEN):][:32]!r}")
        elif tok not in tops:
            raise LayoutError(f"order: {tok[:40]!r} is not a top-level page")
        order.append(tok)

    return {"version": VERSION, "groups": groups, "top": top, "order": order}


# ─── storage ──────────────────────────────────────────────────────────────


async def read_layout() -> tuple[dict[str, Any] | None, datetime | None]:
    """(layout, updated_at); (None, None) for the default, and also when
    V045 has not run (a box behind on migrations draws the default)."""
    try:
        async with session_scope() as s:
            row = (await s.execute(
                text("SELECT value, updated_at FROM household_settings WHERE key = :k"),
                {"k": KEY},
            )).first()
    except Exception as e:  # noqa: BLE001 — table missing, DB blip
        log.debug("nav layout: no household_settings yet: %s", e)
        return None, None
    if row is None:
        return None, None
    value = row[0]
    if isinstance(value, str):
        value = json.loads(value)
    try:
        # What a reader gets is always what a save would keep now.
        return validate_layout(value), row[1]
    except LayoutError as e:
        log.warning("nav layout: the stored layout is not valid, using the default: %s", e)
        return None, None


async def _notify(s) -> None:
    try:
        await s.execute(text("SELECT pg_notify(:c, :p)"), {"c": NOTIFY_CHANNEL, "p": KEY})
    except Exception as e:  # noqa: BLE001 — the poll loop catches up
        log.debug("nav layout: notify failed: %s", e)


def _unavailable(e: Exception) -> HTTPException:
    log.warning("nav layout: could not save: %s", e)
    return HTTPException(
        status_code=503,
        detail="the menu could not be saved just now (is the database up to date?); try again shortly",
    )


async def snapshot() -> dict[str, Any] | None:
    """The ``nav_layout`` realtime channel: the layout itself (small, and
    the same thing the open GET returns), None for the default."""
    layout, _ = await read_layout()
    return layout


def _answer(layout: dict[str, Any] | None, updated_at: datetime | None) -> dict[str, Any]:
    return {
        "layout": layout,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "limits": LIMITS,
    }


# ─── routes ───────────────────────────────────────────────────────────────


@router.get("/api/nav-layout")
async def get_nav_layout() -> dict[str, Any]:
    """Open (like the plugin manifest the shell reads beside it): the
    household's menu layout, or ``layout: null`` for the default."""
    return _answer(*await read_layout())


@router.put("/api/nav-layout", dependencies=ADMIN)
async def put_nav_layout(body: Any = Body(...)) -> dict[str, Any]:
    """Save the household's menu (admin). 422 with the reason for a
    layout this server won't keep; nothing is saved then."""
    try:
        layout = validate_layout(body)
    except LayoutError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    try:
        async with session_scope() as s:
            row = (await s.execute(
                text(
                    "INSERT INTO household_settings (key, value, updated_at) "
                    "VALUES (:k, CAST(:v AS jsonb), now()) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now() "
                    "RETURNING updated_at"
                ),
                {"k": KEY, "v": json.dumps(layout, ensure_ascii=False)},
            )).first()
            await _notify(s)
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e) from None
    return _answer(layout, row[0] if row else None)


@router.delete("/api/nav-layout", dependencies=ADMIN)
async def reset_nav_layout() -> dict[str, Any]:
    """Back to the default menu (admin)."""
    try:
        async with session_scope() as s:
            await s.execute(text("DELETE FROM household_settings WHERE key = :k"), {"k": KEY})
            await _notify(s)
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e) from None
    return _answer(None, None)
