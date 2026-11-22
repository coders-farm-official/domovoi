"""The friend file: how one house invites another.

Friend Houses ("serverless messaging and friend system between households")
has no directory and no server, so a household that wants a friend hands
it a **friend file**, by any means: email, a chat app, a USB stick, a QR
code later in the phone apps. The other house imports it, which sends a
friend request over Tor; nothing happens until the inviting house accepts.

One short line of text is the whole file, so the same thing works as a
file, as pasted text and (later) as a QR code::

    domovoi-friend:1:<base64url payload>

The payload (at most 253 bytes, about 360 characters on the line):

====================  =======  =============================================
field                 bytes    why
====================  =======  =============================================
version               1        ``2`` (pairing v2, 6-digit code, 1.5.0);
                               anything else is refused (finding 17)
house key             32       the inviting house's identity (Ed25519)
session key           32       its Noise static key (X25519)
pairing onion         32       its short-lived pairing address (finding 8)
invite id             16       which invite this is
invite secret         32       proves the bearer holds the file; the Noise
                               pre-shared key, never sent over the wire
expires               4        unix day; the inviting house enforces its own
name length + name    1 + ≤40  what the house calls itself: only ever shown
                               as "calls itself…" (finding 11)
signature             64       house key over all of the above, context
                               ``domovoi-fh-friend-file-v1``
====================  =======  =============================================

The ``1`` in the prefix is the line's format and stays ``1``: the version
byte inside the signed payload is what changes, so a house too old for a
file says "from a newer Domovoi" instead of "not a friend file". A
version-1 file (from a house before 1.5.0, whose pairing uses the 4-digit
code) is refused with :data:`OLD_FILE`: this house doesn't speak that
pairing, and the version is under the inviting house's signature, so it
can't be changed in transit.

It's a **bearer secret**: anyone holding it can ask to be a friend, once.
The inviting house doesn't store the invite secret at all: it derives it
from its own key and the invite id (:meth:`HouseKeys.invite_secret`), so a
database dump never holds one.
"""

from __future__ import annotations

import base64
import re
import struct
import unicodedata
from dataclasses import dataclass

from domovoi.friend_houses import identity as fid

PREFIX = "domovoi-friend:1:"
VERSION = 2
OLDEST_KNOWN = 1  # same layout as 2; refused after its signature checks out
OLD_FILE = ("Your friend's Domovoi needs updating to add friends. Once it's updated, "
            "ask them for a new friend file.")
NAME_MAX_BYTES = 40
FILE_EXTENSION = ".domovoi-friend"
_FIXED = struct.Struct(">B32s32s32s16s32sIB")
_LINE = re.compile(r"domovoi-friend:1:([A-Za-z0-9_-]{20,400})")
MAX_TEXT = 64 * 1024  # what an import will even look through


def clean_name(name: str) -> str:
    """A house name safe to show and to speak: no control or format
    characters (bidi overrides included, finding 30), collapsed spaces, at
    most :data:`NAME_MAX_BYTES` bytes of UTF-8."""
    kept = "".join(" " if ch.isspace() else ch for ch in str(name)
                   if ch.isspace() or unicodedata.category(ch)[0] != "C")
    kept = " ".join(kept.split())
    data = kept.encode("utf-8")[:NAME_MAX_BYTES]
    return data.decode("utf-8", errors="ignore").strip()


@dataclass(frozen=True)
class FriendFile:
    house_key: bytes
    session_key: bytes
    pairing_onion: bytes
    invite_id: bytes
    invite_secret: bytes
    expires_day: int
    name: str
    signature: bytes = b""

    def body(self) -> bytes:
        name = self.name.encode("utf-8")
        return _FIXED.pack(VERSION, self.house_key, self.session_key, self.pairing_onion,
                           self.invite_id, self.invite_secret, self.expires_day,
                           len(name)) + name

    def to_text(self) -> str:
        raw = self.body() + self.signature
        return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @property
    def fingerprint(self) -> str:
        return fid.fingerprint(self.house_key)

    @property
    def pairing_address(self) -> str:
        return fid.onion_address(self.pairing_onion)


def make(keys: fid.HouseKeys, *, pairing_onion: bytes, invite_id: bytes,
         expires_day: int, name: str) -> FriendFile:
    """A signed friend file for one invite."""
    draft = FriendFile(keys.house_key, keys.session_key, pairing_onion, invite_id,
                       keys.invite_secret(invite_id), expires_day, clean_name(name))
    return FriendFile(draft.house_key, draft.session_key, draft.pairing_onion,
                      draft.invite_id, draft.invite_secret, draft.expires_day, draft.name,
                      keys.sign(fid.CTX_FRIEND_FILE, draft.body()))


class BadFriendFile(ValueError):
    """Not a friend file this house can use. The message is for a person."""


class OldFriendFile(BadFriendFile):
    """A friend file from a house that pairs with the old 4-digit code."""


def parse(text: str) -> FriendFile:
    """Find and check the friend file line in ``text`` (a whole file, a
    pasted message, a link). Size, then version, then signature, before
    any field is trusted. A version-1 file is refused with
    :class:`OldFriendFile` once its signature checks out (a byte changed in
    transit reads as damaged, not as old)."""
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise BadFriendFile("That isn't a friend file.")
    match = _LINE.search(text)
    if match is None:
        raise BadFriendFile("That isn't a friend file. Ask for the file again, or paste the whole thing.")
    encoded = match.group(1)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError) as exc:
        raise BadFriendFile("The friend file is damaged. Ask for it again.") from exc
    if len(raw) < _FIXED.size + 64:
        raise BadFriendFile("The friend file is damaged. Ask for it again.")
    version, house, session, pairing, invite_id, secret, expires, name_len = _FIXED.unpack(
        raw[: _FIXED.size])
    if version > VERSION or version < OLDEST_KNOWN:
        raise BadFriendFile("This friend file is from a newer Domovoi. Update this house first.")
    if name_len > NAME_MAX_BYTES or len(raw) != _FIXED.size + name_len + 64:
        raise BadFriendFile("The friend file is damaged. Ask for it again.")
    body, signature = raw[: _FIXED.size + name_len], raw[_FIXED.size + name_len:]
    if not fid.verify(house, fid.CTX_FRIEND_FILE, body, signature):
        raise BadFriendFile("The friend file has been changed or damaged. Ask for it again.")
    if version != VERSION:  # signed as v1 by its house: it pairs the old way
        raise OldFriendFile(OLD_FILE)
    try:
        name = body[_FIXED.size:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadFriendFile("The friend file is damaged. Ask for it again.") from exc
    return FriendFile(house, session, pairing, invite_id, secret, expires, clean_name(name),
                      signature)


def filename_for(name: str) -> str:
    """``Smiths friend file.domovoi-friend``: a name a person recognises,
    with nothing a file system dislikes."""
    safe = re.sub(r"[^A-Za-z0-9 _-]+", "", clean_name(name)).strip() or "Domovoi"
    return f"{safe[:40]} friend file{FILE_EXTENSION}"
