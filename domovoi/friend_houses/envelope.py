"""A Friend Houses message, as it travels and as it is kept.

Friend Houses ("serverless messaging and friend system between households").
Every message is one **envelope** of at most :data:`ENVELOPE_MAX` bytes,
sent as a single frame inside the authenticated Noise session between two
houses (:mod:`domovoi.friend_houses.session`). No chunking: text fits.

The envelope is **signed** by the sending house's key (owner decision 12:
signed proof, not deniability), over a context string and every header
field (threat-model finding 17), so a receiving house can later show
exactly what a friend sent. The session already proves who is on the other
end; the signature is what makes the stored copy evidence.

Layout (big-endian)::

    version     1   (1)
    kind        1   (KIND_TEXT)
    from        32  the sender's house key
    to          32  the recipient's house key
    msg_id      16  random, chosen by the sender
    epoch       8   the sender's sending epoch (identity.resolve_epoch)
    seq         8   per epoch, for display order
    sent_at     8   unix seconds by the SENDER's clock: display only
    body_len    2
    body        body_len bytes, UTF-8
    signature   64

The receiver refuses in this order: size, header, version and kind, body
length, signature, then the kind's own body rules (UTF-8 and length for
text, the layout below for plugin mail). Expiry and ordering never trust
``sent_at`` (finding 19): the receiver's own clock and (epoch, seq) do that.

Plugin mail (``KIND_PLUGIN``, 1.5.0)
------------------------------------
Mail between two houses' copies of one plugin ("apps with friends"). It
uses the same signed header; ``seq`` is the per-(friend, plugin) counter,
not the house-wide one. The body, all of it under the signature::

    ver         1   (1)
    slug_len    1
    slug        slug_len bytes: the manifest's rule, ^[a-z][a-z0-9_]{1,31}$
    ttl_s       4   how long the mail may wait, 30..259,200 seconds
    payload     the rest: 1..16,384 bytes, opaque here

consumed to the exact length. ``ttl_s`` is the time the mail has LEFT when
it is sent (a resend carries less, L13b). The payload is never parsed by
the listener or by core's message code: it goes only to the plugin mail
service (:mod:`domovoi.friend_houses.plugin_mail`). People's messages and plugin mail never
share a path: :func:`peek` lets the listener sort them before it spends
any budget, and core dispatches on the kind and refuses anything it has no
handler for.
"""

from __future__ import annotations

import re
import secrets
import struct
import unicodedata
from dataclasses import dataclass

from domovoi.friend_houses import identity as fid

VERSION = 1
KIND_TEXT = 1
# A batch of changes to something two houses share (a list, F4): the body is
# binary, in the layout of domovoi/friend_houses/spaces.py.
KIND_SPACE = 2
# Plugin mail (1.5.0): the body layout is in the module docstring.
KIND_PLUGIN = 3
KINDS = frozenset({KIND_TEXT, KIND_SPACE, KIND_PLUGIN})
#: The kinds that are people's messages and lists (the 60-an-hour budget).
PEOPLE_KINDS = frozenset({KIND_TEXT, KIND_SPACE})
TEXT_MAX_CHARS = 2000            # Limits p4
ENVELOPE_MAX = 63 * 1024          # one Noise message with room to spare
_HEADER = struct.Struct(">BB32s32s16s8sQQH")
SIGNATURE = 64

PLUGIN_BODY_VERSION = 1
PLUGIN_PAYLOAD_MAX = 16 * 1024
PLUGIN_TTL_MIN = 30
PLUGIN_TTL_MAX = 259_200          # 3 days (owner decision 10)
#: The plugin manifest's slug rule (plugins_runtime/manifest.py SLUG_RE; a
#: test keeps them equal). Copied, not imported: the listener's container
#: carries only the Friend Houses modules.
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}\Z")
SLUG_MAX = 32
_PLUGIN_TTL = struct.Struct(">I")
PLUGIN_BODY_MAX = 2 + SLUG_MAX + _PLUGIN_TTL.size + PLUGIN_PAYLOAD_MAX
PLUGIN_ENVELOPE_MAX = _HEADER.size + PLUGIN_BODY_MAX + SIGNATURE


class BadEnvelope(ValueError):
    """Refused. Never says more than that to the sender."""


