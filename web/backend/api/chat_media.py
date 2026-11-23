"""Photos and videos in a home-server chat (1.5.0, design
MEDIA-UNDERSTANDING §9 "Chat"; slice 6). The search itself is
:mod:`domovoi.media_understanding.ask`, shared with the voice handler.

Two ways in, both answered by the app, not the model:

* **The person's own words** (:func:`fast_request`): "show me photos of
  the dog", "find the video where we sang happy birthday" — the same
  phrasings the voice handler takes. No model is asked.
* **The model's tool line**: a plain text turn that talks about photos or
  videos (:func:`ask.mentions_media`) tells the model it may answer with
  ONLY ``FIND_MEDIA: <words>`` (:data:`ask.TOOL_PROMPT`); the chat holds
  the reply back while it may still be one, as it does for ``LOOK_UP``.

The reply is :func:`ask.answer_text` (the count, what they might show,
when, "They're on the Photos page."), and the row keeps the result cards
in ``stats.media`` (read out as the message's ``media``): each a thumbnail
URL, ``might_show``, ``tags``, ``text``, the date and, for a video, the
moment ``at_s``. Clients draw them as text, never HTML.

**Tiers.** Chat is the household device tier already; a device marked as
a shared screen gets no person-based results (:data:`ask.SHARED_SCREEN_TEXT`),
as the People pages refuse it (web/backend/api/faces.py).

**Later turns** in the same chat (:func:`rules`): the model is given the
latest results inside a fenced block with a per-call code it is told
never to obey or repeat (:func:`ask.model_rules`, the chat video's fence),
because captions, tags and text found in a picture are untrusted words.
Such a turn is not told about ``LOOK_UP`` / ``READ_PAGE`` (security audit
round 6, DOC-10): words in a picture can't send a search out.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy import text

from domovoi.chat_video.context import _fence
from domovoi.media_understanding import ask
from web.backend.db import session_scope

#: How far back a chat's results stay in the model's view (messages).
RECENT_MESSAGES = 6

_END = re.compile(r"[\s.!?]+\Z")


def fast_request(message: str) -> ask.Request | None:
    """The person's message as a photo or video request, or None."""
    t = " ".join((message or "").lower().replace("’", "'").split())
    if not t or len(t) > 300 or "\n" in (message or "").strip():
        return None
    return ask.read_request(_END.sub("", t))


async def shared_screen(request: Request | None) -> bool:
    """Whether the caller names a device an admin marked as a shared screen."""
    if request is None:
        return False
    from web.backend.api import devices as devices_api

    dev = devices_api.caller_device(request)
    if not dev:
        return False
    async with session_scope() as s:
        if not await devices_api._has_shared_screen(s):
            return False
        return bool((await s.execute(text("SELECT shared_screen FROM devices WHERE device_id = :d"),
                                     {"d": dev})).scalar())


@dataclass
class Turn:
    text: str
    media: dict[str, Any]


async def answer(q: str, request: Request | None, req: ask.Request | None = None) -> Turn:
    """Search for ``q`` and say what was found."""
    shared = await shared_screen(request)
    async with session_scope() as s:
        found = await ask.find(s, q, people=not shared)
    said = ask.SHARED_SCREEN_TEXT if (found.state == "people_refused" and shared) else ask.answer_text(found, req)
    return Turn(said, found.as_json())


async def rules(thread_id: int) -> str | None:
    """The system message's addition for a later turn: the chat's latest
    results, fenced. None: no results among the recent messages."""
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT stats FROM chat_messages WHERE thread_id = :t ORDER BY id DESC LIMIT :n"),
            {"t": thread_id, "n": RECENT_MESSAGES})).all()
    for (stats,) in rows:
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except ValueError:
                continue
        media = stats.get("media") if isinstance(stats, dict) else None
        if isinstance(media, dict) and media.get("items"):
            return ask.model_rules(media, _fence())
    return None
