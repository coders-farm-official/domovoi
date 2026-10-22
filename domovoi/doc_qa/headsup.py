"""The honest heads-up: a short card before a shaky result (documents plan, part 5, D3).

**The app decides, never the model.** A request is matched against the
measured weak spots in ``chat-weak-spots.json`` (beside this file; the
app ships the same file): a kind of task the app recognises from the
wording and the documents alone (no model involved), in a band measured
below the bar (:data:`FIRES`), gets a card naming the weakness, with only
the options that can work where it is asked (``can``).

The kinds, in the order they are checked (one card per request):

* ``scanned`` — the documents have pages that are pictures of text (found
  when the file was read); chats don't read them yet.
* ``language`` — the document looks like a language other than the tested
  ones (:func:`guess_language`: letters and stop words, our own code).
* ``calc`` — a sum, average, difference, percentage or days-between over
  numbers spread through the passages, which the document doesn't state
  itself (:func:`calc_request`, :func:`is_lookup`). "Calculate exactly"
  (:mod:`domovoi.doc_qa.calc`) is offered where the app can do the sum.
* ``compare`` — compare, contradict, every mention, throughout, two pages
  or sections named, over a document longer than what one question reads,
  unless every part named was among the passages found.
* ``rewrite`` — rewrite, translate, proofread, reformat aimed at the
  whole document, when it is longer than one reply sees.
* ``long`` — a summary read in parts that will take a long while here
  (the measured estimate), or that the home server would read faster.
* ``blurry``, ``handwriting`` — a picture (:func:`blur_variance`; the
  handwriting step needs the vision model and is not built yet).

**It doesn't nag** (:func:`decide`): a kind shows once per chat; "don't
warn me about this again" (per kind) and the master switch (Settings →
Chat & AI → Chat) are kept on each device and sent with the message. With
a kind silenced, the two that guard against a wrong answer the person
can't see coming (``calc``, ``scanned``: :data:`NOTE_KINDS`) become a
one-line note under the reply instead. With the household's AI features
off, nothing here applies (no model answers).

Pure functions with no I/O beyond reading the table once. The Kotlin twin
is ``DocHeadsUp.kt``; both are held to ``headsup_cases.json`` beside this
file (``scripts/dev/headsup_cases.py`` writes it).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

from domovoi.doc_qa.text import normalize

TABLE_PATH = Path(__file__).with_name("chat-weak-spots.json")

#: The order the kinds are checked in: the first that fires is the card.
DOC_KINDS = ("scanned", "language", "calc", "compare", "rewrite", "long")
PICTURE_KINDS = ("blurry", "handwriting")
KINDS = DOC_KINDS + PICTURE_KINDS
#: Silenced, these become a one-line note under the reply instead.
NOTE_KINDS = frozenset({"calc", "scanned"})
#: A band with one of these verdicts fires its kind's card.
FIRES = frozenset({"not_reliable", "not_measured", "slow"})
#: Options that work wherever they are offered (the rest need ``can``).
ALWAYS = frozenset({"try_anyway", "go_ahead", "send_anyway", "choose_another"})
#: The calculations "calculate exactly" does (:mod:`domovoi.doc_qa.calc`).
CALCULATOR_OPS = frozenset({"sum", "average", "difference", "days"})

LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
}


# ─── The table ───────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def load_table() -> dict[str, Any]:
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


def table_cell(table: dict[str, Any], kind: str, band: str, where: str = "both") -> dict[str, Any] | None:
    """The cell for ``kind`` in ``band``, for ``where`` ("phone", "server")
    or measured for both."""
    for c in table.get("cells") or []:
        if c.get("kind") == kind and c.get("band") == band and c.get("where") in (where, "both"):
            return c
    return None


def verdict(table: dict[str, Any], cell: dict[str, Any] | None) -> str:
    """A cell's verdict: its pass rate against the bar when it has one,
    else what it says; a cell never measured is ``not_measured``."""
    if cell is None:
        return "not_measured"
    p = cell.get("pass")
    if p is not None and cell.get("source") == "eval":
        if p >= float(table.get("good", 0.85)):
            return "good"
        if p >= float(table.get("bar", 0.7)):
            return "okay"
        return "not_reliable"
    return str(cell.get("verdict") or "not_measured")


def fires(table: dict[str, Any], kind: str, band: str, where: str) -> bool:
    return verdict(table, table_cell(table, kind, band, where)) in FIRES


# ─── Wording, recognised without a model ────────────────────────────────
def _q(text: str) -> str:
    """The question as matched: plain, lowercase, curly quotes straight,
    single spaces."""
    t = normalize(text or "").lower().replace("’", "'").replace("‘", "'")
    return " ".join(t.split())


_REWRITE_RE = re.compile(
    r"\b(rewrite|re-write|rephrase|reword|paraphrase|translate|proofread|proof-read|proof read|"
    r"reformat|re-format|redraft|copyedit|copy-edit|polish up|tidy up|simplify)\b"
    r"|\bmake (it|this|the (whole )?\w+) "
    r"(shorter|longer|simpler|clearer|plainer|friendlier|more \w+|less \w+)\b"
    r"|\b(fix|correct|check) (the |its |all (the )?)?(grammar|spelling|typos|punctuation)\b"
    r"|\bput (it|this) (in|into) (plain|simple|simpler|everyday) (english|spanish|words|language)\b"
    r"|\bturn (it|this|the (whole )?(document|letter|file|text)) into\b"
)
#: A rewrite aimed at part of the document, not all of it.
_PART_RE = re.compile(
    r"\b(paragraph|paragraphs|sentence|sentences|line|lines|clause|bullet|heading|title|caption|"
    r"(section|page|part|chapter|article|item) \d+|p\. ?\d+|"
    r"(first|last|second|third|opening|closing|final) (part|paragraph|section|page|line|sentence|bit))\b"
)

_COMPARE_RE = re.compile(
    r"\b(compare|compares|comparing|comparison|contrast|differ|differs|different from|differences|"
    r"contradict\w*|consistent|inconsisten\w*|throughout|anywhere else|elsewhere in|"
    r"every (mention|time|place|instance|reference|section|page)|"
    r"each (mention|time|place|instance|reference)|"
    r"all (the )?(mentions|references|instances|places)|changed? from|versus|vs\.?)(?=\W|$)"
    r"|\bdifference between\b"
)
_PLACE_RE = re.compile(r"\b(page|p\.|pg\.?|section|part|chapter|clause|article|sheet) ?(\d+)\b")

# Aggregation only (review round 1): the days BETWEEN two dates. "How many
# days after move-out is it returned?" asks for a stated number: a lookup.
_DAYS_RE = re.compile(r"\bhow many (days|weeks|months|years)\b.*\bbetween\b.+\band\b"
                      r"|\b(days|weeks|months) between\b.+\band\b|\bhow long (is it )?between\b.+\band\b")
_AVERAGE_RE = re.compile(r"\baverage\b|\bthe mean\b|\bmean (of|value|amount|cost|price|bill|score)\b")
_DIFF_RE = re.compile(r"\bhow much (more|less|higher|lower|bigger|smaller|cheaper|dearer)\b.+\bthan\b"
                      r"|\bhow many more\b.+\bthan\b")
_DIFF_BETWEEN_RE = re.compile(r"\bdifference (between|in)\b")
_QUANTITY_RE = re.compile(r"\b(how much|amount|amounts|cost|costs|price|prices|fee|fees|rent|pay|paid|"
                          r"charge|charges|total|number|balance|salary|wage|wages|deposit|bill)\b|\$|€|£")
# A sum asked for, not a stated one ("the sum insured", "the total number
# of bedrooms", "charges combined into the payment" are lookups).
_SUM_RE = re.compile(r"\b(add up|added up|adds up|add together|added together|sum of|total of|in total|"
                     r"altogether|grand total|combined total|all together)\b"
                     r"|\bthe total\b(?! number)|\bthe sum\b(?! insured)")

_NUMBER_RE = re.compile(r"(?<![\w.,])[-$€£]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.,])[-$€£]?\d+(?:\.\d+)?%?")
_LOOKUP_WORDS = {
    "sum": r"(total|totals|sum|altogether|combined|in all|grand total)",
    "average": r"(average|mean)",
    "percent": r"(percent|percentage|per cent|%)",
}


# Only clear requests to read the words (review round 1): not "say",
# "sign", "form" or "note", which a photo of anything may come with.
_READING_RE = re.compile(
    r"\b(read|reads|reading|transcribe|transcription|translate|spell out|spelled out|copy out|type out)\b"
    r"|\bwhat (does|do|did) (it|this|that|(this|that|the|my|our|your) [\w ']{1,30}) say\b|\bwhat (it|this|that) says\b"
    r"|\bwhat'?s written\b|\bwhat is written\b|\bwhat'?s the (text|writing|wording)\b|\bwhat words\b")


def reading_request(question: str) -> bool:
    """A message with a picture that asks to read words in it ("what does
    this letter say?"); a blurry photo of a sunset needs no heads-up."""
    return bool(_READING_RE.search(_q(question)))


def wants_reading(question: str) -> bool:
    """A picture message whose words matter: a request to read them
    (:func:`reading_request`), or a letter to explain ("what do I need to
    do about this bill?", documents plan D6: ``letter.letter_request``)."""
    from domovoi.doc_qa import letter as _letter

    return reading_request(question) or _letter.letter_request(question)


def rewrite_request(question: str) -> bool:
    """A rewrite, translation, proofread or reformat aimed at the whole
    document ("rewrite the second paragraph" is aimed at a part)."""
    q = _q(question)
    return bool(_REWRITE_RE.search(q)) and not _PART_RE.search(q)


def named_places(question: str) -> list[str]:
    """The pages or sections a question names, as ("page", "7") labels:
    "p. 7", "section 3"."""
    out: list[str] = []
    for m in _PLACE_RE.finditer(_q(question)):
        kind = m.group(1).rstrip(".")
        kind = "page" if kind in ("p", "pg", "page") else kind
        label = f"{kind} {m.group(2)}"
        if label not in out:
            out.append(label)
    return out


def compare_request(question: str) -> bool:
    """A question that compares parts of a document, or looks through all
    of it: the wording, or two pages or sections named."""
    q = _q(question)
    return bool(_COMPARE_RE.search(q)) or len(named_places(question)) >= 2


def calc_request(question: str) -> str | None:
    """The aggregation a question asks for: ``days`` (between two dates),
    ``average``, ``difference`` or ``sum``; None for any other question,
    a lookup of a stated number included."""
    q = _q(question)
    if _DAYS_RE.search(q):
        return "days"
    if _AVERAGE_RE.search(q):
        return "average"
    if _DIFF_RE.search(q) or (_DIFF_BETWEEN_RE.search(q) and _QUANTITY_RE.search(q)):
        return "difference"
    if _SUM_RE.search(q):
        return "sum"
    return None


def numbers_in(text: str) -> list[str]:
    """The numbers a text shows ("$1,200", "12.5%", "30"), as written."""
    return [m.group(0) for m in _NUMBER_RE.finditer(normalize(text or ""))]


def amounts_in(texts: Sequence[str]) -> set[str]:
    """The numbers the passages show that "calculate exactly" can read for
    certain (:func:`calc.read_numbers`), as written, each once; where some
    carry a currency or "%", only those (a bill's one amount beside its
    account number is still one amount)."""
    from domovoi.doc_qa import calc as _calc

    ctx = _calc.context(list(texts))
    nums = [n for t in texts for n in _calc.read_numbers(t, ctx) if n.value is not None]
    priced = [n for n in nums if n.unit]
    return {n.written for n in (priced or nums)}


def is_lookup(op: str, texts: Iterable[str]) -> bool:
    """The passages state what was asked: a "total", "average" or
    "percent" next to a number (a spreadsheet's column totals, a bill's
    total line), so it is quoted, not calculated."""
    words = _LOOKUP_WORDS.get(op)
    if not words:
        return False
    pat = re.compile(r"\b" + words + r"\b[^.\n]{0,40}?\d|\d[^.\n]{0,12}?\b" + words + r"\b" if op != "percent"
                     else r"\d\s?(%|percent|per cent)")
    return any(pat.search(_q(t)) for t in texts)


# ─── Language: letters and stop words, our own code ─────────────────────
_STOP: dict[str, frozenset[str]] = {
    "en": frozenset("the and of to is in that it for you with as on are this be at by not or from have was "
                    "will your an they which but if we has can all their would there been any shall must".split()),
    "es": frozenset("el los las del que y en por para con una es se no su al lo como más pero sus le ya "
                    "este esta entre cuando muy sin sobre también hasta hay donde quien desde todo nos "
                    "durante ese eso usted".split()),
    "fr": frozenset("le les des et est une du dans pour pas qui sur au avec ce il elle sont ou aux ne mais "
                    "nous vous leur été cette être fait comme plus tout si ses aussi".split()),
    "de": frozenset("der die das und ist nicht den mit von zu des sich auf für dem ein eine im sie es auch "
                    "als an wird werden bei oder nach aus sind wenn kann nur über einen einer wir ihr sein".split()),
    "it": frozenset("il di che è per non una sono gli della del le si con da nel alla anche ma più come "
                    "questo essere dei delle ha al ci se lo suo tra molto quando sua".split()),
    "pt": frozenset("o os as não uma do da dos das em para com é que por se mais na no ao como mas foi ele "
                    "ela são seu sua também ou quando muito já está isso pelo pela você".split()),
    "nl": frozenset("de het een en van is dat op te in zijn voor met niet aan er ook als bij maar om nog "
                    "wordt worden dit uit naar heeft kan zo deze wel geen door hun u".split()),
    "pl": frozenset("się nie na jest że do to jak ale po co tak za od przez dla jego czy jej tylko oraz być "
                    "który która które są ten ta już może będzie tym także lub gdy ich było jako".split()),
}
_LANG_ORDER = ("en", "es", "fr", "de", "it", "pt", "nl", "pl")
_WORD_RE = re.compile(r"[^\W\d_]+")
#: A guess needs this many words, this share of them stop words of the
#: best language, and this lead over the next best.
LANG_MIN_WORDS = 25
LANG_MIN_SHARE = 0.12
LANG_LEAD = 1.5


def guess_language(text: str) -> str | None:
    """The language ``text`` looks like ("en", "es", "pl", ...), or None
    when it is too short or too mixed to say."""
    words = _WORD_RE.findall(normalize(text or "").lower())
    if len(words) < LANG_MIN_WORDS:
        return None
    counts = {lang: sum(1 for w in words if w in _STOP[lang]) for lang in _LANG_ORDER}
    ranked = sorted(_LANG_ORDER, key=lambda lang: (-counts[lang], _LANG_ORDER.index(lang)))
    best, second = ranked[0], ranked[1]
    if counts[best] < LANG_MIN_SHARE * len(words):
        return None
    if counts[best] < LANG_LEAD * counts[second]:
        return None
    return best


def doc_sample(parts: Sequence[dict[str, Any]], limit: int = 3000) -> str:
    """The first ``limit`` characters of a document's text, for the
    language guess."""
    out: list[str] = []
    used = 0
    for p in parts or []:
        t = str(p.get("text") or "")
        if not t:
            continue
        out.append(t[: max(0, limit - used)])
        used += len(out[-1]) + 1
        if used >= limit:
            break
    return "\n".join(out)[:limit]


# ─── Pictures ────────────────────────────────────────────────────────────
def blur_variance(grey: Sequence[Sequence[int]]) -> float:
    """The variance of the Laplacian of a grey image (rows of 0-255): low
    is blurry. Our own code (the 4-neighbour kernel), about 30 lines on
    each side; the threshold is the table's ``blur_variance``."""
    h = len(grey)
    w = len(grey[0]) if h else 0
    if h < 3 or w < 3:
        return float("inf")
    n = 0
    s = 0.0
    s2 = 0.0
    for y in range(1, h - 1):
        up, row, down = grey[y - 1], grey[y], grey[y + 1]
        for x in range(1, w - 1):
            v = up[x] + down[x] + row[x - 1] + row[x + 1] - 4 * row[x]
            n += 1
            s += v
            s2 += v * v
    mean = s / n
    return s2 / n - mean * mean


# ─── The request and the card ───────────────────────────────────────────
@dataclass
class Doc:
    name: str
    chars: int
    pages: int | None = None
    words: int = 0
    scanned_pages: list[int] = field(default_factory=list)
    #: The start of its text (:func:`doc_sample`), for the language guess.
    sample: str = ""


@dataclass
class Request:
    """What the rules see: the wording, the documents, the passages found,
    where it is asked, the job's facts and the options that work here."""
    mode: str                       # question | summary | long_summary | picture
    question: str = ""
    where: str = "server"           # phone | server
    docs: list[Doc] = field(default_factory=list)
    #: The passages found for a question: (label, text).
    given: list[tuple[str, str]] = field(default_factory=list)
    #: long_summary: parts, estimate_s; home_server_s when the home
    #: server is reachable and measured faster (D2's offer).
    facts: dict[str, Any] = field(default_factory=dict)
    #: A picture: blur (the Laplacian variance), handwritten (True when the
    #: classifying step said so).
    picture: dict[str, Any] = field(default_factory=dict)
    #: Options beyond ALWAYS that work here: calculate, home_server, sections.
    can: frozenset[str] = frozenset()
    #: The household's AI features are on.
    ai_on: bool = True


@dataclass
class Card:
    kind: str
    text: str
    options: list[str]
    #: The one-line note this kind becomes when silenced (NOTE_KINDS).
    note: str | None = None
    #: The calculation, for calc.
    op: str | None = None
    #: The note under the answer when the person went ahead past the card.
    after: str | None = None
    #: Said as its note only, never as a card (scanned pages under a
    #: question's answer: E-HEADSUP measured the card there as a false
    #: alarm whenever the answer was on a page with text).
    note_only: bool = False

    def to_json(self, table: dict[str, Any] | None = None) -> dict[str, Any]:
        t = table or load_table()
        labels = t.get("options") or {}
        return {"kind": self.kind, "text": self.text,
                "options": [{"id": o, "label": labels.get(o, o)} for o in self.options],
                "never_label": t.get("never_label"), "note": self.note, "op": self.op}


def _list_and(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _size(d: Doc) -> str:
    if d.pages:
        return f"{d.pages} page{'' if d.pages == 1 else 's'}"
    w = d.words or max(1, d.chars // 6)
    if w >= 1000:
        w = int(math.floor(w / 100.0 + 0.5)) * 100
    return f"about {w:,} words"


def _docname(docs: Sequence[Doc]) -> str:
    return docs[0].name if len(docs) == 1 else "These documents"


def _scanned_words(pages: Sequence[int]) -> dict[str, str]:
    """"Pages 4 and 7" / "Page 3" / "18 pages", and the verbs that go with it."""
    n = len(pages)
    if n == 1:
        return {"scanned": f"Page {pages[0]}", "are_pictures": "is a picture", "were_not": "wasn't"}
    words = f"{n} pages" if n > 5 else "Pages " + _list_and([str(p) for p in pages])
    return {"scanned": words, "are_pictures": "are pictures", "were_not": "weren't"}


def _options(kind_opts: Sequence[str], can: frozenset[str]) -> list[str]:
    out = [o for o in kind_opts if o in ALWAYS or o in can]
    return out or ["try_anyway"]


def _fmt(template: str, **kw: Any) -> str:
    return template.format(**kw)


def findings(req: Request, table: dict[str, Any] | None = None) -> list[Card]:
    """Every kind that fires for ``req``, in :data:`KINDS` order, as cards
    (the first is the one shown; :func:`decide` applies the nag rules)."""
    t = table or load_table()
    if not req.ai_on:
        return []
    kinds = t["kinds"]
    th = t.get("thresholds") or {}
    out: list[Card] = []
    if req.mode == "picture":
        blur = req.picture.get("blur")
        if (blur is not None and float(blur) < float(th.get("blur_variance", 0)) and wants_reading(req.question)
                and fires(t, "blurry", "blurry", req.where)):
            k = kinds["blurry"]
            out.append(Card("blurry", k["card"], _options(k["options"], req.can)))
        if req.picture.get("handwritten") and fires(t, "handwriting", "handwritten", req.where):
            k = kinds["handwriting"]
            out.append(Card("handwriting", k["card"], _options(k["options"], req.can)))
        return out
    docs = [d for d in req.docs if d.chars > 0 or d.scanned_pages]
    if not docs:
        return out
    question = req.question or ""
    total_chars = sum(d.chars for d in docs)
    one_pass = int(req.facts.get("one_pass_chars") or th.get("one_pass_chars", 4800))

    # Scanned pages.
    scanned_doc = next((d for d in docs if d.scanned_pages), None)
    if scanned_doc is not None and fires(t, "scanned", "pictures_of_text", req.where):
        k = kinds["scanned"]
        w = _scanned_words(scanned_doc.scanned_pages)
        out.append(Card("scanned", _fmt(k["card"], doc=scanned_doc.name, **w),
                        _options(k["options"], req.can), note=_fmt(k["note"], **w),
                        note_only=req.mode == "question"))

    # Language.
    for d in docs:
        lang = guess_language(d.sample)
        if lang and lang not in (t.get("tested_languages") or []) and fires(t, "language", "untested", req.where):
            k = kinds["language"]
            out.append(Card("language", _fmt(k["card"], doc=d.name, language=LANGUAGE_NAMES.get(lang, lang)),
                            _options(k["options"], req.can)))
            break

    texts = [txt for _label, txt in req.given]
    # Exact calculation (a question over passages found).
    if req.mode == "question":
        op = calc_request(question)
        if op == "days":
            # Two dates to count between, or nothing to offer.
            from domovoi.doc_qa import calc as _calc

            enough = sum(len(_calc.read_dates(txt)) for txt in texts) >= 2
        else:
            # Two amounts the app can read for certain (review round 2): one
            # number on the page ("What is the total amount due?" over a
            # bill's one amount) is a lookup, quoted, not calculated.
            enough = len(amounts_in(texts)) >= 2
        if op and enough and not is_lookup(op, texts) and fires(t, "calc", "combine", req.where):
            k = kinds["calc"]
            if op in CALCULATOR_OPS and "calculate" in req.can:
                out.append(Card("calc", k["card"], _options(k["options"], req.can), note=k["note"], op=op))
            else:
                out.append(Card("calc", k["card_no_calculator"], ["try_anyway"], note=k["note"], op=op))

        # Comparing parts of a long document.
        if not op and compare_request(question) and total_chars > one_pass:
            places = named_places(question)
            labels = [_q(label) for label, _txt in req.given]
            found_all = bool(places) and all(any(_place_in(p, lab) for lab in labels) for p in places)
            if not found_all and fires(t, "compare", "long", req.where):
                k = kinds["compare"]
                out.append(Card("compare", _fmt(k["card"], k=len(req.given) or 4),
                                _options(k["options"], req.can)))

        # Rewriting the whole document.
        if rewrite_request(question) and total_chars > one_pass and fires(t, "rewrite", "over_one_pass", req.where):
            k = kinds["rewrite"]
            big = max(docs, key=lambda d: d.chars)
            seen = max(1, min(99, int(math.floor(100.0 * one_pass / max(1, total_chars) + 0.5))))
            out.append(Card("rewrite", _fmt(k["card"], doc=_docname(docs) if len(docs) > 1 else big.name,
                                            size=_size(big) if len(docs) == 1 else f"about {_size_words(docs)}",
                                            seen=f"{seen}%"),
                            _options(k["options"], req.can)))

    # A long summary.
    if req.mode == "long_summary":
        est = req.facts.get("estimate_s")
        home = req.facts.get("home_server_s")
        parts = int(req.facts.get("parts") or 0)
        slow = est is not None and float(est) >= float(th.get("long_wait_s", 180))
        offer = (home is not None and "home_server" in req.can and est is not None
                 and float(est) >= float(th.get("home_server_wait_s", 60)))
        if parts >= 2 and (slow or offer):
            k = kinds["long"]
            from domovoi.doc_qa import longdoc

            text = _fmt(k["card"], doc=_docname(docs) if len(docs) > 1 else docs[0].name,
                        size=_size(docs[0]) if len(docs) == 1 else f"about {_size_words(docs)}",
                        parts=parts, time=longdoc.about_time(float(est)))
            opts = list(k["options"])
            if offer:
                text += " " + _fmt(k["home_server_line"], home_time=longdoc.about_time(float(home)))
            else:
                opts = [o for o in opts if o != "home_server"]
            out.append(Card("long", text, _options(opts, req.can)))
    for c in out:
        c.after = kinds[c.kind].get("after")
    return out


def _size_words(docs: Sequence[Doc]) -> str:
    w = sum(d.words or max(1, d.chars // 6) for d in docs)
    if w >= 1000:
        w = int(math.floor(w / 100.0 + 0.5)) * 100
    return f"{w:,} words"


def _place_in(place: str, label: str) -> bool:
    """A named place ("page 7", "section 3") is the passage label ("p. 7",
    "section 3: deposit", "lease.pdf, p. 7")."""
    kind, _, num = place.partition(" ")
    if kind == "page":
        return bool(re.search(r"\bp\. ?" + re.escape(num) + r"\b", label))
    return bool(re.search(r"\b" + re.escape(kind) + r"\W{0,3}" + re.escape(num) + r"\b", label))


#: When a question's search finds no passage, the app answers "I couldn't
#: find that in the document" itself; only these kinds still show their
#: card first (a whole-document rewrite rarely shares a word with the
#: document; a language Domovoi wasn't tested in may be why nothing
#: matched), and scanned pages their note.
NOTHING_FOUND_KINDS = frozenset({"rewrite", "language", "scanned"})


def when_nothing_found(found: Sequence[Card]) -> list[Card]:
    """The findings that still apply when the search found nothing."""
    return [c for c in found if c.kind in NOTHING_FOUND_KINDS]


def check(req: Request, table: dict[str, Any] | None = None) -> Card | None:
    """The card for ``req`` before the nag rules, or None."""
    f = findings(req, table)
    return f[0] if f else None


@dataclass
class Decision:
    """What a reply gets: a card to show instead of answering (None: go
    ahead), and notes under the reply."""
    card: Card | None = None
    notes: list[dict[str, str]] = field(default_factory=list)


def decide(found: Sequence[Card], *, shown: Iterable[str] = (), skip: Iterable[str] = (),
           off: bool = False, chosen: tuple[str, str] | None = None) -> Decision:
    """The nag rules over :func:`findings`:

    * ``chosen`` — (kind, option) the person picked on this request's card:
      no new card; that kind's note stays when the option isn't the fix
      ("try anyway" on a sum, "go ahead" past scanned pages).
    * a ``note_only`` finding is always its note;
    * a kind in ``shown`` (its card was shown in this chat), in ``skip``
      ("don't warn me about this again" on this device) or any kind while
      ``off`` (the master switch) shows no card; a silenced
      :data:`NOTE_KINDS` kind becomes its one-line note.
    * otherwise the first kind left is the card.
    """
    shown_s, skip_s = set(shown), set(skip)
    card: Card | None = None
    notes: list[dict[str, str]] = []
    for c in found:
        if chosen is not None and c.kind == chosen[0]:
            # Went ahead past the card: what it means for this answer
            # (nothing to say when the option was the fix).
            text = c.note if c.kind in NOTE_KINDS else c.after
            if text and chosen[1] not in ("calculate", "sections", "home_server"):
                notes.append({"code": f"heads_up_{c.kind}", "text": text})
            continue
        silenced = (off or c.note_only or c.kind in skip_s or c.kind in shown_s or chosen is not None
                    or card is not None)
        if not silenced:
            card = c
            continue
        if c.kind in NOTE_KINDS and c.note:
            notes.append({"code": f"heads_up_{c.kind}", "text": c.note})
    # A card is the whole reply for now: the notes come with the answer,
    # once the person has picked an option (decided again with chosen).
    return Decision(card=card, notes=[] if card is not None else notes)


def valid_kinds(items: Iterable[Any]) -> list[str]:
    """The known kinds among ``items`` (a device's "don't warn me" list,
    as it arrives)."""
    return [str(k) for k in items if str(k) in KINDS]
