"""Up next, the pure part: inputs in, picks out (UP-NEXT-QUEUE-PLAN §2).

No database, no clock, no randomness of its own. Every random choice
comes from the ``random.Random`` the caller passes, made by
:func:`batch_rng` from the session's ``rng_seed`` and ``batch_no``; the
time is the caller's ``now``. The same inputs give the same batch, byte
for byte.

**Library mode** (:func:`library_batch`). A batch of ``n`` is filled by
quota from five buckets: same artist 25%, same era 25% (``year`` within
±5 of a seed's), same genre 20% (overlapping ``genre_keys``), played
together 15% (the caller's playlist and listening-session neighbours of
the seeds), wildcards 15% (any track; up to half of them may be
``similar to <artist>`` when the online signal, phase P7, supplies
``similar``). The shares are rounded by largest remainder so they add up
to ``n``. A bucket with fewer eligible tracks than its quota gives the
shortfall to the others in proportion to their shares (:func:`allocate`):
no year tags, no genres, no history yet all just reflow. A track two
buckets share goes to the one drawn first (the scarcest first,
:data:`~.model.BUCKET_ORDER`); if the buckets still come up short, a
fill pass takes what they have left, then any eligible track.

Inside a bucket each slot first draws a seed by its drift weight (§2.4:
the last five tracks listened to, 1.0 … 0.2 from the newest, plus the
original seed at 0.3; :func:`drift_weights`) among the seeds that still
have candidates, then a track near that seed, weighted by :func:`score`:
``favourite ×1.5 · personal affinity · session demotion``. Sampling, not
top-N, so batches vary.

* ``personal affinity`` (:func:`affinity`) = ``1 + 0.1·min(finished, 10)``
  minus ``0.3 · skip rate`` once a track has 3 plays; the caller passes
  the person's 90-day listens, or the household's.
* ``session demotion``: the session's early-skip factors on the artist
  (×0.3 each) and the track's primary genre (×0.6 each); excluded artists
  are never picked (:class:`~.model.Taste`).

**Hold-back.** Never picked: the seeds (the song playing and the ones
just heard). Not picked: a track heard on this target or by this person
within ``up_next_hold_hours`` (4 h) of ``now``; a track this session
already picked. When that leaves fewer than ``2 × n`` eligible
tracks, the session's no-repeat memory halves (the oldest picks become
eligible again), then the hold window halves, until there are enough or
both are gone: the queue never runs dry because of hold-back. Excluded
artists are never relaxed.

**Spacing** (:func:`space`). After drawing, the batch is shuffled (it
was drawn bucket by bucket), then a greedy re-order: never the
same artist back to back (counting what plays just before the batch), and
at most 2 of one artist in any 6 in a row. When spreading is impossible
(a list of one artist) the 2-in-6 rule gives way first, and back-to-back
is allowed only when nothing else is left.

**List mode** (:func:`list_batch`): a song clicked in a list. The pool is
the rest of this cycle of the list (cycle 0 starts with the clicked song
already done). Each track weighs ``1 + Σ seed weight·(1.5·same artist +
1.0·same era + 0.8·shared genre)``, times :func:`score`; 80% of a batch is
drawn by weighted sampling without replacement (Efraimidis–Spirakis keys,
:func:`es_order`), 20% uniformly from the rest of the pool, so one corner
of the list can't take over. The remaining pool is re-sampled at every
refill, so the cycle drifts with what was listened to and still plays
every track once. When the cycle is used up and autoplay is on, the next
cycle reshuffles the whole list; the last 10% of the previous cycle can't
take its first 10 places (:class:`~.model.ListCycle`). With autoplay off
the session ends at the end of the list. A batch never holds one track
twice (a short list gives short batches).

**The queue** (:func:`place_explicit` and friends): explicit items ("play
next", "add to queue") always sit before every auto pick still to come.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Collection, Iterable, Mapping, Sequence, TypeVar

from .model import (
    BUCKET_ORDER,
    DEFAULT_CONFIG,
    FROM_LIST,
    PLAYED_TOGETHER,
    SAME_ARTIST,
    SAME_ERA,
    SAME_GENRE,
    SIMILAR_ARTIST,
    WILDCARD,
    EngineConfig,
    ListCycle,
    Pick,
    Taste,
    Track,
    TrackStats,
)

T = TypeVar("T")

#: Drift weights of the last five tracks listened to, newest first (§2.4).
DRIFT_WEIGHTS: tuple[float, ...] = (1.0, 0.8, 0.6, 0.4, 0.2)
#: The original seed's weight beside them.
ORIGINAL_SEED_WEIGHT = 0.3

FAVOURITE_FACTOR = 1.5
SKIP_ARTIST_FACTOR = 0.3
SKIP_GENRE_FACTOR = 0.6

# List-mode similarity weights (§2.2 list mode, step 2).
LIST_SAME_ARTIST = 1.5
LIST_SAME_ERA = 1.0
LIST_SHARED_GENRE = 0.8

#: In a new list cycle, the previous cycle's last share may not take the
#: first places (§2.2 list mode, step 4).
BOUNDARY_TAIL_SHARE = 0.10
BOUNDARY_PLACES = 10


# ─── Randomness ───────────────────────────────────────────────────────────


def batch_rng(rng_seed: int, batch_no: int) -> random.Random:
    """The one RNG of a batch. Derived with BLAKE2b rather than ``hash()``
    so it is the same on every platform and Python build."""
    digest = hashlib.blake2b(f"{int(rng_seed)}:{int(batch_no)}".encode(), digest_size=8).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _weighted_index(weights: Sequence[float], rng: random.Random) -> int:
    total = math.fsum(weights)
    if total <= 0:
        return rng.randrange(len(weights))
    r = rng.random() * total
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if r < acc:
            return i
    return len(weights) - 1


def es_order(items: Sequence[T], weights: Sequence[float], rng: random.Random) -> list[T]:
    """Weighted random order without replacement (Efraimidis–Spirakis):
    key ``u^(1/w)``, compared as ``log(u)/w``, largest first. Taking the
    first k is a weighted sample of k."""
    keyed = []
    for i, (item, w) in enumerate(zip(items, weights)):
        u = 1.0 - rng.random()  # (0, 1]
        k = math.log(u) / w if w > 0 else -math.inf
        keyed.append((k, -i, item))
    keyed.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item for _, _, item in keyed]


# ─── Seeds and scores ─────────────────────────────────────────────────────


def drift_weights(recent_listened: Sequence[int], original: int | None) -> list[tuple[int, float]]:
    """``[(track_id, weight)]``: the last five tracks listened to (newest
    first) at 1.0, 0.8, 0.6, 0.4, 0.2, plus the original seed at 0.3. A
    track in both adds up. Order: newest first, the original last."""
    out: dict[int, float] = {}
    for tid, w in zip(recent_listened[:len(DRIFT_WEIGHTS)], DRIFT_WEIGHTS):
        out[tid] = out.get(tid, 0.0) + w
    if original is not None:
        out[original] = out.get(original, 0.0) + ORIGINAL_SEED_WEIGHT
    return list(out.items())


def affinity(stats: TrackStats | None) -> float:
    """``1 + 0.1·min(finished, 10)``, minus ``0.3·skip rate`` from 3 plays."""
    if stats is None:
        return 1.0
    a = 1.0 + 0.1 * min(stats.finished, 10)
    if stats.plays >= 3:
        a -= 0.3 * (stats.skipped / stats.plays)
    return max(a, 0.05)


def score(track: Track, stats: Mapping[int, TrackStats], taste: Taste) -> float:
    s = FAVOURITE_FACTOR if track.favorite else 1.0
    s *= affinity(stats.get(track.id))
    if track.artist_norm:
        s *= taste.artist_factor.get(track.artist_norm, 1.0)
    g = track.primary_genre
    if g:
        s *= taste.genre_factor.get(g, 1.0)
    return s


def _near_era(seed: Track, t: Track, years: int) -> bool:
    return seed.year is not None and t.year is not None and abs(t.year - seed.year) <= years


def _shared_genre(seed: Track, t: Track) -> str | None:
    if not seed.genre_keys or not t.genre_keys:
        return None
    theirs = set(t.genre_keys)
    for k in seed.genre_keys:
        if k in theirs:
            return k
    return None


def _same_artist(seed: Track, t: Track) -> bool:
    return bool(seed.artist_norm) and seed.artist_norm == t.artist_norm


def decade_label(year: int) -> str:
    """``90s`` for 1990-1999 (1920s-1990s by their two digits), else the
    whole decade: ``1910s``, ``2000s``, ``2010s``."""
    d = (year // 10) * 10
    if 1920 <= d <= 1990:
        return f"{d % 100:02d}s"
    return f"{d}s"


def era_label(seed_year: int, track_year: int) -> str:
    """The seed's decade when the pick shares it, else ``from 1997-ish``."""
    if seed_year // 10 == track_year // 10:
        return decade_label(seed_year)
    return f"from {seed_year}-ish"


