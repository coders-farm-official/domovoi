"""Pure-parser coverage: ICY StreamTitle, FCC FMQ, radio-browser rows,
ICY artist/title split + song sanity filter, dedup keys. No DB."""

from __future__ import annotations

from domovoi_plugin_radio.clients.fcc_fm import parse_fmq_response
from domovoi_plugin_radio.clients.icy_metadata import (
    parse_metadata_block,
    parse_stream_title,
)
from domovoi_plugin_radio.clients.radio_browser import _parse_row
from domovoi_plugin_radio.workers.icy_poller import (
    is_likely_song,
    split_artist_title,
)


# ─── ICY StreamTitle ─────────────────────────────────────────────────────


def test_parse_stream_title_basic() -> None:
    blob = b"StreamTitle='Radiohead - Creep';StreamUrl='';\x00\x00\x00"
    assert parse_stream_title(blob) == "Radiohead - Creep"


def test_parse_stream_title_apostrophe_survives() -> None:
    blob = b"StreamTitle='Don't Stop Believin' - Journey';"
    assert parse_stream_title(blob) == "Don't Stop Believin' - Journey"


def test_parse_stream_title_latin1_fallback() -> None:
    blob = "StreamTitle='Björk - Jóga';".encode("latin-1")
    title = parse_stream_title(blob)
    assert title is not None and "J" in title


def test_parse_stream_title_empty_cases() -> None:
    assert parse_stream_title(b"") is None
    assert parse_stream_title(b"\x00\x00") is None
    assert parse_stream_title(b"StreamTitle='';") is None
    assert parse_stream_title(b"nonsense") is None


def test_parse_metadata_block_maps_all_pairs() -> None:
    blob = b"StreamTitle='X - Y';StreamUrl='http://a';\x00"
    out = parse_metadata_block(blob)
    assert out == {"StreamTitle": "X - Y", "StreamUrl": "http://a"}


# ─── ICY artist/title split + sanity filter ─────────────────────────────


def test_split_artist_title_conventional() -> None:
    assert split_artist_title("Radiohead - Creep") == ("Radiohead", "Creep")


def test_split_artist_title_em_dash_normalized() -> None:
    assert split_artist_title("Radiohead — Creep") == ("Radiohead", "Creep")


def test_split_artist_title_bare_title() -> None:
    assert split_artist_title("Creep") == (None, "Creep")


def test_is_likely_song_rejects_junk() -> None:
    assert not is_likely_song("ads", station_name="WXYZ")
    assert not is_likely_song("Weather", station_name="WXYZ")
    assert not is_likely_song("abc", station_name="WXYZ")          # too short
    assert not is_likely_song("WXYZ", station_name="WXYZ")          # own name
    assert not is_likely_song("http://x.example/a.mp3", station_name="WXYZ")
    assert not is_likely_song(r"C:\automation\song.mp3", station_name="WXYZ")


def test_is_likely_song_accepts_real_titles() -> None:
    assert is_likely_song("Radiohead - Creep", station_name="WXYZ")
    # Deliberately NOT denylisted: real songs containing filter words.
    assert is_likely_song("News for Lulu", station_name="WXYZ")


# ─── FCC FMQ parser ──────────────────────────────────────────────────────

_FMQ_SAMPLE = "\n".join([
    "|W201CF|88.1  MHz|FM|201|ND|-|A|-|LIC|BALDWIN|MI|US|BLFT-20011023ABP|x|y",
    "|WQHH|96.5  MHz|FM|243|DA|-|B1|-|LIC|LANSING|MI|US|0000232761|x|y",
    # Booster — filtered out.
    "|WBST|101.1 MHz|FB|266|ND|-|A|-|LIC|SOMEWHERE|MI|US|0000111|x|y",
    # Out-of-band junk — filtered out.
    "|WJNK|432.0 MHz|FM|1|ND|-|A|-|LIC|NOWHERE|MI|US|0000222|x|y",
    # Malformed short row — skipped, not fatal.
    "|WSHORT|99.9 MHz|FM",
    "",
])


def test_parse_fmq_response() -> None:
    rows = list(parse_fmq_response(_FMQ_SAMPLE))
    assert [r.call_sign for r in rows] == ["W201CF", "WQHH"]
    assert rows[0].frequency_mhz == 88.1
    assert rows[0].market_state == "MI"
    assert rows[0].market_city == "BALDWIN"
    assert rows[0].facility_id.startswith("fcc-")
    assert rows[1].frequency_mhz == 96.5


def test_parse_fmq_header_row_skipped() -> None:
    body = "|callsign|frequency|service|\n" + _FMQ_SAMPLE
    rows = list(parse_fmq_response(body))
    assert [r.call_sign for r in rows] == ["W201CF", "WQHH"]


# ─── radio-browser row normalization ────────────────────────────────────


def test_parse_row_full() -> None:
    st = _parse_row({
        "stationuuid": "abc",
        "name": "KEXP 90.3 FM",
        "url_resolved": "https://kexp.example/stream.mp3",
        "countrycode": "US",
        "language": "english",
        "tags": "indie, seattle,,alternative ",
        "codec": "MP3",
        "bitrate": "128",
        "homepage": "https://kexp.org",
    })
    assert st is not None
    assert st.external_id == "abc"
    assert st.stream_url == "https://kexp.example/stream.mp3"
    assert st.tags == ["indie", "seattle", "alternative"]
    assert st.bitrate == 128


def test_parse_row_rejects_unusable() -> None:
    assert _parse_row({"name": "x", "url_resolved": "http://y"}) is None      # no uuid
    assert _parse_row({"stationuuid": "a", "name": "x"}) is None              # no url
    assert _parse_row("not a dict") is None


