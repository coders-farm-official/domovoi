"""ListsHandler — the household's lists by voice (V048, domovoi/lists.py).

Before this handler "add tarp the trailer to my to-do list" reached the
answer model, which said it was done; nothing was kept (live, 2026-11-11).
Now it lands here and is kept in the same lists the dashboard's and the
apps' Lists page show.

What it takes (every pattern ends on the word "list", or starts with a
verb only lists have: "cross off", "tick off"):

* add: "add X to my to-do list", "put X on the shopping list", "add milk,
  eggs and bread to the grocery list" (commas split items; a plain "and"
  does not, so "mac and cheese" stays one item);
* read: "what's on my to-do list", "read me the shopping list", "how many
  things are on my to-do list" — open items only, at most
  ``_MAX_SPOKEN``, then how many more;
* check: "is milk on the shopping list";
* cross off: "cross milk off the shopping list", "cross off milk", "check
  off tarp the trailer", "mark milk as done";
* remove: "remove milk from the shopping list", "take milk off the list";
* clear: "clear the shopping list" asks first (yes/no), "clear the crossed
  off items from my to-do list" doesn't (only ticked-off items go);
* lists: "what lists do I have", "make a list called camping", "delete the
  camping list" (asks first).

Names: "to-do", "todo", "to do" are the To-do list; "grocery" and
"groceries" the Shopping list (``domovoi.lists.name_key``). A list nobody
made is never made from a mis-hearing: "add tents to my camping list" asks
"You don't have a camping list. Want me to make one?" and waits for yes.
A bare "my list" goes to the only list when there is one, else asks which.

Never poached: a playlist ("add this song to my playlist", "add it to my
road trip playlist"), the queue ("add X to the queue") and the library
("add it to my library") don't end on the word "list"; "play list" / "song
list" / "music list" (a playlist mis-heard as two words) are declined, so
the turn routes on as if this handler weren't here.

Offline: one database. The tool is offered to the routing model only when
the turn says "list", "to-do" or "shopping" (``offers_tool``).
"""

from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import lists as L
from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

CREATE_KIND = "core.lists_create"
CLEAR_KIND = "core.lists_clear"
DELETE_KIND = "core.lists_delete"
# How long a "yes" still answers the question (a "yes" said much later, to
# something else, must never clear a list).
CONFIRM_TTL_SEC = 120.0

# Said at most, then "and N more".
_MAX_SPOKEN = 8

# ─── Fast-path regexes ────────────────────────────────────────────────────

_ART = r"(?:my|the|our|your)"
# A list's name: never holding a preposition, so in "add go to the bank to
# my to-do list" the item is "go to the bank", not "go" on a list called
# "bank to my to-do".
_NAME = (
    r"(?P<list>to ?-?dos?|things to do"
    r"|(?:(?!\b(?:to|on|onto|in|into|from|off|of)\b(?!-))[^,])+?)"
)
# "my to-do list", "the list", "the grocery list" — the name is optional.
_LIST = rf"(?:{_ART} )?(?:{_NAME} )?list"
# What can trail a command: "..., please", "... for me".
_END = r"(?:,? (?:please|for me|for us|thanks|thank you))?$"
_ADD_VERB = r"(?:jot down|stick down|write down|put down|add|put|stick|throw|pop|write|jot)"

_ADD_RE = re.compile(
    rf"^{_ADD_VERB} (?P<item>.+?) (?:to|on|onto|in|into|on to|in to) {_LIST}{_END}"
)
# "add to my to-do list tarp the trailer"
_ADD_LATE_RE = re.compile(
    rf"^{_ADD_VERB} (?:to|on|onto) {_LIST},? (?P<item>.+)$"
)

