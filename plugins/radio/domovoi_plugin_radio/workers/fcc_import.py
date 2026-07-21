"""FCC FM bulk import — async job, never a blocking request (locked 19).

Running the import synchronously would hold an HTTP request open for
the FCC CGI's ~30 s. Instead the import is a
background job: the plugin's core endpoint (``core.py``) starts
:func:`start_import_job` and returns immediately; the dashboard polls
:func:`job_status`. The optional boot import
(``RADIO_FCC_IMPORT_ON_BOOT``) runs the same function as a
connectivity-gated startup hook.

Flow per import:

1. Fetch the FCC FM Query for the state via the fcc_fm client.
2. Upsert into ``radio_stations`` with ``source='fm'``,
   ``stream_url=NULL`` (FM rows have no inherent stream URL; the SDR
   handles playback, the simulcast resolver may add one), and
   ``favorited=FALSE``.
3. NOTIFY ``plugin_radio_stations_changed`` so the Stations page picks
   up the new rows.

Idempotent on ``external_id``: re-running the same state updates rows
in place. The upsert is a manual SELECT-then-UPDATE-or-INSERT because
the external_id index isn't (and can't be) UNIQUE — the radio-browser
path persists rows with NULL external_id. Two queries per row is fine
at FCC scale (~1000 rows, background job). The SELECT→INSERT race with
a concurrent manual favorite is documented + retried once.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from domovoi.sdk import PluginSDK

from domovoi_plugin_radio.clients.fcc_fm import FmStation, get_fcc_fm_client

log = logging.getLogger(__name__)

_JOB_STATE_KEY = "fcc_import_job"


@dataclass
class FccImportResult:
    """Counts for UI feedback — "imported 42 new, 15 updated for CO"."""

    inserted: int
    updated: int
    state: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "state": self.state,
        }


async def import_state(sdk: PluginSDK, state_code: str | None = None) -> FccImportResult:
    """Import FCC FM stations for ``state_code`` (default: the
    configured market state) into ``radio_stations``."""
    state = (state_code or sdk.config.market_state or "").strip().upper()
    if not state or len(state) != 2:
        log.warning("fcc import: invalid or missing state %r", state)
        return FccImportResult(inserted=0, updated=0, state=state or "")

    client = get_fcc_fm_client(use_stubs=bool(sdk.core_config.use_stubs))
    stations = await client.fetch_state(state)
    log.info("fcc import: fetched %d FM stations for %s", len(stations), state)

    if not stations:
        return FccImportResult(inserted=0, updated=0, state=state)

    inserted = 0
    updated = 0
    async with sdk.db.session_scope() as s:
        for st in stations:
            n_inserted, n_updated = await _upsert_one(s, st)
            inserted += n_inserted
            updated += n_updated
        if inserted or updated:
            await sdk.realtime.notify(s, "stations_changed", "fcc_import")

    log.info("fcc import: %s — %d inserted, %d updated", state, inserted, updated)
    return FccImportResult(inserted=inserted, updated=updated, state=state)


async def _upsert_one(s: Any, station: FmStation, *, _retried: bool = False) -> tuple[int, int]:
    """Upsert one FM station: (1, 0) on insert, (0, 1) on update."""
    existing = await s.execute(
        text("SELECT id FROM radio_stations WHERE external_id = :eid LIMIT 1"),
        {"eid": station.facility_id},
    )
    row = existing.first()
    if row is not None:
        await s.execute(
            text(
                """
                UPDATE radio_stations
                SET name = :name,
                    frequency_mhz = :freq,
                    market_city = :city,
                    market_state = :state,
                    call_sign = :cs,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {
                "id": int(row[0]),
                "name": station.call_sign,
                "freq": station.frequency_mhz,
                "city": station.market_city,
                "state": station.market_state,
                "cs": station.call_sign,
            },
        )
        return 0, 1

    try:
        await s.execute(
            text(
                """
                INSERT INTO radio_stations
                    (name, source, frequency_mhz, market_city, market_state,
                     call_sign, external_id, country_code, favorited)
                VALUES
                    (:name, 'fm', :freq, :city, :state, :cs, :eid, 'US', FALSE)
                """
            ),
            {
                "name": station.call_sign,
                "freq": station.frequency_mhz,
                "city": station.market_city,
                "state": station.market_state,
                "cs": station.call_sign,
                "eid": station.facility_id,
            },
        )
    except Exception:
        # SELECT→INSERT race with a concurrent writer creating the same
        # external_id. Retry once as an update; give up after that.
        if _retried:
            raise
        return await _upsert_one(s, station, _retried=True)
    return 1, 0


# ─── Async job wrapper (the endpoint's surface) ─────────────────────────


def job_status(sdk: PluginSDK) -> dict[str, Any]:
    """The last/current import job's state for the dashboard poll."""
    return dict(
        sdk.state.get(_JOB_STATE_KEY)
        or {"state": "idle", "result": None, "error": None}
    )


def start_import_job(sdk: PluginSDK, state_code: str | None = None) -> dict[str, Any]:
    """Kick off a background import unless one is already running.
    Returns the job status immediately (``state: running`` on start)."""
    current = sdk.state.get(_JOB_STATE_KEY) or {}
    if current.get("state") == "running":
        return {"started": False, **job_status(sdk)}

    sdk.state[_JOB_STATE_KEY] = {"state": "running", "result": None, "error": None}

    async def _run() -> None:
        try:
            result = await import_state(sdk, state_code)
            sdk.state[_JOB_STATE_KEY] = {
                "state": "done", "result": result.to_dict(), "error": None,
            }
        except Exception as e:  # noqa: BLE001 — job isolation
            log.warning("fcc import job failed: %s", e)
            sdk.state[_JOB_STATE_KEY] = {
                "state": "failed", "result": None, "error": str(e),
            }

    asyncio.create_task(_run(), name="radio-fcc-import")
    return {"started": True, **job_status(sdk)}


async def boot_import(sdk: PluginSDK) -> None:
    """The ``fcc_import`` startup hook body (connectivity-gated by the
    runner via ``requires_online=True``). No-op unless
    ``RADIO_FCC_IMPORT_ON_BOOT`` is set."""
    if not sdk.config.fcc_import_on_boot:
        return
    await import_state(sdk)
