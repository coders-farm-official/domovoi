"""domovoi.media_tags — Domovoi's own tag reader (replaced mutagen, GPL,
2026-10-15). Format by format, from files the tests' writer builds
(``media_writer``, independent of the reader); the real-file corpus and
the comparison with mutagen's answers are in test_media_tags_corpus.py.
DB-free."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from domovoi import media_tags
from domovoi.media_tags import TagError
from domovoi.tests import media_writer as mw

JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) + b"\xff\x00\xff\xe1"
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 40


def read(tmp_path: Path, name: str, data: bytes) -> media_tags.MediaFile | None:
    p = tmp_path / name
    p.write_bytes(data)
    return media_tags.read(p)


def mp3(tmp_path: Path, frames, name: str = "a.mp3", **kw) -> media_tags.MediaFile:
    p = mw.write_mp3(tmp_path / name, frames, **kw)
    f = media_tags.read(p)
    assert f is not None
    return f


# ─── ID3v2 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("version", [2, 3, 4])
def test_id3_text_picture_and_lyrics_in_every_version(tmp_path, version) -> None:
    enc = 1 if version < 4 else 3
    f = mp3(tmp_path, [mw.text_frame("TIT2", "Lantern Song", encoding=enc),
                       mw.text_frame("TPE1", "Ünïcode Bänd", encoding=enc),
                       mw.APIC(encoding=enc, mime="image/jpeg", type=3, desc="front", data=JPEG),
                       mw.USLT(encoding=enc, lang="eng", desc="", text="one\ntwo")], version=version)
    assert f.kind == "mp3" and f.tags.kind == "id3"
    assert f.tags.first("TIT2") == "Lantern Song"
    assert f.tags.get("TPE1") == ["Ünïcode Bänd"]
    (pic,) = f.tags.getall("APIC")
    assert (pic.type, pic.mime, pic.desc, pic.data) == (3, "image/jpeg", "front", JPEG)
    (uslt,) = f.tags.getall("USLT")
    assert (uslt.lang, uslt.text) == ("eng", "one\ntwo")
    assert f.tags.version[0] == version


def test_id3v22_three_letter_ids_read_as_their_v24_names(tmp_path) -> None:
    f = mp3(tmp_path, [mw.text_frame("TALB", "Old", encoding=0),
                       mw.APIC(encoding=0, mime="image/png", type=0, desc="", data=PNG),
                       mw.SYLT(encoding=0, lang="eng", format=2, type=1, desc="", text=[("hi", 10)])], version=2)
    assert f.tags.first("TALB") == "Old"
    assert f.tags.getall("APIC")[0].mime == "image/png"            # PIC's "PNG" format
    assert f.tags.getall("SYLT")[0].text == [("hi", 10)]


@pytest.mark.parametrize("version", [2, 3, 4])
def test_unsynchronisation(tmp_path, version) -> None:
    """The picture's 0xFF bytes are what unsynchronisation exists for."""
    f = mp3(tmp_path, [mw.text_frame("TIT2", "Unsync", encoding=1),
                       mw.APIC(encoding=0, mime="image/jpeg", type=3, desc="", data=JPEG)],
            version=version, unsync=True)
    assert f.tags.first("TIT2") == "Unsync"
    assert f.tags.getall("APIC")[0].data == JPEG


def test_v24_frame_flags_compression_unsync_data_length(tmp_path) -> None:
    f = mp3(tmp_path, [mw.Frame("TIT2", (3, ["Squeezed"]), flags=0x08),
                       mw.Frame("APIC", (3, "image/jpeg", 3, "", JPEG), flags=0x02),
                       mw.Frame("TPE1", (3, ["Indicated"]), flags=0x01)], version=4, footer=True)
    assert f.tags.first("TIT2") == "Squeezed"
    assert f.tags.getall("APIC")[0].data == JPEG
    assert f.tags.first("TPE1") == "Indicated"
    assert f.duration is not None and f.duration > 0           # the footer was skipped too


def test_v23_compressed_frame_and_extended_header(tmp_path) -> None:
    f = mp3(tmp_path, [mw.Frame("TIT2", (0, ["Zipped"]), flags=0x08), mw.text_frame("TPE1", "Plain", encoding=0)],
            version=3, ext_header=True)
    assert (f.tags.first("TIT2"), f.tags.first("TPE1")) == ("Zipped", "Plain")


