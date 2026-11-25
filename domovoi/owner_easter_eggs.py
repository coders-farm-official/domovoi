"""Ownership easter eggs (owner-approved lines, 2026-11-25).

"I'm the owner" / "I'm the boss" / "I'm in charge" gets a joke from
:data:`CLAIM_LINES`; "who's the owner?" / "who's in charge here?" / "who
runs this house?" gets one about the household's People list:
:data:`WHO_LINES` with two or more people, :data:`ONE_PERSON_LINES` with
one, :data:`NO_NAME_LINES` with none. Fixed lines, no model.

A claim grants nothing: who may do an admin's job is the dashboard's
admin sign-in (``Context.proven_admin``, N1), never what a voice says.
A command after the claim runs exactly as before, and its reply follows
the line (router).

Practical questions get facts, never these: "who's the admin?", "who can
approve this?", "how do I sign in as admin?", "who set this up?", "who
owns this device?" never match :data:`WHO_RE` (only owner / boss / in
charge of the house, the place, home).

``{name}`` and ``{other}`` are people on the People list (the ``people``
rows: the household's own, never a guest or an unrecognised voice), two
different ones; a line with ``{other}`` is only picked with two or more.
Each ask picks at random and never repeats the previous line for the same
room or conversation (per process).

Stdlib only (the web process's chat imports it).
"""

from __future__ import annotations

import random
import re
from typing import Any, Iterable

CLAIM_LINES: tuple[str, ...] = (
    "Sure you are.",
    "Bold claim. I'll allow it.",
    "The house has noted your confidence.",
    "Noted. Filed under things people say.",
    "Of course. And I'm a toaster.",
    "Love the energy.",
    "Very official-sounding. Carry on.",
    "A bold move. Let's see how it plays out.",
    "Duly noted, your majesty.",
    "Wonderful. I'll inform absolutely nobody.",
    "Congratulations on the promotion.",
    "Is there a crown? There should be a crown.",
    "Sure thing, boss. Wink.",
    "Says you.",
    "The paperwork must be in the mail.",
    "I believe you. Mostly.",
    "Impressive. Do you have a badge?",
    "Okay, chief.",
    "That's what they all say.",
    "Alright, captain.",
    "Big if true.",
    "Noted, with a raised eyebrow.",
    "Wow. Should I bow?",
)

WHO_LINES: tuple[str, ...] = (
    "Today? I'd say {name}. They haven't been told yet.",
    "I heard it's {name}. Don't tell {other}.",
    "Officially, the house. Unofficially, whoever's holding the remote.",
    "{name}, but only on weekends.",
    "Rumour has it {name}. I'm not confirming anything.",
    "{name}. Allegedly.",
    "Whoever made dinner. Tonight that might be {name}.",
    "{name} thinks so. {other} might disagree.",
    "I'd put my money on {name}.",
    "{name}, until {other} finds out.",
    "Depends who you ask. {name} says {name}.",
    "Whoever has the snacks. Last I checked, {name}.",
    "{name} runs the place. Quietly.",
    "I'm told it's {name}. I'm not told much.",
    "The one who knows where the batteries are. Probably {name}.",
    "{name}, with {other} as vice boss.",
    "Honestly? The dishwasher. But {name} is close.",
    "{name} is in charge of fun. Everything else is up for grabs.",
    "Whoever's loudest. Today, {name}.",
    "I can neither confirm nor deny that it's {name}.",
    "{name}, by a landslide. There was no vote.",
    "The fridge has the final say. {name} is second.",
    "{name}, but {other} has the remote.",
)

ONE_PERSON_LINES: tuple[str, ...] = (
    "Today? I'd say {name}. They haven't been told yet.",
    "{name}. Allegedly.",
    "I'd put my money on {name}.",
    "{name} runs the place. Quietly.",
    "Whoever has the snacks. Last I checked, {name}.",
    "{name}, by a landslide. There was no vote.",
)

