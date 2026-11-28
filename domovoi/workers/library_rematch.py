"""The re-match: hold every track song recognition already matched to the
strict name check (2026-11-28), list the ones that fail, and on the owner's
say-so put the file's own names back.

Why: the 2026-11-12 sweep took AcoustID's first recording with only a score
check, and the agreement rule then let a wrong title through whenever the
artist agreed. "Mo'murda" by Bone Thugs-N-Harmony became "Ghetto Cowboy" (a
file 20 s longer in the same folder), Ed Sheeran's "Perfect" became "Beyond
the Pale", two Beatles songs both became "Ask Me Why". The sweep now holds
the title too (``library_enricher.names_agree``); this finds and fixes the
rows written before that.

**The audit** (:func:`audit_library`, pure, no file read): for each row, its
title against its file's name (and, when given, the file's own tag title
and artist), with the strict rule (``library_enricher._row_fits_file``);
rows that share one MusicBrainz recording id although their files name
different songs; rows whose title is another file's song. Once a re-match
has been applied, running it again comes back clean.

**The dry run** (:func:`dry_run`) changes nothing. It reads each matched
row's file tags (``library_indexer.read_metadata``: tags are never
written, so they still hold the file's own names), runs the audit with
them, and lists every row that fails with its proposed action:

* ``restore_rematch`` — "restore file names, then re-match": the file names
  a song (a real title and a real artist). Its names go back, its
  recording id (and the year and genre fetched by it) go, and it goes back
  in the enrichment queue; the next sweep matches it under the strict
  rule, walking every candidate, or marks it ``disagreed`` for "Needs
  review".
* ``restore_review`` — "restore only, needs review": the file's names are
  not enough to judge a new answer by (no usable artist, or names that look
  like a video upload's). Its names go back and it is marked ``disagreed``,
  with the wrong match kept as a candidate in "Needs review".
* ``bad_encoding`` — the row's names, or its file's tags, hold text that
  couldn't be decoded (a lone surrogate, a control character, U+FFFD).
  Listed only; nothing is changed automatically.

A dry run's ``batch_id`` is derived from exactly what it listed (a UUID of
the list's digest). :func:`apply` runs the dry run again and changes
anything only when the ``batch_id`` is the one given, so what is applied is
exactly the list the owner saw; every change is logged in
``library_tag_changes`` under that ``batch_id`` with cause ``rematch``, and
``library_tag_changes.undo_batch`` undoes it.

Only rows the enricher matched (``enrich_outcome`` ``matched``) are ever
proposed for a change: a hand edit (``manual``), a reviewed candidate
(``reviewed``) or a file's own id (``file``) is never touched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from domovoi import library_tag_changes as tag_changes
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.workers import library_enricher as le

log = logging.getLogger(__name__)

ACTION_REMATCH = "restore_rematch"
ACTION_REVIEW = "restore_review"
ACTION_BAD_ENCODING = "bad_encoding"
ACTION_LABELS = {
    ACTION_REMATCH: "restore file names, then re-match",
    ACTION_REVIEW: "restore only, needs review",
    ACTION_BAD_ENCODING: "bad encoding",
}

# Text no database row should hold: a lone surrogate, a C1 control
# character, or the replacement character a decoder left.
_BAD_TEXT_RE = re.compile("[\ud800-\udfff\x80-\x9f�]")
# The UUID namespace the dry run's batch id is derived in.
_BATCH_NS = uuid.UUID("6f3e2a1c-5b7d-4c8e-9a0f-1d2c3b4a5e60")


@dataclass
class Track:
    """A library row as the audit reads it."""

    id: int
    file_path: str
    title: str | None
    artist: str | None
    album: str | None = None
    recording_id: str | None = None
    outcome: str | None = None
    duration_sec: int | None = None


@dataclass
class FileNames:
    """A file's own names: its tags as the indexer reads them (title and
    artist, the file name's when the tags have none)."""

    title: str | None
    artist: str | None
    album: str | None = None
    bad_encoding: bool = False


