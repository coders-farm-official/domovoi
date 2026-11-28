"""What song recognition changed on each library track, and its undo (V088).

Every update by the enricher (``cause`` ``enrich``), its repair
(``repair``) and the re-match (``rematch``) that changes a track's title,
artist, album or MusicBrainz recording id writes one ``library_tag_changes``
row in the SAME statement as the update (:func:`logged_update`; run it with
:func:`execute_logged`, which locks the row first): the track's tracked
columns before (``old``) and after (``new``), and the ``batch_id`` of the
run that made it (one per sweep, repair or re-match). Before V088 these
updates overwrote the old values in place.

:func:`undo_batch` puts a batch's ``old`` values back, newest change first,
but only on a track whose title, artist and recording id still equal the
batch's ``new`` (a later sweep, re-match or edit since then wins; the album
is not compared, because the year and cover-art lookups fill an empty album
by the recording id afterwards, and the undo puts the old album back with
the rest), and never on a track a person edited by hand: ``enrich_outcome``
``manual`` (the dashboard's track editor, ``web/backend/api/music.py``
``patch_track``) or ``reviewed`` (a candidate applied from "Needs review").

An undo never leaves a track waiting for song recognition: where ``old``
was "not enriched yet" (an undone sweep match), the next sweep would only
match it again with the same answer, so the track is stamped ``disagreed``
instead (enriched now, its names as they were), and the answer that was
undone waits in "Needs review" as its candidate. An undo is itself logged
(``cause`` ``undo``, a batch of its own), so it can be undone the same way.

The files' own tags are never written (tags are database-only): this is
the only record of what a track's names were.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text

log = logging.getLogger(__name__)

CAUSE_ENRICH = "enrich"
CAUSE_REPAIR = "repair"
CAUSE_REMATCH = "rematch"
CAUSE_UNDO = "undo"
CAUSES = (CAUSE_ENRICH, CAUSE_REPAIR, CAUSE_REMATCH, CAUSE_UNDO)

#: The names a change is about: a row is logged when one of these moved.
NAME_COLUMNS = ("title", "artist", "album", "musicbrainz_recording_id")
#: What an undo holds a track to: it applies only while the track still has
#: the batch's values for these (not the album: see the module docstring).
UNDO_GUARD_COLUMNS = ("title", "artist", "musicbrainz_recording_id")

#: Everything a change keeps of a track, before and after: the names, the
#: ids and the year and genre that came with a recording id, and the
#: enrichment state (so an undo also puts a track back in, or out of, the
#: queue as it was).
TRACKED_COLUMNS = NAME_COLUMNS + (
    "musicbrainz_release_id", "musicbrainz_release_group_id",
    "year", "year_source", "year_checked_at",
    "genre", "genre_keys", "genre_source", "genre_checked_at",
    "enriched_at", "enrich_outcome",
)

#: Outcomes a person set: an undo never touches these tracks.
HAND_OUTCOMES = ("manual", "reviewed")
#: What an undo stamps a track that ``old`` would leave waiting.
UNDONE_OUTCOME = "disagreed"


def _snapshot(alias: str) -> str:
    return "jsonb_build_object(" + ", ".join(
        f"'{c}', {alias}.{c}" for c in TRACKED_COLUMNS
    ) + ")"


def _names(alias: str) -> str:
    return "(" + ", ".join(f"{alias}.snap->'{c}'" for c in NAME_COLUMNS) + ")"


def logged_update(set_sql: str, where_sql: str = "", from_sql: str = "") -> str:
    """One statement: ``UPDATE library_tracks SET <set_sql> WHERE id = :id
    <where_sql>`` and, when it moved a name (:data:`NAME_COLUMNS`), a
    ``library_tag_changes`` row with the track before and after, under
    ``:batch_id`` (a uuid) and ``:cause``. ``from_sql`` adds a ``FROM``
    list to the update. Answers one row: ``updated`` (0 or 1) and
    ``logged`` (0 or 1). Run it with :func:`execute_logged`."""
    where = f"library_tracks.id = :id {where_sql}".strip()
    from_part = f" FROM {from_sql}" if from_sql else ""
    # "before" reads the statement's snapshot, so it sees the row as it was
    # before "after" changed it. (No FOR UPDATE here: a row the same
    # statement updates would be skipped by it; execute_logged locks the
    # row first, so the snapshot can't be stale.)
    return f"""
        WITH before AS (
            SELECT t.id, {_snapshot('t')} AS snap
            FROM library_tracks t WHERE t.id = :id
        ), after AS (
            UPDATE library_tracks SET {set_sql}{from_part}
            WHERE {where}
            RETURNING library_tracks.id, {_snapshot('library_tracks')} AS snap
        ), logged AS (
            INSERT INTO library_tag_changes (track_id, batch_id, cause, old, new)
            SELECT a.id, CAST(:batch_id AS uuid), :cause, b.snap, a.snap
            FROM before b JOIN after a ON a.id = b.id
            WHERE {_names('b')} IS DISTINCT FROM {_names('a')}
            RETURNING id
        )
        SELECT (SELECT count(*) FROM after) AS updated,
               (SELECT count(*) FROM logged) AS logged
    """


_LOCK_SQL = text("SELECT 1 FROM library_tracks WHERE id = :id FOR UPDATE")


async def execute_logged(session: Any, sql: str, params: dict[str, Any]) -> Any:
    """Lock the track's row, then run a :func:`logged_update` statement on
    it (in the caller's transaction): a writer that changed the row since
    this transaction's last snapshot can't leave a stale ``old``. Returns
    the ``(updated, logged)`` row."""
    await session.execute(_LOCK_SQL, {"id": params["id"]})
    return (await session.execute(text(sql), params)).first()


def new_batch_id() -> str:
    return str(uuid.uuid4())


# Would the change's "old" leave the track waiting for song recognition
# (library_enricher.WAITING_WHERE: never enriched, or a pre-V020 stamp with
# no outcome and no recording id)?
_OLD_WAITS = (
    "(c.old->>'enriched_at' IS NULL OR (c.old->>'enrich_outcome' IS NULL "
    "AND c.old->>'musicbrainz_recording_id' IS NULL))"
)
# An undo puts the change's ``old`` back, every tracked column, read from
# the change row itself (JSON text back to each column's type), except that
# a track "old" would leave waiting is stamped :data:`UNDONE_OUTCOME` now.
_RESTORE_OLD_SET = f"""
    title = c.old->>'title',
    artist = c.old->>'artist',
    album = c.old->>'album',
    musicbrainz_recording_id = c.old->>'musicbrainz_recording_id',
    musicbrainz_release_id = c.old->>'musicbrainz_release_id',
    musicbrainz_release_group_id = c.old->>'musicbrainz_release_group_id',
    year = CAST(c.old->>'year' AS smallint),
    year_source = c.old->>'year_source',
    year_checked_at = CAST(c.old->>'year_checked_at' AS timestamptz),
    genre = c.old->>'genre',
    genre_keys = CASE WHEN jsonb_typeof(c.old->'genre_keys') = 'array'
        THEN ARRAY(SELECT jsonb_array_elements_text(c.old->'genre_keys')) END,
    genre_source = c.old->>'genre_source',
    genre_checked_at = CAST(c.old->>'genre_checked_at' AS timestamptz),
    enriched_at = CASE WHEN {_OLD_WAITS} THEN now()
        ELSE CAST(c.old->>'enriched_at' AS timestamptz) END,
    enrich_outcome = CASE WHEN {_OLD_WAITS} THEN CAST(:undone AS text)
        ELSE c.old->>'enrich_outcome' END
"""
_UNDO_SQL = logged_update(_RESTORE_OLD_SET, "AND c.id = :change_id", "library_tag_changes c")

_BATCH_CHANGES_SQL = """
    SELECT id, track_id, old, new FROM library_tag_changes
    WHERE batch_id = CAST(:batch_id AS uuid) AND undone_at IS NULL
    ORDER BY id DESC
"""
_CURRENT_SQL = (
    "SELECT " + ", ".join(NAME_COLUMNS) + ", enrich_outcome "
    "FROM library_tracks WHERE id = :id FOR UPDATE"
)


def _as_dict(value: Any) -> dict[str, Any]:
    """A jsonb value as the driver hands it back (decoded, or JSON text)."""
    if isinstance(value, dict):
        return value
    try:
        out = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def still_as_changed(current: dict[str, Any], new: dict[str, Any]) -> bool:
    """Does a track still have the title, artist and recording id a change
    gave it (:data:`UNDO_GUARD_COLUMNS`)? Pure."""
    return all(current.get(c) == new.get(c) for c in UNDO_GUARD_COLUMNS)


def old_waits(old: dict[str, Any]) -> bool:
    """Would ``old`` leave a track waiting for song recognition? Pure."""
    if old.get("enriched_at") is None:
        return True
    return old.get("enrich_outcome") is None and old.get("musicbrainz_recording_id") is None


async def batch_open(session: Any, batch_id: str) -> int:
    """How many changes of ``batch_id`` are not undone."""
    return int((await session.execute(text(
        "SELECT count(*) FROM library_tag_changes "
        "WHERE batch_id = CAST(:b AS uuid) AND undone_at IS NULL"
    ), {"b": str(batch_id)})).scalar_one() or 0)


async def _keep_undone_answer(session: Any, track_id: int, new: dict[str, Any]) -> None:
    """The answer an undo reverted, as the track's "Needs review" candidate."""
    if not (new.get("title") or new.get("artist")):
        return
    cand = {
        "title": new.get("title"), "artist": new.get("artist"), "album": new.get("album"),
        "length_sec": None, "score": None, "sources": None,
        "recording_id": new.get("musicbrainz_recording_id"),
        "release_group_id": new.get("musicbrainz_release_group_id"), "source": "undone",
    }
    try:
        async with session.begin_nested():
            await session.execute(text(
                "INSERT INTO library_match_candidates (track_id, reason, candidates, stored_at) "
                "VALUES (:id, 'disagreed', CAST(:c AS jsonb), now()) "
                "ON CONFLICT (track_id) DO UPDATE SET reason = EXCLUDED.reason, "
                "candidates = EXCLUDED.candidates, stored_at = EXCLUDED.stored_at "
                "WHERE library_match_candidates.decided IS NULL"
            ), {"id": track_id, "c": json.dumps([cand])})
    except Exception as e:  # noqa: BLE001 — a lane without V058; the undo still lands
        log.debug("tag changes undo: couldn't keep the undone answer for %s: %s", track_id, e)


async def undo_batch(batch_id: str) -> dict[str, Any]:
    """Put back the ``old`` values of every change in ``batch_id`` not yet
    undone, newest first, in one transaction. A track is skipped when its
    title, artist or recording id no longer equal the change's ``new``
    (``changed_since``) or a person edited it (``hand_edited``); one ``old``
    would leave waiting is stamped ``disagreed`` instead (``stamped``), with
    the undone answer kept for "Needs review". Returns ``{"batch_id",
    "changes", "restored", "stamped", "changed_since", "hand_edited",
    "undo_batch_id"}``; the undo's own changes are logged under
    ``undo_batch_id``."""
    from domovoi.db.session import session_scope

    undo_id = new_batch_id()
    out: dict[str, Any] = {
        "batch_id": str(batch_id), "changes": 0, "restored": 0, "stamped": 0,
        "changed_since": 0, "hand_edited": 0, "undo_batch_id": undo_id,
    }
    async with session_scope() as s:
        changes = (await s.execute(text(_BATCH_CHANGES_SQL), {"batch_id": str(batch_id)})).all()
        out["changes"] = len(changes)
        restored_ids: list[int] = []
        for ch in changes:
            cur = (await s.execute(text(_CURRENT_SQL), {"id": ch.track_id})).mappings().first()
            if cur is None:
                continue
            if cur["enrich_outcome"] in HAND_OUTCOMES:
                out["hand_edited"] += 1
                continue
            new = _as_dict(ch.new)
            if not still_as_changed(dict(cur), new):
                out["changed_since"] += 1
                continue
            await execute_logged(s, _UNDO_SQL, {
                "id": ch.track_id, "change_id": ch.id, "undone": UNDONE_OUTCOME,
                "batch_id": undo_id, "cause": CAUSE_UNDO,
            })
            await s.execute(
                text("UPDATE library_tag_changes SET undone_at = now() WHERE id = :id"),
                {"id": ch.id},
            )
            if old_waits(_as_dict(ch.old)):
                out["stamped"] += 1
                await _keep_undone_answer(s, int(ch.track_id), new)
            restored_ids.append(int(ch.track_id))
        out["restored"] = len(restored_ids)
        if restored_ids:
            # A track put back as matched no longer waits in "Needs review".
            await s.execute(text(
                "DELETE FROM library_match_candidates WHERE track_id = ANY(:ids) "
                "AND decided IS NULL AND track_id IN (SELECT id FROM library_tracks "
                "WHERE id = ANY(:ids) AND enrich_outcome = 'matched')"
            ), {"ids": restored_ids})
            await s.execute(
                text("SELECT pg_notify('library_changed', :payload)"),
                {"payload": f"undone={len(restored_ids)}"},
            )
        await s.commit()
    if out["restored"]:
        try:
            from domovoi.handlers.shared import library_match

            library_match.invalidate(library=True)
        except Exception as e:  # noqa: BLE001 — the fingerprint catches up anyway
            log.debug("tag changes undo: couldn't refresh the name index: %s", e)
    log.info(
        "library tag changes: undid batch %s: restored=%d (stamped %d) changed_since=%d "
        "hand_edited=%d (undo batch %s)", batch_id, out["restored"], out["stamped"],
        out["changed_since"], out["hand_edited"], undo_id,
    )
    return out


async def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    """The most recent batches, newest first: ``batch_id``, ``cause``,
    ``changes``, ``undone``, ``first_at``, ``last_at``."""
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT batch_id, min(cause) AS cause, count(*) AS changes,
                   count(undone_at) AS undone, min(changed_at) AS first_at,
                   max(changed_at) AS last_at
            FROM library_tag_changes GROUP BY batch_id
            ORDER BY max(changed_at) DESC LIMIT :n
        """), {"n": max(1, min(int(limit), 500))})).all()
    return [{
        "batch_id": str(r.batch_id), "cause": r.cause, "changes": int(r.changes),
        "undone": int(r.undone),
        "first_at": r.first_at.isoformat() if r.first_at else None,
        "last_at": r.last_at.isoformat() if r.last_at else None,
    } for r in rows]
