"""The local lyric sources (domovoi/lyrics/sources.py, contract §5.1–§5.2):
the ``.lrc`` beside a song ([F-S1]–[F-S5]) and the lyrics inside the file
([E1]–[E6]).

DB-free. The media are generated here (silent MPEG frames, a bare FLAC
STREAMINFO, an Ogg Opus stream, a minimal MP4 — the cover-art test's
generators) and mutagen writes the lyric tags into them, as a tagger would.
Every lyric line is INVENTED ([C2]).
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

import pytest

from domovoi.lyrics import sources
from domovoi.lyrics.lrc import MAX_LRC_BYTES
from domovoi.lyrics.sources import (
    DirRow,
    artist_title_matches,
    artist_title_sidecars,
    best_candidate,
    can_read,
    change_stamp_ns,
    parse_sidecar,
    read_embedded,
    read_sidecar_bytes,
    sidecar_candidates,
    stat_file,
    sylt_lines,
)
from domovoi.tests.test_library_cover_art import bare_flac, bare_m4a, bare_ogg, silent_mp3

needs_mutagen = pytest.mark.skipif(
    importlib.util.find_spec("mutagen") is None,
    reason="mutagen (the [real-clients] extra) writes the tagged fixtures",
)

L1 = "the lantern hums beside the river door"
L2 = "and every copper kettle sings at dawn"
L3 = "we carried paper boats along the hall"
LRC = f"[00:01.00]{L1}\n[00:02.50]{L2}\n[00:04.00]{L3}\n"


def _symlink_or_skip(link: Path, target: Path, *, is_dir: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks unavailable here ({type(e).__name__})")


# ─── [F-S1]/[F-S2]: the same-stem sidecar ─────────────────────────────────


def test_candidates_are_the_same_stem_in_any_case_best_first() -> None:
    names = ["Lantern Song.flac", "lantern song.LRC", "Lantern Song.LRC", "Lantern Song.lrc",
             "LANTERN SONG.lrc", "Lantern Song (live).lrc", "Glass Harbor.lrc", "cover.jpg"]
    assert sidecar_candidates("Lantern Song.flac", names) == [
        "Lantern Song.lrc", "Lantern Song.LRC", "LANTERN SONG.lrc", "lantern song.LRC",
    ]
    assert sidecar_candidates("Lantern Song.flac", ["Lantern Song.flac", "Lantern Song.txt"]) == []


def test_the_stem_drops_only_the_last_extension() -> None:
    assert sources.audio_stem("Lantern Song.flac") == "Lantern Song"
    assert sources.audio_stem("v1.2 Glass Harbor.mp3") == "v1.2 Glass Harbor"
    assert sidecar_candidates("a.b.mp3", ["a.b.lrc", "a.lrc"]) == ["a.b.lrc"]


# ─── [F-S3]: "Artist - Title.lrc" only when unambiguous ───────────────────


def _row(i: int, audio: str, artist: str | None, title: str | None) -> DirRow:
    return DirRow(i, audio, artist, title)


def test_an_artist_title_file_is_used_when_it_matches_exactly_one_song() -> None:
    names = ["01 track.mp3", "02 track.mp3", "The Example Band - Lantern Song.lrc"]
    rows = [_row(1, "01 track.mp3", "The Example Band", "Lantern Song"),
            _row(2, "02 track.mp3", "The Example Band", "Glass Harbor")]
    assert artist_title_sidecars(names, rows) == {1: "The Example Band - Lantern Song.lrc"}


def test_the_primary_performer_form_matches_too() -> None:
    names = ["01.mp3", "The Example Band - Lantern Song.lrc"]
    rows = [_row(1, "01.mp3", "The Example Band feat. The Velvet Kites", "Lantern Song")]
    assert artist_title_sidecars(names, rows) == {1: "The Example Band - Lantern Song.lrc"}


def test_two_files_matching_one_song_is_ambiguous() -> None:
    names = ["01.mp3", "The Example Band - Lantern Song.lrc", "the example band - lantern song!.LRC"]
    rows = [_row(1, "01.mp3", "The Example Band", "Lantern Song")]
    assert artist_title_sidecars(names, rows) == {}


def test_a_file_two_songs_match_is_ambiguous() -> None:
    names = ["01.mp3", "02.flac", "The Example Band - Lantern Song.lrc"]
    rows = [_row(1, "01.mp3", "The Example Band", "Lantern Song"),
            _row(2, "02.flac", "The Example Band", "Lantern Song")]       # the same song twice
    assert artist_title_sidecars(names, rows) == {}


def test_a_same_stem_sidecar_wins_and_audio_stems_are_never_artist_title_files() -> None:
    names = ["The Example Band - Lantern Song.mp3", "The Example Band - Lantern Song.lrc",
             "02.mp3", "02.lrc"]
    rows = [_row(1, "The Example Band - Lantern Song.mp3", "The Example Band", "Lantern Song"),
            _row(2, "02.mp3", "The Example Band", "Lantern Song")]
    # row 1's file is its own same-stem sidecar; row 2 has 02.lrc
    assert artist_title_sidecars(names, rows) == {}


def test_rows_without_artist_or_title_never_match() -> None:
    names = ["01.mp3", "Lantern Song.lrc"]
    assert artist_title_sidecars(names, [_row(1, "01.mp3", None, "Lantern Song")]) == {}
    assert artist_title_sidecars(names, [_row(1, "01.mp3", "", "")]) == {}


# ─── [F-S4]: reading a sidecar ────────────────────────────────────────────


def test_a_sidecar_is_read_and_parsed(tmp_path) -> None:
    p = tmp_path / "Lantern Song.lrc"
    p.write_bytes(b"\xef\xbb\xbf" + LRC.encode("utf-8"))
    got = read_sidecar_bytes(p, tmp_path.resolve())
    assert got.error is None and got.data is not None
    parsed = parse_sidecar(got.data)
    assert parsed.synced == ((1000, L1), (2500, L2), (4000, L3))
    plain = tmp_path / "plain.lrc"
    plain.write_text(f"{L1}\n\n{L2}\n", encoding="cp1252")
    assert parse_sidecar(read_sidecar_bytes(plain, tmp_path).data).plain == f"{L1}\n\n{L2}"
    assert stat_file(p).size == p.stat().st_size


def test_oversize_missing_and_directory_sidecars_are_not_read(tmp_path) -> None:
    big = tmp_path / "big.lrc"
    big.write_bytes(b"x" * (MAX_LRC_BYTES + 1))
    assert read_sidecar_bytes(big, tmp_path).error == "too_large"
    exact = tmp_path / "exact.lrc"
    exact.write_bytes(b"[00:01.00]x\n" + b" " * (MAX_LRC_BYTES - 12))
    assert len(exact.read_bytes()) == MAX_LRC_BYTES
    assert read_sidecar_bytes(exact, tmp_path).error is None
    assert read_sidecar_bytes(tmp_path / "none.lrc", tmp_path).error == "missing"
    (tmp_path / "dir.lrc").mkdir()
    assert read_sidecar_bytes(tmp_path / "dir.lrc", tmp_path).error == "not_file"
    assert parse_sidecar(None).plain is None


def test_a_symlinked_sidecar_is_read_only_when_it_points_inside_music_dir(tmp_path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    outside = tmp_path / "elsewhere.lrc"
    outside.write_text(LRC, encoding="utf-8")
    inside = music / "real.lrc"
    inside.write_text(LRC, encoding="utf-8")
    link_in = music / "Lantern Song.lrc"
    _symlink_or_skip(link_in, inside)
    link_out = music / "Glass Harbor.lrc"
    _symlink_or_skip(link_out, outside)
    root = music.resolve()
    assert read_sidecar_bytes(link_in, root).data == inside.read_bytes()
    assert read_sidecar_bytes(link_out, root).error == "outside_music_dir"
    broken = music / "Velvet Kites.lrc"
    _symlink_or_skip(broken, music / "gone.lrc")
    assert read_sidecar_bytes(broken, root).error == "missing"
    # A symlink to nothing is no sidecar (it would be "seen" and then never
    # read, so read again on every tick: the 2026-10-06 review).
    assert stat_file(broken) is None


def test_the_symlink_rule_where_symlinks_cannot_be_made(tmp_path, monkeypatch) -> None:
    """The same rule with lstat / resolve simulated, so it runs on a
    Windows box without the symlink privilege too."""
    import stat as stat_mod

    music = tmp_path / "music"
    music.mkdir()
    outside = tmp_path / "outside.lrc"
    outside.write_bytes(LRC.encode("utf-8"))
    inside = music / "inside.lrc"
    inside.write_bytes(LRC.encode("utf-8"))
    to_out = music / "to-out.lrc"
    to_out.write_bytes(b"stand-in")
    to_in = music / "to-in.lrc"
    to_in.write_bytes(LRC.encode("utf-8"))
    links = {to_out: outside, to_in: inside}
    real_lstat, real_resolve = os.lstat, Path.resolve

    def lstat(p, *a, **k):
        st = real_lstat(p, *a, **k)
        if Path(p) in links:
            return os.stat_result((stat_mod.S_IFLNK | 0o777,) + tuple(st)[1:])
        return st

    def resolve(self, strict=False):
        return real_resolve(links.get(self, self), strict=strict)

    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(Path, "resolve", resolve)
    root = real_resolve(music)
    assert read_sidecar_bytes(to_out, root).error == "outside_music_dir"
    assert read_sidecar_bytes(to_in, root).data == LRC.encode("utf-8")
    assert read_sidecar_bytes(to_in, None).error == "outside_music_dir"


# ─── [E1]–[E6]: lyrics inside the file ────────────────────────────────────


def _mp3(path: Path, *frames) -> Path:
    from mutagen.id3 import ID3

    path.write_bytes(silent_mp3())
    tags = ID3()
    for f in frames:
        tags.add(f)
    tags.save(str(path))
    return path


def _flac(path: Path, comments: dict[str, list[str]]) -> Path:
    from mutagen.flac import FLAC

    path.write_bytes(bare_flac())
    f = FLAC(str(path))
    for k, v in comments.items():
        f[k] = v
    f.save()
    return path


@needs_mutagen
def test_uslt_english_first_then_the_longest(tmp_path) -> None:
    from mutagen.id3 import USLT

    p = _mp3(tmp_path / "a.mp3",
             USLT(encoding=3, lang="fra", desc="x", text=f"{L1}\n{L2}\n{L3}\n{L1}"),
             USLT(encoding=3, lang="eng", desc="short", text=L1),
             USLT(encoding=3, lang="eng", desc="long", text=f"{L1}\n{L2}"))
    got = read_embedded(p, track_id=1)
    assert got is not None and got.detail == "USLT"
    assert got.lyrics.synced is None and got.lyrics.plain == f"{L1}\n{L2}"


@needs_mutagen
def test_unknown_language_beats_other_languages(tmp_path) -> None:
    from mutagen.id3 import USLT

    p = _mp3(tmp_path / "a.mp3",
             USLT(encoding=3, lang="deu", desc="x", text=f"{L1}\n{L2}\n{L3}"),
             USLT(encoding=3, lang="xxx", desc="y", text=L3))
    assert read_embedded(p).lyrics.plain == L3


@needs_mutagen
def test_lrc_text_in_uslt_is_timed(tmp_path) -> None:
    from mutagen.id3 import USLT

    p = _mp3(tmp_path / "a.mp3", USLT(encoding=3, lang="eng", desc="", text=LRC))
    got = read_embedded(p)
    assert got.detail == "USLT (LRC)"
    assert got.lyrics.synced == ((1000, L1), (2500, L2), (4000, L3))


@needs_mutagen
def test_sylt_in_milliseconds_wins_and_the_longest_frame_is_used(tmp_path) -> None:
    from mutagen.id3 import SYLT, USLT

    p = _mp3(tmp_path / "a.mp3",
             USLT(encoding=3, lang="eng", desc="", text=L3),
             SYLT(encoding=3, lang="eng", format=2, type=1, desc="short", text=[(L1, 500)]),
             SYLT(encoding=3, lang="eng", format=2, type=1, desc="full",
                  text=[(L2, 2000), (L1, 1000), ("\n", 1500), (L3, 3000)]),
             SYLT(encoding=3, lang="eng", format=1, type=1, desc="frames",
                  text=[(L1, 1), (L2, 2), (L3, 3), (L1, 4), (L2, 5)]))
    got = read_embedded(p)
    assert got.detail == "SYLT"
    assert got.lyrics.synced == ((1000, L1), (1500, ""), (2000, L2), (3000, L3))
    assert got.lyrics.plain == f"{L1}\n{L2}\n{L3}"


@needs_mutagen
def test_sylt_in_mpeg_frames_alone_is_ignored(tmp_path) -> None:
    from mutagen.id3 import SYLT

    p = _mp3(tmp_path / "a.mp3",
             SYLT(encoding=3, lang="eng", format=1, type=1, desc="", text=[(L1, 10)]))
    assert read_embedded(p) is None


@needs_mutagen
def test_flac_lyrics_keys_in_order(tmp_path) -> None:
    p = _flac(tmp_path / "a.flac", {"UNSYNCEDLYRICS": [L2], "LYRICS": [L1]})
    got = read_embedded(p)
    assert (got.detail, got.lyrics.plain) == ("LYRICS", L1)
    q = _flac(tmp_path / "b.flac", {"unsyncedlyrics": [f"{L2}\n{L3}"]})
    got = read_embedded(q)
    assert (got.detail, got.lyrics.plain) == ("UNSYNCEDLYRICS", f"{L2}\n{L3}")
    r = _flac(tmp_path / "c.flac", {"SYNCEDLYRICS": [LRC]})
    got = read_embedded(r)
    assert got.detail == "SYNCEDLYRICS (LRC)" and got.lyrics.synced[0] == (1000, L1)
    s = _flac(tmp_path / "d.flac", {"UNSYNCED LYRICS": [L3]})
    assert read_embedded(s).detail == "UNSYNCED LYRICS"


@needs_mutagen
def test_ogg_opus_lyrics(tmp_path) -> None:
    from mutagen.oggopus import OggOpus

    p = tmp_path / "a.opus"
    p.write_bytes(bare_ogg("opus"))
    f = OggOpus(str(p))
    f["LYRICS"] = [f"{L3}\n{L1}"]
    f.save()
    got = read_embedded(p)
    assert (got.detail, got.lyrics.plain) == ("LYRICS", f"{L3}\n{L1}")


@needs_mutagen
def test_mp4_lyrics_atom(tmp_path) -> None:
    from mutagen.mp4 import MP4

    p = tmp_path / "a.m4a"
    p.write_bytes(bare_m4a())
    f = MP4(str(p))
    if f.tags is None:
        f.add_tags()
    f.tags["\xa9lyr"] = [f"{L2}\n{L3}"]
    f.save()
    got = read_embedded(p)
    assert (got.detail, got.lyrics.plain) == ("\xa9lyr", f"{L2}\n{L3}")


@needs_mutagen
def test_asf_and_ape_tags() -> None:
    from mutagen.apev2 import APEv2
    from mutagen.asf import ASFTags, ASFUnicodeAttribute

    asf = ASFTags()
    asf.append(("WM/Lyrics", ASFUnicodeAttribute(f"{L1}\n{L2}")))
    found = [c for c in sources._candidates(asf) if c is not None]
    assert [(c.detail, c.lyrics.plain) for c in found] == [("WM/Lyrics", f"{L1}\n{L2}")]
    ape = APEv2()
    ape["lyrics"] = LRC
    found = [c for c in sources._candidates(ape) if c is not None]
    assert found[0].detail == "APE Lyrics (LRC)" and len(found[0].lyrics.synced) == 3


@needs_mutagen
def test_no_tags_corrupt_files_and_oversize_text_give_nothing(tmp_path, caplog) -> None:
    from mutagen.id3 import USLT

    bare = tmp_path / "bare.mp3"
    bare.write_bytes(silent_mp3())
    assert read_embedded(bare) is None
    junk = tmp_path / "junk.flac"
    junk.write_bytes(b"fLaC" + b"\xff" * 64)
    with caplog.at_level(logging.DEBUG, logger="domovoi.lyrics.sources"):
        assert read_embedded(junk, track_id=42) is None
        assert read_embedded(tmp_path / "missing.mp3", track_id=43) is None
    assert any("track 42" in r.getMessage() for r in caplog.records)
    huge = _mp3(tmp_path / "huge.mp3",
                USLT(encoding=3, lang="eng", desc="", text=(L1 + "\n") * (MAX_LRC_BYTES // 30)))
    assert read_embedded(huge) is None
    empty = _mp3(tmp_path / "empty.mp3", USLT(encoding=3, lang="eng", desc="", text="  \n\t\n"))
    assert read_embedded(empty) is None


def test_without_mutagen_the_tags_yield_nothing(tmp_path, monkeypatch) -> None:
    p = tmp_path / "a.mp3"
    p.write_bytes(silent_mp3())
    monkeypatch.setitem(sys.modules, "mutagen", None)        # import mutagen → ImportError
    assert read_embedded(p, track_id=1) is None


@needs_mutagen
def test_nothing_here_logs_lyric_text(tmp_path, caplog) -> None:
    from mutagen.id3 import USLT

    p = _mp3(tmp_path / "a.mp3", USLT(encoding=3, lang="eng", desc="", text=LRC))
    with caplog.at_level(logging.DEBUG):
        read_embedded(p, track_id=7)
        parse_sidecar(LRC.encode())
        read_sidecar_bytes(tmp_path / "a.lrc", tmp_path)
    for word in ("lantern", "kettle", "paper boats"):
        assert word not in caplog.text


# ═══ The 2026-10-06 review ══════════════════════════════════════════════
# (sources / files / data review: names in two Unicode forms, a symlink to
# nothing, a .lrc that is not LRC, SYLT synced by syllable, a FLAC with
# both plain and timed lyrics, a file the core could not read yet)


def test_an_untimed_sidecar_shows_no_tags_and_no_stray_times() -> None:
    tagged = f"[ti:Lantern Song]\n[ar:The Example Band]\n{L1}\n{L3}\n".encode()
    p = parse_sidecar(tagged)
    assert (p.synced, p.plain) == (None, f"{L1}\n{L3}")
    partly = f"[00:01.00]{L1}\n{L2}\n{L3}\n{L1}\n".encode()     # 1 timed line of 4: not LRC
    p = parse_sidecar(partly)
    assert p.synced is None and p.plain == f"{L1}\n{L2}\n{L3}\n{L1}"
    # A real LRC file is read as before, and lyrics in a TAG that are not
    # LRC keep their every line (only a .lrc file's tags are not words).
    assert parse_sidecar(LRC.encode()).synced[0] == (1000, L1)
    assert sources.parse_lyrics_text(tagged.decode()).plain.startswith("[ti:Lantern Song]")


def test_names_compare_in_one_unicode_form() -> None:
    import unicodedata

    nfc = lambda s: unicodedata.normalize("NFC", s)   # noqa: E731
    nfd = lambda s: unicodedata.normalize("NFD", s)   # noqa: E731
    audio = nfc("Café Lantern.mp3")
    assert nfc("Café Lantern.lrc") != nfd("Café Lantern.lrc")
    # a Mac-copied .lrc (decomposed) beside a composed audio name: its sidecar
    assert sidecar_candidates(audio, [audio, nfd("Café Lantern.lrc")]) == [nfd("Café Lantern.lrc")]
    assert sidecar_candidates(nfd("Café Lantern.mp3"), [nfc("CAFÉ LANTERN.lrc")]) == [nfc("CAFÉ LANTERN.lrc")]
    # ...so a .lrc that is another audio file's sidecar in the other form is
    # never an "Artist - Title" file for a third song
    names = ["01.mp3", nfd("Café - Lantern.mp3"), nfc("Café - Lantern.lrc")]
    assert artist_title_matches(names, [_row(1, "01.mp3", "Café", "Lantern")]) == {}


def test_an_artist_title_match_is_found_beside_a_same_stem_file_too() -> None:
    names = ["01 Lantern Song.mp3", "01 Lantern Song.lrc", "The Example Band - Lantern Song.lrc"]
    rows = [_row(1, "01 Lantern Song.mp3", "The Example Band", "Lantern Song")]
    # the scan decides between the two (Domovoi's own same-stem file never
    # hides the household's "Artist - Title" one) ...
    assert artist_title_matches(names, rows) == {1: "The Example Band - Lantern Song.lrc"}
    # ... and the contract's reading, with no Domovoi file involved, stands
    assert artist_title_sidecars(names, rows) == {}


def test_a_symlink_to_nothing_or_a_loop_is_no_sidecar(tmp_path, monkeypatch) -> None:
    """Simulated, so it runs where symlinks can't be made: stat follows the
    link and finds nothing (or a loop)."""
    import errno

    gone = tmp_path / "Lantern Song.lrc"
    loop = tmp_path / "Glass Harbor.lrc"
    denied = tmp_path / "Velvet Kites.lrc"
    for p in (gone, loop, denied):
        p.write_bytes(LRC.encode())
    real_stat = os.stat

    def stat(path, *a, **k):
        if Path(path) == gone:
            raise FileNotFoundError(errno.ENOENT, "no such file")
        if Path(path) == loop:
            raise OSError(errno.ELOOP, "too many levels of symbolic links")
        if Path(path) == denied:
            raise PermissionError(errno.EACCES, "denied")
        return real_stat(path, *a, **k)

    monkeypatch.setattr(os, "stat", stat)
    assert stat_file(gone) is None and stat_file(loop) is None
    assert stat_file(denied) is not None                 # looked at; its read says why not


def test_a_symlink_loop_reads_as_not_a_file(tmp_path, monkeypatch) -> None:
    import errno
    import stat as stat_mod

    p = tmp_path / "Lantern Song.lrc"
    p.write_bytes(b"stand-in")
    real_lstat = os.lstat

    def lstat(path, *a, **k):
        st = real_lstat(path, *a, **k)
        if Path(path) == p:
            return os.stat_result((stat_mod.S_IFLNK | 0o777,) + tuple(st)[1:])
        return st

    monkeypatch.setattr(os, "lstat", lstat)
    for raised in (RuntimeError("Symlink loop"), OSError(errno.ELOOP, "loop")):
        def resolve(self, strict=False, _e=raised):
            raise _e

        monkeypatch.setattr(Path, "resolve", resolve)
        assert read_sidecar_bytes(p, tmp_path).error == "not_file"


def test_the_change_stamp_is_the_later_of_mtime_and_ctime_on_posix() -> None:
    from types import SimpleNamespace

    chmodded = SimpleNamespace(st_mtime_ns=1_000, st_ctime_ns=9_000, st_size=5)
    restored = SimpleNamespace(st_mtime_ns=9_000, st_ctime_ns=1_000, st_size=5)
    assert change_stamp_ns(chmodded, posix=True) == 9_000     # a chmod moved only the ctime
    assert change_stamp_ns(restored, posix=True) == 9_000
    assert change_stamp_ns(chmodded, posix=False) == 1_000    # Windows: ctime is the birth time


def test_can_read(tmp_path) -> None:
    p = tmp_path / "Lantern Song.mp3"
    p.write_bytes(silent_mp3())
    assert can_read(p) is True
    assert can_read(tmp_path / "gone.mp3") is False


# ─── SYLT synced by syllable or word (ID3v2.4's way) ──────────────────────

# The two invented lines L1, L2 in syllables: a space in front of a new
# word, a newline in front of the first syllable of a line.
SYLLABLES = [
    ("the", 12400), (" lan", 12700), ("tern", 12900), (" hums", 13300), (" be", 13800),
    ("side", 14000), (" the", 14300), (" ri", 14500), ("ver", 14700), (" door", 15000),
    ("\nand", 16850), (" ev", 17100), ("ery", 17300), (" cop", 17600), ("per", 17800),
    (" ket", 18100), ("tle", 18300), (" sings", 18600), (" at", 19000), (" dawn", 19300),
]


def _pairs(frame_text):
    return [(ts, body) for body, ts in frame_text]


def test_sylt_syllables_are_joined_into_lines() -> None:
    from domovoi.lyrics.lrc import from_entries

    got = from_entries(sylt_lines(_pairs(SYLLABLES)))
    assert got.synced == ((12400, L1), (16850, L2))
    assert got.plain == f"{L1}\n{L2}"
    # the newline at the END of a line's last piece (some taggers) reads the same
    tail_style = [("the lantern hums", 1000), (" beside the river door\n", 1500),
                  ("and every copper", 3000), (" kettle sings at dawn\n", 3500)]
    assert from_entries(sylt_lines(_pairs(tail_style))).synced == ((1000, L1), (3000, L2))


def test_sylt_of_whole_lines_is_read_one_entry_a_line() -> None:
    whole = [(L1, 1000), ("\n", 1500), (L2, 2000)]       # a newline alone: a blank line
    assert sylt_lines(_pairs(whole)) == _pairs(whole)
    no_newlines = [(L1, 1000), (L2, 2000), (L3, 3000)]
    assert sylt_lines(_pairs(no_newlines)) == _pairs(no_newlines)


@needs_mutagen
def test_a_syllable_synced_sylt_frame_reads_as_lines(tmp_path) -> None:
    from mutagen.id3 import SYLT, USLT

    from domovoi.handlers.shared.lyric_search import build_lines

    p = _mp3(tmp_path / "a.mp3",
             USLT(encoding=3, lang="eng", desc="", text=L3),
             SYLT(encoding=3, lang="eng", format=2, type=1, desc="", text=SYLLABLES))
    got = read_embedded(p, track_id=1)
    assert got.detail == "SYLT"
    assert got.lyrics.synced == ((12400, L1), (16850, L2))
    # lyric search indexes the two lines, not twenty syllables
    assert [r.text for r in build_lines(got.lyrics.plain) if r.span == 1] == [L1, L2]


# ─── Timed beats plain inside a file too ([E3] amended, [M1]) ─────────────


@needs_mutagen
def test_a_flac_with_plain_and_timed_lyrics_shows_the_timed(tmp_path) -> None:
    both = _flac(tmp_path / "a.flac", {"LYRICS": [f"{L1}\n{L2}\n{L3}"], "SYNCEDLYRICS": [LRC]})
    got = read_embedded(both)
    assert got.detail == "SYNCEDLYRICS (LRC)"
    assert got.lyrics.synced == ((1000, L1), (2500, L2), (4000, L3))
    # with no timed lyrics anywhere the order of [E3] decides, as before
    plain = _flac(tmp_path / "b.flac", {"SYNCEDLYRICS": [L2], "LYRICS": [L1]})
    assert read_embedded(plain).detail == "LYRICS"


@needs_mutagen
def test_an_lrc_uslt_beats_a_plain_english_one(tmp_path) -> None:
    from mutagen.id3 import USLT

    p = _mp3(tmp_path / "a.mp3",
             USLT(encoding=3, lang="eng", desc="plain", text=f"{L1}\n{L2}"),
             USLT(encoding=3, lang="und", desc="timed", text=LRC))
    got = read_embedded(p)
    assert got.detail == "USLT (LRC)" and got.lyrics.synced is not None


def test_best_candidate() -> None:
    from domovoi.lyrics.lrc import parse_lrc, parse_plain
    from domovoi.lyrics.sources import EmbeddedLyrics

    plain_a = EmbeddedLyrics("LYRICS", parse_plain(L1))
    plain_b = EmbeddedLyrics("UNSYNCEDLYRICS", parse_plain(L2))
    timed = EmbeddedLyrics("SYNCEDLYRICS (LRC)", parse_lrc(LRC))
    assert best_candidate([None, plain_a, plain_b, timed]) is timed
    assert best_candidate([plain_a, plain_b]) is plain_a
    assert best_candidate([None]) is None and best_candidate([]) is None