@dataclass(frozen=True)
class Envelope:
    kind: int
    sender: bytes
    recipient: bytes
    msg_id: bytes
    epoch: bytes
    seq: int
    sent_at: int
    body: bytes
    signature: bytes = b""

    def signed_part(self) -> bytes:
        return _HEADER.pack(VERSION, self.kind, self.sender, self.recipient, self.msg_id,
                            self.epoch, self.seq, self.sent_at, len(self.body)) + self.body

    def to_bytes(self) -> bytes:
        return self.signed_part() + self.signature

    @property
    def text(self) -> str:
        if self.kind != KIND_TEXT:  # plugin mail or a list change is never text
            raise ValueError("not a text message")
        return self.body.decode("utf-8")

    @property
    def plugin(self) -> PluginBody:
        """The plugin mail body (``KIND_PLUGIN`` only)."""
        if self.kind != KIND_PLUGIN:
            raise ValueError("not plugin mail")
        return parse_plugin_body(self.body)


@dataclass(frozen=True)
class PluginBody:
    slug: str
    ttl_s: int
    payload: bytes

    def to_bytes(self) -> bytes:
        slug = self.slug.encode("ascii")
        return (bytes([PLUGIN_BODY_VERSION, len(slug)]) + slug + _PLUGIN_TTL.pack(self.ttl_s)
                + self.payload)


def valid_slug(slug: object) -> bool:
    return isinstance(slug, str) and SLUG_RE.match(slug) is not None


def parse_plugin_body(body: bytes) -> PluginBody:
    """A plugin mail body, checked to the exact length (BadEnvelope)."""
    if not 2 + 2 + _PLUGIN_TTL.size + 1 <= len(body) <= PLUGIN_BODY_MAX:
        raise BadEnvelope("plugin body size")
    if body[0] != PLUGIN_BODY_VERSION:
        raise BadEnvelope("plugin body version")
    slug_len = body[1]
    if not 2 <= slug_len <= SLUG_MAX:
        raise BadEnvelope("slug length")
    end = 2 + slug_len
    try:
        slug = bytes(body[2:end]).decode("ascii")
    except UnicodeDecodeError as exc:
        raise BadEnvelope("slug") from exc
    if not valid_slug(slug):
        raise BadEnvelope("slug")
    if len(body) < end + _PLUGIN_TTL.size + 1:
        raise BadEnvelope("plugin body size")
    (ttl_s,) = _PLUGIN_TTL.unpack(body[end:end + _PLUGIN_TTL.size])
    if not PLUGIN_TTL_MIN <= ttl_s <= PLUGIN_TTL_MAX:
        raise BadEnvelope("ttl")
    payload = bytes(body[end + _PLUGIN_TTL.size:])
    if not 1 <= len(payload) <= PLUGIN_PAYLOAD_MAX:
        raise BadEnvelope("payload size")
    return PluginBody(slug, ttl_s, payload)


def plugin_mail(keys: fid.HouseKeys, *, to: bytes, slug: str, payload: bytes, ttl_s: int,
                epoch: bytes, seq: int, sent_at: int, msg_id: bytes | None = None) -> Envelope:
    """A signed plugin mail. ValueError for a bad slug, TTL or payload.
    ``msg_id`` is the outbox row's mail id: a resend keeps it (with the
    remaining ``ttl_s``, so a new signature), and the receiver ACKs it as
    the duplicate it is."""
    if not valid_slug(slug):
        raise ValueError("bad plugin slug")
    body = PluginBody(slug, int(ttl_s), bytes(payload)).to_bytes()
    parse_plugin_body(body)  # the same rules the receiver applies
    return signed(keys, kind=KIND_PLUGIN, to=to, body=body, epoch=epoch, seq=seq,
                  sent_at=sent_at, msg_id=msg_id)


@dataclass(frozen=True)
class Peek:
    """What the listener reads of an envelope before spending any budget
    or checking the signature: never trusted beyond sorting it."""

    kind: int
    msg_id: bytes
    slug: str | None  # plugin mail only


def peek(data: bytes) -> Peek:
    """Size, header, version and kind, length, and for plugin mail the slug.
    BadEnvelope for anything malformed. The signature is NOT checked."""
    if not isinstance(data, (bytes, bytearray)) or not \
            _HEADER.size + SIGNATURE <= len(data) <= ENVELOPE_MAX:
        raise BadEnvelope("size")
    head = _HEADER.unpack(data[: _HEADER.size])
    version, kind, msg_id, body_len = head[0], head[1], head[4], head[8]
    if version != VERSION or kind not in KINDS:
        raise BadEnvelope("version or kind")
    if len(data) != _HEADER.size + body_len + SIGNATURE:
        raise BadEnvelope("length")
    slug = None
    if kind == KIND_PLUGIN:
        if len(data) > PLUGIN_ENVELOPE_MAX:
            raise BadEnvelope("size")
        slug = parse_plugin_body(bytes(data[_HEADER.size: _HEADER.size + body_len])).slug
    return Peek(kind, bytes(msg_id), slug)


