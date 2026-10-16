"""The spare-device enrolment payload: ``domovoi-room:1?core=…&fp=…&room=…``.

Spare-device satellites (S1, 2026-10-13). The dashboard's Settings →
Satellites → "Add a spare device" asks an admin for a room name and shows
one string two ways: inside a one-line Linux install command, and as a QR
code for a phone. Both carry the same **enrolment payload**, a URI:

    domovoi-room:1?core=<core base URL>&fp=<server fingerprint>&room=<room name>

* ``core`` — the core's base URL as the device should dial it, ``http`` or
  ``https`` only, e.g. ``http://192.168.0.117:6370``, on the home network
  (:func:`is_home_network_host`): a code someone was shown on a web page
  must not be able to send a room's microphone out of the house. The
  satellite socket is the same host and port with ``ws``/``wss``.
* ``fp`` — the core's Ed25519 fingerprint exactly as
  :func:`satellite.server_identity.fingerprint_for` writes it
  (``SHA256:`` + 43 characters of unpadded standard base64).
* ``room`` — the room name as the admin typed it (at most 64 characters).
  The device derives the room id it connects as with :func:`room_id_for`.

Every value is percent-encoded UTF-8. **The payload carries no secret**: a
device enrolled with it still has to be approved through the strict
pairing code, and what the payload adds is the pin — the device holds the
core to ``fp`` from its very first connect instead of trusting the first
server that answers.

The QR code carries the payload inside a **join link**,
``<core>/join#<payload>`` (:func:`join_link`), so a phone's own camera
opens the server's join page (which hands out the Android app); the
app's scanner and :func:`parse` take either form.

The parser is strict and the same everywhere: this module, the Android
room mode's Kotlin parser and the dashboard all test against
``docs/uml/spare-device-enrol-fixture.json``, which also pins the reason
codes below and the room ids. Stdlib only — the Linux installer runs it
with the system ``python3`` before any venv exists.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.parse
from dataclasses import asdict, dataclass

SCHEME = "domovoi-room"
VERSION = "1"
#: The server's join page, the path of the link the dashboard's QR code
#: carries (:func:`join_link`).
JOIN_PATH = "/join"

#: The longest payload accepted (a QR at error-correction M holds ~2300
#: bytes; the dashboard's are about 150).
MAX_PAYLOAD_LEN = 1024
MAX_CORE_LEN = 256
#: In characters (code points), after decoding.
MAX_ROOM_NAME_LEN = 64
#: The room id's ceiling, the same as a portal-onboarded Pi's
#: (``satellite/provisioning_protocol.py`` ROOM_ID_MAX_LEN).
MAX_ROOM_ID_LEN = 32

#: Why a payload was refused. Stable strings: the Kotlin parser returns the
#: same ones for the same inputs (the fixture says which).
REASONS = (
    "not_enrolment",        # not a domovoi-room: URI at all
    "unsupported_version",  # domovoi-room:<n> with n other than 1
    "malformed",            # bad syntax, bad %-escape, bad UTF-8, a duplicate key, too long
    "missing_core",
    "missing_fp",
    "missing_room",
    "bad_core",             # not http(s), userinfo, a path/query/fragment, no host, bad port
    "not_home_network",     # a well-formed address outside the home network
    "bad_fp",               # not SHA256: + 43 base64 characters
    "bad_room",             # empty, too long, or no letter/digit to make a room id from
)

_FP_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
#: The core's own room-id rule (domovoi/main.py ``_ROOM_ID_RE``, the USB
#: adoption form's): every id this module makes matches it.
ROOM_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
#: Put in front of an id that would start with a digit ("2nd Bedroom").
ROOM_ID_DIGIT_PREFIX = "room_"
#: The home network, for the core address (``is_home_network_host``):
#: RFC 1918 IPv4, IPv6 unique-local and link-local, and the names only a
#: local network answers for — the core's own list
#: (domovoi/transport_guard.py LOCAL_SUFFIXES) without ``.localhost``:
#: mDNS ``.local``, RFC 8375 ``.home.arpa``, the routers' ``.lan``, and
#: ``.internal`` (reserved for private use).
HOME_V4 = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
HOME_V6 = ("fc00::/7", "fe80::/10")
HOME_SUFFIXES = (".local", ".home.arpa", ".lan", ".internal")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_HEX = "0123456789abcdefABCDEF"
# RFC 3986 query characters a value may carry raw: unreserved, the
# sub-delims other than "&" and "=" (which separate pairs), ":", "@", "/"
# and "?". Everything else must be percent-encoded. A "+" is a plus sign,
# NOT a space — this is a URI, not an HTML form.
_RAW_OK = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "-._~" "!$'()*+,;" ":@/?"
)
# What build() leaves unescaped: RFC 3986 unreserved only.
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
# Dropped from a room name before the id is made, so "Kid's Room" is
# kids_room rather than kid_s_room (the portal's slug drops them too).
_APOSTROPHES = frozenset("'’")


class EnrolmentError(ValueError):
    """A payload this device refuses. ``reason`` is one of :data:`REASONS`;
    the message is safe to show (a payload never carries a secret)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        assert reason in REASONS, reason
        self.reason = reason


