"""Asking for the household's photos and videos by voice (1.5.0, design
MEDIA-UNDERSTANDING §9 "Voice"; slice 6).

"show me photos of the beach from last summer", "find the video where we
sang happy birthday", "do we have any pictures from Christmas", "search
my videos for fireworks": one search (:mod:`domovoi.media_understanding.ask`),
answered in a sentence — the count, what they might show, when, and that
they're on the Photos page ("I found 12 photos that might show the beach,
from last summer. They're on the Photos page."). A video's best moment is
said when the search knows it.

Honest answers, as the chat's: understanding off ("Photo understanding is
off; an admin can turn it on in Settings."), nothing looked at yet ("I
haven't looked through your photos yet."), nothing found.

**People.** A name becomes the person filter only with "Recognize faces"
on, and only for a recognized household voice (voice ID: ``ctx.person_id``)
or a phone's chat mic (the household device tier). An unrecognized voice
in a room is a guest here: naming a person gets "I can only look for
photos of a person for someone I recognize." and no results.

**Room screens.** Said in a room with a screen (a phone or tablet in room
mode, or a video satellite's kiosk), the results are shown there as well
for a while (domovoi/room_media.py: the ``room_display`` frame's ``media``
and the kiosk page). A guest's results are never filtered by a person, so
a guest never puts a person's photos on a screen. A phone's chat mic
shows nothing on a room's screen: the chat has its own cards.

Everything runs on the server (``requires_network = "no"``). The router
logs the turn as every other (``router._persist_turn``); the search words
are never written to the log.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.media_understanding import ask
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


def people_allowed(ctx: Context) -> bool:
    """Whether this turn may search by a person's face: a voice that voice ID
    recognized, or a phone's chat mic (household tier). Never a guest."""
    return ctx.person_id is not None or ctx.from_phone


class MediaSearchHandler(Handler):
    name = "media_search"
    # band rationale: anchored media (270-349). Every pattern starts with an
    #   asking verb (show me / find / search / do we have / are there) AND
    #   names a kind (photos, pictures, videos, clips) right after it, so it
    #   poaches nothing: music's ^play catch-all (300) comes first and never
    #   says "show" or "find"; printing (222) owns "print the photos";
    #   library's (310) "find X in my library" is left to it explicitly
    #   (ask._NOT_LIBRARY), and its "do i have X" is music-only, so "do i
    #   have any videos from christmas" is this handler's. Ahead of the
    #   900 media-provider catch-all (^find (.+)$).
    priority_band = 305
    display = HandlerDisplay(label="Photos & videos", tone="media")
    example_phrases = (
        "show me photos of the beach from last summer",
        "find the video where we sang happy birthday",
    )
    requires_network = "no"
    read_only = True

    tool_schema = {
        "name": "media_search",
        "description": (
            "Search the household's own photos and videos by what is in them, the words in them, "
            "a time or a person's name. Not for music, podcasts or films to play."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What to look for, with the kind and time as said."},
            },
            "required": ["query"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [FastPath(rx, MediaSearchHandler._from_match) for rx in ask.REQUEST_RES]

    def offers_tool(self, transcript: str) -> bool:
        return ask.mentions_media(transcript)

    async def _from_match(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response | None:
        req = ask.request_from(m.group("kind"), m.groupdict().get("rest") or "")
        return await self._answer(req, ctx, session)

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        t = " ".join((intent.transcript or "").lower().strip().rstrip(".!?").split())
        req = ask.read_request(t) or ask.Request(q=t)
        return await self._answer(req, ctx, session)

    async def execute_from_tool(self, args: dict[str, Any], ctx: Context,
                                session: AsyncSession) -> Response | None:
        q = " ".join(str(args.get("query") or "").split())[:ask.TOOL_Q_MAX]
        if not q:
            return None
        req = ask.read_request(q.lower()) or ask.Request(q=q)
        return await self._answer(req, ctx, session)

    async def _answer(self, req: ask.Request, ctx: Context, session: AsyncSession) -> Response:
        people = people_allowed(ctx)
        found = await ask.find(session, req.q, people=people)
        said = ask.answer_text(found, req)
        if ctx.room_id and not ctx.from_phone and ctx.app is not None:
            from domovoi import room_media

            try:
                room_media.show(ctx.app, ctx.room_id, found, said, people_ok=people)
            except Exception as e:  # noqa: BLE001 — the spoken answer stands
                log.warning("media_search: showing results in %s failed: %s", ctx.room_id, type(e).__name__)
        data = found.as_json()
        # A voice reply carries no captions or text from the pictures: the
        # count and the ids are enough for a client to open them.
        data["items"] = [{"id": c["id"], "kind": c["kind"], "at_s": c["at_s"]} for c in found.cards]
        return Response(text=said, session_id=ctx.session_id,
                        matched_handler=self.name, data={"media": data})
