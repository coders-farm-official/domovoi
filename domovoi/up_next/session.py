"""An Up next session's state, and the events that change it (§2.1, §2.5).

One session per target (``room:<room_id>`` or ``device:<device_id>``).
:class:`UpNextSession` holds everything the next refill needs: the mode,
the seed and its list, who the mix is for, autoplay, the RNG seed and
batch counter, the list cycle, the session's taste (early-skip
demotions, excluded artists) and the drift seeds. It turns into a JSON
dict and back (:meth:`UpNextSession.to_state` / :meth:`from_state`), so
phase P3 can keep it in ``up_next_sessions.state`` without this module
knowing about tables.

Who the mix is for is decided when the session starts and again when the
listener changes (§2.3): :func:`device_listener` and
:func:`room_listener`. ``person_id`` None means the household.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from .model import DEFAULT_CONFIG, MODES, EngineConfig, ListCycle, Pick, Taste, Track
from .pick import SKIP_ARTIST_FACTOR, SKIP_GENRE_FACTOR, start_cycle

#: Throwaway lists (search results ...) are capped like ``play-tracks``.
IDS_CAP = 500
#: The drift seeds kept: the last five tracks listened to.
DRIFT_KEEP = 5
LIST_KINDS = frozenset({"playlist", "album", "artist", "ids", "genre"})
_TARGET_KINDS = ("room", "device")


# ─── Who is listening (§2.3) ──────────────────────────────────────────────


def device_listener(
    *,
    listener_person_id: int | None,
    owner_person_id: int | None,
    shared_screen: bool,
) -> int | None:
    """A browser or app: the "listening as" person it sent; else the
    device's owner, unless it is a shared screen; else the household."""
    if listener_person_id is not None:
        return listener_person_id
    if owner_person_id is not None and not shared_screen:
        return owner_person_id
    return None


def room_listener(*, voice_person_id: int | None = None, started_by_voice: bool,
                  device_person_id: int | None = None) -> int | None:
    """A room: the identified voice that started it (None = an unknown
    voice: the household), or the starting device's listener."""
    return voice_person_id if started_by_voice else device_person_id


def parse_target(target: str) -> tuple[str, str]:
    """``room:<id>`` / ``device:<id>`` -> (kind, id)."""
    kind, sep, ident = str(target).partition(":")
    if not sep or kind not in _TARGET_KINDS or not ident:
        raise ValueError(f"target must be room:<id> or device:<id>, not {target!r}")
    return kind, ident


# ─── The seed ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SeedRef:
    """The starting track and the list it came from.

    ``kind`` / ``ref``: ``playlist`` + playlist id, ``album`` +
    ``<artist_norm>|<album>``, ``artist`` + artist_norm, ``genre`` + a
    genre key (the Genres tab), ``ids`` + ``track_ids`` (search results
    and other throwaway lists, at most 500), or no list. ``label`` is
    what continue mode's picks say (``from road trip``).
    """

    track_id: int | None
    kind: str | None = None
    ref: str | None = None
    track_ids: tuple[int, ...] = ()
    label: str | None = None

    def __post_init__(self) -> None:
        if self.kind is not None and self.kind not in LIST_KINDS:
            raise ValueError(f"unknown list kind {self.kind!r}")
        if self.kind == "ids":
            object.__setattr__(self, "track_ids", tuple(int(i) for i in self.track_ids[:IDS_CAP]))
        if self.kind not in (None, "ids") and not self.ref:
            raise ValueError(f"a {self.kind} list needs its ref")

    @property
    def list_ref(self) -> str | None:
        """The plan's string form: ``playlist:<id>``, ``album:<a|b>`` ..."""
        if self.kind is None:
            return None
        if self.kind == "ids":
            return "ids:[" + ",".join(str(i) for i in self.track_ids) + "]"
        return f"{self.kind}:{self.ref}"

    def to_json(self) -> dict[str, Any]:
        return {"track_id": self.track_id, "kind": self.kind, "ref": self.ref,
                "track_ids": list(self.track_ids), "label": self.label}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "SeedRef":
        return cls(track_id=d.get("track_id"), kind=d.get("kind"), ref=d.get("ref"),
                   track_ids=tuple(d.get("track_ids") or ()), label=d.get("label"))


# ─── The session ──────────────────────────────────────────────────────────


