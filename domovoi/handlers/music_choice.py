"""The "did you mean …?" dialog for spoken music requests.

When the spoken-name resolver (:mod:`domovoi.handlers.shared.library_match`)
finds a request close to a library name but not close enough to play it
outright (its middle band: ``music_match_ask_threshold`` ≤ score <
``music_match_play_threshold``), MusicHandler asks instead of guessing::

    "play glass harber"   → "Did you mean Glass Harbor?"
    "yes"                 → plays it
    "no"                  → "OK."
    "no, play the velvet kites" / a bare "velvet kites" → plays that instead

Shape (modelled on the radio plugin's ``radio.station_choice``): the
question is parked through the mediated confirmation API as
``core.music_choice`` with up to three candidates, and the reply comes back
here. Unlike a yes/no confirmation, the reply may be free text, so the kind
is also one of MusicHandler's ``choice_kinds``: the router hands the WHOLE
reply to :meth:`MusicChoiceMixin.handle_choice_reply` before its yes/no
pre-empt — whose ``\\b`` would otherwise read "no, play X" as a bare "no"
and lose the "play X".

What a reply can be (:func:`parse_choice_reply`, pure; a leading "uh,",
"oh," or "well," is no part of the answer):

* yes ("yes", "uh, yes", "mm-hmm", "of course", "yeah, play it") → the
  first candidate; so does "either one" / "I don't care" / "you pick";
* an ordinal ("the second one", "number two", "the other one", "no, the
  first one") → that candidate;
* no ("no", "oh, no", "nope, never mind", "no, stop") → "OK." — a "no"
  never runs another command off its tail;
* a NAME — "no, play X", "no, I said X", "I meant X", or a bare "X":
  one of the parked candidates when X names it
  (``library_match.match_reply``). After a "no" (or "I said …") X is
  never the candidate just turned down, unless it names it clearly
  better than the first words did: "no, I said subtract" after "did you
  mean SBTRKT?" plays what today's search finds for "subtract" instead.
  Otherwise X is played as a new request ("play X" through the fast
  paths) — but only when the reply ASKED for that ("no, play X", "I meant
  X") or X is a name the library knows (the resolver plays or asks about
  it). A bare reply that names nothing ("good night", "lock the front
  door") never plays anything (see the last two cases below); "no,
  <words that name nothing>" is a "no";
* which one of SEVERAL by how it sounds (:func:`match_option`): "Eminem",
  "by Eminem", "the Sum 41 one", and the speech-to-text slips of them
  ("biomina" for "by Ominem") — the options' names that tell them apart
  (two songs called the same: their artists), folded so that names that
  sound alike compare alike (stdlib ``difflib`` over a phonetic fold);
  Never a name a reply merely holds or begins ("turn off the lights" and
  an option "Lights", "some" and "Sum 41"), and a "play X" only when X,
  whole, plainly is an option;
* a command with a fast path of its own ("stop", "what time is it", "play
  X"), a question, "shut up", and any turn but the open mic right after
  the question (a wake word, also one said over it, the phone's mic, typed
  chat) is not about the choice: the router routes it as an ordinary turn
  and the question is dropped;
* anything else said on that open mic is the music's to answer, never the
  language model's (owner, 2026-11-28: "Biomina." woke a cold Q&A model
  while a group's music waited): the question is asked once more, briefly
  ("Sum 41 or Eminem?"), and after a second miss the house says it didn't
  catch it and the music the question interrupted comes back. A dismissal
  ("never mind") is a "no"; "never mind, X" runs X when a fast path
  takes it.

A suggestion turned down ("no") stays turned down for those words in this
conversation for :data:`DECLINED_TTL_SEC`: asking for the same thing again
goes straight on to today's search (MPD's search, a podcast, a streaming
provider) instead of asking the same question again.

A parked choice expires after :data:`CHOICE_TTL_SEC` (then any reply is
routed normally). Asking never touches the room's MPD queue and carries no
``music_action``: the streaming layer holds an interrupted song's restart
through the follow-up window (``StreamSession.hold_music_start``), so an
unanswered question gets the old music back, a "no" resumes it, and a
"yes" replaces it with the new play.

It never asks where nobody can answer: the dashboard's play box
(``Context.answerable`` False), a chat-mode tool call (no session), or
with the "Ask 'did you mean…?'" setting (``music_choice_enabled``) off —
then the request goes on to today's search.

Lyric search (a song asked for by words from its lyrics) parks the same
question: "play the song that goes …" asks "did you mean X?" when unsure,
and "what's the song that goes …" answers "That's X." / "It might be X."
with an offer to play it (:meth:`MusicChoiceMixin.tell_lyric_match`). The
words themselves are never said back, and never logged: a lyric request's
log lines say how many words it had (``<lyrics: N words>``).
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.config import settings
from domovoi.confirmations import request_confirmation
from domovoi.handlers.shared import library_match
from domovoi.handlers.shared.library_match import Candidate, EntityRef, Resolution
from domovoi.models import Context, Response
from domovoi.log_privacy import said

log = logging.getLogger(__name__)

MUSIC_CHOICE_KIND = "core.music_choice"
#: How long a "did you mean …?" stays answerable, in seconds.
CHOICE_TTL_SEC = 60.0
#: Candidates parked per question; the prompt says at most two of them.
MAX_CHOICES = 3
SPOKEN_CHOICES = 2
#: A bare reply longer than this is a sentence, not a name: routed normally.
MAX_NAME_WORDS = 10
#: After a "no", a name picks a candidate the question offered only when it
#: names it at least this much better than the request's own words did.
CORRECTION_MARGIN = 0.05
#: How long a turned-down suggestion stays turned down (same words, same
#: conversation), in seconds.
DECLINED_TTL_SEC = 300.0

_LOST_TRACK_TEXT = "I've lost track of that one. What would you like to hear?"
_GONE_TEXT = "That one isn't in your library anymore."
#: The second miss: the question is dropped and the interrupted music
#: comes back (the streaming layer's auto-resume after a reply that asks
#: nothing).
DIDNT_CATCH_TEXT = "Sorry, I didn't catch that."
#: What opens a turn that is a new one, not an answer: a wake word (also
#: when it is said over the question: "barge_in"), the phone's mic button.
#: Such a turn never picks an option by sound; it is routed as a turn of
#: its own unless it says yes, no or an ordinal.
_FRESH_TRIGGERS = frozenset({"wake_word", "barge_in", "push_to_talk"})
#: A reply naming one of several options by sound (:func:`match_option`):
#: the best must score at least this, and beat the next by the margin.
#: (0.75 let "moonlight sonata" pick the album Moonlit Static and "city
#: lights" an option "Lights", review 2026-11-28.) A "play X" said in
#: reply is held to OPTION_STRICT_SCORE: it is a request of its own unless
#: X is plainly one of them.
OPTION_MIN_SCORE = 0.82
OPTION_STRICT_SCORE = 0.85
OPTION_MARGIN = 0.1


# ─── Reading the reply (pure) ──────────────────────────────────────────────

# What a reply may open with before its yes or no: "Uh, yes.", "Oh, no.",
# "Well, yeah." A comma, a full stop or a space must follow, so "mm-hmm"
# stays whole.
_INTERJECTION_RE = re.compile(
    r"^(?:(?:oh|ah|aw|mm|hmm+|um+|uh+|er+|erm|well|so|hey)(?:[,.!]+\s*|\s+))+"
)
# Yes-words the router's _YES_RE leaves out that answer "did you mean …?".
_MORE_YES_RE = re.compile(
    r"^(?:mm-?hmm|mhm|uh-?huh|absolutely|definitely|of course|go on|you got it)\b"
)
_MORE_NO_RE = re.compile(r"^(?:uh-?uh|nuh-?uh|no way|not really)\b")
# "Either one" — any of them will do: the first.
_EITHER_RE = re.compile(
    r"^(?:either(?: one)?(?: is fine| will do)?|whichever(?: one)?|i don't care|don't care"
    r"|(?:it )?doesn't matter|you pick|you choose|any of them|anyone|any one|surprise me)"
    r"(?:,? please)?$"
)
# "Yes, play it loud": a pronoun after the yes's "play" is the candidate.
_PRONOUN_RE = re.compile(r"^(?:it|that|this|them|those|the one)\b")

_ORDINAL_WORDS = {
    "first": 0, "1st": 0, "one": 0, "1": 0,
    "second": 1, "2nd": 1, "two": 1, "2": 1,
    "third": 2, "3rd": 2, "three": 2, "3": 2,
    "other": 1,
}
_ORDINAL_RE = re.compile(
    r"^(?:the )?(first|1st|second|2nd|third|3rd|other|last)"
    r"(?: one| song| track| album| artist| band)?(?:,? please)?$"
    r"|^(?:number|option) (one|two|three|1|2|3)(?:,? please)?$"
)
# Said after a yes-word: nothing that changes the answer. Each phrase must
# be readable ONE way: "sure thing" is "sure" then "thing", never also a
# phrase of its own, or a run of them ("sure thing sure thing ... x") has
# 2^n readings to try before it fails (CodeQL py/redos, 2026-11: forty of
# them held the core for days). test_hostile_input_time.py times it.
_ACCEPT_TAIL_RE = re.compile(
    r"^(?:(?:please|thanks|thank you(?: very much| so much)?|cheers|go ahead|please do|do it"
    r"|that's it|that's the one|that one|exactly|correct|right|that's right|i did|i do"
    r"|yes|yeah|yep|yup|sure|thing|of course|definitely|absolutely|ok|okay"
    r"|sir|ma'am|mate|buddy"
    r"|(?:play|put on) (?:it|that|that one|this|this one|them|those))"
    r"(?:[,.!]?\s+|$))+$"
)
# A whole reply that accepts without a yes-word.
_ACCEPT_RE = re.compile(
    r"^(?:ok|okay|alright|all right|sounds good|go ahead|go for it|do it|please|please do"
    r"|that one|that's the one|that's it|perfect|mm-?hmm|mhm|uh-?huh|absolutely|definitely"
    r"|of course|go on|you got it"
    r"|(?:play|put on) (?:it|that|that one|this one))"
    r"(?:,? (?:please|thanks|thank you))?$"
)
# A "no" and whatever follows it. router._NO_RE's words, plus the ones that
# only make sense after "did you mean …?".
_NO_LEAD_RE = re.compile(
    r"^(?:no(?:,? no)*|nope|nah|wrong|incorrect|that's wrong|not quite|not exactly"
    r"|not that(?: one)?|neither(?: of (?:them|those))?|none of (?:them|those))\b[,.!]?\s*(.*)$"
)
# After a "no": nothing more to do.
_DECLINE_TAIL_RE = re.compile(
    r"^(?:(?:thanks|thank you|never ?mind|forget (?:it|about it|that)|that's all|that's it"
    r"|i'm good|i'm fine|not now|nothing|no|nope|don't bother|cancel(?: that| it)?|leave it"
    r"|it's fine|that's fine|all good|my mistake|sorry)(?:[,.!]?\s+|$))+$"
)
# What leads a corrected request: "no, PLAY x", "no, I SAID x".
_REQUEST_LEAD_RE = re.compile(
    r"^(?:(?:i said|i meant|i mean|i asked for|i was asking for|i wanted|i want|i'd like"
    r"|i would like|give me|play me|play|put on|listen to|to hear|to listen to)\s+)+"
)
# The leads that correct the question rather than ask for something new.
_CORRECTION_LEAD_RE = re.compile(r"\bi (?:said|meant|mean|asked for|was asking for)\b")
# The leads that ASK to play the name (not "give me …", "I want …").
_EXPLICIT_LEAD_RE = re.compile(
    r"\b(?:i said|i meant|i mean|i asked for|i was asking for|play|put on|listen to|to hear)\b"
)
_QUESTION_RE = re.compile(
    r"^(?:what|what's|who|who's|why|how|when|where|which|whose|is|are|can|could|would"
    r"|do|does|did|will|should)\b"
)
# A bare reply that is no name: thanks, a hesitation, a word Whisper makes
# up out of a quiet room ("you", "thank you", "bye"). Routed normally.
_NOT_A_NAME_RE = re.compile(
    r"^(?:thanks|thank you(?: very much| so much)?|thanks for watching|thank you for watching"
    r"|cheers|you|bye|bye bye|goodbye|so|oh|ah|uh+|um+|hm+|huh|sorry|pardon|excuse me"
    r"|hello|hi|hey|wait|hold on|one sec(?:ond)?|i don't know|i'm not sure|not sure|maybe"
    r"|good|great|cool|nice|awesome|the end|good ?night|night night|whatever|shut up|be quiet"
    r"|never ?mind|forget it)$"
)


@dataclass(frozen=True)
class ChoiceReply:
    """What a reply to "did you mean …?" says: ``pick`` candidate
    ``index``; ``decline``; ``name`` (a parked candidate if ``name``
    names one, else a new request — see below); ``pass`` (not about the
    choice: route it normally).

    For a ``name``: ``negated`` — said after a "no" or as "I said / I
    meant …", so never the candidate just turned down unless it names it
    clearly better (:data:`CORRECTION_MARGIN`); ``explicit`` — led by
    "play" / "I said" / "I meant" …, a request, played through today's
    search even when the library knows no such name; ``otherwise`` — what
    a name means that is neither a candidate, nor a request, nor a name the
    library knows: ``pick`` the first candidate (it followed a yes),
    ``decline`` (it followed a no), or ``pass`` (a bare reply)."""

    action: Literal["pick", "decline", "name", "pass"]
    index: int = 0
    name: str = ""
    negated: bool = False
    explicit: bool = False
    otherwise: Literal["pick", "decline", "pass"] = "pass"


_PASS = ChoiceReply("pass")
_DECLINE = ChoiceReply("decline")
_PICK0 = ChoiceReply("pick", index=0)


def _ordinal(text: str, n: int) -> ChoiceReply | None:
    """The candidate an ordinal reply picks (``n`` parked), a decline for
    one past the end ("the other one" with one candidate), else None."""
    m = _ORDINAL_RE.match(text)
    if m is None:
        return None
    word = m.group(1) or m.group(2)
    if word == "last":
        index = min(n, SPOKEN_CHOICES) - 1
    else:
        index = _ORDINAL_WORDS[word]
    if not 0 <= index < n:
        return _DECLINE
    return ChoiceReply("pick", index=index)


def _strip_punct(text: str) -> str:
    return text.strip().lstrip(",.!;: ").strip()


_POLITE_TAIL_RE = re.compile(r"(?:[,\s]+(?:please|thanks|thank you))+$")


def _as_name(text: str, **kw: Any) -> ChoiceReply:
    text = _POLITE_TAIL_RE.sub("", _strip_punct(text).rstrip(",.!?"))
    if not text or len(text.split()) > MAX_NAME_WORDS:
        return _PASS
    return ChoiceReply("name", name=text, **kw)


def _request(text: str, n: int, **kw: Any) -> ChoiceReply | None:
    """ "play X" / "I said X" / "I meant the second one": the corrected
    request — a name, ``explicit`` when the lead asks to play it ("play",
    "I said", "I meant", "to hear"; not "give me", "I want", which ask
    for nothing in particular), ``negated`` when it corrects the question
    ("I said X") — or None when ``text`` doesn't lead with one."""
    m = _REQUEST_LEAD_RE.match(text)
    if m is None:
        return None
    rest = text[m.end():]
    picked = _ordinal(rest, n)
    if picked is not None:
        return picked
    lead = m.group(0)
    if _CORRECTION_LEAD_RE.search(lead):
        kw["negated"] = True
    return _as_name(rest, explicit=bool(_EXPLICIT_LEAD_RE.search(lead)), **kw)


