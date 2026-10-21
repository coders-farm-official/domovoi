"""The help index: Domovoi's own words, cut into passages and searched.

What goes in (each a :class:`Passage` with a title, the section it sits in
and a link a person can open):

* every page of the user manual (``web/static/manual-content.json``, built
  by ``scripts/manual/build_manual.py``), linked as ``#manual/<piece>``;
  a page that belongs to a plugin is in only while that plugin is;
* ``docs/FAQ.md`` and ``docs/TROUBLESHOOTING.md``, one passage per
  section (long sections in a few pieces), linked to the doc on GitHub;
* the help entries for the dashboard's error codes (:mod:`.error_codes`);
* the help files of installed plugins (the manifest's ``help``,
  :class:`PluginHelp`), one passage per section.

Search is BM25 (k1 1.2, b 0.75) over a small stemmed vocabulary, with a
page's title and search words counted more than its body. A hit is
:meth:`Hit.confident` only when it matched most of the question's own
words, so a question the manual doesn't answer finds nothing rather than
the nearest page: "the manual doesn't cover that" is the honest answer.

Built once per process and rebuilt when a source file changes (a stat per
search). Standard library only.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
MANUAL_PATH = REPO / "web" / "static" / "manual-content.json"
FAQ_PATH = REPO / "docs" / "FAQ.md"
TROUBLESHOOTING_PATH = REPO / "docs" / "TROUBLESHOOTING.md"
DOC_BASE = "https://github.com/coders-farm-official/domovoi/blob/{ref}/"

#: A passage longer than this many words is cut, on paragraph lines.
PASSAGE_WORDS = 170
#: A plugin's help file is read up to this size (the manifest check caps it too).
PLUGIN_HELP_MAX_BYTES = 32 * 1024

KIND_MANUAL = "manual"
KIND_FAQ = "faq"
KIND_TROUBLESHOOTING = "troubleshooting"
KIND_ERROR = "error"
KIND_PLUGIN = "plugin"

# Words that carry no meaning for a search. Kept short on purpose: "not",
# "off", "no" and "why" do matter for a help question, and stay.
_STOP = frozenset("""
a an and are as at be been but by can could did do does doing for from get
got had has have how i if in into is it its just me my of on or our please
should so than that the their them then there these they this to too us was
we were what when where which who will with would you your yours i'm im it's
want wanna know tell show let make domovoi domo
""".split())

# A few words people use for the same thing as the manual does.
_SYNONYMS = {
    "satellite": "room", "satellites": "room", "speaker": "room", "speakers": "room",
    "pi": "room", "wifi": "wi-fi", "wlan": "wi-fi", "tv": "screen", "display": "screen",
    "upgrade": "update", "upgrades": "update", "updating": "update",
    "reboot": "restart", "rebooting": "restart",
    "song": "music", "songs": "music", "tune": "music",
    "pic": "picture", "photo": "picture", "photos": "picture", "image": "picture", "images": "picture",
    "screenshot": "picture", "addon": "plugin", "addons": "plugin", "extension": "plugin",
    "mic": "microphone",
}

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")
_INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_PILLS = {"{admin}": "(admin)", "{preview}": "(preview)", "{plugin}": "(plugin)"}


def stem(word: str) -> str:
    """A light suffix strip: enough to meet "rooms"/"room", "playing"/"play"."""
    w = word.strip("'-")
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def tokens(text: str, *, keep_stop: bool = False) -> list[str]:
    out: list[str] = []
    for raw in _WORD_RE.findall((text or "").lower()):
        if not keep_stop and raw in _STOP:
            continue
        word = _SYNONYMS.get(raw, raw)
        out.append(stem(word))
    return out


def plain_inline(text: str, wake: str = "Hey Jarvis") -> str:
    """The manual's inline Markdown subset as plain words."""
    t = _INLINE_LINK_RE.sub(r"\1", str(text or ""))
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    t = t.replace("`", "")
    for k, v in _PILLS.items():
        t = t.replace(k, v)
    return t.replace("{wake}", f"“{wake}”")


@dataclass(frozen=True)
class Passage:
    id: str
    title: str
    text: str
    kind: str
    section: str
    link: str | None = None
    keywords: tuple[str, ...] = ()
    #: The manual piece this passage is (or points to), when there is one.
    piece: str | None = None
    #: Plugin pages and plugin help: the plugin's slug.
    plugin: str | None = None


