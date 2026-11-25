"""MemoryHandler — explicit voice capture of long-term personal facts.

Three concerns live here:

1. **Memories** — free-form facts. ``remember that I'm allergic to
   peanuts`` / ``forget that I'm allergic to peanuts`` /
   ``what do you know about me``.
2. **Favorites** — list-shaped preferences by ``kind``.
   ``my favorite team is the Mariners`` / ``what are my favorite
   teams`` / ``forget my favorite team``.
3. **Anonymous-speaker guard** — anything that wants `person_id`
   produces a clear "I don't know who you are yet" prompt rather
   than silently failing or writing orphan rows.
4. **A bare "forget that"** (N15) — see :data:`_FORGET_LAST_RE` and
   :meth:`MemoryHandler._forget_last` for the rule that decides between
   forgetting the memory just saved, dropping a question, standing down,
   and asking "Forget what?".

The implicit-extraction worker writes into the
same ``memories`` table with ``source='implicit', status='pending'``.
The router-side prompt-prefix injection (see ``router.py``) only
pulls ``status='active'`` rows, so a future pending row never leaks
into the QA prompt without the speaker confirming it first.

Fully local (``requires_network="no"``).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.confirmations import PENDING_CONFIRMATION_KEY, request_confirmation
from domovoi.db.repositories import (
    FavoritesRepository,
    MemoriesRepository,
    SessionRepository,
)
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.handlers.dismiss import _ack
from domovoi.house_facts import FORGET_YES, GENERIC_OBJECT, MEMORY_YES, is_generic_object
from domovoi.models import Context, Intent, Response
from domovoi.voice_identifier import UNHEARD, unheard_text

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────

# "remember that I like jazz", "remember this: my anniversary is June 3rd",
# "remember I'm allergic to peanuts". Negative lookahead on "to " carves
# out reminder territory ("remember to call mom" → ReminderHandler's
# domain, not ours).
#
# A generic object is not a fact (G1): "(can you) remember things about me"
# is a question about the ability, and the capability table answers it
# (house_facts, row "memory"). The lookahead keeps this fast path off it, so
# the router keeps the question whole; _add_memory refuses one too (the
# tool router and typed chat reach it without this regex).
_GENERIC_BODY = rf"(?:{GENERIC_OBJECT})(?:,? (?:please|for me|for us|thanks|thank you))?$"
_REMEMBER_RE = re.compile(
    r"^(?:i want you to |i'd like you to |)"
    r"remember (?!to )"
    rf"(?!(?:that |this[:\-]?\s*|the (?:fact|thing) that )?{_GENERIC_BODY})"
    r"(?:that |this[:\-]?\s*|the (?:fact|thing) that )?"
    r"(?P<body>.+)$"
)

# "forget that I like jazz" / "forget the fact about my dog" /
# "stop remembering that I'm allergic" / "stop saving that I work nights".
# Distinct from VoiceProfileHandler's "forget me" — that requires a bare
# "me" / "everything"; this one demands a "that" / "the X" qualifier
# followed by a body. A bare "forget that name" / "forget that alias" /
# "forget that nickname" is the music-names handler's (band 295: undo the
# name just taught), not a memory whose body is the word "name".
_FORGET_MEMORY_RE = re.compile(
    r"^(?:"
    r"forget (?:that (?!(?:alias|name|nickname)$)|the (?:fact|thing|memory|note) (?:about |that ))"
    r"|stop (?:saving|remembering) (?:that |)"
    # "forget that stuff", "stop remembering things": no one memory (G1).
    rf")(?!{_GENERIC_BODY})(?P<body>.+)$"
)

# A BARE forget — no "that <fact>" after it (N15): "forget that", "forget
# it", "forget about it", "forget what I just said", "never mind, forget
# that". Until 2026-11 these were the dismiss handler's ("No worries.") and
# nothing was forgotten, though the person who had just said "remember
# that …" thought it was. Memory owns them now; `_forget_last` decides what
# each one means (the rule is written there). ``lead``: a "never mind" in
# front makes it a brush-off when there is nothing to forget. Listed ahead
# of _FORGET_MEMORY_RE in fast_paths so "forget that i said that" is this.
_FORGET_LAST_RE = re.compile(
    r"^(?P<lead>(?:never ?mind|nvm)[,\s]+)?(?:(?:no|oh|actually|wait|sorry)[,\s]+)?(?:just )?"
    r"forget (?P<what>it|about it|that|about that|this|"
    r"what i (?:just )?(?:said|told you)|(?:that )?i (?:just )?said (?:that|anything))"
    r"(?:,? (?:please|thanks|thank you))?$"
)
# The idiom "forget it" / "forget about it": with nothing to forget, a
# brush-off ("never mind"), as it always was.
_BRUSH_OFF_WHAT = frozenset({"it", "about it"})

# Session-context key: the memory the last "remember that …" (or a "yes" to
# a memory offer) saved in this conversation, so a bare "forget that" on the
# very next turn can forget exactly it. ``at`` is when it was saved; the
# conversation's recent turns say whether that save is the previous turn.
LAST_SAVED_KEY = "last_memory_saved"
# "Forget what?": a parked choice whose reply names the memory.
FORGET_WHAT_KIND = "core.forget_what"
_FORGET_WHAT_TTL_SEC = 120
_SAVED_REPLY = "Got it — I'll remember that."
_FORGET_WHAT_TEXT = 'Forget what? Tell me which thing, like "that I like jazz".'
# A reply to "Forget what?" that drops the question.
_NEVER_MIND_RE = re.compile(
    r"^(?:no|nope|nah|nothing|never ?mind|nvm|forget it|forget about it|cancel|"
    r"nothing,? never ?mind|no,? never ?mind|that's all|i'm good)(?:,? thanks|,? thank you)?$"
)
# What leads the fact in a reply to "Forget what?": "that I like jazz",
# "the one about my dog", "I meant that I like jazz".
_FORGET_WHAT_LEAD_RE = re.compile(
    r"^(?:(?:i mean|i meant|i said)[,\s]+)?"
    r"(?:that |about |the (?:one|fact|thing|memory|note) (?:about |that )|the )?"
)


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _saved_last_turn(context: dict) -> dict | None:
    """The memory the PREVIOUS turn of this conversation saved, or None.

    The handler marks a save in the session context (:data:`LAST_SAVED_KEY`,
    with when it happened) before the router records the turn; the router
    then appends that turn to ``recent_turns``. So the save is the previous
    turn exactly when the newest recorded exchange came after the mark, the
    one before it came before the mark, and what was said last is the save's
    own reply. Any turn in between — a song, a question, a second "forget
    that" — fails one of the three, and nothing is forgotten on a guess."""
    mark = context.get(LAST_SAVED_KEY)
    if not isinstance(mark, dict):
        return None
    if not isinstance(mark.get("memory_id"), int) or not isinstance(mark.get("person_id"), int):
        return None
    saved_at = _parse_at(mark.get("at"))
    if saved_at is None:
        return None
    if context.get("last_assistant_response") != mark.get("reply"):
        return None
    turns = [t for t in (context.get("recent_turns") or []) if isinstance(t, dict)]
    if len(turns) < 2:
        return None
    newest = _parse_at(turns[-1].get("at"))
    if newest is None or newest < saved_at:
        return None
    if len(turns) >= 3:
        before = _parse_at(turns[-3].get("at"))
        if before is None or before >= saved_at:
            return None
    return mark


