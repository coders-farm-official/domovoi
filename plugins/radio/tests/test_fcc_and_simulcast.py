"""FCC bulk import (async job — locked 19) + simulcast resolution."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio.clients.radio_browser import (
    RadioBrowserStation,
    _set_radio_browser_client_for_tests,
)
from domovoi_plugin_radio.workers import fcc_import, simulcast

pytestmark = requires_db


async def _fm_rows(session):
    rows = await session.execute(
        text(
            "SELECT name, call_sign, frequency_mhz, favorited, stream_url "
            "FROM plugin_radio.radio_stations WHERE source = 'fm' ORDER BY id"
        )
    )
    return rows.all()


# ─── FCC import ──────────────────────────────────────────────────────────


async def test_import_state_inserts_then_updates(radio_sdk, db_session) -> None:
    # The stub FCC client (USE_STUBS=true) returns two stations per state.
    result = await fcc_import.import_state(radio_sdk, "MI")
    assert result.state == "MI"
    assert result.inserted == 2 and result.updated == 0
    rows = await _fm_rows(db_session)
    assert [r.call_sign for r in rows] == ["KMIA", "KMIB"]
    assert all(r.stream_url is None and r.favorited is False for r in rows)

    # Idempotent on external_id: re-running updates in place.
    result2 = await fcc_import.import_state(radio_sdk, "MI")
    assert result2.inserted == 0 and result2.updated == 2
    assert len(await _fm_rows(db_session)) == 2


async def test_import_state_rejects_bad_state(radio_sdk, db_session) -> None:
    result = await fcc_import.import_state(radio_sdk, "MICH")
    assert result.inserted == 0 and result.updated == 0


async def test_import_uses_configured_market_state(
    radio_sdk, db_session, radio_settings
) -> None:
    radio_settings.market_state = "wa"
    result = await fcc_import.import_state(radio_sdk)
    assert result.state == "WA"
    rows = await _fm_rows(db_session)
    assert [r.call_sign for r in rows] == ["KWAA", "KWAB"]


async def test_import_job_runs_in_background(radio_sdk, db_session) -> None:
    """The endpoint surface: POST starts a job and returns immediately;
    the status flips to done with the result counts (locked 19 — never
    a blocking request)."""
    out = fcc_import.start_import_job(radio_sdk, "MI")
    assert out["started"] is True
    # A second start while running is refused.
    if fcc_import.job_status(radio_sdk)["state"] == "running":
        again = fcc_import.start_import_job(radio_sdk, "MI")
        assert again["started"] is False
    for _ in range(100):
        await asyncio.sleep(0.01)
        status = fcc_import.job_status(radio_sdk)
        if status["state"] != "running":
            break
    assert status["state"] == "done"
    assert status["result"]["inserted"] == 2


async def test_boot_import_gated_by_config(
    radio_sdk, db_session, radio_settings
) -> None:
    radio_settings.fcc_import_on_boot = False
    radio_settings.market_state = "MI"
    await fcc_import.boot_import(radio_sdk)
    assert await _fm_rows(db_session) == []
    radio_settings.fcc_import_on_boot = True
    await fcc_import.boot_import(radio_sdk)
    assert len(await _fm_rows(db_session)) == 2


# ─── Simulcast resolution ────────────────────────────────────────────────


class _FixedRadioBrowser:
    def __init__(self, hits) -> None:
        self.hits = hits
        self.calls: list[dict] = []

    async def search(self, name=None, country_code=None, tag=None,
                     language=None, limit=30, offset=0):
        self.calls.append({"name": name, "country_code": country_code})
        return self.hits


def _hit(name: str, url: str = "http://sim.example/x.mp3") -> RadioBrowserStation:
    return RadioBrowserStation(external_id=f"uuid-{name}", name=name, stream_url=url)


async def _insert_fm(session, call_sign="WQHH", stream_url=None, favorited=True) -> int:
    row = await session.execute(
        text(
            """
            INSERT INTO plugin_radio.radio_stations
                (name, source, call_sign, stream_url, favorited,
                 frequency_mhz, country_code)
            VALUES (:cs, 'fm', :cs, :url, :fav, 96.5, 'US')
            RETURNING id
            """
        ),
        {"cs": call_sign, "url": stream_url, "fav": favorited},
    )
    await session.commit()
    return int(row.scalar_one())


def test_pick_best_match_word_boundary() -> None:
    hits = [_hit("WQHHE-FM 101"), _hit("Power 96.5 WQHH"), _hit("WQHH-FM")]
    best = simulcast.pick_best_match(hits, "WQHH")
    assert best is not None and best.name == "Power 96.5 WQHH"
    assert simulcast.pick_best_match([_hit("WQHHE-FM")], "WQHH") is None
    assert simulcast.pick_best_match([], "WQHH") is None


async def test_resolve_stamps_best_hit(radio_sdk, db_session) -> None:
    sid = await _insert_fm(db_session)
    _set_radio_browser_client_for_tests(_FixedRadioBrowser([_hit("WQHH 96.5")]))
    result = await simulcast.resolve_simulcast_for_station(radio_sdk, sid)
    assert result.resolved is True
    assert result.stream_url == "http://sim.example/x.mp3"
    row = (
        await db_session.execute(
            text(
                "SELECT stream_url, external_id "
                "FROM plugin_radio.radio_stations WHERE id = :id"
            ),
            {"id": sid},
        )
    ).first()
    assert row.stream_url == "http://sim.example/x.mp3"
    assert row.external_id == "uuid-WQHH 96.5"


async def test_resolve_is_idempotent_on_existing_url(radio_sdk, db_session) -> None:
    sid = await _insert_fm(db_session, stream_url="http://manual.example/x")
    result = await simulcast.resolve_simulcast_for_station(radio_sdk, sid)
    assert result.resolved is False
    assert "already has" in result.message


async def test_resolve_skips_online_rows(radio_sdk, db_session) -> None:
    row = await db_session.execute(
        text(
            "INSERT INTO plugin_radio.radio_stations "
            "(name, source, call_sign, favorited) "
            "VALUES ('KEXP', 'online', 'KEXP', TRUE) RETURNING id"
        )
    )
    await db_session.commit()
    sid = int(row.scalar_one())
    result = await simulcast.resolve_simulcast_for_station(radio_sdk, sid)
    assert result.resolved is False
    assert "not 'fm'" in result.message


async def test_resolve_missing_station(radio_sdk, db_session) -> None:
    result = await simulcast.resolve_simulcast_for_station(radio_sdk, 99999)
    assert result.resolved is False
    assert "not found" in result.message


async def test_backfill_walks_fm_favorites(radio_sdk, db_session) -> None:
    a = await _insert_fm(db_session, call_sign="WQHH")
    await _insert_fm(db_session, call_sign="WJIM", favorited=False)   # skipped
    b = await _insert_fm(db_session, call_sign="WKAR")
    _set_radio_browser_client_for_tests(
        _FixedRadioBrowser([_hit("WQHH live"), _hit("WKAR live")])
    )
    results = await simulcast.backfill_fm_favorites_missing_simulcast(radio_sdk)
    assert {r.station_id for r in results} == {a, b}
    assert all(r.resolved for r in results)
