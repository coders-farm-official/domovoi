"""Reading a household document aloud ("read me the lease"), documents plan D7.

Only what is already in the household's Documents folder
(``settings.documents_dir``), found by its name the way voice printing
finds one (:mod:`domovoi.printing.lookup`: inside the folder only, no
hidden folders, the Files tab's secret-name filter), and only the kinds
with running text: PDF, Word (.docx), OpenDocument (.odt), text and
markdown. Never a phone's own files: a phone's documents are not in that
folder unless someone sends them home.

The text comes from the same reader the chat uses (:mod:`domovoi.doc_extract`,
in its own short-lived worker process), then is made speakable: headings,
bullets, bold and table rules dropped, a table's cells read as a list,
page headers, footers and footnotes left out. It is cut into **parts** at
sentence ends:

* a short document (about 1.5 minutes or less) is read in one go;
* anything longer is said first ("It's 2 pages, about 3 minutes. The whole
  thing, or just the start?");
* "the whole thing" reads a page or so at a time (:data:`PART_WORDS`),
  asking "Shall I go on?" between parts; "just the start" reads the first
  :data:`START_WORDS` words.

A part is one reply, spoken through the ordinary TTS path sentence by
sentence, so the wake word and "stop" or "pause" cut it off like any reply.
No model is involved: reading works with the household's AI features off.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

#: The kinds read aloud (spreadsheets are left out: a sheet read aloud is
#: a list of numbers, nobody's idea of a document).
READABLE_EXTS = frozenset({".pdf", ".docx", ".odt", ".txt", ".text", ".md", ".markdown"})
#: Speaking rate used for the time said up front (Piper's voices run at
#: roughly 150-170 words a minute).
WORDS_PER_MINUTE = 160
#: Up to this many words a document is read without asking first.
SHORT_WORDS = 250
#: "Just the start": the first part, at most this many words.
START_WORDS = 150
#: "The whole thing": a page or so per part, then "Shall I go on?".
PART_WORDS = 400
#: Words per page, for documents that have no pages of their own.
WORDS_PER_PAGE = 400
#: The longest sentence spoken as one (a run-on in a document is cut).
MAX_SENTENCE_WORDS = 60
#: Parts that are never read: repeated on every page, or notes at the end.
_SKIP_LABELS = ("page header", "page footer", "footnotes")


class ReadRefused(Exception):
    """The document can't be read aloud; ``str()`` is the spoken sentence."""


@dataclass(frozen=True)
class Spoken:
    """A document made speakable."""

    rel: str
    title: str
    sentences: tuple[str, ...]
    words: int
    #: The PDF's own page count; None for other kinds.
    pdf_pages: int | None = None
    mtime: float = 0.0

    def pages(self) -> int:
        if self.pdf_pages:
            return self.pdf_pages
        return max(1, math.ceil(self.words / WORDS_PER_PAGE))

    def minutes(self) -> int:
        return max(1, round(self.words / WORDS_PER_MINUTE))

    def is_short(self) -> bool:
        return self.words <= SHORT_WORDS

    def parts(self, size: int) -> list[str]:
        return split_parts(list(self.sentences), size)


def find(query: str, root: Path | None = None):
    """Documents whose name fits ``query`` (one when it's clear, a few when
    it isn't, none when nothing fits)."""
    from domovoi.printing import lookup

    return lookup.find(query, root, exts=READABLE_EXTS)


# ─── Making text speakable ──────────────────────────────────────────────

_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_BULLET = re.compile(r"^\s*(?:[-*+•▪◦‣]|\(?[0-9]{1,3}[.)]|\(?[a-z][.)])\s+")
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_SPACES = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def clean_line(line: str) -> str:
    """One line of a part's markdown as words to say ("" for nothing)."""
    s = line.strip()
    if not s or _TABLE_RULE.match(s):
        return ""
    s = _LINK.sub(r"\1", s)
    s = _HEADING.sub("", s)
    s = _BULLET.sub("", s)
    if s.startswith("|") or s.count("|") >= 2:
        cells = [c.strip() for c in s.strip("|").split("|")]
        s = ", ".join(c for c in cells if c)
    s = _EMPHASIS.sub("", s)
    s = s.replace("­", "")
    return _SPACES.sub(" ", s).strip()


def _with_stop(s: str) -> str:
    """A heading or list item said as a sentence of its own."""
    return s if s[-1:] in ".!?…:;," else s + "."