_WHAT_IS = r"(?:what(?:'s| is| are)|whats)"
_READ_RES = tuple(re.compile(p) for p in (
    rf"^(?:{_WHAT_IS}(?: left| else| still)?|what do (?:i|we) have|what have (?:i|we) got"
    rf"|what(?:'s| is) there) (?:on|in) {_LIST}{_END}",
    rf"^(?:read|tell|give|show|check|go over|run through|say)(?: me| us)?(?: out)? {_LIST}"
    rf"(?: to me| to us)?(?: out)?{_END}",
    rf"^(?:read|tell|show)(?: me| us)?(?: out)? what(?:'s| is) (?:on|in) {_LIST}{_END}",
    rf"^{_WHAT_IS} {_LIST}{_END}",
    rf"^how many (?:things|items|tasks|jobs)(?: are| do (?:i|we) have)? (?:on|in) {_LIST}{_END}",
))
_IS_ON_RE = re.compile(rf"^(?:is|are) (?:there )?(?P<item>.+?) (?:on|in) {_LIST}{_END}")
# "is there anything on my to-do list": a read, not an item called "anything".
_ANYTHING = frozenset({"anything", "something", "stuff", "much", "a lot", "lots", "loads", "things"})

_LISTS_RE = re.compile(
    r"^(?:(?:what|which) lists (?:do (?:i|we) have|have (?:i|we) got|are there|do you have)"
    r"|what(?:'s| is| are) (?:my|our|the|all my|all the|all of my) lists"
    r"|(?:read|tell|list|name|show)(?: me| us)? (?:all )?(?:of )?(?:my|our|the) lists"
    r"|what lists are there)$"
)

_CROSS_VERB = r"(?:cross|check|tick|strike|mark)"
_CROSS_RES = tuple(re.compile(p) for p in (
    # "cross milk off the shopping list", "check off milk on my to-do list"
    rf"^{_CROSS_VERB} (?P<item>.+?) off (?:of )?{_LIST}{_END}",
    rf"^{_CROSS_VERB} off (?P<item>.+?) (?:on|from|in) {_LIST}{_END}",
    # "cross off milk", "tick milk off" (any list)
    rf"^{_CROSS_VERB} off (?P<item>.+?){_END}",
    rf"^(?:cross|tick|strike|check|mark) (?P<item>.+?) off{_END}",
    # "mark tarp the trailer as done", "mark milk done on the shopping list"
    rf"^mark (?P<item>.+?) (?:as )?(?:done|complete|completed|finished)(?: (?:on|in) {_LIST})?{_END}",
))

_REMOVE_RE = re.compile(
    rf"^(?:remove|delete|take|erase|scratch|drop|get rid of) (?P<item>.+?)"
    rf" (?:from|off|off of|out of) {_LIST}{_END}"
)

_CLEAR_DONE_RE = re.compile(
    r"^(?:clear|remove|delete|get rid of)(?: all)?(?: of)? (?:the |my )?"
    r"(?:done|finished|completed|checked|checked off|crossed off|ticked off|crossed out) "
    r"(?:items|things|ones|stuff|tasks)"
    rf"(?: (?:from|on|off|in) {_LIST})?{_END}"
)
_CLEAR_RES = (
    re.compile(rf"^(?:clear|empty|wipe|clean)(?: out| off)? {_LIST}{_END}"),
    re.compile(
        rf"^(?:delete|remove|clear) (?:everything|all the items|all items|all the things|all of the items) "
        rf"(?:on|from|in|off) {_LIST}{_END}"
    ),
)
_DELETE_LIST_RE = re.compile(
    rf"^(?:delete|remove|get rid of|throw away) (?:{_ART} )?{_NAME} list{_END}"
)

_CREATE_RES = tuple(re.compile(p) for p in (
    rf"^(?:make|create|start|add|set up)(?: me| us)? (?:a |an )?(?:new )?list (?:called|named) (?P<list>.+?){_END}",
    rf"^(?:make|create|start|set up)(?: me| us)? (?:a |an )?(?:new )?{_NAME} list{_END}",
    rf"^(?:make|create|start|set up)(?: me| us)? (?:a |an )?(?:new )?list{_END}",
))

