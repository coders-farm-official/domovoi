"""Property tests of the parsers that read outside input
(domovoi/tests/fuzz_targets.py), under Hypothesis.

Each target gets random bytes, mutations of real files from the fixtures
(audio files, signed APKs), and structured inputs built to reach deep
(calendars, tags, user text shaped to find backtracking). What must hold:
the parser answers or refuses the documented way, keeps to its caps, never
takes longer than ``fuzz_targets.SLOW_SEC``, and round-trips where that
means something (an ICS summary, a Vorbis comment, an ID3 title, the word
diff).

Quick by default: a few dozen examples per test, derandomized, so the
suite stays fast and a red run repeats. ``DOMOVOI_FUZZ_EXAMPLES=2000``
(and ``DOMOVOI_FUZZ_RANDOM=1`` for fresh inputs each run) digs further;
``scripts/dev/fuzz.py`` runs the same targets under Atheris, coverage-
guided (docs/CONTRIBUTING.md, "Fuzz and property tests").

No database, no network.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

try:
    from hypothesis import HealthCheck, example, given, settings
    from hypothesis import strategies as st
except ImportError:  # pragma: no cover - the dev extra and CI install it
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise  # never a quiet skip in CI
    pytest.skip("hypothesis is not installed (pip install -e '.[dev]')", allow_module_level=True)

from domovoi.tests import fuzz_targets as ft

EXAMPLES = int(os.environ.get("DOMOVOI_FUZZ_EXAMPLES", "40"))

settings.register_profile(
    "domovoi-quick",
    max_examples=EXAMPLES,
    deadline=None,  # fuzz_targets.Timer bounds each call instead, more generously
    derandomize=os.environ.get("DOMOVOI_FUZZ_RANDOM") != "1",
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large,
                           HealthCheck.function_scoped_fixture],
    print_blob=True,
)
QUICK = settings.get_profile("domovoi-quick")


def _mutated(files: list[Path]):
    """A fixture file with a handful of bytes overwritten, cut or repeated."""
    blobs = [p.read_bytes() for p in files]
    assert blobs, "no seed files found"

    @st.composite
    def strat(draw):
        data = bytearray(draw(st.sampled_from(blobs)))
        for _ in range(draw(st.integers(0, 6))):
            if not data:
                break
            at = draw(st.integers(0, len(data) - 1))
            how = draw(st.sampled_from(("set", "cut", "dup")))
            if how == "set":
                data[at] = draw(st.integers(0, 255))
            elif how == "cut":
                del data[at:at + draw(st.integers(1, 64))]
            else:
                data[at:at] = data[at:at + draw(st.integers(1, 64))]
        return bytes(data)
    return strat()


# ─── Every target on arbitrary bytes ─────────────────────────────────────

@pytest.mark.parametrize("name", sorted(ft.TARGETS))
@QUICK
@given(data=st.binary(max_size=2048))
def test_any_bytes(name: str, data: bytes) -> None:
    ft.TARGETS[name].run(data)


@pytest.mark.parametrize("name", sorted(n for n, t in ft.TARGETS.items() if t.seeds()))
def test_the_seed_files_themselves(name: str) -> None:
    t = ft.TARGETS[name]
    for p in t.seeds():
        for lead in range(4):
            t.run(bytes([lead]) + p.read_bytes())


@pytest.mark.parametrize("name", sorted(n for n, t in ft.TARGETS.items() if t.samples()))
def test_the_built_samples_themselves(name: str) -> None:
    t = ft.TARGETS[name]
    for data in t.samples():
        t.run(data)


@pytest.mark.parametrize("name", sorted(n for n, t in ft.TARGETS.items() if t.samples()))
@QUICK
@given(data=st.data())
def test_damaged_samples(name: str, data) -> None:
    t = ft.TARGETS[name]
    blob = bytearray(data.draw(st.sampled_from(t.samples())))
    for _ in range(data.draw(st.integers(1, 6))):
        if len(blob) < 2:
            break
        at = data.draw(st.integers(1, len(blob) - 1))  # the lead byte stays: same variant
        blob[at] = data.draw(st.integers(0, 255))
    t.run(bytes(blob))


# ─── Real files, damaged ─────────────────────────────────────────────────

@QUICK
@given(data=_mutated(ft.TARGETS["media_tags"].seeds()), lead=st.integers(0, 255))
def test_damaged_audio_files(data: bytes, lead: int) -> None:
    ft.media_tags(bytes([lead]) + data)


APK_SEEDS = ft.TARGETS["apk_signature"].seeds()
RELEASE_APK = next(p for p in APK_SEEDS if p.name == "release-arm64-v8a.apk")


@QUICK
@given(data=_mutated([RELEASE_APK]))
def test_a_damaged_apk_never_verifies_as_someone_else(data: bytes) -> None:
    """Whatever is changed, the check either refuses or still names the
    release key: no edit to a signed APK makes it vouch for another key."""
    from domovoi import apk_signature as sig

    want = sig.verify(RELEASE_APK).cert_sha256
    ft.apk_signature(data)
    try:
        got = sig.verify(ft._as_file(data, ".apk")).cert_sha256
    except sig.ApkSignatureError:
        return
    assert got == want


# ─── Calendars ───────────────────────────────────────────────────────────

def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 folding: lines of 75 characters, continued after CRLF + space."""
    parts = [line[i:i + 75] for i in range(0, len(line), 75)] or [""]
    return "\r\n ".join(parts)


