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
length, signature, UTF-8. Expiry and ordering never trust ``sent_at``
(finding 19): the receiver's own clock and (epoch, seq) do that.
"""

from __future__ import annotations

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
KINDS = frozenset({KIND_TEXT, KIND_SPACE})
TEXT_MAX_CHARS = 2000            # Limits p4
ENVELOPE_MAX = 63 * 1024          # one Noise message with room to spare
_HEADER = struct.Struct(">BB32s32s16s8sQQH")
SIGNATURE = 64


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
        return self.body.decode("utf-8")


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
           sent_at: int) -> Envelope:
    """Any envelope, signed. Callers check their own body first."""
    if kind not in KINDS:
        raise ValueError("unknown kind")
    if len(body) > ENVELOPE_MAX - _HEADER.size - SIGNATURE:
        raise ValueError("body too large")
    draft = Envelope(kind, keys.house_key, to, secrets.token_bytes(16), epoch, seq,
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
