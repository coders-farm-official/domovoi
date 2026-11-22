"""FriendHousesHandler: messages to and from friend houses, by voice.

Friend Houses ("serverless messaging and friend system between households",
domovoi/friend_houses/). Owner decision 18: voice commands for what the
house already does with its friends.

* **Send:** "message the Smiths dinner's at six", "send a message to the
  Smiths saying see you soon". Domovoi reads it back and sends only on
  "yes" (a message can't be unsent). "Tell the Smiths …" works too, through
  the router: it is intercom's shape, and intercom hands a target that is
  no room but a friend house to :func:`offer_to_friend`. There is no "tell"
  fast path here on purpose: a pattern that matched every "tell X …" would
  change how every such turn is planned (router.plan_route), not just ours.
* **Check:** "any messages?", "do we have any new messages from friends?"
  says how many are unread, and from whom. Never the text.
* **Read:** "read my messages", "read the messages from the Smiths" reads
  the unread ones aloud, oldest first, and marks them read. Only on
  request, whatever a friend's alert level: anyone in the room hears them.
  Each is read as the inbox's alerts are (messages.safe_to_say): every wake
  phrase the house answers to taken out, capped, control characters gone.

One inbox for the whole house (owner decision 16). The text of a friend's
message is spoken only; it never reaches a language model (finding 6), and
this handler offers the router no tool for it.

Names: the household's own name for each friend (what the dashboard shows),
said with or without "the" or "'s house". A "message X …" whose X isn't a
friend is declined, so the turn routes on as if this handler weren't here.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.confirmations import request_confirmation
from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)

SEND_KIND = "core.friend_houses_send"
CONFIRM_TTL_SEC = 120.0
READ_MAX = 5

_MESSAGE_RE = re.compile(
    r"^(?:send (?:a )?(?:message|note) to|message|send a message for) (?P<rest>.+)$"
)
_CHECK_RE = re.compile(
    r"^(?:(?:do|did) (?:i|we) (?:have|get) |are there |have (?:i|we) got |is there )?"
    r"any (?:new )?(?:friend (?:house )?)?messages?(?: from (?:my |our )?friends?)?$"
    r"|^check (?:my |our |the )?(?:friend (?:house )?)?messages$"
)
_READ_RE = re.compile(
    r"^read (?:me )?(?:my |our |the |any |new )*(?:friend (?:house )?)?messages?"
    r"(?: from (?P<name>.+))?$"
)
_LEAD_IN_RE = re.compile(r"^(?:(?:saying|that|to say|and say)\b[\s:,]*|[:,]\s*)")


def _fold(name: str) -> str:
    n = re.sub(r"[^a-z0-9' ]+", " ", (name or "").lower())
    n = re.sub(r"'s\b", "s", n).replace("'", "")
    n = re.sub(r"\b(?:house|home|family)$", "", " ".join(n.split())).strip()
    return re.sub(r"^the ", "", n).strip()


def spoken_forms(name: str) -> set[str]:
    """How a friend's name may be said: "The Smiths" answers to "the
    smiths", "smiths", "the smiths house", "the smiths' house"."""
    cores = {_fold(name)}
    if re.search(r"'s\b", name or ""):
        cores.add(_fold(re.sub(r"'s\b.*$", "", name)))  # "Nana's House" is also "Nana"
    out: set[str] = set()
    for core in filter(None, cores):
        out |= {core, f"the {core}"}
        for base in (core, f"the {core}"):
            out |= {f"{base} house", f"{base}s house", f"{base} family", f"{base} home"}
    return out


def split_friend(rest: str, friends: list[Any]) -> tuple[Any, str] | None:
    """The friend ``rest`` starts with (longest name wins) and what follows."""
    said = " ".join((rest or "").lower().replace("'", "").split())
    best: tuple[int, Any, str] | None = None
    for f in friends:
        for form in spoken_forms(f.local_name):
            form = form.replace("'", "")
            if said == form or said.startswith(form + " "):
                if best is None or len(form) > best[0]:
                    best = (len(form), f, rest.strip()[len(form):] if said != form else "")
    if best is None:
        return None
    body = _LEAD_IN_RE.sub("", best[2].strip()).strip()
    return best[1], body


async def _friends(session: AsyncSession) -> list[Any]:
    from domovoi.friend_houses import store

    return await store.active_friends(session)


def _on() -> bool:
    from domovoi.config import settings

    return bool(getattr(settings, "friend_houses_enabled", False))