# A playlist mis-heard as two words, or another of the music handlers'
# lists: declined, so the turn routes on as if no fast path took it.
_NOT_OURS = frozenset({
    "play", "playing", "music", "song", "songs", "track", "tracks", "album", "albums",
    "queue", "playlist", "radio", "station", "podcast", "podcasts", "set",
})
# "cross it off" with no item: nothing to look for.
_PRONOUNS = frozenset({"it", "that", "this", "them", "those", "me", "us", "him", "her", "everything", "all"})

_CUE_RE = re.compile(r"\blists?\b|\bto ?-?dos?\b|\bshopping\b|\bgrocer(?:y|ies)\b")


def _and_join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _things(n: int) -> str:
    return "one thing" if n == 1 else f"{n} things"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def split_items(said: str) -> list[str]:
    """The items in "milk, eggs and bread": split on commas, and on the
    last "and" only when commas say it's a list. "mac and cheese" is one."""
    s = said.strip().strip(",")
    if "," not in s:
        return [s] if s else []
    parts = [p.strip() for p in s.split(",")]
    last = parts.pop()
    last = re.sub(r"^(?:and|&)\s+", "", last)
    parts += [p.strip() for p in re.split(r"\s+(?:and|&)\s+", last)]
    return [p for p in parts if p]


def _item_text(said: str) -> str:
    """What's kept for an item said aloud: "some milk" is "Milk"."""
    t = re.sub(r"^(?:some|any)\s+", "", " ".join(said.split()))
    return _cap(t)


def _said(item: str) -> str:
    """An item inside a sentence: as the person said it (lowercase)."""
    return item[:1].lower() + item[1:] if item[1:2].islower() or len(item) == 1 else item