def _same_speaker(ctx: Context, owner: int) -> bool:
    """Whether this turn may forget a memory saved for ``owner``.

    A recognised voice must BE the owner. A turn whose voice was never
    scored — a bare "forget that" is usually too short to tell who's
    talking (``voice_identifier.UNHEARD``) — or a typed one with no voice
    at all, is the owner's: it is the very next turn of the same
    conversation, which is how a one-word "yes" to a memory offer is taken
    as the person it was offered to. A voice that WAS scored and matched
    nobody, or was too close to call between people, is not."""
    if ctx.person_id is not None:
        return ctx.person_id == owner
    return ctx.voice_outcome is None or ctx.voice_outcome in UNHEARD


# "my favorite team is the Mariners" / "favorite genre is jazz" /
# "my favorite sports are basketball and tennis".
# ``kind`` is one or two words to avoid swallowing the value into the kind.
_FAVORITE_ADD_RE = re.compile(
    r"^(?:my |our |)"
    r"favorite (?P<kind>\w+(?:\s\w+)?)"
    r"\s+(?:is|are)\s+"
    r"(?P<value>.+)$"
)

# "forget my favorite team", "forget my favorite genres". Plural is
# accepted but normalized to singular by the handler.
_FAVORITE_FORGET_RE = re.compile(
    r"^forget (?:my |our |)favorite (?P<kind>\w+(?:\s\w+)?)$"
)