@dataclass(frozen=True)
class Enrolment:
    """A parsed payload. ``core`` is normalised (lower-case scheme and
    host, no trailing slash); ``ws_url`` is the satellite socket's base."""

    core: str
    ws_url: str
    fp: str
    room: str
    room_id: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


# ─── the room id ──────────────────────────────────────────────────────────

def room_id_for(name: str) -> str:
    """The room id a device enrolled as ``name`` connects as — always one
    the core's own rule accepts (:data:`ROOM_ID_RE`, ``^[a-z][a-z0-9_]{0,31}$``).

    Decompose (NFKD) and drop combining marks, so "Küche" is ``kuche`` and
    "Café" ``cafe``; drop apostrophes (' and ’); lower-case A-Z; keep a-z
    and 0-9; every other run of characters becomes one ``_``, with none at
    either end; an id that would start with a digit gets ``room_`` in
    front ("2nd Bedroom" is ``room_2nd_bedroom``). At most 32 characters.
    Raises ``bad_room`` when nothing is left — a name with no Latin letter
    or digit (a Cyrillic-only name, for now) needs one added — or when it
    is too long. ASCII rules on purpose: the Kotlin side must reach the
    same id byte for byte, and Unicode case mapping differs between
    runtimes; NFKD and the combining-mark category (Mn) do not."""
    out: list[str] = []
    gap = False
    for ch in unicodedata.normalize("NFKD", name):
        if unicodedata.category(ch) == "Mn" or ch in _APOSTROPHES:
            continue
        if "A" <= ch <= "Z":
            ch = chr(ord(ch) + 32)
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            if gap and out:
                out.append("_")
            gap = False
            out.append(ch)
        else:
            gap = True
    room_id = "".join(out)
    if room_id and room_id[0].isdigit():
        room_id = ROOM_ID_DIGIT_PREFIX + room_id
    if not room_id:
        raise EnrolmentError(
            "bad_room",
            f"the room name {name!r} has no letter or digit to make a room id "
            "from — add one (A-Z or 0-9)",
        )
    if len(room_id) > MAX_ROOM_ID_LEN:
        raise EnrolmentError(
            "bad_room",
            f"the room id {room_id!r} is longer than {MAX_ROOM_ID_LEN} "
            "characters — pick a shorter name",
        )
    assert ROOM_ID_RE.match(room_id), room_id
    return room_id


def check_room_name(name: str) -> str:
    """``name`` exactly as a payload may carry it, or raise ``bad_room``:
    at most 64 code points (not UTF-16 units), no control or format
    characters (Unicode Cc, Cf), and a room id can be made from it. Not
    trimmed here — :func:`build` trims what a person typed; a parsed name
    is kept as it came (its id ignores white space at either end anyway)."""
    if not name:
        raise EnrolmentError("bad_room", "the room name is empty")
    if len(name) > MAX_ROOM_NAME_LEN:
        raise EnrolmentError(
            "bad_room",
            f"the room name is longer than {MAX_ROOM_NAME_LEN} characters",
        )
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in name):
        raise EnrolmentError("bad_room", "the room name has control characters")
    room_id_for(name)
    return name


