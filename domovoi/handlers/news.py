"""NewsHandler — voice surface for the News feature.

``requires_network = "degraded"``: reading cached items + the last briefing
works fully offline; only *fetching* (verbal fetch verbs, ad-hoc discovery)
needs the network, and those degrade to a graceful spoken refusal via
``fallback_offline`` + inline ``ctx.online`` checks (mirroring RadioHandler).

Three asks (each returns ``news_items_per_ask`` items unless a count override
is spoken):

  1. GENERAL BRIEFING — "what's the news today" → top-N local + national +
     global from the pre-fetched house scopes. No network, no confirm.
  2. MY NEWS (topics-of-interest) — "what's my news" → the STATUS-FIRST
     digest: unread count + oldest + newest, then offers read-latest /
     fetch-new / read-oldest. Never a blind dump. Falls back to the general
     briefing for an unidentified speaker (house default; pull-only, low
     stakes).
  3. AD-HOC SUBJECT — "any news about <subject>" → cached items for the
     subject (read verb, no confirm) OR a single internet confirm then a live
     fetch (fetch verb).

Read verb vs fetch verb decides whether the internet is touched:
  * READ ("give me / read me the latest on X") → cached only, reads now.
  * FETCH ("fetch / go get the latest on X", or the digest's "fetch new")
    → ONE ``pending_confirmation`` internet confirm, then fetch + read.

Plus: count editing by voice ("give me 5 stories"), and "favorite that"
(favorites the last item read → retention-exempt).

Ordering: NewsHandler sits ahead of the greedy media handlers (music /
library / provider plugins) — its regexes are all anchored on news vocabulary
(news / headlines / stories / "the latest on") so they can't poach "play X".
It never claims a bare "stop" / "next" (those stay with music) — digest
barge-in for "next"/"stop" is the satellite's VAD interrupt during TTS, a
streaming-layer concern (see INTEGRATION_news.md), not a handler regex.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import news_service
from domovoi.config import settings
from domovoi.db.repositories import SessionRepository
from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# Verbs that mean "go get it" (fetch → confirm) vs "read what you have"
# (read → cached, no confirm).
_FETCH_VERBS = {
    "fetch", "go get", "go and get", "get me", "get", "pull up", "pull",
    "grab", "download", "go fetch",
}


def _is_fetch_verb(verb: str | None) -> bool:
    return (verb or "").strip().lower() in _FETCH_VERBS


# ─── General briefing ────────────────────────────────────────────────────
_BRIEFING_RE = re.compile(
    r"^(?:what'?s|what is|give me|read me|tell me|catch me up on|"
    r"what's happening in|what is happening in)\s+"
    r"(?:the\s+)?(?:(?:latest|newest|most\s+recent|recent|breaking)\s+)?"
    r"news(?:\s+today|\s+for today)?$"
)
_HEADLINES_RE = re.compile(
    r"^(?:read me|give me|tell me)?\s*(?:the\s+)?(?:top\s+)?headlines(?:\s+today)?$"
)

# ─── My news (topics digest) ─────────────────────────────────────────────
_MY_NEWS_RE = re.compile(
    r"^(?:what'?s|what is|whats)?\s*my news$"
)
_MY_NEWS_ALT_RE = re.compile(
    r"^(?:what'?s|whats|what is)\s+new\s+(?:on|in|with)\s+my\s+"
    r"(?:topics|interests|news)$"
    r"|^any news (?:for|about) me$"
)

# ─── Digest choices ──────────────────────────────────────────────────────
_READ_LATEST_RE = re.compile(
    r"^read(?:\s+me)?\s+(?:the\s+)?(?:my\s+)?latest(?:\s+"
    r"(?:news|stories|ones|headlines))?$"
)
_READ_OLDEST_RE = re.compile(
    r"^read(?:\s+me)?\s+(?:the\s+)?(?:my\s+)?oldest(?:\s+"
    r"(?:news|stories|ones|first))?$"
)
_FETCH_NEW_RE = re.compile(
    r"^(?:fetch|get|pull|grab|go get)(?:\s+me)?\s+(?:the\s+)?new"
    r"(?:\s+(?:ones|stories|news|items))?$"
)

# ─── Ad-hoc subject (family A: explicit news noun before the preposition) ─
_SUBJECT_A_RE = re.compile(
    r"^(?P<verb>fetch|go and get|go get|go fetch|get me|get|pull up|pull|grab|"
    r"download|give me|read me|read|tell me|what'?s|what is|whats|any)?\s*"
    r"(?:me\s+)?(?:the\s+)?(?:(?P<count>\d+)\s+)?"
    r"(?:latest\s+|recent\s+|newest\s+)?"
    r"(?:news|stories|headlines|updates)\s+"
    r"(?:about|on|regarding|for|in|from)\s+(?P<subject>.+)$"
)
# Family B: "the latest on X" (news noun implied by latest/newest/recent).
_SUBJECT_B_RE = re.compile(
    r"^(?P<verb>fetch|go and get|go get|go fetch|get me|get|pull up|pull|grab|"
    r"download|give me|read me|read|tell me|what'?s|what is|whats)?\s*"
    r"(?:me\s+)?(?:the\s+)?(?:(?P<count>\d+)\s+)?"
    r"(?:latest|newest|recent)\s+(?:news\s+)?"
    r"(?:on|about|for|regarding)\s+(?P<subject>.+)$"
)

# ─── Category ────────────────────────────────────────────────────────────
_CATEGORY_RE = re.compile(
    r"^(?:what'?s|whats|what is)\s+new\s+in\s+(?:the\s+)?(?P<cat>.+)$"
    r"|^(?:any\s+)?news\s+in\s+(?:the\s+)?(?P<cat2>.+)$"
)

# ─── Count edit ──────────────────────────────────────────────────────────
_COUNT_EDIT_RE = re.compile(
    r"^(?:give me|read me|set (?:my )?news to|make it|i want|use|show me|"
    r"change (?:my )?news to)\s+(?P<count>\d+)\s+"
    r"(?:stories|items|articles|headlines)(?:\s+(?:at a time|per ask|each))?$"
)

# ─── Favorite that ───────────────────────────────────────────────────────
_FAVORITE_RE = re.compile(
    r"^(?:favorite|save|bookmark|star|keep)\s+(?:that|this|it|that one|"
    r"that story|that article)$"
)


class NewsHandler(Handler):
    name = "news"
    # band rationale: before all greedy media. Every regex is anchored on news vocabulary
    #   ("news" / "headlines" / "stories" / "the latest on <subject>"), so
    #   it can't poach "play X" (music's catch-all) or "play the latest
    #   <show>" (spoken_audio — starts with "play", which news never
    #   claims). It deliberately does NOT claim a bare "stop" / "next".
    priority_band = 260
    display = HandlerDisplay(label="News", tone="info")
    confirmation_kinds = ("core.news_fetch", "core.news_fetch_topics")
    requires_network = "degraded"

    tool_schema = {
        "name": "news",
        "description": (
            "Read the news — a general local/national/global briefing, the "
            "user's personal topics-of-interest digest, or news about any "
            "subject. Use for 'what's the news', 'what's my news', 'any news "
            "about X'. A 'fetch'/'go get' verb pulls fresh from the internet "
            "(with confirmation); 'give me'/'read me' reads what's cached."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["briefing", "my_news", "subject"],
                },
                "subject": {
                    "type": "string",
                    "description": "Topic/place for action=subject.",
                },
                "fetch": {
                    "type": "boolean",
                    "description": "True to fetch fresh (confirms first); "
                    "false to read cached.",
                },
                "count": {"type": "integer"},
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_COUNT_EDIT_RE, NewsHandler._count_edit_from_match),
            FastPath(_FAVORITE_RE, NewsHandler._favorite_from_match),
            FastPath(_BRIEFING_RE, NewsHandler._briefing_from_match),
            FastPath(_HEADLINES_RE, NewsHandler._briefing_from_match),
            FastPath(_MY_NEWS_RE, NewsHandler._my_news_from_match),
            FastPath(_MY_NEWS_ALT_RE, NewsHandler._my_news_from_match),
            FastPath(_READ_LATEST_RE, NewsHandler._read_latest_from_match),
            FastPath(_READ_OLDEST_RE, NewsHandler._read_oldest_from_match),
            FastPath(_FETCH_NEW_RE, NewsHandler._fetch_new_from_match),
            FastPath(_SUBJECT_A_RE, NewsHandler._subject_from_match),
            FastPath(_SUBJECT_B_RE, NewsHandler._subject_from_match),
            FastPath(_CATEGORY_RE, NewsHandler._category_from_match),
        ]

    # ─── Fast-path adapters ─────────────────────────────────────────────

    async def _count_edit_from_match(self, m, ctx, session):
        return await self._set_count(int(m.group("count")), ctx)

    async def _favorite_from_match(self, m, ctx, session):
        return await self._favorite_last(ctx, session)

    async def _briefing_from_match(self, m, ctx, session):
        return await self._general_briefing(ctx, session, count=None)

    async def _my_news_from_match(self, m, ctx, session):
        return await self._my_news_digest(ctx, session)

    async def _read_latest_from_match(self, m, ctx, session):
        return await self._read_my_items(ctx, session, order="newest", count=None)

    async def _read_oldest_from_match(self, m, ctx, session):
        return await self._read_my_items(ctx, session, order="oldest", count=None)

    async def _fetch_new_from_match(self, m, ctx, session):
        return await self._confirm_topics_fetch(ctx, session)

    async def _subject_from_match(self, m, ctx, session):
        verb = m.groupdict().get("verb")
        count = m.groupdict().get("count")
        subject = (m.group("subject") or "").strip()
        n = int(count) if count else None
        if _is_fetch_verb(verb):
            return await self._confirm_subject_fetch(subject, n, ctx, session)
        return await self._read_subject(subject, n, ctx, session)

    async def _category_from_match(self, m, ctx, session):
        cat = (m.group("cat") or m.group("cat2") or "").strip()
        return await self._read_subject(cat, None, ctx, session)

    # ─── Entry points (execute / tool / offline) ────────────────────────

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        # No fast path matched but the router sent us here — treat as a
        # general briefing.
        return await self._general_briefing(ctx, session, count=None)

    async def execute_from_tool(
        self, args: dict[str, Any], ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        count = args.get("count")
        n = int(count) if isinstance(count, int) else None
        if action == "my_news":
            return await self._my_news_digest(ctx, session)
        if action == "subject":
            subject = (args.get("subject") or "").strip()
            if not subject:
                return self._reply(ctx, "News about what?")
            if args.get("fetch") and ctx.online:
                return await self._confirm_subject_fetch(subject, n, ctx, session)
            return await self._read_subject(subject, n, ctx, session)
        return await self._general_briefing(ctx, session, count=n)

    async def fallback_offline(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        """Offline: read whatever's cached; refuse fetches gracefully. The
        router only auto-routes here on requires_network='yes' handlers, so
        for this 'degraded' handler the inline ctx.online checks in the fetch
        paths do the real work — but a direct dispatch here still degrades to
        the general briefing rather than crashing."""
        return await self._general_briefing(ctx, session, count=None)

    # ─── Ask implementations ────────────────────────────────────────────

    async def _general_briefing(
        self, ctx: Context, session: AsyncSession, *, count: int | None
    ) -> Response:
        n = count or settings.news_items_per_ask
        blocks: list[str] = []
        last_id: int | None = None
        scopes = [("local", "Locally")] if settings.news_location.strip() else []
        scopes += [("national", "Nationally"), ("global", "Around the world")]
        for scope, label in scopes:
            items = await news_service.read_scope_items(session, scope, limit=n)
            if not items:
                continue
            last_id = items[-1]["id"]
            headlines = "; ".join(self._headline(it) for it in items)
            blocks.append(f"{label}: {headlines}.")
        if not blocks:
            return self._reply(
                ctx,
                "I don't have any news yet. The daily fetch runs in the "
                "morning, or you can trigger it from the dashboard.",
            )
        await self._remember_last_item(ctx, session, last_id)
        return self._reply(ctx, "Here's the news. " + " ".join(blocks))

    async def _my_news_digest(self, ctx: Context, session: AsyncSession) -> Response:
        if ctx.person_id is None:
            # Unidentified speaker → house default (pull-only, low stakes).
            return await self._general_briefing(ctx, session, count=None)
        status = await news_service.unread_status(session, ctx.person_id)
        unread = status["unread"]
        if unread == 0:
            briefing = await news_service.get_briefing(session, ctx.person_id)
            if briefing and briefing.get("briefing"):
                return self._reply(
                    ctx,
                    "You're all caught up on your topics. Here's your last "
                    f"briefing: {briefing['briefing']}",
                )
            return self._reply(
                ctx,
                "You don't have any unread news on your topics right now. "
                "Say 'fetch new' and I'll check for updates.",
            )
        oldest = status["oldest"] or {}
        newest = status["newest"] or {}
        parts = [f"You have {unread} unread {'story' if unread == 1 else 'stories'} "
                 "on your topics."]
        if oldest.get("title"):
            parts.append(f"The oldest is {self._topic_phrase(oldest)}.")
        if newest.get("title") and newest.get("title") != oldest.get("title"):
            parts.append(f"The newest is {self._topic_phrase(newest)}.")
        parts.append(
            "Want me to read the latest, fetch new ones, or read the oldest?"
        )
        # Arm expect_followup so the satellite captures the choice without a
        # re-wake; the choices are their own fast paths.
        return Response(
            text=" ".join(parts),
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    async def _read_my_items(
        self, ctx: Context, session: AsyncSession, *, order: str, count: int | None
    ) -> Response:
        if ctx.person_id is None:
            return await self._general_briefing(ctx, session, count=count)
        n = count or settings.news_items_per_ask
        items = await news_service.read_person_items(
            session, ctx.person_id, limit=n, order=order,
            unread_only=True, mark_read=True,
        )
        if not items:
            # Nothing unread — offer cached-any or a fetch.
            items = await news_service.read_person_items(
                session, ctx.person_id, limit=n, order=order,
                unread_only=False, mark_read=False,
            )
            if not items:
                return self._reply(
                    ctx,
                    "You don't have any news on your topics yet. Add a topic "
                    "in the dashboard or say 'fetch new'.",
                )
        await self._remember_last_item(ctx, session, items[-1]["id"])
        body = " ".join(self._spoken_item(it) for it in items)
        return self._reply(ctx, body)

    async def _read_subject(
        self, subject: str, count: int | None, ctx: Context, session: AsyncSession
    ) -> Response:
        subject = subject.strip()
        if not subject:
            return self._reply(ctx, "News about what?")
        n = count or settings.news_items_per_ask
        items = await news_service.read_subject_items(
            session, subject, person_id=ctx.person_id, limit=n,
        )
        if not items:
            hint = (
                " Say 'fetch news about " + subject + "' and I'll look it up."
                if ctx.online else ""
            )
            return self._reply(
                ctx, f"I don't have anything cached about {subject}.{hint}"
            )
        await self._remember_last_item(ctx, session, items[-1]["id"])
        body = " ".join(self._spoken_item(it) for it in items)
        return self._reply(ctx, f"Here's what I have on {subject}. {body}")

    # ─── Fetch (single internet confirm) ────────────────────────────────

    async def _confirm_subject_fetch(
        self, subject: str, count: int | None, ctx: Context, session: AsyncSession
    ) -> Response:
        subject = subject.strip()
        if not subject:
            return self._reply(ctx, "Fetch news about what?")
        if not ctx.online:
            # Degrade: read cached instead of promising a fetch we can't do.
            cached = await self._read_subject(subject, count, ctx, session)
            return self._reply(
                ctx,
                "I can't reach the internet right now. " + cached.text,
            )
        await self._park(
            ctx, session, kind="core.news_fetch",
            data={"subject": subject, "count": count},
        )
        return Response(
            text=f"That means going online to fetch news about {subject}. "
            "Want me to?",
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    async def _confirm_topics_fetch(
        self, ctx: Context, session: AsyncSession
    ) -> Response:
        if ctx.person_id is None:
            return await self._general_briefing(ctx, session, count=None)
        if not ctx.online:
            cached = await self._read_my_items(
                ctx, session, order="newest", count=None
            )
            return self._reply(
                ctx, "I can't reach the internet right now. " + cached.text
            )
        await self._park(ctx, session, kind="core.news_fetch_topics")
        return Response(
            text="That means going online to check your topics for updates. "
            "Want me to?",
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=True,
        )

    async def handle_confirmation(
        self,
        kind: str,
        data: dict[str, Any],
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        if kind == "core.news_fetch":
            subject = data.get("subject") or ""
            count = data.get("count")
            if not affirmative:
                # Fall back to reading whatever's cached.
                return await self._read_subject(subject, count, ctx, session)
            if not ctx.online:
                return self._reply(
                    ctx, "I lost the internet connection — I couldn't fetch that."
                )
            n = count or settings.news_items_per_ask
            items = await news_service.fetch_subject_live(
                session, subject, person_id=ctx.person_id, limit=n,
            )
            if not items:
                return self._reply(
                    ctx,
                    f"I looked but couldn't find fresh news feeds for {subject}.",
                )
            if items and items[0].get("id") is not None:
                await self._remember_last_item(ctx, session, items[-1]["id"])
            body = " ".join(self._spoken_item(it) for it in items)
            return self._reply(ctx, f"Here's the latest on {subject}. {body}")

        if kind == "core.news_fetch_topics":
            if not affirmative:
                return await self._read_my_items(
                    ctx, session, order="newest", count=None
                )
            if ctx.person_id is None or not ctx.online:
                return self._reply(ctx, "I couldn't fetch your topics right now.")
            await news_service.fetch_person_topics(session, ctx.person_id)
            await news_service.summarize_person_briefing(session, ctx.person_id)
            return await self._read_my_items(
                ctx, session, order="newest", count=None
            )

        return self._reply(ctx, "I'm not sure what you're confirming.")

    # ─── Count edit / favorite ──────────────────────────────────────────

    async def _set_count(self, count: int, ctx: Context) -> Response:
        count = max(1, min(count, 10))
        settings.news_items_per_ask = count
        return self._reply(
            ctx, f"Okay, I'll read {count} {'story' if count == 1 else 'stories'} at a time."
        )

    async def _favorite_last(self, ctx: Context, session: AsyncSession) -> Response:
        last_id = await self._last_item_id(ctx, session)
        if last_id is None:
            return self._reply(ctx, "I'm not sure which story to favorite.")
        ok = await news_service.favorite_item(session, last_id, True)
        if not ok:
            return self._reply(ctx, "I couldn't find that story to favorite.")
        return self._reply(ctx, "Saved to your favorites.")

    # ─── Session-context helpers ────────────────────────────────────────

    async def _park(
        self,
        ctx: Context,
        session: AsyncSession,
        *,
        kind: str,
        data: dict | None = None,
    ) -> None:
        if ctx.session_id is None:
            return
        try:
            await request_confirmation(
                session, ctx.session_id, kind=kind, handler=self.name, data=data,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("news: couldn't park pending_confirmation: %s", e)

    async def _remember_last_item(
        self, ctx: Context, session: AsyncSession, item_id: int | None
    ) -> None:
        if ctx.session_id is None or item_id is None:
            return
        try:
            await SessionRepository(session).set_context_key(
                ctx.session_id, "news_last_item_id", item_id
            )
        except Exception as e:  # noqa: BLE001
            log.warning("news: couldn't stash last item id: %s", e)

    async def _last_item_id(self, ctx: Context, session: AsyncSession) -> int | None:
        if ctx.session_id is None:
            return None
        try:
            data = await SessionRepository(session).get_context(ctx.session_id) or {}
        except Exception:  # noqa: BLE001
            return None
        val = data.get("news_last_item_id")
        return int(val) if isinstance(val, int) else None

    # ─── Formatting ─────────────────────────────────────────────────────

    def _headline(self, item: dict[str, Any]) -> str:
        return (item.get("title") or "an untitled story").strip()

    def _spoken_item(self, item: dict[str, Any]) -> str:
        title = (item.get("title") or "").strip()
        source = (item.get("source") or "").strip()
        summary = (item.get("summary") or "").strip()
        lead = title or (summary[:120] if summary else "A story")
        tail = f" (from {source})" if source else ""
        return f"{lead}.{tail}"

    def _topic_phrase(self, item: dict[str, Any]) -> str:
        title = (item.get("title") or "a story").strip()
        topic = (item.get("topic") or "").strip()
        if topic:
            return f"a {topic} story: {title}"
        return title

    def _reply(self, ctx: Context, text_: str) -> Response:
        return Response(
            text=text_,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )
