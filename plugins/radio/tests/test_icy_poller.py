"""RadioIcyPoller — tristate lifecycle, transition detection, the
sanity-filtered acquisition enqueue, and bookkeeping writes."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio.clients.icy_metadata import (
    IcyPollResult,
    IcyStubClient,
    _set_icy_client_for_tests,
)
from domovoi_plugin_radio.workers.icy_poller import (
    MISSES_BEFORE_UNSUPPORTED,
    RadioIcyPoller,
)

pytestmark = requires_db

URL = "http://kexp.example/stream.mp3"


async def _insert_station(session, **kw) -> int:
    defaults = {
        "name": "KEXP",
        "source": "online",
        "stream_url": URL,
        "favorited": True,
        "icy": None,
        "now_playing": None,
    }
    defaults.update(kw)
    row = await session.execute(
        text(
            """
            INSERT INTO plugin_radio.radio_stations
                (name, source, stream_url, favorited, icy_supported, now_playing)
            VALUES (:name, :source, :stream_url, :favorited, :icy, :now_playing)
            RETURNING id
            """
        ),
        defaults,
    )
    await session.commit()
    return int(row.scalar_one())


async def _station_state(session, sid: int):
    return (
        await session.execute(
            text(
                "SELECT icy_supported, now_playing, now_playing_updated_at, "
                "last_icy_poll_at FROM plugin_radio.radio_stations WHERE id = :id"
            ),
            {"id": sid},
        )
    ).first()


def _poller_with(radio_sdk, result: IcyPollResult) -> RadioIcyPoller:
    stub = IcyStubClient(default_result=result)
    _set_icy_client_for_tests(stub)
    return RadioIcyPoller(radio_sdk)


async def test_title_transition_writes_detection_and_emits_event(
    radio_sdk, db_session
) -> None:
    import asyncio

    sid = await _insert_station(db_session)
    events: list = []

    async def _cb(event) -> None:
        events.append(event)

    radio_sdk.events.subscribe("plugin.radio.detection_recorded", _cb)
    poller = _poller_with(
        radio_sdk,
        IcyPollResult(supported=True, stream_title="Radiohead - Creep"),
    )
    polled = await poller.tick()
    assert polled == 1

    state = await _station_state(db_session, sid)
    assert state.icy_supported is True
    assert state.now_playing == "Radiohead - Creep"
    assert state.now_playing_updated_at is not None
    assert state.last_icy_poll_at is not None

    det = (
        await db_session.execute(
            text(
                "SELECT artist, title, fingerprint_source "
                "FROM plugin_radio.radio_detections"
            )
        )
    ).first()
    assert det.artist == "Radiohead"
    assert det.title == "Creep"
    assert det.fingerprint_source == "icy"
    # The post-commit observation broadcast, with the song heuristic.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(events) == 1
    assert events[0].payload["likely_song"] is True
    assert events[0].payload["fingerprint_source"] == "icy"


async def test_same_title_is_not_a_transition(radio_sdk, db_session) -> None:
    await _insert_station(db_session, now_playing="Radiohead - Creep")
    poller = _poller_with(
        radio_sdk,
        IcyPollResult(supported=True, stream_title="Radiohead - Creep"),
    )
    await poller.tick()
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM plugin_radio.radio_detections")
        )
    ).scalar_one()
    assert count == 0


async def test_non_song_titles_detect_with_likely_song_false(
    radio_sdk, db_session
) -> None:
    """The UI's now-playing stays honest; the event carries the verdict
    so subscribers can filter — radio itself never queues anything."""
    import asyncio

    await _insert_station(db_session)
    events: list = []

    async def _cb(event) -> None:
        events.append(event)

    radio_sdk.events.subscribe("plugin.radio.detection_recorded", _cb)
    poller = _poller_with(
        radio_sdk, IcyPollResult(supported=True, stream_title="Weather")
    )
    await poller.tick()
    det = (
        await db_session.execute(
            text("SELECT title FROM plugin_radio.radio_detections")
        )
    ).first()
    assert det.title == "Weather"
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(events) == 1
    assert events[0].payload["likely_song"] is False
    acq_count = (
        await db_session.execute(text("SELECT COUNT(*) FROM media_acquisitions"))
    ).scalar_one()
    assert acq_count == 0


async def test_tristate_flips_false_after_consecutive_misses(
    radio_sdk, db_session
) -> None:
    sid = await _insert_station(db_session)
    poller = _poller_with(radio_sdk, IcyPollResult(supported=False))
    for i in range(MISSES_BEFORE_UNSUPPORTED):
        # Reset the poll cadence so every tick re-selects the station.
        await db_session.execute(
            text(
                "UPDATE plugin_radio.radio_stations "
                "SET last_icy_poll_at = NULL WHERE id = :id"
            ),
            {"id": sid},
        )
        await db_session.commit()
        await poller.tick()
        state = await _station_state(db_session, sid)
        if i < MISSES_BEFORE_UNSUPPORTED - 1:
            assert state.icy_supported is None    # still probing
    assert state.icy_supported is False


async def test_one_success_resets_miss_counter(radio_sdk, db_session) -> None:
    sid = await _insert_station(db_session)
    stub = IcyStubClient(default_result=IcyPollResult(supported=False))
    _set_icy_client_for_tests(stub)
    poller = RadioIcyPoller(radio_sdk)

    async def _repoll():
        await db_session.execute(
            text(
                "UPDATE plugin_radio.radio_stations "
                "SET last_icy_poll_at = NULL WHERE id = :id"
            ),
            {"id": sid},
        )
        await db_session.commit()
        await poller.tick()

    await _repoll()
    await _repoll()
    # A success wipes the streak…
    stub.default_result = IcyPollResult(supported=True, stream_title=None)
    await _repoll()
    assert poller._misses.get(sid) is None
    # …so one more miss doesn't flip the tristate.
    stub.default_result = IcyPollResult(supported=False)
    await _repoll()
    state = await _station_state(db_session, sid)
    assert state.icy_supported is True    # last confirmed value sticks


async def test_confirmed_unsupported_stations_not_polled(
    radio_sdk, db_session
) -> None:
    await _insert_station(db_session, icy=False)
    poller = _poller_with(
        radio_sdk, IcyPollResult(supported=True, stream_title="X - Y")
    )
    assert await poller.tick() == 0


async def test_supported_but_empty_title_keeps_cache(
    radio_sdk, db_session
) -> None:
    sid = await _insert_station(
        db_session, icy=True, now_playing="Radiohead - Creep"
    )
    poller = _poller_with(
        radio_sdk, IcyPollResult(supported=True, stream_title=None)
    )
    await poller.tick()
    state = await _station_state(db_session, sid)
    assert state.now_playing == "Radiohead - Creep"   # prior cache stays
    assert state.icy_supported is True
