"""The LRC format and plain lyric text (domovoi/lyrics/lrc.py, contract §4).

DB-free. Every lyric line here is INVENTED (contract [C1]/[C2]): the
vectors V1–V10 and F are the contract's own, used exactly as written.
"""

from __future__ import annotations

import random
import re

import pytest

from domovoi.lyrics import lrc
from domovoi.lyrics.lrc import (
    MAX_ENTRIES,
    MAX_LINE_CHARS,
    MAX_OFFSET_MS,
    MAX_TAGS,
    decode_lrc_bytes,
    format_lrc,
    looks_like_lrc,
    parse_lrc,
    parse_lrc_untimed,
    parse_lyrics_text,
    parse_plain,
)

L1 = "the lantern hums beside the river door"
L2 = "and every copper kettle sings at dawn"
L3 = "oh the paper boats are sailing down the hall"
L4 = "we carried paper boats along the hall"


# ─── The contract's vectors (§4.7) ────────────────────────────────────────


def test_v1_tags_gaps_and_a_line_with_two_times() -> None:
    src = (
        "[ti:Lantern Song]\n[ar:The Example Band]\n"
        f"[00:12.40]{L1}\n[00:16.85] {L2}\n[00:21.10]\n[00:21.30][01:05.00]{L3}\n"
    )
    p = parse_lrc(src)
    assert p.synced == ((12400, L1), (16850, L2), (21100, ""), (21300, L3), (65000, L3))
    assert p.plain == "\n".join([L1, L2, L3, L3])
    assert dict(p.tags) == {"ti": "Lantern Song", "ar": "The Example Band"}
    assert p.offset_ms == 0
    assert p.marked is False
    assert looks_like_lrc(src)


def test_v2_a_positive_offset_shows_the_lyrics_sooner_and_never_before_zero() -> None:
    p = parse_lrc("[offset:+500]\n[00:01.00]one\n[00:00.20]zero")
    assert p.synced == ((0, "zero"), (500, "one"))
    assert p.plain == "zero\none"
    assert p.offset_ms == 500


def test_v3_a_negative_offset_shows_them_later() -> None:
    p = parse_lrc("[offset:-250]\n[00:10.00]later")
    assert p.synced == ((10250, "later"),)
    assert p.offset_ms == -250


def test_v4_every_fraction_width_and_the_colon_form() -> None:
    p = parse_lrc("[00:01.5]a\n[00:02.05]b\n[00:03.005]c\n[00:04:50]d\n[1:02]e")
    assert p.synced == ((1500, "a"), (2050, "b"), (3005, "c"), (4500, "d"), (62000, "e"))


def test_v5_word_tags_are_dropped() -> None:
    p = parse_lrc("[00:05.00]<00:05.00>the <00:05.40>copper <00:05.90>kettle")
    assert p.synced == ((5000, "the copper kettle"),)


def test_v6_untimed_lines_in_a_timed_file_are_ignored() -> None:
    p = parse_lrc("[00:01.00]paper boats\nsome stray words\n[00:02.00]along the hall")
    assert p.synced == ((1000, "paper boats"), (2000, "along the hall"))
    assert p.plain == "paper boats\nalong the hall"


def test_v7_plain_text_keeps_one_stanza_break() -> None:
    src = "\n\nthe river door\n\n\n\nis open tonight\n"
    assert looks_like_lrc(src) is False
    for p in (parse_lrc(src), parse_plain(src), parse_lyrics_text(src)):
        assert p.synced is None
        assert p.plain == "the river door\n\nis open tonight"


def test_v8_the_domovoi_marker() -> None:
    p = parse_lrc("[re: domovoi ]\n[by:LRCLIB]\n[00:01.00]x")
    assert p.marked is True
    assert dict(p.tags) == {"re": "domovoi", "by": "LRCLIB"}
    assert parse_lrc("[re:SomeEditor]\n[00:01.00]x").marked is False
    assert parse_lrc("[00:01.00]x").marked is False


def test_v9_seconds_of_sixty_or_more_are_no_time_tag() -> None:
    p = parse_lrc("[00:75.00]bad")
    assert p.synced is None
    assert p.plain == "[00:75.00]bad"


def test_v10_a_bom_crlf_utf8_file_and_a_cp1252_file() -> None:
    a = decode_lrc_bytes(b"\xef\xbb\xbf[00:01.00]caf\xc3\xa9\r\n")
    b = decode_lrc_bytes(b"[00:01.00]caf\xe9")
    for text in (a, b):
        assert parse_lrc(text).synced == ((1000, "café"),)


