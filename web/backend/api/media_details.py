"""One photo's or video's details: everything the house worked out about
it, in one answer (1.5.0, Media ▸ Photos, the Info sheet; design
MEDIA-UNDERSTANDING §6, §8, §9).

* ``GET /api/media/items/{item_id}/details`` (household,
  ``require_device_read``, the tier of ``/api/media/search``; a guest gets
  401). Query: ``frames_from`` / ``frames_limit`` (a video's frames, at
  most :data:`FRAMES_MAX` per page, :data:`FRAMES_DEFAULT` by default) and
  ``segments_from`` / ``segments_limit`` (its transcript, at most
  :data:`SEGMENTS_MAX` per page, :data:`SEGMENTS_DEFAULT` by default).

  Answers::

      {"id", "kind", "name", "folder", "library_id", "library", "path",
       "size_bytes", "duration_s", "taken_at", "date_from", "missing",
       "understanding": {"on", "faces_shown", "places_shown"},
       "analysis": {"state", "stage", "error_code", "attempts",
                    "analysed_at", "vision_model", "frame_pass",
                    "stages": [{"analyser", "state", "error_code",
                                "pass_kind", "capped"}]},
       "might_show", "tags", "text", "text_cut",
       "place"?,                      # only while places_shown
       "faces"?,                      # only while faces_shown
       # videos only:
       "frames": [{"at_s", "might_show", "tags", "text"}],
       "frames_page": {"offset", "limit", "total", "more", "next"},
       "transcript": [{"start_s", "end_s", "text"}],
       "transcript_page": {"offset", "limit", "total", "more", "next"},
       "summary": {"text", "suggested_title", "model"} | null}

  ``date_from`` is ``photo`` (the date it was taken) or ``file`` (the
  file's own time, when nothing better is known). ``might_show`` / ``tags``
  / ``text`` are a photo's caption, tags and the text found in it (OCR, up
  to :data:`TEXT_MAX` characters, ``text_cut`` says it was longer); a
  video's are its frames'.

**Who sees what.** The same gates as the rest of Photos:

* ``place`` (the town's name, never a coordinate) only while an admin has
  "Use photo locations" on, and never on a device marked as a shared
  screen (as ``/api/media/search`` answers ``place: null`` there). The key
  is left out, and ``understanding.places_shown`` is false.
* ``faces`` only while "Recognize faces" is on, and never on a shared
  screen (``/api/faces`` refuses those outright). The tiers are the
  ``#faces`` page's (web/backend/api/faces.py): the household sees named
  people and unnamed groups; a face in a hidden (stranger's) group is an
  admin's alone and is left out for anyone else, uncounted. Each face:
  ``person_id``, ``name`` (null for an unnamed group), ``confirmed``
  (false: a suggestion), ``group_id``, and for a video ``at_s`` /
  ``end_s`` (the appearance). Never an embedding, a centroid or a box.

Everything a model, OCR or speech recognition made, and the file names,
is untrusted text, returned exactly as stored: the dashboard renders it
as text, never as HTML.
"""

from __future__ import annotations

import posixpath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from domovoi.admin_auth import require_device_read
from domovoi.media_understanding import catalog, switches
from domovoi.media_understanding import faces as faces_mod
from web.backend.api import devices as devices_api
from web.backend.db import session_scope

router = APIRouter(tags=["media-understanding"])

#: A video's frames per page: the default and the most one page carries.
FRAMES_DEFAULT = 100
FRAMES_MAX = 500
#: A video's transcript lines per page: the default and the most.
SEGMENTS_DEFAULT = 200
SEGMENTS_MAX = 1000
#: The text found in one picture or frame, at most this many characters.
TEXT_MAX = 5000
#: A summary, at most this many characters.
SUMMARY_MAX = 10_000
#: A photo's faces (a crowd), at most this many; a video's appearances too.
FACES_MAX = 200

_CORE_LABELS = {"core:pictures": "Pictures", "core:videos": "Videos"}


def _cut(raw: str | None, most: int) -> tuple[str | None, bool]:
    if not raw:
        return None, False
    return (raw[:most], True) if len(raw) > most else (raw, False)


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


async def _library_label(s: Any, library_id: str) -> str:
    if library_id in _CORE_LABELS:
        return _CORE_LABELS[library_id]
    try:
        for lib in await catalog.libraries(s):
            if lib.id == library_id:
                return lib.label or library_id
    except Exception:  # noqa: BLE001 — the id still names it
        pass
    return library_id


def _page(offset: int, limit: int, total: int) -> dict[str, Any]:
    more = offset + limit < total
    return {"offset": offset, "limit": limit, "total": total, "more": more,
            "next": offset + limit if more else None}


