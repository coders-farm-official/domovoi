"""The LRC lyrics format, and plain lyric text (contract §4, rules
[LD1]–[LD2], [P1]–[P16], [LM1], [F1]–[F6]).

An ``.lrc`` file is lyric lines with time tags in front::

    [ti:Lantern Song]
    [ar:The Example Band]
    [00:12.40]the lantern hums beside the river door
    [00:21.30][01:05.00]and every copper kettle sings at dawn

One line may carry several times (a chorus sung twice); ``[offset:N]``
shifts every time (a positive N shows the lyrics sooner); ``<mm:ss.xx>``
word tags inside a line (karaoke timing) are dropped; a time with no text
is a gap. Text that is not LRC at all (lyrics in a tag, a plain ``.lrc``)
is read as plain lines.

Pure functions, stdlib only, no state between calls: importable by both
processes, safe in a worker thread. Nothing here raises on any input, and
nothing logs (lyrics are never logged anywhere, [C3]).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

#: Bigger files are not read (they still block writing: sidecar [W4]).
MAX_LRC_BYTES = 1_048_576
#: Timed entries kept (the first ones in time order).
MAX_ENTRIES = 4000
#: One lyric line's text, after cleanup.
MAX_LINE_CHARS = 500
#: ``|[offset:]|`` is clamped to this.
MAX_OFFSET_MS = 600_000
#: ID tags kept (the first occurrence of each key).
MAX_TAGS = 32
#: A header value of a written file, in characters ([F2]).
MAX_HEADER_CHARS = 200

_LEAD_TAG_RE = re.compile(r"\s*\[([^\[\]]*)\]")
_TIME_RE = re.compile(r"([0-9]{1,3}):([0-9]{1,2})(?:[.:]([0-9]{1,3}))?")
_ID_TAG_RE = re.compile(r"([A-Za-z#][A-Za-z0-9_#]*)\s*:(.*)", re.DOTALL)
_WORD_TAG_RE = re.compile(r"<[0-9]{1,3}:[0-9]{1,2}(?:[.:][0-9]{1,3})?>")
_OFFSET_RE = re.compile(r"([+-]?)([0-9]+)")
# C0 control characters (a tab is turned into a space first) and lone
# surrogates, which no database or file encoding can hold.
_CONTROL_RE = re.compile("[\x00-\x1f\ud800-\udfff]")
# What a header value loses ([F2]): every C0 control character.
_HEADER_CONTROL_RE = re.compile("[\x00-\x1f\ud800-\udfff]")


@dataclass(frozen=True)
class ParsedLyrics:
    """What a piece of lyric text holds.

    ``synced``: ``(ms, text)`` pairs in time order with the offset applied,
    ``""`` text = a gap; None = not timed. ``plain``: the lyric lines joined
    by ``"\\n"`` (for timed lyrics: the non-empty texts in time order); None
    = no lyrics at all. ``tags``: the ID tags, lower-case keys.
    ``offset_ms``: the ``[offset:]`` applied (0 = none, or not timed)."""

    synced: tuple[tuple[int, str], ...] | None
    plain: str | None
    tags: Mapping[str, str]
    offset_ms: int

    @property
    def marked(self) -> bool:
        """Carries Domovoi's marker ``[re:Domovoi]`` ([LM1]). Necessary, not
        sufficient, for "a file Domovoi wrote" (the hash decides, §5.4)."""
        return self.tags.get("re", "").strip().lower() == "domovoi"

    @property
    def has_text(self) -> bool:
        return self.plain is not None

    def __repr__(self) -> str:  # never the lyrics, not even in a traceback ([C3])
        return (
            f"ParsedLyrics(synced={'None' if self.synced is None else f'{len(self.synced)} lines'}, "
            f"plain={'None' if self.plain is None else f'{len(self.plain)} chars'}, "
            f"tags={sorted(self.tags)}, offset_ms={self.offset_ms})"
        )


_NO_TAGS: Mapping[str, str] = MappingProxyType({})
EMPTY = ParsedLyrics(synced=None, plain=None, tags=_NO_TAGS, offset_ms=0)


# ─── Decoding ─────────────────────────────────────────────────────────────


def decode_lrc_bytes(data: bytes) -> str:
    """The text of a ``.lrc`` file ([LD1]): a UTF-8 BOM is dropped; a file
    starting with a UTF-16 BOM is UTF-16; otherwise strict UTF-8, else
    cp1252 (Windows editors' default), unknown bytes replaced. Never
    raises."""
    try:
        b = bytes(data)
        if b.startswith(b"\xef\xbb\xbf"):
            b = b[3:]
        if b.startswith((b"\xff\xfe", b"\xfe\xff")):
            return b.decode("utf-16", errors="replace")
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("cp1252", errors="replace")
    except Exception:  # noqa: BLE001 — [P11]-style: never raises
        return ""


def _lines_of(text: str) -> list[str]:
    """[LD2]: CRLF and lone CR become LF; a leading BOM character goes."""
    s = text if isinstance(text, str) else ""
    if s.startswith("﻿"):
        s = s[1:]
    return s.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _clean_line(s: str) -> str:
    """One line's text: C0 control characters out (a tab becomes a space),
    inline word tags out, whitespace collapsed to single spaces, stripped
    ([P5], [P13])."""
    s = s.replace("\t", " ")
    s = _CONTROL_RE.sub("", s)
    s = _WORD_TAG_RE.sub("", s)
    return " ".join(s.split())


def _time_ms(content: str) -> int | None:
    """A time tag's milliseconds ([P3]), or None when ``content`` is not a
    time tag. ``mm:ss``, ``mm:ss.f`` (tenths), ``mm:ss.ff`` (hundredths),
    ``mm:ss.fff`` (milliseconds); ``mm:ss:ff`` is the same with a colon."""
    m = _TIME_RE.fullmatch(content)
    if m is None:
        return None
    minutes, seconds, frac = int(m.group(1)), int(m.group(2)), m.group(3)
    if seconds >= 60:
        return None
    ms = (minutes * 60 + seconds) * 1000
    if frac:
        ms += int(frac) * (100, 10, 1)[len(frac) - 1]
    return ms


@dataclass(frozen=True)
class _Line:
    times: tuple[int, ...]           # leading time tags; () = untimed
    text: str                        # cleaned text (timed: after the time tags)
    id_tag: tuple[str, str] | None   # (key, value) when the line is an ID tag
    blank: bool                      # nothing at all on the line


def _scan(text: str) -> list[_Line]:
    """[P1]–[P5] for every line."""
    out: list[_Line] = []
    for raw in _lines_of(text):
        line = raw.strip()
        rest = line
        times: list[int] = []
        id_tag: tuple[str, str] | None = None
        while True:
            m = _LEAD_TAG_RE.match(rest)
            if m is None:
                break
            content = m.group(1).strip()
            t = _time_ms(content)
            if t is None:
                # [P4]: a single leading tag that is no time tag, shaped like
                # "key:value", with nothing after it, is an ID tag. Anything
                # else bracketed is ordinary text ("[Chorus]", "[00:75.00]").
                if not times:
                    im = _ID_TAG_RE.fullmatch(content)
                    if im is not None and not rest[m.end():].strip():
                        id_tag = (im.group(1).lower(), im.group(2).strip())
                break
            times.append(t)
            rest = rest[m.end():]
        if times:
            body = _clean_line(rest)[:MAX_LINE_CHARS].strip()
            out.append(_Line(tuple(times), body, None, False))
        elif id_tag is not None:
            out.append(_Line((), "", id_tag, False))
        else:
            out.append(_Line((), _clean_line(line), None, not line))
    return out


def _offset_ms(raw: str | None) -> int:
    """[P6]: an optional sign and digits; anything else is 0; clamped."""
    if raw is None:
        return 0
    m = _OFFSET_RE.fullmatch(raw.strip())
    if m is None:
        return 0
    digits = m.group(2)
    n = MAX_OFFSET_MS if len(digits) > 9 else min(int(digits), MAX_OFFSET_MS)
    return -n if m.group(1) == "-" else n


def _plain_from(lines: Sequence[str]) -> str | None:
    """[P14]/[P15]: leading and trailing empty lines dropped, a run of
    empty lines kept as ONE (a stanza break); nothing left → None."""
    out: list[str] = []
    for s in lines:
        if not s:
            if out and out[-1]:
                out.append("")
            continue
        out.append(s)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) if out else None


# ─── Parsing ──────────────────────────────────────────────────────────────


def _parse_lrc(text: str) -> ParsedLyrics:
    lines = _scan(text)
    tags: dict[str, str] = {}
    for ln in lines:
        if ln.id_tag is not None:
            key, value = ln.id_tag
            if key not in tags and len(tags) < MAX_TAGS:
                tags[key] = value
    offset = _offset_ms(tags.get("offset"))
    entries = [(max(0, t - offset), ln.text) for ln in lines for t in ln.times]
    entries.sort(key=lambda e: e[0])          # stable: file order breaks ties
    entries = entries[:MAX_ENTRIES]
    frozen_tags = MappingProxyType(tags)
    if any(body for _, body in entries):
        plain = "\n".join(body for _, body in entries if body)
        return ParsedLyrics(tuple(entries), plain, frozen_tags, offset)
    # [P9]: no timed text — the untimed lines are the lyrics.
    untimed = [ln.text for ln in lines if not ln.times and ln.id_tag is None]
    return ParsedLyrics(None, _plain_from(untimed), frozen_tags, 0)


def parse_lrc(text: str) -> ParsedLyrics:
    """Read LRC text ([P1]–[P12]). Never raises."""
    try:
        return _parse_lrc(text)
    except Exception:  # noqa: BLE001 — [P11]
        return EMPTY


def parse_plain(text: str) -> ParsedLyrics:
    """Read lyric text that is not LRC ([P13]–[P15]): word tags and
    control characters out, whitespace collapsed, empty lines at the ends
    dropped and runs of them kept as one. Never raises."""
    try:
        return ParsedLyrics(None, _plain_from([_clean_line(s) for s in _lines_of(text)]),
                            _NO_TAGS, 0)
    except Exception:  # noqa: BLE001
        return EMPTY


def _parse_lrc_untimed(text: str) -> ParsedLyrics:
    lines = _scan(text)
    tags: dict[str, str] = {}
    for ln in lines:
        if ln.id_tag is not None:
            key, value = ln.id_tag
            if key not in tags and len(tags) < MAX_TAGS:
                tags[key] = value
    # A timed line's text is already what follows its time tags.
    texts = [ln.text for ln in lines if ln.id_tag is None]
    return ParsedLyrics(None, _plain_from(texts), MappingProxyType(tags), 0)


def parse_lrc_untimed(text: str) -> ParsedLyrics:
    """A ``.lrc`` file that does not look like LRC ([P16]: too few timed
    lines), read as plain lyrics the way [P9] reads a file with no timed
    text: its ID tags (``[ti:…]``, ``[ar:…]``, ``[offset:…]``) are never
    lyric lines ([P10]), and a line's leading time tags are dropped, so a
    stray ``[00:12.40]`` never shows as words. ``synced`` is None; every
    other rule is :func:`parse_plain`'s. Never raises.

    For ``.lrc`` files only: lyrics inside an audio file's tags that are
    not LRC go through :func:`parse_plain`, untouched."""
    try:
        return _parse_lrc_untimed(text)
    except Exception:  # noqa: BLE001
        return EMPTY


def clean_line(text: str) -> str:
    """One timed line's text as :func:`parse_lrc` leaves it: newlines and
    tabs as spaces, other control characters and word tags out, whitespace
    collapsed, at most :data:`MAX_LINE_CHARS`."""
    s = str(text).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return _clean_line(s)[:MAX_LINE_CHARS].strip()


def from_entries(entries: Sequence[tuple[int, str]]) -> ParsedLyrics:
    """Timed lyrics from raw ``(ms, text)`` pairs — an ID3 SYLT frame, a
    stored row: texts cleaned like an LRC line, times clamped at 0, sorted
    by time (stably), the first :data:`MAX_ENTRIES` kept, ``plain`` the
    non-empty texts. No text in any kept entry → :data:`EMPTY`. Never
    raises (a malformed pair is skipped)."""
    out: list[tuple[int, str]] = []
    try:
        for pair in entries:
            try:
                ms, body = pair
                out.append((max(0, int(ms)), clean_line(body)))
            except (TypeError, ValueError, OverflowError):
                continue
        out.sort(key=lambda e: e[0])
        out = out[:MAX_ENTRIES]
    except Exception:  # noqa: BLE001
        return EMPTY
    if not any(body for _, body in out):
        return EMPTY
    return ParsedLyrics(tuple(out), "\n".join(body for _, body in out if body), _NO_TAGS, 0)


def looks_like_lrc(text: str) -> bool:
    """[P16]: at least three time-tagged lines with text, or at least one
    and they are at least half of the lines that are neither empty nor an
    ID tag."""
    try:
        lines = _scan(text)
    except Exception:  # noqa: BLE001
        return False
    timed = sum(1 for ln in lines if ln.times and ln.text)
    counted = sum(1 for ln in lines if ln.id_tag is None and (ln.times or not ln.blank))
    return timed >= 3 or (timed >= 1 and 2 * timed >= counted)


def parse_lyrics_text(text: str) -> ParsedLyrics:
    """LRC when it looks like LRC, plain text otherwise."""
    return parse_lrc(text) if looks_like_lrc(text) else parse_plain(text)


# ─── Writing ──────────────────────────────────────────────────────────────


def _header_value(value: object) -> str:
    """[F2]: control characters out, square brackets as round ones,
    stripped, at most 200 characters."""
    s = "" if value is None else str(value)
    s = _HEADER_CONTROL_RE.sub("", s).replace("[", "(").replace("]", ")").strip()
    return s[:MAX_HEADER_CHARS].strip()


def _stamp(ms: int) -> str:
    """[F3]: ``[mm:ss.xx]``, rounded to the nearest hundredth."""
    cs = (max(0, int(ms)) + 5) // 10
    return f"[{cs // 6000:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}]"


def format_lrc(
    entries: Sequence[tuple[int, str]],
    *,
    title: str,
    artist: str,
    album: str | None,
    duration_sec: int | None,
    version: str,
) -> str:
    """The ``.lrc`` Domovoi writes for LRCLIB's timed lyrics ([F1]–[F5]):
    the marker header (``[re:Domovoi]`` ``[by:LRCLIB]`` ``[ve:…]``
    ``[ti:…]`` ``[ar:…]`` and, when known, ``[al:…]`` ``[length:mm:ss]``),
    then one ``[mm:ss.xx]text`` line per entry. ``\\n`` line endings and a
    final newline; the caller encodes it as UTF-8 without a BOM.
    :func:`parse_lrc` reads it back as the same entries (times rounded to
    10 ms), marked."""
    head = [
        "[re:Domovoi]",
        "[by:LRCLIB]",
        f"[ve:{_header_value(version)}]",
        f"[ti:{_header_value(title)}]",
        f"[ar:{_header_value(artist)}]",
    ]
    album_s = _header_value(album)
    if album_s:
        head.append(f"[al:{album_s}]")
    if duration_sec is not None and int(duration_sec) > 0:
        d = int(duration_sec)
        head.append(f"[length:{d // 60:02d}:{d % 60:02d}]")
    body = [_stamp(ms) + _clean_line(str(text))[:MAX_LINE_CHARS].strip() for ms, text in entries]
    return "\n".join(head + body) + "\n"


__all__ = [
    "EMPTY",
    "MAX_ENTRIES",
    "MAX_LINE_CHARS",
    "MAX_LRC_BYTES",
    "MAX_OFFSET_MS",
    "MAX_TAGS",
    "ParsedLyrics",
    "clean_line",
    "decode_lrc_bytes",
    "format_lrc",
    "from_entries",
    "looks_like_lrc",
    "parse_lrc",
    "parse_lrc_untimed",
    "parse_lyrics_text",
    "parse_plain",
]