# ─── Quotas ───────────────────────────────────────────────────────────────


def largest_remainder(n: int, shares: Mapping[str, float]) -> dict[str, int]:
    """Split ``n`` by ``shares`` into integers that add up to ``n``
    (largest remainder; ties go to the earlier key)."""
    keys = list(shares)
    total = math.fsum(max(0.0, shares[k]) for k in keys)
    if n <= 0 or total <= 0:
        return {k: 0 for k in keys}
    exact = {k: n * max(0.0, shares[k]) / total for k in keys}
    out = {k: int(math.floor(exact[k])) for k in keys}
    left = n - sum(out.values())
    order = sorted(keys, key=lambda k: (-(exact[k] - out[k]), keys.index(k)))
    for k in order[:left]:
        out[k] += 1
    return out


def allocate(n: int, shares: Mapping[str, float], avail: Mapping[str, int]) -> dict[str, int]:
    """The quota per bucket: ``shares`` of ``n``, each capped at what the
    bucket has, the shortfall shared among the buckets with room in
    proportion to their shares. Adds up to ``min(n, Σ avail)``."""
    q = largest_remainder(n, shares)
    for _ in range(len(shares) + 1):
        short = 0
        for k in q:
            a = max(0, avail.get(k, 0))
            if q[k] > a:
                short += q[k] - a
                q[k] = a
        if short == 0:
            break
        room = {k: shares[k] for k in q if q[k] < avail.get(k, 0) and shares[k] > 0}
        if not room:
            break
        for k, extra in largest_remainder(short, room).items():
            q[k] += extra
    return q