# "what do you remember about me" / "what do you know about me" /
# "what did i tell you to remember".
_LIST_MEMORIES_RE = re.compile(
    r"^(?:"
    r"what do you (?:remember|know) about me"
    r"|what did i tell you to remember"
    r"|what (?:memories|things) do you have (?:about|on) me"
    r")$"
)

# "what are my favorites" / "what's my favorite team" / "what are my
# favorite genres" — kind is optional; bare "favorites" lists everything.
# Splits ``what's`` and ``what (are|is)`` into separate alternatives because
# ``what's`` has no space between ``what`` and the contraction.
_LIST_FAVORITES_RE = re.compile(
    r"^(?:what (?:are|is)|what's)\s+(?:my |our |)"
    r"favorites?"
    r"(?:\s+(?P<kind>\w+(?:\s\w+)?)s?)?"
    r"$"
)


def _singularize(kind: str) -> str:
    """Loose singularizer — "teams" → "team", "favorites" → "favorite".

    Voice favorites get spoken plural ("what are my favorite teams")
    but stored singular ("team"). A real Inflector would be overkill;
    we only need to strip trailing 's' on words that end in two or
    more letters. Handles the household-favorites vocabulary cleanly:
    teams, genres, artists, foods, sports, hobbies."""
    k = kind.strip().lower()
    if len(k) > 3 and k.endswith("s") and not k.endswith("ss"):
        return k[:-1]
    return k


def _format_favorites(rows: list[tuple[int, str, str, int]]) -> str:
    """Render the "what are my favorites" response.

    Groups by kind, lists values comma-separated. Plural in the kind
    label only when there are 2+ values for that kind."""
    if not rows:
        return ""
    by_kind: dict[str, list[str]] = {}
    for _id, kind, value, _rank in rows:
        by_kind.setdefault(kind, []).append(value)
    parts: list[str] = []
    for kind, values in by_kind.items():
        label = kind + ("s" if len(values) > 1 else "")
        parts.append(f"{label}: {', '.join(values)}")
    return "; ".join(parts)


