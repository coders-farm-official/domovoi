"""Satellite API — per-room state and scoped sub-resources.

Source-of-truth pieces:

- ``mpd_rooms`` row → exists ⇒ room is provisioned. ``last_connected_at``
  is bumped by mpd_provisioner on a fresh-process first-connect, so it's
  a coarse "last time the Domovoi server restarted and saw this Pi"
  marker, not a real-time presence ping.
- ``domovoi /v1/admin/snapshot`` → live online state + WiFi
  telemetry. Best-effort: when the core is unreachable, ``status``
  is reported as ``"offline"`` to keep the schema literal.
- Per-room MPD daemon → current song / state / elapsed for the
  ``now_playing`` field.

Sessions / conversations / notes / timers are pure DB reads scoped by
``room_id``. The announce endpoints stub out at 501 since they need
the Domovoi server's ``app.state.active_sessions`` to do TTS fanout.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from domovoi.admin_auth import require_admin_mutation

from satellite import provisioning_protocol as proto

from web.backend import satellite_adoption
from web.backend.api.music import _now_playing_for
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    delete_admin,
    get_admin,
    get_cached_snapshot,
    post_admin,
)
from web.backend.schemas import (
    AdoptRequest,
    AnnounceRequest,
    ConfigUpdateRequest,
    ConversationTurn,
    DisplayRequest,
    DropInStartRequest,
    MpdPorts,
    PendingSatellite,
    RecentlyPlayed,
    RoomLabelRequest,
    Satellite,
    SatelliteDisplay,
    SatellitePairing,
    Session,
    Timer,
    VoiceNote,
    VolumeRequest,
    WifiStatus,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/satellites", tags=["satellites"])


# ─── List + detail ─────────────────────────────────────────────────────────


@router.get("", response_model=list[Satellite])
async def list_satellites() -> list[Satellite]:
    rooms = await _list_rooms()
    snapshot = get_cached_snapshot() or {}
    pairings = await _list_pairings()
    meta = await _list_satellite_meta()
    return [await _satellite_for(r, snapshot, pairings, meta) for r in rooms]


# ─── USB adoption (declared BEFORE /{room_id} so "pending" is never
#     captured as a room id) ────────────────────────────────────────────────


@router.get("/pending", response_model=list[PendingSatellite])
async def list_pending() -> list[PendingSatellite]:
    """Unprovisioned satellites currently presenting a USB adoption volume
    on the Domovoi server. Empty when the feature is off. MACs are matched
    against the inventory so a re-plugged device shows what it already is."""
    pending = await satellite_adoption.snapshot_pending()
    meta = await _list_satellite_meta()
    by_mac = {
        row["mac"]: rid
        for rid, row in meta.items()
        if row.get("mac")
    }
    out: list[PendingSatellite] = []
    for p in pending:
        model = PendingSatellite(**p)
        if model.mac and model.mac in by_mac:
            model.already_adopted_as = by_mac[model.mac]
        out.append(model)
    return out


@router.post(
    "/pending/{pending_id}/adopt",
    # Writes Wi-Fi credentials to a device and mints its pairing token —
    # security-tier, Bearer-only (the core preseed applies the same gate;
    # credentials are forwarded).
    dependencies=[Depends(require_admin_mutation)],
)
async def adopt_pending(pending_id: str, body: AdoptRequest, request: Request):
    """Adopt a pending satellite: validate the room name, preseed pairing on
    the core (raw token returned once), and write the provision file onto
    the device's volume. 410 when the device vanished mid-flow; 409 on a
    room-name collision (or a `force` for a different physical device)."""
    mount = satellite_adoption.mount_for(pending_id)
    if mount is None:
        # The scan cache may simply be older than the plug-in — rescan once.
        satellite_adoption.invalidate_cache()
        await satellite_adoption.snapshot_pending()
        mount = satellite_adoption.mount_for(pending_id)
    if mount is None:
        raise HTTPException(
            status_code=410, detail="device no longer present (unplugged?)"
        )

    # Re-read + validate the device info NOW — the nonce must still match
    # (a reboot mid-adopt re-nonces and this pending_id goes stale).
    try:
        import json as _json

        info = proto.validate_device_info(
            _json.loads((mount / proto.DEVICE_INFO_NAME).read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, proto.ProvisionInvalid):
        raise HTTPException(
            status_code=410, detail="device no longer present (unplugged?)"
        )
    if info["nonce"] != pending_id:
        raise HTTPException(
            status_code=409, detail="device re-announced itself — refresh and retry"
        )

    # Room-name collisions: any existing mpd_rooms / pairing / inventory row
    # blocks a fresh adopt. `force` is allowed ONLY when the existing
    # inventory row belongs to THIS physical device (MAC match) — the
    # wifi_failed retry / re-provision path.
    meta = await _list_satellite_meta()
    rooms = {r["room_id"] for r in await _list_rooms()}
    pairings = await _list_pairings()
    exists = body.room_id in rooms or body.room_id in pairings or body.room_id in meta
    if exists:
        row_mac = (meta.get(body.room_id) or {}).get("mac")
        same_device = bool(info["mac"] and row_mac and info["mac"] == row_mac)
        if not (body.force and same_device):
            raise HTTPException(
                status_code=409,
                detail=f"room {body.room_id!r} already exists",
            )

    status, payload = await post_admin(
        f"/v1/admin/satellites/{body.room_id}/pairing/preseed",
        {
            "sat_type": info["sat_type"],
            "room_label": body.room_label,
            "hardware": info.get("model"),
            "board": info.get("board"),
            "mac": info.get("mac"),
            "force": body.force,
        },
        headers=auth_forward_headers(request),
    )
    if status != 200 or not isinstance(payload, dict) or "token" not in payload:
        return bridge_response(status, payload)

    doc = proto.build_provision(
        nonce=pending_id,
        room_id=body.room_id,
        domovoi_url=satellite_adoption.advertised_domovoi_url(),
        sat_type=info["sat_type"],
        device_profile=body.device_profile,
        pairing_token=payload["token"],
        wifi_ssid=body.wifi_ssid,
        wifi_psk=body.wifi_psk,
        wifi_country=body.wifi_country,
        wifi_hidden=body.wifi_hidden,
        tz=_server_tz(),
        initial_volume=body.initial_volume,
    )
    try:
        await asyncio.to_thread(satellite_adoption.write_provision, mount, doc)
    except OSError:
        # The drive vanished between validation and write — roll back the
        # preseed so no orphaned waiting room lingers.
        await delete_admin(
            f"/v1/admin/satellites/{body.room_id}",
            headers=auth_forward_headers(request),
        )
        raise HTTPException(
            status_code=410, detail="device no longer present (unplugged?)"
        )
    # Nothing sensitive is logged: room + pending id only.
    log.info("adoption: provisioned room=%s pending=%s", body.room_id, pending_id)
    satellite_adoption.invalidate_cache()
    return {"room_id": body.room_id, "status": "provisioned"}


def _server_tz() -> str | None:
    """The server's IANA zone name for the device's timedatectl, when the
    optional tzlocal package can supply one. Best-effort — null skips."""
    try:
        from tzlocal import get_localzone_name

        return get_localzone_name()
    except Exception:
        return None


@router.delete("/{room_id}", dependencies=[Depends(require_admin_mutation)])
async def delete_satellite(room_id: str, request: Request):
    """Remove a never-connected (`waiting`) satellite — inventory row +
    preseeded pairing. Proxies to the core, which 409s for provisioned
    rooms."""
    status, payload = await delete_admin(
        f"/v1/admin/satellites/{room_id}",
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.patch("/{room_id}", response_model=dict)
async def update_satellite(room_id: str, body: RoomLabelRequest):
    """Update a satellite's display room label (grouping tag). Daily-tier
    cosmetic metadata, like volume."""
    status, payload = await post_admin(
        f"/v1/admin/satellites/{room_id}/label",
        {"room_label": body.room_label},
    )
    return bridge_response(status, payload)


@router.get("/{room_id}", response_model=Satellite)
async def get_satellite(room_id: str) -> Satellite:
    rooms = await _list_rooms()
    match = next((r for r in rooms if r["room_id"] == room_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"room {room_id!r} not provisioned")
    snapshot = get_cached_snapshot() or {}
    pairings = await _list_pairings()
    meta = await _list_satellite_meta()
    return await _satellite_for(match, snapshot, pairings, meta)


# ─── Sessions / conversations / notes / timers (per room) ─────────────────


@router.get("/{room_id}/sessions", response_model=list[Session])
async def list_sessions(
    room_id: str, limit: int = Query(default=20, ge=1, le=200)
) -> list[Session]:
    """Recent sessions held in this room.

    ``intent_count`` is the number of conversation_log turns whose
    ``session_id`` matches — a richer count than just looking at
    intents_log because it survives intents_log truncation.
    """
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT s.id::text, s.room_id, s.started_at, s.last_activity,
                       MAX(cl.person_id) AS person_id,
                       COUNT(cl.id) AS turn_count
                FROM sessions s
                LEFT JOIN conversation_log cl ON cl.session_id = s.id
                WHERE s.room_id = :room_id
                GROUP BY s.id, s.room_id, s.started_at, s.last_activity
                ORDER BY s.last_activity DESC
                LIMIT :limit
                """
            ),
            {"room_id": room_id, "limit": limit},
        )
        return [
            Session(
                id=r[0],
                room_id=r[1],
                started_at=r[2],
                last_activity=r[3],
                person_id=int(r[4]) if r[4] is not None else None,
                intent_count=int(r[5]),
            )
            for r in rows.all()
        ]