def _no_rest(core: str) -> str | None:
    """What follows a "no" that opens ``core``, or None when it opens with
    no "no"."""
    m = _NO_LEAD_RE.match(core)
    if m is not None:
        return m.group(1)
    m = _MORE_NO_RE.match(core)
    return core[m.end():] if m is not None else None


def parse_choice_reply(transcript: str, n: int) -> ChoiceReply:
    """Classify a reply to a parked "did you mean …?" with ``n`` candidates.

    ``transcript`` is normalized (lower-case, trailing punctuation gone) but
    not filler-stripped. Pure apart from the router's own matchers (the
    yes-words, the filler strip, the fast-path dry run), which it reads.
    Whether a name is one the library knows is the handler's question
    (:meth:`MusicChoiceMixin.handle_choice_reply`), not this one's."""
    from domovoi.handlers.shared.tool_gate import answers_without_tools
    from domovoi.router import _YES_RE, first_fast_path, strip_leading_filler

    t = transcript.strip()
    if not t or n <= 0:
        return _PASS
    # "Uh, yes." / "Oh, no.": the interjection is no part of the answer.
    core = _INTERJECTION_RE.sub("", t).strip()

    # 1. yes, and whatever follows it.
    m = _YES_RE.match(core) or _MORE_YES_RE.match(core)
    if m is not None:
        rest = _strip_punct(core[m.end():])
        if not rest or _ACCEPT_TAIL_RE.match(rest):
            return _PICK0
        rest = strip_leading_filler(rest)
        hit = _ordinal(rest, n)
        if hit is not None:
            return hit
        hit = _request(rest, n, otherwise="pick")
        if hit is not None:
            if hit.action != "name" or _PRONOUN_RE.match(hit.name):
                return hit if hit.action == "pick" else _PICK0  # "yes, play it loud"
            return hit
        # The yes answered it; a command or a question after it ("yes, and
        # turn it up") is not run off the back of it.
        if (
            rest.startswith(("and ", "then ", "also "))
            or first_fast_path(rest) is not None
            or _QUESTION_RE.match(rest)
            or answers_without_tools(rest)
        ):
            return _PICK0
        named = _as_name(rest, otherwise="pick")
        return named if named.action == "name" else _PICK0

    # 2. no, and whatever follows it.
    rest_of_no = _no_rest(core)
    if rest_of_no is not None:
        rest = _strip_punct(rest_of_no)
        if not rest or _DECLINE_TAIL_RE.match(rest):
            return _DECLINE
        rest = strip_leading_filler(rest)
        hit = _ordinal(rest, n)
        if hit is not None:
            return hit
        hit = _request(rest, n, negated=True, otherwise="decline")
        if hit is not None:
            return hit if hit.action != "pass" else _DECLINE
        # Never run another command off a "no" ("no, stop" is not "stop").
        if first_fast_path(rest) is not None or _QUESTION_RE.match(rest):
            return _DECLINE
        named = _as_name(rest, negated=True, otherwise="decline")
        return named if named.action == "name" else _DECLINE

    # 3. neither: an ordinal, an acceptance, a name — or a new turn.
    t = strip_leading_filler(t)
    if not t:
        return _PASS
    hit = _ordinal(t, n)
    if hit is not None:
        return hit
    if _ACCEPT_RE.match(t) or _EITHER_RE.match(t):
        return _PICK0
    if t.startswith(("play ", "put on ")):
        # "play the second one" picks; "play X" is a new request, which the
        # router plays as one.
        picked = _ordinal(_REQUEST_LEAD_RE.sub("", t, count=1), n)
        return picked if picked is not None and picked.action == "pick" else _PASS
    hit = _request(t, n)  # "I meant X" / "I said X"
    if hit is not None:
        return hit
    if (
        _NOT_A_NAME_RE.match(t)
        or first_fast_path(t) is not None
        or _QUESTION_RE.match(t)
        or answers_without_tools(t)
    ):
        return _PASS
    return _as_name(t)


