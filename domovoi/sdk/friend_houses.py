"""``sdk.friend_houses``: apps with friends (SDK 1.18).

A plugin with ``[permissions] friend_houses = true`` exchanges mail with
the same plugin in friend houses an admin turned "Apps with this friend"
on for (design note ``FRIEND-HOUSES-PLUGINS`` §4).

**The plugin mail service** (:mod:`domovoi.friend_houses.plugin_mail`)
does the work: :meth:`FriendHousesAPI.friends` lists the friends this
plugin may mail, :meth:`~FriendHousesAPI.send_mail` and
:meth:`~FriendHousesAPI.send_image` queue mail for one of them, and
:meth:`~FriendHousesAPI.on_mail`'s handler gets each friend's decoded
mail, every string in it a :class:`FriendText` and an image as the core's
own PNG (:class:`FriendImage`). :meth:`~FriendHousesAPI.on_friends_changed`
handlers run when ``friends()`` may answer differently.

What a friend sent reaches people only through :class:`FriendText`
(review D13): ``str()`` of one is :data:`STAND_IN`, so it never lands in
the chat model's history or a log by accident; :func:`friend_reply` and
``sdk.speech`` filter its words with the same wake-phrase filter people's
messages get (``friend_houses.messages.safe_to_say``) before a room says
them. For a plugin holding the permission the router does the same to
every reply its handlers give (:func:`guard_response`, M5).

**Tiers** (L16): ``friends()``, ``send_mail`` and ``send_image`` raise
:class:`NotAllowed` while the request in progress came in on a route
anyone on the network can call (``@open_endpoint``, or a GET that is
neither ``@device_endpoint`` nor behind ``admin_required``):
``webkit.REQUEST_TIER`` says which.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

#: What the calls raise from a route anyone on the network can call (L16).
OPEN_ROUTE = "apps with friends can't be used from an open route"
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
#: The longest line a room says for a friend_houses plugin (M5), as for a
#: friend's message read aloud (friend_houses.messages.READ_ALOUD_MAX_CHARS).
SPOKEN_MAX = 400


class FriendHousesError(Exception):
    """Base of what ``send_mail`` raises."""


class NotAllowed(FriendHousesError):
    """This plugin may not mail that friend (or may not ask from here)."""


class MailFull(FriendHousesError):
    """This plugin's outbox for that friend is full; try later."""


class TooLarge(FriendHousesError):
    """The payload is over 16,384 bytes (an image mail: its tag and image)."""


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


class FriendImage:
    """A picture a friend house sent (``send_image``), as the core's own
    PNG (review D1): the friend's bytes were opened only in that friend's
    decoder container, which answered raw pixels (at most 768 px a side);
    the core checked them and wrote this PNG itself, so it holds no
    metadata, colour profile or text. ``tag`` is the label the friend's
    copy of the plugin gave it (a :class:`FriendText`). Serve it with
    :meth:`response`; send it on with ``send_image`` (an image leaves only
    as one of these, never as the bytes a friend sent)."""

    __slots__ = ("png", "width", "height", "tag")

    def __init__(self, png: bytes, width: int, height: int, tag: "FriendText") -> None:
        from domovoi.plugins_runtime.isolated import IMAGE_MAX_SIDE, PNG_MAGIC

        if not isinstance(png, (bytes, bytearray)) or not bytes(png).startswith(PNG_MAGIC):
            raise ValueError("not one of the core's PNGs")
        if not (1 <= int(width) <= IMAGE_MAX_SIDE and 1 <= int(height) <= IMAGE_MAX_SIDE):
            raise ValueError("an image is 1..768 pixels a side")
        self.png = bytes(png)
        self.width, self.height = int(width), int(height)
        self.tag = tag if isinstance(tag, FriendText) else FriendText(str(tag))

    def response(self) -> Any:
        """The PNG as a response: ``image/png``, ``nosniff``, a sandboxing
        Content-Security-Policy, never cached."""
        from domovoi.plugins_runtime.isolated import image_response

        return image_response(self.png)

    def __repr__(self) -> str:
        return f"FriendImage({self.width}x{self.height}, {len(self.png)} bytes)"


def _image_bytes(image: Any) -> tuple[bytes, bool]:
    """``(bytes, is the core's own PNG)`` for ``send_image``'s ``image``."""
    if isinstance(image, FriendImage):
        return image.png, True
    if isinstance(image, (bytes, bytearray)):
        if not image:
            raise ValueError("the image is empty")
        return bytes(image), False
    raise TypeError("an image is a FriendImage or this plugin's own image bytes")