class ListsHandler(Handler):
    name = "lists"
    # band rationale: utility cluster beside calendar (175): every pattern
    #   ends on the word "list" (or starts with "cross off"/"tick off"), so
    #   it poaches nothing later — playlists, the queue and the library
    #   don't say "list" — and nothing earlier takes these phrasings (the
    #   calendar's write answer needs a calendar word). Ahead of documents
    #   (225, "read me the X") so "read me the shopping list" is a list.
    priority_band = 178
    display = HandlerDisplay(label="Lists", tone="info")
    example_phrases = (
        "add milk to the shopping list",
        "what's on my to-do list",
        "cross milk off the shopping list",
    )
    requires_network = "no"
    confirmation_kinds = (CREATE_KIND, CLEAR_KIND, DELETE_KIND)

    tool_schema = {
        "name": "lists",
        "description": (
            "The household's lists (to-do, shopping, any list it named): add an "
            "item, read a list, cross an item off, remove one, or say which lists "
            "there are. Not for playlists, the music queue or the calendar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "read", "cross_off", "remove", "lists"]},
                "list": {"type": "string", "description": "Which list: to-do, shopping, or its name."},
                "item": {"type": "string", "description": "The item (add, cross_off, remove)."},
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_LISTS_RE, ListsHandler._lists_from_match),
            *(FastPath(p, ListsHandler._create_from_match) for p in _CREATE_RES),
            FastPath(_CLEAR_DONE_RE, ListsHandler._clear_done_from_match),
            *(FastPath(p, ListsHandler._clear_from_match) for p in _CLEAR_RES),
            *(FastPath(p, ListsHandler._read_from_match) for p in _READ_RES),
            FastPath(_IS_ON_RE, ListsHandler._is_on_from_match),
            *(FastPath(p, ListsHandler._cross_from_match) for p in _CROSS_RES),
            FastPath(_REMOVE_RE, ListsHandler._remove_from_match),
            FastPath(_ADD_RE, ListsHandler._add_from_match),
            FastPath(_ADD_LATE_RE, ListsHandler._add_from_match),
            FastPath(_DELETE_LIST_RE, ListsHandler._delete_list_from_match),
        ]

    def offers_tool(self, transcript: str) -> bool:
        return bool(_CUE_RE.search(transcript or ""))

    # ─── plumbing ─────────────────────────────────────────────────────
    def _reply(self, ctx: Context, text_: str, *, ask: bool = False, data: dict | None = None) -> Response:
        return Response(text=text_, session_id=ctx.session_id, matched_handler=self.name,
                        expect_followup=ask, data=data or {})

    async def _ask(self, ctx: Context, session: AsyncSession, kind: str, prompt: str,
                   data: dict[str, Any]) -> Response | None:
        """Park a yes/no question; None when this turn has no session to
        park it in (the caller then says what to do instead)."""
        if ctx.session_id is None:
            return None
        await request_confirmation(
            session, ctx.session_id, kind=kind, handler=self.name, prompt=prompt[:200],
            data={**data, "expires_at": time.time() + CONFIRM_TTL_SEC},
        )
        return self._reply(ctx, prompt, ask=True)

    @staticmethod
    def _declined_name(m: re.Match[str]) -> bool:
        name = (m.groupdict().get("list") or "").strip()
        return bool(name) and L.name_key(name) in {L.name_key(n) for n in _NOT_OURS}

    async def _which(self, spoken: str | None, ctx: Context, session: AsyncSession,
                     ) -> tuple[L.ListRow | None, Response | None]:
        """The list a turn names, or the reply that says why there isn't
        one. No name: the only list, when there is just one."""
        if spoken and spoken.strip():
            found = await L.find_list(session, spoken)
            if found is not None:
                return found, None
            return None, self._reply(ctx, f"You don't have a {L.clean_name(spoken)} list.")
        all_ = await L.all_lists(session)
        if len(all_) == 1:
            return all_[0], None
        if not all_:
            return None, self._reply(ctx, "You don't have any lists yet. Say make a list called, and a name.")
        return None, self._reply(
            ctx, f"Which list? You have {_and_join([L.spoken_name(x.name) for x in all_])}."
        )

    # ─── entry points ─────────────────────────────────────────────────
    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return await self._lists(ctx, session)

    async def execute_from_tool(self, args: dict[str, Any], ctx: Context, session: AsyncSession) -> Response | None:
        action = str(args.get("action") or "").strip().lower()
        name = str(args.get("list") or "").strip() or None
        item = str(args.get("item") or "").strip()
        if name and L.name_key(name) in {L.name_key(n) for n in _NOT_OURS}:
            return None
        if action == "add" and item:
            return await self._add(item, name, ctx, session)
        if action == "read":
            return await self._read(name, ctx, session)
        if action in ("cross_off", "check_off", "done") and item:
            return await self._cross(item, name, ctx, session)
        if action == "remove" and item:
            return await self._remove(item, name, ctx, session)
        if action == "lists":
            return await self._lists(ctx, session)
        return None

    # ─── fast-path adapters ───────────────────────────────────────────
    async def _lists_from_match(self, m, ctx, session) -> Response:
        return await self._lists(ctx, session)

    async def _read_from_match(self, m, ctx, session) -> Response | None:
        if self._declined_name(m):
            return None
        return await self._read(m.group("list"), ctx, session)

    async def _is_on_from_match(self, m, ctx, session) -> Response | None:
        if self._declined_name(m):
            return None
        if m.group("item").strip() in _ANYTHING:
            return await self._read(m.group("list"), ctx, session)
        return await self._is_on(m.group("item"), m.group("list"), ctx, session)

    async def _add_from_match(self, m, ctx, session) -> Response | None:
        if self._declined_name(m):
            return None
        return await self._add(m.group("item"), m.group("list"), ctx, session)

    async def _cross_from_match(self, m, ctx, session) -> Response | None:
        if self._declined_name(m):
            return None
        item = m.group("item").strip()
        if item in _PRONOUNS:
            return None
        return await self._cross(item, m.groupdict().get("list"), ctx, session)

    async def _remove_from_match(self, m, ctx, session) -> Response | None:
        if self._declined_name(m) or m.group("item").strip() in _PRONOUNS:
            return None
        return await self._remove(m.group("item"), m.group("list"), ctx, session)

    async def _clear_done_from_match(self, m, ctx, session) -> Response | None:
        if self._declined_name(m):
            return None
        return await self._clear_done(m.group("list"), ctx, session)

    async def _clear_from_match(self, m, ctx, session) -> Response | None:
        if self._declined_name(m):
            return None
        return await self._clear(m.group("list"), ctx, session)

    async def _create_from_match(self, m, ctx, session) -> Response | None:
        name = (m.groupdict().get("list") or "").strip()
        if name and self._declined_name(m):
            return None
        return await self._create(name, ctx, session)

    async def _delete_list_from_match(self, m, ctx, session) -> Response | None:
        if self._declined_name(m):
            return None
        return await self._delete_list(m.group("list"), ctx, session)

    # ─── adding ───────────────────────────────────────────────────────
    async def _add(self, said: str, spoken_list: str | None, ctx: Context, session: AsyncSession) -> Response:
        items = [_item_text(i) for i in split_items(said)]
        items = [i for i in items if i]
        if not items:
            return self._reply(ctx, "What should I add?")
        if spoken_list and spoken_list.strip():
            target = await L.find_list(session, spoken_list)
            if target is None:
                name = L.clean_name(spoken_list)
                try:
                    L.valid_name(name)
                except L.ListError:
                    return self._reply(ctx, "That's not a name I can give a list.")
                prompt = (f"You don't have a {L.spoken_name(name)} list. Want me to make one "
                          f"and add {_and_join([_said(i) for i in items])}?")
                asked = await self._ask(ctx, session, CREATE_KIND, prompt, {"name": name, "items": items})
                if asked is not None:
                    return asked
                return self._reply(ctx, f"You don't have a {L.spoken_name(name)} list. "
                                        "Make it on the Lists page, then add to it.")
        else:
            target, why = await self._which(None, ctx, session)
            if target is None:
                assert why is not None
                return why
        return await self._add_to(target, items, ctx, session)

    async def _add_to(self, target: L.ListRow, items: list[str], ctx: Context, session: AsyncSession) -> Response:
        added: list[str] = []
        back: list[str] = []
        already: list[str] = []
        try:
            for i in items:
                _row, outcome = await L.add_item(
                    session, target.id, i, person_id=ctx.person_id,
                    room=ctx.satellite_room_id, via="voice",
                )
                {"added": added, "reopened": back, "already": already}[outcome].append(_said(i))
        except L.ListError as e:
            if e.code == "list_full":
                return self._reply(ctx, f"Your {L.spoken_name(target.name)} list is full. "
                                        "Clear some things off it first.")
            raise
        where = f"your {L.spoken_name(target.name)} list"
        parts: list[str] = []
        if added:
            parts.append(f"Added {_and_join(added)} to {where}.")
        if back:
            parts.append(f"Put {_and_join(back)} back on {where}.")
        if already:
            verb = "is" if len(already) == 1 else "are"
            lead = _cap(_and_join(already))
            parts.append(f"{lead} {verb} already on {where}." if not (added or back)
                         else f"{lead} {verb} already there.")
        return self._reply(ctx, " ".join(parts), data={"list_id": target.id})

    # ─── reading ──────────────────────────────────────────────────────
    async def _read(self, spoken_list: str | None, ctx: Context, session: AsyncSession) -> Response:
        target, why = await self._which(spoken_list, ctx, session)
        if target is None:
            assert why is not None
            return why
        items = await L.items_of(session, target.id, open_only=True)
        where = f"your {L.spoken_name(target.name)} list"
        if not items:
            if target.done_count:
                return self._reply(ctx, f"Everything on {where} is crossed off.")
            return self._reply(ctx, f"{_cap(where)} is empty.")
        said = [i.text for i in items[:_MAX_SPOKEN]]
        more = len(items) - len(said)
        if more:
            return self._reply(
                ctx,
                f"{_cap(where)} has {len(items)} things. The first {len(said)}: "
                f"{_and_join(said)}. And {more} more on the Lists page.",
            )
        return self._reply(ctx, f"{_cap(where)} has {_things(len(items))}: {_and_join(said)}.")

    async def _is_on(self, said: str, spoken_list: str | None, ctx: Context, session: AsyncSession) -> Response:
        target, why = await self._which(spoken_list, ctx, session)
        if target is None:
            assert why is not None
            return why
        where = f"your {L.spoken_name(target.name)} list"
        found = L.find_items(await L.items_of(session, target.id), said)
        if not found:
            return self._reply(ctx, f"No, {_said(said)} isn't on {where}.")
        item = found[0]
        if item.done:
            return self._reply(ctx, f"{_cap(item.text)} is on {where}, crossed off.")
        return self._reply(ctx, f"Yes, {_said(item.text)} is on {where}.")

    async def _lists(self, ctx: Context, session: AsyncSession) -> Response:
        all_ = await L.all_lists(session)
        if not all_:
            return self._reply(ctx, "You don't have any lists yet. Say make a list called, and a name.")
        names = [L.spoken_name(x.name) for x in all_]
        if len(all_) == 1:
            return self._reply(ctx, f"You have one list: {names[0]}.")
        return self._reply(ctx, f"You have {len(all_)} lists: {_and_join(names)}.")

    # ─── crossing off and removing ────────────────────────────────────
    async def _locate(self, said: str, spoken_list: str | None, ctx: Context, session: AsyncSession,
                      ) -> tuple[L.ListRow | None, L.ItemRow | None, Response | None]:
        """The item ``said`` names: on the named list, or on whichever list
        has it when none was named."""
        if spoken_list and spoken_list.strip():
            target, why = await self._which(spoken_list, ctx, session)
            if target is None:
                return None, None, why
            found = L.find_items(await L.items_of(session, target.id), said)
            if not found:
                return None, None, self._reply(
                    ctx, f"I couldn't find {_said(said)} on your {L.spoken_name(target.name)} list.")
            return target, found[0], None
        hits: list[tuple[L.ListRow, L.ItemRow]] = []
        for lst in await L.all_lists(session):
            found = L.find_items(await L.items_of(session, lst.id), said)
            if found:
                hits.append((lst, found[0]))
        if not hits:
            return None, None, self._reply(ctx, f"I couldn't find {_said(said)} on any of your lists.")
        open_hits = [h for h in hits if not h[1].done] or hits
        if len(open_hits) > 1:
            names = _and_join([f"your {L.spoken_name(h[0].name)} list" for h in open_hits])
            return None, None, self._reply(
                ctx, f"{_cap(_said(said))} is on {names}. Say which list.")
        return open_hits[0][0], open_hits[0][1], None

    async def _cross(self, said: str, spoken_list: str | None, ctx: Context, session: AsyncSession) -> Response:
        target, item, why = await self._locate(said, spoken_list, ctx, session)
        if why is not None:
            return why
        assert target is not None and item is not None
        where = f"your {L.spoken_name(target.name)} list"
        if item.done:
            return self._reply(ctx, f"{_cap(_said(item.text))} is already crossed off {where}.")
        await L.update_item(session, target.id, item.id, done=True)
        return self._reply(ctx, f"Crossed {_said(item.text)} off {where}.", data={"list_id": target.id})

    async def _remove(self, said: str, spoken_list: str | None, ctx: Context, session: AsyncSession) -> Response:
        target, item, why = await self._locate(said, spoken_list, ctx, session)
        if why is not None:
            return why
        assert target is not None and item is not None
        await L.delete_item(session, target.id, item.id)
        return self._reply(ctx, f"Removed {_said(item.text)} from your {L.spoken_name(target.name)} list.",
                           data={"list_id": target.id})

    # ─── clearing ─────────────────────────────────────────────────────
    async def _clear_done(self, spoken_list: str | None, ctx: Context, session: AsyncSession) -> Response:
        target, why = await self._which(spoken_list, ctx, session)
        if target is None:
            assert why is not None
            return why
        where = f"your {L.spoken_name(target.name)} list"
        n = await L.clear_items(session, target.id, done_only=True)
        if not n:
            return self._reply(ctx, f"Nothing on {where} is crossed off.")
        return self._reply(ctx, f"Removed {_things(n)} you'd crossed off {where}.")

    async def _clear(self, spoken_list: str | None, ctx: Context, session: AsyncSession) -> Response:
        target, why = await self._which(spoken_list, ctx, session)
        if target is None:
            assert why is not None
            return why
        where = f"your {L.spoken_name(target.name)} list"
        total = target.open_count + target.done_count
        if not total:
            return self._reply(ctx, f"{_cap(where)} is already empty.")
        prompt = f"Clear all {_things(total)} off {where}?" if total > 1 else f"Clear the one thing on {where}?"
        asked = await self._ask(ctx, session, CLEAR_KIND, prompt, {"list_id": target.id, "name": target.name})
        if asked is not None:
            return asked
        return self._reply(ctx, f"To clear {where}, use the Lists page.")

    # ─── making and deleting lists ────────────────────────────────────
    async def _create(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        if not name:
            return self._reply(ctx, "What should it be called? Say make a list called, and its name.")
        try:
            made = await L.create_list(session, name)
        except L.ListError as e:
            if e.code == "name_taken":
                existing = await L.find_list(session, name)
                shown = L.spoken_name(existing.name) if existing else L.spoken_name(L.clean_name(name))
                return self._reply(ctx, f"You already have a {shown} list.")
            if e.code == "too_many_lists":
                return self._reply(ctx, "You have as many lists as I can keep. Delete one on the Lists page first.")
            return self._reply(ctx, "That's not a name I can give a list.")
        return self._reply(ctx, f"Made your {L.spoken_name(made.name)} list.", data={"list_id": made.id})

    async def _delete_list(self, spoken_list: str, ctx: Context, session: AsyncSession) -> Response:
        target, why = await self._which(spoken_list, ctx, session)
        if target is None:
            assert why is not None
            return why
        total = target.open_count + target.done_count
        what = f"your {L.spoken_name(target.name)} list"
        prompt = (f"Delete {what} and the {_things(total)} on it?" if total
                  else f"Delete {what}?")
        asked = await self._ask(ctx, session, DELETE_KIND, prompt, {"list_id": target.id, "name": target.name})
        if asked is not None:
            return asked
        return self._reply(ctx, f"To delete {what}, use the Lists page.")

    # ─── yes / no ─────────────────────────────────────────────────────
    async def handle_confirmation(
        self, kind: str, data: dict, affirmative: bool, ctx: Context, session: AsyncSession
    ) -> Response:
        if not affirmative:
            return self._reply(ctx, "OK, I'll leave it.")
        expires = data.get("expires_at")
        if not isinstance(expires, (int, float)) or time.time() > float(expires):
            return self._reply(ctx, "I didn't do that: it's been a while since I asked. Say it again if you still want it.")
        if kind == CREATE_KIND:
            name = str(data.get("name") or "")
            items = [str(i) for i in (data.get("items") or []) if str(i).strip()]
            try:
                made = await L.create_list(session, name)
            except L.ListError as e:
                if e.code != "name_taken":
                    return self._reply(ctx, "I couldn't make that list.")
                made = await L.find_list(session, name)   # made meanwhile, elsewhere
                if made is None:
                    return self._reply(ctx, "I couldn't make that list.")
            if not items:
                return self._reply(ctx, f"Made your {L.spoken_name(made.name)} list.")
            return await self._add_to(made, items, ctx, session)
        list_id = data.get("list_id")
        target = await L.get_list(session, int(list_id)) if isinstance(list_id, int) else None
        if target is None:
            return self._reply(ctx, "That list is gone already.")
        where = f"your {L.spoken_name(target.name)} list"
        if kind == CLEAR_KIND:
            await L.clear_items(session, target.id, done_only=False)
            return self._reply(ctx, f"Done. {_cap(where)} is empty.", data={"list_id": target.id})
        await L.delete_list(session, target.id)
        return self._reply(ctx, f"Deleted {where}.")


__all__ = ["CLEAR_KIND", "CREATE_KIND", "DELETE_KIND", "ListsHandler", "split_items"]