# ─── Which of several, by how it sounds (pure) ────────────────────────────

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
# Spellings that sound the same, folded the same way (ORDER matters: "ck"
# before "c"). Then every run of vowels is one vowel and doubled letters
# are one: "Ominem", "Eminem" and "emminem" fold alike.
_FOLDS = (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("q", "k"), ("x", "ks"),
          ("c", "k"), ("z", "s"), ("y", "i"))
_VOWELS_RE = re.compile(r"[aeiou]+")
_DOUBLE_RE = re.compile(r"(.)\1+")
# Said around a name that picks an option: "the Sum 41 one", "the one by
# Eminem", "I want the Eminem version please". Each lead or tail word ends
# at a space, so a run of them reads one way.
_OPTION_LEAD_RE = re.compile(
    r"^(?:(?:i want|i meant|i mean|i said|play|put on|give me|it's|its|it was|that's"
    r"|the|that|this|one|version|song|track|by|from)\s+)+"
)
_OPTION_TAIL_RE = re.compile(
    r"(?:\s+(?:one|version|song|track|please|thanks|thank you|instead))+\Z"
)


def _number_words(digits: str) -> str:
    """ "41" as it is said ("forty one"); longer numbers digit by digit."""
    n = int(digits)
    if len(digits) > 2:
        return " ".join(_ONES[int(d)] for d in digits)
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + ("" if ones == 0 else " " + _ONES[ones])