@router.get("/api/media/items/{item_id}/details", dependencies=[Depends(require_device_read)])
async def media_item_details(
    item_id: int,
    request: Request,
    frames_from: int = Query(0, ge=0, le=1_000_000),
    frames_limit: int = Query(FRAMES_DEFAULT, ge=1, le=FRAMES_MAX),
    segments_from: int = Query(0, ge=0, le=1_000_000),
    segments_limit: int = Query(SEGMENTS_DEFAULT, ge=1, le=SEGMENTS_MAX),
) -> dict[str, Any]:
    async with session_scope() as s:
        m = (await s.execute(text(
            "SELECT id, library_id, rel_path, kind, size_bytes, mtime, taken_at, to_timestamp(mtime) AS file_time, "
            "place_name, state, stage, attempts, error_code, frame_pass, duration_s, vision_model, analysed_at, "
            "missing_since FROM media_items WHERE id = :i"), {"i": item_id})).mappings().first()
        if m is None:
            raise HTTPException(status_code=404, detail="no such photo or video")
        sw = await switches.read(s)
        device = await devices_api.caller_device_row(s, request)
        shared = bool(device is not None and device.trusted and device.shared_screen)
        admin = await devices_api.caller_is_admin(request)
        places_shown = bool(sw["places"]) and not shared
        faces_shown = bool(sw["faces"]) and not shared

        stages = (await s.execute(text(
            "SELECT analyser, state, error_code, pass_kind, capped FROM media_item_stages "
            "WHERE item_id = :i ORDER BY analyser"), {"i": item_id})).mappings().all()
        rel = m["rel_path"]
        taken = m["taken_at"] if m["taken_at"] is not None else m["file_time"]
        out: dict[str, Any] = {
            "id": int(m["id"]),
            "kind": m["kind"],
            "name": posixpath.basename(rel),
            "folder": posixpath.dirname(rel),
            "library_id": m["library_id"],
            "library": await _library_label(s, m["library_id"]),
            "path": rel,
            "size_bytes": int(m["size_bytes"]),
            "duration_s": float(m["duration_s"]) if m["duration_s"] is not None else None,
            "taken_at": _iso(taken),
            "date_from": "photo" if m["taken_at"] is not None else "file",
            "missing": m["missing_since"] is not None,
            "understanding": {"on": bool(sw["enabled"]), "faces_shown": faces_shown,
                              "places_shown": places_shown},
            "analysis": {
                "state": m["state"], "stage": m["stage"], "error_code": m["error_code"],
                "attempts": int(m["attempts"] or 0), "analysed_at": _iso(m["analysed_at"]),
                "vision_model": m["vision_model"], "frame_pass": m["frame_pass"],
                "stages": [dict(r) for r in stages],
            },
        }

        # The photo's own caption: frame 0 without a time (a video's
        # frames all have one).
        f0 = (await s.execute(text(
            "SELECT caption, tags, ocr_text FROM media_frames WHERE item_id = :i AND idx = 0 AND at_s IS NULL"),
            {"i": item_id})).mappings().first()
        cut_text, cut = _cut(f0["ocr_text"] if f0 else None, TEXT_MAX)
        out.update(might_show=(f0["caption"] or None) if f0 else None,
                   tags=list(f0["tags"] or []) if f0 else [], text=cut_text, text_cut=cut)

        if places_shown:
            out["place"] = m["place_name"] or None
        if faces_shown:
            faces = await faces_mod.item_faces(s, item_id, admin=admin, most=FACES_MAX)
            if not admin:
                for f in faces:
                    f.pop("hidden", None)
            out["faces"] = faces

        if m["kind"] == "video":
            n_frames = int((await s.execute(text(
                "SELECT count(*) FROM media_frames WHERE item_id = :i AND at_s IS NOT NULL"),
                {"i": item_id})).scalar() or 0)
            frames = (await s.execute(text(
                "SELECT at_s, caption, tags, ocr_text FROM media_frames WHERE item_id = :i AND at_s IS NOT NULL "
                "ORDER BY at_s, idx LIMIT :l OFFSET :o"),
                {"i": item_id, "l": frames_limit, "o": frames_from})).mappings().all()
            out["frames"] = [{"at_s": float(r["at_s"]), "might_show": r["caption"] or None,
                              "tags": list(r["tags"] or []), "text": _cut(r["ocr_text"], TEXT_MAX)[0]}
                             for r in frames]
            out["frames_page"] = _page(frames_from, frames_limit, n_frames)
            n_seg = int((await s.execute(text("SELECT count(*) FROM media_segments WHERE item_id = :i"),
                                         {"i": item_id})).scalar() or 0)
            segs = (await s.execute(text(
                "SELECT start_s, end_s, text FROM media_segments WHERE item_id = :i "
                "ORDER BY start_s, idx LIMIT :l OFFSET :o"),
                {"i": item_id, "l": segments_limit, "o": segments_from})).mappings().all()
            out["transcript"] = [{"start_s": float(r["start_s"]), "end_s": float(r["end_s"]), "text": r["text"]}
                                 for r in segs]
            out["transcript_page"] = _page(segments_from, segments_limit, n_seg)
            summ = (await s.execute(text(
                "SELECT summary, suggested_title, model FROM media_summaries WHERE item_id = :i"),
                {"i": item_id})).mappings().first()
            out["summary"] = ({"text": _cut(summ["summary"], SUMMARY_MAX)[0] or "",
                               "suggested_title": summ["suggested_title"], "model": summ["model"]}
                              if summ is not None and (summ["summary"] or summ["suggested_title"]) else None)
    return out
