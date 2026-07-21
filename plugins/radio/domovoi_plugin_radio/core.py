"""Core-process entry point: ``register(ctx)`` (design §2.1, §9).

Everything the radio plugin contributes to the voice process is wired
here, against the injected :class:`~domovoi.sdk.PluginSDK`:

* config (``RADIO_*`` FieldSpecs),
* the ``radio`` now-playing source + favorites matcher,
* the RadioHandler at band 280,
* four poll workers + two connectivity-gated startup hooks
  (+ an ``sdr_probe`` hook when SDR hardware is enabled),
* the plugin's core HTTP router (``/v1/plugins/radio/...``) — the FCC
  import runs as a background job behind an admin-gated endpoint, never
  a blocking ~30 s request (locked 19),
* event-bus subscriptions for soft-ref cleanup/correlation — paired
  with the reaper's reconciliation sweep because the bus is
  fire-and-forget (design §4.9).

Import-time discipline (design §4.1): nothing here imports numpy/scipy/
librosa/shazamio — the heavy audio deps load lazily inside worker and
client bodies.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from domovoi.sdk import PluginSDK

from domovoi_plugin_radio import SCHEMA
from domovoi_plugin_radio.clients.rtl_sdr import SdrTuner
from domovoi_plugin_radio.handlers.radio import RadioHandler
from domovoi_plugin_radio.settings import RADIO_FIELDSPECS, RadioSettings
from domovoi_plugin_radio.workers import (
    RadioDetectionsReaper,
    RadioIcyPoller,
    RadioSampler,
    TrackFingerprinter,
)
from domovoi_plugin_radio.workers import fcc_import, simulcast

log = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    sdk: PluginSDK = ctx.sdk

    # ── Config first — workers and the handler read sdk.config. ─────────
    ctx.add_config(RadioSettings, RADIO_FIELDSPECS)

    # ── Now-playing plane (§4.7): source + favorites matcher. ──────────
    # Explicit registration — the manifest's declared
    # "now-playing-source:radio" / "now-playing-matcher" capabilities are
    # contract-checked against exactly these calls (design §2.2).
    sdk.now_playing.register_source("radio")
    sdk.now_playing.register_matcher("radio", _make_matcher(sdk))

    # ── Voice plane. ────────────────────────────────────────────────────
    ctx.add_handler(RadioHandler(sdk))

    # ── Background work. ────────────────────────────────────────────────
    ctx.add_worker(RadioSampler(sdk))
    ctx.add_worker(RadioIcyPoller(sdk))
    ctx.add_worker(RadioDetectionsReaper(sdk))
    ctx.add_worker(TrackFingerprinter(sdk))

    async def _fcc_boot_import() -> None:
        await fcc_import.boot_import(sdk)

    async def _simulcast_backfill() -> None:
        await simulcast.backfill_fm_favorites_missing_simulcast(sdk)

    ctx.add_startup_hook(
        _fcc_boot_import, name="fcc_import", requires_online=True,
        after="core.db_ready",
    )
    ctx.add_startup_hook(
        _simulcast_backfill, name="simulcast_backfill", requires_online=True,
        after="core.db_ready",
    )

    # ── FM tuner hardware (optional). ───────────────────────────────────
    # The tuner instance is shared through the plugin's namespaced state
    # slice (sdk.state — single-process by design), never a module
    # global. The probe runs as a startup hook; a failed probe removes
    # the tuner so the handler degrades to its friendly explainer.
    if sdk.config.sdr_enabled:
        tuner = SdrTuner(
            enabled=True,
            device_index=int(sdk.config.sdr_device_index),
            http_port=int(sdk.config.sdr_http_port),
            stream_base=str(sdk.config.sdr_stream_base),
        )
        sdk.state["sdr_tuner"] = tuner

        async def _sdr_probe() -> None:
            ok = await tuner.probe()
            if not ok:
                sdk.state.pop("sdr_tuner", None)
                log.info("radio: SDR probe failed — FM tuning disabled")

        ctx.add_startup_hook(_sdr_probe, name="sdr_probe")

    # ── HTTP (core-side admin/job endpoints, §4.11). ────────────────────
    ctx.add_router(_build_core_router(sdk))

    # ── Soft-ref cleanup + correlation (fast path; the reaper's sweep is
    #    the durable path — design §4.9/§6.1/§9.2). ───────────────────────
    async def _on_library_track_deleted(event: Any) -> None:
        payload = getattr(event, "payload", None) or {}
        track_id = payload.get("track_id")
        if track_id is None:
            return
        async with sdk.db.session_scope() as s:
            await s.execute(
                text(
                    f"DELETE FROM {SCHEMA}.track_fingerprints "
                    "WHERE library_track_id = :t"
                ),
                {"t": int(track_id)},
            )
            await s.execute(
                text(
                    f"UPDATE {SCHEMA}.radio_detections "
                    "SET library_track_id = NULL WHERE library_track_id = :t"
                ),
                {"t": int(track_id)},
            )

    sdk.events.subscribe("core.library_track_deleted", _on_library_track_deleted)

    # ── Disable teardown: stop the SDR pipeline cleanly. ────────────────
    async def _on_disable() -> None:
        tuner = sdk.state.get("sdr_tuner")
        if tuner is not None:
            try:
                await tuner.stop()
            except Exception as e:  # noqa: BLE001 — teardown isolation
                log.warning("radio: SDR stop during disable failed: %s", e)

    ctx.on_disable(_on_disable)

    log.info("radio plugin registered (band 280, 4 workers, 2-3 hooks)")


# ─── Favorites matcher (now-playing attribution chain, §4.7/§9.4) ────────


def _make_matcher(sdk: PluginSDK):
    async def match_now_playing(
        session: Any,
        *,
        mpd_file: str | None = None,
        title: str | None = None,
        **_: Any,
    ) -> dict[str, Any] | None:
        """Attribute a room's playing stream to a radio station.

        1. Byte-equality ``stream_url`` match — the handler plays the
           persisted stream_url verbatim, so the row's URL is exactly
           what MPD's currentsong reports (online stations).
        2. Station-name-as-MPD-title match — the FM/SDR pipeline's URL
           is a transient local target, but the handler stamps the
           station name as the MPD title before playback (the play_url
           stamping contract), so a case-insensitive name match
           identifies the station.

        Returns ``{"kind": "radio", "station_id", "name", "favorited"}``
        or None to pass to the next matcher in the chain.
        """
        row = None
        if mpd_file:
            row = (
                await session.execute(
                    text(
                        f"SELECT id, name, favorited FROM {SCHEMA}.radio_stations "
                        "WHERE stream_url = :url LIMIT 1"
                    ),
                    {"url": mpd_file},
                )
            ).first()
        if row is None and title:
            row = (
                await session.execute(
                    text(
                        f"SELECT id, name, favorited FROM {SCHEMA}.radio_stations "
                        "WHERE LOWER(name) = LOWER(:name) LIMIT 1"
                    ),
                    {"name": title},
                )
            ).first()
        if row is None:
            return None
        return {
            "kind": "radio",
            "station_id": int(row[0]),
            "name": row[1],
            "favorited": bool(row[2]),
        }

    return match_now_playing


# ─── The plugin's core router (/v1/plugins/radio/...) ────────────────────
#
# Mutations are admin-gated BY DEFAULT (design §4.11) — none of these
# opt out, so the FCC import trigger and the simulcast resolver require
# an admin session once setup has run. GETs are open reads.


def _build_core_router(sdk: PluginSDK) -> APIRouter:
    router = APIRouter()

    @router.post("/fcc-import")
    async def start_fcc_import(state: str | None = Query(default=None)):
        """Start the FCC FM bulk import as a BACKGROUND job (locked 19)
        and return immediately; poll the GET for progress."""
        return fcc_import.start_import_job(sdk, state)

    @router.get("/fcc-import")
    async def fcc_import_status():
        return fcc_import.job_status(sdk)

    @router.post("/stations/{station_id}/resolve-simulcast")
    async def resolve_simulcast(station_id: int):
        result = await simulcast.resolve_simulcast_for_station(sdk, station_id)
        return result.to_dict()

    # NOTE: named /state, not /status — the core app already serves the
    # generic GET /v1/plugins/{slug}/status (worker/hook/handler health,
    # design §4.14) and that route, registered at import time, would
    # shadow a same-path plugin route mounted later.
    @router.get("/state")
    async def radio_state():
        """Small live-state read for dashboards: tuner presence +
        current frequency + FCC job state."""
        tuner = sdk.state.get("sdr_tuner")
        return {
            "sdr_available": tuner is not None,
            "sdr_frequency_mhz": getattr(tuner, "current_frequency_mhz", None),
            "fcc_import": fcc_import.job_status(sdk),
        }

    return router