@dataclass(frozen=True)
class PluginHelp:
    """One installed plugin's help file, as :func:`plugin_help_from_rows` reads it."""

    slug: str
    name: str
    text: str
    link: str | None = None


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float
    #: Share of the question's own words this passage holds (0..1).
    coverage: float
    matched: tuple[str, ...] = ()

    #: Below this BM25 score, a hit is noise.
    MIN_SCORE = 1.5
    #: A confident hit holds more than half of the question's own words.
    MIN_COVERAGE = 0.6

    def confident(self) -> bool:
        return self.score >= self.MIN_SCORE and self.coverage >= self.MIN_COVERAGE

    def snippet(self, words: int = 40) -> str:
        return first_words(self.passage.text, words)


def first_words(text: str, words: int) -> str:
    parts = (text or "").split()
    if len(parts) <= words:
        return " ".join(parts)
    return " ".join(parts[:words]).rstrip(",;:—-") + "…"


# ── building the passages ────────────────────────────────────────────────


def _block_lines(block: dict, wake: str) -> list[str]:
    kind = block.get("type")
    prefix = {"app": "In the app: ", "dashboard": "On the dashboard: "}
    if kind == "p":
        return [prefix.get(block.get("only"), "") + plain_inline(block.get("text", ""), wake)]
    if kind == "list":
        out = []
        for i, item in enumerate(block.get("items") or [], 1):
            mark = f"{i}." if block.get("ordered") else "-"
            out.append(f"{mark} {prefix.get(item.get('only'), '')}{plain_inline(item.get('text', ''), wake)}")
        return out
    if kind == "label":
        text = plain_inline(block.get("text", ""), wake)
        if block.get("note"):
            text += " " + plain_inline(block["note"], wake)
        return [text + ":"]
    if kind == "say":
        said = [f"“{plain_inline(p.get('text', ''), wake)}”" for p in block.get("phrases") or []]
        return ["Say " + ", ".join(said) + "."] if said else []
    if kind == "table":
        rows = [block.get("header") or []] + list(block.get("rows") or [])
        return [" | ".join(plain_inline(c, wake) for c in row if c) for row in rows]
    return []


def _chunks(lines: list[str], limit: int = PASSAGE_WORDS) -> list[str]:
    """Lines joined into pieces of at most ``limit`` words (a single long
    line stays whole)."""
    out: list[str] = []
    cur: list[str] = []
    n = 0
    for ln in lines:
        w = len(ln.split())
        if cur and n + w > limit:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(ln)
        n += w
    if cur:
        out.append("\n".join(cur))
    return out


def manual_passages(doc: dict, *, installed_plugins: set[str] | None = None) -> list[Passage]:
    wake_names = (doc.get("wake_word") or {}).get("names") or {}
    wake = wake_names.get((doc.get("wake_word") or {}).get("default"), "Hey Jarvis")
    topics = {t["id"]: t for t in doc.get("topics") or []}
    out: list[Passage] = []
    start = doc.get("start") or {}
    lines = [ln for b in start.get("blocks") or [] for ln in _block_lines(b, wake)]
    for i, chunk in enumerate(_chunks(lines)):
        out.append(Passage(id=f"manual:start:{i}", title=start.get("title", "Start here"),
                           text=chunk, kind=KIND_MANUAL, section="Start here",
                           link="#manual", piece="start"))
    for piece in doc.get("pieces") or []:
        if piece.get("alias_of"):
            continue
        plugin = piece.get("plugin")
        if plugin and installed_plugins is not None and plugin not in installed_plugins:
            continue
        topic = topics.get(piece.get("topic"), {})
        lines = [ln for b in piece.get("blocks") or [] for ln in _block_lines(b, wake)]
        if not lines:
            continue
        section = f"{topic.get('title', 'Manual')} → {piece['title']}"
        kws = tuple(piece.get("keywords") or ()) + (piece.get("subtitle") or "",)
        for i, chunk in enumerate(_chunks(lines)):
            out.append(Passage(
                id=f"manual:{piece['id']}:{i}", title=piece["title"], text=chunk,
                kind=KIND_MANUAL, section=section, link=f"#manual/{piece['id']}",
                keywords=kws, piece=piece["id"], plugin=plugin,
            ))
    return out