NO_NAME_LINES: tuple[str, ...] = (
    "Officially, the house. Unofficially, whoever's holding the remote.",
    "Whoever made dinner.",
    "Honestly? The dishwasher.",
    "The fridge has the final say.",
    "Whoever knows where the batteries are.",
)

_PLACE = r"(?:this|the|our|my) (?:house|home|place|household|family)"
_HERE = rf"(?: (?:here|around here|in (?:this|the|our) (?:house|home|place)|of {_PLACE}))?"
_WHO = r"who(?:'s| is| be)"

#: Owner / boss / in-charge questions, whole turn: never "admin", a
#: device, an approval or a sign-in (those are facts).
WHO_RE = re.compile(
    r"^(?:(?:so|and|ok|okay|hey|well|then)[,\s]+)?(?:"
    rf"{_WHO} (?:the |the real |the actual |really the )?(?:owner|boss|head of {_PLACE}|top dog|big boss)"
    rf"{_HERE}"
    rf"|{_WHO} (?:really |actually )?(?:the one )?in charge{_HERE}"
    rf"|who (?:really |actually )?(?:runs|rules|owns) {_PLACE}"
    rf"|who(?:'s| is) the boss of {_PLACE}"
    r")(?:[,\s]+(?:then|anyway|really))?\Z"
)

#: The last line said, per room or conversation (process-local).
_LAST: dict[str, str] = {}
_LAST_MAX = 256


def _pick(lines: Iterable[str], key: str, rng: Any = random) -> str:
    pool = list(lines)
    last = _LAST.get(key)
    choices = [line for line in pool if line != last] or pool
    line = rng.choice(choices)
    if len(_LAST) >= _LAST_MAX and key not in _LAST:
        _LAST.pop(next(iter(_LAST)))
    _LAST[key] = line
    return line


def claim_line(key: Any = "", *, rng: Any = random) -> str:
    """A line for "I'm the owner" (and the like)."""
    return _pick(CLAIM_LINES, f"claim:{key}", rng)


def is_who_question(text: str) -> bool:
    """Whether ``text`` (normalized: lower case, no closing punctuation)
    asks who owns, runs or is in charge of the house."""
    return bool(WHO_RE.match((text or "").strip()))


def who_line(names: Iterable[str], key: Any = "", *, rng: Any = random) -> str:
    """A line for "who's the owner?", from the People list's ``names``."""
    people = list(dict.fromkeys(" ".join(str(n).split()) for n in names if str(n or "").strip()))
    if not people:
        return _pick(NO_NAME_LINES, f"who:{key}", rng)
    if len(people) == 1:
        return _pick(ONE_PERSON_LINES, f"who:{key}", rng).format(name=people[0])
    template = _pick(WHO_LINES, f"who:{key}", rng)
    name, other = rng.sample(people, 2)
    return template.format(name=name, other=other)


async def people_names(scope: Any) -> list[str]:
    """The People list's names (``people`` rows only), oldest first.
    ``scope`` opens a session (``session_scope``) or is one."""
    from sqlalchemy import text

    sql = text("SELECT name FROM people ORDER BY id")
    if hasattr(scope, "execute"):
        rows = (await scope.execute(sql)).all()
    else:
        async with scope() as s:
            rows = (await s.execute(sql)).all()
    return [str(r[0]) for r in rows if r[0]]


async def chat_reply(message: str, *, scope: Any, key: Any = "") -> str | None:
    """The typed chat's easter egg for ``message``, or None: a bare claim
    ("I'm the owner") or a who-question. A claim with more after it is the
    chat's as before."""
    from domovoi import role_claims

    t = " ".join((message or "").lower().replace("’", "'").split()).rstrip(".!?").strip()
    claim = role_claims.split_claim(t)
    if claim is not None and not claim[1] and role_claims.is_authority(claim[0]):
        return claim_line(key)
    if is_who_question(t):
        return who_line(await people_names(scope), key)
    return None


__all__ = [
    "CLAIM_LINES", "NO_NAME_LINES", "ONE_PERSON_LINES", "WHO_LINES", "WHO_RE",
    "chat_reply", "claim_line", "is_who_question", "people_names", "who_line",
]