@router.get("/{room_id}/conversations", response_model=list[ConversationTurn])
async def list_conversations(
    room_id: str, limit: int = Query(default=50, ge=1, le=500)
) -> list[ConversationTurn]:
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, session_id::text, at, room_id, user_text,
                       assistant_text, matched_handler, matched_path
                FROM conversation_log
                WHERE room_id = :room_id
                ORDER BY at DESC
                LIMIT :limit
                """
            ),
            {"room_id": room_id, "limit": limit},
        )
        return [
            ConversationTurn(
                id=int(r[0]),
                session_id=r[1],
                at=r[2],
                room_id=r[3],
                user_text=r[4],
                assistant_text=r[5],
                matched_handler=r[6],
                matched_path=r[7],
            )
            for r in rows.all()
        ]


@router.get("/{room_id}/notes", response_model=list[VoiceNote])
async def list_notes(room_id: str) -> list[VoiceNote]:
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, room_id, text, created_at
                FROM voice_notes
                WHERE room_id = :room_id
                ORDER BY created_at DESC
                LIMIT 200
                """
            ),
            {"room_id": room_id},
        )
        return [
            VoiceNote(id=int(r[0]), room_id=r[1], body=r[2], captured_at=r[3])
            for r in rows.all()
        ]


@router.get("/{room_id}/recently-played", response_model=list[RecentlyPlayed])
async def list_recently_played(
    room_id: str, limit: int = Query(default=100, ge=1, le=500)
) -> list[RecentlyPlayed]:
    """Everything this room has played — library, playlist, and
    provider-plugin picks (``source`` is an open enum) — newest first.
    An external play that isn't yet in the library carries
    ``can_add=True`` so the drawer can offer "+ add to library", which
    enqueues a generic media acquisition (design §4.8/§10.1); the
    frontend shows the button only while a fulfiller capability is
    registered."""
    _LOCAL_SOURCES = ("library", "playlist", "spoken_audio")
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT mp.id, mp.room_id, mp.source, mp.title, mp.artist,
                       mp.channel, mp.video_id, mp.url, mp.started_at,
                       EXISTS (
                           SELECT 1 FROM library_tracks lt
                           WHERE lt.source = mp.source
                             AND lt.source_id = mp.video_id
                             AND mp.video_id IS NOT NULL
                       ) AS in_library
                FROM media_plays mp
                WHERE mp.room_id = :room_id
                ORDER BY mp.started_at DESC
                LIMIT :limit
                """
            ),
            {"room_id": room_id, "limit": limit},
        )
        out: list[RecentlyPlayed] = []
        for r in rows.all():
            in_library = bool(r.in_library)
            external = r.source not in _LOCAL_SOURCES
            out.append(
                RecentlyPlayed(
                    id=int(r.id),
                    room_id=r.room_id,
                    source=r.source,
                    title=r.title,
                    artist=r.artist,
                    channel=r.channel,
                    external_id=r.video_id,
                    url=r.url,
                    started_at=r.started_at,
                    in_library=in_library,
                    can_add=(
                        external
                        and not in_library
                        and bool(r.title or r.url)
                    ),
                )
            )
        return out


@router.get("/{room_id}/timers", response_model=list[Timer])
async def list_timers(room_id: str) -> list[Timer]:
    """Active timers and reminders for this room.

    A reminder is a timer with ``message IS NOT NULL`` — the
    distinction matters because reminders speak the message on fire,
    bare timers just chime. Surfaced via the ``is_reminder`` flag so
    the UI can render them differently.
    """
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, expires_at, label, message, room_id
                FROM timers
                WHERE room_id = :room_id
                ORDER BY expires_at ASC
                """
            ),
            {"room_id": room_id},
        )
        return [
            Timer(
                id=int(r[0]),
                expires_at=r[1],
                label=r[2],
                message=r[3],
                room_id=r[4],
                is_reminder=r[3] is not None,
            )
            for r in rows.all()
        ]


