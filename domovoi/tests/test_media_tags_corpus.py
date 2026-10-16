"""The real-file corpus (fixtures/media_tags): every tag-reading function
Domovoi has, on files FFmpeg's encoders and muxers wrote and on the
quirks the tests' writer makes, against ``expected.json``.

``expected.json`` is mutagen's answer for each file (recorded from the
last commit that used it, e4dffc1) wherever the new code agrees — 229
answers of 252 — and the new answer, with mutagen's beside it and the
reason, for the 23 it improves (20 the reader's, 3 the library indexer
now asking for WMA's and MP4's own artist / album names). So this test is the "identical behaviour"
check for the mutagen replacement, and a regression check after it.
DB-free (the cover route's picture choice is called directly).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from domovoi.lyrics import sources
from domovoi.workers import audiobook_indexer, library_indexer, podcast_feed_poller
from web.backend.api import music

CORPUS = Path(__file__).resolve().parent / "fixtures" / "media_tags"
EXPECTED = json.loads((CORPUS / "expected.json").read_text(encoding="utf-8"))
FILES = sorted(EXPECTED["files"])


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 — a raise is an answer too
        return {"raises": type(e).__name__}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _short(text):
    if text is None or len(text) <= 400:
        return text
    return {"sha256_16": _sha(text.encode("utf-8")), "chars": len(text)}


def answers(path: Path) -> dict:
    pics = _safe(music._embedded_pictures, path)
    cover = _safe(music._read_embedded_cover, path)
    lyr = _safe(sources.read_embedded, path)
    got = {
        "library": _safe(library_indexer._read_tags, path),
        "audiobook": _safe(audiobook_indexer._read_tags, path),
        "chapters": _safe(podcast_feed_poller._read_id3_chapters, path),
        "pictures": pics if isinstance(pics, dict) else [[t, _sha(d), len(d)] for t, d in pics],
        "cover": cover if (cover is None or isinstance(cover, dict)) else [_sha(cover[0]), cover[1]],
        "lyrics": lyr if (lyr is None or isinstance(lyr, dict)) else {
            "detail": lyr.detail, "plain": _short(lyr.lyrics.plain),
            "synced": [list(x) for x in lyr.lyrics.synced] if lyr.lyrics.synced is not None else None},
    }
    return json.loads(json.dumps(got))       # tuples as lists, as the JSON has them


def test_the_corpus_is_all_there() -> None:
    on_disk = sorted(p.name for p in CORPUS.iterdir() if p.suffix not in (".py", ".json"))
    assert on_disk == FILES
    assert len(FILES) >= 40


@pytest.mark.parametrize("name", FILES)
def test_each_file_answers_as_expected(name: str) -> None:
    assert answers(CORPUS / name) == EXPECTED["files"][name]


def test_every_difference_from_mutagen_is_explained() -> None:
    differed = EXPECTED["mutagen_differed"]
    for name, entry in differed.items():
        assert entry["why"].strip(), name
        for field, old in entry["mutagen"].items():
            assert old != EXPECTED["files"][name][field], (name, field)
    total = sum(len(v) for v in EXPECTED["files"].values())
    changed = sum(len(e["mutagen"]) for e in differed.values())
    assert total == 6 * len(FILES) and changed == 23


@pytest.mark.parametrize("name", ["ff_id3v23.mp3", "ff_id3v24.mp3", "ff_tagged.flac", "ff_tagged.m4a",
                                  "ff_tagged.ogg", "ff_tagged.opus", "ff_tagged.wma"])
def test_ffmpeg_tagged_files_carry_what_ffmpeg_was_given(name: str) -> None:
    """Spelled out for the files FFmpeg tagged, so the corpus check is
    anchored to what was written and not only to an earlier reader."""
    lib = EXPECTED["files"][name]["library"]
    assert (lib["title"], lib["artist"], lib["album"]) == ("Lantern Song", "The Example Band", "River Door")
    assert answers(CORPUS / name)["library"] == lib


def test_ffmpeg_chapters_reach_the_podcast_reader() -> None:
    for name in ("ff_id3v23.mp3", "ff_id3v24.mp3"):
        assert answers(CORPUS / name)["chapters"] == [
            {"start_sec": 0, "title": "Opening"}, {"start_sec": 1, "title": "The Middle Bit"}]


def record() -> None:
    """Re-record ``files`` in expected.json from the current code (after
    ``make_corpus.py``); ``mutagen_differed`` is kept as it was. Review the
    diff before committing it: every changed answer is a behaviour change."""
    doc = json.loads((CORPUS / "expected.json").read_text(encoding="utf-8"))
    names = sorted(p.name for p in CORPUS.iterdir() if p.suffix not in (".py", ".json"))
    doc["files"] = {name: answers(CORPUS / name) for name in names}
    text = json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    (CORPUS / "expected.json").write_bytes(text.encode("utf-8"))
    print(f"recorded {len(names)} files")


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["--record"]:
        raise SystemExit("usage: python -m domovoi.tests.test_media_tags_corpus --record")
    record()
