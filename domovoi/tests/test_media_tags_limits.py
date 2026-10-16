"""What a crafted audio file may cost the tag reader (domovoi.media_tags):
the 2026-10-16 delta security review's proofs, as regressions.

B1  an ID3 frame compressed 1000:1 was inflated whole (1 MiB on disk, 2 GiB
    in memory); now no frame inflates past INFLATE_FRAME_MAX, no tag past
    INFLATE_TAG_MAX, and a frame the reader doesn't interpret isn't
    inflated at all.
B2  an Ogg comment packet was grown 255 bytes at a time (10 MB: 36 s); now
    it is joined once, and capped.
S2  no per-read memory budget: nested chapters copied the tag per level,
    zero-length frames made millions of objects, the Ogg tail read widened
    to the whole file, FLAC kept every picture; now one budget per read
    (bytes, items, steps) and a bounded tail scan.

Memory is measured with tracemalloc (every Python allocation, zlib's
included), time with perf_counter, each against a limit far below what the
review measured and far above what the fixed reader needs. DB-free.
"""

from __future__ import annotations

import importlib
import struct
import time
import tracemalloc
import zlib
from pathlib import Path

import pytest

from domovoi import media_tags
from domovoi.media_tags import _aac, _id3, _ogg, _util
from domovoi.tests import media_writer as mw

# The package's own helper named _flac hides the module of that name.
_flac = importlib.import_module("domovoi.media_tags._flac")

MiB = 1024 * 1024
MPEG_FRAME = b"\xff\xfb\x90\x00" + b"\0" * 413