@router.delete("/{room_id}/timers/{timer_id}", status_code=204)
async def cancel_timer(room_id: str, timer_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text(
                """
                DELETE FROM timers
                WHERE id = :id AND room_id IS NOT DISTINCT FROM :room_id
                """
            ),
            {"id": timer_id, "room_id": room_id},
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"timer {timer_id} not found in room {room_id!r}",
        )


# ─── Action endpoints (proxied to domovoi admin) ──────────────────────


@router.post("/{room_id}/announce")
async def announce(room_id: str, body: AnnounceRequest):
    status, payload = await post_admin(
        "/v1/admin/announce",
        {"room_id": room_id, "message": body.message},
    )
    return bridge_response(status, payload)


@router.post("/announce-all")
async def announce_all(body: AnnounceRequest):
    """Broadcast: ``room_id=null`` to fan out to every connected
    satellite. The Domovoi server returns 503 when nothing's connected."""
    status, payload = await post_admin(
        "/v1/admin/announce",
        {"room_id": None, "message": body.message},
    )
    return bridge_response(status, payload)


@router.post("/{room_id}/dropin/start")
async def dropin_start(room_id: str, body: DropInStartRequest):
    """Open a live two-way drop-in from ``room_id`` (initiator) to
    ``body.target_room`` (Feature 4). Proxies to the Domovoi server, which owns
    the live session pairing. Pass-through status: 400 same room, 404 a room
    not connected, 409 disabled / no-AEC / already-in-a-call."""
    status, payload = await post_admin(
        "/v1/admin/dropin/start",
        {"initiator_room": room_id, "target_room": body.target_room},
    )
    return bridge_response(status, payload)


