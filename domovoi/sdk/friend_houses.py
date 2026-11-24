"""``sdk.friend_houses``: apps with friends (SDK 1.18).

A plugin with ``[permissions] friend_houses = true`` exchanges mail with
the same plugin in friend houses an admin turned "Apps with this friend"
on for (design note ``FRIEND-HOUSES-PLUGINS`` §4).

**The plugin mail service** (:mod:`domovoi.friend_houses.plugin_mail`)
stores friends' mail and delivers it to :meth:`FriendHousesAPI.on_mail`'s
handler, with every string in the value as a :class:`FriendText`. The rest
of the routing arrives later in 1.5.0; until then:

* :meth:`FriendHousesAPI.friends` answers ``[]``;
* :meth:`FriendHousesAPI.send_mail` checks its arguments, then raises
  :class:`NotAllowed` with :data:`NOT_AVAILABLE_YET`;
* :meth:`~FriendHousesAPI.on_friends_changed` records the handler, and
  nothing calls it.

What a friend sent reaches people only through :class:`FriendText`
(review D13): ``str()`` of one is :data:`STAND_IN`, so it never lands in
the chat model's history or a log by accident; :func:`friend_reply` and
``sdk.speech`` filter its words with the same wake-phrase filter people's
messages get (``friend_houses.messages.safe_to_say``) before a room says
them.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

#: What ``send_mail`` raises until its routing arrives (later in 1.5.0).
NOT_AVAILABLE_YET = "Friend Houses for apps isn't available yet"
#: What a friend's words become in the chat history and in ``str()``.
STAND_IN = "[text from a friend house]"

# The wire's bounds (friend_houses/envelope.py; a test keeps them equal).
PAYLOAD_MAX = 16 * 1024
TTL_MIN = 30
TTL_MAX = 259_200                 # 3 days, also the default
REPLACE_KEY_RE = re.compile(r"^[a-z0-9_.:-]{1,64}\Z")
HOUSE_ID_RE = re.compile(r"^[0-9a-f]{32}\Z")
#: The longest friend string a decoder may answer (isolated.STRING_MAX).
TEXT_MAX = 2048
HANDLER_DEADLINE_SEC = 10         # §6.7: a handler that runs longer is a fault


class FriendHousesError(Exception):
    """Base of what ``send_mail`` raises."""


class NotAllowed(FriendHousesError):
    """This plugin may not mail that friend (or plugin mail isn't available yet)."""


class MailFull(FriendHousesError):
    """This plugin's outbox for that friend is full; try later."""


class TooLarge(FriendHousesError):
    """The payload is over 16,384 bytes."""


class RateLimited(FriendHousesError):
    """Over a sending budget; try later."""


# ─── per-plugin friend ids ───────────────────────────────────────────────


from domovoi.friend_houses.identity import plugin_house_id  # noqa: E402  (re-exported)


# ─── what a friend wrote ─────────────────────────────────────────────────


class FriendText:
    """Words a friend house supplied (a name, a chat line in an app).

    ``str()`` and formatting give :data:`STAND_IN`, never the words, so an
    f-string can't carry them into a reply's chat history, a log or a
    spoken line unfiltered. A plugin page shows ``.text`` as plain text
    (never as HTML); a room hears it only through :func:`friend_reply` or
    ``sdk.speech``, which filter it first."""

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        if isinstance(text, FriendText):
            text = text.text
        if not isinstance(text, str):
            raise TypeError("FriendText wraps a str")
        self._text = text[:TEXT_MAX]

    @property
    def text(self) -> str:
        """The words as the friend sent them (cut at 2,048 characters)."""
        return self._text

    def __str__(self) -> str:
        return STAND_IN

    def __format__(self, spec: str) -> str:
        return format(STAND_IN, spec)

    def __repr__(self) -> str:
        return f"FriendText(<{len(self._text)} characters>)"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FriendText) and other._text == self._text

    def __hash__(self) -> int:
        return hash(("FriendText", self._text))

    def __add__(self, other: object):
        raise TypeError("join FriendText with friend_sentence(...), not +")

    __radd__ = __add__

    def __bool__(self) -> bool:
        return bool(self._text.strip())


