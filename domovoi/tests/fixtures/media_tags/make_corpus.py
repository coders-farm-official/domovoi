"""Regenerate the tag-reader corpus beside this file.

    python domovoi/tests/fixtures/media_tags/make_corpus.py

Two kinds of file. ``ff_*`` are made by FFmpeg (8.x, on PATH): real
encoders (LAME, libvorbis, libopus, FLAC, AAC, WMA) and FFmpeg's own tag
writers, so the reader meets files it had no hand in. ``mw_*`` are made by
the tests' own writer (``domovoi/tests/media_writer.py``) for what FFmpeg
never writes: ID3v2.2, unsynchronisation, compressed frames, extended
headers, footers, iTunes-style plain frame sizes, ID3v1 alongside v2,
SYLT, APEv2, WM/Picture, a comment spread over several Ogg pages, broken
files. ``expected.json`` holds what the mutagen-based readers of e4dffc1
(the last commit with mutagen) answered for each file, recorded once with
the corpus, and the reason wherever the new reader answers otherwise;
``test_media_tags_corpus.py`` holds the code to it. Regenerating changes
the files (FFmpeg stamps its version), so re-record the answers after it
(``python -m domovoi.tests.test_media_tags_corpus --record``) and review
the diff: an answer that moved is a behaviour change.
"""

from __future__ import annotations

import base64
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from domovoi.tests import media_writer as mw  # noqa: E402

FF = shutil.which("ffmpeg")
LYRICS = "first line of the lantern song\nsecond line by the river door"
LRC = "[00:00.50]first line of the lantern song\n[00:01.50]second line by the river door\n"


def ff(*args: str) -> None:
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def tone(seconds: float, rate: int = 22050) -> list[str]:
    return ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate={rate}"]


def meta(**kv: str) -> list[str]:
    out: list[str] = []
    for k, v in kv.items():
        out += ["-metadata", f"{k}={v}"]
    return out