def test_v24_extended_header(tmp_path) -> None:
    f = mp3(tmp_path, [mw.text_frame("TIT2", "After The Extension")], version=4, ext_header=True)
    assert f.tags.first("TIT2") == "After The Extension"


def test_v24_tag_written_with_plain_32_bit_frame_sizes(tmp_path) -> None:
    """Some writers (old iTunes) put plain integers where v2.4 wants
    syncsafe ones; a frame over 127 bytes tells them apart."""
    long_title = "T" * 300
    f = mp3(tmp_path, [mw.text_frame("TIT2", long_title), mw.text_frame("TPE1", "Next Frame")],
            version=4, plain_sizes=True)
    assert (f.tags.first("TIT2"), f.tags.first("TPE1")) == (long_title, "Next Frame")
    g = mp3(tmp_path, [mw.text_frame("TIT2", long_title), mw.text_frame("TPE1", "Next Frame")],
            name="b.mp3", version=4)
    assert (g.tags.first("TIT2"), g.tags.first("TPE1")) == (long_title, "Next Frame")


def test_text_encodings_and_multiple_values(tmp_path) -> None:
    f = mp3(tmp_path, [mw.text_frame("TPE1", "First", "Second", encoding=1),
                       mw.text_frame("TPE2", "Big Endian", encoding=2),
                       mw.text_frame("TALB", "Latin ñ", encoding=0),
                       mw.text_frame("TCOM", "", "After A Blank")], version=4)
    assert f.tags.get("TPE1") == ["First", "Second"]
    assert f.tags.first("TPE1") == "First"                       # never "First\x00Second"
    assert f.tags.first("TPE2") == "Big Endian"
    assert f.tags.first("TALB") == "Latin ñ"
    assert f.tags.first("TCOM") == "After A Blank"               # first non-blank value


def test_utf16_values_after_the_first_without_a_bom_keep_its_byte_order(tmp_path) -> None:
    body = b"\x01" + b"\xfe\xff" + "Ab".encode("utf-16-be") + b"\x00\x00" + "Cd".encode("utf-16-be")
    frame = b"TPE1" + mw._syncsafe(len(body)) + b"\x00\x00" + body
    tag = b"ID3\x04\x00\x00" + mw._syncsafe(len(frame)) + frame
    f = read(tmp_path, "a.mp3", tag + mw.silent_mp3())
    assert f.tags.get("TPE1") == ["Ab", "Cd"]


def test_id3v1_alone_and_merged_under_v2(tmp_path) -> None:
    v1 = mw.id3v1(title="V1 Title", artist="V1 Artist", album="V1 Album", track=4)
    alone = mp3(tmp_path, [], name="alone.mp3", v1=v1)
    assert (alone.tags.first("TIT2"), alone.tags.first("TPE1"), alone.tags.first("TALB")) == (
        "V1 Title", "V1 Artist", "V1 Album")
    assert alone.tags.first("TRCK") == "4"
    both = mp3(tmp_path, [mw.text_frame("TIT2", "V2 Title")], name="both.mp3", v1=v1)
    assert both.tags.get("TIT2") == ["V2 Title"]                 # v2 wins; v1's title is not added
    assert both.tags.first("TPE1") == "V1 Artist"                # v1 fills the gap


def test_chapters_with_and_without_titles(tmp_path) -> None:
    f = mp3(tmp_path, [mw.CHAP(element_id="b", start_time=90_000, end_time=120_000, sub_frames=[mw.TIT2("Two")]),
                       mw.CHAP(element_id="a", start_time=0, end_time=90_000, sub_frames=[mw.TIT2("One")]),
                       mw.CHAP(element_id="c", start_time=120_000, end_time=150_000)])
    chaps = f.tags.getall("CHAP")
    assert [(c.element_id, c.start_time) for c in chaps] == [("b", 90_000), ("a", 0), ("c", 120_000)]
    assert [s.text for c in chaps for s in c.sub_frames] == [["Two"], ["One"]]


def test_sylt_entries_and_formats(tmp_path) -> None:
    f = mp3(tmp_path, [mw.SYLT(encoding=1, lang="eng", format=2, type=1, desc="d",
                               text=[("first", 500), ("\nsecond", 1500)]),
                       mw.SYLT(encoding=3, lang="eng", format=1, type=1, desc="", text=[("x", 3)])])
    ms, frames = f.tags.getall("SYLT")
    assert (ms.format, ms.desc, ms.text) == (2, "d", [("first", 500), ("\nsecond", 1500)])
    assert (frames.format, frames.text) == (1, [("x", 3)])


