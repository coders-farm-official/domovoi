"""Cover art from the Cover Art Archive, written into the music files (V058).

Owner request 2026-11-14 (it extends the 2026-10-02 decision that a cover
comes from the file itself): for a track whose identity is confirmed, the
album's front cover is fetched from the Cover Art Archive
(coverartarchive.org, MusicBrainz's image archive) and written into the
file, so every player that reads the file shows it.

**Which tracks.** A MusicBrainz recording id that was confirmed: song
recognition's match that passed the agreement rules (outcome ``matched``),
a candidate a person applied from "Needs review" (``reviewed``), or the
file's own id (``file``). A track whose album isn't known yet (no release
or release group id) is first looked up by its recording id on MusicBrainz
(the year fetch's request, ``MusicBrainzClient.recording_details``, which
chooses an Official album) and the ids are stored.

**What is asked.** ``GET https://coverartarchive.org/release/<id>/front-1200``
(the archive's 1200-pixel copy), then ``front-500``; with no release or no
front cover there, the same for the release group. The archive answers
with a redirect to its storage on archive.org; each hop is checked by the
outbound gate (``domovoi.net_safety``, on an ``egress`` client) and at most
:data:`MAX_IMAGE_BYTES` are read. Only a JPEG or PNG is kept. What leaves
the house: the album's MusicBrainz id, and the client's User-Agent.

**What is done with it** (``domovoi/cover_art_writer.py``):

* MP3 and FLAC: written into the file as its front cover, through a
  verified copy and an atomic rename, the file's own pictures saved first
  for Undo. Under "Only add missing art" a file with any picture, or with
  a cover image in its folder, is left alone. A file whose front cover is
  already the same picture is not rewritten (``same``).
* Other formats (M4A, Ogg, Opus, WMA ...): not written; the picture is kept
  in Domovoi's cover folder and the dashboard shows it when the file has no
  art of its own (``cached``).
* A folder Domovoi may not write, a file that changed meanwhile, a copy
  that didn't verify: the file is left as it was and the reason recorded
  (``skipped``).
* The archive has nothing: the file is left exactly as it was and ``no
  art available`` is recorded; asked again only after
  :data:`NO_ART_RETRY_DAYS` days, or after "Rescan library" clears it.

A track already written from the same release is not fetched again; a
track whose release changes (re-identified) is.

**Gated** on every tick and before every request: the setting
``library_cover_art_enabled`` (its default follows the internet answer,
``config.PROFILE_DEFAULTS``), ``egress.internet_allowed()``, the
connectivity probe reporting online, no shutdown. Paced: at most
:data:`BATCH_SIZE` tracks a tick, about one archive request a second.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi import egress, lifecycle
from domovoi.config import settings
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

BATCH_SIZE = 10
NO_ART_RETRY_DAYS = 30
MAX_IMAGE_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT_SEC = 20.0
REQUEST_INTERVAL_SEC = 1.0
PAUSE_AFTER_ERROR_SEC = 300.0
CAA_BASE = "https://coverartarchive.org"
USER_AGENT = "domovoi-cover-art/1.0 (+https://github.com/coders-farm-official/domovoi)"
#: The outcomes whose recording id is a confirmed identity.
CONFIRMED_OUTCOMES = ("matched", "reviewed", "file")


def _probe_online() -> bool:
    from domovoi.connectivity import current_probe

    probe = current_probe()
    return probe is not None and bool(probe.online)


def allowed() -> bool:
    """The gate (module docstring)."""
    if not settings.library_cover_art_enabled:
        return False
    if not egress.internet_allowed() or lifecycle.shutting_down():
        return False
    return _probe_online()


def cover_dir() -> Path:
    return Path(settings.cover_art_dir).expanduser()


def snapshot_path_for(track_id: int, file_path: str) -> Path:
    digest = hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:16]
    return cover_dir() / "originals" / f"{int(track_id)}-{digest}.json"


def cache_path_for(track_id: int, mime: str) -> Path:
    from domovoi.cover_art_writer import cover_cache_path

    return cover_cache_path(track_id, mime)


def mode() -> str:
    m = str(settings.library_cover_art_mode or "replace").strip().lower()
    return m if m in ("replace", "add_missing") else "replace"


# ─── the archive ─────────────────────────────────────────────────────────


class ArchiveUnavailable(Exception):
    """No answer (line down, internet turned off, 5xx): try again later."""


def candidate_urls(release_id: str | None, release_group_id: str | None) -> list[tuple[str, str, str]]:
    """``(kind, id, url)`` in the order they are asked. Pure."""
    from domovoi.clients.musicbrainz import is_mbid

    out: list[tuple[str, str, str]] = []
    for kind, mbid in (("release", release_id), ("release-group", release_group_id)):
        if mbid and is_mbid(mbid):
            for size in ("1200", "500"):
                out.append((kind, mbid, f"{CAA_BASE}/{kind}/{mbid}/front-{size}"))
    return out


async def fetch_front(release_id: str | None, release_group_id: str | None) -> tuple[bytes, str, str, str] | None:
    """``(image, mime, kind, id)`` of the first front cover the archive has,
    None when it has none (404s only). Raises :class:`ArchiveUnavailable`
    when it couldn't be asked."""
    from domovoi import net_safety
    from domovoi.cover_art_writer import sniff_image

    for kind, mbid, url in candidate_urls(release_id, release_group_id):
        if not allowed():
            raise ArchiveUnavailable("turned off")
        try:
            egress.require_destination(url)
            client = egress.async_client(timeout=FETCH_TIMEOUT_SEC)
            try:
                result = await net_safety.fetch_bytes(
                    url, max_bytes=MAX_IMAGE_BYTES, timeout=FETCH_TIMEOUT_SEC,
                    headers={"User-Agent": USER_AGENT, "Accept": "image/jpeg, image/png"},
                    client=client,
                )
            finally:
                await client.aclose()
        except egress.InternetTurnedOff as e:
            raise ArchiveUnavailable("internet turned off") from e
        except Exception as e:  # noqa: BLE001 — the line, the gate, a too-large answer
            raise ArchiveUnavailable(type(e).__name__) from e
        finally:
            await asyncio.sleep(REQUEST_INTERVAL_SEC)
        if result.status_code in (404, 400):
            continue
        if result.status_code == 429 or result.status_code >= 500:
            raise ArchiveUnavailable(f"HTTP {result.status_code}")
        if result.status_code != 200:
            continue
        mime = sniff_image(result.content)
        if mime is None:
            continue
        return result.content, mime, kind, mbid
    return None


