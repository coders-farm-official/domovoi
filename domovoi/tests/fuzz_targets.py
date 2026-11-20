"""Fuzz targets: the parsers that read someone else's bytes, each wrapped as
one function of raw bytes that runs the parser and checks what it promises.

The same targets run two ways:

* under Hypothesis in the ordinary suite (``test_fuzz_parsers.py``): a few
  dozen inputs each, derandomized, so a run is quick and repeatable;
* under Atheris, coverage-guided, for minutes at a time
  (``scripts/dev/fuzz.py``, the ``fuzz`` job in .github/workflows/ci.yml).

A target returns quietly when the parser either answered or refused the
input the documented way (the module's own error type). Anything else is
a finding: an exception of another type, a broken promise (an
``AssertionError`` naming it), or a call that took longer than
:data:`SLOW_SEC`. Each target takes the first byte or two of its input to
choose a variant (a file name, a document kind), so a fuzzer reaches every
branch from one byte string.

Standard library and Domovoi only: no Hypothesis or Atheris import here.
"""

from __future__ import annotations

import atexit
import io
import json
import math
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: A single call that takes longer than this is a finding (a hang, or work
#: that grows faster than the input). Generous: CI runners are shared.
SLOW_SEC = float(os.environ.get("DOMOVOI_FUZZ_SLOW_SEC", "10"))


@dataclass(frozen=True)
class Target:
    name: str
    run: Callable[[bytes], None]
    #: Files whose bytes are good starting inputs (a fuzzer's seed corpus).
    seeds: Callable[[], list[Path]] = field(default=lambda: [])
    #: What the target does, for ``scripts/dev/fuzz.py --list``.
    about: str = ""
    #: Built starting inputs, lead byte included.
    samples: Callable[[], list[bytes]] = field(default=lambda: [])


class Timer:
    """``with Timer("what"):`` fails the call when it ran past :data:`SLOW_SEC`."""

    def __init__(self, what: str, limit: float | None = None) -> None:
        self.what = what
        self.limit = SLOW_SEC if limit is None else limit

    def __enter__(self) -> "Timer":
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        took = time.perf_counter() - self.t0
        if exc[0] is None:
            assert took <= self.limit, f"{self.what} took {took:.1f} s (limit {self.limit} s)"


def _scratch() -> Path:
    """One scratch folder per process for targets that need a real file."""
    global _SCRATCH
    if _SCRATCH is None:
        _SCRATCH = Path(tempfile.mkdtemp(prefix="domovoi-fuzz-"))
        atexit.register(shutil.rmtree, _SCRATCH, True)
    return _SCRATCH


_SCRATCH: Path | None = None


def _as_file(data: bytes, suffix: str) -> Path:
    p = _scratch() / f"input{suffix}"
    p.write_bytes(data)
    return p


def _pick(data: bytes, options: tuple) -> tuple:
    """(``options[first byte % n]``, the rest of ``data``)."""
    if not data:
        return options[0], b""
    return options[data[0] % len(options)], data[1:]


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _files(*globs: str) -> Callable[[], list[Path]]:
    def find() -> list[Path]:
        out: list[Path] = []
        for g in globs:
            out += sorted(p for p in FIXTURES.glob(g) if p.is_file())
        return out
    return find


def found(name: str) -> list[bytes]:
    """What the fuzzer found for target ``name`` (fixtures/fuzz/<name>-*.bin,
    lead byte included): kept as starting inputs, and replayed by the suite."""
    return [p.read_bytes() for p in sorted((FIXTURES / "fuzz").glob(f"{name}-*.bin"))]


# ─── Audio tags (domovoi/media_tags) ─────────────────────────────────────

_TAG_NAMES = ("", "a.mp3", "a.aac", "a.flac", "a.m4a", "a.ogg", "a.wav", "a.ape", "a.mp2", "a.adif")


def media_tags(data: bytes) -> None:
    """``media_tags.read_fileobj``: a MediaFile, None, or TagError; the
    length is a finite non-negative number; no more items than a read may
    make (``_util.MAX_ITEMS``)."""
    from domovoi import media_tags as mt
    from domovoi.media_tags import _util

    name, body = _pick(data, _TAG_NAMES)
    try:
        with Timer(f"media_tags.read_fileobj({name!r})"):
            f = mt.read_fileobj(io.BytesIO(body), name)
    except mt.TagError:
        return
    if f is None:
        return
    assert isinstance(f, mt.MediaFile)
    assert f.duration is None or (math.isfinite(f.duration) and f.duration >= 0), f.duration
    n_items = len(f.tags) if f.tags is not None else 0
    assert n_items <= _util.MAX_ITEMS, n_items
    assert len(f.pictures) <= _util.MAX_ITEMS
    assert sum(len(p.data) for p in f.pictures) <= _util.READ_BUDGET
    if f.tags is not None:
        for key, _value in f.tags:
            assert isinstance(key, str)


# ─── ICS calendar feeds (domovoi/calendar_ics.py) ─────────────────────────

ICS_WINDOW = (datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 3, 1, tzinfo=timezone.utc))
ICS_ZONE = timezone(timedelta(hours=-5))
ICS_MAX_EVENTS = 50


