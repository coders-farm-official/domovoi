"""Tags read for spoken audio: the audiobook indexer's title / author /
narrator / length, and the podcast poller's ID3 chapters — through
domovoi.media_tags since mutagen left (2026-10-15). DB-free: ffprobe is
stubbed out, so only the tag path answers."""

from __future__ import annotations

import asyncio

import pytest

from domovoi.tests import media_writer as mw
from domovoi.workers import audiobook_indexer as ab
from domovoi.workers.podcast_feed_poller import _read_id3_chapters


@pytest.fixture(autouse=True)
def no_ffprobe(monkeypatch):
    async def none(path):
        return None, None
    monkeypatch.setattr(ab, "_ffprobe_chapters", none)


def test_a_tagged_flac_book_indexes_with_author_and_narrator(tmp_path) -> None:
    """mutagen raised on the MP4 names probed against Vorbis comments, so
    such a book used to fail to index at all."""
    p = tmp_path / "Book.flac"
    p.write_bytes(mw.flac_file({"TITLE": ["The Long Road"], "ARTIST": ["A. Author"], "NARRATOR": ["N. Reader"]},
                               rate=8000, samples=8000 * 125))
    meta = asyncio.run(ab._resolve_book_file(p))
    assert (meta["title"], meta["author"], meta["narrator"], meta["duration_sec"]) == (
        "The Long Road", "A. Author", "N. Reader", 125)


def test_an_opus_book_falls_back_to_composer_for_both(tmp_path) -> None:
    p = tmp_path / "Book.opus"
    p.write_bytes(mw.ogg_file("opus", {"ALBUM": ["Album As Title"], "COMPOSER": ["C. Composer"]}, granule=48312))
    meta = asyncio.run(ab._resolve_book_file(p))
    assert (meta["title"], meta["author"], meta["narrator"], meta["duration_sec"]) == (
        "Album As Title", "C. Composer", "C. Composer", 1)


def test_an_m4b_book_reads_the_mp4_names(tmp_path) -> None:
    p = tmp_path / "Book.m4b"
    p.write_bytes(mw.m4a_file([mw.mp4_text("\xa9nam", "Mp4 Book"), mw.mp4_text("\xa9wrt", "Mp4 Writer"),
                               mw.mp4_text("\xa9nrt", "Mp4 Narrator")], timescale=1000, duration=3_725_000))
    meta = asyncio.run(ab._resolve_book_file(p))
    assert (meta["title"], meta["author"], meta["narrator"], meta["duration_sec"]) == (
        "Mp4 Book", "Mp4 Writer", "Mp4 Narrator", 3725)


def test_an_untagged_book_is_named_after_its_file(tmp_path) -> None:
    p = tmp_path / "Untagged Book.mp3"
    p.write_bytes(mw.silent_mp3(100))
    meta = asyncio.run(ab._resolve_book_file(p))
    assert (meta["title"], meta["author"], meta["duration_sec"]) == ("Untagged Book", None, 2)


def test_a_folder_book_adds_up_its_chapter_lengths(tmp_path) -> None:
    book = tmp_path / "Folder Book"
    book.mkdir()
    mw.write_mp3(book / "01.mp3", [mw.text_frame("TIT2", "Chapter One"), mw.text_frame("TPE1", "Folder Author")],
                 audio=mw.silent_mp3(400))                          # 10.4 s
    (book / "02.flac").write_bytes(mw.flac_file({"TITLE": ["Chapter Two"]}, rate=8000, samples=8000 * 61))
    (book / "03.ogg").write_bytes(mw.ogg_file("vorbis", {"TITLE": ["Chapter Three"]}))
    meta = asyncio.run(ab._resolve_book_folder(book))
    assert meta["author"] == "Folder Author"
    assert [(c["title"], c["start_sec"]) for c in meta["chapters"]] == [
        ("Chapter One", 0), ("Chapter Two", 10), ("Chapter Three", 71)]
    assert meta["duration_sec"] == 72


@pytest.mark.parametrize("version", [3, 4])
def test_podcast_chapters_in_time_order_with_their_titles(tmp_path, version) -> None:
    p = mw.write_mp3(tmp_path / "ep.mp3", [
        mw.CHAP(element_id="b", start_time=125_900, end_time=200_000, sub_frames=[mw.TIT2("Interview", encoding=1)]),
        mw.CHAP(element_id="a", start_time=0, end_time=125_900, sub_frames=[mw.TIT2("Intro", encoding=1)]),
        mw.CHAP(element_id="c", start_time=200_000, end_time=230_000),
    ], version=version)
    assert _read_id3_chapters(p) == [
        {"title": "Intro", "start_sec": 0}, {"title": "Interview", "start_sec": 125}, {"title": "", "start_sec": 200}]


def test_no_chapters_no_tag_or_no_file_is_none(tmp_path) -> None:
    plain = mw.write_mp3(tmp_path / "plain.mp3", [mw.text_frame("TIT2", "No Chapters")])
    bare = tmp_path / "bare.mp3"
    bare.write_bytes(mw.silent_mp3())
    assert _read_id3_chapters(plain) is None
    assert _read_id3_chapters(bare) is None
    assert _read_id3_chapters(tmp_path / "missing.mp3") is None