_STAMP = st.datetimes(min_value=__import__("datetime").datetime(2025, 6, 1),
                      max_value=__import__("datetime").datetime(2026, 9, 1))
_RRULE = st.lists(st.sampled_from([
    "FREQ=DAILY", "FREQ=WEEKLY", "FREQ=MONTHLY", "FREQ=YEARLY", "FREQ=HOURLY", "INTERVAL=0", "INTERVAL=3",
    "COUNT=5", "COUNT=999999", "UNTIL=20260201T000000Z", "UNTIL=20260201", "BYDAY=MO,WE", "BYDAY=-1FR",
    "BYDAY=2TU", "BYDAY=53SU", "BYMONTHDAY=31", "BYMONTHDAY=-1", "BYMONTHDAY=0", "BYMONTH=2", "BYMONTH=13",
    "BYSETPOS=-1", "BYSETPOS=400", "WKST=SU", "BYYEARDAY=100", "BYWEEKNO=3", "FREQ=", "=", "COUNT=x",
]), max_size=6).map(";".join)


@st.composite
def _calendar(draw):
    events = []
    for i in range(draw(st.integers(0, 6))):
        start = draw(_STAMP)
        all_day = draw(st.booleans())
        dt = start.strftime("%Y%m%d") if all_day else start.strftime("%Y%m%dT%H%M%S") + draw(st.sampled_from(["Z", ""]))
        lines = [f"UID:{draw(st.sampled_from(['a', 'b', str(i)]))}",
                 f"DTSTART{';VALUE=DATE' if all_day else draw(st.sampled_from(['', ';TZID=Eastern Standard Time', ';TZID=Mars/Base']))}:{dt}",
                 f"SUMMARY:{_escape(draw(st.text(max_size=80)))}"]
        if draw(st.booleans()):
            lines.append(f"RRULE:{draw(_RRULE)}")
        if draw(st.booleans()):
            lines.append(f"DURATION:{draw(st.sampled_from(['PT1H', 'P1D', '-PT1H', 'P', 'PT99999999H']))}")
        if draw(st.booleans()):
            lines.append(f"EXDATE:{dt}")
        if draw(st.booleans()):
            lines.append(f"RECURRENCE-ID:{dt}")
        if draw(st.booleans()):
            lines.append("STATUS:CANCELLED")
        events.append("BEGIN:VEVENT\r\n" + "\r\n".join(_fold(ln) for ln in lines) + "\r\nEND:VEVENT\r\n")
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"


