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

import asyncio
import math
import re
import time
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


# ─── Finding a document: the cached title index ─────────────────────────
#
# The read fast path decides at match time whether a turn is a document's
# (handlers/documents.py), so finding must be cheap and never block the
# event loop: the titles of Documents are walked once into memory, then
# looked over again in a worker thread (asyncio.to_thread) when older than
# REFRESH_SEC, and only re-walked when a folder's mtime moved. The first
# look after start (no index yet) walks once where it is.

@dataclass(frozen=True)
class Doc:
    rel: str
    title: str
    words: tuple[str, ...]


#: How often the index is looked over (folder mtimes; a re-walk when one moved).
REFRESH_SEC = 30.0
_INDEX: dict = {"root": None, "docs": (), "dirs": {}, "at": 0.0, "busy": False, "warming": False}


def _root() -> Path:
    from domovoi.printing import lookup

    return lookup.documents_root()


def _walk(root: Path) -> tuple[tuple[Doc, ...], dict[str, float]]:
    import os

    from domovoi.printing import lookup

    docs = tuple(Doc(rel=d.rel, title=d.title, words=tuple(lookup._words(d.title)))
                 for d in lookup.all_documents(root, READABLE_EXTS))
    dirs: dict[str, float] = {}
    try:
        real = root.resolve(strict=True)
    except OSError:
        return docs, dirs
    for dirpath, dirnames, _files in os.walk(real):
        depth = len(Path(dirpath).relative_to(real).parts)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and depth < lookup.MAX_DEPTH]
        try:
            dirs[dirpath] = os.stat(dirpath).st_mtime
        except OSError:
            pass
    return docs, dirs


def _changed(dirs: dict[str, float]) -> bool:
    import os

    for path, mtime in dirs.items():
        try:
            if os.stat(path).st_mtime != mtime:
                return True
        except OSError:
            return True
    return False


def _refresh(root: Path) -> None:
    try:
        if _INDEX["root"] == str(root) and _INDEX["dirs"] and not _changed(_INDEX["dirs"]):
            _INDEX["at"] = time.monotonic()
            return
        docs, dirs = _walk(root)
        _INDEX.update(root=str(root), docs=docs, dirs=dirs, at=time.monotonic())
    finally:
        _INDEX["busy"] = False


def index() -> tuple[Doc, ...]:
    """The readable documents in Documents, from memory."""
    root = _root()
    if _INDEX["root"] != str(root):
        if _INDEX["warming"]:
            return ()  # the start-up walk (warm) is still going: nothing yet
        _INDEX["busy"] = True
        _refresh(root)  # first look (or Documents moved): walk once here
    elif time.monotonic() - _INDEX["at"] > REFRESH_SEC and not _INDEX["busy"]:
        _INDEX["busy"] = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _refresh(root)
        else:
            loop.create_task(asyncio.to_thread(_refresh, root))
    return _INDEX["docs"]


async def warm() -> None:
    """Walk Documents once at start-up, in a worker thread, so the first
    "read me …" never walks it on the event loop (domovoi/main.py)."""
    _INDEX["warming"] = True
    try:
        await asyncio.to_thread(_refresh, _root())
    except Exception as e:  # noqa: BLE001 — the first look walks instead
        import logging

        logging.getLogger(__name__).info("documents index: the start-up walk failed: %s", type(e).__name__)
    finally:
        _INDEX["warming"] = False


def invalidate() -> None:
    """A document was just written (a letter saved): the next look re-walks."""
    _INDEX.update(root=None, docs=(), dirs={}, at=0.0, busy=False)


def _docs(root: Path | None) -> tuple[Doc, ...]:
    if root is None:
        return index()
    return _walk(root)[0]


def exact(query: str, root: Path | None = None) -> list[Doc]:
    """Documents whose name is exactly what was said (filler words aside)."""
    from domovoi.printing import lookup

    want = tuple(lookup._words(query))
    return [d for d in _docs(root) if want and d.words == want][:3]


def find(query: str, root: Path | None = None) -> list[Doc]:
    """Documents whose name fits ``query`` (one when it's clear, a few when
    it isn't, none when nothing fits): an exact name wins outright, else
    every word said must be in the name (a word over 3 letters may be the
    start of one)."""
    from domovoi.printing import lookup

    want = lookup._words(query)
    if not want:
        return []
    docs = _docs(root)
    hit = [d for d in docs if d.words == tuple(want)]
    if hit:
        return hit[:3]
    scored = []
    for d in docs:
        if all(any(h == w or (len(w) > 3 and h.startswith(w)) for h in d.words) for w in want):
            scored.append((len(d.words) - len(want), d.title.lower(), d))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [d for _, _, d in scored[:3]]


#: How alike a heard word and a name's word must be to be offered as "did
#: you mean …?" (difflib's ratio): "leaves" for "lease" is 0.73, "lease"
#: for "least" 0.8; "limerick" against "lease" 0.31.
NEAR_RATIO = 0.7
NEAR_HALF = 0.5


def find_near(query: str, root: Path | None = None) -> list[Doc]:
    """Documents whose name is close to what was heard, for "Did you mean
    …?" after :func:`find` found nothing (speech recognition hears "the
    leaves" for "the lease"). Every word heard must be near one of the
    name's words, or one exactly right and the rest half alike; the
    closest few first. Never read without asking."""
    from difflib import SequenceMatcher

    from domovoi.printing import lookup

    want = lookup._words(query)
    if not want:
        return []
    docs = _docs(root)
    # Each heard word against each distinct name word once (a household's
    # names share most words), not once per document.
    vocab = {h for d in docs for h in d.words}
    ratio = {w: {h: SequenceMatcher(None, w, h).ratio() for h in vocab} for w in set(want)}
    scored = []
    for d in docs:
        if not d.words:
            continue
        best = [max(ratio[w][h] for h in d.words) for w in want]
        # Every word near, or one word exactly right and the rest at least
        # half alike ("the letter to the lionthord" for "… landlord": 0.59;
        # "the letter from the bank" against it: "bank" 0.33, not offered).
        if min(best) >= NEAR_RATIO or (max(best) == 1.0 and min(best) >= NEAR_HALF):
            scored.append((-sum(best) / len(best), len(d.words), d.title.lower(), d))
    scored.sort(key=lambda t: t[:3])
    return [d for *_, d in scored[:3]]


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
