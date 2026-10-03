"""The "did you mean …?" dialog for spoken music requests.

When the spoken-name resolver (:mod:`domovoi.handlers.shared.library_match`)
finds a request close to a library name but not close enough to play it
outright (its middle band: ``music_match_ask_threshold`` ≤ score <
``music_match_play_threshold``), MusicHandler asks instead of guessing::

    "play glass harber"   → "Did you mean Glass Harbor?"
    "yes"                 → plays it
    "no"                  → "OK."
    "no, play the velvet kites" / a bare "velvet kites" → plays that instead

Shape (modelled on the radio plugin's ``radio.station_choice``): the
question is parked through the mediated confirmation API as
``core.music_choice`` with up to three candidates, and the reply comes back
here. Unlike a yes/no confirmation, the reply may be free text, so the kind
is also one of MusicHandler's ``choice_kinds``: the router hands the WHOLE
reply to :meth:`MusicChoiceMixin.handle_choice_reply` before its yes/no
pre-empt — whose ``\\b`` would otherwise read "no, play X" as a bare "no"
and lose the "play X".

What a reply can be (:func:`parse_choice_reply`, pure):

* yes ("yes", "yeah, play it", "okay", "sure, that one") → the first
  candidate;
* an ordinal ("the second one", "number two", "the other one", "no, the
  first one") → that candidate;
* no ("no", "nope, never mind", "no, stop") → "OK." — a "no" never runs
  another command off its tail, except a request to play something;
* "no, play X" / "no, I said X" / a bare "X" → if X names one of the
  parked candidates (``library_match.match_reply``) that one, else X is
  played as a new request ("play X" through the fast paths: the resolver,
  which may ask again, then today's search; or a playlist, a podcast…);
* anything else — a new command, a question, a long sentence — is not
  about the choice: the router routes it as an ordinary turn and the
  question is dropped.

A parked choice expires after :data:`CHOICE_TTL_SEC` (then any reply is
routed normally). Asking never touches the room's MPD queue and carries no
``music_action``: the streaming layer holds an interrupted song's restart
through the follow-up window (``StreamSession.hold_music_start``), so an
unanswered question gets the old music back, a "no" resumes it, and a
"yes" replaces it with the new play.

It never asks where nobody can answer: the dashboard's play box
(``Context.answerable`` False), a chat-mode tool call (no session), or
with the "Ask 'did you mean…?'" setting (``music_choice_enabled``) off —
then the request goes on to today's search.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.config import settings
from domovoi.confirmations import request_confirmation
from domovoi.handlers.shared import library_match
from domovoi.handlers.shared.library_match import Candidate, EntityRef, Resolution
from domovoi.models import Context, Response

log = logging.getLogger(__name__)

MUSIC_CHOICE_KIND = "core.music_choice"
#: How long a "did you mean …?" stays answerable, in seconds.
CHOICE_TTL_SEC = 60.0
#: Candidates parked per question; the prompt says at most two of them.
MAX_CHOICES = 3
SPOKEN_CHOICES = 2
#: A bare reply longer than this is a sentence, not a name: routed normally.
MAX_NAME_WORDS = 10

_LOST_TRACK_TEXT = "I've lost track of that one — what would you like to hear?"
_GONE_TEXT = "That one isn't in your library anymore."


# ─── Reading the reply (pure) ──────────────────────────────────────────────

_ORDINAL_WORDS = {
    "first": 0, "1st": 0, "one": 0, "1": 0,
    "second": 1, "2nd": 1, "two": 1, "2": 1,
    "third": 2, "3rd": 2, "three": 2, "3": 2,
    "other": 1,
}
_ORDINAL_RE = re.compile(
    r"^(?:the )?(first|1st|second|2nd|third|3rd|other|last)"
    r"(?: one| song| track| album| artist| band)?(?:,? please)?$"
    r"|^(?:number|option) (one|two|three|1|2|3)(?:,? please)?$"
)
# Said after a yes-word: nothing that changes the answer.
_ACCEPT_TAIL_RE = re.compile(
    r"^(?:(?:please|thanks|thank you(?: very much| so much)?|cheers|go ahead|please do|do it"
    r"|that's it|that's the one|that one|exactly|correct|right|that's right|i did|i do"
    r"|yes|yeah|yep|yup|sure|sure thing|thing|of course|definitely|absolutely|ok|okay"
    r"|(?:play|put on) (?:it|that|that one|this|this one|them|those))"
    r"(?:[,.!]?\s+|$))+$"
)
# A whole reply that accepts without a yes-word.
_ACCEPT_RE = re.compile(
    r"^(?:ok|okay|alright|all right|sounds good|go ahead|go for it|do it|please|please do"
    r"|that one|that's the one|that's it|perfect"
    r"|(?:play|put on) (?:it|that|that one|this one))"
    r"(?:,? (?:please|thanks|thank you))?$"
)
# A "no" and whatever follows it. router._NO_RE's words, plus the ones that
# only make sense after "did you mean …?".
_NO_LEAD_RE = re.compile(
    r"^(?:no(?:,? no)*|nope|nah|wrong|incorrect|that's wrong|not quite|not exactly"
    r"|not that(?: one)?|neither(?: of (?:them|those))?|none of (?:them|those))\b[,.!]?\s*(.*)$"
)
# After a "no": nothing more to do.
_DECLINE_TAIL_RE = re.compile(
    r"^(?:(?:thanks|thank you|never ?mind|forget (?:it|about it|that)|that's all|that's it"
    r"|i'm good|i'm fine|not now|nothing|no|nope|don't bother|cancel(?: that| it)?|leave it"
    r"|it's fine|that's fine|all good|my mistake|sorry)(?:[,.!]?\s+|$))+$"
)
# What leads a corrected request: "no, PLAY x", "no, I SAID x".
_REQUEST_LEAD_RE = re.compile(
    r"^(?:(?:i said|i meant|i mean|i asked for|i was asking for|i wanted|i want|i'd like"
    r"|i would like|give me|play me|play|put on|listen to|to hear|to listen to)\s+)+"
)
_QUESTION_RE = re.compile(
    r"^(?:what|what's|who|who's|why|how|when|where|which|whose|is|are|can|could|would"
    r"|do|does|did|will|should)\b"
)
# A bare reply that is no name: thanks, a hesitation, a word Whisper makes
# up out of a quiet room ("you", "thank you", "bye"). Routed normally.
_NOT_A_NAME_RE = re.compile(
    r"^(?:thanks|thank you(?: very much| so much)?|thanks for watching|thank you for watching"
    r"|cheers|you|bye|bye bye|goodbye|so|oh|ah|uh+|um+|hm+|huh|sorry|pardon|excuse me"
    r"|hello|hi|hey|wait|hold on|one sec(?:ond)?|i don't know|i'm not sure|not sure|maybe"
    r"|good|great|cool|nice|awesome|the end)$"
)


@dataclass(frozen=True)
class ChoiceReply:
    """What a reply to "did you mean …?" says: ``pick`` candidate
    ``index``; ``decline``; ``name`` (play ``name`` — a parked candidate if
    it names one); ``pass`` (not about the choice: route it normally)."""

    action: Literal["pick", "decline", "name", "pass"]
    index: int = 0
    name: str = ""


_PASS = ChoiceReply("pass")
_DECLINE = ChoiceReply("decline")


def _ordinal(text: str, n: int) -> ChoiceReply | None:
    """The candidate an ordinal reply picks (``n`` parked), a decline for
    one past the end ("the other one" with one candidate), else None."""
    m = _ORDINAL_RE.match(text)
    if m is None:
        return None
    word = m.group(1) or m.group(2)
    if word == "last":
        index = min(n, SPOKEN_CHOICES) - 1
    else:
        index = _ORDINAL_WORDS[word]
    if not 0 <= index < n:
        return _DECLINE
    return ChoiceReply("pick", index=index)


def _strip_punct(text: str) -> str:
    return text.strip().lstrip(",.!;: ").strip()


_POLITE_TAIL_RE = re.compile(r"(?:[,\s]+(?:please|thanks|thank you))+$")


def _as_name(text: str) -> ChoiceReply:
    text = _POLITE_TAIL_RE.sub("", _strip_punct(text).rstrip(",.!?"))
    if not text or len(text.split()) > MAX_NAME_WORDS:
        return _PASS
    return ChoiceReply("name", name=text)


def _request(text: str, n: int) -> ChoiceReply | None:
    """ "play X" / "I said X" / "I meant the second one": the corrected
    request, or None when ``text`` doesn't lead with one."""
    m = _REQUEST_LEAD_RE.match(text)
    if m is None:
        return None
    rest = text[m.end():]
    return _ordinal(rest, n) or _as_name(rest)