def test_keys_with_a_description(tmp_path) -> None:
    f = mp3(tmp_path, [mw.TXXX(desc="MOOD", text="calm"), mw.USLT(encoding=3, lang="eng", desc="v", text="w")])
    assert f.tags.first("TXXX") == "calm"
    assert [str(x) for x in f.tags.get("TXXX:MOOD")] == ["calm"]
    assert [x.text for x in f.tags.get("USLT:v:eng")] == ["w"]
    assert f.tags.get("TXXX:other") == []


def test_a_malformed_frame_is_dropped_not_fatal(tmp_path) -> None:
    bad = b"APIC" + mw._syncsafe(3) + b"\x00\x00" + b"\x00ab"          # no MIME terminator, no data
    good = mw._frame_bytes(mw.text_frame("TIT2", "Still Here"), 4, False)
    tag = b"ID3\x04\x00\x00" + mw._syncsafe(len(bad + good)) + bad + good
    f = read(tmp_path, "a.mp3", tag + mw.silent_mp3())
    assert f.tags.first("TIT2") == "Still Here"
    assert f.tags.getall("APIC") == []


def test_unknown_encryption_and_bad_zlib_frames_are_skipped(tmp_path) -> None:
    enc = b"TIT3" + mw._syncsafe(5) + b"\x00\x04" + b"\x01" + b"abcd"
    broken = b"TIT1" + mw._syncsafe(9) + b"\x00\x09" + mw._syncsafe(5) + b"nope!"
    good = mw._frame_bytes(mw.text_frame("TIT2", "Readable"), 4, False)
    tag = b"ID3\x04\x00\x00" + mw._syncsafe(len(enc + broken + good)) + enc + broken + good
    f = read(tmp_path, "a.mp3", tag + mw.silent_mp3())
    assert f.tags.first("TIT2") == "Readable"
    assert f.tags.get("TIT3") == [] and f.tags.get("TIT1") == []


def test_an_absurd_tag_size_reads_no_tag(tmp_path) -> None:
    f = read(tmp_path, "a.mp3", b"ID3\x04\x00\x00\x7f\x7f\x7f\x7f" + mw.silent_mp3())
    assert f is not None and f.tags is None


# ─── MPEG length ─────────────────────────────────────────────────────────


def test_constant_bitrate_length_from_the_size(tmp_path) -> None:
    f = mp3(tmp_path, [mw.text_frame("TIT2", "x")], audio=mw.silent_mp3(1000))
    assert f.duration == pytest.approx(1000 * 417 * 8 / 128000, abs=1e-6)


def test_xing_frame_count_gives_the_length(tmp_path) -> None:
    first = bytearray(mw.silent_mp3(1))
    first[4 + 17:4 + 17 + 12] = b"Xing" + struct.pack(">II", 1, 9000)    # mono MPEG-1: 17 bytes of side info
    f = read(tmp_path, "a.mp3", bytes(first) + mw.silent_mp3(20))
    assert f.duration == pytest.approx(9000 * 1152 / 44100)


def test_vbri_frame_count_gives_the_length(tmp_path) -> None:
    first = bytearray(mw.silent_mp3(1))
    first[36:54] = b"VBRI" + struct.pack(">HHHII", 1, 0, 75, 0, 2000)
    f = read(tmp_path, "a.mp3", bytes(first) + mw.silent_mp3(4))
    assert f.duration == pytest.approx(2000 * 1152 / 44100)


def test_a_false_sync_before_the_audio_is_skipped(tmp_path) -> None:
    junk = b"\xff\xfb\x90" + b"\x01" * 50                                # a header the next frame doesn't confirm
    f = read(tmp_path, "a.mp3", junk + mw.silent_mp3(30))
    assert f.duration == pytest.approx(30 * 417 * 8 / 128000, abs=1e-6)


# ─── FLAC ────────────────────────────────────────────────────────────────


def test_flac_comments_pictures_and_length(tmp_path) -> None:
    f = read(tmp_path, "a.flac", mw.flac_file([("TITLE", "Flac"), ("artist", "Lower"), ("Title", "Second Title")],
                                              [(4, "image/png", PNG), (3, "image/jpeg", JPEG)],
                                              rate=22050, samples=33075))
    assert f.kind == "flac" and f.duration == pytest.approx(1.5)
    assert f.tags.kind == "vorbis"
    assert f.tags.get("title") == ["Flac", "Second Title"]       # case-insensitive, in order
    assert f.tags.first("ARTIST") == "Lower"
    assert [(p.type, p.mime, p.data) for p in f.pictures] == [(4, "image/png", PNG), (3, "image/jpeg", JPEG)]
    assert f.tags.get("\xa9nam") == [] and "\xa9nam" not in f.tags   # a name Vorbis can't hold: absent


