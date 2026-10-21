"""What in a chat message asks for a draft, for a revision of the chat's
draft, or to print it. A small phrase list with no model involved, shared
with the phone (``DraftPhrases.kt``; ``shared_cases.json`` holds both to the
same answers), like the action phrases.

* :func:`draft_request` — "write a cover letter for the warehouse job at
  Acme", "draft a letter to my landlord about the heater", "help me write
  my resume": a new draft of that kind. A question about writing one ("how
  do I write a cover letter?") is not a request.
* :func:`revision_request` — only asked in a chat that has a draft:
  "shorter", "make it more formal", "mention my forklift licence", "it's
  too long". Anything else is an ordinary message (a question about the
  draft is answered as one).
* :func:`print_request` — "print it", "print the letter", "print a copy".

D5's guided flows (resume form, cover letter with the keyword check,
business letter) start drafts from chips and forms through
:func:`domovoi.drafts.prompts.first_draft` with fields; they don't need a
phrase here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_POLITE = re.compile(
    r"^(?:(?:ok|okay|now|and|also|great|thanks|thank you|please|hey domovoi|domovoi|so|then|right)[,.!]* )*"
    r"(?:(?:can|could|would|will) you (?:please )?|i(?:'d| would) like you to |i (?:want|need) you to |please )?"
    r"(?:please )?"
)
_QUESTION = re.compile(r"^(?:how|what|why|when|where|which|who|whom|whose|should|is|are|was|were|do|does|did|"
                       r"can i|could i|tips|any tips|advice)\b")
_DRAFT = re.compile(
    r"^(?:help me (?:with |to )?)?(?:(?:write|draft|compose|create|make|prepare|put together|type up|start|do|"
    r"redo|i need|i want|i(?:'d| would) like) )?(?:me |up )?(?:(?:a|an|my|the|our) )?(?:new )?(?P<what>[a-z' -]{0,40}?)"
    r"\b(?P<obj>resume|cv|cover letter|letter)\b(?P<rest>.*)$"
)
_VERB = re.compile(r"^(?:help me\b|write\b|draft\b|compose\b|create\b|make\b|prepare\b|put together\b|type up\b|"
                   r"start\b|do\b|redo\b|i need\b|i want\b|i(?:'d| would) like\b|(?:a|an|my|the|our|new) )")
_CHANGE_AFTER = re.compile(r"^ ?(?:shorter|longer|more|less|better|clearer|simpler|friendlier|nicer|sound|"
                           r"formal|informal|casual)\b")
_PERSONAL = re.compile(r"\b(?:personal|landlord|neighbou?rs?|school|teacher|friend|family|grandma|grandpa|"
                       r"grandmother|grandfather|mom|mum|dad|aunt|uncle)\b")
_NOT_A_DOCUMENT = re.compile(r"\b(?:email|e-mail|text message|text|song|poem|story|essay|newsletter|post|tweet)\b")
_REVISE = re.compile(
    r"^(?:make (?:it|this|that|the [a-z]+(?: [a-z]+)?) |shorter\b|longer\b|shorten\b|lengthen\b|expand\b|"
    r"tighten\b|simplify\b|condense\b|trim\b|cut\b|remove\b|delete\b|drop\b|add\b|include\b|mention\b|put\b|"
    r"insert\b|change\b|replace\b|swap\b|use\b|reword\b|rephrase\b|rewrite\b|redo\b|fix\b|correct\b|"
    r"tone (?:it )?(?:down|up)\b|more\b|less\b|too\b|sound\b|say\b|move\b|switch\b|start with\b|end with\b|"
    r"open with\b|don't\b|do not\b|no need\b|instead\b|turn\b|keep it\b|try again\b|another version\b|"
    r"(?:it|that|this)(?:'s| is) (?:too|a bit|a little|not)\b|(?:sign|address) it\b)"
)
_PRINT = re.compile(
    r"^print(?: (?:it|this|that|out|it out|this out|that out|a copy|me a copy|"
    r"(?:the|my) (?:draft|letter|resume|cv|cover letter|document)))?$"
)


def normalize(text: str) -> str:
    """Lowercase, accents and curly quotes plain, spaces collapsed, the end
    punctuation and the polite opening ("can you please …") dropped."""
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'").replace("‘", "'").lower()
    s = " ".join(s.split())
    s = s.rstrip(" .!?")
    prev = None
    while prev != s:
        prev = s
        s = _POLITE.sub("", s, count=1).strip()
    return s


@dataclass(frozen=True)
class DraftAsk:
    kind: str
    template: str
    #: What the draft is for, as the person put it ("for the warehouse job
    #: at Acme"), in their own capitals; "" when they didn't say.
    topic: str


def draft_request(text: str) -> DraftAsk | None:
    s = normalize(text)
    if not s or _QUESTION.match(s) or not _VERB.match(s):
        return None
    m = _DRAFT.match(s)
    if not m:
        return None
    what = m.group("what") or ""
    if _NOT_A_DOCUMENT.search(what):
        return None
    obj = m.group("obj")
    kind = "resume" if obj in ("resume", "cv") else ("cover_letter" if obj == "cover letter" else "letter")
    rest = m.group("rest") or ""
    if _CHANGE_AFTER.match(rest):
        # "make the letter shorter": a change to a letter, not a new one.
        return None
    template = {"resume": "resume-plain", "cover_letter": "letter-block"}.get(kind, "letter-block")
    if kind == "letter" and _PERSONAL.search(what + " " + rest):
        template = "letter-personal"
    return DraftAsk(kind=kind, template=template, topic=_topic(text, len(rest)))


def _topic(original: str, rest_len: int) -> str:
    """The tail after the document word, from the original text (its own
    capitals), trimmed to what reads as a title."""
    o = " ".join((original or "").split()).rstrip(" .!?")
    tail = o[len(o) - rest_len:].strip() if rest_len else ""
    tail = re.sub(r"^(?:,|that|which|please)\s*", "", tail, flags=re.I)
    if len(tail) > 60:
        cut = tail[:60].rsplit(" ", 1)[0]
        tail = cut if cut else tail[:60]
    return tail


def revision_request(text: str) -> bool:
    s = normalize(text)
    return bool(s) and bool(_REVISE.match(s))


def print_request(text: str) -> bool:
    return bool(_PRINT.match(normalize(text)))


def title_for(kind_label: str, topic: str) -> str:
    """"Cover letter for the warehouse job at Acme", "Letter to my landlord";
    the kind alone when there's no topic."""
    t = (topic or "").strip()
    return f"{kind_label} {t}".strip()[:120] if t else kind_label