def _cut_long(sentence: str) -> list[str]:
    words = sentence.split()
    if len(words) <= MAX_SENTENCE_WORDS:
        return [sentence]
    return [" ".join(words[i:i + MAX_SENTENCE_WORDS]) for i in range(0, len(words), MAX_SENTENCE_WORDS)]


def sentences_of(parts: list[dict]) -> list[str]:
    """The document's sentences in reading order, from its extracted parts."""
    out: list[str] = []
    for p in parts:
        if str(p.get("label") or "").lower() in _SKIP_LABELS:
            continue
        text = str(p.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
        for block in re.split(r"\n\s*\n", text):
            lines = [clean_line(ln) for ln in block.split("\n")]
            lines = [ln for ln in lines if ln]
            if not lines:
                continue
            # A block of short lines (an address, a list, a heading) is said
            # line by line; running text is joined and cut at sentence ends.
            if all(len(ln.split()) <= 8 for ln in lines) and len(lines) > 1:
                pieces = [_with_stop(ln) for ln in lines]
            else:
                joined = " ".join(lines)
                pieces = [x.strip() for x in _SENTENCE_END.split(joined) if x.strip()]
                if len(pieces) == 1 and len(lines) == 1 and len(joined.split()) <= 8:
                    pieces = [_with_stop(joined)]
            for piece in pieces:
                out.extend(_cut_long(piece))
    return out


def split_parts(sentences: list[str], size: int) -> list[str]:
    """``sentences`` grouped into parts of about ``size`` words, cut only
    between sentences (a part always holds at least one)."""
    parts: list[str] = []
    cur: list[str] = []
    n = 0
    for s in sentences:
        w = len(s.split())
        if cur and n + w > size:
            parts.append(" ".join(cur))
            cur, n = [], 0
        cur.append(s)
        n += w
    if cur:
        parts.append(" ".join(cur))
    return parts


def length_line(sp: Spoken) -> str:
    """"It's 2 pages, about 3 minutes."""
    pages = sp.pages()
    minutes = sp.minutes()
    return "It's %s, about %s." % (
        "one page" if pages == 1 else "%d pages" % pages,
        "a minute" if minutes == 1 else "%d minutes" % minutes,
    )


# ─── Loading ────────────────────────────────────────────────────────────

async def load(rel: str, root: Path | None = None) -> Spoken:
    """The document at ``rel`` (Documents-relative) made speakable. Raises
    :class:`ReadRefused` with what to say when it can't be read."""
    from domovoi import doc_extract
    from domovoi.doc_extract import runner
    from domovoi.printing import lookup

    path = lookup.resolve(rel, root)
    if path is None:
        raise ReadRefused("That document isn't in Documents any more.")
    kind = doc_extract.kind_for(path.name)
    if kind is None or path.suffix.lower() not in READABLE_EXTS:
        raise ReadRefused("I can read PDF, Word, OpenDocument and text files aloud, not that kind of file.")
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is not None and key in _CACHE:
        # "Shall I go on?" → "yes" reads the same file again: once is enough.
        return _CACHE[key]
    try:
        result = await runner.extract(path, kind)
    except runner.ExtractFailed as e:
        from domovoi.doc_qa import notes as doc_notes

        said = str(e)
        if said == doc_notes.NO_TEXT_SCANNED:
            raise ReadRefused("That document is a scan with no text in it, so I can't read it aloud.") from None
        if said == doc_notes.NO_TEXT:
            raise ReadRefused("That document has no text I can read aloud.") from None
        raise ReadRefused("I couldn't read that document.") from None
    parts = result.get("parts") or []
    sentences = sentences_of(parts)
    if not sentences:
        raise ReadRefused("That document has no text I can read aloud.")
    pdf_pages = None
    if kind == "pdf":
        pdf_pages = max((int(p.get("page") or 0) for p in parts), default=0) or None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    words = sum(len(s.split()) for s in sentences)
    sp = Spoken(rel=rel, title=path.stem, sentences=tuple(sentences), words=words,
                pdf_pages=pdf_pages, mtime=mtime)
    if key is not None:
        while len(_CACHE) >= _CACHE_SIZE:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = sp
    return sp


#: The last few documents read, by (path, mtime, size): a changed file is a
#: new key, so it is read again.
_CACHE: dict[tuple[str, int, int], Spoken] = {}
_CACHE_SIZE = 4
