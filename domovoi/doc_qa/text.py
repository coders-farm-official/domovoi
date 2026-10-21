"""Passages and words: how a document's parts become something to search.

The Kotlin twin is ``android/.../assistant/docs/DocText.kt``; both are
held to ``shared_cases.json``. Keep every rule here simple enough to
write the same way in both languages (character loops, no regex
features one side lacks).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

# About 300 tokens: English runs near 4 characters a token.
PASSAGE_CHARS = 1200
# A "sentence" longer than this (a run-on list, a table cell dump) is cut
# at a space so one passage never grows past about 1.5 times the target.
SENTENCE_MAX_CHARS = 1800

# Words that carry no meaning for the search. Short on purpose: a question
# word like "when" or "how much" still matters to the person, but not to
# which passage holds the answer.
STOP_WORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have
he her him his how i if in into is it its me my no not of on or our she so
than that the their them then there these they this to too us was we were
what when where which who whom why will with would you your about any all
also am do does just may might must shall should some such very
s t d ll re ve m
""".split())


@dataclass
class Passage:
    """One searchable piece of a document."""
    #: Which document of the chat (0 for the first), and which of its parts.
    doc: int
    part: int
    #: The label an answer cites: the part's ("p. 7", "Section: Deposit").
    label: str
    #: The passage's own text (without the part's ``head``).
    text: str
    #: The part's head (a table's header row), shown above the text.
    head: str = ""
    #: Tokens for the index (head included: "Amount" belongs to its column).
    terms: list[str] = field(default_factory=list)

    def shown(self) -> str:
        """The text the model and the person see: head, then the text."""
        return f"{self.head}\n{self.text}" if self.head else self.text


def nfkc(s: str) -> str:
    """NFKC (ligatures, full-width letters and non-breaking spaces become
    their plain forms), less soft hyphens: what extracted text keeps."""
    return unicodedata.normalize("NFKC", s or "").replace("­", "")


def clean(s: str) -> str:
    """Text as a file wrote it, less soft hyphens and NULs: what the
    extractors keep for formats whose characters are already the
    writer's own (Word, ODT, sheets, text). PDF text is :func:`nfkc`'d,
    since its ligatures are a font's, not the writer's."""
    return (s or "").replace("­", "").replace("\x00", "")


def normalize(s: str) -> str:
    """:func:`nfkc`, with curly quotes and dashes made plain: text as it is
    compared (search terms, labels, quotes), never as it is shown."""
    s = nfkc(s)
    return (s.replace("‘", "'").replace("’", "'").replace("“", '"')
            .replace("”", '"').replace("–", "-").replace("—", "-"))


def _stem(w: str) -> str:
    """Simple suffix stripping: plurals, -ing, -ed, -ly, then a final e,
    so "leases", "leasing", "leased" and "lease" meet at "leas"."""
    if len(w) <= 3 or not w.isalpha():
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    if w.endswith("ing") and len(w) > 5:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]
    elif w.endswith("ly") and len(w) > 5:
        w = w[:-2]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz" and not w[-1].isdigit():
        w = w[:-1]
    if w.endswith("e") and len(w) > 3:
        w = w[:-1]
    return w


def words(s: str) -> list[str]:
    """Lowercase words of ``s``: runs of letters or digits; a digit run
    keeps its inner "," "." ":" "/" ("1,200.50", "3/4", "10:30")."""
    s = normalize(s).lower()
    out: list[str] = []
    cur: list[str] = []
    n = len(s)
    for i, ch in enumerate(s):
        if ch.isalnum():
            cur.append(ch)
            continue
        if (ch in ",.:/" and cur and cur[-1].isdigit() and i + 1 < n and s[i + 1].isdigit()):
            cur.append(ch)
            continue
        if cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def tokens(s: str) -> list[str]:
    """The index's terms for ``s``: :func:`words` less stop words, stemmed."""
    return [_stem(w) for w in words(s) if w not in STOP_WORDS]


def sentences(text: str) -> list[str]:
    """``text`` cut into sentences: at a line break, and after ". ", "! " or
    "? " when the next word starts with a capital, a digit, a quote or a
    bracket (so "e.g. this" and "$1.50 each" stay whole). A sentence past
    :data:`SENTENCE_MAX_CHARS` is cut at a space."""
    out: list[str] = []
    for line in (text or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        start = 0
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            if ch in ".!?" and i + 2 < n and line[i + 1] == " ":
                nxt = line[i + 2]
                if nxt.isupper() or nxt.isdigit() or nxt in "\"'([“‘":
                    out.append(line[start:i + 1].strip())
                    start = i + 2
                    i += 2
                    continue
            i += 1
        rest = line[start:].strip()
        if rest:
            out.append(rest)
    capped: list[str] = []
    for s in out:
        while len(s) > SENTENCE_MAX_CHARS:
            cut = s.rfind(" ", 0, SENTENCE_MAX_CHARS)
            if cut <= 0:
                cut = SENTENCE_MAX_CHARS
            capped.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            capped.append(s)
    return capped


def cut_passages(parts: Iterable[dict[str, Any]], doc: int = 0,
                 target: int = PASSAGE_CHARS) -> list[Passage]:
    """Each part's text in passages of about ``target`` characters, on
    sentence boundaries; each passage after the first in a part starts with
    the previous one's last sentence (one sentence of overlap). A passage
    never crosses parts, so its label is its part's."""
    out: list[Passage] = []
    for pi, part in enumerate(parts):
        label = str(part.get("label") or "")
        head = str(part.get("head") or "").strip()
        sents = sentences(str(part.get("text") or ""))
        if not sents:
            continue
        cur: list[str] = []
        size = 0
        fresh = 0          # sentences in ``cur`` not already in the last passage
        for s in sents:
            if cur and fresh and size + len(s) + 1 > target:
                out.append(_passage(doc, pi, label, cur, head))
                cur = [cur[-1]] if len(cur[-1]) < target // 2 else []
                size = sum(len(x) + 1 for x in cur)
                fresh = 0
            cur.append(s)
            size += len(s) + 1
            fresh += 1
        if cur and fresh:
            out.append(_passage(doc, pi, label, cur, head))
    return out


def _passage(doc: int, part: int, label: str, sents: list[str], head: str) -> Passage:
    text = "\n".join(sents) if any(x.startswith("|") for x in sents) else " ".join(sents)
    p = Passage(doc=doc, part=part, label=label, text=text, head=head)
    p.terms = tokens(p.shown())
    return p