class FriendHousesHandler(Handler):
    name = "friend_houses"
    # band rationale: comms cluster, just before intercom (210): "message X",
    #   "send a message to X", "any messages" and "read my messages" are
    #   phrasings no other handler takes ("message X" is declined unless X is
    #   a friend house). Before documents (225, "read me the X") so "read me
    #   my messages" is ours.
    priority_band = 205
    display = HandlerDisplay(label="Friend Houses", tone="info")
    # Only phrases that land without any friend set up (test_example_phrases
    # runs on an empty house); "tell <friend> …" is in the module note.
    example_phrases = (
        "any messages",
        "read my messages",
    )
    requires_network = "no"  # sending queues; delivery is the outbox's job
    confirmation_kinds = (SEND_KIND,)
    tool_schema = {
        "name": "friend_houses",
        "description": "Messages to and from friend houses (other Domovoi homes).",
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_CHECK_RE, FriendHousesHandler._check),
            FastPath(_READ_RE, FriendHousesHandler._read),
            FastPath(_MESSAGE_RE, FriendHousesHandler._send),
        ]

    def offers_tool(self, transcript: str) -> bool:
        return False  # friend text and friend names stay out of the model

    def _reply(self, ctx: Context, text_: str, *, ask: bool = False,
               history_text: str | None = None) -> Response:
        return Response(text=text_, session_id=ctx.session_id, matched_handler=self.name,
                        expect_followup=ask, history_text=history_text)

    async def execute(self, intent: Intent, ctx: Context, session: AsyncSession) -> Response:
        return self._reply(ctx, "Say \"any messages\", \"read my messages\", or \"tell\" and a "
                                "friend house's name with your message.")

    # ─── send ─────────────────────────────────────────────────────────
    async def _send(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response | None:
        if not _on():
            return None
        found = split_friend(m.group("rest"), await _friends(session))
        if found is None:
            return None  # not a friend house: route on
        return await self._ask_to_send(found[0], found[1], ctx, session)

    async def _ask_to_send(self, friend: Any, body: str, ctx: Context,
                           session: AsyncSession) -> Response:
        if not body:
            return self._reply(ctx, f"What should I tell {friend.local_name}?")
        if len(body) > 2000:
            return self._reply(ctx, "That's too long for one message. Try a shorter one.")
        if ctx.session_id is None:
            return self._reply(ctx, "I can't ask you to check it from here. Send it from the dashboard.")
        prompt = f"Send \"{body}\" to {friend.local_name}?"
        await request_confirmation(
            session, ctx.session_id, kind=SEND_KIND, handler=self.name, prompt=prompt[:200],
            data={"friend_id": friend.id, "name": friend.local_name, "text": body,
                  "expires_at": time.time() + CONFIRM_TTL_SEC},
        )
        return self._reply(ctx, prompt, ask=True)

    async def handle_confirmation(self, kind: str, data: dict, affirmative: bool, ctx: Context,
                                  session: AsyncSession) -> Response:
        if not affirmative:
            return self._reply(ctx, "OK, I won't send it.")
        expires = data.get("expires_at")
        if not isinstance(expires, (int, float)) or time.time() > float(expires):
            return self._reply(ctx, "I didn't send that: it's been a while since I asked. Say it again.")
        from domovoi.friend_houses import messages

        try:
            await messages.send(str(data.get("friend_id")), str(data.get("text") or ""))
        except messages.MessageError as e:
            return self._reply(ctx, str(e))
        return self._reply(ctx, f"Sent to {data.get('name') or 'them'}.")

    # ─── check ────────────────────────────────────────────────────────
    async def _check(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        if not _on():
            return self._reply(ctx, "Friend Houses isn't turned on, so there are no messages.")
        from domovoi.friend_houses import messages

        convos = [c for c in await messages.conversations() if c["unread"]]
        if not convos:
            return self._reply(ctx, "No new messages.")
        total = sum(c["unread"] for c in convos)
        phrases = await messages.wake_phrases()
        names = [messages.safe_name(c["name"], phrases) for c in convos]
        who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        word = "message" if total == 1 else "messages"
        # The chat model's history keeps a stand-in: friend house names are
        # the friends' own words, as their messages are (see _read).
        return self._reply(ctx, f"{total} new {word}, from {who}. Say \"read my messages\" to hear them.",
                           history_text=f"I said there {'is' if total == 1 else 'are'} {total} new "
                                        f"friend house {word}.")

    # ─── read ─────────────────────────────────────────────────────────
    async def _read(self, m: re.Match[str], ctx: Context, session: AsyncSession) -> Response:
        if not _on():
            return self._reply(ctx, "Friend Houses isn't turned on, so there are no messages.")
        from domovoi.friend_houses import messages

        convos = [c for c in await messages.conversations() if c["unread"]]
        named = (m.groupdict().get("name") or "").strip()
        if named:
            wanted = {_fold(f) for f in [named]}
            convos = [c for c in convos if _fold(c["name"]) in wanted or
                      any(_fold(named) == _fold(form) for form in spoken_forms(c["name"]))]
            if not convos:
                return self._reply(ctx, f"No new messages from {named}.")
        if not convos:
            return self._reply(ctx, "No new messages.")
        phrases = await messages.wake_phrases()
        parts: list[str] = []
        read = 0
        for c in convos:
            house = bytes.fromhex(c["house"])
            unread = [x for x in await messages.conversation(house, limit=50) if x["state"] == "unread"]
            room = READ_MAX - read
            for msg in unread[:room]:
                said = messages.safe_to_say(msg["body"], phrases)
                if said:
                    parts.append(f"From {messages.safe_name(c['name'], phrases)}: {said}")
                read += 1
            if len(unread) <= room:
                await messages.mark_read(house)  # all of it was heard
            if read >= READ_MAX:
                break
        more = sum(c["unread"] for c in convos) - read
        tail = f" There {'is' if more == 1 else 'are'} {more} more in the inbox." if more > 0 else ""
        if not parts:
            return self._reply(ctx, "No new messages.")
        # The whole sentence is filtered once more: two messages side by
        # side can make a phrase that neither has alone. The chat model's
        # history keeps a stand-in, as the Lists handler does for a friend's
        # items: a friend's words never become an earlier answer to it.
        return self._reply(ctx, messages.speakable(" ".join(parts) + tail, phrases),
                           history_text=f"I read {len(parts)} friend house "
                                        f"{'message' if len(parts) == 1 else 'messages'} aloud.")


async def offer_to_friend(target: str, body: str, ctx: Context,
                          session: AsyncSession) -> Response | None:
    """Intercom's hand-off: "tell the Smiths …" routed to intercom with a
    target that is no room. When it names a friend house (and Friend Houses
    is on), read the message back and ask, as "message the Smiths …" does;
    otherwise None and intercom answers as before."""
    if not _on() or not (target or "").strip():
        return None
    found = split_friend(target, await _friends(session))
    if found is None or found[1]:
        return None  # not a friend's name (or more than one)
    return await FriendHousesHandler()._ask_to_send(found[0], (body or "").strip(), ctx, session)
