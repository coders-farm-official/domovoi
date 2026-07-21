"""Containment + MPD-URI mapping tests for the spoken-audio web routers.

Mirrors test_documents.py's containment coverage: every served episode /
audiobook path must resolve inside its configured dir, and a wild path is
refused (the music.py:349-367 guard). Also checks the shared MPD-URI mapping
(host path → /music-nested URI) and its containment refusal.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from domovoi.config import settings
from domovoi import spoken_audio as sa
from web.backend.api import audiobooks as ab
from web.backend.api import podcasts as pc


# ─── Podcasts containment ───────────────────────────────────────────────
def test_podcast_path_accepts_inside(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path))
    f = tmp_path / "show" / "ep.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    assert pc._safe_episode_path(str(f)) == f.resolve()


def test_podcast_path_rejects_outside(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path / "pods"))
    (tmp_path / "pods").mkdir()
    outside = tmp_path / "elsewhere" / "x.mp3"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    with pytest.raises(HTTPException) as ei:
        pc._safe_episode_path(str(outside))
    assert ei.value.status_code == 400


# ─── Audiobooks containment ─────────────────────────────────────────────
def test_audiobook_path_rejects_outside(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path / "books"))
    (tmp_path / "books").mkdir()
    outside = tmp_path / "escape.m4b"
    outside.write_bytes(b"x")
    with pytest.raises(HTTPException) as ei:
        ab._safe_within(outside)
    assert ei.value.status_code == 400


def test_audiobook_folder_chapter_containment(monkeypatch, tmp_path):
    """A folder book chapter is resolved inside the book folder; a traversal
    filename can't escape audiobooks_dir."""
    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path))
    book = tmp_path / "dune"
    book.mkdir()
    ch = book / "ch1.mp3"
    ch.write_bytes(b"x")
    assert ab._safe_within(book / "ch1.mp3") == ch.resolve()


# ─── MPD URI mapping ────────────────────────────────────────────────────
def test_mpd_uri_maps_under_music(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path / "p"))
    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path / "a"))
    (tmp_path / "p" / "show").mkdir(parents=True)
    ep = tmp_path / "p" / "show" / "e.mp3"
    uri = sa.mpd_uri_for(sa.ITEM_PODCAST, str(ep))
    assert uri == "podcasts/show/e.mp3"

    (tmp_path / "a").mkdir()
    book = tmp_path / "a" / "dune.m4b"
    assert sa.mpd_uri_for(sa.ITEM_AUDIOBOOK, str(book)) == "audiobooks/dune.m4b"


def test_mpd_uri_refuses_outside(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path / "p"))
    (tmp_path / "p").mkdir()
    outside = tmp_path / "elsewhere" / "x.mp3"
    assert sa.mpd_uri_for(sa.ITEM_PODCAST, str(outside)) is None
