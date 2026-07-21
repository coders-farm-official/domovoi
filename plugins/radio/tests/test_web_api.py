"""The plugin's web router (design §5.1/§9.1): stations CRUD, search
proxy, detections feed, badge, the core-endpoint proxies, the browser
stream proxy's honest 409s, and the realtime snapshot contract."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from domovoi.db.session import SessionLocal
from domovoi.tests.conftest import requires_db

pytestmark = requires_db


def _seed_station(client, **kw):
    body = {"name": "KEXP", "source": "online",
            "stream_url": "http://kexp.example/stream.mp3",
            "external_id": "uuid-kexp", "tags": ["indie"]}
    body.update(kw)
    resp = client.post("/api/plugins/radio/stations", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture(autouse=True)
def _clean_tables(web_client):
    """The web tier drives its own writes — truncate the plugin schema
    around each test (the db_session fixture isn't in play here)."""
    async def _truncate():
        async with SessionLocal() as s:
            async with s.begin():
                await s.execute(
                    text(
                        "TRUNCATE plugin_radio.radio_detections, "
                        "plugin_radio.track_fingerprints, "
                        "plugin_radio.radio_stations RESTART IDENTITY CASCADE"
                    )
                )

    asyncio.run(_truncate())
    yield


# ─── Stations CRUD ───────────────────────────────────────────────────────


def test_create_and_list_station(web_client) -> None:
    created = _seed_station(web_client)
    assert created["favorited"] is True
    assert created["id"] > 0

    listed = web_client.get(
        "/api/plugins/radio/stations", params={"favorited_only": True}
    ).json()
    assert [s["name"] for s in listed] == ["KEXP"]


def test_create_idempotent_on_external_id(web_client) -> None:
    a = _seed_station(web_client)
    b = _seed_station(web_client)          # same external_id → same row
    assert a["id"] == b["id"]
    listed = web_client.get("/api/plugins/radio/stations").json()
    assert len(listed) == 1


def test_patch_station_interval_and_unfavorite(web_client) -> None:
    st = _seed_station(web_client)
    patched = web_client.patch(
        f"/api/plugins/radio/stations/{st['id']}",
        json={"sample_interval_sec": 300, "favorited": False},
    ).json()
    assert patched["sample_interval_sec"] == 300
    assert patched["favorited"] is False


def test_patch_404_and_empty_body(web_client) -> None:
    assert web_client.patch(
        "/api/plugins/radio/stations/9999", json={"favorited": True}
    ).status_code == 404
    st = _seed_station(web_client)
    assert web_client.patch(
        f"/api/plugins/radio/stations/{st['id']}", json={}
    ).status_code == 400


def test_delete_station(web_client) -> None:
    st = _seed_station(web_client)
    assert web_client.delete(
        f"/api/plugins/radio/stations/{st['id']}"
    ).status_code == 204
    assert web_client.delete(
        f"/api/plugins/radio/stations/{st['id']}"
    ).status_code == 404


def test_fm_frequency_filter_uses_numeric_cast(web_client) -> None:
    _seed_station(
        web_client, name="WKAR", source="fm", stream_url=None,
        external_id="fcc-1", frequency_mhz=90.5, call_sign="WKAR",
    )
    hits = web_client.get(
        "/api/plugins/radio/stations",
        params={"source": "fm", "frequency_mhz": 90.5},
    ).json()
    assert [h["name"] for h in hits] == ["WKAR"]
    assert web_client.get(
        "/api/plugins/radio/stations", params={"source": "nope"}
    ).status_code == 400


# ─── Search proxy ────────────────────────────────────────────────────────


def test_search_flags_already_persisted_hits(web_client) -> None:
    # The stub directory returns external_ids stub-jazz-0/1/2; persist
    # one of them first and the search must flag it.
    _seed_station(web_client, name="Stub jazz 1", external_id="stub-jazz-0")
    hits = web_client.get(
        "/api/plugins/radio/search", params={"q": "jazz"}
    ).json()
    assert len(hits) == 3
    flagged = {h["external_id"]: (h["id"], h["favorited"]) for h in hits}
    assert flagged["stub-jazz-0"][0] > 0
    assert flagged["stub-jazz-0"][1] is True
    assert flagged["stub-jazz-1"] == (0, False)


def test_search_empty_query_returns_empty(web_client) -> None:
    assert web_client.get("/api/plugins/radio/search").json() == []


# ─── Detections + badge ─────────────────────────────────────────────────


def test_detections_feed_and_cursor(web_client) -> None:
    st = _seed_station(web_client)

    async def _seed_dets():
        async with SessionLocal() as s:
            async with s.begin():
                for i in range(3):
                    await s.execute(
                        text(
                            "INSERT INTO plugin_radio.radio_detections "
                            "(station_id, artist, title, fingerprint_source) "
                            "VALUES (:sid, 'A', :t, 'icy')"
                        ),
                        {"sid": st["id"], "t": f"T{i}"},
                    )

    asyncio.run(_seed_dets())
    feed = web_client.get(
        "/api/plugins/radio/detections", params={"station_id": st["id"]}
    ).json()
    assert [d["title"] for d in feed] == ["T2", "T1", "T0"]   # newest first
    paged = web_client.get(
        "/api/plugins/radio/detections", params={"before": feed[0]["id"]}
    ).json()
    assert [d["title"] for d in paged] == ["T1", "T0"]


def test_badge_counts_favorites(web_client) -> None:
    assert web_client.get("/api/plugins/radio/badge").json() == {"favorites": 0}
    _seed_station(web_client)
    assert web_client.get("/api/plugins/radio/badge").json() == {"favorites": 1}


# ─── Core-endpoint proxies (no core imports — recorded calls) ───────────


def test_fcc_import_proxies_to_core(web_client) -> None:
    core_proxy = web_client.ctx.core
    core_proxy.responses["/v1/plugins/radio/fcc-import"] = {"started": True}
    out = web_client.post("/api/plugins/radio/fcc-import").json()
    assert out == {"started": True}
    (call,) = core_proxy.calls
    assert call["method"] == "POST_ADMIN"
    assert call["path"] == "/v1/plugins/radio/fcc-import"


def test_resolve_simulcast_proxies_to_core(web_client) -> None:
    core_proxy = web_client.ctx.core
    core_proxy.responses["/v1/plugins/radio/stations/7/resolve-simulcast"] = {
        "resolved": True, "station_id": 7,
        "stream_url": "http://sim/x", "message": "resolved",
    }
    out = web_client.post(
        "/api/plugins/radio/stations/7/resolve-simulcast"
    ).json()
    assert out["resolved"] is True
    (call,) = core_proxy.calls
    assert call["method"] == "POST_ADMIN"


# ─── Browser stream proxy ────────────────────────────────────────────────


def test_stream_proxy_404_unknown_station(web_client) -> None:
    assert web_client.get(
        "/api/plugins/radio/stations/9999/stream"
    ).status_code == 404


def test_stream_proxy_409_for_fm_without_url(web_client) -> None:
    st = _seed_station(
        web_client, name="WKAR", source="fm", stream_url=None,
        external_id="fcc-wkar", frequency_mhz=90.5,
    )
    resp = web_client.get(f"/api/plugins/radio/stations/{st['id']}/stream")
    assert resp.status_code == 409
    assert "satellite room" in resp.json()["detail"]


def test_stream_proxy_409_for_host_local_url(web_client) -> None:
    st = _seed_station(
        web_client, name="FMTMP", external_id="x-local",
        stream_url="http://127.0.0.1:6391/fm.mp3",
    )
    resp = web_client.get(f"/api/plugins/radio/stations/{st['id']}/stream")
    assert resp.status_code == 409
    assert "host-local" in resp.json()["detail"]


# ─── Realtime snapshots (§5.3 contract) ─────────────────────────────────


def test_snapshots_are_verbatim_json_payloads(web_client) -> None:
    from domovoi_plugin_radio import web as radio_web

    st = _seed_station(web_client)

    async def _run():
        async with web_client.ctx.db_session_scope() as s:
            stations = await radio_web.snapshot_stations(s)
            detections = await radio_web.snapshot_detections(s)
        return stations, detections

    stations, detections = asyncio.run(_run())
    assert stations["stations"][0]["name"] == "KEXP"
    assert detections == {"detections": []}
    # SNAPSHOTS is what the manifest [[realtime]] wiring resolves.
    assert set(radio_web.SNAPSHOTS) == {
        "snapshot_stations", "snapshot_detections",
    }
    # The GET-flood regression guard: nothing elapsed-shaped in payloads.
    assert "elapsed_sec" not in str(stations)