@dataclass
class Finding:
    """One row the audit flags."""

    id: int
    kind: str                     # "mismatch" or "bad_encoding"
    reason: str
    shared_with: list[int] = field(default_factory=list)
    title_of: list[int] = field(default_factory=list)


def has_bad_text(*values: str | None) -> bool:
    """Do any of ``values`` hold a lone surrogate, a C1 control character
    or U+FFFD? Pure."""
    return any(v and _BAD_TEXT_RE.search(v) for v in values)


def stem_of(file_path: str) -> str:
    return Path(str(file_path).replace("\\", "/")).stem


def name_readings(track: Track) -> list[str]:
    """What a file's name may call its song (``library_enricher.
    title_readings`` of its stem, the row's artist left out); [] for a name
    that names none ("track01", "Track 3"). Pure."""
    stem = stem_of(track.file_path)
    if not le.real_title(stem):
        return []
    return [r for r in le.title_readings(stem, artist=track.artist) if le.real_title(r)]


def _same_song(a: list[str], b: list[str]) -> bool:
    return any(le.titles_agree(x, y) for x in a for y in b)


def audit_names(
    row_title: str | None, row_artist: str | None,
    file_title: str | None, file_artist: str | None, file_path: str,
) -> str | None:
    """Why a matched row's names fail the strict check against its file's
    (``file_title`` / ``file_artist``: its tags, or what the indexer reads
    from its name), or None when they fit or the file names nothing to
    hold them to. Pure."""
    stem = stem_of(file_path)
    ours = le.file_names(file_path, file_title, file_artist)
    if ours == (None, None):
        return None
    if le._row_fits_file(ours, stem, row_title, row_artist):
        return None
    return (f"{row_title!r} by {row_artist!r} doesn't agree with the file's "
            f"{ours[0]!r} by {ours[1]!r}")


def _file_names_from_name(track: Track) -> FileNames:
    """What a file's name alone says: its whole stem as the title (every
    "A - B" part is a reading of it, :func:`library_enricher.title_readings`)
    and no artist, as an "Artist - Title" name can be either way round."""
    return FileNames(title=stem_of(track.file_path), artist=None)


def shared_recordings(tracks: Iterable[Track]) -> dict[int, list[int]]:
    """Rows that share a MusicBrainz recording id with another row whose
    file names a different song: row id → the other row ids. Copies of the
    same song (their files' titles agree) are not listed. Pure."""
    groups: dict[str, list[Track]] = defaultdict(list)
    for t in tracks:
        if t.recording_id:
            groups[t.recording_id].append(t)
    out: dict[int, list[int]] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        readings = {m.id: name_readings(m) for m in members}
        for m in members:
            others = [
                o.id for o in members if o.id != m.id
                and readings[m.id] and readings[o.id]
                and not _same_song(readings[m.id], readings[o.id])
            ]
            if others:
                out[m.id] = others
    return out


def title_owners(tracks: Iterable[Track]) -> dict[int, list[int]]:
    """Rows whose title is not their own file's song but another file's:
    row id → the ids of the files whose name is that title (how most wrong
    matches looked: a neighbour in the same folder, a few seconds apart).
    Pure."""
    tracks = list(tracks)
    readings = {t.id: name_readings(t) for t in tracks}
    by_key: dict[str, list[int]] = defaultdict(list)
    for t in tracks:
        for key in dict.fromkeys(le._name_key(r) for r in readings[t.id]):
            if key:
                by_key[key].append(t.id)
    out: dict[int, list[int]] = {}
    for t in tracks:
        own = readings[t.id]
        if not t.title or not own or _same_song(own, [t.title]):
            continue
        owners = [i for i in by_key.get(le._name_key(t.title), []) if i != t.id]
        if owners:
            out[t.id] = owners
    return out


