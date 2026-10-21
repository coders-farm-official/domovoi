"""Domovoi's help desk on the dashboard — ``/api/help`` (part 9).

* ``GET /api/help/search?q=`` — OPEN, like the manual it searches: the
  passages Domovoi's own help holds for a question (the manual, the FAQ,
  the troubleshooting guide, the error-code entries, installed plugins'
  help files), best first, each with its section and link. No model:
  this is the "?" panel's search, and with the household's AI features
  off it is the whole of help.
* ``GET /api/help/facts`` — household READ: live facts from this server,
  exact (version, rooms, the internet answer, AI on or off, plugins).
* ``GET /api/help/checks`` — household READ: what the system's own
  records show (Home's attention checks, the rooms' health and
  self-checks, the update record, the container guard), redacted
  (``domovoi/help_desk/troubleshoot.py``). What Home shows only to an
  admin is here only for one.
* ``POST /api/help/report`` — household tier: "Report a problem", the
  redacted summary the person reviews, then copies or saves. Nothing is
  sent anywhere and nothing is kept.
* ``GET /api/help/deliveries`` — household READ: help steps sent by voice
  to a person's phones (V035), for the app's catch-up read.

Chat answers help questions through the same pieces
(``web/backend/api/chat.py``, :func:`help_turn_for`): a thread titled
"Help: …" is a help chat; in any other thread, a question about Domovoi
whose answer the help holds is answered from it too.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import admin_auth, ai_features, egress
from domovoi.admin_auth import require_device, require_device_read
from domovoi.help_desk import answer as help_answer
from domovoi.help_desk import delivery, diagnostics
from domovoi.help_desk import facts as help_facts
from domovoi.help_desk import troubleshoot
from domovoi.help_desk.index import HelpIndex, first_words, get_index
from domovoi.help_desk.source import plugin_help_from_rows, plugin_rows
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/help", tags=["help"])

READ = [Depends(require_device_read)]
DEVICE = [Depends(require_device)]

_VERSION_TTL_SEC = 60.0
_version_cache: dict[str, Any] = {}


async def running_version() -> str | None:
    """The release label of the code on this box (cached a minute: it
    runs ``git describe``)."""
    from domovoi import git_version

    now = time.monotonic()
    if _version_cache.get("at") and now - _version_cache["at"] < _VERSION_TTL_SEC:
        return _version_cache.get("label")
    try:
        label = await asyncio.to_thread(git_version.version_label)
    except Exception as e:  # noqa: BLE001
        log.warning("help: version unreadable: %s", e)
        label = None
    _version_cache.update(at=now, label=label)
    return label


async def _plugins() -> list[dict[str, Any]]:
    try:
        async with session_scope() as s:
            return await plugin_rows(s)
    except Exception as e:  # noqa: BLE001
        log.warning("help: plugins unreadable: %s", e)
        return []


def index_for(rows: list[dict[str, Any]]) -> HelpIndex:
    installed = {str(r.get("slug")) for r in rows
                 if r.get("enabled") and r.get("status") in ("ok", "degraded")}
    return get_index(plugin_help_from_rows(rows), installed_plugins=installed)


async def caller_is_admin(request: Request | None) -> bool:
    if request is None:
        return False
    try:
        result = await admin_auth.check_admin_request(request)
    except Exception:  # noqa: BLE001 — can't tell: not an admin
        return False
    return result in ("ok", "cookie-only", "pre-setup")


# ── live state ──────────────────────────────────────────────────────────


async def _rooms() -> list[dict[str, Any]]:
    from web.backend.api import satellites

    try:
        return [s.model_dump() for s in await satellites.list_satellites()]
    except Exception as e:  # noqa: BLE001
        log.warning("help: rooms unreadable: %s", e)
        return []


async def _health() -> dict[str, Any]:
    try:
        from web.backend.main import health

        return (await health()).model_dump()
    except Exception as e:  # noqa: BLE001
        log.warning("help: health unreadable: %s", e)
        return {}


async def _room_records() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Each room's newest health sample and newest outage report (V022)."""
    samples: dict[str, dict[str, Any]] = {}
    outages: dict[str, dict[str, Any]] = {}
    try:
        async with session_scope() as s:
            for r in (await s.execute(text(
                "SELECT DISTINCT ON (room_id) room_id, received_at, sample FROM satellite_health "
                "ORDER BY room_id, received_at DESC"
            ))).all():
                samples[r[0]] = {"received_at": r[1], "sample": r[2] or {}}
            for r in (await s.execute(text(
                "SELECT DISTINCT ON (room_id) room_id, reported_at, report FROM satellite_outages "
                "ORDER BY room_id, reported_at DESC"
            ))).all():
                outages[r[0]] = {"reported_at": r[1], "report": r[2] or {}}
    except Exception as e:  # noqa: BLE001 — V022 missing: no records yet
        log.info("help: room records unreadable: %s", e)
    return samples, outages


async def _last_update() -> dict[str, Any] | None:
    from domovoi import git_version

    try:
        return (await asyncio.to_thread(git_version.load_last_update))[0]
    except Exception as e:  # noqa: BLE001
        log.info("help: update record unreadable: %s", e)
        return None