def test_f_the_file_domovoi_writes() -> None:
    out = format_lrc(
        [(12400, L1), (59995, L2), (65000, ""), (6000123, L4)],
        title="Lantern Song [Live]", artist="The Example Band", album=None,
        duration_sec=205, version="1.0.0",
    )
    assert out == (
        "[re:Domovoi]\n[by:LRCLIB]\n[ve:1.0.0]\n[ti:Lantern Song (Live)]\n"
        "[ar:The Example Band]\n[length:03:25]\n"
        f"[00:12.40]{L1}\n[01:00.00]{L2}\n[01:05.00]\n[100:00.12]{L4}\n"
    )


# ─── Decoding [LD1] / [LD2] ───────────────────────────────────────────────


def test_utf16_files_with_either_byte_order_mark() -> None:
    text = f"[00:01.00]{L1}\n"
    for enc in ("utf-16-le", "utf-16-be"):
        bom = b"\xff\xfe" if enc.endswith("le") else b"\xfe\xff"
        assert decode_lrc_bytes(bom + text.encode(enc)) == text


def test_lone_carriage_returns_split_lines_too() -> None:
    p = parse_lrc("[00:01.00]paper boats\r[00:02.00]along the hall\r\n[00:03.00]at dawn")
    assert [t for _, t in p.synced] == ["paper boats", "along the hall", "at dawn"]


# ─── Tags, offsets, limits ────────────────────────────────────────────────


def test_section_labels_and_tag_lines_with_text_are_ordinary_text() -> None:
    p = parse_plain("[Chorus]\nthe river door")
    assert p.plain == "[Chorus]\nthe river door"
    # an ID-shaped tag followed by text is not an ID tag
    q = parse_lrc("[ar:Glass Harbor] hello there")
    assert dict(q.tags) == {}
    assert q.plain == "[ar:Glass Harbor] hello there"
    # a non-time tag after the times stays in the text
    r = parse_lrc("[00:01.00][Chorus] the river door")
    assert r.synced == ((1000, "[Chorus] the river door"),)


def test_id_tags_never_become_lyrics_and_the_first_one_wins() -> None:
    p = parse_lrc("[ti:First]\n[ti:Second]\n[al:Glass Harbor]\n[00:01.00]paper boats")
    assert dict(p.tags) == {"ti": "First", "al": "Glass Harbor"}
    assert p.plain == "paper boats"


def test_at_most_max_tags_are_kept() -> None:
    src = "\n".join(f"[k{i}:v{i}]" for i in range(MAX_TAGS + 5)) + "\n[00:01.00]x"
    p = parse_lrc(src)
    assert len(p.tags) == MAX_TAGS
    assert p.plain == "x"


@pytest.mark.parametrize("raw, expected", [
    ("+500", 500), ("500", 500), ("-75", -75), (" 12 ", 12), ("+ 5", 0), ("abc", 0),
    ("1.5", 0), ("99999999", MAX_OFFSET_MS), ("-" + "9" * 40, -MAX_OFFSET_MS),
])
def test_offsets(raw, expected) -> None:
    p = parse_lrc(f"[offset:{raw}]\n[20:00.00]late")
    assert p.offset_ms == expected
    assert p.synced == ((max(0, 1_200_000 - expected), "late"),)


def test_lines_are_cut_and_cleaned() -> None:
    long = "lantern " * 200
    p = parse_lrc(f"[00:01.00]{long}\n[00:02.00]copper\tkettle\x00\x07 sings\x1f")
    first, second = p.synced
    assert len(first[1]) <= MAX_LINE_CHARS and not first[1].endswith(" ")
    assert second == (2000, "copper kettle sings")


def test_at_most_max_entries_first_in_time_order() -> None:
    lines = [f"[{(i // 6000):02d}:{(i // 100) % 60:02d}.{i % 100:02d}]w{i}" for i in range(MAX_ENTRIES + 50)]
    lines.reverse()                                   # file order is not time order
    p = parse_lrc("\n".join(lines))
    assert len(p.synced) == MAX_ENTRIES
    assert p.synced[0] == (0, "w0")
    assert p.synced[-1][1] == f"w{MAX_ENTRIES - 1}"


