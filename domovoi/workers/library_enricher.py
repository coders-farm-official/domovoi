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
       maps fingerprints to MusicBrainz recording IDs. It needs an
       APPLICATION key (https://acoustid.org/new-application); a user
       key is refused.
    3. Score threshold (``library_enricher_acoustid_min_score``,
       default 0.7) gates "we trust this match."
    4. We get title + artist + MB recording ID; album is queried via
       MusicBrainz separately if needed (deferred — the existing
       MusicBrainz enrichment hook in provider download pipelines has the same
       problem, treat it as a follow-up).

Stage 2 — **shazamio** fallback:
    - Used when AcoustID has no match, has no key, or can't fingerprint
      the file.
    - Sends raw audio to Shazam's actual API. Catalog is much bigger
      than AcoustID's; catches mainstream pop / hip-hop AcoustID lacks.
    - No API key needed, but shazamio is an optional extra.

The enricher rate-limits at one request per
``library_enricher_delay_sec`` (default 1 s) so a 764-track first run
takes ~13 minutes. Re-runs only process tracks where ``enriched_at IS
NULL`` — the enrichment timestamp marker.

**A track is stamped done only when a provider actually answered**
(fix B3). Each stamp records what was concluded in
``library_tracks.enrich_outcome`` (Flyway V020):

* ``matched``      — a provider identified it.
* ``no_match``     — a provider answered and nothing scored (AcoustID
                     answered with nothing at or above the minimum score,
                     or has no key, AND Shazam answered with no track, or
                     isn't installed, AND neither raised).
* ``missing_file`` — the file is gone.
* ``manual``       — a person edited the tags in the dashboard
                     (``web/backend/api/music.py`` ``patch_track``).
* ``recheck``      — requeued by the one-off recovery below.

A network error, a refused key, the internet being turned off, or no
provider configured at all is NOT "no match": the row is left exactly as
it was, so the next sweep tries again. A refused AcoustID key is treated
like no key for the rest of the sweep (Shazam's answer, if installed,
decides). After :data:`MAX_CONSECUTIVE_ERRORS` lookups in a row that NO
provider answered, the sweep stops.

**The one-off recovery.** Before V020 every attempt stamped
``enriched_at``, whether or not anything answered — a box with no
AcoustID key and no shazamio stamped its whole library "no match" without
sending a request (the Beelink: 5,232 rows, 0 MusicBrainz ids). V020
marks every stamped row that carries a MusicBrainz recording id as
``matched`` (an AcoustID match always wrote one). Any other stamped row
still has a NULL outcome. A sweep looks at those rows too, and once a
provider has actually answered for some file (a match or a genuine
no-match), :func:`requeue_legacy` puts the rest back in the queue once,
with outcome ``recheck``. Until then — no key, a refused key, a provider
that can't run here, the line down — they stay exactly as they were.
A match for a legacy or ``recheck`` row fills only the fields that are
EMPTY — a legacy hand correction can't be told apart from a legacy
no-provider stamp, so its tags must never be overwritten. So the
recovery adds MusicBrainz ids and fills empty tags; it does not correct
a title or artist that was guessed from a file name.

Two limits of the recovery: a legacy SHAZAM match never wrote a
MusicBrainz id, so it is requeued and looked up again like the rest (its
tags are kept; if no provider recognises it this time it is recorded as
``no_match``). And the Shazam add-on is used only after a throwaway
interpreter has imported it once: a build that crashes at import is
skipped with a log line instead of taking the core down.

Network-required. The connectivity probe gates the startup hook (and
this module checks it again), so we don't spam the APIs with errors when
offline; under ``INTERNET_ACCESS=never`` nothing runs at all.
"""

from __future__ import annotations

import asyncio
import enum
import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi import connectivity, egress
from domovoi.config import settings
from domovoi.db.session import session_scope

log = logging.getLogger(__name__)

# A run of provider errors this long means the provider (or the line to it)
# is down, or the key is refused: stop the sweep instead of walking the
# whole library failing. Nothing is stamped, so the next sweep resumes.
MAX_CONSECUTIVE_ERRORS = 5

OUTCOME_MATCHED = "matched"
OUTCOME_NO_MATCH = "no_match"
OUTCOME_MISSING_FILE = "missing_file"
OUTCOME_MANUAL = "manual"
OUTCOME_RECHECK = "recheck"

# Rows stamped before V020 with nothing to show a provider ever answered:
# no outcome (V020 marked every stamped row WITH a MusicBrainz id
# 'matched') and no MusicBrainz recording id. The recovery requeues
# exactly these, and the voice trigger counts them as waiting.
LEGACY_UNANSWERED_WHERE = (
    "enriched_at IS NOT NULL AND enrich_outcome IS NULL "
    "AND musicbrainz_recording_id IS NULL"
)
# Everything the next sweep with a provider will look at.
WAITING_WHERE = f"enriched_at IS NULL OR ({LEGACY_UNANSWERED_WHERE})"

ACOUSTID_KEY_HINT = (
    "AcoustID refused the key — it must be an APPLICATION key from "
    "https://acoustid.org/new-application"
)


@dataclass
class EnrichmentResult:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    musicbrainz_recording_id: str | None = None
    source: str = ""  # "acoustid" or "shazam"


class Verdict(enum.Enum):
    """What one provider said about one file."""

    MATCH = "match"          # identified it
    NO_MATCH = "no_match"    # answered: nothing (at or above the score)
    NOT_ASKED = "not_asked"  # no key / not installed / can't fingerprint this file
    ERROR = "error"          # didn't answer: network, service, refused key, internet off


@dataclass(frozen=True)
class Lookup:
    verdict: Verdict
    result: EnrichmentResult | None = None
    detail: str = ""
    # ERROR only: the provider or the line to it failed (counts toward
    # MAX_CONSECUTIVE_ERRORS). False for a failure about this one file.
    transient: bool = False
    # NOT_ASKED only: the provider can't run on this box at all (its
    # package or fpcalc is missing), so the sweep stops asking it.
    unusable: bool = False


def provider_available() -> bool:
    """An AcoustID key is set, or the Shazam add-on (shazamio) is
    installed (and not already found to crash at import). Without either,
    no track can be identified, so nothing is attempted and nothing is
    stamped."""
    return bool((settings.acoustid_api_key or "").strip()) or (
        importlib.util.find_spec("shazamio") is not None and _shazam_import_ok is not False
    )


# One sweep at a time per process (the boot sweep, a voice request and
# the dashboard button would otherwise walk the same rows twice). A plain
# flag: everything here runs on the one event loop.
_sweep_running = False


def enrich_running() -> bool:
    """A sweep is in progress in this process."""
    return _sweep_running


_key_hint_logged = False


def _is_key_refusal(e: Exception) -> bool:
    """AcoustID refused the key itself (error 4 "invalid API key"; 17
    "unknown application") — not the line, not this file."""
    code = getattr(e, "code", None)
    msg = (getattr(e, "message", None) or str(e) or "").lower()
    return code in (4, 17) or "api key" in msg or "apikey" in msg or "unknown application" in msg


def _note_acoustid_error(e: Exception) -> None:
    """Log the application-key hint once per process when AcoustID
    refuses the key."""
    global _key_hint_logged
    if _is_key_refusal(e) and not _key_hint_logged:
        _key_hint_logged = True
        log.warning("%s (AcoustID said: %s)", ACOUSTID_KEY_HINT, getattr(e, "message", e))


# Whether ``import shazamio`` survives in a fresh interpreter: None until
# probed. A source-built shazamio-core segfaults at import on some
# platforms (CPython 3.14 on Ubuntu 26.04, pyproject.toml), which would
# take the whole core down — and, since the recovery requeues a library,
# do it again on every boot. The probe costs one short subprocess, once
# per process, before the first in-process import.
_shazam_import_ok: bool | None = None


def _probe_shazamio_import() -> bool:
    import subprocess
    import sys

    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import shazamio"],
            capture_output=True,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("library enricher: couldn't check the Shazam add-on (%s); not using it", e)
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        log.warning(
            "library enricher: the Shazam add-on (shazamio) is installed but "
            "importing it fails (exit %s%s); not using it. Reinstall it, or "
            "remove it to silence this.", proc.returncode,
            f": {tail[-1][:200]}" if tail else "",
        )
        return False
    return True


async def shazam_importable() -> bool:
    """Whether the Shazam layer can be imported in this process without
    killing it. True without a probe once it is already imported (a test's
    stand-in, or an earlier sweep); False when it isn't installed."""
    global _shazam_import_ok
    if sys.modules.get("shazamio") is not None:
        return True
    if importlib.util.find_spec("shazamio") is None:
        return False
    if _shazam_import_ok is None:
        _shazam_import_ok = await asyncio.to_thread(_probe_shazamio_import)
    return _shazam_import_ok


def _is_network_error(e: BaseException) -> bool:
    """A failure of the line or the service, rather than of one file."""
    if isinstance(e, (OSError, TimeoutError, asyncio.TimeoutError, egress.InternetTurnedOff)):
        return True
    module = (type(e).__module__ or "").split(".", 1)[0]
    return module in ("aiohttp", "httpx", "requests", "urllib3")


async def _enrich_via_acoustid(file_path: Path) -> Lookup:
    """Run Chromaprint via fpcalc, query AcoustID, return what it said.
    Never raises — the verdict carries the outcome."""
    api_key = (settings.acoustid_api_key or "").strip()
    if not api_key:
        return Lookup(Verdict.NOT_ASKED, detail="no AcoustID key")
    try:
        import acoustid
    except ImportError:
        log.warning("AcoustID enrichment: pyacoustid isn't installed; skipping AcoustID")
        return Lookup(Verdict.NOT_ASKED, detail="pyacoustid not installed", unusable=True)
    try:
        egress.require_internet("AcoustID song lookup")
    except egress.InternetTurnedOff as e:
        return Lookup(Verdict.ERROR, detail=str(e), transient=True)

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
            "https://acoustid.org/chromaprint and retry. Using Shazam "
            "only for this run, if it is installed."
        )
        return Lookup(Verdict.NOT_ASKED, detail="fpcalc not found", unusable=True)
    except acoustid.FingerprintGenerationError as e:
        log.debug("fpcalc failed for %s: %s", file_path, e)
        return Lookup(Verdict.NOT_ASKED, detail=f"couldn't fingerprint: {e}")
    except acoustid.WebServiceError as e:
        _note_acoustid_error(e)
        if _is_key_refusal(e):
            # A refused key is like no key: AcoustID can't answer for any
            # file this sweep, so it isn't asked again and Shazam's answer
            # (if installed) decides. Nothing is stamped for it.
            return Lookup(
                Verdict.NOT_ASKED, detail="AcoustID refused the key", unusable=True,
            )
        # pyacoustid wraps connection failures in WebServiceError too.
        log.debug("AcoustID web error for %s: %s", file_path, e)
        return Lookup(Verdict.ERROR, detail=f"AcoustID: {e}", transient=True)
    except Exception as e:
        log.debug("AcoustID unexpected error for %s: %s", file_path, e)
        return Lookup(Verdict.ERROR, detail=f"AcoustID: {e}", transient=True)

    if not results:
        return Lookup(Verdict.NO_MATCH, detail="AcoustID: no result")
    # acoustid.match yields tuples of (score, recording_id, title, artist)
    score, recording_id, title, artist = results[0]
    if score is None or score < settings.library_enricher_acoustid_min_score:
        log.debug(
            "AcoustID match for %s below threshold (score=%s)",
            file_path.name, score,
        )
        return Lookup(Verdict.NO_MATCH, detail=f"AcoustID: score {score}")
    return Lookup(
        Verdict.MATCH,
        result=EnrichmentResult(
            title=title or None,
            artist=artist or None,
            musicbrainz_recording_id=recording_id or None,
            source="acoustid",
        ),
    )


async def _enrich_via_shazam(file_path: Path) -> Lookup:
    """Send the file to Shazam's API via shazamio and return what it
    said. shazamio handles audio loading + chunking internally."""
    if not await shazam_importable():
        return Lookup(
            Verdict.NOT_ASKED, detail="shazamio not installed or broken", unusable=True,
        )
    try:
        from shazamio import Shazam
    except ImportError:
        log.debug("shazamio not installed; skipping Shazam layer")
        return Lookup(Verdict.NOT_ASKED, detail="shazamio not installed", unusable=True)
    try:
        egress.require_internet("Shazam song lookup")
    except egress.InternetTurnedOff as e:
        return Lookup(Verdict.ERROR, detail=str(e), transient=True)
    try:
        shazam = Shazam()
        out = await shazam.recognize(str(file_path))
    except Exception as e:
        log.debug("Shazam query failed for %s: %s", file_path, e)
        return Lookup(Verdict.ERROR, detail=f"Shazam: {e}", transient=_is_network_error(e))

    track = (out or {}).get("track") if isinstance(out, dict) else None
    if not track:
        return Lookup(Verdict.NO_MATCH, detail="Shazam: no track")
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
    return Lookup(
        Verdict.MATCH,
        result=EnrichmentResult(
            title=title or None,
            artist=artist or None,
            album=album or None,
            source="shazam",
        ),
    )


@dataclass
class _Providers:
    """Which providers this sweep still asks (one found unusable on the
    first file isn't asked again)."""

    acoustid: bool = True
    shazam: bool = True

    @property
    def any(self) -> bool:
        return self.acoustid or self.shazam


_NOT_ASKED = Lookup(Verdict.NOT_ASKED, detail="not asked")


async def _enrich_one(file_path: Path, providers: _Providers | None = None) -> Lookup:
    """AcoustID, then Shazam. A match from either wins. Otherwise an
    error from either means the track isn't decided (it is retried
    later); a no-match from one with no error from the other is a
    genuine no-match; nothing answered at all is NOT_ASKED."""
    providers = providers if providers is not None else _Providers()
    ac = await _enrich_via_acoustid(file_path) if providers.acoustid else _NOT_ASKED
    if ac.unusable:
        providers.acoustid = False
    if ac.verdict is Verdict.MATCH:
        return ac
    sh = await _enrich_via_shazam(file_path) if providers.shazam else _NOT_ASKED
    if sh.unusable:
        providers.shazam = False
    if sh.verdict is Verdict.MATCH:
        return sh
    errors = [x for x in (ac, sh) if x.verdict is Verdict.ERROR]
    if errors:
        # Not decided (one provider couldn't answer), so not stamped. It
        # counts toward stopping the sweep only when NO provider answered:
        # one provider down while the other keeps answering is not "the
        # line is down".
        answered = any(x.verdict is Verdict.NO_MATCH for x in (ac, sh))
        return Lookup(
            Verdict.ERROR,
            detail="; ".join(x.detail for x in errors),
            transient=any(x.transient for x in errors) and not answered,
        )
    if Verdict.NO_MATCH in (ac.verdict, sh.verdict):
        return Lookup(Verdict.NO_MATCH, detail="; ".join(
            x.detail for x in (ac, sh) if x.verdict is Verdict.NO_MATCH
        ))
    return Lookup(Verdict.NOT_ASKED, detail="; ".join((ac.detail, sh.detail)))


def _counts(**over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "scanned": 0,
        "matched": 0,
        "no_match": 0,
        "errors": 0,
        "skipped_missing_file": 0,
        "requeued_legacy": 0,
        "aborted": False,
        "skipped": "",
    }
    out.update(over)
    return out


async def requeue_legacy(session) -> int:
    """The one-off B3 recovery: put rows stamped before V020 with no
    sign a provider ever answered (no outcome, no MusicBrainz id) back in
    the queue, as ``recheck``. Every later stamp writes an outcome, so a
    second call finds nothing. Returns how many rows it requeued. The
    caller commits."""
    result = await session.execute(
        text(
            "UPDATE library_tracks SET enriched_at = NULL, enrich_outcome = :recheck "
            f"WHERE {LEGACY_UNANSWERED_WHERE}"
        ),
        {"recheck": OUTCOME_RECHECK},
    )
    return int(result.rowcount or 0)


async def waiting_count(session) -> int:
    """Tracks the next sweep with a provider would look at: the
    unenriched ones plus the legacy rows the recovery will requeue."""
    return int((await session.execute(
        text(f"SELECT count(*) FROM library_tracks WHERE {WAITING_WHERE}")
    )).scalar_one() or 0)


_MATCH_SQL = """
    UPDATE library_tracks
    SET title = COALESCE(:t, title),
        artist = COALESCE(:a, artist),
        album = COALESCE(:al, album),
        musicbrainz_recording_id = COALESCE(:mb, musicbrainz_recording_id),
        enriched_at = NOW(),
        enrich_outcome = :matched
    WHERE id = :id
"""
# A requeued legacy row: fill only what is empty, so a hand correction
# made before V020 (indistinguishable from a no-provider stamp) survives.
_FILL_ONLY_SQL = """
    UPDATE library_tracks
    SET title = COALESCE(NULLIF(title, ''), :t, title),
        artist = COALESCE(NULLIF(artist, ''), :a, artist),
        album = COALESCE(NULLIF(album, ''), :al, album),
        musicbrainz_recording_id = COALESCE(musicbrainz_recording_id, :mb),
        enriched_at = NOW(),
        enrich_outcome = :matched
    WHERE id = :id
"""
_STAMP_SQL = "UPDATE library_tracks SET enriched_at = NOW(), enrich_outcome = :outcome WHERE id = :id"


async def enrich_library() -> dict[str, Any]:
    """Walk ``library_tracks`` where ``enriched_at IS NULL``, identify
    each track, UPDATE with canonical metadata. Returns counts:
    ``{"scanned", "matched", "no_match", "errors",
    "skipped_missing_file", "requeued_legacy", "aborted", "skipped"}``,
    where ``skipped`` is "" for a sweep that ran, else why it didn't:
    "disabled", "internet_off", "offline", "no_provider" or "running"
    (another sweep is in progress) — and then no row was touched.

    A match uses ``COALESCE(:new, existing)`` on every field so the
    indexer's data is kept where the API returns NULL (fill-only for a
    ``recheck`` row). Only a provider's answer stamps ``enriched_at``.
    """
    global _sweep_running
    if not settings.library_enricher_enabled:
        log.info("library enricher: disabled by config")
        return _counts(skipped="disabled")
    if egress.internet_turned_off():
        log.info("library enricher: internet access is turned off; not running")
        return _counts(skipped="internet_off")
    probe = connectivity.current_probe()
    if probe is not None and not probe.online:
        log.info("library enricher: offline; not running")
        return _counts(skipped="offline")
    if not provider_available():
        try:
            async with session_scope() as session:
                waiting = await waiting_count(session)
        except Exception as e:  # the log line is all this count is for
            log.debug("library enricher: couldn't count waiting tracks: %s", e)
            waiting = 0
        log.info(
            "library enricher: no AcoustID key and no Shazam add-on; "
            "%d tracks wait for one", waiting,
        )
        return _counts(skipped="no_provider")
    if _sweep_running:
        log.info("library enricher: a sweep is already running")
        return _counts(skipped="running")

    _sweep_running = True
    try:
        return await _sweep()
    finally:
        _sweep_running = False


async def _sweep() -> dict[str, Any]:
    counts = _counts()
    providers = _Providers()
    consecutive_errors = 0
    # The legacy rows (stamped before V020 with no sign a provider ever
    # answered) are looked at in this sweep, but put back in the queue
    # (requeue_legacy) only once a provider has actually answered for some
    # file: a refused key, a provider that can't run here or a line that
    # is down leaves every one of them exactly as it was.
    requeued_done = False
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, file_path, enrich_outcome, "
                    f"({LEGACY_UNANSWERED_WHERE}) AS legacy "
                    "FROM library_tracks "
                    f"WHERE {WAITING_WHERE} "
                    "ORDER BY id"
                )
            )
        ).all()
        counts["scanned"] = len(rows)
        if not rows:
            log.info("library enricher: nothing to enrich (all tracks already processed)")
            return counts

        log.info("library enricher: %d unenriched tracks; starting sweep", len(rows))

        async def _requeue_once() -> None:
            nonlocal requeued_done
            if requeued_done:
                return
            requeued_done = True
            # Includes the row that just got the first answer when it is a
            # legacy row (it is stamped right after this).
            requeued = await requeue_legacy(session)
            counts["requeued_legacy"] = requeued
            if requeued:
                log.info(
                    "library enricher: requeued %d tracks stamped before song "
                    "recognition could answer (one-off recovery; their tags are "
                    "only filled where empty)", requeued,
                )

        for row in rows:
            file_path = Path(row.file_path)
            if not file_path.exists():
                # Stale row pointing at a deleted/moved file. Stamp it so
                # we don't re-evaluate next pass; a cleanup worker can
                # harvest these later.
                counts["skipped_missing_file"] += 1
                await session.execute(
                    text(_STAMP_SQL), {"id": row.id, "outcome": OUTCOME_MISSING_FILE},
                )
                await session.commit()
                continue

            try:
                lookup = await _enrich_one(file_path, providers)
            except Exception as e:  # _enrich_one doesn't raise; belt and braces
                log.warning("enricher unexpected failure for %s: %s", file_path.name, e)
                lookup = Lookup(Verdict.ERROR, detail=str(e), transient=True)

            if lookup.verdict is Verdict.MATCH and lookup.result is not None:
                result = lookup.result
                fill_only = bool(row.legacy) or row.enrich_outcome == OUTCOME_RECHECK
                await _requeue_once()
                await session.execute(
                    text(_FILL_ONLY_SQL if fill_only else _MATCH_SQL),
                    {
                        "id": row.id,
                        "t": result.title,
                        "a": result.artist,
                        "al": result.album,
                        "mb": result.musicbrainz_recording_id,
                        "matched": OUTCOME_MATCHED,
                    },
                )
                counts["matched"] += 1
                consecutive_errors = 0
                log.info(
                    "enriched [%s%s]: %r → %r by %r (mb=%s)",
                    result.source, ", fill-only" if fill_only else "",
                    file_path.name[:60],
                    result.title, result.artist,
                    result.musicbrainz_recording_id or "—",
                )
            elif lookup.verdict is Verdict.NO_MATCH:
                counts["no_match"] += 1
                consecutive_errors = 0
                await _requeue_once()
                await session.execute(
                    text(_STAMP_SQL), {"id": row.id, "outcome": OUTCOME_NO_MATCH},
                )
            else:
                # An error, or nothing could answer for this file: leave
                # the row exactly as it is, so the next sweep retries it.
                counts["errors"] += 1
                if lookup.verdict is Verdict.ERROR and lookup.transient:
                    consecutive_errors += 1
                log.debug("enricher: no answer for %s: %s", file_path.name, lookup.detail)

            # Commit each row so a long sweep can be safely interrupted
            # without losing progress.
            await session.commit()

            if not providers.any:
                counts["aborted"] = True
                log.warning(
                    "library enricher: no song-recognition provider can run on "
                    "this box (%s); stopping, nothing stamped", lookup.detail,
                )
                break
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                counts["aborted"] = True
                log.warning(
                    "library enricher: %d lookups failed in a row (last: %s); "
                    "stopping this sweep, nothing stamped — it resumes next time",
                    consecutive_errors, lookup.detail,
                )
                break

            # Rate limit. Skip the wait after the last row.
            if row is not rows[-1]:
                await asyncio.sleep(settings.library_enricher_delay_sec)

        await session.commit()

    log.info(
        "library enricher: scanned=%d matched=%d no_match=%d errors=%d "
        "missing=%d requeued_legacy=%d aborted=%s",
        counts["scanned"], counts["matched"], counts["no_match"], counts["errors"],
        counts["skipped_missing_file"], counts["requeued_legacy"], counts["aborted"],
    )
    return counts
