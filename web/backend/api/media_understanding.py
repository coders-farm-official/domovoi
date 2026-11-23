"""Settings → Chat & AI → Photos and videos: the media-understanding
switches, progress and excluded folders (1.5.0, design
MEDIA-UNDERSTANDING §5 and §8; owner answers 2026-11-22).

Routes (tiers per the owner's answers: the household sees progress; all
switches and folder exclusions are admin):

* ``GET  /api/media-understanding`` (household, ``require_device_read``):
  the switches, progress per kind (done, left, failed), the runner's phase
  ("waiting_for_voice" while it holds off for a voice answer), the
  estimate, the benchmark, the libraries and the excluded folders.
* ``PUT  /api/media-understanding/settings`` (admin): change switches
  (``enabled``, ``faces``, ``places``, ``frame_cap_mode``, ``frame_cap``).
  Turning faces off deletes every face and group
  (``face_data.delete_all``), which needs ``confirm_delete_faces: true`` when
  there are any (409 otherwise). Turning them on starts the one-time,
  pinned model fetch on the core. Turning ``places`` ("Use photo
  locations") off deletes every stored GPS position and place name
  (``geocode.delete_all``), which needs ``confirm_delete_places: true``
  when there are any (409 otherwise); turning it on starts the one-time,
  pinned fetch of the GeoNames town list on the core.
* ``POST /api/media-understanding/pause`` (admin): ``{"paused": bool}``.
* ``POST /api/media-understanding/run-now`` (admin): ignore the night
  window until the backlog is done (still voice first, still capped).
* ``POST /api/media-understanding/rescan`` (admin, ``require_admin_mutation``):
  walk the libraries at the core's next look (within a minute).
* ``POST /api/media-understanding/excluded`` (admin):
  ``{"library_id", "prefix", "confirm_delete"}``. Any library the catalogue
  walks (``libraries`` in the GET: Pictures, Videos, the other Files
  libraries, plugins' and removable drives'), or one it still has results
  for (a drive unplugged); ``prefix`` '' is the whole library. New
  analysis stops there and what was worked out about its files is deleted
  at once (captions, tags, text, transcripts, summaries, faces, places,
  the search words). When there is any, that needs ``confirm_delete:
  true`` (409 otherwise, with ``files``: how many would lose their
  results).
* ``DELETE /api/media-understanding/excluded?library_id=&prefix=`` (admin):
  include it again (the walker looks at it at its next look).

Everything is in Postgres; the core's runner and walker read it. Each
change fires ``pg_notify('media_understanding_changed')``.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text

from domovoi.admin_auth import require_admin_mutation, require_device_read
from domovoi.media_understanding import catalog, geocode, switches
from domovoi.media_understanding import faces as face_data
from domovoi.media_understanding.runner import estimate, progress
from web.backend.db import session_scope

router = APIRouter(tags=["media-understanding"])

PREFIX_MAX = 1024
LIBRARY_ID_MAX = 200


async def _libraries(s: Any) -> list[dict[str, Any]]:
    """Every library the catalogue walks, then any it still has results
    for that isn't here now (a drive unplugged), so it can be excluded too."""
    out = [{"id": lib.id, "label": lib.label or lib.id, "present": catalog.is_present(lib),
            "kinds": sorted(lib.kinds)}
           for lib in await catalog.libraries(s)]
    seen = {x["id"] for x in out}
    rows = (await s.execute(text(
        "SELECT library_id FROM media_libraries UNION SELECT DISTINCT library_id FROM media_items "
        "ORDER BY 1"))).scalars().all()
    out += [{"id": r, "label": r, "present": False, "kinds": []} for r in rows if r not in seen]
    return out