# ─── deciding and writing ────────────────────────────────────────────────


def _folder_image(path: Path) -> bool:
    """A cover image beside the file the dashboard already shows."""
    stems = ("cover", "folder", "front", "album")
    exts = (".jpg", ".jpeg", ".png", ".webp")
    try:
        for p in path.parent.iterdir():
            if p.stem.lower() in stems and p.suffix.lower() in exts and p.is_file():
                return True
    except OSError:
        return False
    return False


def _embedded_any(path: Path) -> bool:
    """Any picture in the file, whatever its format."""
    from domovoi import media_tags

    try:
        f = media_tags.read(path)
    except Exception:  # noqa: BLE001
        return False
    if f is None:
        return False
    if f.pictures:
        return True
    tags = f.tags
    if tags is None:
        return False
    for key in ("APIC", "covr", "WM/Picture", "metadata_block_picture", "coverart"):
        try:
            getall = getattr(tags, "getall", None)
            if (getall(key) if callable(getall) else tags.get(key)):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def apply_cover(track_id: int, file_path: str, image: bytes, mime: str) -> dict[str, Any]:
    """Blocking: write (or cache) ``image`` for one track and say what
    happened: ``{"state", "note", "snapshot_path", "had_art", "created_tag",
    "cache_path", "size", "mtime_ns"}``."""
    from domovoi.cover_art_writer import CoverWriteSkipped, writable_kind, write_front_cover

    path = Path(file_path)
    out: dict[str, Any] = {"state": "skipped", "note": None, "snapshot_path": None, "had_art": None,
                           "created_tag": False, "cache_path": None, "size": None, "mtime_ns": None}
    if not path.is_file():
        out["note"] = "the file isn't there"
        return out
    only_missing = mode() == "add_missing"
    if only_missing and (_folder_image(path) or _embedded_any(path)):
        out["note"] = "the file already has art"
        return out
    if writable_kind(path) is None:
        # Not a format Domovoi writes: keep Domovoi's own copy for display.
        if _embedded_any(path) or _folder_image(path):
            out["note"] = "Domovoi doesn't write this kind of file, and it has art of its own"
            return out
        from domovoi.cover_art_writer import remove_cached_cover

        remove_cached_cover(track_id)
        dest = cache_path_for(track_id, mime)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(image)
        tmp.replace(dest)
        out.update(state="cached", cache_path=str(dest),
                   note="Domovoi doesn't write this kind of file; shown from Domovoi's own copy")
        return out
    snap = snapshot_path_for(track_id, file_path)
    try:
        res = write_front_cover(path, image, snapshot_path=snap, only_if_missing=only_missing)
    except CoverWriteSkipped as e:
        if str(e) == "same":
            out.update(state="same", note="the file already has this cover")
        else:
            out["note"] = str(e)[:300]
        return out
    except OSError as e:
        out["note"] = f"couldn't write the file: {e.strerror or e}"[:300]
        return out
    out.update(state="written", snapshot_path=res.snapshot_path, had_art=res.had_art,
               created_tag=res.created_tag, size=res.size, mtime_ns=res.mtime_ns)
    return out