def _syncsafe4(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _deflate(prefix: bytes, fill: bytes, nbytes: int) -> bytes:
    """``prefix`` + ``nbytes`` of ``fill``, compressed without ever holding
    the inflated bytes."""
    co = zlib.compressobj(9)
    parts = [co.compress(prefix)]
    chunk = fill * (4 * MiB // len(fill))
    left = nbytes
    while left > 0:
        n = min(left, len(chunk))
        parts.append(co.compress(chunk[:n]))
        left -= n
    parts.append(co.flush())
    return b"".join(parts)


def _v23_compressed(fid: bytes, inflated: bytes | None = None, *, prefix: bytes = b"\x00",
                    fill: bytes = b"A", nbytes: int = 0) -> bytes:
    """A v2.3 frame with the compression flag (0x80): the inflated size,
    then the zlib stream."""
    body = zlib.compress(inflated) if inflated is not None else _deflate(prefix, fill, nbytes)
    size = len(inflated) if inflated is not None else len(prefix) + nbytes
    body = size.to_bytes(4, "big") + body
    return fid + len(body).to_bytes(4, "big") + b"\x00\x80" + body


def _tag(version: int, frames: bytes) -> bytes:
    return b"ID3" + bytes([version, 0, 0]) + _syncsafe4(len(frames)) + frames


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _measured(fn, *args):
    """(result, peak bytes Python allocated during the call, seconds)."""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        t = time.perf_counter()
        out = fn(*args)
        dt = time.perf_counter() - t
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return out, peak, dt


# ─── B1: inflation ───────────────────────────────────────────────────────


def test_the_reviews_zlib_bomb_is_never_inflated(tmp_path) -> None:
    """The review's file: a frame of an id no reader interprets, claiming a
    GiB (here 256 MiB, enough to show it), compressed to a fraction of one.
    mutagen left such a frame alone; so does this reader now."""
    frames = b"".join(_v23_compressed(b"PRV" + bytes([0x30 + i]), nbytes=256 * MiB, fill=b"\0")
                      for i in range(3))
    p = _write(tmp_path, "bomb.mp3", _tag(3, frames) + MPEG_FRAME)
    assert p.stat().st_size < 1 * MiB
    for reader in (media_tags.read, media_tags.read_id3):
        out, peak, _ = _measured(reader, p)
        assert peak < 4 * MiB, f"{reader.__name__} held {peak / MiB:.0f} MiB"
    assert media_tags.read_id3(p).frames == []


@pytest.mark.parametrize("version", [3, 4])
def test_a_frame_the_reader_interprets_inflates_only_up_to_its_cap(tmp_path, version) -> None:
    """A compressed TIT2 (v2.3) or APIC (v2.4, with its data length
    indicator) of 256 MiB: what comes out is never more than the cap, and
    the frame is dropped; the frames around it still read."""
    if version == 3:
        bomb = _v23_compressed(b"TIT2", nbytes=256 * MiB)
    else:
        body = _deflate(b"\x00image/jpeg\x00\x03\x00\xff\xd8\xff", b"\0", 255 * MiB)
        body = _syncsafe4(255 * MiB) + body
        bomb = b"APIC" + _syncsafe4(len(body)) + b"\x00\x09" + body
    tag = mw.id3_tag([mw.text_frame("TPE1", "Before")], version=version, padding=0)[10:]
    tag += bomb + mw.id3_tag([mw.text_frame("TALB", "After")], version=version, padding=0)[10:]
    p = _write(tmp_path, "bomb.mp3", _tag(version, tag) + MPEG_FRAME)
    f, peak, dt = _measured(media_tags.read, p)
    assert peak < _id3.INFLATE_FRAME_MAX * 3, f"held {peak / MiB:.0f} MiB"
    assert dt < 5
    assert (f.tags.first("TPE1"), f.tags.first("TALB")) == ("Before", "After")
    assert f.tags.first("TIT2") is None and f.tags.getall("APIC") == []


def test_a_frame_of_exactly_the_cap_is_kept_and_one_byte_more_is_not(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_id3, "INFLATE_FRAME_MAX", 1000)
    exact = b"\x00" + b"x" * 999
    p = _write(tmp_path, "a.mp3", _tag(3, _v23_compressed(b"TIT2", exact)) + MPEG_FRAME)
    assert media_tags.read(p).tags.first("TIT2") == "x" * 999
    p = _write(tmp_path, "b.mp3", _tag(3, _v23_compressed(b"TIT2", exact + b"x")) + MPEG_FRAME)
    assert media_tags.read_id3(p).first("TIT2") is None


def test_a_tag_inflates_no_more_than_its_budget_in_all(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_id3, "INFLATE_TAG_MAX", 2500)
    frames = b"".join(_v23_compressed(fid, b"\x00" + fid.decode().encode() * 250)
                      for fid in (b"TIT2", b"TPE1", b"TALB"))      # 1001 bytes each
    tags = media_tags.read_id3(_write(tmp_path, "a.mp3", _tag(3, frames) + MPEG_FRAME))
    assert [f.id for f in tags.frames] == ["TIT2", "TPE1"]


def test_a_truncated_zlib_stream_is_still_dropped(tmp_path) -> None:
    """As ``zlib.decompress`` refused it: a stream with no end."""
    good = _v23_compressed(b"TIT2", b"\x00Whole")
    cut = good[:-3]
    cut = cut[:4] + (len(cut) - 10).to_bytes(4, "big") + cut[8:]
    tags = media_tags.read_id3(_write(tmp_path, "a.mp3", _tag(3, cut) + MPEG_FRAME))
    assert tags.first("TIT2") is None


# ─── S2: the rest of the ID3 budget ──────────────────────────────────────


def test_nested_chapters_are_not_read_and_cost_nothing(tmp_path) -> None:
    """The review's CHAP-in-CHAP file (400 levels round 4 MiB: 3.2 GiB, and
    mutagen 3.1 GiB): a chapter inside a chapter is not read, so nothing
    is copied per level. A real chapter list still reads."""
    def frame(fid: bytes, body: bytes) -> bytes:
        return fid + len(body).to_bytes(4, "big") + b"\x00\x00" + body

    inner = frame(b"PRIV", b"\x01" * (4 * MiB))
    for _ in range(400):
        inner = frame(b"CHAP", b"c\x00" + b"\x00" * 16 + inner)
    p = _write(tmp_path, "nest.mp3", _tag(3, inner))
    tags, peak, _ = _measured(media_tags.read_id3, p)
    assert peak < 3 * p.stat().st_size, f"held {peak / MiB:.0f} MiB"
    (chap,) = tags.getall("CHAP")
    assert chap.sub_frames == []

    flat = mw.write_mp3(tmp_path / "flat.mp3", [
        mw.CHAP(element_id="a", start_time=0, end_time=1000, sub_frames=[mw.TIT2("One")]),
        mw.CHAP(element_id="b", start_time=1000, end_time=2000, sub_frames=[mw.TIT2("Two")])], version=3)
    assert [c.sub_frames[0].text for c in media_tags.read_id3(flat).getall("CHAP")] == [["One"], ["Two"]]


def test_millions_of_empty_frames_make_no_more_than_the_item_budget(tmp_path) -> None:
    """The review's 32 MiB tag of zero-length frames made 3.3 million
    objects (786 MiB); the walk now stops at MAX_ITEMS and keeps those."""
    n = 2 * MiB // 10
    first = mw.id3_tag([mw.text_frame("TIT2", "First")], version=3, padding=0)[10:]
    p = _write(tmp_path, "tiny.mp3", _tag(3, first + (b"PRIV" + bytes(6)) * n) + MPEG_FRAME)
    f, peak, dt = _measured(media_tags.read, p)
    assert len(f.tags.frames) <= _util.MAX_ITEMS
    assert f.tags.first("TIT2") == "First"
    assert peak < 3 * p.stat().st_size + 8 * MiB, f"held {peak / MiB:.0f} MiB"
    assert dt < 5


def test_a_text_frame_of_a_million_values_is_dropped(tmp_path) -> None:
    body = b"\x00" + b"a\x00" * 1_000_000
    frames = b"TPE1" + len(body).to_bytes(4, "big") + b"\x00\x00" + body
    frames += mw.id3_tag([mw.text_frame("TIT2", "Kept")], version=3, padding=0)[10:]
    tags, peak, _ = _measured(media_tags.read_id3, _write(tmp_path, "a.mp3", _tag(3, frames)))
    assert tags.first("TPE1") is None
    assert tags.first("TIT2") == "Kept"          # it didn't use up the read's budget
    assert peak < 16 * MiB


def test_synced_lyrics_entries_count_against_the_items(tmp_path) -> None:
    entry = b"x\x00" + (10).to_bytes(4, "big")
    body = b"\x00eng\x02\x01\x00" + entry * 100_000
    frames = b"SYLT" + len(body).to_bytes(4, "big") + b"\x00\x00" + body
    tags = media_tags.read_id3(_write(tmp_path, "a.mp3", _tag(3, frames)))
    assert tags.getall("SYLT") == []                 # over the budget: dropped
    small = b"\x00eng\x02\x01\x00" + entry * 50
    frames = b"SYLT" + len(small).to_bytes(4, "big") + b"\x00\x00" + small
    (sylt,) = media_tags.read_id3(_write(tmp_path, "b.mp3", _tag(3, frames))).getall("SYLT")
    assert len(sylt.text) == 50


# ─── B2 and S2: Ogg ──────────────────────────────────────────────────────


def _ogg_page(flags: int, seq: int, lacing: list[int], data: bytes, granule: int = 0, serial: int = 1) -> bytes:
    head = b"OggS\x00" + bytes([flags]) + struct.pack("<qIII", granule, serial, seq, 0) + bytes([len(lacing)])
    return head + bytes(lacing) + data


def _big_comment_ogg(pages: int) -> bytes:
    """The review's p4 file: a Vorbis comment packet over ``pages`` full
    pages (65025 bytes each), the stream's length on the last page."""
    ident = b"\x01vorbis" + struct.pack("<IBI", 0, 2, 44100) + b"\x00" * 15
    out = [_ogg_page(0x02, 0, [len(ident)], ident)]
    start = b"\x03vorbis" + struct.pack("<II", 0, 0)
    for i in range(pages):
        body = (start if i == 0 else b"")
        out.append(_ogg_page(0x01 if i else 0x00, i + 1, [255] * 255, body + b"A" * (65025 - len(body))))
    out.append(_ogg_page(0x01, pages + 1, [0], b"", granule=44100 * 60))
    return b"".join(out)


def test_a_comment_packet_over_160_pages_reads_in_linear_time(tmp_path) -> None:
    """10 MB took 35.6 s (and stalled the core's loop past its watchdog at
    every boot); mutagen read it in 0.05 s."""
    p = _write(tmp_path, "big.ogg", _big_comment_ogg(160))
    f, peak, dt = _measured(media_tags.read, p)
    assert (f.kind, f.duration) == ("ogg_vorbis", 60.0)
    assert dt < 2, f"{dt:.1f} s"
    assert peak < 4 * p.stat().st_size, f"held {peak / MiB:.0f} MiB"


def test_a_header_packet_over_the_block_cap_is_a_broken_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_ogg, "MAX_BLOCK", 2 * 65025)
    p = _write(tmp_path, "big.ogg", _big_comment_ogg(3))
    with pytest.raises(media_tags.TagError):
        media_tags.read(p)
    assert media_tags.read(_write(tmp_path, "ok.ogg", _big_comment_ogg(1))).duration == 60.0


def test_the_last_granule_is_found_without_reading_the_file_whole(tmp_path) -> None:
    """The review's p5 file: its only granule early, then 48 MiB that are
    not Ogg; the tail search had widened until it held all of it (512 MiB
    file: 784 MiB). Now it reads backwards a chunk at a time."""
    ident = b"\x01vorbis" + struct.pack("<IBI", 0, 2, 44100) + b"\x00" * 15
    comment = b"\x03vorbis" + struct.pack("<II", 0, 0)
    p = tmp_path / "tail.ogg"
    with p.open("wb") as fh:
        fh.write(_ogg_page(0x02, 0, [len(ident)], ident))
        fh.write(_ogg_page(0x00, 1, [len(comment)], comment, granule=44100 * 3))
        chunk = b"\0" * MiB
        for _ in range(48):
            fh.write(chunk)
    f, peak, dt = _measured(media_tags.read, p)
    assert f.duration == 3.0
    assert peak < 2 * MiB, f"held {peak / MiB:.0f} MiB"


@pytest.mark.parametrize("shift", range(0, 30, 7))
def test_a_last_page_straddling_a_chunk_edge_is_still_found(tmp_path, monkeypatch, shift) -> None:
    monkeypatch.setattr(_ogg, "_TAIL", 64)
    data = mw.ogg_file("vorbis", {"TITLE": ["Edge"]}, granule=88200)
    p = _write(tmp_path, "a.ogg", data + b"\0" * shift)
    assert media_tags.read(p).duration == pytest.approx(2.0)


# ─── S2: the other containers ────────────────────────────────────────────


def test_flac_pictures_past_their_share_are_skipped_unread(tmp_path, monkeypatch) -> None:
    """The review's p5 FLAC (32 pictures of 16 MiB: 575 MiB held): pictures
    are kept only within PICTURES_BUDGET, in file order, and the comments
    after them still read."""
    monkeypatch.setattr(_flac, "PICTURES_BUDGET", 100_000)
    jpeg = b"\xff\xd8\xff" + b"\0" * 40_000
    blocks = [(0, mw.streaminfo(44100, 44100))]
    blocks += [(6, mw.flac_picture(t, "image/jpeg", jpeg)) for t in (3, 0, 4, 4, 4)]
    blocks += [(4, mw.vorbis_comment([("TITLE", "After The Pictures")]))]
    out = b"fLaC"
    for i, (btype, body) in enumerate(blocks):
        out += bytes([(0x80 if i == len(blocks) - 1 else 0) | btype]) + len(body).to_bytes(3, "big") + body
    f = media_tags.read(_write(tmp_path, "a.flac", out))
    assert [pic.type for pic in f.pictures] == [3, 0]
    assert f.tags.first("title") == "After The Pictures"


def test_the_flac_picture_share_is_real(tmp_path) -> None:
    """Six 12 MiB pictures, then the comments (written block by block)."""
    picture = mw.flac_picture(3, "image/jpeg", b"\xff\xd8\xff" + b"\0" * (12 * MiB))
    p = tmp_path / "big.flac"
    with p.open("wb") as fh:
        info = mw.streaminfo(44100, 44100)
        fh.write(b"fLaC" + bytes([0]) + len(info).to_bytes(3, "big") + info)
        for _ in range(6):
            fh.write(bytes([6]) + len(picture).to_bytes(3, "big") + picture)
        comment = mw.vorbis_comment([("TITLE", "Big Art")])
        fh.write(bytes([0x84]) + len(comment).to_bytes(3, "big") + comment)
    del picture
    f, peak, _ = _measured(media_tags.read, p)
    assert len(f.pictures) == _flac.PICTURES_BUDGET // (12 * MiB + 64) == 2
    assert f.tags.first("title") == "Big Art"
    assert peak < _flac.PICTURES_BUDGET * 2 + 16 * MiB, f"held {peak / MiB:.0f} MiB"


def _tiny_structures(ext: str, n: int = 300_000) -> bytes:
    """The review's p6 files: n boxes / chunks / metadata blocks of 4-8
    bytes each."""
    if ext == "m4a":
        return (struct.pack(">I4s", 16, b"ftyp") + b"M4A \0\0\0\0"
                + struct.pack(">I4s", 8 + n * 8, b"moov") + struct.pack(">I4s", 8, b"trak") * n)
    if ext == "wav":
        return b"RIFF" + struct.pack("<I", 4 + n * 8) + b"WAVE" + (b"junk" + b"\0\0\0\0") * n
    return (b"fLaC" + bytes([0]) + (34).to_bytes(3, "big") + bytes(10) + ((44100 << 44) | 1).to_bytes(8, "big")
            + bytes(16) + b"\x01\x00\x00\x00" * n + b"\x81\x00\x00\x00")


@pytest.mark.parametrize("ext", ["m4a", "wav", "flac"])
def test_a_file_of_endless_tiny_structures_is_a_broken_file(tmp_path, ext) -> None:
    with pytest.raises(_util.BudgetExceeded):
        media_tags.read(_write(tmp_path, f"a.{ext}", _tiny_structures(ext)))


def test_adts_frames_past_the_count_are_estimated(tmp_path, monkeypatch) -> None:
    frame = bytes([0xFF, 0xF1, 0x50, 0x80, 0x01, 0x3F, 0xFC]) + b"\0" * 2
    assert _aac.adts_header(frame)[1] == len(frame) == 9
    stream = frame * 4000
    exact = media_tags.read(_write(tmp_path, "a.aac", stream)).duration
    monkeypatch.setattr(_aac, "MAX_COUNTED_FRAMES", 100)
    assert media_tags.read(_write(tmp_path, "b.aac", stream)).duration == pytest.approx(exact, rel=0.01)


def test_an_asf_dword_is_four_bytes_whatever_it_claims(tmp_path) -> None:
    """The review's N2: a Title stored as a 2000-byte DWORD made a
    4800-digit number that str() refuses, and the track fell out of the
    library."""
    from domovoi.media_tags import _asf

    name = "Title".encode("utf-16-le") + b"\0\0"
    val = b"\xff" * 2000
    ext = struct.pack("<H", 1) + struct.pack("<H", len(name)) + name + struct.pack("<HH", 3, len(val)) + val
    obj = _asf.EXTENDED_CONTENT + struct.pack("<Q", 24 + len(ext)) + ext
    hdr = _asf.HEADER + struct.pack("<Q", 30 + len(obj)) + struct.pack("<IBB", 1, 1, 2) + obj
    f = media_tags.read(_write(tmp_path, "a.wma", hdr))
    assert f.tags.first("Title") == str(0xFFFFFFFF)


def test_one_read_keeps_to_one_budget(tmp_path, monkeypatch) -> None:
    """The budget is per read and shared by everything in it; the next
    read starts afresh."""
    monkeypatch.setattr(_util, "READ_BUDGET", 50_000)
    big = mw.write_mp3(tmp_path / "big.mp3", [mw.text_frame("TIT2", "x" * 60_000)])
    with pytest.raises(_util.BudgetExceeded):
        media_tags.read(big)
    small = mw.write_mp3(tmp_path / "small.mp3", [mw.text_frame("TIT2", "Small")])
    for _ in range(3):
        assert media_tags.read(small).tags.first("TIT2") == "Small"
    # A parser called on its own gets a budget of its own.
    assert media_tags.parse_vorbis_comment(mw.vorbis_comment([("A", "b")])).first("a") == "b"
