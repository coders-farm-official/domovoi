"""Web-process entry point: ``register_web(ctx)`` (design §5.1, §9.1).

Runs in the SEPARATE web dashboard process (:6369). Hard rules, both
tripwired at install and enforced by the web process's import guard:

* never imports ``core.py`` or any ``domovoi.*`` module outside
  ``domovoi.webkit`` — this module touches only stdlib, FastAPI/
  pydantic/SQLAlchemy (present in the web process), and httpx (a
  declared requirement);
* DB access goes through ``ctx.db_session_scope`` (sessions arrive with
  ``search_path = plugin_radio, public`` preset);
* anything needing live core state (the FCC import job, the simulcast
  resolver) is PROXIED to the plugin's own core endpoints via
  ``ctx.core.post_admin`` — the web layer never imports core
  modules.

Router mounts at ``/api/plugins/radio``; static JSX at
``/plugins/radio/static``. ``SNAPSHOTS`` feeds the manifest-declared
realtime wiring (design §5.3): snapshot functions are called by the web
state poll loop AND on NOTIFY, and their return value is broadcast
verbatim on the mapped realtime channel — keep them cheap (and never
include anything elapsed_sec-shaped).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi_plugin_radio.clients.radio_browser import (
    RadioBrowserStation,
    get_radio_browser_client,
)

log = logging.getLogger(__name__)

_STREAM_CHUNK = 64 * 1024

# The full station column list, shared by every read endpoint AND the
# snapshot so a schema change can't drift between them.
_STATION_COLUMNS = """
    id, name, source, stream_url, frequency_mhz,
    market_city, market_state, call_sign, external_id,
    country_code, language, tags, favorited,
    sample_interval_sec, last_sampled_at,
    created_at, updated_at,
    now_playing, now_playing_updated_at, icy_supported