def github_anchor(heading: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"[`*_]", "", text).lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def markdown_sections(md: str) -> list[tuple[str, list[str], str]]:
    """``[(heading, body lines, anchor)]`` for the ``##`` and ``###``
    sections of a Markdown file (fenced code kept, as text)."""
    out: list[tuple[str, list[str], str]] = []
    heading: str | None = None
    body: list[str] = []
    seen: dict[str, int] = {}
    in_code = False

    def flush() -> None:
        if heading is not None:
            slug = github_anchor(heading)
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            out.append((heading, body, slug if n == 0 else f"{slug}-{n}"))

    for ln in md.splitlines():
        if ln.startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", ln)
        if m and len(m.group(1)) in (2, 3):
            flush()
            heading, body = m.group(2), []
        elif m and len(m.group(1)) == 1:
            # The document's own title: counts for the anchor numbering.
            seen[github_anchor(m.group(2))] = seen.get(github_anchor(m.group(2)), 0) + 1
        elif heading is not None:
            body.append(ln)
    flush()
    return out


def _paragraph_lines(body: list[str]) -> list[str]:
    """Body lines folded into paragraphs (blank lines split them)."""
    out: list[str] = []
    cur: list[str] = []
    for ln in body:
        if not ln.strip():
            if cur:
                out.append(" ".join(cur))
                cur = []
            continue
        s = ln.strip()
        if s.startswith(("- ", "* ", "|")) or re.match(r"^\d+\.\s", s):
            if cur:
                out.append(" ".join(cur))
                cur = []
            out.append(plain_inline(s))
            continue
        cur.append(plain_inline(s))
    if cur:
        out.append(" ".join(cur))
    return [ln for ln in out if ln.strip() and not ln.strip().startswith("<!--")]


def doc_passages(path: Path, kind: str, label: str, *, ref: str = "main") -> list[Passage]:
    try:
        md = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rel = path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else path.name
    base = DOC_BASE.format(ref=ref) + rel
    out: list[Passage] = []
    for heading, body, anchor in markdown_sections(md):
        lines = _paragraph_lines(body)
        if not lines:
            continue
        title = plain_inline(heading)
        for i, chunk in enumerate(_chunks(lines)):
            out.append(Passage(id=f"{kind}:{anchor}:{i}", title=title, text=chunk, kind=kind,
                               section=f"{label} → {title}", link=f"{base}#{anchor}"))
    return out


def plugin_passages(helps: Iterable[PluginHelp]) -> list[Passage]:
    out: list[Passage] = []
    for h in helps:
        sections = markdown_sections("## " + h.name + "\n" + h.text) if not re.search(
            r"^#{2,3}\s", h.text, re.M) else markdown_sections(h.text)
        for heading, body, anchor in sections:
            lines = _paragraph_lines(body)
            if not lines:
                continue
            title = plain_inline(heading)
            for i, chunk in enumerate(_chunks(lines)):
                out.append(Passage(
                    id=f"plugin:{h.slug}:{anchor}:{i}", title=title, text=chunk, kind=KIND_PLUGIN,
                    section=f"{h.name} plugin → {title}", link=h.link, plugin=h.slug,
                    keywords=(h.name, h.slug, "plugin"),
                ))
    return out


def error_passages() -> list[Passage]:
    from domovoi.help_desk.error_codes import ERROR_CODES

    out = []
    for code, entry in ERROR_CODES.items():
        out.append(Passage(
            id=f"error:{code}", title=entry.title, text=entry.text, kind=KIND_ERROR,
            section=f"Error codes → {entry.title}", link=f"#manual/{entry.piece}",
            keywords=(code, code.replace("_", " ").replace("-", " "), "error"), piece=entry.piece,
        ))
    return out


# ── the index ────────────────────────────────────────────────────────────