def ics_text(text: str, max_events: int = ICS_MAX_EVENTS):
    """``parse_ics`` on ``text`` in the fixed window and zone, with every
    promise checked. Returns the calendar, or None when refused."""
    from domovoi import calendar_ics, household_time

    lo, hi = ICS_WINDOW
    try:
        with household_time.use_zone(ICS_ZONE), Timer("calendar_ics.parse_ics"):
            cal = calendar_ics.parse_ics(text, window_start=lo, window_end=hi, max_events=max_events)
    except calendar_ics.IcsError:
        return None
    assert len(cal.events) <= max_events
    assert cal.name is None or 0 < len(cal.name) <= calendar_ics.MAX_TEXT
    keys = [(e.start, e.uid) for e in cal.events]
    assert keys == sorted(keys), "events are sorted by start"
    for e in cal.events:
        assert e.start.utcoffset() == timedelta(0), e.start
        assert e.end is None or e.end.utcoffset() == timedelta(0)
        assert e.start < hi and (e.end or e.start) >= lo, (e.start, e.end)
        assert e.end is None or e.end >= e.start
        assert 0 < len(e.title) <= calendar_ics.MAX_TEXT
        assert e.location is None or 0 < len(e.location) <= calendar_ics.MAX_TEXT
        assert len(e.uid) <= 300
    return cal


def ics(data: bytes) -> None:
    """``calendar_ics.parse_ics``: a calendar or IcsError; at most
    ``max_events`` events, sorted, in the window, aware UTC, every text
    cut to ``MAX_TEXT``. Odd first byte: the input gets a VCALENDAR head,
    so the fuzzer spends its time past the "is this a calendar" gate."""
    head, body = _pick(data, ("", "BEGIN:VCALENDAR\r\n"))
    ics_text(head + _text(body))


# ─── The APK signature check (domovoi/apk_signature.py) ──────────────────

def apk_signature(data: bytes) -> None:
    """``apk_signature.verify``: a Verified or ApkSignatureError; a
    verified signer is an RSA key of 2048-8192 bits that signed with v2."""
    from domovoi import apk_signature as sig

    path = _as_file(data, ".apk")
    try:
        with Timer("apk_signature.verify"):
            v = sig.verify(path)
    except sig.ApkSignatureError:
        return
    assert re.fullmatch(r"[0-9a-f]{64}", v.cert_sha256)
    assert sig.MIN_RSA_BITS <= v.key_bits <= sig.MAX_RSA_BITS
    assert 2 in v.schemes and set(v.schemes) <= {2, 3}


# ─── Chat documents (domovoi/doc_extract) ─────────────────────────────────

_DOC_KINDS = ("txt", "md", "csv", "html", "docx", "odt", "xlsx")


def documents(data: bytes) -> None:
    """``doc_extract.extract`` (what the chat's document worker runs) on
    every kind but PDF: a result or ``Refused`` with a sentence; the text
    kept stays within ``MAX_TEXT_CHARS``. PDF is left out: PDFium is native
    code, run only inside the worker's memory and time caps."""
    from domovoi import doc_extract

    _warm_documents()
    kind, body = _pick(data, _DOC_KINDS)
    path = _as_file(body, "." + kind)
    try:
        with Timer(f"doc_extract.extract({kind})"):
            result = doc_extract.extract(path, kind)
    except doc_extract.Refused as e:
        assert str(e).strip(), "a refusal is a sentence"
        return
    parts = result["parts"]
    assert parts, "a document with nothing in it is refused, not returned empty"
    assert result["chars"] <= doc_extract.MAX_TEXT_CHARS + 1000
    assert isinstance(result["words"], int) and result["words"] >= 0
    for p in parts:
        assert isinstance(p.get("text", ""), str)


_WARM = False


