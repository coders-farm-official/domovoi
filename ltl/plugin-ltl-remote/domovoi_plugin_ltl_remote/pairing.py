"""Pairing codes and the enrollment handshake with LTL.

Pairing deliberately reuses the idiom the household admin has already
met once: eight short words, the same shape as Domovoi's first-run setup
code. Someone who has set up a Domovoi server knows what to do with
``maple-heron-brick-…`` without being told.

The code is generated **here**, on the home server, and only its SHA-256
reaches LTL. That ordering matters: a dump of LTL's ``pending_enrollments``
table contains hashes, so it cannot be replayed into a claim, and LTL
never holds a secret that would let it impersonate the household during
its own pairing window.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# The 256-word list is copied from Domovoi's own setup-code generator
# rather than imported from ``domovoi.admin_auth``. That module is core
# internals, not a plugin-visible SDK surface (``sdk.core_config`` has a
# whitelist, and it is not on it), so importing it would give this plugin
# a dependency core is free to break. The words are plain ASCII so a code
# survives any console and copy-paste path.
_WORDS: tuple[str, ...] = tuple((
    "acorn apple arrow autumn badge baker basil beach berry birch bison "
    "blaze bloom bluff brass bread breeze brick brook broom bucket butter "
    "cabin candle canoe carrot cedar chalk cherry chess cider cliff clover "
    "cobble comet coral cotton cradle crane creek cricket crumb crystal "
    "daisy dawn delta denim dove drift drum dusk eagle earth ember fable "
    "falcon feather fern field finch flame flint fog forest fossil fox "
    "frost garden garlic geese ginger glade glass goose grain grape grove "
    "harbor hazel heron hill honey horse iris ivory ivy jade jasper juniper "
    "kettle kite lake lantern larch laurel leaf ledge lemon lilac linen "
    "lotus lunar maple marble meadow mint mirror moss moth mountain mulberry "
    "myrtle napkin nectar nest night north nutmeg oak oasis ocean olive "
    "onyx orchard otter owl paddle pansy paper peach pearl pebble pepper "
    "petal pine planet plum pond poplar poppy prairie quail quartz quill "
    "rain raven reed ridge river robin rocket rose rowan rustic saddle "
    "saffron sage salmon sand satin seed shell shore silver sky slate "
    "smoke snow socket sorrel spark sparrow spice spring spruce squash "
    "stone storm straw stream sugar summer sunset swan sweater table "
    "tallow tansy teapot thistle thorn thyme tiger timber toast topaz "
    "torch trail trout tulip tundra turnip twig umber valley vapor velvet "
    "vine violet wagon walnut water weave wheat willow window winter wolf "
    "wren yarn yarrow zephyr acre alder amber anchor aspen aster bank barn "
    "bay bell boat bone book bough bowl box bud bulb bush cake calf cape "
    "cart cave chime clay coal coast coin cone cork corn cove crow cup "
    "dam dew dock door down draw dune "
).split())

assert len(_WORDS) >= 256, "pairing wordlist must give >= 8 bits per word"

CODE_WORDS = 8                       # 8 x 8 bits = 64 bits of entropy
CODE_TTL = timedelta(minutes=15)
CODE_HASH_LABEL = b"ltl-remote/v1 pairing:"

_SEPARATORS = re.compile(r"[\s\-_.,]+")


class PairingError(Exception):
    """A code that cannot be parsed, or one that has expired."""


def generate_code() -> str:
    """Eight words, hyphen-joined. Uses ``secrets``, not ``random`` — the
    code is the only thing standing between a stranger and claiming this
    household during the pairing window."""
    return "-".join(secrets.choice(_WORDS[:256]) for _ in range(CODE_WORDS))


def normalize_code(raw: str) -> str:
    """Fold what a human actually typed back to canonical form.

    People retype these from a screen: they use spaces instead of
    hyphens, capitalize the first word, or paste trailing whitespace.
    None of that should be a failed pairing, so normalization happens
    before hashing on both ends.
    """
    if not isinstance(raw, str):
        raise PairingError("pairing code must be text")
    words = [w for w in _SEPARATORS.split(raw.strip().lower()) if w]
    if len(words) != CODE_WORDS:
        raise PairingError(f"pairing code must be {CODE_WORDS} words")
    unknown = [w for w in words if w not in _WORDS[:256]]
    if unknown:
        # Naming the bad word turns "invalid code" into a typo the user
        # can actually fix.
        raise PairingError(f"not a pairing word: {unknown[0]!r}")
    return "-".join(words)


def hash_code(raw: str) -> str:
    """The value that travels to LTL. Normalizes first, so the hash a
    home server registers and the hash a claim computes agree even when
    the two humans typed different whitespace."""
    return hashlib.sha256(
        CODE_HASH_LABEL + normalize_code(raw).encode("utf-8")
    ).hexdigest()


def device_fingerprint(public_key: bytes) -> str:
    """Short fingerprint for one client device — four groups of four,
    shorter than the household fingerprint because it is compared against
    a device label in a list, not read aloud between two screens."""
    hexed = hashlib.sha256(b"ltl-remote/v1 device" + public_key).hexdigest()[:16].upper()
    return " ".join(hexed[i:i + 4] for i in range(0, len(hexed), 4))


@dataclass(frozen=True)
class Enrollment:
    """What the plugin generated, and what it sends where.

    ``code`` never leaves the house — the dashboard renders it, the
    admin reads it, and only ``code_hash`` goes over the wire.
    """

    code: str
    code_hash: str
    expires_at: datetime

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    def registration_payload(
        self, *, dh_pub_b64: str, sig_pub_b64: str, fingerprint: str, hostname: str
    ) -> dict[str, str]:
        return {
            "code_hash": self.code_hash,
            "dh_public_key": dh_pub_b64,
            "sig_public_key": sig_pub_b64,
            "fingerprint": fingerprint,
            "hostname": hostname,
        }


def new_enrollment(ttl: timedelta = CODE_TTL) -> Enrollment:
    code = generate_code()
    return Enrollment(
        code=code,
        code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc) + ttl,
    )
