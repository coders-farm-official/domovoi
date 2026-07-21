"""Resolve internet simulcasts for FM stations via radio-browser.

The FCC bulk import populates ``radio_stations`` with call sign,
frequency, and market — everything except a playable stream URL.
radio-browser indexes most US FM stations' network/website simulcasts;
this module bridges the two by querying radio-browser by call sign and
stamping the best hit's ``url_resolved`` onto our FM row.

Two surfaces:

* The plugin web endpoint
  ``POST /api/plugins/radio/stations/{id}/resolve-simulcast`` →
  proxied to the plugin's core endpoint, which calls
  :func:`resolve_simulcast_for_station` (invoked by the dashboard right
  after an FM row is favorited, and by an explicit "Resolve" button).
* The ``simulcast_backfill`` startup hook —
  :func:`backfill_fm_favorites_missing_simulcast` — catches FM
  favorites created before resolution existed (or while offline).

Best-hit selection is deliberately conservative:

1. radio-browser's own ``clickcount`` ordering (what real listeners
   click on) — the client already requests it.
2. The call sign must appear as a separate word-bounded token in the
   hit's name: real call signs are token-separated ("WQHH 96.5",
   "WQHH-FM"), while shared-prefix call signs run together ("WQHHE-FM")
   where ``\\b`` correctly fails.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text

from domovoi.sdk import PluginSDK

from domovoi_plugin_radio.clients.radio_browser import (
    RadioBrowserStation,
    get_radio_browser_client,
)

log = logging.getLogger(__name__)


@dataclass
class ResolveResult:
    """One resolve attempt's outcome — returned verbatim by the API so
    the dashboard can toast without re-querying."""

    resolved: bool
    station_id: int
    stream_url: str | None = None
    external_id: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "resolved": self.resolved,
            "station_id": self.station_id,
            "stream_url": self.stream_url,
            "external_id": self.external_id,
            "message": self.message,
        }


async def resolve_simulcast_for_station(
    sdk: PluginSDK, station_id: int
) -> ResolveResult:
    """Populate ``stream_url`` for an FM station from radio-browser.

    Idempotent: rows that already carry a stream_url are left alone
    (``resolved=False`` + "already has" message), so the dashboard
    button and the boot backfill are both safe to spam.
    """
    async with sdk.db.session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, call_sign, country_code, source, stream_url "
                    "FROM radio_stations WHERE id = :id"
                ),
                {"id": station_id},
            )
        ).first()
    if row is None:
        return ResolveResult(
            resolved=False, station_id=station_id,
            message=f"station {station_id} not found",
        )

    sid = int(row[0])
    call_sign = (row[1] or "").strip()
    country_code = (row[2] or "").strip() or None
    source = row[3]
    existing_url = (row[4] or "").strip() or None

    if existing_url:
        return ResolveResult(
            resolved=False, station_id=sid, stream_url=existing_url,
            message="already has a stream URL",
        )
    if not call_sign:
        return ResolveResult(
            resolved=False, station_id=sid, message="no call sign to look up",
        )
    if source != "fm":
        # Lookup-by-callsign is FM-only by intent: online rows are
        # curated via the search UI, and resolving by name there could
        # overwrite a deliberate manual URL with something worse.
        return ResolveResult(
            resolved=False, station_id=sid,
            message=f"source is {source!r}, not 'fm'; skipping",
        )

    client = get_radio_browser_client(
        use_stubs=bool(sdk.core_config.use_stubs)
    )
    hits = await client.search(name=call_sign, country_code=country_code, limit=10)

    best = pick_best_match(hits, call_sign)
    if best is None:
        return ResolveResult(
            resolved=False, station_id=sid,
            message=f"no simulcast directory hit for {call_sign!r}",
        )

    async with sdk.db.session_scope() as s:
        updated = await s.execute(
            text(
                """
                UPDATE radio_stations
                SET stream_url = :stream_url,
                    external_id = COALESCE(NULLIF(external_id, ''), :external_id),
                    updated_at = NOW()
                WHERE id = :id
                RETURNING id
                """
            ),
            {"id": sid, "stream_url": best.stream_url, "external_id": best.external_id},
        )
        if updated.first() is None:
            # Row deleted between SELECT and UPDATE — odd, not fatal.
            return ResolveResult(
                resolved=False, station_id=sid, message="row vanished mid-resolve",
            )
        # Same NOTIFY the favorites mutations fire so the dashboard's
        # list refetches with the new stream_url within ~50 ms.
        await sdk.realtime.notify(s, "stations_changed", "simulcast_resolved")

    log.info("simulcast: %s → %s (id=%d)", call_sign, best.stream_url, sid)
    return ResolveResult(
        resolved=True, station_id=sid,
        stream_url=best.stream_url, external_id=best.external_id,
        message=f"resolved {call_sign} → {best.name}",
    )


def pick_best_match(
    hits: list[RadioBrowserStation], call_sign: str
) -> RadioBrowserStation | None:
    """Most plausible simulcast hit for a call sign — first
    popularity-ordered hit whose name contains the call sign as a
    word-bounded token (see module docstring)."""
    if not hits or not call_sign:
        return None
    pattern = re.compile(rf"\b{re.escape(call_sign)}\b", re.IGNORECASE)
    for hit in hits:
        if pattern.search(hit.name or ""):
            return hit
    return None


async def backfill_fm_favorites_missing_simulcast(
    sdk: PluginSDK, *, max_stations: int = 25
) -> list[ResolveResult]:
    """The ``simulcast_backfill`` startup hook body: walk FM favorites
    with no stream_url and try to resolve each.

    Bounded by ``max_stations`` so a misconfigured deployment can't fire
    1000+ directory requests on boot; sequential rather than concurrent
    because radio-browser asks clients to be polite (~1 rps finishes a
    25-station backfill inside half a minute).
    """
    async with sdk.db.session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT id
                    FROM radio_stations
                    WHERE favorited
                      AND source = 'fm'
                      AND (stream_url IS NULL OR LENGTH(TRIM(stream_url)) = 0)
                      AND call_sign IS NOT NULL
                    ORDER BY id
                    LIMIT :limit
                    """
                ),
                {"limit": max_stations},
            )
        ).all()
    ids = [int(r[0]) for r in rows]
    if not ids:
        return []

    log.info(
        "simulcast: backfilling %d FM favorite(s) without stream URLs", len(ids)
    )
    results: list[ResolveResult] = []
    for sid in ids:
        try:
            results.append(await resolve_simulcast_for_station(sdk, sid))
        except Exception as e:
            log.warning(
                "simulcast: id=%d raised %s: %s", sid, type(e).__name__, e
            )
            results.append(
                ResolveResult(
                    resolved=False, station_id=sid,
                    message=f"unexpected error: {e}",
                )
            )
    return results