"""


# ─── Schemas ──────────────────────────────────────────────────────────────


class RadioStation(BaseModel):
    """One ``radio_stations`` row. The search proxy returns the same
    shape with ``id=0`` for hits not yet persisted, so the page can
    disambiguate "already favorited" without a second roundtrip."""

    id: int
    name: str
    source: str = "online"                    # 'online' | 'fm'
    stream_url: str | None = None
    frequency_mhz: float | None = None
    market_city: str | None = None
    market_state: str | None = None
    call_sign: str | None = None
    external_id: str | None = None
    country_code: str | None = None
    language: str | None = None
    tags: list[str] = Field(default_factory=list)
    favorited: bool = False
    sample_interval_sec: int = 180
    last_sampled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # ICY metadata cache; ``icy_supported`` is the tristate.
    now_playing: str | None = None
    now_playing_updated_at: datetime | None = None
    icy_supported: bool | None = None


class RadioStationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source: str = "online"
    stream_url: str | None = None
    frequency_mhz: float | None = None
    market_city: str | None = None
    market_state: str | None = None
    call_sign: str | None = None
    external_id: str | None = None
    country_code: str | None = None
    language: str | None = None
    tags: list[str] = Field(default_factory=list)
    sample_interval_sec: int = Field(default=180, ge=30, le=86400)


class RadioStationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    stream_url: str | None = None
    favorited: bool | None = None
    sample_interval_sec: int | None = Field(default=None, ge=30, le=86400)
    tags: list[str] | None = None


class RadioDetection(BaseModel):
    id: int
    station_id: int
    artist: str | None = None
    title: str | None = None
    detected_at: datetime
    fingerprint_source: str          # 'local' | 'shazam' | 'icy' (app-validated)
    in_library: bool = False
    library_track_id: int | None = None


# ─── Registration ─────────────────────────────────────────────────────────


def register_web(ctx: Any) -> None:
    ctx.add_router(build_router(ctx))


def build_router(ctx: Any) -> APIRouter:
    """Build the plugin's web router against a WebPluginContext-shaped
    object (``db_session_scope`` + ``core``). Split from
    :func:`register_web` so the test harness can mount it directly."""
    router = APIRouter(tags=["plugin:radio"])
    session_scope = ctx.db_session_scope

    # ── Search (proxy to the station directory) ──────────────────────

    @router.get("/search", response_model=list[RadioStation])
    async def search_stations(
        q: str | None = Query(default=None, description="Name substring"),
        country_code: str | None = Query(default=None, description="ISO 2-letter"),
        tag: str | None = Query(default=None),
        language: str | None = Query(default=None),
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[RadioStation]:
        """Proxy a radio-browser search; hits whose ``external_id`` is
        already persisted are flagged with that row's id + favorited
        state so the page renders the star correctly."""
        client = get_radio_browser_client(use_stubs=_use_stubs())
        hits = await client.search(
            name=q, country_code=country_code, tag=tag,
            language=language, limit=limit, offset=offset,
        )
        if not hits:
            return []

        external_ids = [h.external_id for h in hits]
        async with session_scope() as s:
            existing_rows = await s.execute(
                text(
                    "SELECT id, external_id, favorited FROM radio_stations "
                    "WHERE external_id = ANY(:ids)"
                ),
                {"ids": external_ids},
            )
            existing = {r[1]: (int(r[0]), bool(r[2])) for r in existing_rows.all()}
        return [_search_hit_to_station(h, existing) for h in hits]

    # ── Stations list + CRUD ──────────────────────────────────────────

    @router.get("/stations", response_model=list[RadioStation])
    async def list_stations(
        favorited_only: bool = Query(default=False),
        source: str | None = Query(default=None, description="online or fm"),
        q: str | None = Query(
            default=None,
            description="Substring vs name, call_sign, or market_city.",
        ),
        frequency_mhz: float | None = Query(
            default=None,
            description="Exact frequency match (NUMERIC(5,1) precision).",
        ),
        limit: int = Query(default=200, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> list[RadioStation]:
        where: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if favorited_only:
            where.append("favorited")
        if source:
            if source not in ("online", "fm"):
                raise HTTPException(status_code=400, detail=f"invalid source {source!r}")
            where.append("source = :source")
            params["source"] = source
        if q:
            # The three columns users type fragments of; ILIKE because
            # FCC data is upper-cased and user input usually isn't.
            where.append(
                "(name ILIKE :q "
                "OR COALESCE(call_sign, '') ILIKE :q "
                "OR COALESCE(market_city, '') ILIKE :q)"
            )
            params["q"] = f"%{q}%"
        if frequency_mhz is not None:
            # Cast to numeric — asyncpg refuses float-vs-NUMERIC(5,1)
            # comparison (same load-bearing cast the voice handler uses).
            where.append("frequency_mhz = (:freq)::numeric(5,1)")
            params["freq"] = frequency_mhz
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        async with session_scope() as s:
            rows = await s.execute(
                text(
                    f"""
                    SELECT {_STATION_COLUMNS}
                    FROM radio_stations
                    {where_sql}
                    ORDER BY favorited DESC, name ASC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
            return [_row_to_station(r) for r in rows.all()]

    @router.get("/stations/{station_id}", response_model=RadioStation)
    async def get_station(station_id: int) -> RadioStation:
        async with session_scope() as s:
            row = await s.execute(
                text(
                    f"SELECT {_STATION_COLUMNS} FROM radio_stations "
                    "WHERE id = :id"
                ),
                {"id": station_id},
            )
            result = row.first()
        if result is None:
            raise HTTPException(
                status_code=404, detail=f"station {station_id} not found"
            )
        return _row_to_station(result)

    @router.post("/stations", response_model=RadioStation, status_code=201)
    async def create_station(payload: RadioStationCreate) -> RadioStation:
        """Favorite a station (or create a manual entry). Idempotent on
        ``external_id``: an existing row just gets ``favorited=TRUE`` —
        exactly what the page's optimistic star toggle assumes."""
        if payload.source not in ("online", "fm"):
            raise HTTPException(
                status_code=400, detail=f"invalid source {payload.source!r}"
            )
        async with session_scope() as s:
            if payload.external_id:
                existing = await s.execute(
                    text("SELECT id FROM radio_stations WHERE external_id = :eid"),
                    {"eid": payload.external_id},
                )
                row = existing.first()
                if row is not None:
                    await s.execute(
                        text(
                            "UPDATE radio_stations SET favorited = TRUE, "
                            "updated_at = NOW() WHERE id = :id"
                        ),
                        {"id": int(row[0])},
                    )
                    await _notify_stations(s, "favorited")
                    fetched = await s.execute(
                        text(
                            f"SELECT {_STATION_COLUMNS} FROM radio_stations "
                            "WHERE id = :id"
                        ),
                        {"id": int(row[0])},
                    )
                    refreshed = fetched.first()
                    if refreshed is None:
                        raise HTTPException(
                            status_code=500, detail="lost row after favorite"
                        )
                    return _row_to_station(refreshed)

            inserted = await s.execute(
                text(
                    f"""
                    INSERT INTO radio_stations
                        (name, source, stream_url, frequency_mhz,
                         market_city, market_state, call_sign, external_id,
                         country_code, language, tags, favorited,
                         sample_interval_sec)
                    VALUES
                        (:name, :source, :stream_url, :frequency_mhz,
                         :market_city, :market_state, :call_sign, :external_id,
                         :country_code, :language, :tags, TRUE,
                         :sample_interval_sec)
                    RETURNING {_STATION_COLUMNS}
                    """
                ),
                payload.model_dump(),
            )
            result = inserted.first()
            await _notify_stations(s, "created")
        if result is None:
            raise HTTPException(status_code=500, detail="insert returned no row")
        return _row_to_station(result)

    @router.patch("/stations/{station_id}", response_model=RadioStation)
    async def patch_station(
        station_id: int, payload: RadioStationPatch
    ) -> RadioStation:
        """Partial update — most commonly the favorited flag or the
        per-station sample interval."""
        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="no fields provided")
        set_fragments = [f"{k} = :{k}" for k in updates]
        set_fragments.append("updated_at = NOW()")
        params: dict[str, Any] = {"id": station_id, **updates}

        async with session_scope() as s:
            row = await s.execute(
                text(
                    f"""
                    UPDATE radio_stations
                    SET {', '.join(set_fragments)}
                    WHERE id = :id
                    RETURNING {_STATION_COLUMNS}
                    """
                ),
                params,
            )
            result = row.first()
            if result is not None:
                # Don't fire on a PATCH that updated zero rows.
                await _notify_stations(s, "updated")
        if result is None:
            raise HTTPException(
                status_code=404, detail=f"station {station_id} not found"
            )
        return _row_to_station(result)

    @router.delete("/stations/{station_id}", status_code=204)
    async def delete_station(station_id: int) -> None:
        """Delete outright (cascades to detections via the intra-schema
        FK). "Changed my mind about this favorite" should PATCH
        ``favorited=false`` instead — that keeps the detection history."""
        async with session_scope() as s:
            result = await s.execute(
                text("DELETE FROM radio_stations WHERE id = :id"),
                {"id": station_id},
            )
            if (result.rowcount or 0) > 0:
                await _notify_stations(s, "deleted")
        if (result.rowcount or 0) == 0:
            raise HTTPException(
                status_code=404, detail=f"station {station_id} not found"
            )

    # ── Proxies to the plugin's CORE endpoints (no core imports) ─────

    @router.post("/stations/{station_id}/resolve-simulcast")
    async def resolve_simulcast(station_id: int) -> dict[str, Any]:
        """Look up an FM station's call sign in the online directory and
        persist the best simulcast URL. Proxied to the plugin's core
        endpoint (slug-relative path → /v1/plugins/radio/...)."""
        result = await ctx.core.post_admin(
            f"stations/{station_id}/resolve-simulcast"
        )
        if isinstance(result, dict) and "not found" in str(result.get("message", "")):
            raise HTTPException(status_code=404, detail=result["message"])
        return result

    @router.post("/fcc-import")
    async def fcc_import(state: str | None = Query(default=None)) -> dict[str, Any]:
        """Trigger the FCC FM bulk import as a background job on the
        core and return immediately (poll GET /fcc-import for status)."""
        path = "fcc-import" + (f"?state={state}" if state else "")
        return await ctx.core.post_admin(path)

    @router.get("/fcc-import")
    async def fcc_import_status() -> dict[str, Any]:
        return await ctx.core.get("/v1/plugins/radio/fcc-import")

    # ── Detections feed ───────────────────────────────────────────────

    @router.get("/detections", response_model=list[RadioDetection])
    async def list_detections(
        station_id: int | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        before: int | None = Query(
            default=None,
            description="Rows with id < this value (cursor pagination).",
        ),
    ) -> list[RadioDetection]:
        where: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if station_id is not None:
            where.append("station_id = :station_id")
            params["station_id"] = station_id
        if before is not None:
            where.append("id < :before")
            params["before"] = before
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        async with session_scope() as s:
            rows = await s.execute(
                text(
                    f"""
                    SELECT id, station_id, artist, title, detected_at,
                           fingerprint_source, in_library, library_track_id
                    FROM radio_detections
                    {where_sql}
                    ORDER BY id DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
            return [_row_to_detection(r) for r in rows.all()]

    # ── Sidebar badge (open GET — design §5.3 badge contract) ─────────

    @router.get("/badge")
    async def badge() -> dict[str, int]:
        async with session_scope() as s:
            row = await s.execute(
                text("SELECT COUNT(*) FROM radio_stations WHERE favorited")
            )
            count = int(row.scalar_one())
        return {"favorites": count}

    # ── Browser stream proxy (the plugin's own route — §9.1) ──────────

    @router.get("/stations/{station_id}/stream")
    async def station_stream(station_id: int):
        """Proxy a station's audio through the web backend so the
        browser player dodges CORS / mixed-content on arbitrary station
        URLs. FM/SDR rows resolve to a transient host-local URL the
        browser can't reach — those get an honest 409."""
        async with session_scope() as s:
            row = await s.execute(
                text(
                    "SELECT name, source, stream_url FROM radio_stations "
                    "WHERE id = :id"
                ),
                {"id": station_id},
            )
            station = row.first()
        if station is None:
            raise HTTPException(
                status_code=404, detail=f"station {station_id} not found"
            )

        name, source, stream_url = station[0], station[1], station[2]
        if not stream_url or not str(stream_url).lower().startswith(
            ("http://", "https://")
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"station {name!r} has no browser-playable stream URL "
                    f"(source={source!r}); FM/SDR stations play only through "
                    "a satellite room, not the browser"
                ),
            )
        lowered = str(stream_url).lower()
        if any(h in lowered for h in ("localhost", "127.0.0.1", "://0.0.0.0")):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"station {name!r} streams from a host-local URL "
                    f"({stream_url}) the browser can't reach; play it "
                    "through a satellite room instead"
                ),
            )
        return await _proxy_stream(str(stream_url))

    return router