def test_equal_times_keep_file_order() -> None:
    p = parse_lrc("[00:01.00]second line\n[00:01.00]third line\n[00:00.50]first line")
    assert [t for _, t in p.synced] == ["first line", "second line", "third line"]


def test_a_file_of_gaps_only_reads_its_untimed_lines() -> None:
    p = parse_lrc("[ti:Glass Harbor]\n[00:01.00]\n[00:02.00]\nthe river door\n")
    assert p.synced is None
    assert p.plain == "the river door"
    assert p.offset_ms == 0
    assert dict(p.tags) == {"ti": "Glass Harbor"}


def test_nothing_at_all() -> None:
    for text in ("", "\n\n", "[ti:x]\n[ar:y]", "[00:01.00]\n[00:02.00]"):
        p = parse_lrc(text)
        assert p.synced is None and p.plain is None
    assert parse_plain("   \n\t\n").plain is None


# ─── looks_like_lrc [P16] ─────────────────────────────────────────────────


def test_looks_like_lrc_counts_timed_lines_with_text() -> None:
    assert looks_like_lrc("[00:01.00]a\n[00:02.00]b\n[00:03.00]c\n" + "plain words\n" * 20)
    assert looks_like_lrc("[00:01.00]a\nplain words")          # 1 of 2: half
    assert not looks_like_lrc("[00:01.00]a\nplain\nwords")      # 1 of 3
    assert not looks_like_lrc("[ti:x]\n[ar:y]\nthe river door")
    assert not looks_like_lrc("[00:01.00]\n[00:02.00]\nthe river door")
    assert looks_like_lrc("[ti:x]\n[ar:y]\n[al:z]\n[00:01.00]the river door")


def test_parse_lyrics_text_picks_the_reader() -> None:
    timed = parse_lyrics_text(f"[00:01.00]{L1}\n[00:02.00]{L2}")
    assert timed.synced == ((1000, L1), (2000, L2))
    plain = parse_lyrics_text(f"{L1}\n{L2}")
    assert plain.synced is None and plain.plain == f"{L1}\n{L2}"


# ─── Writing [F1]–[F6] ────────────────────────────────────────────────────


def test_the_header_omits_what_is_not_known_and_cleans_what_is() -> None:
    out = format_lrc([(1000, "x")], title="Glass\x07 Harbor ]", artist=" The Velvet Kites ",
                     album="  ", duration_sec=None, version="2.1.0")
    assert out.splitlines()[:5] == [
        "[re:Domovoi]", "[by:LRCLIB]", "[ve:2.1.0]", "[ti:Glass Harbor )]", "[ar:The Velvet Kites]",
    ]
    assert "[al:" not in out and "[length:" not in out
    assert out.endswith("[00:01.00]x\n")
    long = format_lrc([], title="t" * 500, artist="a", album="b" * 300, duration_sec=3600,
                      version="1")
    ti = re.search(r"\[ti:(t*)\]", long).group(1)
    assert len(ti) == 200
    assert "[length:60:00]" in long


_WORDS = (
    "lantern hums beside the river door and every copper kettle sings at dawn "
    "we carried paper boats along the hall glass harbor velvet kites open tonight"
).split()