def main() -> None:
    if FF is None:
        raise SystemExit("ffmpeg is not on PATH")
    for old in HERE.glob("ff_*"):
        old.unlink()
    for old in HERE.glob("mw_*"):
        old.unlink()
    tmp = Path(tempfile.mkdtemp())
    jpg, png = tmp / "front.jpg", tmp / "back.png"
    ff("-f", "lavfi", "-i", "color=c=red:s=8x8", "-frames:v", "1", str(jpg))
    ff("-f", "lavfi", "-i", "color=c=blue:s=8x8", "-frames:v", "1", str(png))
    chapters = tmp / "chapters.txt"
    chapters.write_text(";FFMETADATA1\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1200\ntitle=Opening\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1200\nEND=2600\ntitle=The Middle Bit\n",
                        encoding="utf-8")
    tags = dict(title="Lantern Song", artist="The Example Band", album="River Door",
                album_artist="Example Collective", composer="A. Writer", track="3/9", date="2024")

    # ── FFmpeg: MP3 ──────────────────────────────────────────────────
    for v in (3, 4):
        ff(*tone(2.6), "-i", str(jpg), "-i", str(png), "-i", str(chapters),
           "-map", "0:a", "-map", "1:v", "-map", "2:v", "-map_chapters", "3",
           "-c:a", "libmp3lame", "-b:a", "64k", "-c:v", "copy",
           "-disposition:v:0", "attached_pic", "-disposition:v:1", "attached_pic",
           "-metadata:s:v:0", "comment=Cover (front)", "-metadata:s:v:1", "comment=Cover (back)",
           *meta(**tags, lyrics=LYRICS), "-id3v2_version", str(v), str(HERE / f"ff_id3v2{v}.mp3"))
    ff(*tone(1.4), "-c:a", "libmp3lame", "-b:a", "32k", *meta(title="Only In Version One", artist="Old Tagger"),
       "-id3v2_version", "0", "-write_id3v1", "1", str(HERE / "ff_id3v1_only.mp3"))
    ff(*tone(3.2), "-c:a", "libmp3lame", "-b:a", "48k", "-write_xing", "0",
       *meta(title="No Xing Header", artist="Constant Rate"), str(HERE / "ff_cbr_no_xing.mp3"))
    ff(*tone(2.2), "-c:a", "libmp3lame", "-q:a", "6", *meta(title="Variable Rate", artist="VBR"),
       str(HERE / "ff_vbr.mp3"))
    ff(*tone(1.0), "-c:a", "libmp3lame", "-b:a", "32k", *meta(title="Ünïcödé ✓ 中文", artist="Ärtist"),
       "-id3v2_version", "3", str(HERE / "ff_unicode_v23.mp3"))

    # ── FFmpeg: FLAC, Ogg, MP4, WMA, WAV, AIFF, ADTS ─────────────────
    ff(*tone(2.0), "-i", str(jpg), "-map", "0:a", "-map", "1:v", "-c:a", "flac", "-c:v", "copy",
       "-disposition:v", "attached_pic", "-metadata:s:v", "comment=Cover (front)",
       *meta(**tags, lyrics=LRC), str(HERE / "ff_tagged.flac"))
    block = base64.b64encode(mw.flac_picture(3, "image/jpeg", jpg.read_bytes())).decode()
    for ext, codec in (("ogg", "libvorbis"), ("opus", "libopus")):
        ff(*tone(1.8, 48000), "-c:a", codec, *meta(**tags, lyrics=LYRICS, METADATA_BLOCK_PICTURE=block),
           str(HERE / f"ff_tagged.{ext}"))
    ff(*tone(1.5), "-c:a", "flac", "-f", "ogg", *meta(title="FLAC In Ogg", artist="Xiph"),
       str(HERE / "ff_flac_in_ogg.oga"))
    for ext in ("m4a", "m4b"):
        ff(*tone(2.4), "-i", str(jpg), "-i", str(chapters), "-map", "0:a", "-map", "1:v",
           "-map_chapters", "2", "-c:a", "aac", "-b:a", "48k", "-c:v", "copy",
           "-disposition:v", "attached_pic",
           *meta(**tags, lyrics=LYRICS), str(HERE / f"ff_tagged.{ext}"))
    ff(*tone(2.1), "-c:a", "wmav2", "-b:a", "32k", *meta(**tags), str(HERE / "ff_tagged.wma"))
    ff(*tone(0.7, 8000), "-c:a", "pcm_s16le", *meta(title="Wave Title", artist="Wave Artist"),
       "-write_id3v2", "1", str(HERE / "ff_id3.wav"))
    ff(*tone(0.6, 8000), "-c:a", "pcm_s16le", *meta(title="Plain Wave"), str(HERE / "ff_info.wav"))
    ff(*tone(0.9, 8000), "-c:a", "pcm_s16be", *meta(title="Aiff Title", artist="Aiff Artist"),
       "-write_id3v2", "1", str(HERE / "ff_id3.aiff"))
    ff(*tone(2.3), "-c:a", "aac", "-b:a", "32k", "-f", "adts", str(HERE / "ff_raw.aac"))
    ff(*tone(1.1), "-c:a", "aac", "-b:a", "32k", "-f", "adts", "-write_id3v2", "1",
       *meta(title="ADTS With ID3", artist="Raw"), str(HERE / "ff_id3.aac"))

    # ── The tests' writer: what FFmpeg doesn't write ─────────────────
    audio = mw.silent_mp3(12)
    pic = jpg.read_bytes()
    pic_ff = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 2 + b"\xff\x00\xff\xe1"   # 0xFF bytes to unsynchronise

    def mp3(name: str, frames, **kw) -> None:
        kw.setdefault("audio", audio)
        mw.write_mp3(HERE / name, frames, **kw)

    mp3("mw_id3v22.mp3", [mw.text_frame("TIT2", "Two Point Two", encoding=0),
                          mw.text_frame("TPE1", "Old iTunes", encoding=1),
                          mw.text_frame("TALB", "Legacy", encoding=0),
                          mw.APIC(encoding=0, mime="image/jpeg", type=3, desc="", data=pic),
                          mw.USLT(encoding=0, lang="eng", desc="", text=LYRICS)], version=2)
    mp3("mw_v23_unsync_ext.mp3", [mw.text_frame("TIT2", "Unsynchronised", encoding=1),
                                  mw.text_frame("TPE1", "Tag Wide", encoding=0),
                                  mw.APIC(encoding=0, mime="image/jpeg", type=3, desc="front", data=pic_ff),
                                  mw.USLT(encoding=1, lang="eng", desc="", text=LYRICS)],
        version=3, unsync=True, ext_header=True)
    mp3("mw_v23_compressed.mp3", [mw.Frame("TIT2", (0, ["Squeezed Title"]), flags=0x08),
                                  mw.text_frame("TPE1", "Zlib", encoding=0),
                                  mw.Frame("USLT", (0, "eng", "", LYRICS * 4), flags=0x08)], version=3)
    mp3("mw_v24_frame_unsync.mp3", [mw.text_frame("TIT2", "Per Frame"),
                                    mw.Frame("APIC", (3, "image/jpeg", 3, "", pic_ff), flags=0x02),
                                    mw.Frame("TPE1", (3, ["Compressed Artist"]), flags=0x08)],
        version=4, footer=True)
    mp3("mw_v24_tag_unsync.mp3", [mw.text_frame("TIT2", "Whole Tag Flag"),
                                  mw.APIC(encoding=3, mime="image/png", type=0, desc="", data=pic_ff)],
        version=4, unsync=True, ext_header=True)
    mp3("mw_v24_plain_sizes.mp3", [mw.text_frame("TIT2", "Plain Sizes " + "x" * 200),
                                   mw.text_frame("TPE1", "iTunes Seven"),
                                   mw.USLT(encoding=3, lang="eng", desc="", text=LYRICS * 3)],
        version=4, plain_sizes=True)
    mp3("mw_v24_multi.mp3", [mw.text_frame("TIT2", "Two Artists"),
                             mw.text_frame("TPE1", "First Artist", "Second Artist"),
                             mw.text_frame("TPE2", "Band Of Both", encoding=2),
                             mw.text_frame("TALB", "Split", encoding=1),
                             mw.text_frame("TCOM", "", "Late Composer")], version=4)
    mp3("mw_v1_merge.mp3", [mw.text_frame("TIT2", "From Version Two")],
        v1=mw.id3v1(title="From Version One", artist="V1 Artist", album="V1 Album", track=7))
    mp3("mw_sylt.mp3", [mw.SYLT(encoding=1, lang="eng", format=2, type=1, desc="",
                                text=[("first line", 500), ("\nsecond", 1500), (" line", 1800)]),
                        mw.SYLT(encoding=3, lang="eng", format=1, type=1, desc="frames", text=[("x", 1)]),
                        mw.USLT(encoding=3, lang="deu", desc="", text="nicht englisch"),
                        mw.USLT(encoding=3, lang="eng", desc="", text=LRC)])
    mp3("mw_chapters_v24.mp3", [mw.text_frame("TIT2", "Episode 12"),
                                mw.CHAP(element_id="c1", start_time=61500, end_time=120000,
                                        sub_frames=[mw.TIT2("Second")]),
                                mw.CHAP(element_id="c0", start_time=0, end_time=61500,
                                        sub_frames=[mw.TIT2("Cold Open")]),
                                mw.CHAP(element_id="c2", start_time=120000, end_time=180000)])
    mp3("mw_chapters_v23.mp3", [mw.CHAP(element_id="a", start_time=2000, end_time=5000,
                                        sub_frames=[mw.TIT2("Only One", encoding=1)])], version=3)
    mp3("mw_pictures_order.mp3", [mw.APIC(encoding=3, mime="image/png", type=4, desc="back", data=png.read_bytes()),
                                  mw.APIC(encoding=3, mime="", type=0, desc="other", data=pic),
                                  mw.APIC(encoding=1, mime="image/jpeg", type=3, desc="front", data=pic)])
    mp3("mw_tag_only.mp3", [mw.text_frame("TIT2", "No Audio Frames")], audio=b"")
    (HERE / "mw_empty.mp3").write_bytes(b"")
    (HERE / "mw_junk.mp3").write_bytes(b"this is not audio at all" * 10)
    (HERE / "mw_truncated.flac").write_bytes(mw.flac_file({"TITLE": ["Cut"]}, [(3, "image/jpeg", pic)])[:-40])

    (HERE / "mw_id3_prefixed.flac").write_bytes(
        mw.flac_file({"TITLE": ["Flac Under Id3"], "ARTIST": ["Hidden"]}, samples=88200,
                     id3_prefix=mw.id3_tag([mw.text_frame("TIT2", "The ID3 Title")])))
    (HERE / "mw_vorbis_keys.flac").write_bytes(
        mw.flac_file([("Title", "Mixed Case Keys"), ("ALBUMARTIST", "Only Album Artist"),
                      ("unsyncedlyrics", LYRICS), ("SYNCEDLYRICS", LRC), ("COVERART", base64.b64encode(pic).decode())],
                     [(4, "image/png", png.read_bytes()), (3, "image/jpeg", pic)], samples=33075, rate=22050))
    (HERE / "mw_long_comment.opus").write_bytes(
        mw.ogg_file("opus", [("TITLE", "Many Pages"), ("LYRICS", (LYRICS + "\n") * 900)], granule=96312))
    (HERE / "mw_items.m4a").write_bytes(mw.m4a_file(
        [mw.mp4_text("\xa9nam", "Atom Title"), mw.mp4_text("aART", "Album Artist Atom"),
         mw.mp4_text("\xa9alb", "Atom Album"), mw.mp4_text("\xa9wrt", "Atom Writer"),
         mw.mp4_text("\xa9nrt", "Atom Narrator"),
         (b"trkn", [mw.mp4_data(0, struct.pack(">HHHH", 0, 4, 12, 0))]),
         (b"----", [mw.atom(b"mean", b"\x00" * 4 + b"com.apple.iTunes"), mw.atom(b"name", b"\x00" * 4 + b"MOOD"),
                    mw.mp4_data(1, b"calm")]),
         mw.mp4_cover(png.read_bytes(), png=True), mw.mp4_cover(pic)], timescale=1000, duration=125500))
    (HERE / "mw_asf.wma").write_bytes(mw.asf_file(
        title="Asf Title", author="Asf Author", play_100ns=45_000_000, preroll_ms=3000,
        extended=[("WM/AlbumTitle", 0, mw.asf_text("Asf Album")), ("WM/AlbumArtist", 0, mw.asf_text("Asf Band")),
                  ("WM/Lyrics", 0, mw.asf_text(LYRICS)), ("WM/TrackNumber", 3, struct.pack("<I", 5)),
                  ("WM/Picture", 1, mw.asf_picture(3, "image/jpeg", pic, "Front"))]))
    (HERE / "mw_wavpack.wv").write_bytes(b"wvpk" + b"\x00" * 60 + mw.ape_tag(
        [("Title", "Ape Title"), ("Artist", "Ape Artist"), ("Lyrics", LRC)]))
    (HERE / "mw_ape_header.ape").write_bytes(mw.ape_tag([("Title", "Header First"), ("Album", "Ape Album")], header=True))
    (HERE / "mw_id3.wav").write_bytes(mw.wav_file(seconds=1.25, id3=mw.id3_tag(
        [mw.text_frame("TIT2", "Chunk Title"), mw.text_frame("TPE1", "Chunk Artist"),
         mw.USLT(encoding=3, lang="eng", desc="", text=LYRICS)], version=3), chunk_id=b"ID3 "))
    shutil.rmtree(tmp)
    for p in sorted(HERE.glob("ff_*")) + sorted(HERE.glob("mw_*")):
        print(f"{p.stat().st_size:8d}  {p.name}")


if __name__ == "__main__":
    main()
