"""'I'm the owner': a role or a title said where a name would go.

"I'm Alex" and "my name is Alex" introduce someone (the voice profile
handler learns the voice under that name). "I'm the owner", "I'm the
admin", "I'm the mom", "I am a parent" don't: no name was said, and the
house never enrols "The Owner" as a person (1.4.1, case 18.8).

Nor does saying it change anything: who may do an admin's job is decided
by the admin sign-in on the dashboard, never by what a voice says about
itself (voice is never a proven admin, ``Context.proven_admin``). So:

* a role claim in front of a command ("I'm the owner, set a timer for 5
  minutes") is dropped and the command routed as said without it
  (``router.strip_request_preamble``), with its own rules;
* a role claim on its own, or in front of something no fast path takes
  ("I'm the owner, update the house"), gets a fixed answer from the voice
  profile handler: for an authority title, an ownership easter egg line
  (domovoi/owner_easter_eggs.py: a joke, never a grant); for any other
  role, how to say a name. An authority claim in front of a command adds
  that line before the command's own reply.

Stdlib only.
"""

from __future__ import annotations

import re

# Titles someone might say to claim the right to an admin's job.
AUTHORITY = (
    r"(?:home ?|house ?)?owner|owners|admin|admins|administrator|boss|landlord|landlady|manager"
    r"|head of (?:the )?(?:house|household|family)|man of the house|lady of the house|one in charge"
    # "I'm in charge", "I'm in charge here / of this house"; not "in charge
    # of dinner".
    r"|in charge(?! of (?!(?:this|the|our) (?:house|home|place|household)\b))"
)
# Everything else a person is without being named.
OTHER = (
    r"mom|mum|mommy|mummy|mother|dad|daddy|father|parent|parents|kid|kids|child|son|daughter"
    r"|husband|wife|partner|spouse|grandma|grandpa|grandmother|grandfather|granny|nana|aunt|uncle"
    r"|baby|babysitter|nanny|guest|visitor|tenant|roommate|flatmate|housemate|user|member"
    r"|cleaner|neighbou?r|teenager|teen|brother|sister|sibling|cook|chef"
)
_ARTICLE = r"(?:the|an?|your|this house's|the house's|our)"
_WHERE = r"(?: (?:here|around here|in this house|of (?:the|this) house(?:hold)?|of the family))?"

#: "I'm the owner" / "I am an admin" / "I'm the mom here", optionally
#: followed by more ("…, update the house"). An authority title also
#: without an article ("I'm admin"); any other role only with one ("I'm
#: Mom" may well be what the family calls her, and stays a name).
ROLE_CLAIM_RE = re.compile(
    rf"^i(?:'m| am) (?:{_ARTICLE} (?P<role>{AUTHORITY}|{OTHER})|(?P<title>{AUTHORITY})){_WHERE}"
    r"(?:[,.;:!?]*\s+(?:(?:and|so)\s+)?(?:please\s+)?(?P<rest>\S.*))?\Z"
)

_AUTHORITY_RE = re.compile(rf"^(?:{AUTHORITY})\Z")

#: A captured "name" that is an article phrase ("the owner", "a parent") or
#: an authority title ("admin"), never a name. A family word on its own
#: ("Mom") may be what the household calls someone, so it stays a name.
_NOT_A_NAME_RE = re.compile(
    rf"^(?:(?:the|an?|your|our|my|this|that|his|her|their) .+|(?:{AUTHORITY}))\Z"
)


def is_authority(role: str) -> bool:
    """Whether ``role`` (a ROLE_CLAIM_RE capture) claims an admin's rights."""
    return bool(_AUTHORITY_RE.match((role or "").strip().lower()))


def is_role_not_name(name: str) -> bool:
    """Whether a captured name ("the owner", "admin", "a parent") is a
    role or an article phrase rather than a name."""
    return bool(_NOT_A_NAME_RE.match(" ".join((name or "").lower().split())))


def split_claim(transcript: str) -> tuple[str, str] | None:
    """``(role, rest)`` for a role claim (normalized, lower case), rest ""
    when nothing follows it; None when ``transcript`` isn't one."""
    m = ROLE_CLAIM_RE.match(transcript or "")
    if m is None:
        return None
    role = m.group("role") or m.group("title") or ""
    return role, (m.group("rest") or "").strip().rstrip(".,!?").strip()


__all__ = ["AUTHORITY", "OTHER", "ROLE_CLAIM_RE", "is_authority", "is_role_not_name", "split_claim"]