# ─── the queue ───────────────────────────────────────────────────────────

_DUE_SQL = text(f"""
    SELECT t.id, t.file_path, t.musicbrainz_recording_id, t.musicbrainz_release_id,
           t.musicbrainz_release_group_id, c.state, c.source_id
    FROM library_tracks t
    LEFT JOIN library_cover_art c ON c.track_id = t.id
    WHERE t.musicbrainz_recording_id IS NOT NULL
      AND t.enrich_outcome IN ({", ".join(f"'{o}'" for o in CONFIRMED_OUTCOMES)})
      AND (c.track_id IS NULL
           OR (c.state = 'no_art' AND c.checked_at < now() - make_interval(days => :retry))
           OR (c.state IN ('written', 'same', 'cached')
               AND c.source_id IS DISTINCT FROM t.musicbrainz_release_id
               AND c.source_id IS DISTINCT FROM t.musicbrainz_release_group_id))
      AND t.id > :after
    ORDER BY t.id
    LIMIT :n
""")

_RECORD_SQL = text("""
    INSERT INTO library_cover_art (track_id, state, source_kind, source_id, note, snapshot_path,
                                   had_art, created_tag, cache_path, file_size, file_mtime_ns,
                                   written_at, checked_at)
    VALUES (:id, :state, :kind, :sid, :note, :snap, :had, :created, :cache, :size, :mtime,
            CASE WHEN :state = 'written' THEN now() ELSE NULL END, now())
    ON CONFLICT (track_id) DO UPDATE SET
        state = EXCLUDED.state, source_kind = EXCLUDED.source_kind, source_id = EXCLUDED.source_id,
        note = EXCLUDED.note,
        -- The FIRST snapshot is kept across later writes (Undo returns the
        -- file to how it was before Domovoi touched it).
        snapshot_path = COALESCE(library_cover_art.snapshot_path, EXCLUDED.snapshot_path),
        had_art = COALESCE(library_cover_art.had_art, EXCLUDED.had_art),
        created_tag = library_cover_art.created_tag OR EXCLUDED.created_tag,
        cache_path = COALESCE(EXCLUDED.cache_path, library_cover_art.cache_path),
        file_size = COALESCE(EXCLUDED.file_size, library_cover_art.file_size),
        file_mtime_ns = COALESCE(EXCLUDED.file_mtime_ns, library_cover_art.file_mtime_ns),
        written_at = COALESCE(EXCLUDED.written_at, library_cover_art.written_at),
        checked_at = now()
""")


async def _record(track_id: int, outcome: dict[str, Any], kind: str | None, sid: str | None) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(_RECORD_SQL, {
            "id": track_id, "state": outcome["state"], "kind": kind, "sid": sid,
            "note": outcome.get("note"), "snap": outcome.get("snapshot_path"),
            "had": outcome.get("had_art"), "created": bool(outcome.get("created_tag")),
            "cache": outcome.get("cache_path"), "size": outcome.get("size"),
            "mtime": outcome.get("mtime_ns"),
        })
        if outcome["state"] in ("written", "cached"):
            await s.execute(text("SELECT pg_notify('library_changed', :p)"),
                            {"p": f"cover={track_id}"})
        await s.commit()