def audit_library(
    tracks: Iterable[Track], files: dict[int, FileNames] | None = None,
) -> list[Finding]:
    """The audit: every row a re-match would act on or list, best evidence
    first. ``files``: each row's file names (its tags); a row without an
    entry is held to what its file's NAME says. Only ``matched`` rows are
    held to their file's names; a row of any outcome whose names (or its
    file's tags) hold undecodable text is listed as ``bad_encoding``. Pure:
    no file is read here."""
    tracks = list(tracks)
    files = files or {}
    shared = shared_recordings(tracks)
    owners = title_owners(tracks)
    out: list[Finding] = []
    for t in tracks:
        names = files.get(t.id)
        if has_bad_text(t.title, t.artist, t.album) or (names is not None and names.bad_encoding):
            out.append(Finding(t.id, "bad_encoding",
                               "the names hold text that couldn't be decoded"))
            continue
        if t.outcome != le.OUTCOME_MATCHED:
            continue
        names = names or _file_names_from_name(t)
        reason = audit_names(t.title, t.artist, names.title, names.artist, t.file_path)
        if reason is None:
            continue
        out.append(Finding(t.id, "mismatch", reason,
                           shared_with=shared.get(t.id, []), title_of=owners.get(t.id, [])))
    return out


def _put_back(track: Track, names: FileNames) -> tuple[str | None, str | None, str]:
    """``(title, artist, action)`` a re-match would give a mismatched row.

    The file's names as the repair restores them (``_names_to_put_back``:
    upload noise dropped where that leaves a song's names). With a real
    title and a usable artist: re-match. Otherwise the title goes back
    (cleaned) and the artist is the file's when usable, else the row's;
    the row waits in "Needs review"."""
    ours = le.file_names(track.file_path, names.title, names.artist)
    back = le._names_to_put_back(ours)
    if back is not None and le.real_title(back[0]) and le.artist_usable(back[1]):
        return back[0], back[1], ACTION_REMATCH
    candidates = [back[1] if back is not None else None, ours[1]]
    artist = next((a for a in candidates if le.artist_usable(a)), None) or track.artist
    title = (back[0] if back is not None else None) or ours[0] or names.title
    return _song_title(title, artist), artist, ACTION_REVIEW


def _song_title(title: str | None, artist: str | None) -> str | None:
    """An upload-style title as a song's: "The Beatles - Drive My Car
    (Lyrics)" by The Beatles → "Drive My Car". The title as it is when it
    doesn't come down to one part."""
    from domovoi.handlers.shared.spoken_names import clean_title

    if not title or not le.looks_like_upload_names(title, None):
        return title
    singles = [r for r in le.title_readings(title, artist=artist)
               if not le._NAME_SEP_RE.search(r) and le.real_title(r)]
    chosen = singles[0] if len(singles) == 1 else le.strip_upload_noise(title)
    return clean_title(chosen) or chosen


def _rel(file_path: str) -> str:
    try:
        return Path(file_path).resolve(strict=False).relative_to(
            Path(settings.music_dir).expanduser().resolve(strict=False)).as_posix()
    except (OSError, ValueError):
        return Path(str(file_path).replace("\\", "/")).name


def plan_rows(
    tracks: list[Track], files: dict[int, FileNames],
) -> list[dict[str, Any]]:
    """The dry run's list from the audit: one entry per flagged row, by id.
    Pure."""
    by_id = {t.id: t for t in tracks}
    rows: list[dict[str, Any]] = []
    for f in audit_library(tracks, files):
        t = by_id[f.id]
        names = files.get(t.id) or _file_names_from_name(t)
        entry: dict[str, Any] = {
            "id": t.id,
            "file": _rel(t.file_path),
            "db": {"title": t.title, "artist": t.artist, "album": t.album,
                   "recording_id": t.recording_id},
            "file_tags": {"title": names.title, "artist": names.artist},
            "reason": f.reason,
            "shared_recording_with": f.shared_with,
            "title_belongs_to": f.title_of,
        }
        if f.kind == "bad_encoding":
            entry.update(action=ACTION_BAD_ENCODING, put_back=None)
        else:
            title, artist, action = _put_back(t, names)
            entry.update(action=action, put_back={"title": title, "artist": artist,
                                                  "album": names.album or t.album})
        entry["action_label"] = ACTION_LABELS[entry["action"]]
        rows.append(entry)
    rows.sort(key=lambda r: r["id"])
    return rows