def _warm_documents() -> None:
    """Read each built sample once, untimed: the first .docx or .xlsx read
    imports its libraries, which on a cold disk can take seconds and is not
    the parser's time."""
    global _WARM
    if _WARM:
        return
    _WARM = True
    from domovoi import doc_extract

    for sample in _document_samples():
        kind, body = _pick(sample, _DOC_KINDS)
        try:
            doc_extract.extract(_as_file(body, "." + kind), kind)
        except doc_extract.Refused:
            pass


def zip_xml(data: bytes) -> None:
    """``doc_extract.safe``: the XML gate every .docx/.odt/.xlsx part goes
    through. ``parse_xml`` / ``check_member`` either refuse or accept, and
    nothing they accept declares a DOCTYPE or an entity."""
    from domovoi.doc_extract import safe

    with Timer("doc_extract.safe"):
        try:
            safe.parse_xml(data)
        except safe.Refused:
            pass
        try:
            safe.check_member("part.xml", data)
        except safe.Refused:
            return
    low = data.lower()
    for decl in (b"<!doctype", b"<!entity"):
        assert decl not in low, f"{decl!r} passed check_member"


# ─── Chat video intake (domovoi/chat_video) ──────────────────────────────

def chat_video(data: bytes) -> None:
    """The video intake's readers. ``parse_probe`` on ffprobe's JSON:
    a ProbeResult, or the ValueError/AttributeError ``probe()`` turns into
    a refusal; ``refusal`` names a known code or lets through only what
    ``limits`` allows. ``parse_scenes`` on ffmpeg's log: at most
    ``MAX_SCENE_ROWS`` finite rows. The speech windows of raw PCM: at most
    ``MAX_SPEECH_PIECES`` pieces, none longer than ``SPEECH_PIECE_SEC``."""
    from domovoi.chat_video import limits, media

    mode, body = _pick(data, ("probe", "scenes", "pcm", "token"))
    with Timer(f"chat_video {mode}"):
        if mode == "probe":
            try:
                doc = json.loads(_text(body))
            except (ValueError, RecursionError):
                return
            try:
                result = media.parse_probe(doc)
            except (ValueError, AttributeError):
                return
            why = media.refusal(result)
            if why is not None:
                assert why[0] in (limits.CODE_UNSUPPORTED, limits.CODE_UNREADABLE, limits.CODE_TOO_LONG), why
                assert why[1].strip()
                return
            assert 0 < result.duration <= limits.MAX_DURATION_SEC + limits.DURATION_SLACK_SEC
            assert result.video_codec in limits.VIDEO_CODECS
            assert set(result.container.split(",")) & limits.CONTAINERS
        elif mode == "scenes":
            rows = media.parse_scenes(_text(body))
            assert len(rows) <= limits.MAX_SCENE_ROWS
            assert all(math.isfinite(t) and math.isfinite(s) for t, s in rows)
        elif mode == "pcm":
            levels = media.window_levels(body)
            assert len(levels) == len(body) // 2 // media.WINDOW
            spans = media.speech_spans(levels)
            pieces = media.speech_pieces(spans, levels)
            assert len(pieces) <= limits.MAX_SPEECH_PIECES
            for a, b in pieces:
                assert 0 <= a < b, (a, b)
                assert b - a <= limits.SPEECH_PIECE_SEC + 0.02, (a, b)
                assert len(media.pcm_slice(body, a, b)) <= len(body)
        else:
            token = _text(body)
            if limits.valid_token(token):
                assert re.fullmatch(r"[0-9a-f]{32}", token)
                assert limits.meta_path(token).parent == limits.VIDEOS_DIR
            else:
                assert limits.video_path(token) is None


# ─── Uploaded file names (web/backend/api/files_security.py) ─────────────

def upload_paths(data: bytes) -> None:
    """``files_security.safe_join``, the containment every Files, Documents
    and music upload goes through, under both the POSIX and the Windows
    name rules: a 4xx refusal, or a path inside the library root that
    ``unstorable_reason`` accepts and that isn't a secret-shaped name."""
    from fastapi import HTTPException

    from web.backend.api import files_security as fs

    rules, body = _pick(data, (False, True))
    rel = _text(body)
    root = (_scratch() / "library").resolve()
    root.mkdir(exist_ok=True)
    saved = fs.WINDOWS_NAME_RULES
    fs.WINDOWS_NAME_RULES = rules
    try:
        with Timer("files_security.safe_join"):
            try:
                target = fs.safe_join(root, rel)
            except HTTPException as e:
                assert 400 <= e.status_code < 500, e.status_code
                return
            except (ValueError, OSError) as e:
                raise AssertionError(f"safe_join let {type(e).__name__} escape: {e}") from e
        assert target == root or root in target.parents, target
        if rel.strip():
            assert fs.unstorable_reason(rel) is None
    finally:
        fs.WINDOWS_NAME_RULES = saved