async def _store_release(track_id: int, details: Any) -> None:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text(
            "UPDATE library_tracks SET "
            "musicbrainz_release_id = COALESCE(musicbrainz_release_id, CAST(:rel AS text)), "
            "musicbrainz_release_group_id = COALESCE(musicbrainz_release_group_id, CAST(:rg AS text)), "
            "album = COALESCE(NULLIF(album, ''), CAST(:al AS text)) WHERE id = :id"
        ), {"id": track_id, "rel": details.release_id, "rg": details.release_group_id,
            "al": details.album})
        await s.commit()


class LibraryCoverArt(Worker):
    """See the module docstring."""

    name = "library_cover_art"
    enabled_setting = None   # self-gated per tick (applies without a restart)
    interval_setting = "library_cover_art_interval_sec"
    stub_suppressed = True
    requires_online = True

    def __init__(self, *, client: Any = None) -> None:
        self._client = client
        self._after = 0
        self._paused_until: float | None = None
        self.state = "idle"

    def _mb(self) -> Any:
        from domovoi.clients.musicbrainz import get_musicbrainz_client

        return self._client or get_musicbrainz_client()

    async def tick(self) -> dict[str, int]:
        from domovoi.clients.musicbrainz import (
            MusicBrainzRateLimited,
            MusicBrainzRejected,
            MusicBrainzUnavailable,
            is_mbid,
        )
        from domovoi.db.session import session_scope

        result = {"asked": 0, "written": 0, "cached": 0, "no_art": 0, "skipped": 0}
        if not allowed():
            self.state = "off"
            return result
        now = time.monotonic()
        if self._paused_until is not None and now < self._paused_until:
            return result
        self._paused_until = None
        async with session_scope() as s:
            rows = (await s.execute(_DUE_SQL, {
                "retry": NO_ART_RETRY_DAYS, "after": self._after, "n": BATCH_SIZE,
            })).all()
        if not rows:
            self._after = 0
            self.state = "done"
            return result
        self.state = "running"
        written_paths: list[Path] = []
        for row in rows:
            if not allowed():
                break
            # The cursor moves past a row once it is decided; a pause leaves
            # it, so the row is the first one asked after the pause.
            self._after = int(row.id) - 1
            release, group = row.musicbrainz_release_id, row.musicbrainz_release_group_id
            if not release and not group and is_mbid(row.musicbrainz_recording_id):
                try:
                    details = await self._mb().recording_details(row.musicbrainz_recording_id)
                except (MusicBrainzRateLimited, MusicBrainzUnavailable):
                    self._paused_until = time.monotonic() + PAUSE_AFTER_ERROR_SEC
                    break
                except MusicBrainzRejected:
                    details = None
                if details is not None and (details.release_id or details.release_group_id):
                    await _store_release(int(row.id), details)
                    release, group = details.release_id, details.release_group_id
            if not release and not group:
                await _record(int(row.id), {"state": "no_art", "note": "the album isn't known"},
                              None, None)
                result["no_art"] += 1
                self._after = int(row.id)
                continue
            result["asked"] += 1
            try:
                found = await fetch_front(release, group)
            except ArchiveUnavailable as e:
                log.info("cover art: the archive didn't answer (%s); trying again later", e)
                self._paused_until = time.monotonic() + PAUSE_AFTER_ERROR_SEC
                break
            if found is None:
                await _record(int(row.id), {"state": "no_art", "note": "no art available"},
                              None, release or group)
                result["no_art"] += 1
                continue
            image, mime, kind, sid = found
            from domovoi import executors

            outcome = await executors.run("tags", apply_cover, int(row.id), str(row.file_path),
                                          image, mime)
            await _record(int(row.id), outcome, kind, sid)
            result[outcome["state"] if outcome["state"] in result else "skipped"] += 1
            self._after = int(row.id)
            if outcome["state"] == "written":
                written_paths.append(Path(row.file_path))
        if written_paths:
            try:
                from domovoi.mpd_library_sync import update_rooms_for

                await update_rooms_for(written_paths, reason="cover art")
            except Exception as e:  # noqa: BLE001
                log.debug("cover art: couldn't tell the rooms: %s", e)
            log.info("cover art: %s", result)
        return result


__all__ = ["LibraryCoverArt", "apply_cover", "candidate_urls", "fetch_front"]