# ─── Realtime snapshots (manifest [[realtime]] wiring, §5.3) ─────────────
#
# Called by the web state poll loop with a plugin-schema-scoped session;
# the returned dict broadcasts VERBATIM on the mapped channel. Cheap +
# idempotent (poll-safe: every 1.5 s tick AND on NOTIFY) — and nothing
# elapsed_sec-shaped, ever (guards the GET-flood regression).


async def snapshot_stations(session: AsyncSession) -> dict[str, Any]:
    rows = await session.execute(
        text(
            f"""
            SELECT {_STATION_COLUMNS}
            FROM radio_stations
            WHERE favorited
            ORDER BY name ASC
            LIMIT 500
            """
        )
    )
    return {
        "stations": [
            _row_to_station(r).model_dump(mode="json") for r in rows.all()
        ]
    }


async def snapshot_detections(session: AsyncSession) -> dict[str, Any]:
    rows = await session.execute(
        text(
            """
            SELECT id, station_id, artist, title, detected_at,
                   fingerprint_source, in_library, library_track_id
            FROM radio_detections
            ORDER BY id DESC
            LIMIT 100
            """
        )
    )
    return {
        "detections": [
            _row_to_detection(r).model_dump(mode="json") for r in rows.all()
        ]
    }


SNAPSHOTS = {
    "snapshot_stations": snapshot_stations,
    "snapshot_detections": snapshot_detections,
}