class FriendSentence:
    """A line made of this plugin's own words and a friend's
    (:func:`friend_sentence`): ``history()`` puts :data:`STAND_IN` where
    the friend's words were; ``await spoken()`` filters them for a room."""

    __slots__ = ("parts",)

    def __init__(self, parts: Iterable[Any]) -> None:
        out: list[str | FriendText] = []
        for p in parts:
            if isinstance(p, FriendSentence):
                out.extend(p.parts)
            elif isinstance(p, (str, FriendText)):
                out.append(p)
            else:
                raise TypeError("a friend sentence is made of str and FriendText")
        self.parts: tuple[str | FriendText, ...] = tuple(out)

    @property
    def from_friend(self) -> bool:
        return any(isinstance(p, FriendText) for p in self.parts)

    def history(self) -> str:
        """The line as the chat history keeps it."""
        return "".join(STAND_IN if isinstance(p, FriendText) else p for p in self.parts)

    def __str__(self) -> str:
        return self.history()

    def __repr__(self) -> str:
        return f"FriendSentence({self.history()!r})"

    def said_with(self, phrases: list[str]) -> str:
        """The line as a room may say it, given the house's wake phrases:
        each friend part through ``safe_to_say``, then the whole line
        through ``speakable`` (two parts side by side can make a phrase
        neither is alone)."""
        from domovoi.friend_houses import messages

        said = "".join(messages.safe_to_say(p.text, phrases) if isinstance(p, FriendText) else p
                       for p in self.parts)
        return messages.speakable(said, phrases)

    async def spoken(self) -> str:
        from domovoi.friend_houses import messages

        return self.said_with(await messages.wake_phrases())


def friend_sentence(*parts: Any) -> FriendSentence:
    """``friend_sentence("Sam played ", FriendText(card), ".")``."""
    return FriendSentence(parts)


async def friend_reply(*parts: Any, **response_fields: Any):
    """A handler's :class:`~domovoi.models.Response` whose words include a
    friend's: the room hears them filtered, and the chat history keeps
    :data:`STAND_IN` in their place (``Response.history_text``)."""
    from domovoi.models import Response

    line = FriendSentence(parts)
    if "text" in response_fields or "history_text" in response_fields:
        raise TypeError("friend_reply sets text and history_text itself")
    return Response(text=await line.spoken(), history_text=line.history(), **response_fields)


async def speakable_for(text: Any, *, always: bool = False) -> str:
    """What ``sdk.speech`` says for ``text``: a :class:`FriendText` or
    :class:`FriendSentence` filtered; a plain ``str`` as is, unless
    ``always`` (a plugin with the ``friend_houses`` permission: every line
    it says is filtered, in case friend words reached it as a plain
    string)."""
    if isinstance(text, (FriendText, FriendSentence)):
        return await FriendSentence([text]).spoken()
    if text is None:
        return ""
    if not isinstance(text, str):
        raise TypeError("speech takes a str, a FriendText or a friend_sentence(...)")
    if always and text.strip():
        return await FriendSentence([text]).spoken()
    return text


# ─── the API ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FriendHouse:
    """One friend house as this plugin sees it."""

    house_id: str          # this plugin's id for the friend (plugin_house_id)
    name: FriendText       # the friend's name: shown as plain text, filtered when said
    online: bool
    has_plugin: bool       # its last CAPS listed this plugin, fresh (§6.8)


MailHandler = Callable[[str, str, int, Any], Awaitable[Any]]
FriendsHandler = Callable[[], Awaitable[Any]]

#: slug -> the plugin's API, for the mail service to find handlers by.
_REGISTERED: dict[str, "FriendHousesAPI"] = {}


def registered(slug: str) -> "FriendHousesAPI | None":
    return _REGISTERED.get(slug)


