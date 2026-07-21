"""Library indexer tests — filename fallback parsing, idempotent
re-runs, non-audio-file skipping. The mutagen path is exercised
implicitly via the user's real library; these tests don't fabricate
fake ID3 tags (mutagen mocking is more drag than value at this scale).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from domovoi.workers.library_indexer import (
    _fallback_filename_parse,
    _resolve_metadata_for,
    index_music_dir,
)


# ─── Filename fallback parser ─────────────────────────────────────────────

def test_fallback_parses_artist_dash_title() -> None:
    """The dominant pattern in flat 'Music/' folders."""
    artist, title = _fallback_filename_parse(Path("Akon - Sunny Day.mp3"))
    assert artist == "Akon"
    assert title == "Sunny Day"


def test_fallback_strips_upload_noise_from_title() -> None:
    """The (Official Music Video) cruft on the title side gets stripped
    by the same core library_naming regex every ingest path uses, so
    manually-dropped provider downloads end up with clean titles."""
    artist, title = _fallback_filename_parse(
        Path("ABBA - Take A Chance On Me (Official Music Video).mp3")
    )
    assert artist == "ABBA"
    assert title == "Take A Chance On Me"


def test_fallback_handles_dash_without_trailing_space() -> None:
    """User has files like "ASAP Rocky- Fucken Problem.mp3" — the dash
    has a leading space but no trailing space. The regex's `\\s*` on
    both sides of the dash handles it."""
    artist, title = _fallback_filename_parse(Path("ASAP Rocky- Fucken Problem.mp3"))
    assert artist == "ASAP Rocky"
    assert title == "Fucken Problem"


def test_fallback_no_separator_returns_title_only() -> None:
    """When no Artist - Title structure exists, return the bare stem
    as title and let artist stay None — the user can fix tags later."""
    artist, title = _fallback_filename_parse(Path("9MM x LOLI SHIGURE UI.mp3"))
    assert artist is None
    assert title == "9MM x LOLI SHIGURE UI"


def test_fallback_handles_unicode_dash() -> None:
    """En-dashes and em-dashes, not just ASCII hyphen — common in
    classical / international filenames. Note: artist names containing
    a hyphen (e.g. "Yo-Yo Ma") confuse the parser since it greedy-
    matches the first separator. Acceptable trade-off — the user can
    fix tags or rename the file."""
    artist, title = _fallback_filename_parse(Path("Beethoven – Symphony No. 5.mp3"))
    assert artist == "Beethoven"
    assert "Symphony No. 5" in title


# ─── Metadata resolution ──────────────────────────────────────────────────

def test_resolve_returns_at_least_a_title() -> None:
    """No matter how unparseable the filename, we always return a
    title — worst case the bare stem. This guarantees the INSERT
    has a non-null title to satisfy downstream UI / search."""
    meta = _resolve_metadata_for(Path("untagged_garbage.mp3"))
    assert meta["title"] is not None
    assert meta["title"] == "untagged_garbage"


# ─── DB-backed indexer behavior ───────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_indexer_skips_when_music_dir_missing(db_session, monkeypatch, tmp_path) -> None:
    """A nonexistent MUSIC_DIR shouldn't crash the indexer — log a
    warning and return zero counts."""
    fake_dir = tmp_path / "does-not-exist"
    monkeypatch.setattr("domovoi.workers.library_indexer.settings.music_dir", str(fake_dir))

    counts = await index_music_dir()
    assert counts == {"scanned": 0, "inserted": 0, "skipped": 0, "errors": 0}


@requires_db
@pytest.mark.asyncio
async def test_indexer_inserts_audio_files_via_filename_parse(
    db_session, monkeypatch, tmp_path,
) -> None:
    """End-to-end: empty MUSIC_DIR with three audio files (no ID3
    tags), indexer parses filenames and inserts rows."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    # Create some empty (no-ID3) audio-extension files. Mutagen will
    # return None for these; the indexer falls through to filename
    # parsing.
    (music_dir / "Akon - Sunny Day.mp3").write_bytes(b"")
    (music_dir / "Radiohead - Creep.flac").write_bytes(b"")
    (music_dir / "untagged.opus").write_bytes(b"")
    # And a non-audio file that should be ignored.
    (music_dir / "cover.jpg").write_bytes(b"")
    (music_dir / "lyrics.txt").write_text("la la la")

    monkeypatch.setattr("domovoi.workers.library_indexer.settings.music_dir", str(music_dir))

    counts = await index_music_dir()
    assert counts["scanned"] == 3  # only the audio files
    assert counts["inserted"] == 3
    assert counts["skipped"] == 0

    rows = (await db_session.execute(
        text("SELECT title, artist, added_via FROM library_tracks ORDER BY title")
    )).all()
    assert len(rows) == 3
    titles = {r.title for r in rows}
    assert "Sunny Day" in titles
    assert "Creep" in titles
    assert "untagged" in titles
    # All marked as manual (the indexer's identity).
    assert all(r.added_via == "manual" for r in rows)


@requires_db
@pytest.mark.asyncio
async def test_indexer_is_idempotent(db_session, monkeypatch, tmp_path) -> None:
    """Re-running the indexer doesn't double-insert. ON CONFLICT
    (file_path) DO NOTHING is the workhorse here — the second call
    should report skipped=N, inserted=0."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "Track One.mp3").write_bytes(b"")
    (music_dir / "Track Two.mp3").write_bytes(b"")
    monkeypatch.setattr("domovoi.workers.library_indexer.settings.music_dir", str(music_dir))

    first = await index_music_dir()
    assert first["inserted"] == 2 and first["skipped"] == 0

    second = await index_music_dir()
    assert second["inserted"] == 0 and second["skipped"] == 2

    # No duplicates.
    count = (await db_session.execute(
        text("SELECT count(*) FROM library_tracks")
    )).scalar_one()
    assert count == 2


@requires_db
@pytest.mark.asyncio
async def test_indexer_picks_up_new_files_on_rerun(
    db_session, monkeypatch, tmp_path,
) -> None:
    """First run indexes existing files; second run after a new file
    appears picks up just the new one (delta behavior)."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "First Track.mp3").write_bytes(b"")
    monkeypatch.setattr("domovoi.workers.library_indexer.settings.music_dir", str(music_dir))

    first = await index_music_dir()
    assert first["inserted"] == 1

    # User drops a new file in.
    (music_dir / "Second Track.mp3").write_bytes(b"")

    second = await index_music_dir()
    assert second["inserted"] == 1  # just the new one
    assert second["skipped"] == 1   # the existing one


@requires_db
@pytest.mark.asyncio
async def test_indexer_walks_subdirectories(
    db_session, monkeypatch, tmp_path,
) -> None:
    """rglob('*') means MUSIC_DIR/Artist/Album/Track.mp3 layouts work
    the same as flat MUSIC_DIR/Track.mp3."""
    music_dir = tmp_path / "music"
    (music_dir / "Radiohead" / "OK Computer").mkdir(parents=True)
    (music_dir / "Radiohead" / "OK Computer" / "Paranoid Android.mp3").write_bytes(b"")
    (music_dir / "Lady Gaga").mkdir(parents=True)
    (music_dir / "Lady Gaga" / "Poker Face.mp3").write_bytes(b"")
    monkeypatch.setattr("domovoi.workers.library_indexer.settings.music_dir", str(music_dir))

    counts = await index_music_dir()
    assert counts["scanned"] == 2
    assert counts["inserted"] == 2