def sound_key(text: str | None) -> str:
    """``text`` folded so that names that sound alike compare alike: digits
    as words, letters only, a silent final "e" dropped, spellings of one
    sound made one, vowel runs and doubled letters made one. The words are
    joined, so "by Ominem" and Whisper's "biomina" land close together."""
    t = (text or "").lower().replace("&", " and ")
    t = re.sub(r"\d+", lambda m: f" {_number_words(m.group())} ", t)
    out = []
    for word in re.findall(r"[a-z]+", t):
        if len(word) > 3 and word.endswith("e"):
            word = word[:-1]
        for a, b in _FOLDS:
            word = word.replace(a, b)
        out.append(_VOWELS_RE.sub("a", word))
    return _DOUBLE_RE.sub(r"\1", "".join(out))


def _likeness(said: str, name: str) -> float:
    """How well ``said`` (a sound key) names ``name`` (a sound key), 0-1:
    the whole of the one against the whole of the other, or against the
    name with a "by" in front (Whisper writes "by Ominem" as one word,
    "biomina"). Never part of either: a reply that merely holds a name
    ("turn off the lights" and an option "Lights") or begins one ("some"
    and "Sum 41") names nothing (review 2026-11-28)."""
    if not said or not name:
        return 0.0
    ratio = difflib.SequenceMatcher(None, said, name, autojunk=False).ratio()
    glued = _DOUBLE_RE.sub(r"\1", "ba" + name)   # sound_key("by") is "ba"
    by = difflib.SequenceMatcher(None, said, glued, autojunk=False).ratio()
    return max(ratio, by)


def _plain_words(text: str | None) -> str:
    """Lower-case words and digits, single-spaced: how a name is found
    inside a reply word for word."""
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def _shared_phrases(options: list[Mapping[str, Any]]) -> list[str]:
    """The names every option has (the title of two songs called "In Too
    Deep"): said in a reply ("in too deep by Eminem"), they pick none."""
    sets = [{_plain_words(n) for n in (o.get("label"), o.get("name")) if n} for o in options]
    shared = set.intersection(*sets) if sets else set()
    return sorted((p for p in shared if p), key=len, reverse=True)


def _option_forms(reply: str, options: list[Mapping[str, Any]], *, strict: bool) -> set[str]:
    """The sound keys of ``reply`` to compare with the options' names: the
    reply, the reply without the words said around a name ("the … one",
    "the one by …"), and that without a name every option shares and a
    "by" left in front. ``strict`` (a "play X" said in reply): the trimmed
    words alone."""
    t = _POLITE_TAIL_RE.sub("", _strip_punct(reply or "").rstrip(",.!?").lower())
    trimmed = _OPTION_TAIL_RE.sub("", _OPTION_LEAD_RE.sub("", t)).strip()
    if strict:
        forms = {trimmed}
    else:
        bare = f" {_plain_words(trimmed)} "
        for phrase in _shared_phrases(options):
            bare = bare.replace(f" {phrase} ", " ")
        bare = re.sub(r"^\s*(?:by|from)\s+", "", bare).strip()
        forms = {t, trimmed, bare}
    keys = {sound_key(f) for f in forms if f and f.strip()}
    keys.discard("")
    return keys


def _distinct_names(options: list[Mapping[str, Any]]) -> list[list[str]]:
    """For each option, the names that tell it apart from the others: its
    own name (title, album or artist, as spelled and as said) unless
    another option has that name too — two songs called "In Too Deep" —
    and its artist's unless another option is by the same artist."""
    def keys(names: Any) -> set[str]:
        return {sound_key(n) for n in names if n}

    primary = [keys((o.get("label"), o.get("name"))) for o in options]
    secondary = [keys((o.get("artist"), o.get("artist_speak"))) for o in options]
    out: list[list[str]] = []
    for i in range(len(options)):
        other_primary = set().union(*(p for j, p in enumerate(primary) if j != i))
        other_secondary = set().union(*(s for j, s in enumerate(secondary) if j != i))
        mine = [k for k in primary[i] if k and k not in other_primary]
        mine += [k for k in secondary[i]
                 if k and k not in other_primary and k not in other_secondary and k not in mine]
        out.append(mine)
    return out


def match_option(
    reply: str, options: list[Mapping[str, Any]], *, strict: bool = False,
) -> int | None:
    """Which of ``options`` (the two or more the question SAID; each
    ``label`` / ``name`` / ``artist`` / ``artist_speak``) ``reply`` names
    by how it sounds, or None. The best must score
    :data:`OPTION_MIN_SCORE` and beat the next best by
    :data:`OPTION_MARGIN`: a reply that sounds like neither, or like both,
    names none. ``strict``: ``reply`` is the X of a "play X" said in reply,
    compared whole (see :func:`_option_forms`)."""
    if len(options) < 2:
        return None
    forms = _option_forms(reply, options, strict=strict)
    if not forms:
        return None
    scores = []
    for names in _distinct_names(options):
        scores.append(max((_likeness(f, n) for f in forms for n in names), default=0.0))
    ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
    best, runner = ranked[0], ranked[1]
    floor = OPTION_STRICT_SCORE if strict else OPTION_MIN_SCORE
    if scores[best] < floor or scores[best] - scores[runner] < OPTION_MARGIN:
        return None
    return best