class FriendHousesAPI:
    """Injected as ``sdk.friend_houses``; never constructed by plugins."""

    def __init__(self, slug: str, *, permitted: bool = False, register: bool = True) -> None:
        self.slug = slug
        self.permitted = bool(permitted)
        self.mail_handler: MailHandler | None = None
        self.schema: dict[str, Any] | None = None
        self.friends_handlers: list[FriendsHandler] = []
        if register:
            _REGISTERED[slug] = self

    def _need_permission(self) -> None:
        if not self.permitted:
            raise NotAllowed(
                f"plugin {self.slug!r} doesn't declare [permissions] friend_houses = true"
            )

    async def friends(self) -> list[FriendHouse]:
        """The friends this plugin may mail: "Apps with this friend" on and
        this plugin allowed for them. ``[]`` until its routing arrives
        (later in 1.5.0) (and always without the permission)."""
        return []

    async def send_mail(self, house_id: str, payload: bytes, *, ttl_s: int = TTL_MAX,
                        replace_key: str | None = None) -> str:
        """Queue ``payload`` for the friend ``house_id``; returns the mail's
        id. The arguments are checked first (``TypeError``, ``ValueError``,
        :class:`TooLarge`); then, until its routing arrives, it
        raises :class:`NotAllowed` with :data:`NOT_AVAILABLE_YET`. Nothing
        is queued."""
        self._need_permission()
        if not isinstance(house_id, str) or not HOUSE_ID_RE.match(house_id):
            raise ValueError("house_id is a friend's id from friends()")
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload is bytes, rebuilt from validated values")
        if not payload:
            raise ValueError("payload is empty")
        if len(payload) > PAYLOAD_MAX:
            raise TooLarge(f"payload is {len(payload)} bytes; at most {PAYLOAD_MAX}")
        if isinstance(ttl_s, bool) or not isinstance(ttl_s, int) or not TTL_MIN <= ttl_s <= TTL_MAX:
            raise ValueError(f"ttl_s is {TTL_MIN}..{TTL_MAX} seconds")
        if replace_key is not None and (not isinstance(replace_key, str)
                                        or not REPLACE_KEY_RE.match(replace_key)):
            raise ValueError("replace_key is up to 64 of a-z 0-9 _ . : -")
        raise NotAllowed(NOT_AVAILABLE_YET)

    def on_mail(self, handler: MailHandler, schema: dict[str, Any]) -> None:
        """Register the one handler for this plugin's mail and the schema its
        decoder's answers must match (the core's strict subset; without one
        nothing is accepted). ``handler(house_id, mail_id, seq, value)`` is
        ``async``, idempotent by ``mail_id`` and returns within 10 s.

        The plugin mail service calls it for each decoded mail, in ``seq``
        order per friend, **at least once**: again after it raised, timed
        out or the core stopped while it ran, until the mail is quarantined
        (its second fault). Every string in ``value`` is a
        :class:`FriendText`. It must never block the event loop: core
        can't stop a handler that never awaits; that takes the whole core
        down, and the friend's mail is then charged as having done so."""
        self._need_permission()
        if not inspect.iscoroutinefunction(handler):
            raise TypeError("on_mail takes an async handler")
        if schema is None:
            raise ValueError("on_mail needs a schema: without one nothing is accepted")
        from domovoi.plugins_runtime.isolated import compile_schema

        self.schema = compile_schema(schema)
        self.mail_handler = handler

    def on_friends_changed(self, handler: FriendsHandler) -> None:
        """``async handler()`` runs when ``friends()`` may answer differently.
        Recorded only, until its routing arrives (later in 1.5.0)."""
        self._need_permission()
        if not inspect.iscoroutinefunction(handler):
            raise TypeError("on_friends_changed takes an async handler")
        self.friends_handlers.append(handler)

    def teardown(self) -> None:
        self.mail_handler = None
        self.schema = None
        self.friends_handlers.clear()
        if _REGISTERED.get(self.slug) is self:
            _REGISTERED.pop(self.slug, None)


__all__ = [
    "FriendHouse", "FriendHousesAPI", "FriendHousesError", "FriendSentence", "FriendText",
    "MailFull", "NOT_AVAILABLE_YET", "NotAllowed", "RateLimited", "STAND_IN", "TooLarge",
    "friend_reply", "friend_sentence", "plugin_house_id", "registered", "speakable_for",
]