def batch_id_for(rows: list[dict[str, Any]]) -> str:
    """The id of exactly this list: a UUID derived from its digest. Pure."""
    digest = hashlib.sha256(
        json.dumps(rows, sort_keys=True, ensure_ascii=True, default=str).encode("ascii")
    ).hexdigest()
    return str(uuid.uuid5(_BATCH_NS, digest))


def counts_of(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {ACTION_REMATCH: 0, ACTION_REVIEW: 0, ACTION_BAD_ENCODING: 0}
    for r in rows:
        out[r["action"]] += 1
    out["total"] = len(rows)
    return out


_ROWS_SQL = """
    SELECT id, file_path, title, artist, album, musicbrainz_recording_id,
           enrich_outcome, duration_sec
    FROM library_tracks ORDER BY id
"""


async def _load() -> tuple[list[Track], dict[int, FileNames], int]:
    """Every row, and the file names of every matched row whose file is
    there (read like the indexer reads them, off the event loop)."""
    from domovoi.workers import library_indexer

    async with session_scope() as s:
        raw = (await s.execute(text(_ROWS_SQL))).all()
        known = await library_indexer.known_artist_keys(s) if raw else None
    tracks = [Track(int(r.id), r.file_path, r.title, r.artist, r.album,
                    r.musicbrainz_recording_id, r.enrich_outcome, r.duration_sec) for r in raw]
    files: dict[int, FileNames] = {}
    unreadable = 0
    for t in tracks:
        if t.outcome != le.OUTCOME_MATCHED:
            continue
        path = Path(t.file_path)
        if not path.exists():
            continue
        try:
            meta = await library_indexer.read_metadata(path, known)
        except Exception as e:  # noqa: BLE001 — one unreadable file
            unreadable += 1
            log.debug("library re-match: couldn't read %s: %s", path.name, e)
            continue
        files[t.id] = FileNames(meta.get("title"), meta.get("artist"), meta.get("album"),
                                bool(meta.get("bad_encoding")))
    return tracks, files, unreadable


async def dry_run() -> dict[str, Any]:
    """The list a re-match would act on, and its ``batch_id``. Changes
    nothing. ``{"dry_run": true, "batch_id", "counts", "rows",
    "checked", "unreadable"}``; each row: ``id``, ``file``, ``db``
    (title / artist / album / recording_id), ``file_tags`` (title /
    artist), ``reason``, ``action``, ``action_label``, ``put_back``,
    ``shared_recording_with``, ``title_belongs_to``."""
    tracks, files, unreadable = await _load()
    # A matched row whose file is gone has no file names to hold it to.
    judged = [t for t in tracks if t.outcome != le.OUTCOME_MATCHED or t.id in files]
    rows = plan_rows(judged, files)
    out = {
        "dry_run": True,
        "batch_id": batch_id_for(rows),
        "counts": counts_of(rows),
        "checked": sum(1 for t in judged if t.outcome == le.OUTCOME_MATCHED),
        "unreadable": unreadable,
        "rows": rows,
    }
    log.info("library re-match dry run: %s (batch %s)", out["counts"], out["batch_id"])
    return out


# Re-match: the file's names back, the wrong recording (and what came with
# it) gone, and back in the queue as never enriched, so the next sweep
# matches it under the strict rule.
_REMATCH_SQL = tag_changes.logged_update(
    le.RESTORE_NAMES_SET + """,
        musicbrainz_release_id = NULL,
        musicbrainz_release_group_id = NULL,
        year_checked_at = NULL,
        genre_checked_at = NULL,
        enriched_at = NULL,
        enrich_outcome = NULL""",
    "AND enrich_outcome = :matched",
)
# Restore only: the file's names back, marked disagreed for "Needs review".
_REVIEW_SQL = tag_changes.logged_update(
    le.RESTORE_NAMES_SET + """,
        musicbrainz_release_id = NULL,
        musicbrainz_release_group_id = NULL,
        enriched_at = NOW(),
        enrich_outcome = :disagreed""",
    "AND enrich_outcome = :matched",
)


class PlanChanged(Exception):
    """The library no longer gives the list the ``batch_id`` named."""

    def __init__(self, batch_id: str) -> None:
        super().__init__(batch_id)
        self.batch_id = batch_id


async def apply(batch_id: str) -> dict[str, Any]:
    """Apply the dry run whose ``batch_id`` is given, in one transaction.
    Raises :class:`PlanChanged` (carrying the current ``batch_id``) when a
    dry run now lists something else. ``bad_encoding`` rows are left as
    they are. Returns ``{"applied", "batch_id", "rematch", "review",
    "skipped"}`` (``skipped``: "" when it ran, else "running")."""
    out: dict[str, Any] = {"applied": 0, "batch_id": str(batch_id), "rematch": 0,
                           "review": 0, "skipped": ""}
    if le.enrich_running():
        out["skipped"] = "running"
        return out
    le._sweep_running = True
    try:
        plan = await dry_run()
        if plan["batch_id"] != str(batch_id):
            raise PlanChanged(plan["batch_id"])
        await _apply_rows(plan["rows"], str(batch_id), out)
    finally:
        le._sweep_running = False
    log.info("library re-match applied: batch %s: rematch=%d review=%d",
             batch_id, out["rematch"], out["review"])
    return out


async def _apply_rows(rows: list[dict[str, Any]], batch_id: str, out: dict[str, Any]) -> None:
    async with session_scope() as s:
        for r in rows:
            if r["action"] == ACTION_BAD_ENCODING:
                continue
            back = r["put_back"] or {}
            params = {
                "id": r["id"], "t": back.get("title"), "a": back.get("artist"),
                "al": back.get("album"), "matched": le.OUTCOME_MATCHED,
                "disagreed": le.OUTCOME_DISAGREED,
                "batch_id": batch_id, "cause": tag_changes.CAUSE_REMATCH,
            }
            sql = _REMATCH_SQL if r["action"] == ACTION_REMATCH else _REVIEW_SQL
            done = (await s.execute(text(sql), params)).first()
            if not done or not done.updated:
                continue
            if r["action"] == ACTION_REVIEW:
                db = r["db"]
                wrong = le.EnrichmentResult(
                    title=db.get("title"), artist=db.get("artist"), album=db.get("album"),
                    musicbrainz_recording_id=db.get("recording_id"), source="acoustid",
                )
                await le._store_candidates(
                    s, r["id"], "disagreed", le.Lookup(le.Verdict.DISAGREED, result=wrong),
                )
                out["review"] += 1
            else:
                await s.execute(text(le._CANDIDATES_CLEAR_SQL), {"id": r["id"]})
                out["rematch"] += 1
            out["applied"] += 1
        if out["applied"]:
            await s.execute(
                text("SELECT pg_notify('library_changed', :payload)"),
                {"payload": f"rematched={out['applied']}"},
            )
        await s.commit()
    if out["applied"]:
        try:
            from domovoi.handlers.shared import library_match

            library_match.invalidate(library=True)
        except Exception as e:  # noqa: BLE001 — the fingerprint catches up anyway
            log.debug("library re-match: couldn't refresh the name index: %s", e)