class MemoryHandler(Handler):
    name = "memory"
    # band rationale: after voice_notes (230) so "jot down ___" / "what was my last note"
    #   win their canonical phrasing; memory vocabulary starts with
    #   "remember", "forget", "my favorite" — none collide. Its bare
    #   "forget that" / "forget it" (N15) moved here from dismiss (100):
    #   nothing in 100-239 takes a "forget" turn but voice_profile's
    #   "forget me", which these never are.
    priority_band = 240
    display = HandlerDisplay(label="Memory", tone="info")
    example_phrases = (
        "remember that I parked on level 3",
        "what do you remember about me",
    )
    confirmation_kinds = ("core.pending_memory_offer", FORGET_WHAT_KIND)
    choice_kinds = (FORGET_WHAT_KIND,)
    requires_network = "no"
    tool_schema = {
        "name": "memory",
        "description": (
            "Remember, forget, or recall personal facts and favorites "
            "about the current speaker. Used when the user wants to "
            "manage long-term personal context: 'remember that X', "
            "'forget that X', 'my favorite Y is Z', 'what do you "
            "remember about me'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "add_memory",
                        "forget_memory",
                        "list_memories",
                        "add_favorite",
                        "forget_favorite",
                        "list_favorites",
                    ],
                },
                "body": {
                    "type": "string",
                    "description": "Memory body or fragment to match.",
                },
                "kind": {
                    "type": "string",
                    "description": "Favorite kind (team, genre, etc.).",
                },
                "value": {
                    "type": "string",
                    "description": "Favorite value.",
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        # Unbound functions, not bound methods: the router calls
        # ``fp.method(handler, m, ctx, session)`` and passes the handler
        # itself. A bound method here receives the handler as ``m`` and
        # blows up with a TypeError on every turn (F-V002).
        self.fast_paths = [
            FastPath(_REMEMBER_RE, MemoryHandler._add_memory_from_match),
            # Tier B, as these phrases were under dismiss: "forget that" is
            # also how "forget that I like jazz" starts, so a pause after it
            # gets the longer hold (domovoi/early_commit.py).
            FastPath(_FORGET_LAST_RE, MemoryHandler._forget_last_from_match, early_commit="B"),
            FastPath(_FORGET_MEMORY_RE, MemoryHandler._forget_memory_from_match),
            FastPath(_FAVORITE_ADD_RE, MemoryHandler._add_favorite_from_match),
            FastPath(_FAVORITE_FORGET_RE, MemoryHandler._forget_favorite_from_match),
            FastPath(_LIST_MEMORIES_RE, MemoryHandler._list_memories_from_match),
            FastPath(_LIST_FAVORITES_RE, MemoryHandler._list_favorites_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        """Fallthrough path — shouldn't be called in practice; the
        fast-paths above and the LLM tool routing cover every entry."""
        return Response(
            text="Try saying 'remember that ___' or 'what do you know about me'.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def handle_confirmation(
        self,
        kind: str,
        data: dict,
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """Resume a flow the previous turn parked.

        Currently handles a single ``kind``:

        * ``pending_memory_offer`` — the router surfaced a memory
          the implicit-extractor worker generated. Yes promotes
          ``status='pending' → 'active'`` (and it auto-feeds into the
          QA prompt prefix from the next turn); no marks it
          ``rejected`` so the extractor won't surface the same fact
          again.
        """
        if kind == FORGET_WHAT_KIND:
            # A yes/no to "Forget what?" names nothing: a "no" drops it, a
            # "yes" gets the question again.
            if affirmative:
                return Response(
                    text=_FORGET_WHAT_TEXT,
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                )
            return Response(text=_ack(), session_id=ctx.session_id, matched_handler=self.name)
        if kind != "core.pending_memory_offer":
            return Response(
                text="Got it.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        memory_id = data.get("memory_id")
        # A one-word "yes" is too short for voice ID, so the reply turn
        # usually has no speaker: the person it was offered to answers it.
        person_id = ctx.person_id if ctx.person_id is not None else data.get("person_id")
        if not isinstance(memory_id, int) or not isinstance(person_id, int):
            return Response(
                text="Got it.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        repo = MemoriesRepository(session)
        new_status = "active" if affirmative else "rejected"
        updated = await repo.set_status(memory_id, person_id, new_status)
        if updated == 0:
            # Memory was deleted between the offer and the yes/no —
            # acknowledge softly and move on.
            return Response(
                text="Got it.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if affirmative:
            # The extractor's fact is a saved memory now: a "forget that"
            # on the next turn takes it back.
            await self._mark_saved(ctx, session, memory_id, person_id)
            return Response(
                text=_SAVED_REPLY,
                session_id=ctx.session_id,
                matched_handler=self.name,
                data={"memory_id": memory_id, "status": "active"},
            )
        return Response(
            text="OK, I won't save that.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"memory_id": memory_id, "status": "rejected"},
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        """LLM tool-call dispatch — bridges qwen's tool-routing path
        into the same handlers the regex fast-paths use."""
        action = (args.get("action") or "").strip()
        if action == "add_memory":
            return await self._add_memory(args.get("body") or "", ctx, session)
        if action == "forget_memory":
            return await self._forget_memory(args.get("body") or "", ctx, session)
        if action == "list_memories":
            return await self._list_memories(ctx, session)
        if action == "add_favorite":
            return await self._add_favorite(
                args.get("kind") or "", args.get("value") or "", ctx, session
            )
        if action == "forget_favorite":
            return await self._forget_favorite(args.get("kind") or "", ctx, session)
        if action == "list_favorites":
            return await self._list_favorites(args.get("kind"), ctx, session)
        return Response(
            text=f"I don't know how to {action} a memory.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Guard ────────────────────────────────────────────────────────

    async def _ensure_person(self, ctx: Context) -> Response | None:
        """Anon-speaker bail-out. Memories without a ``person_id`` would
        have nowhere to attach (the FK is NOT NULL); rather than
        silently writing a global row, we tell the speaker how to
        identify themselves first."""
        if ctx.person_id is None:
            # A voice that was never scored (too short to tell) isn't an
            # unknown one: asking them to introduce themselves would only
            # get "I already know you".
            unheard = unheard_text(ctx.voice_outcome)
            return Response(
                text=unheard or (
                    "I don't know who you are yet — say 'my name is "
                    "so-and-so' and then I can start remembering "
                    "things for you."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        return None

    # ─── Memories ─────────────────────────────────────────────────────

    async def _add_memory_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._add_memory(m.group("body"), ctx, session)

    async def _add_memory(
        self, body: str, ctx: Context, session: AsyncSession
    ) -> Response:
        if is_generic_object(body):
            # "things about me" is no fact (G1): the ability is explained,
            # nothing is saved.
            return Response(text=MEMORY_YES, session_id=ctx.session_id, matched_handler=self.name)
        guard = await self._ensure_person(ctx)
        if guard is not None:
            return guard
        body = (body or "").strip().rstrip(".,!?")
        if not body:
            return Response(
                text="What should I remember?",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        repo = MemoriesRepository(session)
        memory_id = await repo.add(
            person_id=ctx.person_id,  # type: ignore[arg-type]  # guarded above
            body=body,
            source="explicit",
            status="active",
        )
        await self._mark_saved(ctx, session, memory_id, ctx.person_id)  # type: ignore[arg-type]
        return Response(
            text=_SAVED_REPLY,
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"memory_id": memory_id, "body": body},
        )

    async def _forget_memory_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._forget_memory(m.group("body"), ctx, session)

    async def _forget_memory(
        self, fragment: str, ctx: Context, session: AsyncSession
    ) -> Response:
        if is_generic_object(fragment):
            # "stuff" would fuzzy-match any memory (G1): nothing is deleted.
            return Response(text=FORGET_YES, session_id=ctx.session_id, matched_handler=self.name)
        guard = await self._ensure_person(ctx)
        if guard is not None:
            return guard
        fragment = (fragment or "").strip().rstrip(".,!?")
        if not fragment:
            return Response(
                text="What should I forget?",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        repo = MemoriesRepository(session)
        matches = await repo.find_fuzzy(
            ctx.person_id, fragment, limit=5  # type: ignore[arg-type]
        )
        if not matches:
            return Response(
                text=f"I don't have anything saved about \"{fragment}\".",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        if len(matches) > 1:
            # Multiple candidate rows — refuse to guess. Voice surface
            # can't easily disambiguate via numbered list; tell the
            # speaker to be more specific.
            return Response(
                text=(
                    f"I have a few things matching {fragment}. "
                    f"Say a bit more of the one to forget."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
                data={"candidates": [{"id": mid, "body": b} for mid, b in matches]},
            )
        memory_id, body = matches[0]
        await repo.delete(memory_id, ctx.person_id)  # type: ignore[arg-type]
        return Response(
            text=f"OK, forgotten: {body}",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"deleted_memory_id": memory_id, "body": body},
        )

    # ─── A bare "forget that" (N15) ────────────────────────────────────

    async def _mark_saved(
        self, ctx: Context, session: AsyncSession, memory_id: int, person_id: int
    ) -> None:
        """Note the memory this turn saved, for a "forget that" next turn
        (:func:`_saved_last_turn`)."""
        if ctx.session_id is None:
            return
        await SessionRepository(session).set_context_key(
            ctx.session_id,
            LAST_SAVED_KEY,
            {
                "memory_id": memory_id,
                "person_id": person_id,
                "reply": _SAVED_REPLY,
                "at": datetime.now(tz=timezone.utc).isoformat(),
            },
        )

    async def _forget_last_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        brush_off = bool(m.group("lead")) or m.group("what") in _BRUSH_OFF_WHAT
        return await self._forget_last(brush_off, ctx, session)

    async def _forget_last(
        self, brush_off: bool, ctx: Context, session: AsyncSession
    ) -> Response:
        """A bare "forget that" / "forget it" / "forget what I just said" /
        "never mind, forget that". The rule, in this order:

        1. The previous turn of this conversation saved a memory ("remember
           that …", or a yes to a memory the extractor offered): forget
           exactly that one — "OK, forgotten: <fact>." — when this turn is
           its owner's (:func:`_same_speaker`); another recognised person
           is told it isn't theirs to forget, and nothing is deleted.
        2. The previous turn asked a question that is still waiting (a
           parked confirmation, and the reply ended in "?"): the forget
           drops the question. A memory offer ("…Should I remember that?")
           is turned down exactly as a "no" would; any other question is
           cleared so a later "yes" can't act on it, and gets a plain
           "Okay." A parked CHOICE never gets here: its own reply reader
           has the turn first (router).
        3. "forget it" / "forget about it" / a "never mind" in front, with
           nothing above: a brush-off, as the dismiss handler always
           answered it.
        4. Anything else ("forget that", "forget what I just said"): asks
           "Forget what?" — never "No worries.", which would let the person
           think something was forgotten. The reply is read as the thing to
           forget (:meth:`handle_choice_reply`).
        """
        repo = SessionRepository(session)
        context: dict = {}
        if ctx.session_id is not None:
            context = await repo.get_context(ctx.session_id) or {}

        mark = _saved_last_turn(context)
        if mark is not None:
            owner = int(mark["person_id"])
            memory_id = int(mark["memory_id"])
            memories = MemoriesRepository(session)
            body = next(
                (b for i, b, _t, _s in await memories.list_active(owner) if i == memory_id),
                None,
            )
            if body is not None:
                if not _same_speaker(ctx, owner):
                    return Response(
                        text=(
                            "I can only forget that for the person who asked me "
                            "to remember it."
                        ),
                        session_id=ctx.session_id,
                        matched_handler=self.name,
                    )
                await memories.delete(memory_id, owner)
                await repo.set_context_key(ctx.session_id, LAST_SAVED_KEY, None)  # type: ignore[arg-type]
                return Response(
                    text=f"OK, forgotten: {body}.",
                    session_id=ctx.session_id,
                    matched_handler=self.name,
                    data={"deleted_memory_id": memory_id, "body": body},
                )

        pending = context.get(PENDING_CONFIRMATION_KEY)
        last_reply = str(context.get("last_assistant_response") or "").rstrip()
        if isinstance(pending, dict) and pending.get("kind") and last_reply.endswith("?"):
            await repo.set_context_key(
                ctx.session_id, PENDING_CONFIRMATION_KEY, None  # type: ignore[arg-type]
            )
            if pending.get("kind") == "core.pending_memory_offer" and pending.get("handler") == self.name:
                return await self.handle_confirmation(
                    "core.pending_memory_offer", pending, False, ctx, session
                )
            return Response(
                text=_ack(),
                session_id=ctx.session_id,
                matched_handler=self.name,
                data={"dropped_question": pending.get("kind")},
            )

        if brush_off:
            return Response(text=_ack(), session_id=ctx.session_id, matched_handler=self.name)

        if ctx.session_id is not None:
            await request_confirmation(
                session,
                ctx.session_id,
                kind=FORGET_WHAT_KIND,
                handler=self.name,
                data={"person_id": ctx.person_id, "expires_at": time.time() + _FORGET_WHAT_TTL_SEC},
                prompt="Forget what?",
            )
        return Response(
            text=_FORGET_WHAT_TEXT,
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=ctx.session_id is not None,
        )

    async def handle_choice_reply(
        self,
        kind: str,
        data: dict,
        transcript: str,
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        """The reply to "Forget what?". A "never mind" drops it; a fact the
        speaker has saved is forgotten (the same match "forget that <fact>"
        makes: one match deletes, several ask for more). A whole forget
        command, or a reply that matches none of their memories ("what
        time is it"), is not an answer to the question: it is routed as a
        new turn (None)."""
        if kind != FORGET_WHAT_KIND or float(data.get("expires_at") or 0) < time.time():
            return None
        said = (transcript or "").strip()
        if _NEVER_MIND_RE.match(said):
            return Response(text=_ack(), session_id=ctx.session_id, matched_handler=self.name)
        if said.startswith("forget "):
            # A whole forget command: the fast paths take it as one.
            return None
        asked_by = data.get("person_id")
        if not isinstance(asked_by, int):
            # Asked of nobody we know: nothing of theirs to look in.
            return None
        if ctx.person_id is None and _same_speaker(ctx, asked_by):
            ctx = ctx.model_copy(update={"person_id": asked_by})
        if ctx.person_id != asked_by:
            return None
        fragment = _FORGET_WHAT_LEAD_RE.sub("", said, count=1).strip().rstrip(".,!?")
        if not fragment:
            return None
        if not await MemoriesRepository(session).find_fuzzy(asked_by, fragment, limit=1):
            return None
        return await self._forget_memory(fragment, ctx, session)

    async def _list_memories_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._list_memories(ctx, session)

    async def _list_memories(
        self, ctx: Context, session: AsyncSession
    ) -> Response:
        guard = await self._ensure_person(ctx)
        if guard is not None:
            return guard
        rows = await MemoriesRepository(session).list_active(
            ctx.person_id  # type: ignore[arg-type]
        )
        if not rows:
            return Response(
                text=(
                    "I don't have anything saved about you yet — tell "
                    "me something and I'll remember it."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        # Voice format: short conversational lead-in + period-joined
        # bodies. Caps the spoken length at the first 5 items so the
        # response doesn't run several seconds; full list lives in
        # response.data for the web UI / debugging.
        bodies = [b for _id, b, _topic, _src in rows]
        spoken = bodies[:5]
        suffix = ""
        if len(bodies) > 5:
            suffix = f" And {len(bodies) - 5} more — check the web UI for the full list."
        return Response(
            text="Here's what I remember: " + ". ".join(spoken) + "." + suffix,
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"memories": [{"id": i, "body": b} for i, b, _t, _s in rows]},
        )

    # ─── Favorites ────────────────────────────────────────────────────

    async def _add_favorite_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._add_favorite(
            m.group("kind"), m.group("value"), ctx, session
        )

    async def _add_favorite(
        self, kind: str, value: str, ctx: Context, session: AsyncSession
    ) -> Response:
        guard = await self._ensure_person(ctx)
        if guard is not None:
            return guard
        kind = _singularize(kind)
        value = (value or "").strip().rstrip(".,!?")
        if not kind or not value:
            return Response(
                text="Try 'my favorite team is the Mariners'.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        fav_id = await FavoritesRepository(session).add(
            person_id=ctx.person_id,  # type: ignore[arg-type]
            kind=kind,
            value=value,
        )
        return Response(
            text=f"Got it — your favorite {kind} is {value}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"favorite_id": fav_id, "kind": kind, "value": value},
        )

    async def _forget_favorite_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return await self._forget_favorite(m.group("kind"), ctx, session)

    async def _forget_favorite(
        self, kind: str, ctx: Context, session: AsyncSession
    ) -> Response:
        guard = await self._ensure_person(ctx)
        if guard is not None:
            return guard
        kind = _singularize(kind)
        if not kind:
            return Response(
                text="Which favorite should I forget?",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        repo = FavoritesRepository(session)
        rows = await repo.list_for_person(
            ctx.person_id, kind=kind  # type: ignore[arg-type]
        )
        if not rows:
            return Response(
                text=f"I don't have any favorite {kind} saved for you.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        deleted = 0
        for r_id, _kind, _val, _rank in rows:
            deleted += await repo.delete(r_id, ctx.person_id)  # type: ignore[arg-type]
        plural = "s" if deleted != 1 else ""
        return Response(
            text=f"OK, cleared {deleted} favorite {kind}{plural}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={"deleted_kind": kind, "count": deleted},
        )

    async def _list_favorites_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        kind_raw = m.group("kind")
        kind = _singularize(kind_raw) if kind_raw else None
        return await self._list_favorites(kind, ctx, session)

    async def _list_favorites(
        self, kind: str | None, ctx: Context, session: AsyncSession
    ) -> Response:
        guard = await self._ensure_person(ctx)
        if guard is not None:
            return guard
        rows = await FavoritesRepository(session).list_for_person(
            ctx.person_id,  # type: ignore[arg-type]
            kind=kind,
        )
        if not rows:
            scope = f" {kind}" if kind else ""
            return Response(
                text=f"I don't have any favorite{scope} saved for you yet.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        body = _format_favorites(rows)
        return Response(
            text=f"Your favorites — {body}.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            data={
                "favorites": [
                    {"id": i, "kind": k, "value": v, "rank": r}
                    for i, k, v, r in rows
                ]
            },
        )
