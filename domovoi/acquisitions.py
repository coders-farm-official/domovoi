"""Media acquisition queue — core service (design §4.8, locked 6).

The four-producer fulcrum: voice handlers, the web add-by-query/url
endpoints, chat tools, and plugins all enqueue *structured* requests to
obtain media into the local library — ``{kind: query|url, text,
metadata, ...}`` — never a provider wire format. Registered
**acquisition fulfillers** (media-provider plugins) claim rows with
``SELECT ... FOR UPDATE SKIP LOCKED`` and complete or fail them.

Graceful absence is a supported state, not an error: enqueue always
succeeds; with no matching fulfiller enabled, rows sit ``pending`` and
the producer surfaces :meth:`AcquisitionService.friendly_absence_message`.
Installing a fulfiller later drains the backlog — deliberately a
feature (detections captured offline get fetched once a provider is
installed).

Dedup, three layers (locked 6 — no substring-LIKE dedup):
  1. library fuzzy match (core pg_trgm) at enqueue;
  2. live-queue identity via the ``dedup_key`` partial-unique index
     (``INSERT ... ON CONFLICT DO NOTHING``) — the key is
     provider-namespaced, e.g. ``"<provider>:video:<id>"``;
  3. fulfillers re-dedup post-resolve with their provider knowledge.

Every state change fires ``pg_notify('acquisitions_changed', ...)`` on
the caller's open session (commit-coupled NOTIFY — dossier §7 inv. 8)
and emits ``core.acquisition_*`` on the event bus.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import registered_values
from domovoi.capabilities import (
    ACQUISITION_ABSENCE_MESSAGE,
    CAPABILITIES,
    MEDIA_ACQUISITION_FULFILLER,
)
from domovoi.config import settings
from domovoi.events import EVENTS
from domovoi.handlers.shared.library_dedup import find_fuzzy_library_match

log = logging.getLogger(__name__)

AcquisitionKind = Literal["query", "url"]
_KINDS = ("query", "url")

_ROW_COLUMNS = (
    "id, kind, text, metadata, requested_by, origin_ref, "
    "attach_to_playlist_id, dedup_key, status, claimed_by, claimed_at, "
    "attempts, next_attempt_at, result, error, requested_at, completed_at"
)


@dataclass(frozen=True)
class Acquisition:
    """One ``media_acquisitions`` row, as fulfillers and events see it."""

    id: int
    kind: str
    text: str
    metadata: dict[str, Any]
    requested_by: str
    origin_ref: str | None
    attach_to_playlist_id: int | None
    dedup_key: str | None
    status: str
    claimed_by: str | None
    claimed_at: datetime | None
    attempts: int
    next_attempt_at: datetime | None
    result: dict[str, Any] | None
    error: str | None
    requested_at: datetime | None
    completed_at: datetime | None

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe payload for the ``core.acquisition_*`` events."""
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "metadata": dict(self.metadata),
            "requested_by": self.requested_by,
            "origin_ref": self.origin_ref,
            "attach_to_playlist_id": self.attach_to_playlist_id,
            "dedup_key": self.dedup_key,
            "status": self.status,
            "claimed_by": self.claimed_by,
            "attempts": self.attempts,
            "error": self.error,
        }


def _row_to_acquisition(row: Any) -> Acquisition:
    return Acquisition(
        id=int(row.id),
        kind=row.kind,
        text=row.text,
        metadata=dict(row.metadata or {}),
        requested_by=row.requested_by,
        origin_ref=row.origin_ref,
        attach_to_playlist_id=(
            int(row.attach_to_playlist_id)
            if row.attach_to_playlist_id is not None else None
        ),
        dedup_key=row.dedup_key,
        status=row.status,
        claimed_by=row.claimed_by,
        claimed_at=row.claimed_at,
        attempts=int(row.attempts),
        next_attempt_at=row.next_attempt_at,
        result=dict(row.result) if row.result else None,
        error=row.error,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
    )