# ─── Hold-back ────────────────────────────────────────────────────────────


def eligible_tracks(
    candidates: Iterable[Track],
    *,
    taste: Taste,
    last_heard: Mapping[int, datetime],
    now: datetime,
    hold_hours: float,
    no_repeat: Sequence[int],
    min_pool: int,
) -> list[Track]:
    """The candidates the batch may use (module docstring, "Hold-back"),
    sorted by id. ``no_repeat`` is oldest first: halving keeps the newest."""
    cands = sorted({t.id: t for t in candidates if not taste.excludes(t)}.values(), key=lambda t: t.id)
    window = len(no_repeat)
    hold = max(0.0, float(hold_hours))
    while True:
        recent = set(no_repeat[len(no_repeat) - window:]) if window else set()
        cutoff = now - timedelta(hours=hold) if hold > 0 else None
        out = [
            t for t in cands
            if t.id not in recent
            and not (cutoff is not None and (h := last_heard.get(t.id)) is not None and h >= cutoff)
        ]
        if len(out) >= min_pool or (window == 0 and hold == 0):
            return out
        if window > 0:
            window //= 2
        else:
            hold = hold / 2 if hold > 0.05 else 0.0


# ─── Spacing ──────────────────────────────────────────────────────────────


def space(
    items: Sequence[T],
    key: Callable[[T], str],
    *,
    prev: Sequence[str] = (),
    window: int = DEFAULT_CONFIG.spacing_window,
    max_in_window: int = DEFAULT_CONFIG.spacing_max_in_window,
    blocked: Callable[[T, int], bool] | None = None,
) -> list[T]:
    """Re-order ``items`` so one artist (``key``) never plays twice in a
    row and at most ``max_in_window`` times in any ``window`` in a row,
    keeping the drawn order wherever it can. ``prev`` are the keys that
    play just before (newest last). ``blocked(item, pos)`` is a hard rule
    (the list boundary): a blocked item is never placed at ``pos`` while
    something else can be.

    Greedy, with one look-ahead that keeps "no back to back" feasible: an
    artist holding more than half of what is left must go now. Back to
    back happens only when nothing else is left to play."""
    remaining = list(items)
    ctx = list(prev)[-max(window - 1, 1):]
    out: list[T] = []
    while remaining:
        pos = len(out)
        idx = list(range(len(remaining)))
        if blocked is not None:
            ok = [i for i in idx if not blocked(remaining[i], pos)]
            if ok:
                idx = ok
        last = ctx[-1] if ctx else None
        recent = ctx[-(window - 1):] if window > 1 else []
        counts = Counter(key(remaining[i]) for i in idx)
        rest = len(remaining)
        choice = None
        heavy = [k for k, c in counts.items() if 2 * c > rest and k != last]
        if heavy:
            choice = next(i for i in idx if key(remaining[i]) == heavy[0])
        if choice is None:  # an artist not heard in the window at all
            choice = next((i for i in idx if key(remaining[i]) not in recent), None)
        if choice is None:
            choice = next(
                (i for i in idx
                 if key(remaining[i]) != last and recent.count(key(remaining[i])) < max_in_window),
                None,
            )
        if choice is None:
            choice = next((i for i in idx if key(remaining[i]) != last), None)
        if choice is None:
            choice = idx[0]
        item = remaining.pop(choice)
        out.append(item)
        ctx.append(key(item))
        if len(ctx) > window:
            del ctx[0]
    return out


