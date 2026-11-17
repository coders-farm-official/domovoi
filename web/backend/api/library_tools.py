"""Library tools (V058): Duplicates, Needs review, Cover art, the trash.

Everything here is the admin's: it moves files, renames songs and changes
pictures in files, so reads take ``require_admin_read`` and changes
``require_admin_mutation``. The web process does the work itself (a file
move, a row update); the core's workers fill what these lists show (song
recognition keeps review candidates, the cover-art worker records what it
wrote).

* ``GET  /api/music/library-tools/duplicates`` — groups of copies of one
  song (``domovoi/library_duplicates.py``), the best copy pre-selected;
  groups a person chose "Keep both" for are left out until their
  membership changes.
* ``POST /api/music/library-tools/duplicates/resolve`` — keep one copy,
  move the others into the trash folder inside the music folder
  (``domovoi/library_trash.py``): their playlist entries, favourite flag
  and listening history move to the kept copy, their library rows go.
* ``POST /api/music/library-tools/duplicates/keep-both`` — remember a
  group as it is.
* ``GET  /api/music/library-tools/trash``, ``POST .../trash/{id}/restore``
  — what was moved, and putting a copy back (its file, then its library
  row with the names it had; it is not put back into playlists).
* ``GET  /api/music/library-tools/review``, ``POST .../review/{track_id}/apply``,
  ``POST .../review/{track_id}/skip`` — tracks song recognition didn't
  rename (the answer disagreed with the file's names, or wasn't sure) with
  the candidates it found. Apply sets the names and the MusicBrainz id like
  a hand edit (outcome ``reviewed``: never recognised again) and lets the
  year / genre fetch ask about the new id; Skip is remembered.
* ``GET  /api/music/library-tools/cover-art``,
  ``POST .../cover-art/{track_id}/undo`` — the Cover Art Archive's
  pictures Domovoi wrote into files (or keeps for formats it doesn't
  write), and Undo, which puts the file's own pictures back.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from domovoi import cover_art_writer, library_cover_version, library_trash
from domovoi import library_duplicates as dups
from domovoi import library_file_ids as _library_file_ids  # noqa: F401 — the indexer's lazy import (restore)
from domovoi import library_playlists_import as _playlists_import  # noqa: F401 — preloaded for the guard
from domovoi import media_tags
from domovoi.admin_auth import require_admin_mutation, require_admin_read
from domovoi.clients.musicbrainz import is_mbid
from domovoi.config import settings as core_settings
from web.backend.db import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music/library-tools", tags=["music"])
READ = [Depends(require_admin_read)]
ADMIN = [Depends(require_admin_mutation)]

#: Most groups returned (the biggest first).
MAX_GROUPS = 1000


def _music_root() -> Path:
    return Path(core_settings.music_dir).expanduser().resolve(strict=False)


def _rel(path: str) -> str:
    try:
        return Path(path).resolve(strict=False).relative_to(_music_root()).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def _folder_image(path: Path) -> bool:
    try:
        return any(p.stem.lower() in ("cover", "folder", "front", "album")
                   and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                   for p in path.parent.iterdir())
    except OSError:
        return False


def _file_facts(path_str: str) -> tuple[int | None, bool]:
    """(size in bytes, has cover art) of one file; blocking."""
    path = Path(path_str)
    try:
        size = path.stat().st_size
    except OSError:
        return None, False
    if _folder_image(path):
        return size, True
    try:
        f = media_tags.read(path)
    except Exception:  # noqa: BLE001
        return size, False
    if f is None:
        return size, False
    if f.pictures:
        return size, True
    tags = f.tags
    if tags is None:
        return size, False
    getall = getattr(tags, "getall", None)
    if callable(getall):
        return size, bool(getall("APIC"))
    for key in ("covr", "WM/Picture", "metadata_block_picture", "coverart"):
        try:
            if tags.get(key):
                return size, True
        except Exception:  # noqa: BLE001
            continue
    return size, False


# ─── Duplicates ──────────────────────────────────────────────────────────


def _identity_word(c: dups.Copy) -> str:
    return ("identified", "tagged", "untagged")[2 - c.identity]


async def _load_copies() -> tuple[list[dups.Copy], dict[int, dict[str, Any]]]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT id, title, artist, album, duration_sec, musicbrainz_recording_id, file_path, "
            "favorited FROM library_tracks ORDER BY added_at, id"
        ))).all()
    copies, extra = [], {}
    for n, r in enumerate(rows):
        copies.append(dups.Copy(int(r[0]), r[1], r[2], r[3], r[4], r[5], added_order=n))
        extra[int(r[0])] = {"file_path": r[6], "favorited": bool(r[7])}
    return copies, extra


@router.get("/duplicates", dependencies=READ)
async def list_duplicates() -> dict[str, Any]:
    """Groups of copies of one song (module docstring)."""
    copies, extra = await _load_copies()
    groups = dups.find_groups(copies)
    async with session_scope() as s:
        kept = {r[0] for r in (await s.execute(text(
            "SELECT group_key FROM library_duplicate_keeps"))).all()}
        counts = {int(r[0]): int(r[1]) for r in (await s.execute(text(
            "SELECT track_id, count(*) FROM playlist_tracks GROUP BY track_id"))).all()}
    shown = [g for g in groups if g.key not in kept][:MAX_GROUPS]
    # Sizes and art: only for the copies shown (a stat and a tag read each).
    wanted = [c for g in shown for c in g.copies]

    def facts() -> dict[int, tuple[int | None, bool]]:
        return {c.id: _file_facts(extra[c.id]["file_path"]) for c in wanted}

    got = await anyio.to_thread.run_sync(facts)
    out = []
    for g in shown:
        for c in g.copies:
            c.size_bytes, c.has_art = got.get(c.id, (None, False))
        best = dups.best_copy(g.copies)
        out.append({
            "key": g.key, "kind": g.kind, "label": dups.KIND_LABELS[g.kind], "best_id": best.id,
            "members": [{
                "id": c.id, "title": c.title, "artist": c.artist, "album": c.album,
                "duration_sec": c.duration_sec,
                "bitrate_kbps": round(c.bitrate_kbps) if c.bitrate_kbps else None,
                "identity": _identity_word(c), "has_art": c.has_art,
                "path": _rel(extra[c.id]["file_path"]),
                "playlists": counts.get(c.id, 0),
                "missing": c.size_bytes is None,
            } for c in g.copies],
        })
    return {"groups": out, "total": len(groups), "kept": sum(1 for g in groups if g.key in kept)}


class ResolveBody(BaseModel):
    keep_id: int
    member_ids: list[int] = Field(..., min_length=2, max_length=50)


class KeepBothBody(BaseModel):
    member_ids: list[int] = Field(..., min_length=2, max_length=50)


async def _repoint(s: Any, gone: int, keep: int) -> None:
    """Everything that named ``gone`` names ``keep`` instead: playlist
    entries (where ``keep`` isn't already in that playlist), the favourite
    flag, listening history."""
    await s.execute(text(
        "DELETE FROM playlist_tracks pt WHERE pt.track_id = :gone AND EXISTS ("
        "SELECT 1 FROM playlist_tracks k WHERE k.playlist_id = pt.playlist_id AND k.track_id = :keep)"
    ), {"gone": gone, "keep": keep})
    await s.execute(text("UPDATE playlist_tracks SET track_id = :keep WHERE track_id = :gone"),
                    {"gone": gone, "keep": keep})
    await s.execute(text(
        "UPDATE library_tracks SET favorited = TRUE WHERE id = :keep "
        "AND EXISTS (SELECT 1 FROM library_tracks WHERE id = :gone AND favorited)"
    ), {"gone": gone, "keep": keep})
    try:
        async with s.begin_nested():
            await s.execute(text("UPDATE music_listens SET track_id = :keep WHERE track_id = :gone"),
                            {"gone": gone, "keep": keep})
    except Exception as e:  # noqa: BLE001 — history is a nicety
        log.debug("duplicates: listening history not moved: %s", e)


@router.post("/duplicates/resolve", dependencies=ADMIN)
async def resolve_duplicates(body: ResolveBody, request: Request) -> dict[str, Any]:
    """Keep ``keep_id``; move every other copy in ``member_ids`` into the
    trash folder (not deleted: Restore puts it back)."""
    ids = list(dict.fromkeys(int(i) for i in body.member_ids))
    if body.keep_id not in ids or len(ids) < 2:
        raise HTTPException(status_code=400, detail="keep one of the group's copies")
    async with session_scope() as s:
        rows = {int(r.id): r for r in (await s.execute(text(
            "SELECT id, file_path, title, artist, album, duration_sec, musicbrainz_recording_id, "
            "enrich_outcome, favorited FROM library_tracks WHERE id = ANY(:ids)"
        ), {"ids": ids})).all()}
    if body.keep_id not in rows:
        raise HTTPException(status_code=404, detail="the copy to keep isn't in the library any more")
    moved, failed = [], []
    for tid in ids:
        if tid == body.keep_id or tid not in rows:
            continue
        r = rows[tid]
        try:
            dest = await anyio.to_thread.run_sync(
                library_trash.move_to_trash, r.file_path, core_settings.music_dir)
        except library_trash.TrashError as e:
            failed.append({"id": tid, "reason": str(e)})
            continue
        snapshot = {k: getattr(r, k) for k in (
            "title", "artist", "album", "duration_sec", "musicbrainz_recording_id",
            "enrich_outcome", "favorited")}
        try:
            async with session_scope() as s:
                await _repoint(s, tid, body.keep_id)
                await s.execute(text(
                    "INSERT INTO library_trash (original_path, trash_path, track, kept_track_id) "
                    "VALUES (:o, :t, CAST(:j AS jsonb), :k)"
                ), {"o": r.file_path, "t": str(dest), "j": json.dumps(snapshot), "k": body.keep_id})
                await s.execute(text("DELETE FROM library_tracks WHERE id = :id"), {"id": tid})
                await s.execute(text("SELECT pg_notify('library_changed', 'duplicates')"))
                await s.execute(text("SELECT pg_notify('playlists_changed', 'duplicates')"))
                await s.commit()
        except Exception as e:  # noqa: BLE001 — put the file back; the row stayed
            log.warning("duplicates: couldn't update the library for %s: %s", tid, e)
            try:
                await anyio.to_thread.run_sync(
                    library_trash.restore_file, dest, r.file_path, core_settings.music_dir)
            except library_trash.TrashError:
                pass
            failed.append({"id": tid, "reason": "the library couldn't be updated"})
            continue
        moved.append(tid)
    # The copies moved out leave every room's player too (S2-18).
    if moved:
        from web.backend.api.music import tell_rooms_tracks_removed

        await tell_rooms_tracks_removed(request, [rows[t].file_path for t in moved])
    return {"moved": moved, "failed": failed, "kept": body.keep_id}


@router.post("/duplicates/keep-both", dependencies=ADMIN)
async def keep_both(body: KeepBothBody) -> dict[str, Any]:
    key = dups.group_key(body.member_ids)
    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO library_duplicate_keeps (group_key, member_ids) VALUES (:k, :m) "
            "ON CONFLICT (group_key) DO UPDATE SET decided_at = now()"
        ), {"k": key, "m": sorted(int(i) for i in body.member_ids)})
        await s.commit()
    return {"key": key}


@router.get("/trash", dependencies=READ)
async def list_trash() -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT id, original_path, trash_path, track, kept_track_id, moved_at "
            "FROM library_trash WHERE restored_at IS NULL ORDER BY moved_at DESC, id DESC LIMIT 500"
        ))).all()
    out = []
    for r in rows:
        track = r.track if isinstance(r.track, dict) else json.loads(r.track or "{}")
        out.append({
            "id": int(r.id), "path": _rel(r.original_path), "title": track.get("title"),
            "artist": track.get("artist"), "kept_track_id": r.kept_track_id,
            "moved_at": r.moved_at.isoformat() if r.moved_at else None,
            "in_trash": Path(r.trash_path).is_file(),
        })
    return {"items": out, "folder": library_trash.TRASH_DIR_NAME}


@router.post("/trash/{item_id}/restore", dependencies=ADMIN)
async def restore_from_trash(item_id: int) -> dict[str, Any]:
    from domovoi.workers.library_indexer import index_paths

    async with session_scope() as s:
        r = (await s.execute(text(
            "SELECT original_path, trash_path, track FROM library_trash "
            "WHERE id = :id AND restored_at IS NULL"), {"id": item_id})).first()
    if r is None:
        raise HTTPException(status_code=404, detail="nothing to restore")
    try:
        await anyio.to_thread.run_sync(
            library_trash.restore_file, r.trash_path, r.original_path, core_settings.music_dir)
    except library_trash.TrashError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    await index_paths([r.original_path])
    track = r.track if isinstance(r.track, dict) else json.loads(r.track or "{}")
    resolved = str(Path(r.original_path).expanduser().resolve(strict=False))
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE library_tracks SET title = COALESCE(:t, title), artist = COALESCE(:a, artist), "
            "album = COALESCE(:al, album), musicbrainz_recording_id = COALESCE(:mb, musicbrainz_recording_id), "
            "enrich_outcome = COALESCE(:o, enrich_outcome), "
            "enriched_at = CASE WHEN :o IS NULL THEN enriched_at ELSE now() END "
            "WHERE file_path IN (:p, :rp)"
        ), {"t": track.get("title"), "a": track.get("artist"), "al": track.get("album"),
            "mb": track.get("musicbrainz_recording_id"), "o": track.get("enrich_outcome"),
            "p": r.original_path, "rp": resolved})
        await s.execute(text("UPDATE library_trash SET restored_at = now() WHERE id = :id"),
                        {"id": item_id})
        await s.commit()
    return {"restored": True, "path": _rel(r.original_path)}


# ─── Needs review ────────────────────────────────────────────────────────


@router.get("/review", dependencies=READ)
async def list_review() -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT c.track_id, c.reason, c.candidates, c.stored_at, t.title, t.artist, t.album, "
            "t.duration_sec, t.file_path FROM library_match_candidates c "
            "JOIN library_tracks t ON t.id = c.track_id WHERE c.decided IS NULL "
            "ORDER BY c.stored_at DESC, c.track_id LIMIT 1000"
        ))).all()
    items = []
    for r in rows:
        cands = r.candidates if isinstance(r.candidates, list) else json.loads(r.candidates or "[]")
        items.append({
            "track_id": int(r.track_id), "reason": r.reason,
            "title": r.title, "artist": r.artist, "album": r.album, "duration_sec": r.duration_sec,
            "file": Path(r.file_path).name, "candidates": cands[:5],
        })
    return {"items": items}


class ApplyBody(BaseModel):
    index: int = Field(..., ge=0, le=4)


@router.post("/review/{track_id}/apply", dependencies=ADMIN)
async def apply_review(track_id: int, body: ApplyBody) -> dict[str, Any]:
    async with session_scope() as s:
        r = (await s.execute(text(
            "SELECT candidates FROM library_match_candidates WHERE track_id = :id AND decided IS NULL"
        ), {"id": track_id})).first()
        if r is None:
            raise HTTPException(status_code=404, detail="nothing waits for review on that song")
        cands = r[0] if isinstance(r[0], list) else json.loads(r[0] or "[]")
        if body.index >= len(cands):
            raise HTTPException(status_code=400, detail="no such candidate")
        c = cands[body.index]
        mb = c.get("recording_id") if is_mbid(c.get("recording_id")) else None
        rg = c.get("release_group_id") if is_mbid(c.get("release_group_id")) else None
        # Like a hand edit: the names, the id; outcome 'reviewed' so song
        # recognition never renames it; the year and genre a previous id
        # brought go, and the year / genre fetch asks about this one.
        await s.execute(text("""
            UPDATE library_tracks SET
                title = COALESCE(CAST(:t AS text), title),
                artist = COALESCE(CAST(:a AS text), artist),
                album = COALESCE(CAST(:al AS text), album),
                musicbrainz_recording_id = CAST(:mb AS text),
                musicbrainz_release_id = NULL,
                musicbrainz_release_group_id = CAST(:rg AS text),
                year = CASE WHEN year_source = 'musicbrainz' THEN NULL ELSE year END,
                year_source = CASE WHEN year_source = 'musicbrainz' THEN NULL ELSE year_source END,
                genre = CASE WHEN genre_source = 'musicbrainz' THEN NULL ELSE genre END,
                genre_keys = CASE WHEN genre_source = 'musicbrainz' THEN NULL ELSE genre_keys END,
                genre_source = CASE WHEN genre_source = 'musicbrainz' THEN NULL ELSE genre_source END,
                year_checked_at = NULL,
                genre_checked_at = NULL,
                enriched_at = now(),
                enrich_outcome = 'reviewed'
            WHERE id = :id
        """), {"id": track_id, "t": c.get("title"), "a": c.get("artist"), "al": c.get("album"),
               "mb": mb, "rg": rg})
        await s.execute(text(
            "UPDATE library_match_candidates SET decided = 'applied', decided_at = now() "
            "WHERE track_id = :id"), {"id": track_id})
        await s.execute(text("SELECT pg_notify('library_changed', 'reviewed')"))
        await s.commit()
    return {"applied": True, "track_id": track_id}


@router.post("/review/{track_id}/skip", dependencies=ADMIN)
async def skip_review(track_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        res = await s.execute(text(
            "UPDATE library_match_candidates SET decided = 'skipped', decided_at = now() "
            "WHERE track_id = :id AND decided IS NULL"), {"id": track_id})
        await s.commit()
    if not res.rowcount:
        raise HTTPException(status_code=404, detail="nothing waits for review on that song")
    return {"skipped": True, "track_id": track_id}


# ─── Cover art ───────────────────────────────────────────────────────────


@router.get("/cover-art", dependencies=READ)
async def cover_art_status() -> dict[str, Any]:
    async with session_scope() as s:
        counts = {r[0]: int(r[1]) for r in (await s.execute(text(
            "SELECT state, count(*) FROM library_cover_art GROUP BY state"))).all()}
        rows = (await s.execute(text(
            "SELECT c.track_id, c.state, c.note, c.had_art, c.written_at, c.checked_at, "
            "t.title, t.artist, t.album, t.cover_rev FROM library_cover_art c "
            "JOIN library_tracks t ON t.id = c.track_id "
            "WHERE c.state IN ('written', 'cached', 'skipped') "
            "ORDER BY c.checked_at DESC LIMIT 300"
        ))).all()
    return {
        "enabled": bool(core_settings.library_cover_art_enabled),
        "mode": core_settings.library_cover_art_mode,
        "counts": counts,
        "items": [{
            "track_id": int(r.track_id), "state": r.state, "note": r.note, "had_art": r.had_art,
            "written_at": r.written_at.isoformat() if r.written_at else None,
            "title": r.title, "artist": r.artist, "album": r.album,
            # The track's cover version (V059), for a ?v= cover URL.
            "cover_v": int(r.cover_rev or 0),
        } for r in rows],
    }


async def _undo_one(track_id: int) -> dict[str, Any]:
    async with session_scope() as s:
        r = (await s.execute(text(
            "SELECT c.state, c.snapshot_path, t.file_path FROM library_cover_art c "
            "JOIN library_tracks t ON t.id = c.track_id WHERE c.track_id = :id"), {"id": track_id})).first()
    if r is None or r.state not in ("written", "cached"):
        raise HTTPException(status_code=404, detail="Domovoi hasn't added art to that song")
    if r.state == "cached":
        await anyio.to_thread.run_sync(cover_art_writer.remove_cached_cover, track_id)
    else:
        try:
            await anyio.to_thread.run_sync(cover_art_writer.undo, Path(r.file_path), r.snapshot_path)
        except cover_art_writer.CoverWriteSkipped as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except OSError as e:
            raise HTTPException(status_code=409, detail=f"couldn't change the file: {e.strerror or e}") from e
    # The file as Undo left it, so the next rescan doesn't count it again.
    sig = (None if r.state == "cached" else
           await anyio.to_thread.run_sync(library_cover_version.cover_signature, r.file_path))
    async with session_scope() as s:
        await s.execute(text(
            "UPDATE library_cover_art SET state = 'undone', note = 'undone', checked_at = now() "
            "WHERE track_id = :id"), {"id": track_id})
        await s.execute(text("SELECT pg_notify('library_changed', :p)"), {"p": f"cover={track_id}"})
        # A new cover version (V059): the cover URL changes and
        # `library.cover.changed` names the track.
        await library_cover_version.bump(s, [track_id], {track_id: sig})
        await s.commit()
    return {"undone": True, "track_id": track_id}


@router.post("/cover-art/{track_id}/undo", dependencies=ADMIN)
async def undo_cover_art(track_id: int) -> dict[str, Any]:
    """Put the file back as it was before Domovoi's first cover write (its
    own pictures back, or none), or drop Domovoi's own copy. The song is
    then left alone (``undone``) unless its album changes."""
    return await _undo_one(track_id)


__all__ = ["router"]
