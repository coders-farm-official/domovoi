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
(favorites the last item read, or the one just told more about →
retention-exempt).

How a read-out sounds (2026-11-17, the owner: "no pauses or anything that
audibly separates headlines"): every headline is a sentence of its own,
numbered ("First, … Second, …") straight through the whole read-out, in a
paragraph of its own. A blank line is a longer silence in the speech
(``tts.PARAGRAPH_SILENCE_SEC``), so the stories are heard apart. The scope
labels ("Locally.", "Nationally.", "Around the world.") are sentences too.

"Tell me more": every read-out remembers the stories it read, in order
(``news_read`` in the session context). For ten minutes after,
"tell me more about the second one" / "the last one" / "number three" /
"the one about the hot tub recall" / "more on that" says that story's
summary and source. A thin summary can be filled from the article page
(read the way "what does this page say" reads one, then summarised by the
Q&A model): asked first unless the household lets lookups go online without
asking. With no recent news list, or words that match no story read, the
turn goes on to Q&A ("tell me more about X" is a question there).

Ordering: NewsHandler sits ahead of the greedy media handlers (music /
library / provider plugins) — its regexes are all anchored on news vocabulary
(news / headlines / stories / "the latest on") so they can't poach "play X".
It never claims a bare "stop" / "next" (those stay with music) — digest
barge-in for "next"/"stop" is the satellite's VAD interrupt during TTS, a
streaming-layer concern (see INTEGRATION_news.md), not a handler regex.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import ai_features, egress, news_service
from domovoi.config import settings
from domovoi.db.repositories import SessionRepository
from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


def _offline_lead() -> str:
    """The lead-in before reading cached news when a fetch can't happen:
    today's wording while the internet is down, the turned-off one when
    the box is set to stay off the internet (INTERNET_ACCESS=never)."""
    if egress.internet_turned_off():
        return f"{egress.spoken_offline_phrase()}, so here's what I already have. "
    return "I can't reach the internet right now. "


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

# Words a news request carries — the ``offers_tool`` gate. Generous on
# purpose ("latest", "update", "report" over-match mildly): a false
# positive offers the schema, and adding a tool changes the router's
# prompt, so each one costs the tool model a re-read of that schema. Whole
# words only: as bare substrings "history" held "story", and "reporter" or
# "topical" matched too, so "tell me about the history of Rome" was
# offered the news tool (and wrote a 300-token answer of its own).
_NEWS_CUE_RE = re.compile(
    r"\b(?:news|headlines?|briefings?|digests?|happening|going on|current events"
    r"|latest|updates?|stor(?:y|ies)|reports?|topics?|elections?|politic\w*)\b"
)
# "a story", "a bedtime story", "a short story about dragons": a story to
# be told, not a news story, so the article-led singular is not a cue
# ("a news story", "a top story" still are).
_TOLD_STORY_RE = re.compile(
    r"\ban?\s+(?:(?!(?:news|top|breaking|latest|lead|main|big|front)\b)\w+\s+){0,2}?story\b"
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
# "what's new in science". Not the music library: "what's new in my
# library" / "in the music library" / "in my music" is the library's
# recently-added question (handlers/library.py), which this pattern took
# from band 260 before 2026-10-17 and answered as news.
_NOT_A_NEWS_CATEGORY = (
    r"(?!(?:(?:my|our|the)\s+)?(?:music\s+)?(?:library|collection)$"
    r"|(?:my|our)\s+music$)"
)
_CATEGORY_RE = re.compile(
    rf"^(?:what'?s|whats|what is)\s+new\s+in\s+{_NOT_A_NEWS_CATEGORY}(?:the\s+)?(?P<cat>.+)$"
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

# ─── Tell me more (a story just read) ────────────────────────────────────
_ORDINAL_WORDS = (
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
    "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth",
    "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth",
    "twentieth",
)
_NUMBER_WORDS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty",
)
_ORD = "|".join(_ORDINAL_WORDS) + r"|\d{1,2}(?:st|nd|rd|th)"
_NUM = "|".join(_NUMBER_WORDS) + r"|\d{1,2}"
_THING = r"(?:one|story|article|headline|item|news story)"
# A reference that can only mean a story in a list just read: an ordinal,
# a number, the last one, or "the one about X". The bare forms ("the
# second one", "number three") are fast paths of their own only for these.
_LIST_REF = (
    rf"(?:the\s+)?(?:{_ORD})(?:\s+{_THING})?"
    rf"|(?:number|story|item|headline)\s+(?:number\s+)?(?:{_NUM})"
    rf"|(?:the\s+)?(?:last|final)\s+{_THING}"
    rf"|(?:the\s+)?{_THING}\s+(?:about|on)\s+.+"
)
_FOLLOWUP_RE = re.compile(
    r"^(?:(?:can|could|would)\s+you\s+)?(?:"
    r"(?:tell|give)\s+me\s+(?:a\s+(?:bit|little)\s+|some\s+)?more"
    r"(?:\s+(?:about|on)\s+(?P<ref1>.+))?"
    r"|(?:read|say)(?:\s+me)?\s+(?:a\s+(?:bit|little)\s+|some\s+)?more\s+(?:about|on)\s+(?P<ref2>.+)"
    r"|(?:a\s+(?:bit|little)\s+|some\s+)?more\s+(?:about|on)\s+(?P<ref3>.+)"
    r"|(?:tell\s+me|what\s+about|how\s+about|and|what'?s|what\s+is)\s+(?:about\s+)?"
    rf"(?P<ref4>{_LIST_REF})(?:\s+about)?"
    rf"|(?P<ref5>{_LIST_REF})"
    r")$"
)
# The words that name the story just said, or the one being talked about.
_CURRENT_REFS = frozenset({
    "", "that", "it", "this", "that one", "this one", "that story", "this story",
    "that article", "this article", "that headline", "the story", "the article",
    "that news", "the news story",
})
# How long after a read-out "tell me more about …" means a story in it.
FOLLOWUP_WINDOW_SEC = 10 * 60
# A stored summary shorter than this is thin: the full article may be read.
THIN_SUMMARY_CHARS = 200
# Sentences of a summary said out loud.
SUMMARY_SENTENCES = 4
_TAG_RE = re.compile(r"<[^>]*>")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_ARTICLE_QUESTION = (
    "Summarise this news story in three or four short, plain spoken sentences: "
    "what happened, who it affects, and what happens next if it says."
)


def _ordinal_word(n: int) -> str:
    """"First" .. "Twentieth", then "Number 21"."""
    if 1 <= n <= len(_ORDINAL_WORDS):
        return _ORDINAL_WORDS[n - 1].capitalize()
    return f"Number {n}"


def _as_sentence(text: str) -> str:
    """``text`` ending in a full stop, unless it already ends a sentence."""
    t = " ".join((text or "").split()).rstrip(" ,;:-–—")
    if not t:
        return t
    return t if t[-1] in ".!?…\"'”’" else t + "."


def _plain(text: str | None) -> str:
    """A feed summary as plain words: tags dropped, entities read."""
    t = _TAG_RE.sub(" ", (text or "")[:8000])
    return " ".join(html.unescape(t).split())


def _said_key(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


def _parse_ref(ref: str) -> tuple[str, Any]:
    """What a follow-up's words point at: ("current", None), ("last", None),
    ("index", n), ("words", (text, explicit))."""
    r = " ".join((ref or "").split()).strip(" ?.,!")
    if r in _CURRENT_REFS:
        return "current", None
    m = re.fullmatch(rf"(?:the\s+)?(?:last|final)(?:\s+{_THING})?", r)
    if m:
        return "last", None
    m = re.fullmatch(rf"(?:the\s+)?({_ORD})(?:\s+{_THING})?", r)
    if m:
        w = m.group(1)
        if w in _ORDINAL_WORDS:
            return "index", _ORDINAL_WORDS.index(w) + 1
        return "index", int(re.match(r"\d+", w).group(0))
    m = re.fullmatch(rf"(?:number|story|item|headline)\s+(?:number\s+)?({_NUM})", r)
    if m:
        w = m.group(1)
        return "index", (_NUMBER_WORDS.index(w) + 1) if w in _NUMBER_WORDS else int(w)
    m = re.fullmatch(rf"(?:the\s+)?{_THING}\s+(?:about|on)\s+(.+)", r)
    if m:
        return "words", (m.group(1), True)
    m = re.fullmatch(r"(?:the\s+)?(.+?)\s+(?:story|article|headline)", r)
    if m:
        return "words", (m.group(1), True)
    return "words", (r, False)


def _match_title(words: str, titles: list[str]) -> int | None:
    """The index of the title that holds most of ``words`` (at least half
    of its meaningful words), the earliest on a tie; None for no match."""
    from domovoi.lookup_answer import overlap

    best, best_score = None, 0.0
    for i, title in enumerate(titles):
        score = overlap(words, title or "")
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.5 else None


class NewsHandler(Handler):
    name = "news"
    # band rationale: before all greedy media. Every regex is anchored on news vocabulary
    #   ("news" / "headlines" / "stories" / "the latest on <subject>"), so
    #   it can't poach "play X" (music's catch-all) or "play the latest
    #   <show>" (spoken_audio — starts with "play", which news never
    #   claims). It deliberately does NOT claim a bare "stop" / "next".
    priority_band = 260
    display = HandlerDisplay(label="News", tone="info")
    example_phrases = (
        "what's the news",
        "read me the headlines",
        "any news about the city council",
    )
    confirmation_kinds = (
        "core.news_fetch", "core.news_fetch_topics", "core.news_read_article",
    )
    requires_network = "degraded"

    tool_schema = {
        "name": "news",
        "description": (
            "Read the news, only when the user asks for news, headlines or "
            "a briefing: a general local/national/global briefing, the "
            "user's personal topics-of-interest digest, or news about a "
            "subject ('any news about X'). A factual question ('who wrote "
            "the odyssey', 'what is the tallest mountain') is NOT a news "
            "request — do not use this to look things up. A 'fetch'/'go "
            "get' verb pulls fresh from the internet (with confirmation); "
            "'give me'/'read me' reads what's cached."
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

    def offers_tool(self, transcript: str) -> bool:
        # qwen2.5-family tool models read a bare trivia question as a
        # subject lookup ("who wrote the odyssey" → news(subject="Odyssey
        # author"), measured 2026-09-15 on qwen2.5:14b). A news request
        # says so somewhere in the utterance; one that doesn't can't be one.
        return bool(_NEWS_CUE_RE.search(_TOLD_STORY_RE.sub(" ", transcript)))

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
            # Declines (None) unless this session read news in the last ten
            # minutes, so "tell me more about X" stays Q&A's otherwise.
            FastPath(_FOLLOWUP_RE, NewsHandler._followup_from_match),
        ]

    # ─── Fast-path adapters ─────────────────────────────────────────────

    async def _count_edit_from_match(self, m, ctx, session):
        return await self._set_count(int(m.group("count")), ctx)

    async def _favorite_from_match(self, m, ctx, session):
        return await self._favorite_last(ctx, session)

    async def _followup_from_match(self, m, ctx, session):
        ref = next((g for g in m.groups() if g is not None), "")
        return await self._tell_more(ref, ctx, session)

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
                return self._reply(ctx, "What should I get news about?")
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
        groups: list[tuple[str, list[dict[str, Any]]]] = []
        from domovoi import home_location

        scopes = [("local", "Locally")] if await home_location.load(session) else []
        scopes += [("national", "Nationally"), ("global", "Around the world")]
        for scope, label in scopes:
            items = await news_service.read_scope_items(session, scope, limit=n)
            if items:
                groups.append((label, items))
        if not groups:
            return self._reply(
                ctx,
                "I don't have any news yet. The daily fetch runs in the "
                "morning, or you can trigger it from the dashboard.",
            )
        # One numbering straight through, so "the fifth one" is the fifth
        # heard; sources are left out here to keep the briefing short.
        paragraphs = ["Here's the news."]
        read: list[dict[str, Any]] = []
        for label, items in groups:
            for i, it in enumerate(items):
                read.append(it)
                line = f"{_ordinal_word(len(read))}, {self._headline_sentence(it)}"
                paragraphs.append(f"{label}. {line}" if i == 0 else line)
        return await self._read_out(ctx, session, paragraphs, read)

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
        return await self._read_out(ctx, session, self._numbered(items), items)

    async def _read_subject(
        self, subject: str, count: int | None, ctx: Context, session: AsyncSession
    ) -> Response:
        subject = subject.strip()
        if not subject:
            return self._reply(ctx, "What should I get news about?")
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
        return await self._read_out(
            ctx, session,
            [f"Here's what I have on {subject}.", *self._numbered(items)], items,
        )

    # ─── Fetch (single internet confirm) ────────────────────────────────

    async def _confirm_subject_fetch(
        self, subject: str, count: int | None, ctx: Context, session: AsyncSession
    ) -> Response:
        subject = subject.strip()
        if not subject:
            return self._reply(ctx, "What should I fetch news about?")
        if not ctx.online:
            # Degrade: read cached instead of promising a fetch we can't do.
            cached = await self._read_subject(subject, count, ctx, session)
            return await self._with_lead(ctx, session, cached, _offline_lead())
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
            return await self._with_lead(ctx, session, cached, _offline_lead())
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
            return await self._read_out(
                ctx, session,
                [f"Here's the latest on {subject}.", *self._numbered(items)], items,
            )

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

        if kind == "core.news_read_article":
            if not affirmative:
                return self._reply(ctx, "Okay.")
            item = data.get("item") or {}
            return await self._expand(item, ctx, session, read_article=True)

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

    async def _remember_read(
        self,
        ctx: Context,
        session: AsyncSession,
        items: list[dict[str, Any]],
        said: str,
    ) -> None:
        """Keep the stories a read-out read, in order, for "tell me more"
        (``news_read``) and the last one for "favorite that"
        (``news_last_item_id``). A story fetched for an unknown speaker has
        no row, so its words are kept instead of an id."""
        if ctx.session_id is None:
            return
        entries: list[dict[str, Any]] = []
        for it in items:
            entry: dict[str, Any] = {
                "id": it.get("id"),
                "title": (it.get("title") or "")[:300],
            }
            if it.get("id") is None:
                entry.update(
                    summary=(it.get("summary") or "")[:2000],
                    source=(it.get("source") or "")[:200],
                    url=(it.get("url") or "")[:2000],
                )
            entries.append(entry)
        state = {
            "at": time.time(),
            "items": entries,
            "said": _said_key(said),
            "current": None,
        }
        try:
            repo = SessionRepository(session)
            await repo.set_context_key(ctx.session_id, "news_read", state)
            await repo.set_context_key(
                ctx.session_id, "news_last_item_id", entries[-1]["id"] if entries else None,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("news: couldn't stash the stories read: %s", e)

    async def _remember_expanded(
        self,
        ctx: Context,
        session: AsyncSession,
        item: dict[str, Any],
        said: str,
    ) -> None:
        """The story just told more about becomes "that" (for "favorite
        that" and "more on that")."""
        if ctx.session_id is None:
            return
        try:
            repo = SessionRepository(session)
            data = await repo.get_context(ctx.session_id) or {}
            state = data.get("news_read")
            if isinstance(state, dict) and isinstance(state.get("items"), list):
                idx = next(
                    (i for i, e in enumerate(state["items"])
                     if isinstance(e, dict) and e.get("id") == item.get("id")
                     and e.get("title") == (item.get("title") or "")[:300]),
                    None,
                )
                if idx is None and item.get("id") is not None:
                    idx = next(
                        (i for i, e in enumerate(state["items"])
                         if isinstance(e, dict) and e.get("id") == item.get("id")),
                        None,
                    )
                state["current"] = idx
                state["said"] = _said_key(said)
                await repo.set_context_key(ctx.session_id, "news_read", state)
            await repo.set_context_key(ctx.session_id, "news_last_item_id", item.get("id"))
        except Exception as e:  # noqa: BLE001
            log.warning("news: couldn't stash the story told about: %s", e)

    async def _read_state(
        self, ctx: Context, session: AsyncSession
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """(the stories read in this session in the last
        :data:`FOLLOWUP_WINDOW_SEC`, or None; the session context)."""
        if ctx.session_id is None:
            return None, {}
        try:
            data = await SessionRepository(session).get_context(ctx.session_id) or {}
        except Exception:  # noqa: BLE001
            return None, {}
        state = data.get("news_read")
        if not isinstance(state, dict):
            return None, data
        items = state.get("items")
        at = state.get("at")
        if not isinstance(items, list) or not items or not isinstance(at, (int, float)):
            return None, data
        if time.time() - at > FOLLOWUP_WINDOW_SEC:
            return None, data
        return state, data

    async def _last_item_id(self, ctx: Context, session: AsyncSession) -> int | None:
        if ctx.session_id is None:
            return None
        try:
            data = await SessionRepository(session).get_context(ctx.session_id) or {}
        except Exception:  # noqa: BLE001
            return None
        val = data.get("news_last_item_id")
        return int(val) if isinstance(val, int) else None

    # ─── Read-out ───────────────────────────────────────────────────────

    async def _read_out(
        self,
        ctx: Context,
        session: AsyncSession,
        paragraphs: list[str],
        items: list[dict[str, Any]],
    ) -> Response:
        """A read-out: one paragraph per story (a blank line, which the
        speech says as a longer silence), the stories remembered in order,
        and the microphone kept open for "tell me more about …"."""
        if ctx.session_id is not None:
            paragraphs = [
                *paragraphs,
                "Do you want more on any of them?" if len(items) > 1 else "Do you want more on it?",
            ]
        text_ = "\n\n".join(p for p in paragraphs if p)
        await self._remember_read(ctx, session, items, text_)
        return Response(
            text=text_,
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=ctx.session_id is not None,
        )

    async def _with_lead(
        self, ctx: Context, session: AsyncSession, resp: Response, lead: str
    ) -> Response:
        """``resp`` with ``lead`` said first ("I can't reach the internet
        right now. "), still the read-out "more on that" refers to."""
        text_ = lead + resp.text
        state, _ = await self._read_state(ctx, session)
        if state is not None and state.get("said") == _said_key(resp.text):
            state["said"] = _said_key(text_)
            try:
                await SessionRepository(session).set_context_key(
                    ctx.session_id, "news_read", state)
            except Exception as e:  # noqa: BLE001
                log.warning("news: couldn't stash the stories read: %s", e)
        return resp.model_copy(update={"text": text_})

    # ─── Tell me more ───────────────────────────────────────────────────

    async def _tell_more(
        self, ref: str, ctx: Context, session: AsyncSession
    ) -> Response | None:
        """"Tell me more about the second one": the story the words point
        at, from the last read-out in this session. None (the turn goes on
        to the tool model and Q&A) when nothing was read in the last ten
        minutes, when "that" no longer means a story (something else was
        said since), or when the words match no story read."""
        state, data = await self._read_state(ctx, session)
        if state is None:
            return None
        items = [e for e in state["items"] if isinstance(e, dict)]
        if not items:
            return None
        kind, arg = _parse_ref(ref)
        if kind == "current":
            last_said = data.get("last_assistant_response") or ""
            if _said_key(last_said) != state.get("said"):
                return None
            cur = state.get("current")
            idx = cur if isinstance(cur, int) and 0 <= cur < len(items) else len(items) - 1
        elif kind == "last":
            idx = len(items) - 1
        elif kind == "index":
            n = int(arg)
            if not 1 <= n <= len(items):
                k = len(items)
                missing = (
                    f"no {_ordinal_word(n).lower()} one" if 1 <= n <= len(_ORDINAL_WORDS)
                    else f"no story number {n}"
                )
                return self._reply(
                    ctx,
                    f"I only read {k} {'story' if k == 1 else 'stories'}, so there's {missing}.",
                )
            idx = n - 1
        else:
            words, explicit = arg
            found = _match_title(words, [e.get("title") or "" for e in items])
            if found is None:
                if explicit:
                    return self._reply(
                        ctx, f"None of the stories I just read was about {words}."
                    )
                return None
            idx = found
        return await self._expand(items[idx], ctx, session)

    async def _expand(
        self,
        entry: dict[str, Any],
        ctx: Context,
        session: AsyncSession,
        *,
        read_article: bool = False,
    ) -> Response:
        """One story told in a few sentences: its headline, its stored
        summary and its source. A thin summary may be filled from the
        article page (``read_article``, or asked first); otherwise it says
        plainly that this is all there is."""
        item = dict(entry)
        if item.get("id") is not None:
            row = await news_service.get_item(session, int(item["id"]))
            if row is None:
                return self._reply(
                    ctx, "I can't find that story any more. It may have been cleared out."
                )
            item = row
        title = " ".join((item.get("title") or "").split())
        summary = _plain(item.get("summary"))
        source = " ".join((item.get("source") or "").split())
        if summary and title and summary.lower().rstrip(".") == title.lower().rstrip("."):
            summary = ""  # a feed that repeats the headline as its summary
        sentences = _SENT_SPLIT_RE.split(summary) if summary else []
        said_summary = _as_sentence(" ".join(sentences[:SUMMARY_SENTENCES]))
        thin = len(summary) < THIN_SUMMARY_CHARS

        parts: list[str] = [_as_sentence(title) if title else ""]
        ask = False
        if read_article:
            article, problem = await self._article_summary(item, ctx)
            if article:
                parts.append(article)
            else:
                parts += [said_summary, problem or ""]
            if source:
                parts.append(f"That's from {source}.")
        else:
            parts.append(said_summary)
            if source:
                parts.append(f"That's from {source}.")
            if thin:
                can, why = self._can_read_article(item, ctx)
                if can and settings.lookup_always_allow:
                    article, problem = await self._article_summary(item, ctx)
                    parts = [parts[0], article] if article else [*parts, problem or ""]
                    if article and source:
                        parts.append(f"That's from {source}.")
                elif can:
                    ask = True
                    parts.append(
                        "That's all the feed gave me. Want me to read the full article online?"
                    )
                elif why:
                    parts.append(why)
                elif not summary:
                    parts.append("That's all I have on it: just the headline.")
                else:
                    parts.append("That's all I have on it.")
        text_ = " ".join(p for p in parts if p).strip() or "I don't have anything more on that one."
        if ask:
            await self._park(
                ctx, session, kind="core.news_read_article",
                data={"item": {
                    "id": item.get("id"), "title": title[:300],
                    "summary": (item.get("summary") or "")[:2000],
                    "source": source[:200], "url": (item.get("url") or "")[:2000],
                }},
            )
        await self._remember_expanded(ctx, session, {**entry, "id": item.get("id")}, text_)
        return Response(
            text=text_,
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=ask,
        )

    def _can_read_article(
        self, item: dict[str, Any], ctx: Context
    ) -> tuple[bool, str | None]:
        """Whether the article page may be read now, and if not, the line
        that says why (None when there is nothing worth saying: no web
        address, or the AI features are off and nothing could summarise it)."""
        url = (item.get("url") or "").strip()
        if not url.lower().startswith("https://") or ai_features.turned_off():
            return False, None
        if egress.internet_turned_off():
            return False, f"{egress.spoken_offline_phrase()}, so I can't read the full article."
        if not ctx.online:
            return False, "I can't reach the internet right now, so I can't read the full article."
        return True, None

    async def _article_summary(
        self, item: dict[str, Any], ctx: Context
    ) -> tuple[str | None, str | None]:
        """(the article page summarised in a few spoken sentences, None) or
        (None, the line that says why not). The page is read the way "what
        does this page say" reads one (``lookup_web.read_page``: HTTPS
        only, public addresses, a byte cap, the egress rules) and handed to
        the Q&A model fenced as data (``lookup_answer.system_prompt``)."""
        from domovoi import lookup_answer, lookup_web
        from domovoi.clients.ollama import get_ollama_client
        from domovoi.speech_sanitize import sanitize_for_speech

        can, why = self._can_read_article(item, ctx)
        if not can:
            return None, why or "I can't read the full article."
        if not lookup_web.PACE.allow(key="voice:news"):
            return None, "I've read a lot of pages this minute, so I didn't read the article. Try again shortly."
        title = (item.get("title") or "").strip()
        try:
            outcome = await lookup_web.read_page(item["url"], query=title)
        except egress.InternetTurnedOff:
            return None, f"{egress.spoken_offline_phrase()}, so I can't read the full article."
        except lookup_web.LookupRefused as e:
            return None, f"I couldn't read the full article. {e}"
        except Exception as e:  # noqa: BLE001
            log.warning("news: reading an article page failed: %s", type(e).__name__)
            return None, "I couldn't read the full article."
        passages = lookup_answer.rank_passages(title or _ARTICLE_QUESTION, outcome.results)
        if not passages:
            return None, "The article page didn't have anything more to read."
        try:
            reply = await get_ollama_client().qa(
                _ARTICLE_QUESTION,
                system_prompt=lookup_answer.system_prompt(_ARTICLE_QUESTION, passages),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("news: summarising an article failed: %s", type(e).__name__)
            return None, "I read the article but couldn't summarise it just now."
        out = lookup_answer.strip_unknown_links(lookup_answer.strip_model_sources(reply or ""), set())
        out = sanitize_for_speech(out)
        if (
            not out
            or lookup_answer.couldnt_find(out)
            or lookup_answer.parse_proposal(out) is not None
        ):
            return None, "The article page didn't say more than that."
        sentences = _SENT_SPLIT_RE.split(" ".join(out.split()))
        return _as_sentence(" ".join(sentences[:SUMMARY_SENTENCES])), None

    # ─── Formatting ─────────────────────────────────────────────────────

    def _headline_sentence(self, item: dict[str, Any]) -> str:
        """The headline as a sentence of its own (a feed's titles carry no
        end mark, and without one the voice runs into the next story)."""
        title = (item.get("title") or "").strip()
        if not title:
            summary = _plain(item.get("summary"))
            title = summary[:120] if summary else "a story with no headline"
        return _as_sentence(title)

    def _item_sentence(self, item: dict[str, Any]) -> str:
        """A headline with its source: "Council votes on the bridge, from
        Example News." """
        headline = self._headline_sentence(item)
        source = " ".join((item.get("source") or "").split())
        if not source:
            return headline
        if headline.endswith("."):
            return f"{headline[:-1]}, from {source}."
        return f"{headline} From {source}."

    def _numbered(self, items: list[dict[str, Any]]) -> list[str]:
        """One paragraph per story, numbered when there is more than one."""
        if len(items) == 1:
            return [self._item_sentence(items[0])]
        return [
            f"{_ordinal_word(i)}, {self._item_sentence(it)}"
            for i, it in enumerate(items, start=1)
        ]

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
