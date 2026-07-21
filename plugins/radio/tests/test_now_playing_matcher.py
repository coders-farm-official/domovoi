"""The favorites-attribution matcher (design §9.4): byte-equality
stream_url first (online), station-name-as-MPD-title fallback (FM/SDR
— which is exactly why play_url's title stamping is load-bearing)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio.core import _make_matcher

pytestmark = requires_db

URL = "http://kexp.example/stream.mp3"


async def _seed(session) -> int:
    row = await session.execute(
        text(
            "INSERT INTO plugin_radio.radio_stations "
            "(name, source, stream_url, favorited) "
            "VALUES ('KEXP 90.3', 'online', :url, FALSE) RETURNING id"
        ),
        {"url": URL},
    )
    await session.commit()
    return int(row.scalar_one())


async def test_matches_by_exact_stream_url(stub_sdk, db_session) -> None:
    sid = await _seed(db_session)
    matcher = _make_matcher(stub_sdk)
    out = await matcher(db_session, mpd_file=URL, title="whatever")
    assert out == {
        "kind": "radio", "station_id": sid,
        "name": "KEXP 90.3", "favorited": False,
    }


async def test_falls_back_to_name_as_title(stub_sdk, db_session) -> None:
    """FM/SDR: the stream URL is a transient local target, but the
    handler stamps the station name as the MPD title."""
    sid = await _seed(db_session)
    matcher = _make_matcher(stub_sdk)
    out = await matcher(
        db_session,
        mpd_file="http://127.0.0.1:6391/fm.mp3",
        title="kexp 90.3",                      # case-insensitive
    )
    assert out is not None and out["station_id"] == sid


async def test_returns_none_to_pass_the_chain(stub_sdk, db_session) -> None:
    await _seed(db_session)
    matcher = _make_matcher(stub_sdk)
    assert await matcher(
        db_session, mpd_file="http://other/x.mp3", title="Some Song"
    ) is None
    assert await matcher(db_session) is None


async def test_registered_through_sdk_on_load(db_session) -> None:
    from pathlib import Path

    from domovoi.now_playing import NOW_PLAYING
    from domovoi.plugins_runtime.loader import LOADER
    from domovoi.plugins_runtime.manifest import parse_manifest_dir

    plugin_dir = Path(__file__).resolve().parents[1]
    manifest = parse_manifest_dir(plugin_dir)
    await LOADER.load_plugin(
        slug="radio", install_dir=plugin_dir, manifest=manifest,
        foreign_corpus=[], update_registry_status=False,
    )
    try:
        chain = dict(NOW_PLAYING.matchers())
        assert "radio" in chain
        sid = await _seed(db_session)
        out = await chain["radio"](db_session, mpd_file=URL, title=None)
        assert out is not None and out["station_id"] == sid
    finally:
        await LOADER.unload_plugin("radio")