# ─── the core URL ─────────────────────────────────────────────────────────

def is_home_network_host(host: str) -> bool:
    """Whether ``host`` (lower-case, no brackets) is on the home network:
    an RFC 1918 IPv4 address, an IPv6 unique-local (fc00::/7) or link-local
    (fe80::/10) address, or a name ending in ``.local``, ``.home.arpa``,
    ``.lan`` or ``.internal``.
    Not loopback (a device would dial itself), not the carrier-grade NAT
    range, not a public address or a public name, even over https: a
    payload decides where a room's microphone streams to."""
    import ipaddress

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return any(host.endswith(s) and len(host) > len(s) for s in HOME_SUFFIXES)
    nets = HOME_V4 if ip.version == 4 else HOME_V6
    return any(ip in ipaddress.ip_network(n) for n in nets)


def normalize_core(value: str) -> tuple[str, str]:
    """``(core, ws_url)`` for a core base URL, or raise ``bad_core``.

    http or https; a host (an IPv6 literal in brackets, no zone id); an
    optional port 1-65535; no user, no path beyond "/", no query, no
    fragment. Scheme and host come back lower-case, without a trailing
    slash. Not trimmed: white space anywhere is refused. A well-formed
    address off the home network is ``not_home_network``."""
    text = value
    if not text or len(text) > MAX_CORE_LEN:
        raise EnrolmentError("bad_core", "the core address is empty or too long")
    if any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in text):
        raise EnrolmentError("bad_core", "the core address has spaces or non-ASCII characters")
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        raise EnrolmentError("bad_core", "the core address is not a URL") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise EnrolmentError(
            "bad_core", f"the core address must start with http:// or https://, not {text[:40]!r}"
        )
    if not text.lower().startswith(scheme + "://"):
        raise EnrolmentError("bad_core", "the core address has no host")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise EnrolmentError("bad_core", "the core address must not carry a user name")
    if parts.path not in ("", "/") or parts.query or parts.fragment or "?" in text or "#" in text:
        raise EnrolmentError("bad_core", "the core address must be just scheme://host:port")
    netloc = parts.netloc
    if netloc.startswith("["):
        end = netloc.find("]")
        if end < 0:
            raise EnrolmentError("bad_core", "the core address has an unclosed IPv6 bracket")
        host = netloc[1:end].lower()
        rest = netloc[end + 1:]
        if "%" in host:
            raise EnrolmentError("bad_core", "an IPv6 zone id (%…) is not supported")
        try:
            import ipaddress

            ipaddress.IPv6Address(host)
        except ValueError:
            raise EnrolmentError("bad_core", f"{host!r} is not an IPv6 address") from None
        shown = f"[{host}]"
    else:
        host, sep, port_text = netloc.partition(":")
        host = host.lower()
        rest = sep + port_text
        if not host or not re.fullmatch(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?", host):
            raise EnrolmentError("bad_core", f"{host!r} is not a host name or IPv4 address")
        shown = host
    port = ""
    if rest:
        if not rest.startswith(":") or not rest[1:].isdigit() or not rest[1:].isascii():
            raise EnrolmentError("bad_core", "the core address has a bad port")
        number = int(rest[1:])
        if not 1 <= number <= 65535:
            raise EnrolmentError("bad_core", "the core address's port is out of range")
        port = f":{number}"
    if not is_home_network_host(host):
        raise EnrolmentError(
            "not_home_network",
            f"{host} is not on the home network (a private address, or a name "
            "ending in .local, .home.arpa, .lan or .internal): a room must not stream its "
            "microphone out of the house",
        )
    core = f"{scheme}://{shown}{port}"
    ws_url = f"{'wss' if scheme == 'https' else 'ws'}://{shown}{port}"
    return core, ws_url


# ─── percent-encoding ─────────────────────────────────────────────────────

def _decode(value: str) -> str:
    """Percent-decode one query value, strictly: every raw character must
    be one a URI query may carry, every ``%`` must start a two-digit hex
    escape, and the bytes must be UTF-8."""
    raw = bytearray()
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "%":
            pair = value[i + 1:i + 3]
            if len(pair) != 2 or pair[0] not in _HEX or pair[1] not in _HEX:
                raise EnrolmentError("malformed", "a %-escape is not two hex digits")
            raw.append(int(pair, 16))
            i += 3
            continue
        if ch not in _RAW_OK:
            raise EnrolmentError(
                "malformed", f"the character {ch!r} must be percent-encoded"
            )
        raw.append(ord(ch))
        i += 1
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise EnrolmentError("malformed", "a value is not UTF-8") from None


def _encode(value: str) -> str:
    return "".join(
        ch if ch in _UNRESERVED else "".join(f"%{b:02X}" for b in ch.encode("utf-8"))
        for ch in value
    )


# ─── parse and build ──────────────────────────────────────────────────────

def _ascii_lower(text: str) -> str:
    """A-Z lower-cased, nothing else (Unicode case mapping differs between
    runtimes; the Kotlin parser does the same)."""
    return text.translate(_ASCII_LOWER)


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def parse(payload: str) -> Enrolment:
    """The :class:`Enrolment` a payload names, or raise
    :class:`EnrolmentError`. Unknown keys are ignored (a later version of
    the dashboard may add optional ones); a key given twice is refused.

    Also takes the **join link** the dashboard's QR code carries,
    ``<core>/join#<payload>`` (:func:`join_link`): the payload is its
    fragment, and the part before must be exactly that payload's core
    plus ``/join``."""
    if not isinstance(payload, str):
        raise EnrolmentError("not_enrolment", "the payload is not text")
    # A paste brings a newline or a space along; only those are trimmed.
    text = payload.strip(" \t\r\n")
    if _ascii_lower(text[:8]).startswith(("http://", "https://")):
        return _parse_join_link(text)
    return _parse_payload(text)


def _parse_join_link(text: str) -> Enrolment:
    base, hash_, fragment = text.partition("#")
    if not hash_ or not _ascii_lower(base).endswith(JOIN_PATH):
        raise EnrolmentError(
            "not_enrolment", "this link is not a Domovoi room code (<server>/join#domovoi-room:…)"
        )
    if len(base) > MAX_CORE_LEN + len(JOIN_PATH):
        raise EnrolmentError("malformed", "the link's server address is too long")
    enrolment = _parse_payload(fragment)
    if _ascii_lower(base) != _ascii_lower(enrolment.core + JOIN_PATH):
        raise EnrolmentError("malformed", "the link's server is not the one its room code names")
    return enrolment


def join_link(payload: str) -> str:
    """The link the dashboard's QR code carries for ``payload``:
    ``<core>/join#<payload>``. A phone's camera opens it in the browser,
    where the server's join page (domovoi/main.py ``GET /join``) offers the
    Domovoi app and then opens it as the room; the app's own scanner reads
    the payload out of it (:func:`parse`). The payload is the fragment, so
    no server ever logs it."""
    text = payload.strip(" \t\r\n")
    enrolment = _parse_payload(text)
    return f"{enrolment.core}{JOIN_PATH}#{text}"


def _parse_payload(text: str) -> Enrolment:
    head, colon, rest = text.partition(":")
    if not colon or head.lower() != SCHEME:
        raise EnrolmentError(
            "not_enrolment", "this is not a Domovoi room enrolment (domovoi-room:…)"
        )
    if len(text) > MAX_PAYLOAD_LEN:
        raise EnrolmentError("malformed", f"the payload is longer than {MAX_PAYLOAD_LEN} characters")
    version, question, query = rest.partition("?")
    if not version.isdigit() or not version.isascii():
        raise EnrolmentError("malformed", "the payload has no version (domovoi-room:1?…)")
    if version != VERSION:
        raise EnrolmentError(
            "unsupported_version",
            f"this device understands enrolment version {VERSION}, not {version} — "
            "update it, or make the code on an older Domovoi",
        )
    if not question:
        raise EnrolmentError("malformed", "the payload has no fields")
    if "#" in query:
        raise EnrolmentError("malformed", "the payload must not carry a fragment (#)")
    fields: dict[str, str] = {}
    for pair in query.split("&"):
        key, eq, value = pair.partition("=")
        if not eq or not _KEY_RE.match(key):
            raise EnrolmentError("malformed", f"{pair[:40]!r} is not a key=value field")
        if key in fields:
            raise EnrolmentError("malformed", f"the field {key!r} is given twice")
        fields[key] = _decode(value)
    for key in ("core", "fp", "room"):
        if not fields.get(key):
            raise EnrolmentError(f"missing_{key}", f"the payload has no {key}")
    core, ws_url = normalize_core(fields["core"])
    fp = fields["fp"]
    if not _FP_RE.match(fp):
        raise EnrolmentError(
            "bad_fp", "the fingerprint must be SHA256: and 43 base64 characters"
        )
    room = check_room_name(fields["room"])
    return Enrolment(core=core, ws_url=ws_url, fp=fp, room=room, room_id=room_id_for(room))


def build(core: str, fp: str, room: str) -> str:
    """The payload for these values, every value percent-encoded (RFC 3986
    unreserved characters only left as they are). Trims what a person
    typed (the room name, the address), then validates exactly what
    :func:`parse` will, so a payload this builds always parses."""
    core_norm, _ = normalize_core(core.strip())
    fp = fp.strip()
    if not _FP_RE.match(fp):
        raise EnrolmentError("bad_fp", "the fingerprint must be SHA256: and 43 base64 characters")
    name = check_room_name(room.strip())
    return (
        f"{SCHEME}:{VERSION}?core={_encode(core_norm)}"
        f"&fp={_encode(fp)}&room={_encode(name)}"
    )


def verify(enrolment: Enrolment, *, opener=None, timeout: float | None = None) -> str:
    """Make the core at ``enrolment.core`` prove it holds the key behind
    ``enrolment.fp`` — the address-bound ``/v1/health`` challenge every
    satellite runs before it connects (:func:`server_identity.verify_server`).
    Returns the proven fingerprint; raises ``server_identity.IdentityError``.
    The installer calls this before it writes anything."""
    from satellite import server_identity

    kwargs = {} if timeout is None else {"timeout": timeout}
    return server_identity.verify_server(
        enrolment.core, expected_fingerprint=enrolment.fp, opener=opener, **kwargs
    )


# ─── command line (the installer's) ───────────────────────────────────────

def _main(argv: list[str]) -> int:
    """``python3 -m satellite.enrolment parse|verify PAYLOAD``.

    ``parse`` prints the enrolment as JSON. ``verify`` also proves the core
    (exit 3 when it can't). A refused payload exits 2 with
    ``{"error": reason, "message": …}`` on stderr."""
    if len(argv) != 2 or argv[0] not in ("parse", "verify"):
        print("usage: python3 -m satellite.enrolment parse|verify 'domovoi-room:1?…'", file=sys.stderr)
        return 64
    try:
        enrolment = parse(argv[1])
    except EnrolmentError as e:
        print(json.dumps({"error": e.reason, "message": str(e)}), file=sys.stderr)
        return 2
    if argv[0] == "verify":
        from satellite import server_identity

        try:
            proven = verify(enrolment)
        except server_identity.IdentityError as e:
            print(json.dumps({"error": "unverified", "message": str(e)}), file=sys.stderr)
            return 3
        print(json.dumps({**enrolment.to_json(), "verified": proven}))
        return 0
    print(json.dumps(enrolment.to_json()))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