# ─── Helpers ──────────────────────────────────────────────────────────────


def _use_stubs() -> bool:
    """The web process's stub flag — read from the environment (this
    module must not import core settings)."""
    import os

    return os.environ.get("USE_STUBS", "").strip().lower() in ("1", "true", "yes")


async def _notify_stations(s: AsyncSession, reason: str) -> None:
    """Commit-coupled NOTIFY on the caller's open session. The channel
    name matches the manifest's [[realtime]] declaration — both derive
    from the ``plugin_radio_`` prefix so the ends can't drift."""
    await s.execute(
        text("SELECT pg_notify('plugin_radio_stations_changed', :r)"),
        {"r": reason},
    )


def _search_hit_to_station(
    hit: RadioBrowserStation,
    existing_by_external_id: dict[str, tuple[int, bool]],
) -> RadioStation:
    persisted = existing_by_external_id.get(hit.external_id)
    row_id, favorited = persisted if persisted else (0, False)
    return RadioStation(
        id=row_id,
        name=hit.name,
        source="online",
        stream_url=hit.stream_url,
        external_id=hit.external_id,
        country_code=hit.country_code,
        language=hit.language,
        tags=hit.tags or [],
        favorited=favorited,
    )


def _row_to_station(r: Any) -> RadioStation:
    return RadioStation(
        id=int(r[0]),
        name=r[1],
        source=r[2],
        stream_url=r[3],
        frequency_mhz=float(r[4]) if r[4] is not None else None,
        market_city=r[5],
        market_state=r[6],
        call_sign=r[7],
        external_id=r[8],
        country_code=r[9],
        language=r[10],
        tags=list(r[11]) if r[11] is not None else [],
        favorited=bool(r[12]),
        sample_interval_sec=int(r[13]),
        last_sampled_at=r[14],
        created_at=r[15],
        updated_at=r[16],
        now_playing=r[17],
        now_playing_updated_at=r[18],
        icy_supported=r[19],
    )


def _row_to_detection(r: Any) -> RadioDetection:
    return RadioDetection(
        id=int(r[0]),
        station_id=int(r[1]),
        artist=r[2],
        title=r[3],
        detected_at=r[4],
        fingerprint_source=r[5],
        in_library=bool(r[6]),
        library_track_id=int(r[7]) if r[7] is not None else None,
    )


async def _proxy_stream(url: str) -> StreamingResponse:
    """Open ``url`` and relay its bytes. Keeps the upstream connection +
    client alive for the life of the response (closed in the
    generator's ``finally``); an unreachable upstream surfaces as 502
    before any bytes are sent."""
    import httpx

    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    try:
        req = client.build_request("GET", url, headers={"Icy-MetaData": "0"})
        resp = await client.send(req, stream=True)
    except Exception as e:
        await client.aclose()
        raise HTTPException(
            status_code=502, detail=f"upstream stream unreachable: {e}"
        ) from e
    if resp.status_code >= 400:
        code = resp.status_code
        await resp.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"upstream returned {code}")

    content_type = resp.headers.get("content-type", "audio/mpeg")

    async def _gen():
        try:
            async for chunk in resp.aiter_bytes(chunk_size=_STREAM_CHUNK):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(_gen(), media_type=content_type)