def test_flac_after_an_id3_tag(tmp_path) -> None:
    data = mw.flac_file({"TITLE": ["Underneath"]}, id3_prefix=mw.id3_tag([mw.text_frame("TIT2", "On Top")]))
    f = read(tmp_path, "a.flac", data)
    assert f.kind == "flac" and f.tags.first("title") == "Underneath"


@pytest.mark.parametrize("data", [
    b"fLaC" + b"\xff" * 64,                                              # block type 127
    b"fLaC" + b"\x84\x00\x00\x10" + b"\x00" * 16,                         # first block is not STREAMINFO
    mw.flac_file({"TITLE": ["Cut"]}, [(3, "image/jpeg", JPEG)])[:-40],    # file ends inside a block
], ids=["invalid-type", "no-streaminfo", "truncated"])
def test_broken_flac_raises(tmp_path, data) -> None:
    with pytest.raises(TagError):
        read(tmp_path, "a.flac", data)


def test_flac_picture_block_parsing() -> None:
    pic = media_tags.parse_picture(mw.flac_picture(3, "image/png", PNG, "front"))
    assert (pic.type, pic.mime, pic.desc, pic.data) == (3, "image/png", "front", PNG)
    with pytest.raises(TagError):
        media_tags.parse_picture(mw.flac_picture(3, "image/png", PNG)[:-5])


def test_vorbis_comment_fields_without_an_equals_sign_are_skipped() -> None:
    raw = struct.pack("<I", 1) + b"v" + struct.pack("<I", 2) + struct.pack("<I", 7) + b"NOEQUAL" + struct.pack("<I", 5) + b"A=b=c"
    tags = media_tags.parse_vorbis_comment(raw)
    assert tags.items == [("A", "b=c")] and tags.vendor == "v"


# ─── Ogg ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["opus", "vorbis"])
def test_ogg_comments_and_length(tmp_path, kind) -> None:
    f = read(tmp_path, f"a.{kind}", mw.ogg_file(kind, {"TITLE": ["Ogg"], "LYRICS": ["la"]},
                                               granule=48312 if kind == "opus" else 88200))
    assert f.kind == f"ogg_{kind}"
    assert f.tags.first("title") == "Ogg" and f.tags.get("lyrics") == ["la"]
    assert f.duration == pytest.approx(1.0 if kind == "opus" else 2.0)   # Opus: less the pre-skip


def test_a_comment_header_over_many_pages(tmp_path) -> None:
    big = "x" * 70_000
    f = read(tmp_path, "a.opus", mw.ogg_file("opus", [("TITLE", "Long"), ("LYRICS", big), ("ARTIST", "After")]))
    assert f.tags.get("LYRICS") == [big] and f.tags.first("ARTIST") == "After"


def test_the_writer_makes_valid_ogg_crcs() -> None:
    page = mw.ogg_stream([(b"hello", 0)])
    crc = struct.unpack_from("<I", page, 22)[0]
    assert mw.ogg_crc(page[:22] + b"\x00" * 4 + page[26:]) == crc != 0


def test_ogg_that_loses_sync_raises(tmp_path) -> None:
    data = mw.ogg_file("vorbis")
    with pytest.raises(TagError):
        read(tmp_path, "a.ogg", data[:60] + b"garbage" + data[60:])


# ─── MP4 ─────────────────────────────────────────────────────────────────