def clean_text(text: str) -> str:
    """Message text with nothing a screen or a speaker could be tricked by:
    control and format characters (bidi overrides included) dropped, except
    line breaks and tabs; outer whitespace trimmed."""
    out = []
    for ch in str(text):
        if ch in "\n\t":
            out.append(ch)
        elif unicodedata.category(ch)[0] != "C":
            out.append(ch)
    return "".join(out).strip()


def text_message(keys: fid.HouseKeys, *, to: bytes, text: str, epoch: bytes, seq: int,
                 sent_at: int) -> Envelope:
    """A signed text message. ValueError for empty or too-long text."""
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("a message needs some text")
    if len(cleaned) > TEXT_MAX_CHARS:
        raise ValueError(f"a message is at most {TEXT_MAX_CHARS} characters")
    return signed(keys, kind=KIND_TEXT, to=to, body=cleaned.encode("utf-8"), epoch=epoch,
                  seq=seq, sent_at=sent_at)


def signed(keys: fid.HouseKeys, *, kind: int, to: bytes, body: bytes, epoch: bytes, seq: int,
           sent_at: int, msg_id: bytes | None = None) -> Envelope:
    """Any envelope, signed. Callers check their own body first."""
    if kind not in KINDS:
        raise ValueError("unknown kind")
    if len(body) > ENVELOPE_MAX - _HEADER.size - SIGNATURE:
        raise ValueError("body too large")
    if kind == KIND_PLUGIN:
        parse_plugin_body(body)
    if msg_id is not None and len(msg_id) != 16:
        raise ValueError("msg_id is 16 bytes")
    draft = Envelope(kind, keys.house_key, to, msg_id or secrets.token_bytes(16), epoch, seq,
                     sent_at, body)
    return Envelope(draft.kind, draft.sender, draft.recipient, draft.msg_id, draft.epoch,
                    draft.seq, draft.sent_at, draft.body,
                    keys.sign(fid.CTX_MESSAGE, draft.signed_part()))


def parse(data: bytes, *, sender: bytes, recipient: bytes) -> Envelope:
    """A checked envelope from ``sender`` (the house the session
    authenticated) to ``recipient`` (this house)."""
    if not isinstance(data, (bytes, bytearray)) or not \
            _HEADER.size + SIGNATURE <= len(data) <= ENVELOPE_MAX:
        raise BadEnvelope("size")
    (version, kind, frm, to, msg_id, epoch, seq, sent_at,
     body_len) = _HEADER.unpack(data[: _HEADER.size])
    if version != VERSION or kind not in KINDS:
        raise BadEnvelope("version or kind")
    if len(data) != _HEADER.size + body_len + SIGNATURE:
        raise BadEnvelope("length")
    if kind == KIND_PLUGIN and len(data) > PLUGIN_ENVELOPE_MAX:
        raise BadEnvelope("size")
    if not (secrets.compare_digest(frm, sender) and secrets.compare_digest(to, recipient)):
        raise BadEnvelope("not from this sender to this house")
    signed_part, signature = bytes(data[: -SIGNATURE]), bytes(data[-SIGNATURE:])
    if not fid.verify(frm, fid.CTX_MESSAGE, signed_part, signature):
        raise BadEnvelope("signature")
    body = signed_part[_HEADER.size:]
    if kind == KIND_TEXT:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BadEnvelope("encoding") from exc
        if not (0 < len(text) <= TEXT_MAX_CHARS):
            raise BadEnvelope("text length")
    elif kind == KIND_PLUGIN:
        parse_plugin_body(body)  # kind-specific bounds (review D12)
    return Envelope(kind, frm, to, msg_id, epoch, seq, sent_at, body, signature)


def verify_stored(data: bytes) -> bool:
    """Whether a kept envelope still checks out: the proof decision 12 is for.
    Anyone holding the sender's house key (it's public) can run this."""
    try:
        frm = bytes(data[2:34])
        to = bytes(data[34:66])
        parse(data, sender=frm, recipient=to)
    except (BadEnvelope, struct.error, IndexError):
        return False
    return True
