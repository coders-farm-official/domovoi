"""The values Up next works with: tracks, picks, the engine's settings.

Plain frozen dataclasses with no behaviour beyond small helpers, shared by
:mod:`.pick` (pure), :mod:`.data` (SQL) and :mod:`.session` (state).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

# ─── Reason codes (stable: the API and the room queue carry them) ─────────

SAME_ARTIST = "same_artist"
SAME_ERA = "same_era"
SAME_GENRE = "same_genre"
PLAYED_TOGETHER = "played_together"
WILDCARD = "wildcard"
SIMILAR_ARTIST = "similar_artist"
FROM_LIST = "from_list"

REASON_CODES = frozenset({
    SAME_ARTIST, SAME_ERA, SAME_GENRE, PLAYED_TOGETHER, WILDCARD, SIMILAR_ARTIST, FROM_LIST,
})

# Library-mode buckets, in the order they are drawn (the scarcest first, so
# a track two buckets share goes to the one that has fewer to offer).
BUCKET_ORDER: tuple[str, ...] = (PLAYED_TOGETHER, SAME_ARTIST, SAME_GENRE, SAME_ERA, WILDCARD)

#: The plan's library-mode quota (UP-NEXT-QUEUE-PLAN §2.2).
DEFAULT_SHARES: Mapping[str, float] = {
    SAME_ARTIST: 0.25,
    SAME_ERA: 0.25,
    SAME_GENRE: 0.20,
    PLAYED_TOGETHER: 0.15,
    WILDCARD: 0.15,
}

MODES = frozenset({"list", "continue", "library"})


@dataclass(frozen=True, slots=True)
class Track:
    """What the picker knows about a library track.

    ``artist_norm`` is ``library_tracks.artist_norm`` (V050): one spelling
    per credit, ``""`` when the track has no artist. ``favorite`` is the
    household favourite, or the listener's own when that exists.
    """

    id: int
    artist_norm: str = ""
    year: int | None = None
    genre_keys: tuple[str, ...] = ()
    favorite: bool = False

    @property
    def spacing_key(self) -> str:
        """The artist for spacing; a track with no artist is its own."""
        return self.artist_norm or f"#{self.id}"

    @property
    def primary_genre(self) -> str | None:
        return self.genre_keys[0] if self.genre_keys else None


@dataclass(frozen=True, slots=True)
class TrackStats:
    """A track's listens in the affinity window (90 days), for the person
    or, for the household, everyone. ``last_days``: days since it was last
    heard by the same people within the freshness window (a year), None
    when it wasn't (never played, or not in a year): the freshness weight
    (:func:`domovoi.up_next.pick.freshness`)."""

    finished: int = 0
    plays: int = 0
    skipped: int = 0
    last_days: float | None = None


@dataclass(frozen=True, slots=True)
class Pick:
    """One auto pick. ``seed_track_id`` is the track it was found near
    (None for a wildcard). Labels are lowercase chrome and never name a
    person (§2.7)."""

    track_id: int
    reason_code: str
    reason_label: str
    seed_track_id: int | None = None
    #: The signals that weighed most in choosing it, strongest first, as
    #: ``(code, value)`` pairs (:func:`~.pick.why_signals`); "why this
    #: song?" is said from these (:func:`~.why.why_text`), never invented.
    why: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Batch:
    batch_no: int
    picks: tuple[Pick, ...]
    #: True when the session has nothing more to give (a list played with
    #: autoplay off reached its end, or an empty library).
    ended: bool = False


# ─── Freshness: the one place its numbers live ────────────────────────────
#
# Plain shuffles keep landing on the same rotation: the songs played most
# get the most affinity, so they come up most, so they get played more.
# Freshness pushes back a little. By days since the song was last heard
# (by the listener, else the household), on a log scale so the first days
# count most:
#   heard today .......... ×0.70
#   2 days ago ........... ×0.85
#   a week ............... ×0.98
#   a month .............. ×1.16
#   90 days, or never .... ×1.30
# then × (1 - 0.01 per play in the last 90 days, at most -0.15) for a song
# played very often; never below ×0.6. Compare favourite ×1.5 and affinity
# up to ×2.0: a well-liked song heard yesterday (2.0 × 0.8 = 1.6) still
# beats a never-played stranger (1.3) — it just doesn't win every time.
# No history at all (listening history off): every track ×1.3, i.e. no
# effect. tests/test_up_next_freshness.py measures the coverage it buys. These
# are the Music mix's middle step; its Discovery / Favorites ends move
# FRESH_MIN, FRESH_MAX and AFFINITY_STEP (domovoi/up_next/mix.py).
FRESH_MIN = 0.70
FRESH_MAX = 1.30
FRESH_FULL_DAYS = 90.0
FRESH_PLAY_STEP = 0.01
FRESH_PLAY_CAP = 15
FRESH_FLOOR = 0.60
#: Personal affinity per finished play (``1 + step·min(finished, 10)``).
AFFINITY_STEP = 0.10

# ─── Popularity (ListenBrainz, phase P7) ──────────────────────────────────
# ``popularity``: track id -> 0..1, the song's listen count against its
# artist's best-known song in the library, log scale (the data layer
# computes it). ×0.9 for a deep cut up to ×1.2 for the artist's hit; no
# figure ×1.0. Smaller than favourite, affinity and freshness on purpose:
# the house's own taste leads.
POP_LOW = 0.90
POP_HIGH = 1.20


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """The engine's knobs; the names are the settings P3/P4 will expose."""

    up_next_batch_size: int = 30
    up_next_refill_at: int = 5
    up_next_hold_hours: float = 4.0
    up_next_skip_demote_sec: int = 30
    #: list mode: share of a batch drawn by weight; the rest are uniform.
    list_weighted_share: float = 0.8
    #: list mode: a list this long or longer starts a new, reshuffled cycle
    #: when it is used up (a repeat goes unnoticed). A shorter one plays
    #: through once, then the session carries on in the library (continue
    #: mode), so 2 search results never ping-pong.
    list_repeat_min: int = 50
    #: library mode quota.
    shares: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_SHARES))
    #: ± years for "same era".
    era_years: int = 5
    #: spacing: at most this many of one artist in any window of this size.
    spacing_window: int = 6
    spacing_max_in_window: int = 2
    #: the session's no-repeat memory (picked track ids kept in state).
    picked_memory: int = 2000
    #: lean toward songs not heard in a while (:func:`~.pick.freshness`),
    #: from ``fresh_min`` (heard today) to ``fresh_max`` (90 days, never).
    freshness: bool = True
    fresh_min: float = FRESH_MIN
    fresh_max: float = FRESH_MAX
    #: personal affinity per finished play.
    affinity_step: float = AFFINITY_STEP