def short_names(options: list[Mapping[str, Any]]) -> list[str]:
    """How a repeated question names its options, briefly: their names
    when those differ ("Glass Harbor or the album Moonlit Static?"), their
    artists when only those do ("Sum 41 or Eminem?"), else "the first one",
    "the second one"."""
    def said(o: Mapping[str, Any]) -> str:
        name = str(o.get("name") or o.get("label") or "")
        return f"the album {name}" if o.get("type") == "album" and name else name

    names = [said(o) for o in options]
    if all(names) and len({sound_key(n) for n in names}) == len(names):
        return names
    artists = [str(o.get("artist_speak") or o.get("artist") or "") for o in options]
    if all(artists) and len({sound_key(a) for a in artists}) == len(artists):
        return artists
    return ["the first one", "the second one", "the third one"][: len(options)]


# A reply that dismisses the question: a "no" — never a turn for the
# language model. Whatever follows it ("never mind, play the radio") is a
# command of its own when a fast path takes it. ("Shut up" and "be quiet"
# are not here: they ask the room to be quiet, so they are routed as the
# commands they are, never answered with the music back.)
_DISMISS_RE = re.compile(
    r"^(?:never ?mind|forget (?:it|about it|that)|cancel(?: that| it)?"
    r"|stop asking|leave it|nothing|no matter)\b[,.!]?\s*"
)
_QUIET_RE = re.compile(r"^(?:shut up|be quiet|quiet|hush)\b")
#: The trigger of the open mic right after the question: the one turn a
#: reply that names nothing is asked about again on.
_ANSWER_TRIGGER = "followup"


# ─── The parked payload ────────────────────────────────────────────────────


def _play_room(ctx: Context) -> dict[str, str]:
    """``{"play_room": <room>}`` for a question asked about a play in
    ANOTHER room ("play glass harber in the garage"): the answer plays
    there, not where it is said (music_rooms.py). Empty otherwise."""
    if getattr(ctx, "asked_from_room", None) and ctx.room_id:
        return {"play_room": ctx.room_id}
    return {}