@QUICK
@given(text=_calendar(), max_events=st.integers(1, 60))
def test_generated_calendars(text: str, max_events: int) -> None:
    ft.ics_text(text, max_events=max_events)


@QUICK
@given(title=st.text(min_size=1, max_size=400).filter(lambda s: "\r" not in s))
def test_an_event_title_round_trips(title: str) -> None:
    """Escaped and folded as a feed writes it, a SUMMARY reads back as the
    same words (spaces collapsed, cut at ``MAX_TEXT``)."""
    from domovoi import calendar_ics

    text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260110T100000Z\r\n"
            + _fold("SUMMARY:" + _escape(title)) + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    cal = ft.ics_text(text)
    want = " ".join(title.split())[:calendar_ics.MAX_TEXT] or "(no title)"
    assert [e.title for e in cal.events] == [want]


# ─── Tags, built ─────────────────────────────────────────────────────────

_KEY = st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7D, blacklist_characters="="),
               min_size=1, max_size=20)


@QUICK
@given(vendor=st.text(max_size=30), items=st.lists(st.tuples(_KEY, st.text(max_size=60)), max_size=10))
def test_a_vorbis_comment_round_trips(vendor: str, items: list[tuple[str, str]]) -> None:
    from domovoi.media_tags import parse_vorbis_comment

    v = vendor.encode("utf-8", "surrogatepass")
    body = struct.pack("<I", len(v)) + v + struct.pack("<I", len(items))
    for k, val in items:
        raw = k.encode("ascii") + b"=" + val.encode("utf-8", "surrogatepass")
        body += struct.pack("<I", len(raw)) + raw
    tags = parse_vorbis_comment(body)
    decoded = [(k, val.encode("utf-8", "surrogatepass").decode("utf-8", "replace")) for k, val in items]
    assert list(tags) == decoded


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


@QUICK
@given(title=st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
                     min_size=1, max_size=200).filter(lambda s: s.strip()))
def test_an_id3v24_title_round_trips(title: str) -> None:
    """A UTF-8 TIT2 frame in an ID3v2.4 tag reads back as the same text."""
    import io

    from domovoi import media_tags

    body = b"\x03" + title.encode("utf-8")
    frame = b"TIT2" + _syncsafe(len(body)) + b"\x00\x00" + body
    tag = b"ID3\x04\x00\x00" + _syncsafe(len(frame)) + frame
    f = media_tags.read_fileobj(io.BytesIO(tag), "song.mp3")
    assert f is not None and f.tags is not None
    assert f.tags.first("TIT2") == title.strip()


# ─── Drafts and user text ────────────────────────────────────────────────

_MARKDOWN = st.lists(st.sampled_from([
    "# ", "## ", "- ", "* ", "• ", "**", "__", "*", "_", "`", "```", "---", "[", "]", "(", ")", "\\",
    "\n", "\r\n", "\n\n", " ", "\t", "word", "Dear Sam,", "\x00", "\ufffe", "\ud800",
]), max_size=60).map("".join)


@QUICK
@given(text=_MARKDOWN)
def test_draft_markdown(text: str) -> None:
    ft.drafts(text.encode("utf-8", "surrogatepass"))


@QUICK
@given(old=_MARKDOWN, new=_MARKDOWN)
def test_the_draft_diff_rebuilds_both_sides(old: str, new: str) -> None:
    from domovoi.drafts import diff

    a, b = diff.display_text(old), diff.display_text(new)
    ops = diff.diff(a, b)
    assert "".join(t for op, t in ops if op != "+") == "".join(diff.tokens(a))
    assert "".join(t for op, t in ops if op != "-") == "".join(diff.tokens(b))
    assert diff.change_line(old, new)


def _diff_cost(old: str, new: str) -> tuple[list[list[str]], float, int]:
    """The diff, its seconds (untraced) and its peak bytes (traced)."""
    import time
    import tracemalloc

    from domovoi.drafts import diff

    t = time.perf_counter()
    ops = diff.diff(old, new)
    took = time.perf_counter() - t
    tracemalloc.start()
    try:
        diff.diff(old, new)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return ops, took, peak