async def _guard() -> dict[str, Any] | None:
    from web.backend.api.containers import snapshot

    try:
        return await snapshot()
    except Exception as e:  # noqa: BLE001
        log.info("help: container guard unreadable: %s", e)
        return None


async def gather_facts(rooms: list[dict[str, Any]] | None = None,
                       plugins: list[dict[str, Any]] | None = None) -> help_facts.LiveFacts:
    if rooms is None:
        rooms = await _rooms()
    if plugins is None:
        plugins = await _plugins()
    return help_facts.build(
        version=await running_version(), internet=egress.policy(), ai_on=ai_features.enabled(),
        rooms=rooms, plugins=plugins,
    )


async def gather_state(rooms: list[dict[str, Any]] | None = None,
                       plugins: list[dict[str, Any]] | None = None) -> troubleshoot.State:
    if rooms is None:
        rooms = await _rooms()
    if plugins is None:
        plugins = await _plugins()
    samples, outages = await _room_records()
    return troubleshoot.State(
        health=await _health(), rooms=rooms, sat_health=samples, outages=outages, plugins=plugins,
        last_update=await _last_update(), guard=await _guard(), internet=egress.policy(),
    )


# ── chat's help turn ────────────────────────────────────────────────────


async def help_turn_for(question: str, *, help_thread: bool, admin: bool,
                        has_screenshot: bool = False, extra_query: str = "") -> help_answer.HelpTurn | None:
    """The help turn for a chat message, or None when it isn't one (a
    message in an ordinary chat that isn't about Domovoi, or that the help
    holds nothing for: that chat goes on as before)."""
    if not help_thread and not help_answer.looks_like_help(question):
        return None
    plugins = await _plugins()
    index = index_for(plugins)
    # Live state only when the question is about it: reading it asks the
    # core, and a plain how-to shouldn't wait on that.
    want_facts = help_answer.wants_facts(question)
    want_checks = help_answer.wants_troubleshoot(question) or help_answer.wants_troubleshoot(extra_query)
    rooms = await _rooms() if (want_facts or want_checks) else []
    facts_text = help_facts.render(await gather_facts(rooms, plugins)) if want_facts else None
    found = (troubleshoot.findings(await gather_state(rooms, plugins), admin=admin)
             if want_checks else None)
    turn = help_answer.plan(question, index, facts_text=facts_text, findings=found,
                            has_screenshot=has_screenshot, extra_query=extra_query)
    if not help_thread and not turn.hits:
        return None
    return turn


# ── routes ──────────────────────────────────────────────────────────────


@router.get("/search")
async def search(q: str = Query("", max_length=500), limit: int = Query(5, ge=1, le=10)) -> dict[str, Any]:
    """The help's passages for ``q``. OPEN (the manual is): nothing here is
    live state. ``covered`` is false when nothing answers the question."""
    index = index_for(await _plugins())
    hits = index.search(q, limit=limit)
    confident = [h for h in hits if h.confident()]
    return {
        "query": q,
        "covered": bool(confident),
        "results": [
            {"id": h.passage.id, "title": h.passage.title, "section": h.passage.section,
             "snippet": first_words(h.passage.text, 45), "link": h.passage.link, "kind": h.passage.kind,
             "confident": h.confident()}
            for h in (confident or hits[:3])
        ],
        "not_covered": None if confident else help_answer.NOT_COVERED,
    }


@router.get("/facts", dependencies=READ)
async def facts() -> dict[str, Any]:
    return (await gather_facts()).as_dict()


@router.get("/checks", dependencies=READ)
async def checks(request: Request) -> dict[str, Any]:
    found = troubleshoot.findings(await gather_state(), admin=await caller_is_admin(request))
    return {"findings": [f.as_dict() for f in found]}


class ReportBody(BaseModel):
    what_happened: str = Field("", max_length=4000)
    include_room_names: bool = False


@router.post("/report", dependencies=DEVICE)
async def report(body: ReportBody, request: Request) -> dict[str, Any]:
    """The "Report a problem" summary, for the person to read, then copy or
    save. Built on request, returned, never stored or sent."""
    rooms = await _rooms()
    plugins = await _plugins()
    state = await gather_state(rooms, plugins)
    admin = await caller_is_admin(request)
    found = troubleshoot.findings(state, admin=admin)
    status = (state.last_update or {}).get("status") if admin else None
    text_ = diagnostics.build(
        await gather_facts(rooms, plugins), found, what_happened=body.what_happened,
        include_room_names=body.include_room_names, last_update_status=status,
    )
    return {"text": text_, "filename": "domovoi-problem-report.md"}


@router.get("/deliveries", dependencies=READ)
async def deliveries(person_id: int | None = Query(None, ge=1), since_id: int | None = Query(None, ge=0),
                     limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """Help steps sent by voice (V035): newest first, or after ``since_id``
    oldest first. The app reads its owner's on start and after a
    ``help_deliveries.changed`` event."""
    try:
        async with session_scope() as s:
            rows = await delivery.recent(s, person_id=person_id, since_id=since_id, limit=limit)
    except Exception as e:  # noqa: BLE001 — V035 missing: nothing was ever sent
        log.info("help: deliveries unreadable: %s", e)
        rows = []
    return {"deliveries": rows}