@dataclass(frozen=True)
class EnqueueResult:
    """Outcome of :meth:`AcquisitionService.enqueue`.

    ``outcome``:
      * ``"enqueued"``          — a new row was inserted (``acquisition`` set).
      * ``"duplicate"``         — a live row with the same ``dedup_key``
                                   already exists (``duplicate_of_id`` set).
      * ``"already_in_library"`` — the library fuzzy dedup hit
                                   (``library_match`` carries the row's
                                   title/artist).
    ``user_message`` is ready-to-speak voice/UI copy for all three.
    """

    outcome: Literal["enqueued", "duplicate", "already_in_library"]
    user_message: str
    acquisition: Acquisition | None = None
    duplicate_of_id: int | None = None
    library_match: dict[str, str | None] | None = None


@dataclass(frozen=True)
class AcquisitionAvailability:
    fulfillers: list[str]
    can_fulfill_query: bool
    can_fulfill_url: bool


@dataclass(frozen=True)
class _Fulfiller:
    """CapabilityRegistry impl record for a registered fulfiller."""

    slug: str
    kinds: frozenset[str]
    url_matcher: Callable[[str], bool] | None = None


async def _notify(session: AsyncSession, reason: str) -> None:
    await session.execute(
        text("SELECT pg_notify('acquisitions_changed', :reason)"),
        {"reason": reason},
    )