async def _mail_png(raw: bytes, tag: str, core_png: bool) -> bytes:
    """The core's compact PNG of ``raw`` that fits an image mail with
    ``tag`` (``png_compact.mail_png``, in the ``pictures`` pool):
    :class:`TooLarge` when it can't fit, ValueError when it isn't a picture."""
    from domovoi import executors
    from domovoi.plugins_runtime import png_compact

    budget = PAYLOAD_MAX - 1 - len(tag)
    try:
        return await executors.run("pictures", png_compact.mail_png, raw, budget, core_png=core_png)
    except png_compact.TooBig as e:
        raise TooLarge(str(e)) from None


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
    string). That plain line gets the friend text's whole filter (M5): the
    wake phrases, the assistant's name by sound, and at most
    :data:`SPOKEN_MAX` characters."""
    if isinstance(text, (FriendText, FriendSentence)):
        return await FriendSentence([text]).spoken()
    if text is None:
        return ""
    if not isinstance(text, str):
        raise TypeError("speech takes a str, a FriendText or a friend_sentence(...)")
    if always and text.strip():
        return await said_whole(text)
    return text


async def said_whole(text: str) -> str:
    """A plain line from a ``friend_houses`` plugin as a room may say it:
    ``safe_to_say`` (wake phrases, the name pass, the cap) over the whole
    line, then ``speakable`` (M5)."""
    from domovoi.friend_houses import messages

    phrases = await messages.wake_phrases()
    said = messages.safe_to_say(text, phrases, limit=SPOKEN_MAX, rest="…")
    return messages.speakable(said, phrases)


def holds_permission(slug: str | None) -> bool:
    """Whether plugin ``slug`` holds ``[permissions] friend_houses``."""
    api = _REGISTERED.get(slug) if slug else None
    return bool(api is not None and api.permitted)


async def guard_response(response: Any) -> Any:
    """M5: a reply from a handler of a plugin holding the permission. Its
    ``text`` is filtered the way ``sdk.speech`` filters (the room hears
    no wake phrase, nothing past :data:`SPOKEN_MAX`), and unless the plugin
    supplied ``history_text`` (:func:`friend_reply` does) the chat history
    and the conversation log keep :data:`STAND_IN`. Changes ``response``
    in place and returns it."""
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        return response
    if getattr(response, "history_text", None) is None:
        response.history_text = STAND_IN
    response.text = await said_whole(text)
    return response


def _refuse_on_open_route() -> None:
    """L16: these calls refuse a request anyone on the network could make."""
    from domovoi.webkit import REQUEST_TIER

    if REQUEST_TIER.get() == "open":
        raise NotAllowed(OPEN_ROUTE)


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


def registered_all() -> list["FriendHousesAPI"]:
    return list(_REGISTERED.values())


class FriendHousesAPI:
    """Injected as ``sdk.friend_houses``; never constructed by plugins."""

    def __init__(self, slug: str, *, permitted: bool = False, register: bool = True) -> None:
        self.slug = slug
        self.permitted = bool(permitted)
        self.mail_handler: MailHandler | None = None
        self.schema: dict[str, Any] | None = None
        self.images = False
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
        this plugin allowed for them. ``[]`` while Friend Houses is off or
        its private connection is dark (nothing could reach them), and
        without the permission. :class:`NotAllowed` from an open route."""
        if not self.permitted:
            return []
        _refuse_on_open_route()
        from domovoi.friend_houses import plugin_mail

        return [FriendHouse(f["house_id"], FriendText(f["name"]), bool(f["online"]),
                            bool(f["has_plugin"]))
                for f in await plugin_mail.friends_for(self.slug)]

    @staticmethod
    def _check_send(house_id: Any, ttl_s: Any, replace_key: Any) -> None:
        if not isinstance(house_id, str) or not HOUSE_ID_RE.match(house_id):
            raise ValueError("house_id is a friend's id from friends()")
        if isinstance(ttl_s, bool) or not isinstance(ttl_s, int) or not TTL_MIN <= ttl_s <= TTL_MAX:
            raise ValueError(f"ttl_s is {TTL_MIN}..{TTL_MAX} seconds")
        if replace_key is not None and (not isinstance(replace_key, str)
                                        or not REPLACE_KEY_RE.match(replace_key)):
            raise ValueError("replace_key is up to 64 of a-z 0-9 _ . : -")

    async def send_mail(self, house_id: str, payload: bytes, *, ttl_s: int = TTL_MAX,
                        replace_key: str | None = None) -> str:
        """Queue ``payload`` for the friend ``house_id`` (from
        :meth:`friends`); returns the mail's id (32 hex). The arguments are
        checked first (``TypeError``, ``ValueError``, :class:`TooLarge`);
        then :class:`NotAllowed` (not a friend this plugin may mail, the
        plugin isn't running, or an open route), :class:`MailFull` or
        :class:`RateLimited`."""
        self._need_permission()
        self._check_send(house_id, ttl_s, replace_key)
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload is bytes, rebuilt from validated values")
        if not payload:
            raise ValueError("payload is empty")
        if len(payload) > PAYLOAD_MAX:
            raise TooLarge(f"payload is {len(payload)} bytes; at most {PAYLOAD_MAX}")
        _refuse_on_open_route()
        from domovoi.friend_houses import plugin_mail

        return await plugin_mail.send_to(self.slug, house_id, bytes(payload), ttl_s=ttl_s,
                                         replace_key=replace_key)

    async def send_image(self, house_id: str, image: "FriendImage | bytes", *, tag: str,
                         ttl_s: int = TTL_MAX, replace_key: str | None = None) -> str:
        """Queue a picture for the friend ``house_id``: a :class:`FriendImage`
        (one a friend sent, passed on as the core's PNG) or this plugin's own
        PNG, JPEG or WebP bytes. ``tag`` (up to 64 of ``a-z 0-9 _ . : -``)
        tells the friend's copy what it is. The tag and the image together
        are at most 16,384 bytes, so the core writes the picture as its own
        compact PNG (no metadata), lossless when that fits, else with fewer
        colours, else scaled down (768, 512, 384, 256, 192, 128 px on the
        long side); a picture that doesn't fit even at 128 px raises
        :class:`TooLarge`, bytes that aren't a PNG, JPEG or WebP picture
        ValueError. A :class:`FriendImage` that already fits goes as it is.
        The friend's house opens it only in its decoder container and hands
        its plugin the core's own PNG, at most 768 px a side; its plugin
        must take images (``on_mail(..., images=True)``). Raises like
        :meth:`send_mail`."""
        from domovoi.friend_houses import envelope as fe

        self._need_permission()
        self._check_send(house_id, ttl_s, replace_key)
        raw, core_png = _image_bytes(image)
        if not isinstance(tag, str) or not fe.IMAGE_TAG_RE.match(tag):
            raise ValueError("tag is up to 64 of a-z 0-9 _ . : -")
        _refuse_on_open_route()
        payload = fe.image_payload(tag, await _mail_png(raw, tag, core_png))
        if len(payload) > PAYLOAD_MAX:
            raise TooLarge(f"the tag and image are {len(payload)} bytes; at most {PAYLOAD_MAX}")
        from domovoi.friend_houses import plugin_mail

        return await plugin_mail.send_to(self.slug, house_id, payload, ttl_s=ttl_s,
                                         replace_key=replace_key, version=fe.PLUGIN_BODY_IMAGE)

    def on_mail(self, handler: MailHandler, schema: dict[str, Any], *, images: bool = False) -> None:
        """Register the one handler for this plugin's mail and the schema its
        decoder's answers must match (the core's strict subset; without one
        nothing is accepted). ``handler(house_id, mail_id, seq, value)`` is
        ``async``, idempotent by ``mail_id`` and returns within 10 s. With
        ``images=True`` it also gets the friends' ``send_image`` pictures:
        ``value`` is then a :class:`FriendImage` (without it, a friend's
        image is refused, like a mail the schema refuses).

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
        self.images = bool(images)

    def on_friends_changed(self, handler: FriendsHandler) -> None:
        """``async handler()`` runs shortly after ``friends()`` may answer
        differently: "Apps with this friend" or this plugin turned on or off
        for a friend, a friend removed or blocked, a friend's house saying
        which apps it has (``has_plugin``). Several changes together call it
        once. Not for ``online``, which changes too often: read it when
        needed. Like a mail handler it gets 10 s."""
        self._need_permission()
        if not inspect.iscoroutinefunction(handler):
            raise TypeError("on_friends_changed takes an async handler")
        self.friends_handlers.append(handler)

    def teardown(self) -> None:
        self.mail_handler = None
        self.schema = None
        self.images = False
        self.friends_handlers.clear()
        if _REGISTERED.get(self.slug) is self:
            _REGISTERED.pop(self.slug, None)


__all__ = [
    "FriendHouse", "FriendHousesAPI", "FriendHousesError", "FriendImage", "FriendSentence",
    "FriendText", "MailFull", "NotAllowed", "OPEN_ROUTE", "RateLimited", "STAND_IN", "TooLarge",
    "friend_reply", "friend_sentence", "guard_response", "holds_permission", "plugin_house_id",
    "registered", "registered_all", "speakable_for",
]