def parse_choice_reply(transcript: str, n: int) -> ChoiceReply:
    """Classify a reply to a parked "did you mean …?" with ``n`` candidates.

    ``transcript`` is normalized (lower-case, trailing punctuation gone) but
    not filler-stripped. Pure apart from the router's own matchers (the
    yes-words, the filler strip, the fast-path dry run), which it reads."""
    from domovoi.handlers.shared.tool_gate import answers_without_tools
    from domovoi.router import _YES_RE, first_fast_path, strip_leading_filler

    t = transcript.strip()
    if not t or n <= 0:
        return _PASS

    # 1. yes, and whatever follows it.
    m = _YES_RE.match(t)
    if m is not None:
        rest = _strip_punct(t[m.end():])
        if not rest or _ACCEPT_TAIL_RE.match(rest):
            return ChoiceReply("pick", index=0)
        rest = strip_leading_filler(rest)
        hit = _ordinal(rest, n) or _request(rest, n)
        if hit is not None:
            return hit
        # The yes answered it; a command or a question after it ("yes, and
        # turn it up") is not run off the back of it.
        if (
            rest.startswith(("and ", "then ", "also "))
            or first_fast_path(rest) is not None
            or _QUESTION_RE.match(rest)
            or answers_without_tools(rest)
        ):
            return ChoiceReply("pick", index=0)
        named = _as_name(rest)
        return named if named.action == "name" else ChoiceReply("pick", index=0)

    # 2. no, and whatever follows it.
    m = _NO_LEAD_RE.match(t)
    if m is not None:
        rest = _strip_punct(m.group(1))
        if not rest or _DECLINE_TAIL_RE.match(rest):
            return _DECLINE
        rest = strip_leading_filler(rest)
        hit = _ordinal(rest, n) or _request(rest, n)
        if hit is not None:
            return hit if hit.action != "pass" else _DECLINE
        # Never run another command off a "no" ("no, stop" is not "stop").
        if first_fast_path(rest) is not None or _QUESTION_RE.match(rest):
            return _DECLINE
        named = _as_name(rest)
        return named if named.action == "name" else _DECLINE

    # 3. neither: an ordinal, an acceptance, a name — or a new turn.
    t = strip_leading_filler(t)
    if not t:
        return _PASS
    hit = _ordinal(t, n)
    if hit is not None:
        return hit
    if _ACCEPT_RE.match(t):
        return ChoiceReply("pick", index=0)
    if t.startswith(("play ", "put on ")):
        # "play the second one" picks; "play X" is a new request, which the
        # router plays as one.
        picked = _ordinal(_REQUEST_LEAD_RE.sub("", t, count=1), n)
        return picked if picked is not None and picked.action == "pick" else _PASS
    if (
        _NOT_A_NAME_RE.match(t)
        or first_fast_path(t) is not None
        or _QUESTION_RE.match(t)
        or answers_without_tools(t)
    ):
        return _PASS
    return _as_name(t)