def _expired(data: Mapping[str, Any]) -> bool:
    try:
        return time.time() > float(data.get("expires_at"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return True


def _parked_refs(data: Mapping[str, Any]) -> list[EntityRef]:
    refs: list[EntityRef] = []
    for cand in data.get("candidates") or ():
        ref = EntityRef.from_dict(cand.get("ref")) if isinstance(cand, Mapping) else None
        if ref is not None:
            refs.append(ref)
    return refs


def _parked_options(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The parked candidates as :func:`match_option` / :func:`short_names`
    read them, in the order of :func:`_parked_refs`: each one's name as
    spelled and as said, its artist's as spelled and as said (the question
    parks how it said each artist, ``artist_speak``; a question parked
    before that has the spelling alone)."""
    from domovoi.handlers.shared.spoken_names import speakable

    spoken = data.get("artist_speak")
    spoken = list(spoken) if isinstance(spoken, list) else []
    out: list[dict[str, Any]] = []
    for i, cand in enumerate(data.get("candidates") or ()):
        ref = EntityRef.from_dict(cand.get("ref")) if isinstance(cand, Mapping) else None
        if ref is None:
            continue
        artist = ref.artist_label if ref.type not in ("artist", "credit") else None
        said_artist = spoken[i] if i < len(spoken) and isinstance(spoken[i], str) else ""
        out.append({
            "type": ref.type,
            "label": ref.label,
            "name": str(cand.get("speak") or "") or speakable(ref.label),
            "artist": artist,
            "artist_speak": (said_artist or (speakable(artist) if artist else "")),
        })
    return out


# ─── Suggestions turned down (process-local, like CHOICE_PARKED) ──────────

# str(session_id) → [(the request's key, the refs turned down, until)].
_DECLINED: dict[str, list[tuple[str, frozenset[tuple[str, str]], float]]] = {}
_DECLINED_PRUNE_AT = 64


def _words_key(text: str | None) -> str:
    from domovoi.handlers.shared.spoken_names import alias_key

    return alias_key(text or "")


def remember_declined(session_id: Any, heard: str | None, refs: Any) -> None:
    """The person said "no" to ``refs`` offered for ``heard``: for
    :data:`DECLINED_TTL_SEC` the same words in this conversation are not
    asked about again (today's search answers them instead)."""
    key = _words_key(heard)
    if session_id is None or not key:
        return
    now = time.monotonic()
    if len(_DECLINED) >= _DECLINED_PRUNE_AT:
        for sid in [k for k, v in _DECLINED.items() if all(until <= now for *_, until in v)]:
            _DECLINED.pop(sid, None)
    taken = frozenset((r.type, r.key) for r in refs if isinstance(r, EntityRef))
    entries = [e for e in _DECLINED.get(str(session_id), []) if e[2] > now and e[0] != key]
    entries.append((key, taken, now + DECLINED_TTL_SEC))
    _DECLINED[str(session_id)] = entries


def was_declined(session_id: Any, heard: str | None, candidates: Any) -> bool:
    """Whether every one of ``candidates`` was turned down for these same
    words in this conversation, within :data:`DECLINED_TTL_SEC`."""
    if session_id is None:
        return False
    entries = _DECLINED.get(str(session_id))
    key = _words_key(heard)
    if not entries or not key:
        return False
    now = time.monotonic()
    refs = {(c.ref.type, c.ref.key) for c in candidates}
    return any(k == key and until > now and refs <= taken for k, taken, until in entries)


def forget_declined() -> None:
    """Tests: drop every remembered "no"."""
    _DECLINED.clear()


def _is_lyric_request(res: Resolution, query: Mapping[str, Any]) -> bool:
    """A question about words from a song's lyrics (lyric search), not a
    name: asked with ``{"lyrics": …}``, or answered by lyric matches."""
    return "lyrics" in query or any(c.via == "lyrics" for c in res.candidates)


def _heard_for_log(res: Resolution, query: Mapping[str, Any]) -> str:
    """How a log line names what was asked about: the words as heard for a
    name, but for lyric words only how many there were — Domovoi never
    logs a lyric (lyrics-build contract [Q51])."""
    if _is_lyric_request(res, query):
        return f"<lyrics: {len(str(res.heard or '').split())} words>"
    return repr(said(res.heard))


class MusicChoiceMixin:
    """MusicHandler's "did you mean …?" (``class MusicHandler(MusicChoiceMixin,
    Handler)``). Relies on MusicHandler for ``name``, ``_play`` and
    ``play_candidate``."""

    confirmation_kinds: tuple[str, ...] = (MUSIC_CHOICE_KIND,)
    choice_kinds: tuple[str, ...] = (MUSIC_CHOICE_KIND,)

    # ── asking ──
    async def ask_did_you_mean(
        self,
        res: Resolution,
        query: Mapping[str, str],
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        """Ask which of ``res.candidates`` was meant, parking them for the
        reply — or None (the caller goes on to today's search) when nobody
        can answer here or asking is switched off."""
        if not settings.music_choice_enabled or ctx.session_id is None or not ctx.answerable:
            return None
        candidates = list(res.candidates[:MAX_CHOICES])
        if not candidates:
            return None
        if was_declined(ctx.session_id, res.heard, candidates):
            # Asked a moment ago and told "no": don't ask the same thing
            # again — today's search (and a streaming provider) gets it.
            log.info("music: not asking about %s again (turned down)", _heard_for_log(res, query))
            return None
        phrases = [await self._choice_phrase(c, session) for c in candidates[:SPOKEN_CHOICES]]
        if len(phrases) == 1:
            prompt = f"Did you mean {phrases[0]}?"
        else:
            prompt = f"Did you mean {phrases[0]}, or {phrases[1]}?"
        payload = {
            "candidates": [c.to_dict() for c in candidates],
            "artist_speak": await self._artist_speaks(candidates, session),
            "query": {k: v for k, v in query.items() if isinstance(v, str)},
            "heard": res.heard,
            "room_id": ctx.room_id,
            "expires_at": time.time() + CHOICE_TTL_SEC,
            **_play_room(ctx),
        }
        try:
            await request_confirmation(
                session, ctx.session_id, kind=MUSIC_CHOICE_KIND,
                handler=self.name, data=payload, prompt=prompt,  # type: ignore[attr-defined]
            )
        except Exception as e:  # noqa: BLE001 — a question nobody can answer is worse than a search
            log.warning("music: couldn't park the did-you-mean question: %s", e)
            return None
        log.info(
            "music: asked %r for %s (%s)", prompt, _heard_for_log(res, query),
            ", ".join(f"{c.ref.type}:{c.ref.label}@{c.score:.2f}" for c in candidates),
        )
        return Response(
            text=prompt,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
            expect_followup=True,
            data={"choice": [c.to_dict() for c in candidates]},
        )

    async def offer_instead(
        self,
        cand: Candidate,
        prompt: str,
        *,
        heard: str,
        query: Mapping[str, str],
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        """Offer ``cand`` in place of a song the house doesn't have ("I
        don't have X. Want me to shuffle Y instead?"), parked as the SAME
        ``core.music_choice`` question :meth:`ask_did_you_mean` parks, so
        "yes" plays it, "no" ends it ("OK.") and a name plays that. None —
        the caller just says it doesn't have the song — when nobody can
        answer here, asking is switched off, or this offer was turned down
        a moment ago for the same words."""
        if not settings.music_choice_enabled or ctx.session_id is None or not ctx.answerable:
            return None
        if was_declined(ctx.session_id, heard, [cand]):
            return None
        payload = {
            "candidates": [cand.to_dict()],
            "query": {k: v for k, v in query.items() if isinstance(v, str)},
            "heard": heard,
            "room_id": ctx.room_id,
            "expires_at": time.time() + CHOICE_TTL_SEC,
            **_play_room(ctx),
        }
        try:
            await request_confirmation(
                session, ctx.session_id, kind=MUSIC_CHOICE_KIND,
                handler=self.name, data=payload, prompt=prompt,  # type: ignore[attr-defined]
            )
        except Exception as e:  # noqa: BLE001 — the plain answer is still an answer
            log.warning("music: couldn't park the offer of another artist: %s", type(e).__name__)
            return None
        log.info(
            "music: offered %s:%s for %r (not in the library)",
            cand.ref.type, cand.ref.label, said(heard),
        )
        return Response(
            text=prompt,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
            expect_followup=True,
            data={"choice": [cand.to_dict()]},
        )

    async def tell_lyric_match(
        self,
        res: Resolution,
        phrase: str,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """The answer to "what's the song that goes …?" (lyric search,
        ``res`` a play- or ask-level :class:`Resolution`): "That's X." or
        "It might be X." / "It might be X, or Y." — and, where someone can
        answer and the "did you mean" setting is on, an offer to play it,
        parked as the SAME ``core.music_choice`` question
        :meth:`ask_did_you_mean` parks (the play-level song alone, or the
        songs as good as the best; ``query = {"lyrics": phrase}``), so
        "yes", "the second one", "no" or a name are answered by
        :meth:`handle_choice_reply` like any "did you mean …?". The words
        are never said back."""
        if res.decision == "play":
            candidates = list(res.candidates[:1])
        else:
            candidates = list(res.candidates[:MAX_CHOICES])
        if not candidates:
            return self._choice_text(ctx, "I couldn't find a song in your library with those words.")
        phrases = [await self._choice_phrase(c, session) for c in candidates[:SPOKEN_CHOICES]]
        if res.decision == "play":
            sentence = f"That's {phrases[0]}."
        elif len(phrases) == 1:
            sentence = f"It might be {phrases[0]}."
        else:
            sentence = f"It might be {phrases[0]}, or {phrases[1]}."
        if not (settings.music_choice_enabled and ctx.session_id is not None and ctx.answerable):
            return self._choice_text(ctx, sentence)
        prompt = sentence + (" Want me to play it?" if len(candidates) == 1 else " Want me to play one?")
        payload = {
            "candidates": [c.to_dict() for c in candidates],
            "artist_speak": await self._artist_speaks(candidates, session),
            "query": {"lyrics": phrase},
            "heard": phrase,
            "room_id": ctx.room_id,
            "expires_at": time.time() + CHOICE_TTL_SEC,
            **_play_room(ctx),
        }
        try:
            await request_confirmation(
                session, ctx.session_id, kind=MUSIC_CHOICE_KIND,
                handler=self.name, data=payload, prompt=prompt,  # type: ignore[attr-defined]
            )
        except Exception as e:  # noqa: BLE001 — the answer alone is still an answer
            log.warning("music: couldn't park the offer to play a lyric match: %s", type(e).__name__)
            return self._choice_text(ctx, sentence)
        log.info(
            "music: told a lyric match for %s (%s)", _heard_for_log(res, {"lyrics": phrase}),
            ", ".join(f"{c.ref.type}:{c.track_ids[0] if c.track_ids else c.ref.key}@{c.score:.2f}" for c in candidates),
        )
        return Response(
            text=prompt,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
            expect_followup=True,
            data={"choice": [c.to_dict() for c in candidates]},
        )

    async def _choice_phrase(self, cand: Candidate, session: AsyncSession) -> str:
        """How the question names one candidate: an artist by its spoken
        name, a song as "<song> by <artist>", an album as "the album <x>"
        (+ " by <artist>")."""
        from domovoi.handlers.shared.spoken_names import speakable

        ref = cand.ref
        speak = cand.speak or speakable(ref.label)
        if ref.type in ("artist", "credit"):
            return speak
        artist = await self._artist_speak(ref.artist_label, session)
        if ref.type == "album":
            return f"the album {speak}" + (f" by {artist}" if artist else "")
        return f"{speak} by {artist}" if artist else speak

    async def _artist_speaks(self, candidates: list[Candidate], session: AsyncSession) -> list[str]:
        """How the question says each candidate's artist ("" for an artist
        itself): parked with it, so a reply that names the artist as the
        house said it ("by Ominem") is matched (:func:`match_option`)."""
        return [
            "" if c.ref.type in ("artist", "credit") else await self._artist_speak(c.ref.artist_label, session)
            for c in candidates
        ]

    @staticmethod
    async def _artist_speak(label: str | None, session: AsyncSession) -> str:
        from domovoi.handlers.shared.spoken_names import alias_key, speakable

        if not label:
            return ""
        key = alias_key(label)
        if not key:
            return speakable(label)
        try:
            return await library_match.speak_for(
                session, EntityRef(type="artist", key=key, label=label)
            ) or speakable(label)
        except Exception as e:  # noqa: BLE001 — the plain spelling is fine
            log.debug("music: speak_for(%r) failed: %s", label, e)
            return speakable(label)

    # ── the reply ──
    async def handle_choice_reply(
        self,
        kind: str,
        data: dict[str, Any],
        transcript: str,
        ctx: Context,
        session: AsyncSession,
    ) -> Response | None:
        if kind != MUSIC_CHOICE_KIND or _expired(data):
            return None
        refs = _parked_refs(data)
        if not refs:
            return None
        reply = parse_choice_reply(transcript, len(refs))
        log.info("music: did-you-mean reply %r → %s", said(transcript), reply)
        heard = str(data.get("heard") or "")
        if reply.action == "pass":
            return await self._not_an_answer(transcript, data, refs, ctx, session)
        if reply.action == "decline":
            remember_declined(ctx.session_id, heard, refs)
            return self._choice_text(ctx, "OK.")
        if reply.action == "pick":
            return await self._play_parked(refs[reply.index], ctx, session)
        return await self._answer_with_name(reply, refs, heard, ctx, session, data=data, transcript=transcript)

    async def _answer_with_name(
        self,
        reply: ChoiceReply,
        refs: list[EntityRef],
        heard: str,
        ctx: Context,
        session: AsyncSession,
        *,
        data: Mapping[str, Any] | None = None,
        transcript: str = "",
    ) -> Response | None:
        """A reply that says a name (see :class:`ChoiceReply`)."""
        name = reply.name
        hit = await library_match.match_reply(session, name, refs)
        if hit is not None and 0 <= hit[0] < len(refs):
            pos, score = hit
            if not reply.negated or await self._names_it_better(session, name, score, heard, refs[pos]):
                return await self._play_parked(refs[pos], ctx, session)
            # "No, I said subtract" after "did you mean SBTRKT?": the same
            # words again, so never SBTRKT.
            return await self._play_turned_down(name, refs, heard, ctx, session)
        # Which of several, by how it sounds: "Eminem", "by Eminem", "the
        # Sum 41 one", Whisper's "biomina" — before the library is asked
        # about the words, which would shuffle all of Eminem for "by Eminem".
        # Never the request's own words said again after a "no", never on a
        # fresh turn, and a request ("no, play Northern Lights") only when
        # its words are plainly one of them, whole.
        if (
            data is not None
            and getattr(ctx, "trigger", None) not in _FRESH_TRIGGERS
            and not (reply.negated and _words_key(name) == _words_key(heard))
        ):
            pos = self._option_named(name, data, refs, strict=reply.explicit)
            if pos is not None:
                return await self._play_parked(refs[pos], ctx, session)
        if reply.explicit and not reply.negated:
            return await self._play_named(name, ctx, session)
        res = await library_match.resolve_request(session, {"any": name})
        known = res.decision in ("play", "ask") and res.best is not None
        if reply.negated and known and res.best.ref in refs:  # type: ignore[union-attr]
            return await self._play_turned_down(name, refs, heard, ctx, session)
        if known or reply.explicit:
            if reply.negated:
                remember_declined(ctx.session_id, heard, refs)
            return await self._play_named(name, ctx, session)
        # A name nothing in the library goes by ("good night", "turn off
        # the lights"): it never reaches today's search from here.
        if reply.otherwise == "pick":
            return await self._play_parked(refs[0], ctx, session)
        if reply.otherwise == "decline":
            remember_declined(ctx.session_id, heard, refs)
            return self._choice_text(ctx, "OK.")
        if data is None:
            return None
        return await self._not_an_answer(transcript or name, data, refs, ctx, session, named=True)

    @staticmethod
    def _option_named(
        text: str, data: Mapping[str, Any], refs: list[EntityRef], *, strict: bool = False,
    ) -> int | None:
        """Which option the question SAID (the first :data:`SPOKEN_CHOICES`)
        ``text`` names by sound (:func:`match_option`), as an index into
        ``refs``; None for one option, or no clear one."""
        options = _parked_options(data)
        if len(options) != len(refs):
            return None
        return match_option(text, options[:SPOKEN_CHOICES], strict=strict)

    async def _not_an_answer(
        self,
        transcript: str,
        data: Mapping[str, Any],
        refs: list[EntityRef],
        ctx: Context,
        session: AsyncSession,
        *,
        named: bool = False,
    ) -> Response | None:
        """A reply that is none of the parked question's answers (None: it
        is routed as an ordinary turn, and the question is dropped).
        In this order:

        * "play X" / "put on X": one of the options when X, whole, plainly
          is one ("play the Eminem one"); otherwise the request it is;
        * a command a fast path takes, a question, or any turn but the
          open mic right after the question (``trigger`` "followup": a wake
          word, also one said over the question, the phone's mic, typed
          chat) is routed as itself;
        * "shut up", "be quiet": routed, as the quiet they ask for;
        * a dismissal is a "no" ("OK.", the music back); "never mind, X"
          runs X when a fast path takes it;
        * one of the options by sound ("biomina");
        * otherwise the question is asked once more, briefly; a second miss
          says so, and the music the question interrupted comes back (no
          follow-up, no music_action: the streaming layer's auto-resume).

        Nothing here ever reaches the language model."""
        from domovoi.router import first_fast_path, run_fast_path, strip_leading_filler

        t = strip_leading_filler(_strip_punct(transcript or "").rstrip(",.!?"))
        if not t:
            return None
        trigger = getattr(ctx, "trigger", None)
        lead = re.match(r"(?:play|put on)\s+", t)
        if lead is not None:
            if not named and trigger not in _FRESH_TRIGGERS:
                pos = self._option_named(t[lead.end():], data, refs, strict=True)
                if pos is not None:
                    return await self._play_parked(refs[pos], ctx, session)
            return None
        if first_fast_path(t) is not None or _QUESTION_RE.match(t):
            return None
        if trigger != _ANSWER_TRIGGER or _QUIET_RE.match(t):
            return None
        dismissed = _DISMISS_RE.match(t)
        if dismissed is not None:
            rest = t[dismissed.end():].strip()
            hit = first_fast_path(strip_leading_filler(rest)) if rest else None
            if hit is not None:
                handler, fp, m = hit
                response = await run_fast_path(handler, fp, m, ctx, session)
                if response is not None:
                    return response
            remember_declined(ctx.session_id, data.get("heard"), refs)
            return self._choice_text(ctx, "OK.")
        if not named:
            pos = self._option_named(t, data, refs)
            if pos is not None:
                return await self._play_parked(refs[pos], ctx, session)
        if data.get("reasked"):
            log.info("music: did-you-mean not answered after asking again; dropped")
            return self._choice_text(ctx, DIDNT_CATCH_TEXT)
        return await self._ask_again(data, ctx, session)

    async def _ask_again(
        self, data: Mapping[str, Any], ctx: Context, session: AsyncSession,
    ) -> Response:
        """The same question once more, briefly ("Sum 41 or Eminem?"),
        parked again for its own reply; a second miss is not asked again."""
        options = _parked_options(data)[:SPOKEN_CHOICES]
        names = short_names(options) if options else []
        if len(names) >= 2:
            prompt = f"{names[0]} or {names[1]}?"
        elif names:
            prompt = f"Did you mean {names[0]}? Yes or no?"
        else:
            return self._choice_text(ctx, DIDNT_CATCH_TEXT)
        payload = {k: v for k, v in data.items() if k not in ("handler", "kind", "prompt")}
        payload["reasked"] = True
        payload["expires_at"] = time.time() + CHOICE_TTL_SEC
        try:
            await request_confirmation(
                session, ctx.session_id, kind=MUSIC_CHOICE_KIND,  # type: ignore[arg-type]
                handler=self.name, data=payload, prompt=prompt,  # type: ignore[attr-defined]
            )
        except Exception as e:  # noqa: BLE001 — then it is simply not asked again
            log.warning("music: couldn't park the did-you-mean question again: %s", type(e).__name__)
            return self._choice_text(ctx, DIDNT_CATCH_TEXT)
        log.info("music: did-you-mean not answered; asked again: %r", prompt)
        return Response(
            text=prompt,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
            expect_followup=True,
            data={"choice": list(data.get("candidates") or ())},
        )

    @staticmethod
    async def _names_it_better(
        session: AsyncSession, name: str, score: float, heard: str, ref: EntityRef
    ) -> bool:
        """Does a name said after a "no" pick ``ref`` — i.e. name it
        clearly better (:data:`CORRECTION_MARGIN`) than the request's own
        words ``heard`` did? The same words again never do."""
        if not heard:
            return True
        if _words_key(name) == _words_key(heard):
            return False
        before = await library_match.match_reply(session, heard, [ref])
        return score > (before[1] if before else 0.0) + CORRECTION_MARGIN

    async def _play_turned_down(
        self, name: str, refs: list[EntityRef], heard: str, ctx: Context, session: AsyncSession
    ) -> Response:
        """The person turned the offered names down and said their own
        words again: today's search answers them (MPD's search, a podcast,
        a streaming provider), never a candidate they just declined."""
        remember_declined(ctx.session_id, heard, refs)
        return await self._play(  # type: ignore[attr-defined]
            {"any": name}, ctx, session, allow_choice=False, avoid=tuple(refs),
        )

    async def handle_confirmation(
        self,
        kind: str,
        data: dict[str, Any],
        affirmative: bool,
        ctx: Context,
        session: AsyncSession,
    ) -> Response:
        """A plain yes/no to the question, for a caller that has no reply
        text (the router itself hands choices to handle_choice_reply)."""
        if kind != MUSIC_CHOICE_KIND:
            return self._choice_text(ctx, "I'm not sure what you're confirming.")
        if not affirmative:
            remember_declined(ctx.session_id, data.get("heard"), _parked_refs(data))
            return self._choice_text(ctx, "OK.")
        refs = _parked_refs(data)
        if _expired(data) or not refs:
            response = self._choice_text(ctx, _LOST_TRACK_TEXT)
            response.expect_followup = True
            return response
        return await self._play_parked(refs[0], ctx, session)

    async def _play_parked(self, ref: EntityRef, ctx: Context, session: AsyncSession) -> Response:
        cand = await library_match.candidate_from_ref(session, ref)
        if cand is None:
            return self._choice_text(ctx, _GONE_TEXT)
        return await self.play_candidate(cand, ctx, session)  # type: ignore[attr-defined]

    async def _play_named(self, name: str, ctx: Context, session: AsyncSession) -> Response:
        """Play ``name`` as the request "play <name>" would: the same fast
        path — MusicHandler's play (the resolver: plays, asks again, or
        today's search), or a playlist, podcast or station the name is."""
        from domovoi.router import first_fast_path, run_fast_path

        hit = first_fast_path(f"play {name}")
        response = None
        if hit is not None:
            handler, fp, m = hit
            response = await run_fast_path(handler, fp, m, ctx, session)
        if response is None:  # MusicHandler's "play (.+)" takes anything; a declining path
            return await self._play({"any": name}, ctx, session)  # type: ignore[attr-defined]
        return response

    def _choice_text(self, ctx: Context, text: str) -> Response:
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,  # type: ignore[attr-defined]
        )


__all__ = [
    "CHOICE_TTL_SEC",
    "CORRECTION_MARGIN",
    "ChoiceReply",
    "DECLINED_TTL_SEC",
    "MAX_CHOICES",
    "MUSIC_CHOICE_KIND",
    "MusicChoiceMixin",
    "forget_declined",
    "parse_choice_reply",
    "remember_declined",
    "was_declined",
]