DEFAULT_CONFIG = EngineConfig()


@dataclass(frozen=True, slots=True)
class Taste:
    """The session's taste, as the picker reads it (§2.5): weight factors
    from early skips and the artists excluded for the rest of the session.
    :class:`domovoi.up_next.session.UpNextSession` keeps and changes it."""

    artist_factor: Mapping[str, float] = field(default_factory=dict)
    genre_factor: Mapping[str, float] = field(default_factory=dict)
    excluded_artists: frozenset[str] = frozenset()
    #: "less like this" on a track with no artist excludes that track.
    excluded_tracks: frozenset[int] = frozenset()

    def excludes(self, track: Track) -> bool:
        if track.id in self.excluded_tracks:
            return True
        return bool(track.artist_norm) and track.artist_norm in self.excluded_artists


@dataclass(frozen=True, slots=True)
class ListCycle:
    """Where a list-mode session is in its list (§2.2 list mode).

    ``done`` are the ids already handed out in this cycle, in order (the
    clicked song first in cycle 0). ``tail`` is the last 10% of the
    previous cycle: they may not take the first 10 places of this one.
    """

    cycle: int = 0
    done: tuple[int, ...] = ()
    tail: tuple[int, ...] = ()


#: When a track was last heard on this target or by this person, for the
#: hold-back (track id -> time).
LastHeard = Mapping[int, datetime]