# ─── Library mode ─────────────────────────────────────────────────────────


@dataclass
class _Bucket:
    code: str
    #: seed id -> [(track, label)] near that seed (sorted by track id).
    by_seed: dict[int | None, list[tuple[Track, str]]]


def _draw(
    bucket: _Bucket,
    *,
    seed_weight: Mapping[int | None, float],
    weight_of: Callable[[Track], float],
    used: set[int],
    rng: random.Random,
    prefer: Collection[int] = (),
) -> Pick | None:
    """One pick from a bucket: a seed among those with candidates left
    (``prefer``-red seeds first when any has some), weighed by its drift
    weight times the mean score of its candidates (so a demoted artist's
    seed is drawn less for "same artist"), then a track near it by score."""
    live: list[tuple[int | None, list[tuple[Track, str]]]] = []
    for sid, lst in bucket.by_seed.items():
        rest = [(t, lab) for t, lab in lst if t.id not in used]
        if rest:
            live.append((sid, rest))
    if not live:
        return None
    if prefer:
        pref = [x for x in live if x[0] in prefer]
        if pref:
            live = pref
    si = _weighted_index(
        [(seed_weight.get(sid, 0.0) or 1e-9) * (math.fsum(weight_of(t) for t, _ in rest) / len(rest))
         for sid, rest in live],
        rng,
    )
    sid, rest = live[si]
    ti = _weighted_index([weight_of(t) for t, _ in rest], rng)
    track, label = rest[ti]
    used.add(track.id)
    return Pick(track.id, bucket.code, label, sid)


