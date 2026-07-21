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

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.db.repositories import (
    FavoritesRepository,
    MemoriesRepository,
    PeopleRepository,
)
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────

# "remember that I like jazz", "remember this: my anniversary is June 3rd",
# "remember I'm allergic to peanuts". Negative lookahead on "to " carves
# out reminder territory ("remember to call mom" → ReminderHandler's
# domain, not ours).
_REMEMBER_RE = re.compile(
    r"^(?:i want you to |i'd like you to |)"
    r"remember (?!to )"
    r"(?:that |this[:\-]?\s*|the (?:fact|thing) that )?"
    r"(?P<body>.+)$"
)

# "forget that I like jazz" / "forget the fact about my dog" /
# "stop remembering that I'm allergic" / "stop saving that I work nights".
# Distinct from VoiceProfileHandler's "forget me" — that requires a bare
# "me" / "everything"; this one demands a "that" / "the X" qualifier
# followed by a body.
_FORGET_MEMORY_RE = re.compile(
    r"^(?:"
    r"forget (?:that |the (?:fact|thing|memory|note) (?:about |that ))"
    r"|stop (?:saving|remembering) (?:that |)"
    r")(?P<body>.+)$"
)

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
    #   "remember", "forget", "my favorite" — none collide.
    priority_band = 240
    display = HandlerDisplay(label="Memory", tone="info")
    confirmation_kinds = ("core.pending_memory_offer",)
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
        self.fast_paths = [
            FastPath(_REMEMBER_RE, self._add_memory_from_match),
            FastPath(_FORGET_MEMORY_RE, self._forget_memory_from_match),
            FastPath(_FAVORITE_ADD_RE, self._add_favorite_from_match),
            FastPath(_FAVORITE_FORGET_RE, self._forget_favorite_from_match),
            FastPath(_LIST_MEMORIES_RE, self._list_memories_from_match),
            FastPath(_LIST_FAVORITES_RE, self._list_favorites_from_match),
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
        if kind != "core.pending_memory_offer":
            return Response(
                text="Got it.",
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        memory_id = data.get("memory_id")
        person_id = ctx.person_id
        if not isinstance(memory_id, int) or person_id is None:
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
            return Response(
                text="Got it — I'll remember that.",
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
            return Response(
                text=(
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
        guard = await self._ensure_person(ctx)
        if guard is not None:
            return guard
        body = (body or "").strip().rstrip(".,!?")
        if not body:
            return Response(
                text="Remember what?",
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
        return Response(
            text="Got it — I'll remember that.",
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
        guard = await self._ensure_person(ctx)
        if guard is not None:
            return guard
        fragment = (fragment or "").strip().rstrip(".,!?")
        if not fragment:
            return Response(
                text="Forget what?",
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
                    f"I have a few things matching \"{fragment}\" — "
                    f"can you be more specific?"
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
                text="Forget which favorite?",
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
