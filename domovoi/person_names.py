"""People's names as speech gives them: spelled-out letters, a corrected
name in a reply, a name read back letter by letter, and names that sound
like a household member's.

Speaker enrolment (``handlers/voice_profile.py``) asks "Nice to meet you,
Jordan. That's J, O, R, D, A, N. Did I get that right?" and reads the reply
here. Before 2026-11-11 only a reply that began with yes or no reached the
question, and a "yes, but it's spelled J-O-R-D-Y-N" committed the name as
first heard: a household member was saved under a common misspelling of
their name. Now:

* :func:`read_name_reply` sorts a reply into yes / no / a name (corrected or
  spelled) / a correction it couldn't read / cancel / not about the name.
  "Yes, but …" and "yes, it's spelled …" are corrections, never a yes.
* :func:`letters_of` reads the shapes Whisper writes a spelling in —
  "S A M", "S-A-M", "S. A. M.", "s, a, m", "ess ay em", "jay oh ar dee" —
  as the letters.
* :func:`spell_out` reads a name back for text-to-speech as "J, O, R, D, A,
  N": separated by commas every letter is said as its name (measured with
  Domovoi's phonemizer: "A-L-E-X" runs together, and a lone "A" without a
  comma is read as the article).
* :func:`sound_alike_people` finds household members whose name sounds
  like a heard one ("Jordyn" for Jordan): the introduction then asks "Did
  you mean Jordan?" instead of making a second person.

Pure, no I/O. The fuzzy/phonetic helpers come from
``handlers/shared/spoken_names.py`` (Double Metaphone + rapidfuzz, already
core dependencies), imported only when a sound-alike check runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# The English names of the letters as a transcript writes them. The
# library resolver's table (spoken_names._LETTER_NAMES), plus a few more
# a spelling answer produces ("aye", "double u").
_LETTER_WORDS = {
    "ay": "a", "aye": "a", "bee": "b", "be": "b", "see": "c", "cee": "c", "sea": "c",
    "dee": "d", "ee": "e", "ef": "f", "eff": "f", "gee": "g", "aitch": "h", "haitch": "h",
    "eye": "i", "jay": "j", "kay": "k", "el": "l", "ell": "l", "em": "m",
    "en": "n", "oh": "o", "pee": "p", "cue": "q", "queue": "q", "ar": "r",
    "are": "r", "es": "s", "ess": "s", "tee": "t", "tea": "t", "you": "u",
    "vee": "v", "doubleyou": "w", "doubleu": "w", "ex": "x", "why": "y", "wye": "y",
    "zee": "z", "zed": "z",
}

#: A name has at least this many letters to count as spelled.
MIN_SPELLED_LETTERS = 2
#: The longest name taken from a reply, in words.
MAX_NAME_WORDS = 3


def letters_of(text: str | None) -> str | None:
    """The letters of a name spelled out in ``text`` ("j-o-r-d-y-n",
    "J. O. R. D. Y. N.", "jay oh ar dee why en"), lower case and run
    together ("jordyn"), or None when ``text`` is not a spelling.

    Every word must be one letter or a letter's name; at least
    :data:`MIN_SPELLED_LETTERS` of them. Two or fewer letter-NAME words with
    no lone letter ("see you", "are you", "oh you") are ordinary words, not
    a spelling."""
    if not text:
        return None
    t = text.lower()
    t = re.sub(r"double[\s-]+(?:you|u)\b", "doubleyou", t)
    words = [w for w in re.split(r"[^a-z]+", t) if w]
    if len(words) < MIN_SPELLED_LETTERS:
        return None
    out: list[str] = []
    named = 0
    for w in words:
        if len(w) == 1:
            out.append(w)
        elif w in _LETTER_WORDS:
            out.append(_LETTER_WORDS[w])
            named += 1
        else:
            return None
    if named == len(out) and named < 3:
        return None
    return "".join(out)


def spell_out(name: str) -> str:
    """``name`` read back letter by letter for text-to-speech: "Jordan" →
    "J, O, R, D, A, N"; words apart with a full stop ("M, A, R, Y. J, O")."""
    words = []
    for word in name.split():
        parts = []
        for ch in word:
            if ch.isalpha():
                parts.append(ch.upper())
            elif ch == "-":
                parts.append("hyphen")
            elif ch in "'’":
                parts.append("apostrophe")
        if parts:
            words.append(", ".join(parts))
    return ". ".join(words)


def name_from_letters(letters: str) -> str:
    """Spelled letters as a name: "jordyn" → "Jordyn"."""
    return letters[:1].upper() + letters[1:]


# ─── Replies to "Did I get that right?" ───────────────────────────────────

YES = "yes"
NO = "no"
NAME = "name"          # a name: corrected, spelled, or given after a "no"
UNCLEAR = "unclear"    # a correction ("yes, but…", "it's spelled…") not read
CANCEL = "cancel"      # "never mind", "forget it", "don't save it"


@dataclass(frozen=True)
class NameReply:
    """What a reply to the name question said. ``name`` (title case) is set
    for :data:`NAME`; ``spelled`` when the person spelled it letter by
    letter (then it is exactly what they said, so it is not checked against
    sound-alikes); ``polarity`` is the yes/no the reply began with, if any;
    ``bare`` when the name was a lone word with nothing saying it is a name
    ("no, Jordyn"), which the caller holds against its commands ("no,
    pause" is not a name)."""

    kind: str
    name: str | None = None
    spelled: bool = False
    polarity: bool | None = None
    bare: bool = False


_FILLER_RE = re.compile(r"^(?:(?:um+|uh+|er+|erm|hmm+|well|so|oh|okay|ok)[,\s]+)+")
_YES_LEAD_RE = re.compile(
    r"^(?:yes|yeah|yep|yup|yes yes|sure|correct|right|that's right|that is right|"
    r"that's correct|exactly|affirmative|close|almost|nearly|sort of|kind of)\b[,.!\s]*"
)
_NO_LEAD_RE = re.compile(
    r"^(?:no no|no|nope|nah|wrong|incorrect|that's wrong|that's not right|not quite|"
    r"not exactly|not really)\b[,.!\s]*"
)
# Courtesy after a yes/no that adds nothing: "yes please", "no thanks",
# "yes that's me", "yes you did".
_COURTESY_RE = re.compile(
    r"^(?:please|thanks|thank you|that's me|it is|it's right|that's it|you did|"
    r"you got it|you got it right|perfect|great|good|spot on|that's correct|"
    r"that's right|correct|right|i am|you are right)$"
)
_CANCEL_RE = re.compile(
    r"^(?:never ?mind|forget it|forget about it|cancel(?: that)?|"
    r"don't save (?:it|that|me|my name)|don't bother|skip it|no thanks|no thank you)$"
)
_BUT_RE = re.compile(r"^(?:but|although|though|except|only)\b[,\s]*")
_CORRECTION_HINT_RE = re.compile(r"\b(?:but|spell(?:ed|t|ing)?|letters?|not|wrong|actually)\b")
_SPELLED_RE = re.compile(
    r"(?:^|\b)(?:spelled|spelt|spelling(?: is| it)?|spell it|you spell it)\s+"
    r"(?:it\s+)?(?:like\s+|as\s+)?(?P<rest>.+)$"
)
_NAME_LEAD_RE = re.compile(
    r"^(?:(?:it's|it is|its|that's|that is)\s+(?:actually\s+)?"
    r"|my name(?:'s| is)\s+(?:actually\s+)?|i'm\s+(?:actually\s+)?|i am\s+(?:actually\s+)?"
    r"|call me\s+|actually\s+|i said\s+|i'm called\s+|the name(?:'s| is)\s+|name's\s+"
    r"|you should call me\s+|it should be\s+|should be\s+)+"
)
_NAME_WORDS_RE = re.compile(r"^[a-z][a-z'\-]*(?: [a-z][a-z'\-]*){0,%d}$" % (MAX_NAME_WORDS - 1))

#: Words that are never somebody's name when a reply is read: what people
#: say instead of a name, and adjectives "I'm …" catches (``_NAME_BLOCKLIST``
#: in handlers/voice_profile.py is merged in by the caller).
NOT_A_NAME = frozenset({
    "what", "huh", "pardon", "sorry", "thanks", "thank", "hello", "hi", "hey",
    "yes", "no", "yeah", "nope", "okay", "ok", "right", "wrong", "correct",
    "it", "that", "this", "me", "you", "him", "her", "them", "fine", "good",
    "great", "sure", "maybe", "close", "almost", "nothing", "never", "please",
    "stop", "cancel", "wait", "hold", "again", "repeat", "what's", "who",
    "a", "an", "the", "with", "and", "or", "of", "to", "in", "on", "my", "your",
    "play", "pause", "resume", "skip", "next", "previous", "louder", "quieter",
    "volume", "mute", "unmute", "help", "turn", "set", "add", "remove", "open",
})


def _normalize(text: str) -> str:
    t = (text or "").lower().replace("’", "'").strip()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .!?")


def _plain_name(raw: str, not_a_name: Iterable[str]) -> str | None:
    """``raw`` (normalized) as a name, or None: one to
    :data:`MAX_NAME_WORDS` words of letters, none a word that is never a
    name, title-cased."""
    raw = raw.strip(" ,.!?")
    if not raw or not _NAME_WORDS_RE.match(raw):
        return None
    blocked = NOT_A_NAME | frozenset(not_a_name)
    if raw in blocked or any(w in blocked for w in raw.split()):
        return None
    return " ".join(part.capitalize() for part in raw.split())


def _name_in(rest: str, not_a_name: Iterable[str]) -> NameReply | None:
    """A name in what is left of a reply once a yes/no is taken off it:
    spelled ("spelled J O R…", "J-O-R…", "Jordyn, J O R …"), or said ("it's
    Jordyn", "my name is Jordyn", a bare "Jordyn"). None when there is no
    name in it."""
    rest = _BUT_RE.sub("", rest).strip(" ,")
    if not rest:
        return None
    m = _SPELLED_RE.search(rest)
    if m:
        letters = letters_of(m.group("rest"))
        if letters:
            return NameReply(NAME, name_from_letters(letters), spelled=True)
        named = _plain_name(m.group("rest"), not_a_name)
        if named and len(named.split()) == 1:
            # "spelled JORDYN": Whisper ran the letters together.
            return NameReply(NAME, named, spelled=True)
        return None
    letters = letters_of(rest)
    if letters:
        return NameReply(NAME, name_from_letters(letters), spelled=True)
    stripped = _NAME_LEAD_RE.sub("", rest).strip(" ,")
    letters = letters_of(stripped)
    if letters:
        return NameReply(NAME, name_from_letters(letters), spelled=True)
    # "Jordyn, J O R D Y N" / "Jordyn. J-O-R-D-Y-N": the letters win.
    head, sep, tail = stripped.partition(",")
    if not sep:
        head, sep, tail = stripped.partition(". ")
    if sep:
        letters = letters_of(tail)
        if letters:
            return NameReply(NAME, name_from_letters(letters), spelled=True)
    named = _plain_name(stripped, not_a_name)
    if named:
        bare = stripped == rest
        if bare and len(named.split()) > 1:
            # Several words with nothing saying they are a name ("no, play
            # the radio"): not a name.
            return None
        return NameReply(NAME, named, bare=bare)
    return None


def read_name_reply(text: str, *, not_a_name: Iterable[str] = ()) -> NameReply | None:
    """What a reply to "… Did I get that right?" (or "What's your name?")
    says, or None when it is about something else (the turn is then
    routed as usual).

    * :data:`YES` / :data:`NO` — a plain answer ("yes", "yeah that's me",
      "no thanks" is :data:`CANCEL`);
    * :data:`NAME` — a name in the reply: "no, it's Jordyn", "it's spelled
      J O R D Y N", "yes, but it's J-O-R-D-Y-N", "my name is Jordyn", a
      bare "Jordyn" or a bare spelling;
    * :data:`UNCLEAR` — a correction whose name couldn't be read ("yes, but
      you spelled it wrong", "not quite");
    * :data:`CANCEL` — "never mind", "forget it", "don't save it".

    ``not_a_name``: more words never taken as a name (the handler's
    adjective blocklist)."""
    t = _normalize(text)
    if not t:
        return None
    if _CANCEL_RE.match(t):
        return NameReply(CANCEL)
    t = _FILLER_RE.sub("", t).strip()
    polarity: bool | None = None
    m = _YES_LEAD_RE.match(t)
    if m:
        polarity = True
        hedge = m.group(0).strip(" ,.!").split()[0] in ("close", "almost", "nearly", "sort", "kind")
    else:
        hedge = False
        m = _NO_LEAD_RE.match(t)
        if m:
            polarity = False
    rest = t[m.end():].strip(" ,.!") if m else t
    if polarity is not None and (not rest or _COURTESY_RE.match(rest)):
        if hedge:
            return NameReply(UNCLEAR, polarity=True)
        return NameReply(YES if polarity else NO, polarity=polarity)
    if _CANCEL_RE.match(rest):
        return NameReply(CANCEL)
    found = _name_in(rest, not_a_name)
    if found is not None:
        return NameReply(
            NAME, found.name, spelled=found.spelled, polarity=polarity, bare=found.bare,
        )
    if polarity is not None and (hedge or _CORRECTION_HINT_RE.search(rest)):
        return NameReply(UNCLEAR, polarity=polarity)
    if polarity is None and _SPELLED_RE.search(t):
        return NameReply(UNCLEAR)
    if polarity is not None and not _CORRECTION_HINT_RE.search(rest):
        # "yes, set a timer…", "no, play the radio": the yes/no answered
        # the question, and what follows is ignored here (as before).
        return NameReply(YES if polarity else NO, polarity=polarity)
    return None


# ─── Sound-alikes among the household ────────────────────────────────────


def sounds_like_name(heard: str, known: str) -> bool:
    """Whether ``heard`` is plausibly ``known`` misheard or misspelled: not
    the same name, but a spelling within the library resolver's fuzzy bar
    or the same Double Metaphone key (``spoken_names.sounds_like``):
    "Jordyn" / "Jordan", "Kathryn" / "Catherine", "Shawn" / "Sean" no
    (different keys, spelled apart)."""
    from domovoi.handlers.shared.spoken_names import sounds_like

    a, b = " ".join(heard.split()).lower(), " ".join(known.split()).lower()
    if not a or not b or a == b:
        return False
    return sounds_like(a, b)


def sound_alike_people(heard: str, people: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    """The household members (``(id, name)`` rows) whose name sounds like
    ``heard`` without being it (case and spacing aside)."""
    return [(int(pid), name) for pid, name in people if sounds_like_name(heard, name)]
