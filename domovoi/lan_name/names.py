"""The network name's rules: what a household may type, and the default's
``domovoi``, ``domovoi2``, ``domovoi3`` ... sequence (owner, 2026-11-11: no
dash).

One function, :func:`normalize`, is the whole rule; DomovoiHost's copy
(``windows/common/MdnsResponder.cs``, ``LanName.Normalize``) and the Mac
helper's (to come) run the same golden list, ``vectors.json`` beside this
file, so the three can't drift.
"""

from __future__ import annotations

import re

DEFAULT = "domovoi"
#: The last number the default may take (``domovoi99``).
MAX_SUFFIX = 99
MAX_LEN = 63

#: The ``_HOST_LABEL`` rule of domovoi/streaming.py: letters, digits and
#: hyphens, 1-63, no hyphen at either end.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")   # fullmatch only
_RESERVED = frozenset({"localhost", "local"})
#: The only characters trimmed: ASCII whitespace, as C#'s copy trims.
_ASCII_SPACE = " \t\n\r\f\v"
#: The longest thing :func:`normalize` looks at, typed ".local" included.
_MAX_INPUT = 80

HINT = "letters, digits and hyphens · up to 63"


class BadName(ValueError):
    """A name that can't be used, with why in the dashboard's words."""


def normalize(raw: object) -> str:
    """The label a typed name stands for: trimmed, lowercased, a trailing
    dot and ``.local`` taken off. :class:`BadName` when it isn't one.

    ASCII only, checked first: no Unicode whitespace is trimmed and no
    Unicode letter lowercased into an ASCII one (U+212A KELVIN SIGN
    lowercases to "k"), so the C# copy, which trims and lowercases ASCII
    only, can't disagree (security review 2026-11-12, I1/I2)."""
    if not isinstance(raw, str):
        raise BadName("no name given")
    if not raw.isascii():
        raise BadName("letters, digits and hyphens only")
    s = raw.strip(_ASCII_SPACE)
    if len(s) > _MAX_INPUT:
        raise BadName("too long: up to 63 letters, digits and hyphens")
    s = s.lower()
    if s.endswith("."):
        s = s[:-1]
    if s.endswith(".local"):
        s = s[: -len(".local")]
    if not s:
        raise BadName("no name given")
    if len(s) > MAX_LEN:
        raise BadName("too long: up to 63 letters, digits and hyphens")
    if not _LABEL.fullmatch(s):
        if "." in s:
            raise BadName("one word only: no dots")
        if s.startswith("-") or s.endswith("-"):
            raise BadName("can't start or end with a hyphen")
        raise BadName("letters, digits and hyphens only")
    if s in _RESERVED:
        raise BadName(f"{s} is kept for this device itself")
    return s


def is_valid(raw: object) -> bool:
    try:
        normalize(raw)
    except BadName:
        return False
    return True


def family(name: str) -> list[str]:
    """The names the default walks through, in order: ``domovoi``,
    ``domovoi2`` ... ``domovoi99``. Only the default has one; a name the
    household typed is never changed by us."""
    if name != DEFAULT:
        return [name]
    return [DEFAULT] + [f"{DEFAULT}{n}" for n in range(2, MAX_SUFFIX + 1)]


def in_family(requested: str, name: str | None) -> bool:
    return bool(name) and name in family(requested)


def fqdn(name: str | None) -> str | None:
    return f"{name}.local" if name else None