# ─── The parked payload ────────────────────────────────────────────────────


def _expired(data: Mapping[str, Any]) -> bool:
    try:
        return time.time() > float(data.get("expires_at"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return True


def _parked_refs(data: Mapping[str, Any]) -> list[EntityRef]:
    refs: list[EntityRef] = []
    for cand in data.get("candidates") or ():
        ref = EntityRef.from_dict(cand.get("ref")) if isinstance(cand, Mapping) else None
        if ref is not None:
            refs.append(ref)
    return refs


class MusicChoiceMixin:
    """MusicHandler's "did you mean …?" (``class MusicHandler(MusicChoiceMixin,
    Handler)``). Relies on MusicHandler for ``name``, ``_play`` and
    ``play_candidate``."""

    confirmation_kinds: tuple[str, ...] = (MUSIC_CHOICE_KIND,)
    choice_kinds: tuple[str, ...] = (MUSIC_CHOICE_KIND,)

    # ── asking ──
    async def ask_did_you_mean(
        self,
        res: Resolution,
        query: Mapping[str, str],
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        """Ask which of ``res.candidates`` was meant, parking them for the
        reply — or None (the caller goes on to today's search) when nobody
        can answer here or asking is switched off."""
        if not settings.music_choice_enabled or ctx.session_id is None or not ctx.answerable:
            return None
        candidates = list(res.candidates[:MAX_CHOICES])
        if not candidates:
            return None
        phrases = [await self._choice_phrase(c, session) for c in candidates[:SPOKEN_CHOICES]]
        if len(phrases) == 1:
            prompt = f"Did you mean {phrases[0]}?"
        else:
            prompt = f"Did you mean {phrases[0]}, or {phrases[1]}?"
        payload = {
            "candidates": [c.to_dict() for c in candidates],
            "query": {k: v for k, v in query.items() if isinstance(v, str)},
            "heard": res.heard,
            "room_id": ctx.room_id,
            "expires_at": time.time() + CHOICE_TTL_SEC,
        }
        try:
            await request_confirmation(
                session, ctx.session_id, kind=MUSIC_CHOICE_KIND,
                handler=self.name, data=payload, prompt=prompt,  # type: ignore[attr-defined]
            )
        except Exception as e:  # noqa: BLE001 — a question nobody can answer is worse than a search
            log.warning("music: couldn't park the did-you-mean question: %s", e)
            return None
        log.info(
            "music: asked %r for %r (%s)", prompt, res.heard,
            ", ".join(f"{c.ref.type}:{c.ref.label}@{c.score:.2f}" for c in candidates),
        )
        return Response(
            text=prompt,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
            expect_followup=True,
            data={"choice": [c.to_dict() for c in candidates]},
        )

    async def _choice_phrase(self, cand: Candidate, session: AsyncSession) -> str:
        """How the question names one candidate: an artist by its spoken
        name, a song as "<song> by <artist>", an album as "the album <x>"
        (+ " by <artist>")."""
        from domovoi.handlers.shared.spoken_names import speakable

        ref = cand.ref
        speak = cand.speak or speakable(ref.label)
        if ref.type in ("artist", "credit"):
            return speak
        artist = await self._artist_speak(ref.artist_label, session)
        if ref.type == "album":
            return f"the album {speak}" + (f" by {artist}" if artist else "")
        return f"{speak} by {artist}" if artist else speak

    @staticmethod
    async def _artist_speak(label: str | None, session: AsyncSession) -> str:
        from domovoi.handlers.shared.spoken_names import alias_key, speakable

        if not label:
            return ""
        key = alias_key(label)
        if not key:
            return speakable(label)
        try:
            return await library_match.speak_for(
                session, EntityRef(type="artist", key=key, label=label)
            ) or speakable(label)
        except Exception as e:  # noqa: BLE001 — the plain spelling is fine
            log.debug("music: speak_for(%r) failed: %s", label, e)
            return speakable(label)

    # ── the reply ──
    async def handle_choice_reply(
        self,
        kind: str,
        data: dict[str, Any],
        transcript: str,
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        if kind != MUSIC_CHOICE_KIND or _expired(data):
            return None
        refs = _parked_refs(data)
        if not refs:
            return None
        reply = parse_choice_reply(transcript, len(refs))
        log.info("music: did-you-mean reply %r → %s", transcript, reply)
        if reply.action == "pass":
            return None
        if reply.action == "decline":
            return self._choice_text(ctx, "OK.")
        if reply.action == "pick":
            return await self._play_parked(refs[reply.index], ctx, session)
        hit = await library_match.match_reply(session, reply.name, refs)
        if hit is not None and 0 <= hit[0] < len(refs):
            return await self._play_parked(refs[hit[0]], ctx, session)
        return await self._play_named(reply.name, ctx, session)

    async def handle_confirmation(
        self,
        kind: str,
        data: dict[str, Any],
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """A plain yes/no to the question, for a caller that has no reply
        text (the router itself hands choices to handle_choice_reply)."""
        if kind != MUSIC_CHOICE_KIND:
            return self._choice_text(ctx, "I'm not sure what you're confirming.")
        if not affirmative:
            return self._choice_text(ctx, "OK.")
        refs = _parked_refs(data)
        if _expired(data) or not refs:
            response = self._choice_text(ctx, _LOST_TRACK_TEXT)
            response.expect_followup = True
            return response
        return await self._play_parked(refs[0], ctx, session)

    async def _play_parked(self, ref: EntityRef, ctx: Context, session: AsyncSession) -> Response:
        cand = await library_match.candidate_from_ref(session, ref)
        if cand is None:
            return self._choice_text(ctx, _GONE_TEXT)
        return await self.play_candidate(cand, ctx, session)  # type: ignore[attr-defined]

    async def _play_named(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        """Play ``name`` as the request "play <name>" would: the same fast
        path — MusicHandler's play (the resolver: plays, asks again, or
        today's search), or a playlist, podcast or station the name is."""
        from domovoi.router import first_fast_path, run_fast_path

        hit = first_fast_path(f"play {name}")
        if hit is None:  # pragma: no cover — MusicHandler's "play (.+)" takes anything
            return await self._play({"any": name}, ctx, session)  # type: ignore[attr-defined]
        handler, fp, m = hit
        return await run_fast_path(handler, fp, m, ctx, session)

    def _choice_text(self, ctx: Context, text: str) -> Response:
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
        )


__all__ = [
    "CHOICE_TTL_SEC",
    "ChoiceReply",
    "MAX_CHOICES",
    "MUSIC_CHOICE_KIND",
    "MusicChoiceMixin",
    "parse_choice_reply",
]