@dataclass
class UpNextSession:
    target: str
    mode: str
    seed: SeedRef
    person_id: int | None = None
    autoplay: bool = True
    rng_seed: int = 0
    batch_no: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    list_cycle: ListCycle = field(default_factory=ListCycle)
    #: newest first, at most five: the drift seeds (§2.4).
    recent_listened: list[int] = field(default_factory=list)
    #: oldest first: every track this session picked (the no-repeat memory).
    picked: list[int] = field(default_factory=list)
    artist_factor: dict[str, float] = field(default_factory=dict)
    genre_factor: dict[str, float] = field(default_factory=dict)
    early_skips: dict[str, int] = field(default_factory=dict)
    excluded_artists: set[str] = field(default_factory=set)
    excluded_tracks: set[int] = field(default_factory=set)
    ended: bool = False

    def __post_init__(self) -> None:
        parse_target(self.target)
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {sorted(MODES)}, not {self.mode!r}")
        if self.mode == "list" and self.seed.kind is None:
            raise ValueError("a list session needs the list")

    @classmethod
    def start(
        cls, *, target: str, mode: str, seed: SeedRef, person_id: int | None = None,
        autoplay: bool = True, rng_seed: int | None = None,
    ) -> "UpNextSession":
        """A new session. ``rng_seed`` is random unless a test pins it."""
        s = cls(target=target, mode=mode, seed=seed, person_id=person_id, autoplay=autoplay,
                rng_seed=secrets.randbits(63) if rng_seed is None else int(rng_seed))
        if mode == "list":
            s.list_cycle = start_cycle(seed.track_id)
        return s

    # ── reads ──

    @property
    def target_kind(self) -> str:
        return parse_target(self.target)[0]

    @property
    def target_id(self) -> str:
        return parse_target(self.target)[1]

    def taste(self) -> Taste:
        return Taste(
            artist_factor=dict(self.artist_factor),
            genre_factor=dict(self.genre_factor),
            excluded_artists=frozenset(self.excluded_artists),
            excluded_tracks=frozenset(self.excluded_tracks),
        )

    # ── events ──

    def record_listen(
        self, track: Track, *, played_sec: float, duration_sec: float | None, outcome: str,
        config: EngineConfig = DEFAULT_CONFIG,
    ) -> bool:
        """A song of this session ended. Listened (finished, or 50% or
        more) makes it the newest drift seed. A skip within
        ``up_next_skip_demote_sec`` (30 s) demotes its artist ×0.3 and its
        primary genre ×0.6 for the session; a second early skip of the
        artist excludes it. Returns True when the artist was just excluded
        (the caller swaps that artist's waiting picks out)."""
        listened = outcome == "finished" or (
            bool(duration_sec) and played_sec >= 0.5 * float(duration_sec))
        if listened:
            self.recent_listened = [track.id, *(i for i in self.recent_listened if i != track.id)]
            del self.recent_listened[DRIFT_KEEP:]
        if outcome != "skipped" or played_sec >= config.up_next_skip_demote_sec:
            return False
        genre = track.primary_genre
        if genre:
            self.genre_factor[genre] = self.genre_factor.get(genre, 1.0) * SKIP_GENRE_FACTOR
        if not track.artist_norm:
            return False
        n = self.early_skips.get(track.artist_norm, 0) + 1
        self.early_skips[track.artist_norm] = n
        if n >= 2:
            newly = track.artist_norm not in self.excluded_artists
            self.excluded_artists.add(track.artist_norm)
            return newly
        self.artist_factor[track.artist_norm] = (
            self.artist_factor.get(track.artist_norm, 1.0) * SKIP_ARTIST_FACTOR)
        return False

    def less_like_this(self, track: Track) -> None:
        """"Less like this" / "skip this artist": the artist is out for the
        session at once (a track with no artist: that track)."""
        if track.artist_norm:
            self.excluded_artists.add(track.artist_norm)
        else:
            self.excluded_tracks.add(track.id)

    def record_batch(self, picks: list[Pick] | tuple[Pick, ...], *, list_cycle: ListCycle | None = None,
                     ended: bool = False, config: EngineConfig = DEFAULT_CONFIG) -> None:
        self.batch_no += 1
        self.picked.extend(p.track_id for p in picks)
        if len(self.picked) > config.picked_memory:
            del self.picked[: len(self.picked) - config.picked_memory]
        if list_cycle is not None:
            self.list_cycle = list_cycle
        if ended:
            self.ended = True

    def set_listener(self, person_id: int | None) -> None:
        """The listener changed (§2.3): the mix is for them from the next
        refill on."""
        self.person_id = person_id

    def move(self, target: str) -> None:
        """Casting: the session carries on on another target."""
        parse_target(target)
        self.target = target

    # ── state ──

    def to_state(self) -> dict[str, Any]:
        """Everything but the columns P3 keeps beside it (id, target,
        person, mode, seed, autoplay, rng_seed, batch_no), as JSON."""
        c = self.list_cycle
        return {
            "list": {"cycle": c.cycle, "done": list(c.done), "tail": list(c.tail)},
            "recent_listened": list(self.recent_listened),
            "picked": list(self.picked),
            "artist_factor": dict(self.artist_factor),
            "genre_factor": dict(self.genre_factor),
            "early_skips": dict(self.early_skips),
            "excluded_artists": sorted(self.excluded_artists),
            "excluded_tracks": sorted(self.excluded_tracks),
            "ended": self.ended,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "target": self.target, "mode": self.mode, "seed": self.seed.to_json(),
            "person_id": self.person_id, "autoplay": self.autoplay, "rng_seed": self.rng_seed,
            "batch_no": self.batch_no, "state": self.to_state(),
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "UpNextSession":
        st = d.get("state") or {}
        lst = st.get("list") or {}
        return cls(
            id=str(d["id"]), target=d["target"], mode=d["mode"], seed=SeedRef.from_json(d["seed"]),
            person_id=d.get("person_id"), autoplay=bool(d.get("autoplay", True)),
            rng_seed=int(d["rng_seed"]), batch_no=int(d.get("batch_no", 0)),
            list_cycle=ListCycle(int(lst.get("cycle", 0)), tuple(lst.get("done") or ()),
                                 tuple(lst.get("tail") or ())),
            recent_listened=[int(i) for i in st.get("recent_listened") or ()],
            picked=[int(i) for i in st.get("picked") or ()],
            artist_factor={k: float(v) for k, v in (st.get("artist_factor") or {}).items()},
            genre_factor={k: float(v) for k, v in (st.get("genre_factor") or {}).items()},
            early_skips={k: int(v) for k, v in (st.get("early_skips") or {}).items()},
            excluded_artists=set(st.get("excluded_artists") or ()),
            excluded_tracks={int(i) for i in st.get("excluded_tracks") or ()},
            ended=bool(st.get("ended", False)),
        )