def _unrelated(words: int) -> tuple[str, str]:
    return " ".join(f"a{i}" for i in range(words)), " ".join(f"b{i}" for i in range(words))


def test_the_draft_diff_memory_is_bounded_for_unrelated_drafts() -> None:
    """Was a fuzz finding: Myers kept a copy of its whole V array per edit
    step, so two unrelated drafts cost O((N+M)*D): 200 unrelated words a
    side peaked at 5.8 MiB, 2,000 words took 1.6 s / 630 MB and 4,000
    words 6.9 s / 2.4 GB. Each step now keeps only its 2d+3 diagonals and
    the work stops at WORK_BUDGET (deterministic: memory, not time)."""
    ops, _, peak = _diff_cost(*_unrelated(200))
    assert len(ops) > 4  # still compared word by word
    assert peak < 2 * 2 ** 20, f"diff of two unrelated 200-word drafts peaked at {peak // 2 ** 20} MiB"


@pytest.mark.parametrize("shape", ["unrelated 4,000 words", "unrelated at the token cap",
                                   "200,000 characters", "one word repeated", "two words at random"])
def test_the_draft_diff_is_bounded_up_to_the_real_caps(shape: str) -> None:
    """The worst a draft can ask of the diff: a hand edit is up to 200,000
    characters (chat_drafts.MAX_BODY_CHARS) and the diff compares word by
    word up to MAX_TOKENS. Measured after the fix: under 0.1 s and 7 MiB
    for each (the limits here leave room for a slow CI runner); before it,
    the unrelated cases took seconds to minutes and gigabytes. Past the
    budget the diff keeps what both sides start and end with and shows the
    stretch between as removed then added, and it still rebuilds both."""
    import random

    from domovoi.drafts import diff

    rnd = random.Random(7)
    old, new = {
        "unrelated 4,000 words": _unrelated(4000),
        "unrelated at the token cap": _unrelated(diff.MAX_TOKENS // 4),
        "200,000 characters": ("Dear Sam, " + "a " * 99_990, "Dear Sam, " + "b " * 99_990),
        "one word repeated": (" ".join(["x"] * 9_990), " ".join(["x"] * 10_000)),
        "two words at random": (" ".join(rnd.choice("xy") for _ in range(10_000)),
                                " ".join(rnd.choice("xy") for _ in range(10_000))),
    }[shape]
    ops, took, peak = _diff_cost(old, new)
    assert "".join(t for op, t in ops if op != "+") == "".join(diff.tokens(old))
    assert "".join(t for op, t in ops if op != "-") == "".join(diff.tokens(new))
    assert peak < 16 * 2 ** 20, f"{shape}: peaked at {peak // 2 ** 20} MiB"
    assert took < 2.0, f"{shape}: took {took:.2f} s"


def test_the_draft_diff_past_its_budget_shows_one_replaced_stretch() -> None:
    """The plain view, with the ops the clients already read (the iPhone app
    drops any step that isn't an [op, text] pair): what both versions start
    and end with kept, one removed block, one added block."""
    from domovoi.drafts import diff

    a, b = _unrelated(1000)
    old, new = "# Notes\n\n" + a + "\n\nThanks, Sam", "# Notes\n\n" + b + "\n\nThanks, Sam"
    assert diff.diff(old, new) == [["=", "# Notes\n\n"], ["-", a], ["+", b], ["=", "\n\nThanks, Sam"]]
    assert diff.diff("same", "same") == [["=", "same"]]


#: Pieces of the phrases the patterns look for, so that generated text
#: reaches past the first character of a pattern and into its repeats.
_WORDS = st.sampled_from([
    "play", "the", "music", "in", "kitchen", "set", "a", "timer", "for", "minutes", "remind", "me", "to",
    "what", "is", "time", "and", "by", "on", "of", "please", "can", "you", "turn", "volume", "up", "down",
    "write", "draft", "letter", "my", "resume", "cover", "print", "it", "make", "shorter", "add", "list",
    "is there a way to", "next", "song", "1", "2", "10", "%", ".", ",", "?", "!", " ", "  ", "'s", "-", "\n",
])


@QUICK
@given(words=st.lists(_WORDS, max_size=40), repeat=st.integers(1, 800))
def test_user_text_shaped_like_commands(words: list[str], repeat: int) -> None:
    """Command-shaped text, repeated up to a chat-sized message: every
    pattern answers within the limit (a backtracking pattern would not)."""
    unit = " ".join(words)
    ft.user_text_on((unit + " ") * repeat)


@pytest.mark.parametrize("text", [
    "play " * 6400, "a" * 32000, "set a timer for " + "1" * 31000, "is there a way to " * 1700,
    "remind me to " * 2400, "write a letter " * 2000, "[" * 32000, " " * 32000 + "x",
], ids=lambda t: f"{t[:16].strip()!r}x{len(t)}")
def test_long_user_text(text: str) -> None:
    ft.user_text_on(text)


@QUICK
@given(rel=st.text(max_size=80), windows=st.booleans())
@example(rel="../x", windows=False)
@example(rel="a/../../x", windows=False)
@example(rel="C:/Windows", windows=True)
@example(rel="//host/share", windows=False)
@example(rel=".env:x", windows=True)
@example(rel="notes.txt.", windows=True)
@example(rel="a\x00b", windows=False)
def test_upload_paths_stay_inside_the_root(rel: str, windows: bool) -> None:
    ft.upload_paths(bytes([int(windows)]) + rel.encode("utf-8", "surrogatepass"))


# ─── What fuzzing found (2026-11), kept as plain regression tests ─────────

#: Inputs Atheris found, as it saved them (lead byte included):
#: ``<target>-<what>.bin``. Each must now pass its target.
FOUND = sorted((ft.FIXTURES / "fuzz").glob("*.bin"))


@pytest.mark.parametrize("path", FOUND, ids=[p.stem for p in FOUND])
def test_what_the_fuzzer_found_passes_now(path: Path) -> None:
    ft.TARGETS[path.stem.split("-", 1)[0]].run(path.read_bytes())


def test_a_negative_ogg_granule_is_no_length() -> None:
    """A last page with a granule of -2**62 gave a negative duration."""
    import io

    from domovoi import media_tags

    data = (ft.FIXTURES / "fuzz" / "media_tags-ogg-negative-granule.bin").read_bytes()[1:]
    f = media_tags.read_fileobj(io.BytesIO(data), "")
    assert f is not None and (f.duration is None or f.duration >= 0)

def test_an_aiff_sample_rate_past_a_double_is_no_length_not_a_crash() -> None:
    """COMM's 80-bit sample rate with a huge exponent raised OverflowError
    out of media_tags.read (callers catch TagError and OSError only)."""
    import io

    from domovoi import media_tags

    comm = struct.pack(">hIh", 1, 1000, 16) + bytes([0x7F, 0xFE]) + b"\x80" + b"\x00" * 7
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm
    f = media_tags.read_fileobj(io.BytesIO(b"FORM" + struct.pack(">I", len(body)) + body), "a.aiff")
    assert f is not None and f.kind == "aiff" and f.duration is None


def test_a_file_that_is_only_the_start_of_an_ape_header_is_a_tag_error() -> None:
    """Atheris's first find: 19 bytes starting APETAGEX raised struct.error."""
    import io

    from domovoi import media_tags

    with pytest.raises(media_tags.TagError):
        media_tags.read_fileobj(io.BytesIO(b"APETAGEX" + b"\x0e" * 10 + b"\x00"), "")


def test_a_probe_with_an_infinite_size_is_read_not_raised() -> None:
    """ffprobe JSON with 1e400 (inf) for a width raised OverflowError, which
    probe() doesn't turn into a refusal."""
    import json

    from domovoi.chat_video import media

    doc = json.loads('{"format":{"duration":"10","format_name":"mov,mp4"},'
                     '"streams":[{"codec_type":"video","codec_name":"h264","width":1e400,"height":1e400}]}')
    r = media.parse_probe(doc)
    assert r.width is None and r.sizes == ((0, 0),)


def test_a_probe_whose_streams_are_not_a_list_reads_as_no_streams() -> None:
    """A fuzzer-found ffprobe answer with ``"streams": 1`` raised TypeError."""
    from domovoi.chat_video import media

    for streams in (1, "x", {"a": 1}, [1, "x", None]):
        r = media.parse_probe({"format": {"duration": "12.5"}, "streams": streams})
        assert r.video_codec is None and r.sizes == ()
    r = media.parse_probe({"format": 3, "streams": [{"codec_type": "video", "disposition": 7}]})
    assert r.duration == 0.0 and r.sizes == ((0, 0),)


def test_a_scene_log_with_a_malformed_number_is_skipped_not_raised() -> None:
    from domovoi.chat_video import media

    log = "pts_time:1.2.3\nlavfi.scene_score=0.5.5\npts_time:.\nlavfi.scene_score=.\npts_time:4\nlavfi.scene_score=.25\n"
    assert media.parse_scenes(log) == [(1.2, 0.5), (4.0, 0.25)]


@pytest.mark.parametrize("event", [
    "DTSTART:20250601T000000Z\r\nDURATION:PT99999999H",           # end past year 9999
    "DTSTART:20260110T100000Z\r\nDURATION:P99999999999999999999W",  # past timedelta itself
    "DTSTART:99991231T233000Z\r\nDURATION:PT1H",
    "DTSTART;VALUE=DATE:99991231\r\nDTEND;VALUE=DATE:00010101",
    "DTSTART;TZID=Eastern Standard Time:99991231T233000",
], ids=["huge-duration", "duration-past-timedelta", "year-9999", "all-day-9999-to-1", "vtimezone-9999"])
def test_a_calendar_date_at_the_ends_of_time_is_no_crash(event: str) -> None:
    """These raised OverflowError / ValueError out of parse_ics, failing the
    whole feed's refresh for one odd event."""
    text = ft.ICS_SAMPLE.replace("END:VCALENDAR\r\n", f"BEGIN:VEVENT\r\nUID:odd\r\n{event}\r\nSUMMARY:Odd\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    cal = ft.ics_text(text)
    assert cal is not None and any(e.title == "Dinner, with Mom" for e in cal.events)


def test_a_believable_long_event_keeps_its_end() -> None:
    from domovoi import calendar_ics

    text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260110T100000Z\r\nDURATION:P30D\r\n"
            "SUMMARY:Trip\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    (e,) = ft.ics_text(text).events
    assert e.end - e.start == __import__("datetime").timedelta(days=30) <= calendar_ics.MAX_SPAN


def test_a_document_with_a_damaged_deflate_stream_is_refused(tmp_path) -> None:
    """zlib.error escaped SafeZip.read: a refusal now, like every other
    damaged part."""
    import zipfile

    from domovoi.doc_extract import safe

    p = tmp_path / "d.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document>" + "x" * 4000 + "</w:document>")
    data = bytearray(p.read_bytes())
    start = data.index(b"word/document.xml") + len("word/document.xml")
    data[start + 2:start + 12] = b"\xff" * 10   # the deflate stream, not the headers
    p.write_bytes(bytes(data))
    with safe.SafeZip(p) as z, pytest.raises(safe.Refused):
        z.read("word/document.xml")


def test_a_zip_of_a_version_python_does_not_read_is_refused(tmp_path) -> None:
    """NotImplementedError ("zip file version 10.2") escaped SafeZip()."""
    import zipfile

    from domovoi.doc_extract import safe

    p = tmp_path / "d.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        z.writestr("word/document.xml", "<w:document/>")
    data = bytearray(p.read_bytes())
    cd = data.index(b"PK\x01\x02")
    data[cd + 6:cd + 8] = (102).to_bytes(2, "little")  # "version needed to extract": 10.2
    p.write_bytes(bytes(data))
    with pytest.raises(safe.Refused):
        safe.SafeZip(p)