class AcquisitionService:
    """See module docstring. A process singleton (:data:`ACQUISITIONS`);
    fulfiller registrations live in the shared
    :data:`domovoi.capabilities.CAPABILITIES` registry so presence checks,
    ``GET /v1/capabilities`` introspection, and plugin teardown
    (``unregister_all_for``) all see one source of truth."""

    # ── Fulfiller registration (through the capability registry) ───────
    def register_fulfiller(
        self,
        slug: str,
        *,
        kinds: set[str],
        url_matcher: Callable[[str], bool] | None = None,
    ) -> None:
        bad = set(kinds) - set(_KINDS)
        if bad:
            raise ValueError(f"register_fulfiller: unknown kinds {sorted(bad)}")
        CAPABILITIES.register(
            MEDIA_ACQUISITION_FULFILLER,
            _Fulfiller(slug=slug, kinds=frozenset(kinds), url_matcher=url_matcher),
            slug=slug,
        )

    def unregister_fulfiller(self, slug: str) -> None:
        CAPABILITIES.unregister(MEDIA_ACQUISITION_FULFILLER, slug=slug)

    def _fulfillers(self) -> list[_Fulfiller]:
        return [
            f for f in CAPABILITIES.get_all(MEDIA_ACQUISITION_FULFILLER)
            if isinstance(f, _Fulfiller)
        ]

    def _fulfiller(self, slug: str) -> _Fulfiller:
        for f in self._fulfillers():
            if f.slug == slug:
                return f
        raise ValueError(
            f"claim_next: {slug!r} is not a registered acquisition fulfiller"
        )

    def availability(self) -> AcquisitionAvailability:
        fulfillers = self._fulfillers()
        return AcquisitionAvailability(
            fulfillers=[f.slug for f in fulfillers],
            can_fulfill_query=any("query" in f.kinds for f in fulfillers),
            can_fulfill_url=any("url" in f.kinds for f in fulfillers),
        )

    def url_allowed_by_fulfillers(self, url: str) -> bool:
        """True when ANY registered url-kind fulfiller's ``url_matcher``
        claims the URL — the server-side allowlist check of the §7.3
        outbound-fetch tier (unauthenticated add-by-url may only target
        hosts an installed provider explicitly recognizes). A fulfiller
        without a matcher is conservative: it never allowlists."""
        for f in self._fulfillers():
            if "url" not in f.kinds or f.url_matcher is None:
                continue
            try:
                if f.url_matcher(url):
                    return True
            except Exception as e:
                log.warning("url_matcher for %s raised: %s", f.slug, e)
        return False

    def friendly_absence_message(self, kind: str) -> str:
        """The canonical graceful-absence voice/UI copy (locked 6)."""
        return ACQUISITION_ABSENCE_MESSAGE

    # ── Producer side ──────────────────────────────────────────────────
    async def enqueue(
        self,
        session: AsyncSession,
        *,
        kind: AcquisitionKind,
        text_: str | None = None,
        metadata: dict[str, Any] | None = None,
        requested_by: str,
        origin_ref: str | None = None,
        attach_to_playlist_id: int | None = None,
        dedup_key: str | None = None,
        skip_library_dedup: bool = False,
        **kwargs: Any,
    ) -> EnqueueResult:
        """Queue a request to obtain media into the library.

        Accepts the request text as ``text=`` (design signature) or
        ``text_=``; ``text`` is a soft keyword because it shadows the
        sqlalchemy import at call sites. ``skip_library_dedup`` lets a
        caller that already ran its own library check (e.g. an exact
        by-URL add) bypass the fuzzy layer.
        """
        if "text" in kwargs:
            text_ = kwargs.pop("text")
        if kwargs:
            raise TypeError(f"enqueue: unexpected kwargs {sorted(kwargs)}")
        if kind not in _KINDS:
            raise ValueError(f"enqueue: kind must be one of {_KINDS}, got {kind!r}")
        if not text_ or not text_.strip():
            raise ValueError("enqueue: text is required")
        text_ = text_.strip()
        meta = dict(metadata or {})

        # Layer 1 — library fuzzy dedup (core pg_trgm). Query-kind requests
        # match on the metadata title/artist hints, falling back to the
        # free text; url-kind requests only when the producer supplied a
        # title hint (a bare URL has nothing to fuzzy-match).
        if not skip_library_dedup:
            title = meta.get("title") or (text_ if kind == "query" else None)
            artist = meta.get("artist")
            if title:
                try:
                    match = await find_fuzzy_library_match(
                        session, title=str(title), artist=artist
                    )
                except Exception as e:  # pragma: no cover — pg_trgm hiccup
                    log.warning("enqueue: library dedup failed: %s", e)
                    match = None
                if match is not None:
                    label = match.get("title") or "that"
                    return EnqueueResult(
                        outcome="already_in_library",
                        user_message=f"{label} is already in your library.",
                        library_match=match,
                    )

        # Layer 2 — live-queue dedup via the partial-unique dedup_key index.
        registered_values.require("acquisition_status", "pending")
        row = (
            await session.execute(
                text(
                    f"""
                    INSERT INTO media_acquisitions
                        (kind, text, metadata, requested_by, origin_ref,
                         attach_to_playlist_id, dedup_key)
                    VALUES
                        (:kind, :text, CAST(:metadata AS JSONB), :requested_by,
                         :origin_ref, :attach_pid, :dedup_key)
                    ON CONFLICT (dedup_key)
                        WHERE dedup_key IS NOT NULL
                          AND status IN ('pending', 'claimed')
                        DO NOTHING
                    RETURNING {_ROW_COLUMNS}
                    """
                ),
                {
                    "kind": kind,
                    "text": text_,
                    "metadata": json.dumps(meta),
                    "requested_by": requested_by,
                    "origin_ref": origin_ref,
                    "attach_pid": attach_to_playlist_id,
                    "dedup_key": dedup_key,
                },
            )
        ).first()

        if row is None:
            # Conflict with a live (pending/claimed) row — duplicate.
            dup = (
                await session.execute(
                    text(
                        "SELECT id FROM media_acquisitions "
                        "WHERE dedup_key = :k AND status IN ('pending', 'claimed') "
                        "ORDER BY id DESC LIMIT 1"
                    ),
                    {"k": dedup_key},
                )
            ).first()
            return EnqueueResult(
                outcome="duplicate",
                user_message="That's already queued for download.",
                duplicate_of_id=int(dup[0]) if dup is not None else None,
            )

        acq = _row_to_acquisition(row)
        await _notify(session, "enqueued")
        EVENTS.emit("core.acquisition_enqueued", acq.snapshot())

        availability = self.availability()
        can = (
            availability.can_fulfill_query
            if kind == "query" else availability.can_fulfill_url
        )
        message = (
            "Queued — I'll fetch it shortly."
            if can else self.friendly_absence_message(kind)
        )
        return EnqueueResult(
            outcome="enqueued", user_message=message, acquisition=acq
        )

    # ── Fulfiller side ─────────────────────────────────────────────────
    async def claim_next(
        self, session: AsyncSession, *, slug: str
    ) -> Acquisition | None:
        """Claim the oldest due pending row this fulfiller matches.

        ``SELECT ... FOR UPDATE SKIP LOCKED`` over pending rows with
        ``kind ∈ kinds``; url-kind rows are additionally filtered through
        the fulfiller's ``url_matcher`` in Python after a candidate fetch
        (unmatched URLs stay pending for a fulfiller that understands
        them). Sets ``status='claimed'``, ``claimed_by``, ``claimed_at``,
        ``attempts += 1``.
        """
        fulfiller = self._fulfiller(slug)
        registered_values.require("acquisition_status", "claimed")
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT {_ROW_COLUMNS}
                    FROM media_acquisitions
                    WHERE status = 'pending'
                      AND next_attempt_at <= NOW()
                      AND kind = ANY(:kinds)
                    ORDER BY requested_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 10
                    """
                ),
                {"kinds": sorted(fulfiller.kinds)},
            )
        ).all()

        chosen: Any = None
        for row in rows:
            if row.kind == "url" and fulfiller.url_matcher is not None:
                try:
                    if not fulfiller.url_matcher(row.text):
                        continue
                except Exception as e:  # noqa: BLE001 — matcher is plugin code
                    log.warning("url_matcher for %s raised: %s", slug, e)
                    continue
            chosen = row
            break
        if chosen is None:
            return None

        claimed = (
            await session.execute(
                text(
                    f"""
                    UPDATE media_acquisitions
                    SET status = 'claimed', claimed_by = :slug,
                        claimed_at = NOW(), attempts = attempts + 1
                    WHERE id = :id
                    RETURNING {_ROW_COLUMNS}
                    """
                ),
                {"slug": slug, "id": int(chosen.id)},
            )
        ).first()
        await _notify(session, "claimed")
        return _row_to_acquisition(claimed)

    async def complete(
        self,
        session: AsyncSession,
        acq_id: int,
        *,
        library_track_id: int,
        file_path: str,
    ) -> None:
        """Mark done + perform the playlist attach (soft ref — skipped
        with a log if the playlist died meanwhile, locked 5)."""
        registered_values.require("acquisition_status", "done")
        row = (
            await session.execute(
                text(
                    f"""
                    UPDATE media_acquisitions
                    SET status = 'done', completed_at = NOW(), error = NULL,
                        result = CAST(:result AS JSONB)
                    WHERE id = :id
                    RETURNING {_ROW_COLUMNS}
                    """
                ),
                {
                    "id": acq_id,
                    "result": json.dumps(
                        {"library_track_id": library_track_id, "file_path": file_path}
                    ),
                },
            )
        ).first()
        if row is None:
            raise ValueError(f"complete: acquisition {acq_id} not found")
        acq = _row_to_acquisition(row)

        if acq.attach_to_playlist_id is not None:
            attached = await self._attach_to_playlist(
                session, acq.attach_to_playlist_id, library_track_id
            )
            if not attached:
                log.info(
                    "acquisition %s: playlist %s vanished before attach; skipping",
                    acq_id, acq.attach_to_playlist_id,
                )

        await _notify(session, "done")
        EVENTS.emit("core.acquisition_completed", acq.snapshot())

    @staticmethod
    async def _attach_to_playlist(
        session: AsyncSession, playlist_id: int, track_id: int
    ) -> bool:
        exists = (
            await session.execute(
                text("SELECT 1 FROM playlists WHERE id = :pid"),
                {"pid": playlist_id},
            )
        ).first()
        if exists is None:
            return False
        await session.execute(
            text(
                """
                INSERT INTO playlist_tracks (playlist_id, track_id, position)
                VALUES (
                    :pid, :tid,
                    COALESCE((SELECT MAX(position) + 1 FROM playlist_tracks
                              WHERE playlist_id = :pid), 0)
                )
                ON CONFLICT (playlist_id, track_id) DO NOTHING
                """
            ),
            {"pid": playlist_id, "tid": track_id},
        )
        await session.execute(
            text("SELECT pg_notify('playlists_changed', 'attached')")
        )
        return True

    async def fail(
        self,
        session: AsyncSession,
        acq_id: int,
        *,
        error: str,
        retry_in: timedelta | None = None,
        unfulfillable: bool = False,
    ) -> None:
        """Record a fulfillment failure.

        ``retry_in`` → back to ``pending`` with ``next_attempt_at``
        pushed out, unless ``attempts`` already reached
        ``settings.acquisition_max_attempts``. ``retry_in=None`` →
        terminal ``failed``; ``unfulfillable=True`` → terminal
        ``unfulfillable`` (e.g. a duplicate discovered post-resolve).
        """
        current = (
            await session.execute(
                text("SELECT attempts FROM media_acquisitions WHERE id = :id"),
                {"id": acq_id},
            )
        ).first()
        if current is None:
            raise ValueError(f"fail: acquisition {acq_id} not found")

        if unfulfillable:
            new_status = registered_values.require(
                "acquisition_status", "unfulfillable"
            )
        elif (
            retry_in is not None
            and int(current.attempts) < settings.acquisition_max_attempts
        ):
            new_status = registered_values.require("acquisition_status", "pending")
        else:
            new_status = registered_values.require("acquisition_status", "failed")

        if new_status == "pending":
            row = (
                await session.execute(
                    text(
                        f"""
                        UPDATE media_acquisitions
                        SET status = 'pending', error = :err,
                            claimed_by = NULL, claimed_at = NULL,
                            next_attempt_at = NOW() + make_interval(secs => :sec)
                        WHERE id = :id
                        RETURNING {_ROW_COLUMNS}
                        """
                    ),
                    {
                        "id": acq_id,
                        "err": error[:2000],
                        "sec": retry_in.total_seconds() if retry_in else 0.0,
                    },
                )
            ).first()
        else:
            row = (
                await session.execute(
                    text(
                        f"""
                        UPDATE media_acquisitions
                        SET status = :status, error = :err, completed_at = NOW()
                        WHERE id = :id
                        RETURNING {_ROW_COLUMNS}
                        """
                    ),
                    {"id": acq_id, "err": error[:2000], "status": new_status},
                )
            ).first()

        await _notify(session, new_status)
        EVENTS.emit("core.acquisition_failed", _row_to_acquisition(row).snapshot())

    # ── Reconciliation-sweep helper (§4.9 normative pairing) ───────────
    async def completed_for_origin(
        self,
        session: AsyncSession,
        *,
        origin_ref_prefix: str,
        since: datetime | None = None,
    ) -> list[Acquisition]:
        """Completed acquisitions whose ``origin_ref`` starts with the
        given prefix — the query a plugin's periodic reconciliation sweep
        uses to re-correlate completions its bus subscription missed
        (the bus is fire-and-forget; the sweep is truth)."""
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT {_ROW_COLUMNS} FROM media_acquisitions
                    WHERE status = 'done'
                      AND origin_ref LIKE :prefix
                      AND (CAST(:since AS TIMESTAMPTZ) IS NULL
                           OR completed_at > :since)
                    ORDER BY completed_at ASC
                    """
                ),
                {"prefix": origin_ref_prefix + "%", "since": since},
            )
        ).all()
        return [_row_to_acquisition(r) for r in rows]


# The process-wide singleton (single-process invariant — dossier §7 inv. 14).
ACQUISITIONS = AcquisitionService()