def clean_prefix(raw: Any) -> str:
    """A folder inside a library, as stored: forward slashes, no leading
    or trailing slash, never ``..``; '' is the whole library."""
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail="prefix must be text")
    p = raw.replace("\\", "/").strip().strip("/")
    if len(p) > PREFIX_MAX:
        raise HTTPException(status_code=422, detail="the folder path is too long")
    parts = [x for x in p.split("/") if x]
    if any(x in (".", "..") for x in parts):
        raise HTTPException(status_code=422, detail="the folder path can't contain . or ..")
    if any(unicodedata.category(ch) == "Cc" for ch in p):
        raise HTTPException(status_code=422, detail="the folder path has a control character")
    return "/".join(parts)


async def _library_id(s: Any, raw: Any) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > LIBRARY_ID_MAX:
        raise HTTPException(status_code=404, detail="no such library")
    if raw not in {x["id"] for x in await _libraries(s)}:
        raise HTTPException(status_code=404, detail="no such library")
    return raw


async def _notify(s: Any, what: str) -> None:
    await s.execute(text("SELECT pg_notify('media_understanding_changed', :w)"), {"w": what})


@router.get("/api/media-understanding", dependencies=[Depends(require_device_read)])
async def get_state() -> dict[str, Any]:
    from domovoi import ai_features

    async with session_scope() as s:
        sw = await switches.read(s)
        prog = await progress(s)
        excluded = (await s.execute(text(
            "SELECT library_id, prefix FROM media_excluded ORDER BY library_id, prefix"))).mappings().all()
        faces = int((await s.execute(text("SELECT count(*) FROM media_faces"))).scalar() or 0)
        located = await geocode.located_count(s)
        places_state = await geocode.read_state(s)
        libraries = await _libraries(s)
    est = estimate(prog["photos"]["left"], prog["videos"]["left"], prog["benchmark"])
    return {
        "settings": {k: sw[k] for k in ("enabled", "faces", "places", "paused", "run_now",
                                        "frame_cap_mode", "frame_cap")},
        "frame_cap_range": [switches.FRAME_CAP_MIN, switches.FRAME_CAP_MAX],
        "ai_on": ai_features.enabled(),
        "progress": prog,
        "estimate": {"seconds": round(est.seconds), "nights": est.nights},
        "face_count": faces,
        # Never a coordinate: how many photos have a location, and why
        # places can't be named yet (the town list's one-time download).
        "places": {"count": located, "ready": geocode.ready(),
                   "problem": geocode.problem() if sw["places"] else None,
                   "fetch_error": places_state.get("fetch_error")},
        "libraries": libraries,
        "excluded": [dict(r) for r in excluded],
    }