@router.post("/{room_id}/dropin/end")
async def dropin_end(room_id: str):
    """Hang up whatever drop-in ``room_id`` is in (Feature 4). 404 when the
    room isn't in a call."""
    status, payload = await post_admin(
        "/v1/admin/dropin/end", {"room_id": room_id}
    )
    return bridge_response(status, payload)


@router.get("/{room_id}/dropin/phone-info")
async def dropin_phone_info(room_id: str):
    """Connection info for a phone client that wants to drop in on
    ``room_id`` itself (phone ↔ room audio, domovoi
    ``/v1/dropin/{room_id}`` WebSocket — see domovoi/phone_dropin.py).

    The phone only knows this web backend's address; the streaming socket
    lives on the Domovoi server process. Return the Domovoi server's port (and
    host when it isn't the loopback shorthand) so the client can derive
    ``ws://<host>:<port>/v1/dropin/{room_id}``. No domovoi round-trip
    — this is static config."""
    from urllib.parse import urlparse

    from web.backend.domovoi_client import domovoi_url

    parsed = urlparse(domovoi_url())
    host = parsed.hostname or "localhost"
    return {
        "room_id": room_id,
        # localhost means "same host as this web backend" — the phone
        # should substitute the host it reached us on.
        "domovoi_host": None if host in ("localhost", "127.0.0.1") else host,
        "domovoi_port": parsed.port or 6370,
        "ws_path": f"/v1/dropin/{room_id}",
        "audio_sample_rate": 16000,
    }


