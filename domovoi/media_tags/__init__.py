"""Domovoi's own reader for the tags inside audio files.

Replaces mutagen (GPL-2.0-or-later) since 2026-10-15; the project takes
permissively licensed dependencies only (docs/CONTRIBUTING.md,
"Dependencies and licences"). Written from the format specifications, not
from any other library's code. It reads; it never writes a file.

What it covers is what Domovoi asks of a file — the library and audiobook
indexers' title / artist / album / length, the cover-art route's pictures,
the lyrics scan's lyrics, the podcast poller's chapters — across MP3 (ID3v2.2
to 2.4, ID3v1), FLAC, Ogg Vorbis / Opus / FLAC / Speex, MP4 / M4A / M4B,
WMA (ASF), WAV, AIFF, raw AAC (ADTS) and the APEv2-tagged formats.

    from domovoi import media_tags

    f = media_tags.read(path)       # None: not an audio file this knows
    f.duration                       # seconds, or None
    f.tags.first("TIT2", "title", "\\xa9nam", "Title")
    f.pictures                       # FLAC's PICTURE blocks

:func:`read` raises :class:`TagError` for a file of a known kind whose
structure is broken, and OSError when the file can't be read; every caller
catches both.

Every read keeps to a budget (``_util``: ``READ_BUDGET`` bytes taken into
memory, ``MAX_ITEMS`` things made, ``MAX_STEPS`` structures stepped over),
whatever the file says about itself: a file that asks for more is broken
(:class:`BudgetExceeded`, a TagError), or, where the rest can be left
unread (an ID3 tag's further frames, FLAC pictures past their share), is
cut short. Reads are blocking; the server runs them in worker threads
(``domovoi.executors``, the ``tags`` pool), never on its event loop.
"""

from __future__ import annotations

import os
from typing import BinaryIO

from domovoi.media_tags._ape import read_ape, read_ape_at_start
from domovoi.media_tags._asf import HEADER as _ASF_HEADER
from domovoi.media_tags._asf import read_asf
from domovoi.media_tags._aac import adts_header, adts_length
from domovoi.media_tags._flac import parse_picture, parse_vorbis_comment, read_flac
from domovoi.media_tags._id3 import (
    ChapterFrame,
    CommentFrame,
    Frame,
    ID3Tags,
    LyricsFrame,
    PictureFrame,
    RawFrame,
    SyncedLyricsFrame,
    TextFrame,
    UserTextFrame,
    merge_v1,
    parse_id3v2,
    read_id3v1,
    read_id3v2_at,
)
from domovoi.media_tags._model import (
    APETags,
    APEValue,
    ASFTags,
    ASFValue,
    MediaFile,
    MP4Tags,
    Picture,
    Tags,
    VorbisTags,
)
from domovoi.media_tags._mp4 import read_mp4
from domovoi.media_tags._mpeg import find_first_frame, stream_length
from domovoi.media_tags._ogg import read_ogg
from domovoi.media_tags._riff import read_aiff, read_wav
from domovoi.media_tags._util import BudgetExceeded, TagError, file_size, one_read

_MPEG_EXTS = (".mp3", ".mp2", ".mpg", ".mpeg")
_AAC_EXTS = (".aac", ".aacp", ".adts", ".adif")
_MP4_TYPES = (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot")
# Formats whose tags are APEv2 at the end: Monkey's Audio, WavPack,
# Musepack (SV7, SV8), OptimFROG, TAK.
_APE_MAGIC = (b"MAC ", b"wvpk", b"MPCK", b"MP+", b"OFR ", b"tBaK")


def read(path: str | os.PathLike[str]) -> MediaFile | None:
    """The file at ``path``, read; None when it isn't audio this knows."""
    with open(path, "rb") as f:
        return read_fileobj(f, os.fspath(path))


@one_read
def read_fileobj(f: BinaryIO, name: str = "") -> MediaFile | None:
    size = file_size(f)
    f.seek(0)
    head = f.read(64)
    ext = os.path.splitext(name)[1].lower()
    if not head:
        return None
    if head[:4] == b"fLaC":
        return _flac(f, 0)
    if head[:4] == b"OggS":
        kind, duration, tags = read_ogg(f, size)
        return MediaFile(kind, duration, tags or None)
    if head[4:8] in _MP4_TYPES:
        duration, tags = read_mp4(f, size)
        return MediaFile("mp4", duration, tags or None)
    if head[:16] == _ASF_HEADER:
        duration, tags = read_asf(f)
        return MediaFile("asf", duration, tags)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        duration, tags = read_wav(f, size)
        return MediaFile("wav", duration, tags or None)
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        duration, tags = read_aiff(f, size)
        return MediaFile("aiff", duration, tags or None)
    if head[:8] == b"APETAGEX":
        return MediaFile("ape", None, read_ape_at_start(f) or None)
    if head[:4] in _APE_MAGIC or head[:3] in _APE_MAGIC:
        return MediaFile("ape", None, read_ape(f, size) or None)

    # What's left starts with an ID3v2 tag, an MPEG or ADTS frame, or is
    # nothing this reads.
    tags, tag_len = read_id3v2_at(f, 0)
    f.seek(tag_len)
    after = f.read(64)
    if after[:4] == b"fLaC":            # a FLAC file someone put an ID3 tag on
        return _flac(f, tag_len)
    # The stream is taken to run to the end of the file, an ID3v1 block
    # included: the length a constant-bitrate file has always been given.
    audio_end = size
    v1 = read_id3v1(f, size)
    if adts_header(after) is not None and ext not in _MPEG_EXTS:
        return MediaFile("aac", adts_length(f, tag_len, audio_end), tags or None)
    # Behind an ID3 tag, or in a file named as MPEG audio, the first frame
    # is searched for; anything else must start with one, so a sync pattern
    # somewhere inside an unrelated binary can't make it "an MP3".
    search = 256 * 1024 if (tags is not None or ext in _MPEG_EXTS) else 1
    first = find_first_frame(f, tag_len, audio_end, limit=search)
    if first is not None or (ext in _MPEG_EXTS and (tags is not None or v1)):
        duration = None
        if first is not None:
            start, header = first
            duration = stream_length(f, start, header, audio_end)
        return MediaFile("mp3", duration, merge_v1(tags, v1) or None)
    if ext in _AAC_EXTS and after[:4] == b"ADIF":
        return MediaFile("aac", None, tags or None)
    if tags is not None:
        return MediaFile("id3", None, tags or None)
    return None


def _flac(f: BinaryIO, start: int) -> MediaFile:
    duration, tags, pictures = read_flac(f, start)
    return MediaFile("flac", duration, tags or None, pictures)


@one_read
def read_id3(path: str | os.PathLike[str]) -> ID3Tags | None:
    """Only the ID3v2 tag at the start of the file, whatever follows it."""
    with open(path, "rb") as f:
        tags, _ = read_id3v2_at(f, 0)
    return tags


__all__ = [
    "APETags",
    "APEValue",
    "ASFTags",
    "ASFValue",
    "BudgetExceeded",
    "ChapterFrame",
    "CommentFrame",
    "Frame",
    "ID3Tags",
    "LyricsFrame",
    "MediaFile",
    "MP4Tags",
    "Picture",
    "PictureFrame",
    "RawFrame",
    "SyncedLyricsFrame",
    "TagError",
    "Tags",
    "TextFrame",
    "UserTextFrame",
    "VorbisTags",
    "parse_id3v2",
    "parse_picture",
    "parse_vorbis_comment",
    "read",
    "read_fileobj",
    "read_id3",
]