@router.put("/api/media-understanding/settings", dependencies=[Depends(require_admin_mutation)])
async def put_settings(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    body = dict(body)
    confirm = bool(body.pop("confirm_delete_faces", False))
    confirm_places = bool(body.pop("confirm_delete_places", False))
    async with session_scope() as s:
        current = await switches.read(s)
        try:
            new = switches.apply_changes(current, body)
        except switches.InvalidSwitches as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        if current["faces"] and not new["faces"]:
            n = int((await s.execute(text("SELECT count(*) FROM media_faces"))).scalar() or 0)
            g = int((await s.execute(text("SELECT count(*) FROM face_groups"))).scalar() or 0)
            if (n or g) and not confirm:
                raise HTTPException(status_code=409, detail=(
                    f"turning faces off deletes {n} saved faces and every group and name on them; "
                    "send confirm_delete_faces to go ahead"))
        if current["places"] and not new["places"]:
            n = await geocode.located_count(s)
            if n and not confirm_places:
                raise HTTPException(status_code=409, detail=(
                    f"turning photo locations off deletes where {n} photos were taken (their positions "
                    "and place names); send confirm_delete_places to go ahead"))
        # The switch first: its row lock makes a face the runner is storing
        # right now either land before this delete or see faces off
        # (domovoi/media_understanding/faces.py, _faces_on_locked).
        saved = await switches.write(s, new)
        if current["faces"] and not new["faces"]:
            await face_data.delete_all(s)
        elif new["faces"] and not current["faces"]:
            # Turned on: the runner fetches the models (only now), then
            # queues the photos again for faces.
            await face_data.write_state(s, requeued=False, fetch_error=None)
        if current["places"] and not new["places"]:
            await geocode.delete_all(s)
        elif new["places"] and not current["places"]:
            # Turned on: the runner fetches the town list (only now), then
            # reads where each photo was taken.
            await geocode.write_state(s, requeued=False, fetch_error=None)
    return {"settings": saved}


@router.post("/api/media-understanding/pause", dependencies=[Depends(require_admin_mutation)])
async def pause(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    paused = body.get("paused")
    if not isinstance(paused, bool):
        raise HTTPException(status_code=422, detail="paused must be true or false")
    async with session_scope() as s:
        sw = await switches.read(s)
        sw["paused"] = paused
        if paused:
            sw["run_now"] = False
        saved = await switches.write(s, sw)
    return {"settings": saved}


@router.post("/api/media-understanding/run-now", dependencies=[Depends(require_admin_mutation)])
async def run_now() -> dict[str, Any]:
    async with session_scope() as s:
        sw = await switches.read(s)
        if not sw["enabled"]:
            raise HTTPException(status_code=409, detail="turn on Understand photos and videos first")
        sw["run_now"] = True
        sw["paused"] = False
        saved = await switches.write(s, sw)
    return {"settings": saved}


# Admin (security review 2026-11-23, L49): a rescan keeps the walker
# running; the household's walks are hourly anyway.
@router.post("/api/media-understanding/rescan", dependencies=[Depends(require_admin_mutation)])
async def rescan() -> dict[str, bool]:
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE media_runner_state SET rescan_requested = true, updated_at = now() WHERE id = 1"))
        await _notify(s, "rescan")
    return {"ok": True}


@router.post("/api/media-understanding/excluded", dependencies=[Depends(require_admin_mutation)])
async def add_excluded(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    prefix = clean_prefix(body.get("prefix", ""))
    confirm = body.get("confirm_delete") is True
    async with session_scope() as s:
        lib = await _library_id(s, body.get("library_id"))
        files = await catalog.count_under(s, lib, prefix)
        if files and not confirm:
            raise HTTPException(status_code=409, detail={
                "message": (f"excluding it deletes what was worked out about {files} "
                            f"{'file' if files == 1 else 'files'} in it; send confirm_delete to go ahead"),
                "files": files})
        await s.execute(text(
            "INSERT INTO media_excluded (library_id, prefix) VALUES (:l, :p) ON CONFLICT DO NOTHING"),
            {"l": lib, "p": prefix})
        deleted = await catalog.delete_under(s, lib, prefix)
        await _notify(s, "excluded")
    return {"library_id": lib, "prefix": prefix, "deleted": deleted}


@router.delete("/api/media-understanding/excluded", dependencies=[Depends(require_admin_mutation)])
async def remove_excluded(library_id: str = Query(..., max_length=LIBRARY_ID_MAX),
                          prefix: str = Query("")) -> dict[str, Any]:
    # Any excluded row can go, whether or not its library is here now.
    lib = library_id
    p = clean_prefix(prefix)
    async with session_scope() as s:
        r = await s.execute(text("DELETE FROM media_excluded WHERE library_id = :l AND prefix = :p"),
                            {"l": lib, "p": p})
        if not r.rowcount:
            raise HTTPException(status_code=404, detail="that folder isn't excluded")
        await s.execute(text(
            "UPDATE media_runner_state SET rescan_requested = true, updated_at = now() WHERE id = 1"))
        await _notify(s, "excluded")
    return {"ok": True}


async def forget_deleted(library_id: str, rel_paths: list[str]) -> None:
    """Files deleted through Files, in any library: their results go at
    once. Best effort: the walker deletes them within the hour anyway."""
    if not library_id or not rel_paths:
        return
    try:
        async with session_scope() as s:
            await catalog.forget_paths(s, library_id, rel_paths)
    except Exception:  # noqa: BLE001 — never fails a delete the household made
        pass
