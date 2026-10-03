"""MusicBrainz client tests.

Title-cleaner is pure logic and gets the bulk of the coverage. The HTTP
client is exercised at the integration level via provider-pipeline tests
with a custom stub.
"""

from __future__ import annotations

import pytest

from domovoi.clients.musicbrainz import (
    MusicBrainzStubClient,
    _clean_media_title,
)


def test_clean_strips_artist_dash_title() -> None:
    cleaned, artist = _clean_media_title("Radiohead - Creep")
    assert cleaned == "Creep"
    assert artist == "Radiohead"


def test_clean_strips_official_video_marker() -> None:
    cleaned, artist = _clean_media_title("Radiohead - Creep (Official Music Video)")
    assert cleaned == "Creep"
    assert artist == "Radiohead"


def test_clean_strips_hd_and_lyrics() -> None:
    cleaned, _ = _clean_media_title("Some Song [HD]")
    assert cleaned == "Some Song"
    cleaned, _ = _clean_media_title("Another Song (Lyrics)")
    assert cleaned == "Another Song"


def test_clean_strips_feat() -> None:
    cleaned, _ = _clean_media_title("Track Name (feat. Someone Else)")
    assert cleaned == "Track Name"
    cleaned, _ = _clean_media_title("Track Name (ft. Someone)")
    assert cleaned == "Track Name"


def test_clean_strips_year() -> None:
    cleaned, _ = _clean_media_title("Old Song [1999]")
    assert cleaned == "Old Song"


def test_clean_strips_unicode_dashes() -> None:
    # en-dash and em-dash both used in external upload titles
    assert _clean_media_title("Artist – Title")[1] == "Artist"
    assert _clean_media_title("Artist — Title")[1] == "Artist"


def test_clean_no_artist_dash_returns_none_for_artist() -> None:
    cleaned, artist = _clean_media_title("Just A Song Title")
    assert cleaned == "Just A Song Title"
    assert artist is None


def test_clean_collapses_whitespace_after_stripping() -> None:
    cleaned, _ = _clean_media_title("Song Name  (Official)   [HD]")
    assert cleaned == "Song Name"


@pytest.mark.asyncio
async def test_stub_returns_none() -> None:
    client = MusicBrainzStubClient()
    assert await client.lookup(title="anything", artist="anyone") is None


# ─── Internet access turned off (INTERNET_ACCESS=never) ─────────────────


@pytest.mark.asyncio
async def test_internet_off_is_unavailable_without_a_request(monkeypatch) -> None:
    """Under never the client sends nothing and raises
    MusicBrainzUnavailable("internet off"), so the alias fetch backs off
    instead of recording "MusicBrainz has no such artist"."""
    import requests

    from domovoi import egress
    from domovoi.clients.musicbrainz import HttpMusicBrainzClient, MusicBrainzUnavailable

    def no_request(*_a, **_kw):  # pragma: no cover - reaching this IS the failure
        raise AssertionError("MusicBrainz was called with internet access turned off")

    monkeypatch.setattr(requests, "get", no_request)
    client = HttpMusicBrainzClient()
    monkeypatch.setattr(client, "MIN_INTERVAL_SEC", 0)
    with egress.override_policy("never"):
        with pytest.raises(MusicBrainzUnavailable, match="internet off"):
            await client.search_artists("Tech N9ne")
        # The best-effort lookup turns the refusal into "no match" (None).
        assert await client.lookup(title="Creep", artist="Radiohead") is None