@router.post("/{room_id}/volume")
async def set_volume(room_id: str, body: VolumeRequest):
    """Set a satellite's master output volume (0-100) from the overview tab.
    Proxies to the Domovoi server, which sends the ``set_volume`` frame to the
    live session (scales TTS + music). 404 when the room isn't connected,
    503 when nothing is."""
    status, payload = await post_admin(
        "/v1/admin/satellite/set-volume",
        {"room_id": room_id, "level": body.level},
    )
    return bridge_response(status, payload)


@router.post("/{room_id}/restart")
async def restart(room_id: str):
    """Ask a satellite to restart its own service. The Pi drains playback
    then runs a sudo'ed systemctl restart (needs the self-restart sudoers
    entry from PROVISIONING.md). 404 if the room isn't connected."""
    status, payload = await post_admin(
        "/v1/admin/satellite/restart", {"room_id": room_id}
    )
    return bridge_response(status, payload)


@router.post("/{room_id}/display")
async def set_display(room_id: str, body: DisplayRequest):
    """Drive a video satellite's screen from the drawer's Display block:
    panel on/off or a kiosk-browser restart. Daily-tier device control,
    like volume/restart. Proxies to the Domovoi server, which sends the
    ``set_display`` frame to the live session. 404 when the room isn't
    connected, 409 when it isn't a video satellite, 503 when nothing is."""
    status, payload = await post_admin(
        "/v1/admin/satellite/display",
        {"room_id": room_id, "action": body.action},
    )
    return bridge_response(status, payload)