@dataclass
class HelpIndex:
    passages: list[Passage]
    _tf: list[dict[str, float]] = field(default_factory=list, repr=False)
    _len: list[int] = field(default_factory=list, repr=False)
    _df: dict[str, int] = field(default_factory=dict, repr=False)
    _avg: float = 1.0

    K1 = 1.2
    B = 0.75

    def __post_init__(self) -> None:
        for p in self.passages:
            tf: dict[str, float] = {}
            body = tokens(p.text)
            for t in body:
                tf[t] = tf.get(t, 0.0) + 1.0
            for t in tokens(p.title):
                tf[t] = tf.get(t, 0.0) + 3.0
            for kw in p.keywords:
                for t in tokens(kw):
                    tf[t] = tf.get(t, 0.0) + 2.0
            self._tf.append(tf)
            self._len.append(max(1, len(body) + len(tokens(p.title))))
            for t in tf:
                self._df[t] = self._df.get(t, 0) + 1
        self._avg = (sum(self._len) / len(self._len)) if self._len else 1.0

    def __len__(self) -> int:
        return len(self.passages)

    def get(self, passage_id: str) -> Passage | None:
        for p in self.passages:
            if p.id == passage_id:
                return p
        return None

    @staticmethod
    def _weight(p: Passage, lowered: str) -> float:
        """An error code's entry is the answer when the question names the
        code ("what does model_missing mean"), and a poor one otherwise:
        "how do I install a plugin" wants the Plugins page, not the entry
        for "that plugin isn't installed". The manual's own pages come
        slightly before the docs, which say the same at more length."""
        if p.kind == KIND_ERROR:
            code = p.keywords[0] if p.keywords else ""
            named = code and (code in lowered or code.replace("_", " ").replace("-", " ") in lowered)
            return 1.6 if named else 0.45
        if p.kind == KIND_MANUAL:
            return 1.15
        return 1.0

    def search(self, query: str, *, limit: int = 3, kinds: tuple[str, ...] | None = None) -> list[Hit]:
        q = list(dict.fromkeys(tokens(query)))
        if not q or not self.passages:
            return []
        n = len(self.passages)
        lowered = (query or "").lower()
        scored: list[Hit] = []
        for i, p in enumerate(self.passages):
            if kinds is not None and p.kind not in kinds:
                continue
            tf = self._tf[i]
            score = 0.0
            matched = []
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                matched.append(t)
                df = self._df.get(t, 0)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = f + self.K1 * (1 - self.B + self.B * self._len[i] / self._avg)
                score += idf * f * (self.K1 + 1) / denom
            if score > 0:
                # A page's own search phrase, said whole ("add a room",
                # "wake word"), is a strong sign it is the page asked for.
                score += sum(3.0 for kw in p.keywords
                             if " " in kw.strip() and kw.lower() in lowered)
                score *= self._weight(p, lowered)
                scored.append(Hit(p, round(score, 4), len(matched) / len(q), tuple(matched)))
        scored.sort(key=lambda h: (-h.score, -h.coverage, h.passage.id))
        # One hit per page: the best piece of a long page stands for it.
        out: list[Hit] = []
        seen: set[str] = set()
        for h in scored:
            key = h.passage.link or h.passage.id
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
            if len(out) >= limit:
                break
        return out


def build_index(
    *,
    manual: dict | None = None,
    installed_plugins: set[str] | None = None,
    plugin_helps: Iterable[PluginHelp] = (),
    doc_ref: str = "main",
    faq_path: Path = FAQ_PATH,
    troubleshooting_path: Path = TROUBLESHOOTING_PATH,
) -> HelpIndex:
    passages: list[Passage] = []
    if manual is None:
        try:
            manual = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("help index: the manual could not be read (%s)", e)
            manual = {}
    passages += manual_passages(manual, installed_plugins=installed_plugins)
    passages += doc_passages(faq_path, KIND_FAQ, "FAQ", ref=doc_ref)
    passages += doc_passages(troubleshooting_path, KIND_TROUBLESHOOTING, "Troubleshooting", ref=doc_ref)
    passages += error_passages()
    passages += plugin_passages(plugin_helps)
    return HelpIndex(passages)


# ── the process's copy ───────────────────────────────────────────────────

_LOCK = threading.Lock()
_CACHE: dict[str, object] = {}


def _stamp(path: Path) -> tuple[float, int]:
    try:
        st = path.stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return (0.0, -1)


def get_index(
    plugin_helps: Iterable[PluginHelp] = (),
    *,
    installed_plugins: set[str] | None = None,
) -> HelpIndex:
    """This process's index, rebuilt when the manual, the two docs or the
    plugin help files it was built from have changed."""
    helps = tuple(plugin_helps)
    key = (
        _stamp(MANUAL_PATH), _stamp(FAQ_PATH), _stamp(TROUBLESHOOTING_PATH),
        tuple((h.slug, hash(h.text)) for h in helps),
        tuple(sorted(installed_plugins)) if installed_plugins is not None else None,
    )
    with _LOCK:
        if _CACHE.get("key") == key and isinstance(_CACHE.get("index"), HelpIndex):
            return _CACHE["index"]  # type: ignore[return-value]
        index = build_index(installed_plugins=installed_plugins, plugin_helps=helps)
        _CACHE["key"] = key
        _CACHE["index"] = index
        return index


def reset_cache() -> None:
    with _LOCK:
        _CACHE.clear()