def test_round_trip_f5() -> None:
    rng = random.Random(20261005)
    for _ in range(300):
        n = rng.randint(1, 60)
        times = sorted(rng.randint(0, 3_600_000) for _ in range(n))
        entries = []
        for t in times:
            text = "" if rng.random() < 0.1 else " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 9)))
            entries.append((t, text))
        out = format_lrc(entries, title="Lantern Song", artist="The Example Band",
                         album=rng.choice([None, "Glass Harbor"]), duration_sec=rng.choice([None, 205]),
                         version="1.0.0")
        back = parse_lrc(out)
        assert back.marked
        expected = [((t + 5) // 10 * 10, s) for t, s in entries]
        if any(s for _, s in entries):
            # stable sort of rounded times keeps the order
            assert list(back.synced) == sorted(expected, key=lambda e: e[0])
        else:
            assert back.synced is None


# ─── [P11] never raises; outputs are always well-formed ───────────────────


def _check_shape(p: lrc.ParsedLyrics) -> None:
    if p.synced is not None:
        assert len(p.synced) <= MAX_ENTRIES
        assert [t for t, _ in p.synced] == sorted(t for t, _ in p.synced)
        assert all(t >= 0 for t, _ in p.synced)
        assert all(len(s) <= MAX_LINE_CHARS for _, s in p.synced)
        assert p.plain == "\n".join(s for _, s in p.synced if s)
    if p.plain is not None:
        assert p.plain
        assert not re.search("[\x00-\x09\x0b-\x1f]", p.plain)
        assert not p.plain.startswith("\n") and not p.plain.endswith("\n")


def test_random_bytes_never_raise() -> None:
    rng = random.Random(7)
    alphabet = b"[]<>:.0123456789+-abc \t\r\n\x00\xff\xfe\xef\xbb\xbf\xc3\xa9"
    for _ in range(2000):
        n = rng.randint(0, 200)
        data = bytes(rng.choice(alphabet) for _ in range(n))
        text = decode_lrc_bytes(data)
        assert isinstance(text, str)
        for fn in (parse_lrc, parse_plain, parse_lyrics_text, parse_lrc_untimed):
            _check_shape(fn(text))
        assert looks_like_lrc(text) in (True, False)
    for junk in (b"", bytes(range(256)), b"\xff\xfe\x00", b"\xfe\xff\xd8\x00"):
        _check_shape(parse_lyrics_text(decode_lrc_bytes(junk)))


def test_reprs_never_show_lyrics() -> None:
    """[C3]: a ParsedLyrics (or anything holding lyrics) in a traceback or
    a log shows sizes, never words."""
    from domovoi.lyrics.sources import SidecarBytes
    from domovoi.lyrics.store import LrcRow

    p = parse_lrc(f"[ti:Lantern Song]\n[00:01.00]{L1}\n[00:02.00]{L2}")
    text = repr(p) + repr(SidecarBytes(f"[00:01.00]{L1}".encode())) + repr(
        LrcRow(1, "/m/a.mp3", "t", "a", None, 205, False, ((1000, L1),), None, None, None))
    for word in ("lantern", "kettle", "river door"):
        assert word not in text
    assert "2 lines" in repr(p) and "1 lines" in text


def test_odd_inputs_never_raise() -> None:
    for text in (None, 5, "\ud800 lone surrogate", "[" * 1000, "]" * 1000, "[00:01.00]" * 3000):
        p = parse_lrc(text)  # type: ignore[arg-type]
        _check_shape(p)
        _check_shape(parse_plain(text))  # type: ignore[arg-type]
        _check_shape(parse_lrc_untimed(text))  # type: ignore[arg-type]
    assert decode_lrc_bytes(None) == ""  # type: ignore[arg-type]


# ─── A .lrc that does not look like LRC (2026-10-06 review, [P9]/[P10]) ───


def test_an_untimed_lrc_never_shows_its_id_tags() -> None:
    src = f"[ti:Lantern Song]\n[ar:The Example Band]\n[offset:+250]\n{L1}\n\n{L4}\n"
    assert not looks_like_lrc(src)
    p = parse_lrc_untimed(src)
    assert p.synced is None and p.offset_ms == 0
    assert p.plain == f"{L1}\n\n{L4}"
    assert dict(p.tags) == {"ti": "Lantern Song", "ar": "The Example Band", "offset": "+250"}
    # plain text reading keeps them (lyrics inside a tag are read that way)
    assert parse_plain(src).plain.startswith("[ti:Lantern Song]")


def test_a_lrc_with_one_or_two_times_shows_no_stray_time_tags() -> None:
    src = f"[00:01.00]{L1}\n{L2}\n[00:09.50][00:30.00]{L3}\n{L4}\n[00:40.00]\n"
    assert not looks_like_lrc(src)                    # 2 timed of 5: read as plain
    p = parse_lrc_untimed(src)
    assert p.synced is None
    assert p.plain == f"{L1}\n{L2}\n{L3}\n{L4}"           # each line once, in file order
    assert "[00:" not in p.plain


def test_untimed_lrc_reading_keeps_the_plain_rules() -> None:
    # [P13]-[P15]: word tags out, whitespace collapsed, one stanza break;
    # section labels and invalid times are words like any others
    src = f"\n\n[Chorus]\n<00:01.00>{L1}\t \n\n\n[00:75.00]{L4}\n\n"
    assert parse_lrc_untimed(src).plain == f"[Chorus]\n{L1}\n\n[00:75.00]{L4}"
    assert parse_lrc_untimed("[ti:only a title]\n").plain is None
    assert parse_lrc_untimed("").plain is None