@router.post(
    "/{room_id}/upgrade",
    # §7.3 gated list: satellite code push is admin-tier, Bearer-only.
    # The core applies the same gate; credentials are forwarded.
    dependencies=[Depends(require_admin_mutation)],
)
async def upgrade(room_id: str, request: Request):
    """Ask a satellite to sync its code from the Domovoi server and self-restart.
    The Pi tarballs its satellite/ tree, mirrors the /v1/satellite-code manifest
    (verifying each file's sha256), records the new synced SHA, then restarts —
    rolling back from the tarball if it doesn't reconnect in time. 503 when
    nothing's connected, 404 if this room isn't."""
    status, payload = await post_admin(
        "/v1/admin/satellite/upgrade",
        {"room_id": room_id},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.get("/{room_id}/config")
async def get_satellite_config(room_id: str):
    """Editable config for this satellite (schema + the values the Pi
    reported). Passes through to the Domovoi server, which holds the live
    per-room config cache. 404 if the room isn't connected."""
    status, payload = await get_admin(f"/v1/admin/satellite/{room_id}/config")
    return bridge_response(status, payload)


@router.patch("/{room_id}/config")
async def patch_satellite_config(room_id: str, body: ConfigUpdateRequest):
    """Push config edits to a satellite. The Domovoi server validates them and
    sends a set_config frame; the Pi rewrites its config.toml and restarts to
    apply. Returns {sent, rejected, restarting}."""
    status, payload = await post_admin(
        f"/v1/admin/satellite/{room_id}/config", {"changes": body.changes}
    )
    return bridge_response(status, payload)


@router.post(
    "/{room_id}/pairing/reset",
    # §7.3 gated list: resetting a satellite's pairing is a SECURITY op (it
    # lets the next connection re-pair as this room), so it's admin-tier,
    # Bearer-only. The core applies the same gate; credentials are forwarded.
    dependencies=[Depends(require_admin_mutation)],
)
async def reset_pairing(room_id: str, request: Request):
    """Reset a satellite's WS pairing (V002): delete its pairing row so the
    next ``hello`` for this room re-pairs trust-on-first-use. Needed after
    re-flashing a Pi or moving a room to a new device. Proxies to the core,
    which deletes the row (works whether or not the room is connected).
    Returns {room_id, reset}."""
    status, payload = await delete_admin(
        f"/v1/admin/satellites/{room_id}/pairing",
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


# ─── Helpers ───────────────────────────────────────────────────────────────


async def _list_rooms() -> list[dict[str, Any]]:
    """Every known satellite: provisioned rooms (mpd_rooms) plus adopted-
    but-never-connected ones (satellites rows only → null ports, rendered
    as `waiting`). Falls back to mpd_rooms alone when V003 isn't applied."""
    query_full = """
        SELECT COALESCE(m.room_id, s.room_id) AS room_id,
               m.control_port, m.http_port, m.last_connected_at
        FROM mpd_rooms m
        FULL OUTER JOIN satellites s ON s.room_id = m.room_id
        ORDER BY 1
        """
    query_legacy = """
        SELECT room_id, control_port, http_port, last_connected_at
        FROM mpd_rooms
        ORDER BY room_id
        """
    async with session_scope() as s:
        try:
            rows = await s.execute(text(query_full))
            data = rows.all()
        except Exception:
            await s.rollback()
            rows = await s.execute(text(query_legacy))
            data = rows.all()
    return [
        {
            "room_id": r[0],
            "control_port": int(r[1]) if r[1] is not None else None,
            "http_port": int(r[2]) if r[2] is not None else None,
            "last_connected_at": r[3],
        }
        for r in data
    ]


async def _list_pairings() -> dict[str, dict[str, Any]]:
    """All satellite pairing rows (V002), keyed by room_id. Read straight
    from the shared Postgres so status is fresh even for an offline room
    (pairing lives in the DB, not the live session)."""
    async with session_scope() as s:
        rows = await s.execute(
            text(
                "SELECT room_id, paired_at, last_seen_at FROM satellite_pairings"
            )
        )
    return {
        r[0]: {"paired_at": r[1], "last_seen_at": r[2]} for r in rows.all()
    }


async def _list_satellite_meta() -> dict[str, dict[str, Any]]:
    """All satellite inventory rows (V003), keyed by room_id — sat_type /
    room label / hardware metadata, fresh even for an offline room. Returns
    {} when the V003 migration hasn't been applied yet so the satellites
    list keeps working (types then just render as the 'voice' default)."""
    try:
        async with session_scope() as s:
            rows = await s.execute(
                text(
                    "SELECT room_id, sat_type, room_label, hardware, board, "
                    "mac, adopted_at FROM satellites"
                )
            )
        return {
            r[0]: {
                "sat_type": r[1],
                "room_label": r[2],
                "hardware": r[3],
                "board": r[4],
                "mac": r[5],
                "adopted_at": r[6],
            }
            for r in rows.all()
        }
    except Exception as e:
        log.debug("satellites inventory read failed (V003 not applied?): %s", e)
        return {}


async def _satellite_for(
    room: dict[str, Any],
    snapshot: dict[str, Any],
    pairings: dict[str, dict[str, Any]] | None = None,
    meta: dict[str, dict[str, Any]] | None = None,
) -> Satellite:
    room_id = room["room_id"]
    active_rooms = set(snapshot.get("active_rooms") or [])
    wifi_dict = (snapshot.get("wifi_status") or {}).get(room_id) if snapshot else None

    online = room_id in active_rooms
    # Null ports = adopted but never connected ("waiting"): no MPD instance
    # exists yet, so there's nothing to read now-playing from.
    waiting = room["control_port"] is None or room["http_port"] is None
    now_playing_model = (
        None
        if waiting
        else await _now_playing_for(
            (room_id, room["control_port"], room["http_port"])
        )
    )

    wifi: WifiStatus | None = None
    if isinstance(wifi_dict, dict):
        wifi = WifiStatus(
            rx_mbits=_as_float(wifi_dict.get("rx_mbits")),
            tx_mbits=_as_float(wifi_dict.get("tx_mbits")),
            ssid=wifi_dict.get("ssid"),
        )

    # Drop-in (Feature 4): AEC capability + current call peer, both from the
    # Domovoi server snapshot. active_dropins is a list of {initiator, target},
    # so this room is in a call if it appears on either side.
    full_duplex = bool((snapshot.get("satellite_full_duplex") or {}).get(room_id, False))
    voice = (snapshot.get("satellite_voice") or {}).get(room_id)
    volume = _as_int((snapshot.get("satellite_volume") or {}).get(room_id))
    # The code SHA the Pi last synced to (Feature 10), reported in its hello
    # frame and cached per-room in the Domovoi server snapshot. None until the
    # Pi has run an upgrade (or while it's offline).
    version = (snapshot.get("satellite_synced_sha") or {}).get(room_id)
    in_call_with: str | None = None
    for call in snapshot.get("active_dropins") or []:
        if call.get("initiator") == room_id:
            in_call_with = call.get("target")
            break
        if call.get("target") == room_id:
            in_call_with = call.get("initiator")
            break

    pair_row = (pairings or {}).get(room_id)
    pairing = SatellitePairing(
        paired=pair_row is not None,
        paired_at=pair_row["paired_at"] if pair_row else None,
        last_seen_at=pair_row["last_seen_at"] if pair_row else None,
    )

    # Satellite type: the inventory row (V003) wins — it's written only by
    # EXPLICIT sources (adoption preseed / an explicit hello) — with the live
    # snapshot as fallback for rooms that predate the table, then the
    # historical default. mic state is live-only (unknown offline → True).
    meta_row = (meta or {}).get(room_id) or {}
    sat_type = (
        meta_row.get("sat_type")
        or (snapshot.get("satellite_sat_type") or {}).get(room_id)
        or "voice"
    )
    mic_enabled = bool(
        (snapshot.get("satellite_mic_enabled") or {}).get(room_id, True)
    )
    display_dict = (snapshot.get("satellite_display") or {}).get(room_id)
    display = (
        SatelliteDisplay(
            on=display_dict.get("on"),
            kiosk_alive=display_dict.get("kiosk_alive"),
            brightness=_as_int(display_dict.get("brightness")),
            idle_mode=display_dict.get("idle_mode"),
        )
        if isinstance(display_dict, dict)
        else None
    )

    return Satellite(
        room_id=room_id,
        status="online" if online else ("waiting" if waiting else "offline"),
        last_connected_at=room["last_connected_at"],
        wifi=wifi,
        now_playing=now_playing_model,
        active_session_id=None,  # TODO: lift from the snapshot proxy
        mpd_ports=(
            None
            if waiting
            else MpdPorts(control=room["control_port"], http=room["http_port"])
        ),
        version=version,
        full_duplex=full_duplex,
        in_call_with=in_call_with,
        voice=voice,
        volume=volume,
        pairing=pairing,
        sat_type=sat_type,
        mic_enabled=mic_enabled,
        room_label=meta_row.get("room_label"),
        hardware=meta_row.get("hardware"),
        adopted_at=meta_row.get("adopted_at"),
        display=display,
    )


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
