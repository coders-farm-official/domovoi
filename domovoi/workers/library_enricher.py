"""Two-stage acoustic-fingerprint enrichment for ``library_tracks``.

The indexer (``library_indexer.py``) populates ``library_tracks`` from
ID3 tags + filename parsing — that gets us 88% of the user's library
with both title and artist, but the rest carry whatever sloppy
filename-parse output we could derive (or NULL artist for files that
don't match "Artist - Title.mp3"). The enricher fills that gap by
identifying each file acoustically and writing back the canonical
metadata.

Stage 1 — **AcoustID** via Chromaprint:
    1. ``fpcalc`` (Chromaprint binary) generates a compact fingerprint
       from the audio.
    2. ``pyacoustid.match`` queries AcoustID's free public API, which
       maps fingerprints to MusicBrainz recording IDs.
    3. Score threshold (``library_enricher_acoustid_min_score``,
       default 0.7) gates "we trust this match."
    4. We get title + artist + MB recording ID; album is queried via
       MusicBrainz separately if needed (deferred — the existing
       MusicBrainz enrichment hook in provider download pipelines has the same
       problem, treat it as a follow-up).

Stage 2 — **shazamio** fallback:
    - Used when AcoustID misses (no fingerprint match in catalog),
      when ``ACOUSTID_API_KEY`` is empty, or when ``fpcalc`` isn't on
      PATH.
    - Sends raw audio to Shazam's actual API. Catalog is much bigger
      than AcoustID's; catches mainstream pop / hip-hop AcoustID lacks.
    - No API key needed.

The enricher rate-limits at one request per
``library_enricher_delay_sec`` (default 1 s) so a 764-track first run
takes ~13 minutes. Re-runs only process tracks where ``enriched_at IS
NULL`` — the enrichment timestamp marker — so subsequent passes are no-ops
unless new files appeared. Both success and "no match" stamp
``enriched_at`` so failed lookups don't get retried forever; users
who want to retry no-match tracks can ``UPDATE library_tracks SET
enriched_at = NULL WHERE musicbrainz_recording_id IS NULL``.

Network-required. The connectivity probe gates the startup hook so we
don't spam the APIs with errors when offline.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope

log = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    musicbrainz_recording_id: str | None = None
    source: str = ""  # "acoustid" or "shazam"


async def _enrich_via_acoustid(file_path: Path) -> EnrichmentResult | None:
    """Run Chromaprint via fpcalc, query AcoustID, return the top
    match if it clears the confidence threshold. Returns None on:
    missing API key, missing fpcalc binary, no match, or sub-threshold
    score. Never raises — failures are logged at DEBUG and treated as
    "AcoustID can't help here."
    """
    api_key = settings.acoustid_api_key.strip()
    if not api_key:
        return None
    try:
        import acoustid
    except ImportError:
        log.debug("pyacoustid not installed; skipping AcoustID layer")
        return None

    # acoustid.match is sync (shells out to fpcalc, hits the API),
    # so run in the thread pool to keep the event loop responsive.
    try:
        results = await asyncio.to_thread(
            lambda: list(acoustid.match(api_key, str(file_path))),
        )
    except acoustid.NoBackendError:
        log.warning(
            "AcoustID enrichment: `fpcalc` (Chromaprint binary) not "
            "found on PATH. Install Chromaprint from "
            "https://acoustid.org/chromaprint and retry. Falling back "
            "to Shazam for this run."
        )
        return None
    except acoustid.FingerprintGenerationError as e:
        log.debug("fpcalc failed for %s: %s", file_path, e)
        return None
    except acoustid.WebServiceError as e:
        log.debug("AcoustID web error for %s: %s", file_path, e)
        return None
    except Exception as e:
        log.debug("AcoustID unexpected error for %s: %s", file_path, e)
        return None

    if not results:
        return None
    # acoustid.match yields tuples of (score, recording_id, title, artist)
    score, recording_id, title, artist = results[0]
    if score is None or score < settings.library_enricher_acoustid_min_score:
        log.debug(
            "AcoustID match for %s below threshold (score=%s)",
            file_path.name, score,
        )
        return None
    return EnrichmentResult(
        title=title or None,
        artist=artist or None,
        musicbrainz_recording_id=recording_id or None,
        source="acoustid",
    )


async def _enrich_via_shazam(file_path: Path) -> EnrichmentResult | None:
    """Send the file to Shazam's API via shazamio. Returns the top
    match if any. shazamio handles audio loading + chunking internally.
    """
    try:
        from shazamio import Shazam
    except ImportError:
        log.debug("shazamio not installed; skipping Shazam layer")
        return None
    try:
        shazam = Shazam()
        out = await shazam.recognize(str(file_path))
    except Exception as e:
        log.debug("Shazam query failed for %s: %s", file_path, e)
        return None

    track = (out or {}).get("track") if isinstance(out, dict) else None
    if not track:
        return None
    title = track.get("title")
    # Shazam stores artist as "subtitle" at the top level.
    artist = track.get("subtitle")
    # Album lives inside `sections[].metadata` — best-effort dig.
    album: str | None = None
    for section in track.get("sections", []):
        for entry in section.get("metadata", []) if isinstance(section, dict) else []:
            if isinstance(entry, dict) and entry.get("title", "").lower() == "album":
                album = entry.get("text")
                break
        if album:
            break
    return EnrichmentResult(
        title=title or None,
        artist=artist or None,
        album=album or None,
        source="shazam",
    )


async def _enrich_one(track_id: int, file_path: Path) -> EnrichmentResult | None:
    """Try AcoustID, then Shazam, then give up. None means "no
    identifier could place this track" — caller should still stamp
    enriched_at to avoid re-trying."""
    result = await _enrich_via_acoustid(file_path)
    if result is not None:
        return result
    return await _enrich_via_shazam(file_path)


async def enrich_library() -> dict[str, int]:
    """Walk ``library_tracks`` where ``enriched_at IS NULL``, identify
    each track, UPDATE with canonical metadata. Returns counts:
    ``{"scanned", "matched", "no_match", "errors", "skipped_missing_file"}``.

    Uses ``COALESCE(:new, existing)`` on every field so the indexer's
    mutagen/filename data is preserved when the API returns NULL for
    that field. ``enriched_at`` is set unconditionally after each
    attempt so no-match tracks don't get retried on every restart.
    """
    if not settings.library_enricher_enabled:
        log.info("library enricher: disabled by config")
        return {"scanned": 0, "matched": 0, "no_match": 0, "errors": 0, "skipped_missing_file": 0}

    matched = no_match = errors = skipped_missing = 0
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, file_path FROM library_tracks "
                    "WHERE enriched_at IS NULL "
                    "ORDER BY id"
                )
            )
        ).all()
        scanned = len(rows)
        if scanned == 0:
            log.info("library enricher: nothing to enrich (all tracks already processed)")
            return {"scanned": 0, "matched": 0, "no_match": 0, "errors": 0, "skipped_missing_file": 0}

        log.info("library enricher: %d unenriched tracks; starting sweep", scanned)
        for row in rows:
            file_path = Path(row.file_path)
            if not file_path.exists():
                # Stale row pointing at a deleted/moved file. Stamp
                # enriched_at so we don't re-evaluate next pass; a
                # cleanup worker can harvest these later.
                skipped_missing += 1
                await session.execute(
                    text("UPDATE library_tracks SET enriched_at = NOW() WHERE id = :id"),
                    {"id": row.id},
                )
                continue

            try:
                result = await _enrich_one(row.id, file_path)
            except Exception as e:
                errors += 1
                log.warning("enricher unexpected failure for %s: %s", file_path.name, e)
                result = None

            if result is not None:
                await session.execute(
                    text(
                        """
                        UPDATE library_tracks
                        SET title = COALESCE(:t, title),
                            artist = COALESCE(:a, artist),
                            album = COALESCE(:al, album),
                            musicbrainz_recording_id = COALESCE(:mb, musicbrainz_recording_id),
                            enriched_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": row.id,
                        "t": result.title,
                        "a": result.artist,
                        "al": result.album,
                        "mb": result.musicbrainz_recording_id,
                    },
                )
                matched += 1
                log.info(
                    "enriched [%s]: %r by %r → %r by %r (mb=%s)",
                    result.source,
                    file_path.name[:60], None,
                    result.title, result.artist,
                    result.musicbrainz_recording_id or "—",
                )
            else:
                no_match += 1
                await session.execute(
                    text("UPDATE library_tracks SET enriched_at = NOW() WHERE id = :id"),
                    {"id": row.id},
                )

            # Commit each row so a long sweep can be safely interrupted
            # without losing progress.
            await session.commit()

            # Rate limit. Skip the wait after the last row.
            if row is not rows[-1]:
                await asyncio.sleep(settings.library_enricher_delay_sec)

        await session.commit()

    log.info(
        "library enricher: scanned=%d matched=%d no_match=%d errors=%d missing=%d",
        scanned, matched, no_match, errors, skipped_missing,
    )
    return {
        "scanned": scanned,
        "matched": matched,
        "no_match": no_match,
        "errors": errors,
        "skipped_missing_file": skipped_missing,
    }