# ─── Chat drafts (domovoi/drafts) ────────────────────────────────────────

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
DIFF_MAX_CHARS = 600


def drafts(data: bytes) -> None:
    """A draft's markdown, as the blocks a .docx or PDF is drawn from:
    ``blocks.parse`` keeps no character XML can't hold and no empty line;
    ``strip_links`` is exactly the regex it replaced (on input short enough
    for the quadratic reference); the word diff between two halves of the
    input rebuilds both sides."""
    from domovoi.drafts import blocks, diff

    text = _text(data)
    with Timer("drafts.blocks.parse"):
        parsed = blocks.parse(text)
    for b in parsed:
        assert b.style in ("title", "heading", "strong", "body", "bullet"), b.style
        assert b.lines and all(line.strip() for line in b.lines)
        for line in b.lines:
            assert blocks.xml_safe(line) == line
            assert "\n" not in line and "\r" not in line
    assert blocks.xml_safe(blocks.xml_safe(text)) == blocks.xml_safe(text)
    if len(text) <= 4000:
        assert blocks.strip_links(text) == _LINK.sub(r"\1", text)
    # The diff only on short input: its cost on two unrelated drafts grows
    # with their product (the xfail in test_fuzz_parsers.py), and a fuzzer
    # would find that one known slowness over and over.
    if len(text) > DIFF_MAX_CHARS:
        return
    half = len(text) // 2
    old, new = text[:half], text[half:]
    with Timer("drafts.diff"):
        ops = diff.diff(old, new)
    assert "".join(t for op, t in ops if op != "+") == "".join(diff.tokens(old))
    assert "".join(t for op, t in ops if op != "-") == "".join(diff.tokens(new))


# ─── User text: the intent patterns ──────────────────────────────────────

def _fast_paths() -> Iterator[tuple[str, re.Pattern[str]]]:
    from domovoi.handlers import HANDLERS
    from domovoi.handlers.base import as_fast_path

    for h in HANDLERS:
        for entry in h.fast_paths:
            yield h.name, as_fast_path(entry).pattern


#: Chat accepts a message of up to 32,000 characters (web/backend/api/chat.py).
MAX_MESSAGE = 32000


def user_text_on(text: str) -> None:
    """Every regex user text meets before a model does, on ``text``."""
    from domovoi import router
    from domovoi.drafts import phrases

    text = text[:MAX_MESSAGE]
    norm = router.strip_leading_filler(router.normalize_transcript(text))
    for name, pattern in _fast_paths():
        with Timer(f"fast path of {name}: {pattern.pattern[:60]!r}", limit=min(SLOW_SEC, 2.0)):
            pattern.match(norm)
    with Timer("router.plan_route"):
        router.plan_route(text)
    with Timer("router.goes_to_the_tool_model"):
        router.goes_to_the_tool_model(text)
    with Timer("drafts.phrases"):
        ask = phrases.draft_request(text)
        phrases.revision_request(text, after_draft=True)
        phrases.print_request(text)
    if ask is not None:
        assert ask.kind in ("resume", "cover_letter", "letter")
        assert len(ask.topic) <= 60