def library_batch(
    *,
    n: int,
    rng: random.Random,
    now: datetime,
    seeds: Sequence[tuple[Track, float]],
    candidates: Iterable[Track],
    wildcard_ids: Collection[int] | None = None,
    together: Mapping[int, Mapping[int, str]] | None = None,
    prefer_together_seeds: Collection[int] = (),
    similar: Mapping[int, str] | None = None,
    stats: Mapping[int, TrackStats] | None = None,
    last_heard: Mapping[int, datetime] | None = None,
    session_picked: Sequence[int] = (),
    taste: Taste = Taste(),
    prev_keys: Sequence[str] = (),
    config: EngineConfig = DEFAULT_CONFIG,
) -> list[Pick]:
    """A library-mode (or continue-mode) batch; see the module docstring.

    ``seeds``: ``[(track, drift weight)]``. ``candidates``: every track the
    caller fetched (near the seeds, the together hits, the wildcard
    sample; the whole library in tests). ``wildcard_ids``: the uniform
    sample, None = every candidate. ``together``: seed id -> {track id:
    label}. ``prefer_together_seeds``: continue mode's list tracks, whose
    neighbours the together bucket takes first. ``similar``: track id ->
    ``similar to <artist>`` label (P7; empty until then).
    """
    stats = stats or {}
    together = together or {}
    similar = similar or {}
    seed_ids = {s.id for s, _ in seeds}
    eligible = eligible_tracks(
        (t for t in candidates if t.id not in seed_ids),
        taste=taste,
        last_heard=last_heard or {},
        now=now,
        hold_hours=config.up_next_hold_hours,
        no_repeat=session_picked,
        min_pool=2 * n,
    )
    if n <= 0 or not eligible:
        return []
    by_id = {t.id: t for t in eligible}
    seed_weight: dict[int | None, float] = {s.id: w for s, w in seeds}
    seed_weight[None] = 1.0
    for sid in together:
        seed_weight.setdefault(sid, ORIGINAL_SEED_WEIGHT)

    buckets: dict[str, _Bucket] = {c: _Bucket(c, {}) for c in BUCKET_ORDER}
    for seed, _w in seeds:
        artist = [(t, "same artist") for t in eligible if t.id != seed.id and _same_artist(seed, t)]
        era = [(t, era_label(seed.year, t.year)) for t in eligible
               if t.id != seed.id and _near_era(seed, t, config.era_years)]
        genre = []
        for t in eligible:
            if t.id != seed.id and (g := _shared_genre(seed, t)) is not None:
                genre.append((t, g))
        if artist:
            buckets[SAME_ARTIST].by_seed[seed.id] = artist
        if era:
            buckets[SAME_ERA].by_seed[seed.id] = era
        if genre:
            buckets[SAME_GENRE].by_seed[seed.id] = genre
    for sid in sorted(together):
        hits = [(by_id[tid], lab) for tid, lab in sorted(together[sid].items()) if tid in by_id]
        if hits:
            buckets[PLAYED_TOGETHER].by_seed[sid] = hits
    wild_pool = [t for t in eligible if wildcard_ids is None or t.id in wildcard_ids]
    if wild_pool:
        buckets[WILDCARD].by_seed[None] = [(t, "wildcard") for t in wild_pool]
    sim_pool = [(by_id[tid], lab) for tid, lab in sorted(similar.items()) if tid in by_id]

    avail = {}
    for code, b in buckets.items():
        ids = {t.id for lst in b.by_seed.values() for t, _ in lst}
        if code == WILDCARD:
            ids |= {t.id for t, _ in sim_pool}
        avail[code] = len(ids)
    quotas = allocate(n, config.shares, avail)

    weight_cache: dict[int, float] = {}

    def weight_of(t: Track) -> float:
        w = weight_cache.get(t.id)
        if w is None:
            w = weight_cache[t.id] = score(t, stats, taste)
        return w

    used: set[int] = set()
    picks: list[Pick] = []
    for code in BUCKET_ORDER:
        want = quotas.get(code, 0)
        got = 0
        if code == WILDCARD and sim_pool and want:
            sim_bucket = _Bucket(SIMILAR_ARTIST, {None: sim_pool})
            for _ in range(want // 2):
                p = _draw(sim_bucket, seed_weight=seed_weight, weight_of=weight_of, used=used, rng=rng)
                if p is None:
                    break
                picks.append(p)
                got += 1
        while got < want:
            p = _draw(buckets[code], seed_weight=seed_weight, weight_of=weight_of, used=used, rng=rng,
                      prefer=prefer_together_seeds if code == PLAYED_TOGETHER else ())
            if p is None:
                break
            picks.append(p)
            got += 1

    # Fill: buckets that shared tracks came up short; take what is left,
    # one bucket at a time in turn, then any eligible track.
    while len(picks) < n:
        progressed = False
        for code in BUCKET_ORDER:
            if len(picks) >= n:
                break
            p = _draw(buckets[code], seed_weight=seed_weight, weight_of=weight_of, used=used, rng=rng)
            if p is not None:
                picks.append(p)
                progressed = True
        if not progressed:
            break
    if len(picks) < n:
        rest = [t for t in eligible if t.id not in used]
        anyb = _Bucket(WILDCARD, {None: [(t, "wildcard") for t in rest]})
        while len(picks) < n:
            p = _draw(anyb, seed_weight=seed_weight, weight_of=weight_of, used=used, rng=rng)
            if p is None:
                break
            picks.append(p)

    # Drawn bucket by bucket; heard mixed (then spaced).
    rng.shuffle(picks)
    key_of = {t.id: t.spacing_key for t in eligible}
    return space(picks, lambda p: key_of[p.track_id], prev=prev_keys,
                 window=config.spacing_window, max_in_window=config.spacing_max_in_window)


# ─── List mode ────────────────────────────────────────────────────────────


def list_weight(track: Track, seeds: Sequence[tuple[Track, float]], era_years: int = 5) -> float:
    """``1 + Σ w·(1.5·same artist + 1.0·same era + 0.8·shared genre)``
    over the seeds, their weights normalised to add up to 1."""
    total = math.fsum(w for _, w in seeds)
    if total <= 0:
        return 1.0
    sim = 0.0
    for seed, w in seeds:
        part = 0.0
        if _same_artist(seed, track):
            part += LIST_SAME_ARTIST
        if _near_era(seed, track, era_years):
            part += LIST_SAME_ERA
        if _shared_genre(seed, track) is not None:
            part += LIST_SHARED_GENRE
        sim += (w / total) * part
    return 1.0 + sim


def start_cycle(clicked: int | None) -> ListCycle:
    """Cycle 0 of a list session: the clicked song is already playing."""
    return ListCycle(cycle=0, done=(clicked,) if clicked is not None else (), tail=())


def list_batch(
    *,
    n: int,
    rng: random.Random,
    members: Sequence[Track],
    state: ListCycle,
    seeds: Sequence[tuple[Track, float]],
    stats: Mapping[int, TrackStats] | None = None,
    taste: Taste = Taste(),
    autoplay: bool = True,
    label: str = "from this list",
    seed_track_id: int | None = None,
    prev_keys: Sequence[str] = (),
    config: EngineConfig = DEFAULT_CONFIG,
) -> tuple[list[Pick], ListCycle, bool]:
    """A list-mode batch: ``(picks, the new cycle state, ended)``.

    ``members`` is the list as it is now (edits to a playlist count from
    the next refill), in list order. ``ended`` is True when the list is
    done and autoplay is off, or nothing in it may be played.
    """
    stats = stats or {}
    seen: set[int] = set()
    pool_all: list[Track] = []
    for t in members:
        if t.id not in seen and not taste.excludes(t):
            seen.add(t.id)
            pool_all.append(t)
    if not pool_all or n <= 0:
        return [], state, not pool_all
    by_id = {t.id: t for t in pool_all}
    picks: list[Pick] = []
    batch_ids: set[int] = set()
    st = state
    ended = False

    while len(picks) < n:
        done = set(st.done)
        pool = [t for t in pool_all if t.id not in done]
        if not pool:
            if not autoplay:
                ended = True
                break
            k_tail = math.ceil(BOUNDARY_TAIL_SHARE * len(st.done)) if st.done else 0
            tail = tuple(i for i in st.done[len(st.done) - k_tail:] if i in by_id) if k_tail else ()
            st = ListCycle(cycle=st.cycle + 1, done=(), tail=tail)
            continue
        d = len(st.done)
        tail = set(st.tail)
        boundary = max(0, min(BOUNDARY_PLACES, len(pool_all) - len(tail)))
        k = min(n - len(picks), len(pool))
        # Tail tracks may only take places at or past the boundary.
        tail_room = max(0, d + k - boundary)

        weights = [list_weight(t, seeds, config.era_years) * score(t, stats, taste) for t in pool]
        order = es_order(pool, weights, rng)
        k_w = int(round(config.list_weighted_share * k))
        chosen: list[Track] = []
        tail_used = 0
        for t in order:
            if len(chosen) >= k_w:
                break
            if t.id in tail:
                if tail_used >= tail_room:
                    continue
                tail_used += 1
            chosen.append(t)
        chosen_ids = {t.id for t in chosen}
        rest = [t for t in pool if t.id not in chosen_ids
                and (t.id not in tail or tail_used < tail_room)]
        while len(chosen) < k and rest:
            t = rest.pop(rng.randrange(len(rest)))
            if t.id in tail:
                if tail_used >= tail_room:
                    continue
                tail_used += 1
            chosen.insert(rng.randint(0, len(chosen)), t)
        if not chosen:
            break

        ctx = [*prev_keys, *(by_id[p.track_id].spacing_key for p in picks)]
        seg = space(
            chosen, lambda t: t.spacing_key, prev=ctx,
            window=config.spacing_window, max_in_window=config.spacing_max_in_window,
            blocked=lambda t, pos, d=d, b=boundary, tl=tail: t.id in tl and d + pos < b,
        )
        taken: list[int] = []
        stop = False
        for t in seg:
            if t.id in batch_ids:
                stop = True  # a short list: never one track twice in a batch
                break
            batch_ids.add(t.id)
            taken.append(t.id)
            picks.append(Pick(t.id, FROM_LIST, label, seed_track_id))
        st = ListCycle(cycle=st.cycle, done=(*st.done, *taken), tail=st.tail)
        if stop or not taken:
            break
    return picks, st, ended


# ─── The queue: explicit items before auto picks ──────────────────────────


@dataclass(frozen=True, slots=True)
class QueueItem:
    track_id: int
    origin: str  # 'explicit' | 'auto'
    pick: Pick | None = None


def place_explicit(
    queue: Sequence[QueueItem], items: Sequence[int], *, current: int, where: str = "end",
) -> list[QueueItem]:
    """Insert explicit items. ``next``: right after the current song.
    ``end``: after the last explicit item still to come, before the first
    auto pick (§4: "end" never lands behind Up next)."""
    q = list(queue)
    if where == "next":
        pos = current + 1
    elif where == "end":
        pos = next((i for i in range(current + 1, len(q)) if q[i].origin == "auto"), len(q))
    else:
        raise ValueError(f"where must be 'next' or 'end', not {where!r}")
    pos = max(0, min(pos, len(q)))
    new = [QueueItem(tid, "explicit") for tid in items]
    return q[:pos] + new + q[pos:]


def append_auto(queue: Sequence[QueueItem], picks: Sequence[Pick]) -> list[QueueItem]:
    return [*queue, *(QueueItem(p.track_id, "auto", p) for p in picks)]


def drop_auto(
    queue: Sequence[QueueItem], *, current: int, drop: Callable[[QueueItem], bool],
) -> list[QueueItem]:
    """Remove waiting auto picks (after the current song) that ``drop``
    selects: "less like this" swaps an excluded artist's picks out."""
    return [it for i, it in enumerate(queue)
            if not (i > current and it.origin == "auto" and drop(it))]


def auto_left(queue: Sequence[QueueItem], current: int) -> int:
    return sum(1 for it in queue[current + 1:] if it.origin == "auto")


def needs_refill(queue: Sequence[QueueItem], current: int, config: EngineConfig = DEFAULT_CONFIG) -> bool:
    """True once ``up_next_refill_at`` (5) or fewer auto picks are left."""
    return auto_left(queue, current) <= config.up_next_refill_at
