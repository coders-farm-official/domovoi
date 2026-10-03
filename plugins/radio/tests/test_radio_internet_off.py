"""The radio plugin under the server's internet answer (B9, B5 radio).

* Offline (``sdk.connectivity.online`` false, which includes
  ``INTERNET_ACCESS=never``): the sampler samples only stations on the
  house network, leaves internet stations' ``last_sampled_at`` alone, and
  never asks Shazam.
* Under never: the directory search, the simulcast lookup (and its boot
  backfill), the FCC import (and its boot import), playing an internet
  station and its stream proxy all refuse — 409 with the turned-off
  reason from the routes, before any request, job, or proxy call. Saving a
  station and FM stay allowed.
* The clients send nothing under never; the voice handler never hands MPD
  an internet stream.
* The manifest is 1.3.0 and needs SDK ``>=1.4``.

These tests live outside ``domovoi/tests``, so they do not get the core
suite's autouse policy pin: every policy-dependent test sets it with
``egress.override_policy(...)`` itself.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from domovoi import egress
from domovoi.tests.conftest import requires_db

from domovoi_plugin_radio.clients import radio_browser, shazam_stream
from domovoi_plugin_radio.clients.fcc_fm import RealFccFmClient
from domovoi_plugin_radio.workers import fcc_import, simulcast
from domovoi_plugin_radio.workers.sampler import RadioSampler, _stream_is_local

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REASON = egress.TURNED_OFF_REASON
INTERNET_URL = "http://kexp.example/stream.mp3"
LAN_URL = "http://192.168.1.50:8000/fm.mp3"


def _boom(*a, **k):
    raise AssertionError(f"must not run under never: {a!r} {k!r}")


class _Offline:
    """sdk.connectivity while the box is set to stay off the internet."""

    online = False
    policy = "never"
    reason = "turned_off"
    internet_allowed = False


def _assert_internet_off(resp) -> None:
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == REASON
    assert resp.headers.get("X-Domovoi-Refusal") == "internet-off"


# ─── sampler (B9) ────────────────────────────────────────────────────────


def test_local_streams_are_the_house_network() -> None:
    assert _stream_is_local(LAN_URL)
    assert _stream_is_local("http://127.0.0.1:8090/fm")
    assert _stream_is_local("http://sdr-box.local:8090/fm")
    assert not _stream_is_local(INTERNET_URL)
    assert not _stream_is_local("not a url")


async def _insert(session, name: str, url: str) -> int:
    row = await session.execute(
        text(
            "INSERT INTO plugin_radio.radio_stations "
            "(name, source, stream_url, favorited) "
            "VALUES (:n, 'online', :u, TRUE) RETURNING id"
        ),
        {"n": name, "u": url},
    )
    await session.commit()
    return int(row.scalar_one())


async def _last_sampled(session, sid: int):
    return (await session.execute(
        text("SELECT last_sampled_at FROM plugin_radio.radio_stations WHERE id = :id"),
        {"id": sid},
    )).scalar_one()


@requires_db
@pytest.mark.asyncio
async def test_offline_sampler_samples_only_house_streams(radio_sdk, db_session, monkeypatch) -> None:
    internet = await _insert(db_session, "KEXP", INTERNET_URL)
    house = await _insert(db_session, "FM box", LAN_URL)
    grabbed: list[str] = []

    async def grab(url, duration_sec, *, timeout_sec=20.0):
        grabbed.append(url)
        return None          # "dead stream": the sampler bumps and moves on

    monkeypatch.setattr(shazam_stream, "grab_to_tempfile", grab)
    radio_sdk.connectivity = _Offline()
    sampler = RadioSampler(radio_sdk)
    assert await sampler.tick() == 1
    assert grabbed == [LAN_URL]
    # The internet station is not marked as sampled: it is due again the
    # moment the internet is back.
    await db_session.rollback()
    assert await _last_sampled(db_session, internet) is None
    assert await _last_sampled(db_session, house) is not None


@requires_db
@pytest.mark.asyncio
async def test_offline_sampler_never_asks_shazam(radio_sdk, db_session, monkeypatch) -> None:
    from domovoi_plugin_radio.clients import fingerprint as fp

    async def no_local_match(session, wav_path, min_confidence=0):
        return None

    monkeypatch.setattr(fp, "match_sample", no_local_match)
    radio_sdk.connectivity = _Offline()
    sampler = RadioSampler(radio_sdk)
    monkeypatch.setattr(sampler, "_shazam", _boom)
    sampler._online = False
    assert await sampler._identify("unused.wav") == (None, None, None)


@requires_db
@pytest.mark.asyncio
async def test_online_tick_still_samples_everything(radio_sdk, db_session, monkeypatch) -> None:
    await _insert(db_session, "KEXP", INTERNET_URL)
    await _insert(db_session, "FM box", LAN_URL)
    grabbed: list[str] = []

    async def grab(url, duration_sec, *, timeout_sec=20.0):
        grabbed.append(url)
        return None

    monkeypatch.setattr(shazam_stream, "grab_to_tempfile", grab)
    with egress.override_policy(""):
        assert await RadioSampler(radio_sdk).tick() == 2
    assert sorted(grabbed) == sorted([INTERNET_URL, LAN_URL])


# ─── simulcast + FCC (core side) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_simulcast_refuses_without_a_lookup(stub_sdk, monkeypatch) -> None:
    monkeypatch.setattr(simulcast, "get_radio_browser_client", _boom)
    with egress.override_policy("never"):
        result = await simulcast.resolve_simulcast_for_station(stub_sdk, 7)
        backfill = await simulcast.backfill_fm_favorites_missing_simulcast(stub_sdk)
    assert result.to_dict() == {
        "resolved": False, "station_id": 7, "stream_url": None,
        "external_id": None, "message": REASON, "status": "internet_off",
    }
    assert backfill == []


@pytest.mark.asyncio
async def test_fcc_import_refuses_under_never(stub_sdk, monkeypatch) -> None:
    monkeypatch.setattr(fcc_import, "get_fcc_fm_client", _boom)
    stub_sdk.config.fcc_import_on_boot = True
    with egress.override_policy("never"):
        started = fcc_import.start_import_job(stub_sdk, "CO")
        with pytest.raises(egress.InternetTurnedOff):
            await fcc_import.import_state(stub_sdk, "CO")
        await fcc_import.boot_import(stub_sdk)     # a quiet no-op
    assert started["started"] is False and started["error"] == REASON
    assert "fcc_import_job" not in stub_sdk.state


def _core_client(stub_sdk):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from domovoi_plugin_radio.core import _build_core_router

    app = FastAPI()
    app.include_router(_build_core_router(stub_sdk), prefix="/v1/plugins/radio")
    return TestClient(app)


def test_core_routes_refuse_under_never(stub_sdk, monkeypatch) -> None:
    monkeypatch.setattr(fcc_import, "start_import_job", _boom)
    monkeypatch.setattr(simulcast, "resolve_simulcast_for_station", _boom)
    with _core_client(stub_sdk) as client, egress.override_policy("never"):
        _assert_internet_off(client.post("/v1/plugins/radio/fcc-import"))
        _assert_internet_off(client.post("/v1/plugins/radio/stations/3/resolve-simulcast"))
        # The status read stays open.
        assert client.get("/v1/plugins/radio/fcc-import").status_code == 200


# ─── clients send nothing ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_directory_client_sends_nothing(monkeypatch) -> None:
    monkeypatch.setattr(radio_browser.egress, "async_client", _boom)
    client = radio_browser.RealRadioBrowserClient()
    with egress.override_policy("never"):
        assert await client.search(name="jazz") == []


@pytest.mark.asyncio
async def test_fcc_client_request_is_refused_by_the_hook(monkeypatch) -> None:
    """The client is built by egress.async_client, whose request hook runs
    before any transport: under never the request never leaves."""
    real = httpx.AsyncClient

    def factory(*a, **k):
        k["transport"] = httpx.MockTransport(_boom)
        return real(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    with egress.override_policy("never"):
        assert await RealFccFmClient().fetch_state("CO") == []


@pytest.mark.asyncio
async def test_sampling_an_internet_stream_spawns_no_ffmpeg(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    with egress.override_policy("never"):
        assert await shazam_stream.grab_to_tempfile(INTERNET_URL, 15) is None


@pytest.mark.asyncio
async def test_shazam_is_never_asked_under_never(monkeypatch) -> None:
    import sys
    import types

    fake = types.ModuleType("shazamio")
    fake.Shazam = _boom
    monkeypatch.setitem(sys.modules, "shazamio", fake)
    with egress.override_policy("never"):
        assert await shazam_stream._shazam_recognize(Path("x.wav")) is None


# ─── the voice handler ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handler_never_hands_mpd_an_internet_stream(stub_sdk) -> None:
    from domovoi.sdk import Context

    from domovoi_plugin_radio.handlers.radio import RadioHandler

    handler = RadioHandler(stub_sdk)
    ctx = Context(room_id="kitchen", online=False)
    station = {"id": 1, "name": "KEXP", "source": "online", "stream_url": INTERNET_URL}
    with egress.override_policy("never"):
        resp = await handler._stream_station_row(station, ctx, None)
    assert resp.text == "I'm set to stay off the internet, so I can't stream KEXP. FM stations still work."
    assert stub_sdk.playback.calls == []
    # A stream on the house network still plays.
    lan = {**station, "name": "FM box", "stream_url": LAN_URL}
    with egress.override_policy("never"):
        ok = await handler._stream_station_row(lan, ctx, None)
    assert ok.text == "Streaming FM box."
    assert [c["stream_url"] for c in stub_sdk.playback.calls] == [LAN_URL]


# ─── web routes ──────────────────────────────────────────────────────────


@requires_db
def test_web_routes_refuse_under_never(web_client, monkeypatch) -> None:
    monkeypatch.setattr(radio_browser, "get_radio_browser_client", _boom)
    from domovoi_plugin_radio import web as radio_web

    monkeypatch.setattr(radio_web, "get_radio_browser_client", _boom)
    with egress.override_policy("never"):
        _assert_internet_off(web_client.get("/api/plugins/radio/search?q=jazz"))
        _assert_internet_off(web_client.post("/api/plugins/radio/stations/1/resolve-simulcast"))
        _assert_internet_off(web_client.post("/api/plugins/radio/fcc-import"))
        _assert_internet_off(web_client.post(
            "/api/plugins/radio/play",
            json={"name": "KEXP", "source": "online", "stream_url": INTERNET_URL},
        ))
        # Saving a station fetches nothing: allowed.
        saved = web_client.post(
            "/api/plugins/radio/stations",
            json={"name": "KEXP", "source": "online", "stream_url": INTERNET_URL},
        )
        assert saved.status_code == 201, saved.text
        sid = saved.json()["id"]
        # Playing it by id, and its browser stream, are refused.
        _assert_internet_off(web_client.post("/api/plugins/radio/play", json={"station_id": sid}))
        _assert_internet_off(web_client.get(f"/api/plugins/radio/stations/{sid}/stream"))
        # FM plays (a tuner in the house).
        fm = web_client.post(
            "/api/plugins/radio/stations",
            json={"name": "KZZZ", "source": "fm", "frequency_mhz": 97.5},
        )
        assert fm.status_code == 201, fm.text
        played = web_client.post("/api/plugins/radio/play", json={"station_id": fm.json()["id"]})
        assert played.status_code == 200, played.text
    # Nothing was proxied to the core.
    assert web_client.ctx.core.calls == []


# ─── manifest ────────────────────────────────────────────────────────────


def test_manifest_is_1_3_0_and_needs_sdk_1_4() -> None:
    import tomllib

    manifest = tomllib.loads((PLUGIN_DIR / "domovoi-plugin.toml").read_text(encoding="utf-8"))
    assert manifest["plugin"]["version"] == "1.3.0"
    assert manifest["plugin"]["domovoi_api"] == ">=1.4,<2.0"