def user_text(data: bytes) -> None:
    """The handlers' fast-path patterns, the router's own (``plan_route``,
    ``goes_to_the_tool_model``) and the chat's draft phrases on any text:
    no exception, and no pattern slower than 2 s on a chat-sized message.
    A first byte over 0xF0 repeats the rest up to a whole chat message,
    the shape that finds a pattern's backtracking."""
    if data and data[0] >= 0xF0 and len(data) > 1:
        unit = _text(data[1:])
        text = (unit * (MAX_MESSAGE // max(1, len(unit)) + 1))[:MAX_MESSAGE]
    else:
        text = _text(data)
    user_text_on(text)


def _phrase_seeds() -> list[Path]:
    return sorted((REPO / "domovoi" / "drafts").glob("shared_cases.json"))


ICS_SAMPLE = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:Family\r\nX-WR-TIMEZONE:America/New_York\r\n"
    "BEGIN:VTIMEZONE\r\nTZID:Eastern Standard Time\r\nBEGIN:STANDARD\r\nDTSTART:16010101T020000\r\n"
    "TZOFFSETFROM:-0400\r\nTZOFFSETTO:-0500\r\nRRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11\r\nEND:STANDARD\r\n"
    "END:VTIMEZONE\r\n"
    "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:Dinner\\, with Mom\r\nLOCATION:12 Main St\r\n"
    "DTSTART;TZID=Eastern Standard Time:20260105T180000\r\nDTEND;TZID=Eastern Standard Time:20260105T190000\r\n"
    "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=20\r\nEXDATE;TZID=Eastern Standard Time:20260107T180000\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:1\r\nRECURRENCE-ID;TZID=Eastern Standard Time:20260112T180000\r\n"
    "DTSTART;TZID=Eastern Standard Time:20260112T200000\r\nDURATION:PT1H\r\nSUMMARY:Moved\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:2\r\nDTSTART;VALUE=DATE:20260201\r\nRRULE:FREQ=MONTHLY;BYDAY=-1FR;UNTIL=20261231\r\n"
    "SUMMARY:Bins\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def _ics_samples() -> list[bytes]:
    return [b"\x00" + ICS_SAMPLE.encode()]


def _document_samples() -> list[bytes]:
    from domovoi.tests import doc_fixtures as fx

    out = [
        b"\x00" + b"The rent is $1,200.\n\nThe deposit is $1,500.\n",
        b"\x01" + b"# Lease\n\n## Rent\nDue on the first.\n\n```\ncode\n```\n",
        b"\x02" + b"item,cost\nbread,2.50\nmilk,1.20\n",
        b"\x03" + b"<html><body><h1>Lease</h1><p>Rent <b>due</b></p><table><tr><td>1</td></tr></table></body></html>",
        b"\x04" + fx.make_docx(),
        b"\x05" + fx.make_odt(),
    ]
    try:
        out.append(b"\x06" + fx.make_xlsx())
    except ImportError:  # openpyxl: the suite's lock has it
        pass
    return out


def _video_samples() -> list[bytes]:
    probe = {"format": {"duration": "12.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
             "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                         {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                         {"index": 2, "codec_type": "video", "codec_name": "mjpeg", "width": 300, "height": 300,
                          "disposition": {"attached_pic": 1}}]}
    scenes = "frame:0 pts:0 pts_time:0\nlavfi.scene_score=0.000\nframe:1 pts:3003 pts_time:1.5\nlavfi.scene_score=0.42\n"
    pcm = bytes(range(256)) * 40
    return [b"\x00" + json.dumps(probe).encode(), b"\x01" + scenes.encode(), b"\x02" + pcm,
            b"\x03" + b"0123456789abcdef0123456789abcdef"]


def _text_samples() -> list[bytes]:
    return [b"play some jazz in the kitchen", b"set a timer for ten minutes",
            b"write a cover letter for the warehouse job at Acme", b"is there a way to pause the music",
            b"remind me to call mom at 5 pm tomorrow", b"\xf0play play "]


TARGETS: dict[str, Target] = {t.name: t for t in (
    Target("media_tags", media_tags, _files("media_tags/*.mp3", "media_tags/*.flac", "media_tags/*.ogg",
                                            "media_tags/*.opus", "media_tags/*.oga", "media_tags/*.m4a",
                                            "media_tags/*.wma", "media_tags/*.wav", "media_tags/*.aiff",
                                            "media_tags/*.aac", "media_tags/*.ape"),
           "audio tag reader (ID3, FLAC, Ogg, MP4, ASF, RIFF, APE, AAC)"),
    Target("ics", ics, about="ICS calendar feed reader", samples=_ics_samples),
    Target("apk_signature", apk_signature, _files("android_app/*.apk"), "APK Signature Scheme v2/v3 check"),
    Target("documents", documents, about="chat document extraction (txt, md, csv, html, docx, odt, xlsx)",
           samples=_document_samples),
    Target("zip_xml", zip_xml, about="the XML gate of zipped documents (no DOCTYPE, no entities)",
           samples=lambda: [b'<?xml version="1.0"?><w:document xmlns:w="urn:w"><w:p>x</w:p></w:document>',
                            b'<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>']),
    Target("chat_video", chat_video, about="video intake: ffprobe JSON, ffmpeg scene log, PCM speech windows, tokens",
           samples=_video_samples),
    Target("upload_paths", upload_paths, about="upload path containment (safe_join), POSIX and Windows rules",
           samples=lambda: [b"\x00music/uploads/song.mp3", b"\x01Documents/tax.pdf:stash.md",
                            b"\x00../.ssh/id_rsa"]),
    Target("drafts", drafts, _phrase_seeds, "chat draft markdown blocks, link stripping, word diff"),
    Target("user_text", user_text, about="intent fast paths, router patterns and draft phrases on user text",
           samples=_text_samples),
)}