def test_mp4_items_and_length(tmp_path) -> None:
    items = [mw.mp4_text("\xa9nam", "Atom Title"), mw.mp4_text("\xa9lyr", "line one", "line two"),
             (b"trkn", [mw.mp4_data(0, struct.pack(">HHHH", 0, 3, 12, 0))]),
             (b"tmpo", [mw.mp4_data(21, struct.pack(">H", 120))]),
             (b"----", [mw.atom(b"mean", b"\x00" * 4 + b"com.apple.iTunes"),
                        mw.atom(b"name", b"\x00" * 4 + b"MOOD"), mw.mp4_data(1, b"calm")]),
             mw.mp4_cover(PNG, png=True), mw.mp4_cover(JPEG)]
    f = read(tmp_path, "a.m4a", mw.m4a_file(items, timescale=1000, duration=61_250))
    assert f.kind == "mp4" and f.duration == pytest.approx(61.25)
    assert f.tags.first("\xa9nam") == "Atom Title"
    assert f.tags.get("\xa9lyr") == ["line one", "line two"]
    assert f.tags.get("trkn") == [(3, 12)] and f.tags.get("tmpo") == [120]
    assert f.tags.get("----:com.apple.iTunes:MOOD") == [b"calm"]
    assert f.tags.get("covr") == [PNG, JPEG]
    assert f.tags.get("TIT2") == []


def test_mp4_without_tags_and_with_a_version_1_mdhd(tmp_path) -> None:
    f = read(tmp_path, "a.m4a", mw.bare_m4a())
    assert (f.tags, f.duration) == (None, 1.0)
    mdhd = mw.full_atom(b"mdhd", 1 << 24, struct.pack(">QQIQHH", 0, 0, 1000, 5_000_000, 0, 0))
    hdlr = mw.full_atom(b"hdlr", 0, b"\x00" * 4 + b"soun" + b"\x00" * 13)
    trak = mw.atom(b"trak", mw.atom(b"mdia", mdhd + hdlr))
    data = mw.atom(b"ftyp", b"M4B ") + mw.atom(b"moov", trak)
    assert read(tmp_path, "b.m4b", data).duration == pytest.approx(5000.0)


def test_mp4_box_running_past_its_parent_raises(tmp_path) -> None:
    data = mw.atom(b"ftyp", b"M4A ") + struct.pack(">I", 4000) + b"moov" + b"\x00" * 20
    with pytest.raises(TagError):
        read(tmp_path, "a.m4a", data)


# ─── ASF, APEv2, WAV, AIFF, AAC ──────────────────────────────────────────


def test_asf_attributes_and_length(tmp_path) -> None:
    pic = mw.asf_picture(3, "image/jpeg", JPEG, "Front")
    f = read(tmp_path, "a.wma", mw.asf_file(
        title="Asf Title", author="Asf Author", play_100ns=50_000_000, preroll_ms=1500,
        extended=[("WM/AlbumTitle", 0, mw.asf_text("Album")), ("WM/Picture", 1, pic),
                  ("WM/TrackNumber", 3, struct.pack("<I", 7)), ("IsVBR", 2, struct.pack("<I", 1))]))
    assert f.kind == "asf" and f.duration == pytest.approx(3.5)
    assert (f.tags.first("Title"), f.tags.first("Author"), f.tags.first("WM/AlbumTitle")) == ("Asf Title", "Asf Author", "Album")
    assert [v.value for v in f.tags.get("WM/Picture")] == [pic]
    assert [v.value for v in f.tags.get("WM/TrackNumber")] == [7]
    assert [v.value for v in f.tags.get("IsVBR")] == [True]


def test_ape_tags_at_the_end_before_id3v1_and_at_the_start(tmp_path) -> None:
    tag = mw.ape_tag([("Title", "Ape"), ("Lyrics", "one\x00two"), ("Cover Art (Front)", b"bin")])
    f = read(tmp_path, "a.wv", b"wvpk" + b"\x00" * 60 + tag)
    assert f.kind == "ape" and f.tags.first("TITLE") == "Ape"
    assert f.tags.get("lyrics") == ["one", "two"]
    assert f.tags.item("Cover Art (Front)").kind == 1
    g = read(tmp_path, "b.ape", b"MAC " + b"\x00" * 60 + tag + mw.id3v1(title="v1"))
    assert g.tags.first("Title") == "Ape"
    h = read(tmp_path, "c.ape", mw.ape_tag([("Album", "First Thing")], header=True))
    assert h.tags.first("album") == "First Thing"


@pytest.mark.parametrize("chunk", [b"id3 ", b"ID3 "])
def test_wav_length_and_id3_chunk(tmp_path, chunk) -> None:
    f = read(tmp_path, "a.wav", mw.wav_file(seconds=1.5, id3=mw.id3_tag([mw.text_frame("TIT2", "Wave")], version=3),
                                            chunk_id=chunk))
    assert f.kind == "wav" and f.duration == pytest.approx(1.5)
    assert f.tags.first("TIT2") == "Wave"
    plain = read(tmp_path, "b.wav", mw.wav_file(seconds=0.25))
    assert plain.tags is None and plain.duration == pytest.approx(0.25)


