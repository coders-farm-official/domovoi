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

**On your phone** (design §9: "Want them on your phone?"). When there
are results, the asker is a household voice that voice ID recognized
(``ctx.person_id``; never a guest) and at least one phone is marked as
theirs (Settings → Household → Phones & browsers, "This phone belongs
to"), the answer ends "Want them on your phone?" (``core.media_send``).
A yes searches again and sends the results to THEIR phones only, through
the help desk's send-to-their-phone path (``domovoi/help_desk/delivery.py``):
a home-server chat titled "Photos: …" whose reply carries the result
cards, and the app's notification. The help desk's limits hold (a few
sends an hour per person and per asking session, the same search once an
hour). A phone's own chat mic, a guest, a voice nobody recognized, or a
person with no phone is never asked.

**Room screens.** Said in a room with a screen (a phone or tablet in room
mode, or a video satellite's kiosk), the results are shown there as well
for a while (domovoi/room_media.py: the ``room_display`` frame's ``media``
and the kiosk page). A guest's results are never filtered by a person, so
a guest never puts a person's photos on a screen. A phone's chat mic
shows nothing on a room's screen: the chat has its own cards.

Everything runs on the server (``requires_network = "no"``). The router
records the turn as every other (``router._persist_turn``: intents_log
and conversation_log); the search words are never written to an app log,
and a failed search logs only the kind of error (:class:`ask.SearchFailed`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.media_understanding import ask
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)

SEND_KIND = "core.media_send"
OFFER_TEXT = "Want them on your phone?"
#: The chat a send makes is titled this, then what was asked.
TITLE_PREFIX = "Photos: "


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
    confirmation_kinds = (SEND_KIND,)

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
        try:
            found = await ask.find(session, req.q, people=people)
        except ask.SearchFailed as e:
            # The kind of error only: never the words.
            log.warning("media_search: the search failed: %s", e)
            return Response(text=ask.FAILED_TEXT, session_id=ctx.session_id, matched_handler=self.name)
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
        offer = await self._may_offer(found, ctx, session)
        if offer:
            await request_confirmation(
                session, ctx.session_id, kind=SEND_KIND, handler=self.name,
                data={"q": req.q, "connector": req.connector, "subject": req.subject,
                      "person_id": int(ctx.person_id)},
                prompt=OFFER_TEXT,
            )
            said = f"{said} {OFFER_TEXT}"
        return Response(text=said, session_id=ctx.session_id, expect_followup=offer,
                        matched_handler=self.name, data={"media": data})

    # ── "Want them on your phone?" ───────────────────────────────────────

    @staticmethod
    async def _may_offer(found: ask.Answer, ctx: Context, session: AsyncSession) -> bool:
        """Whether to offer to send the results: something was found, the
        asker is a voice voice ID recognized (never a guest) asking in a
        room (a phone's chat has the cards already), a yes can be heard
        (a session), and at least one phone is marked as theirs."""
        if found.state != "found" or not found.cards:
            return False
        if ctx.from_phone or ctx.session_id is None or ctx.person_id is None:
            return False
        from domovoi.help_desk import delivery

        return bool(await delivery.phones_of(session, int(ctx.person_id)))

    async def handle_confirmation(
        self, kind: str, data: dict, affirmative: bool, ctx: Context, session: AsyncSession
    ) -> Response:
        if kind != SEND_KIND or not affirmative:
            return Response(text="Okay, I won't send them.", session_id=ctx.session_id,
                            matched_handler=self.name)
        return await self._send(data, ctx, session)

    async def _send(self, data: dict, ctx: Context, session: AsyncSession) -> Response:
        from domovoi.help_desk import delivery

        def reply(text: str, extra: dict | None = None) -> Response:
            return Response(text=text, session_id=ctx.session_id, matched_handler=self.name, data=extra or {})

        # The person the offer was made to: only ever their own phones.
        person_id = data.get("person_id")
        if not isinstance(person_id, int):
            return reply("I don't know whose phone to send them to, so I haven't sent anything.")
        if ctx.person_id is not None and int(ctx.person_id) != person_id:
            # Someone else said yes: the offer was to the one who asked.
            return reply("That offer was for the person who asked, so I haven't sent anything.")
        if await delivery.person_name(session, person_id) is None:
            return reply("I don't know who that is any more, so I haven't sent anything.")
        phones = await delivery.phones_of(session, person_id)
        if not phones:
            from domovoi.handlers.help_desk import NO_PHONE_TEXT

            return reply(NO_PHONE_TEXT.format(who="yours"))
        req = ask.Request(q=str(data.get("q") or ""), connector=str(data.get("connector") or ""),
                          subject=str(data.get("subject") or ""))
        if not req.q:
            return reply("I couldn't find those again, so I haven't sent anything.")
        title = delivery.help_title(req.subject or req.q, prefix=TITLE_PREFIX)
        refused = await delivery.refusal(session, person_id=person_id, title=title,
                                         asker_session=ctx.session_id, what="photos")
        if refused:
            return reply(refused)
        try:
            found = await ask.find(session, req.q, people=True)
        except ask.SearchFailed as e:
            log.warning("media_search: the search to send failed: %s", e)
            return reply(f"{ask.FAILED_TEXT} Nothing was sent.")
        if found.state != "found" or not found.cards:
            return reply("I couldn't find those again, so I haven't sent anything.")
        d = await delivery.deliver(
            session, person_id=person_id, question=_asked(req), steps=ask.answer_text(found, req),
            phones=phones, title=title, stats={"model_role": "media", "media": found.as_json()})
        delivery.note_sent(ctx.session_id)
        return reply(f"Sent to your phone. It's in Chat as {d.title}.",
                     {"delivery_id": d.id, "thread_id": d.thread_id})


def _asked(req: ask.Request) -> str:
    """The question as the chat shows it ("Photos of the beach last summer")."""
    kind = (req.q.split() or ["photos"])[0]
    words = " ".join(x for x in (kind, req.connector or ("of" if req.subject else ""), req.subject) if x)
    return words[:1].upper() + words[1:]