def test_aiff_length_and_id3_chunk(tmp_path) -> None:
    def ext80(v: float) -> bytes:                                # 80-bit IEEE extended, for COMM
        import math
        m, e = math.frexp(v)
        return struct.pack(">HQ", e + 16382, int(m * (1 << 64)))
    comm = struct.pack(">hIh", 1, 16000, 16) + ext80(8000.0)
    id3 = mw.id3_tag([mw.text_frame("TIT2", "Aiff")])
    chunks = b"COMM" + struct.pack(">I", len(comm)) + comm + b"ID3 " + struct.pack(">I", len(id3)) + id3
    data = b"FORM" + struct.pack(">I", 4 + len(chunks)) + b"AIFF" + chunks
    f = read(tmp_path, "a.aiff", data)
    assert f.kind == "aiff" and f.duration == pytest.approx(2.0) and f.tags.first("TIT2") == "Aiff"


def test_adts_length_and_its_id3_tag(tmp_path) -> None:
    def frame(length: int) -> bytes:                             # 44.1 kHz (index 4), one raw block
        return bytes([0xFF, 0xF1, 0x50, 0x80 | ((length >> 11) & 3), (length >> 3) & 0xFF,
                      ((length & 7) << 5) | 0x1F, 0xFC]) + b"\x00" * (length - 7)
    stream = b"".join(frame(200) for _ in range(431))
    f = read(tmp_path, "a.aac", mw.id3_tag([mw.text_frame("TIT2", "Raw AAC")]) + stream)
    assert f.kind == "aac" and f.duration == pytest.approx(431 * 1024 / 44100)
    assert f.tags.first("TIT2") == "Raw AAC"


# ─── what is not audio ───────────────────────────────────────────────────


@pytest.mark.parametrize("name,data", [("a.mp3", b""), ("a.mp3", b"not audio " * 20), ("a.txt", b"hello"),
                                       ("a.flac", b"\x00" * 100)])
def test_not_audio_reads_as_none(tmp_path, name, data) -> None:
    assert read(tmp_path, name, data) is None


def test_an_mpeg_frame_deep_inside_another_file_is_not_an_mp3(tmp_path) -> None:
    data = b"\x00" * 5000 + mw.silent_mp3(10)
    assert read(tmp_path, "a.bin", data) is None
    assert read(tmp_path, "a.mp3", data).kind == "mp3"           # named as MPEG audio: searched for
    assert read(tmp_path, "b.bin", mw.silent_mp3(10)).kind == "mp3"   # starting with a frame: found


def test_tags_on_an_mp3_without_audio_frames_still_read(tmp_path) -> None:
    f = mp3(tmp_path, [mw.text_frame("TIT2", "Tag Only")], audio=b"")
    assert (f.kind, f.duration, f.tags.first("TIT2")) == ("mp3", None, "Tag Only")


def test_a_missing_file_raises_oserror(tmp_path) -> None:
    with pytest.raises(OSError):
        media_tags.read(tmp_path / "missing.mp3")


def test_read_id3_reads_only_the_leading_tag(tmp_path) -> None:
    p = tmp_path / "a.bin"
    p.write_bytes(mw.id3_tag([mw.text_frame("TIT2", "Leading")]) + b"anything at all")
    assert media_tags.read_id3(p).first("TIT2") == "Leading"
    p.write_bytes(b"no tag")
    assert media_tags.read_id3(p) is None


# ─── Tags.first ──────────────────────────────────────────────────────────


def test_first_tries_names_in_order_and_skips_blanks() -> None:
    tags = media_tags.VorbisTags([("TITLE", "  "), ("ARTIST", ""), ("ALBUMARTIST", " Band ")])
    assert tags.first("title", "artist", "albumartist") == "Band"
    assert tags.first("\xa9ART", "TPE1") is None
    assert media_tags.MP4Tags([("covr", b"\xff")]).first("\xa9nam") is None


def test_the_reader_never_imports_a_tagging_library() -> None:
    import sys

    for name in list(sys.modules):
        if name == "mutagen" or name.startswith("mutagen."):
            pytest.skip("something else in this process imported mutagen")
    media_tags.read_fileobj(__import__("io").BytesIO(mw.flac_file({"TITLE": ["x"]})), "a.flac")
    assert not any(n == "mutagen" or n.startswith("mutagen.") for n in sys.modules)

