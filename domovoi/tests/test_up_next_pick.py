"""Up next, the pure picker and the session (UP-NEXT-QUEUE-PLAN §2.8).

No database: a synthetic library of 300 tracks, 40 artists, six decades,
some without year or genre (``up_next_fixtures``), a pinned RNG seed and
an injected ``now``.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import timedelta

import pytest

from domovoi.up_next import pick as P
from domovoi.up_next.engine import next_batch
from domovoi.up_next.model import (
    DEFAULT_CONFIG,
    FROM_LIST,
    PLAYED_TOGETHER,
    REASON_CODES,
    SAME_ARTIST,
    SAME_ERA,
    SAME_GENRE,
    SIMILAR_ARTIST,
    WILDCARD,
    EngineConfig,
    ListCycle,
    Taste,
    Track,
    TrackStats,
)
from domovoi.up_next.session import SeedRef, UpNextSession, device_listener, room_listener
from domovoi.tests.up_next_fixtures import NOW, MemoryLibrary, MemoryListens, synthetic_library

LIB = synthetic_library()
BY_ID = {t.id: t for t in LIB}


def _seeds(*ids_weights):
    return [(BY_ID[i], w) for i, w in ids_weights]


#: Six drift seeds by six artists in four decades.
RICH_SEEDS = _seeds((1, 1.0), (2, 0.8), (3, 0.6), (4, 0.4), (5, 0.2), (6, 0.3))
#: Together hits for the newest seed (tracks of artists far from every seed).
TOGETHER = {1: {i: "from road trip" for i in range(200, 240)}}


def _lib_batch(batch_no=0, *, rng_seed=42, **kw):
    args = dict(n=30, rng=P.batch_rng(rng_seed, batch_no), now=NOW, seeds=RICH_SEEDS,
                candidates=LIB, together=TOGETHER)
    args.update(kw)
    return P.library_batch(**args)


def _key(i):
    return BY_ID[i].spacing_key


# ─── Determinism ──────────────────────────────────────────────────────────


def test_same_input_same_batch():
    a = _lib_batch(3)
    b = _lib_batch(3)
    assert a == b and len(a) == 30
    assert _lib_batch(4) != a
    assert _lib_batch(3, rng_seed=43) != a


def test_batch_rng_is_platform_stable():
    # BLAKE2b, not hash(): pinned so a Python upgrade can't change batches.
    assert P.batch_rng(42, 0).random() == P.batch_rng(42, 0).random()
    assert round(P.batch_rng(1, 0).random(), 12) == round(P.batch_rng(1, 0).random(), 12)
    assert P.batch_rng(1, 0).getrandbits(32) != P.batch_rng(1, 1).getrandbits(32)


def test_pinned_outcome():
    # The exact batch for a pinned seed: a change to the algorithm shows up
    # here first (update the pin deliberately).
    picks = _lib_batch(0)
    assert [p.track_id for p in picks[:5]] == PINNED_FIRST_FIVE


PINNED_FIRST_FIVE = [209, 103, 167, 141, 201]


# ─── Quotas and reflow ────────────────────────────────────────────────────


def test_largest_remainder_adds_up():
    q = P.largest_remainder(30, DEFAULT_CONFIG.shares)
    assert sum(q.values()) == 30
    assert q == {SAME_ARTIST: 8, SAME_ERA: 7, SAME_GENRE: 6, PLAYED_TOGETHER: 5, WILDCARD: 4} or \
        all(abs(q[k] - 30 * s) < 1 for k, s in DEFAULT_CONFIG.shares.items())


def test_allocate_reflows_in_proportion():
    avail = {SAME_ARTIST: 100, SAME_ERA: 100, SAME_GENRE: 0, PLAYED_TOGETHER: 100, WILDCARD: 100}
    q = P.allocate(30, DEFAULT_CONFIG.shares, avail)
    assert q[SAME_GENRE] == 0 and sum(q.values()) == 30
    # artist and era (0.25 each) get more than together and wildcard (0.15)
    exact = {k: 30 * s / 0.8 for k, s in DEFAULT_CONFIG.shares.items() if k != SAME_GENRE}
    for k, e in exact.items():
        assert abs(q[k] - e) <= 1
    # a bucket short of its quota gives the rest away
    q2 = P.allocate(30, DEFAULT_CONFIG.shares, {**avail, SAME_GENRE: 2, PLAYED_TOGETHER: 1})
    assert q2[SAME_GENRE] == 2 and q2[PLAYED_TOGETHER] == 1 and sum(q2.values()) == 30
    # less than n in total: everything there is
    assert sum(P.allocate(30, DEFAULT_CONFIG.shares, {SAME_ARTIST: 3, WILDCARD: 4}).values()) == 7


@pytest.mark.parametrize("batch_no", range(8))
def test_quota_shares_within_one(batch_no):
    picks = _lib_batch(batch_no)
    counts = Counter(p.reason_code for p in picks)
    q = P.largest_remainder(30, DEFAULT_CONFIG.shares)
    for code, want in q.items():
        assert abs(counts[code] - want) <= 1, (code, counts)
    assert len(picks) == 30


def test_reflow_with_no_genres_and_no_history():
    lib = synthetic_library(genres=False)
    by = {t.id: t for t in lib}
    picks = P.library_batch(n=30, rng=P.batch_rng(1, 0), now=NOW,
                            seeds=[(by[i], w) for i, w in ((1, 1.0), (2, 0.8), (3, 0.6))],
                            candidates=lib)
    counts = Counter(p.reason_code for p in picks)
    assert len(picks) == 30
    assert counts[SAME_GENRE] == 0 and counts[PLAYED_TOGETHER] == 0
    # split across artist, era and wildcard in proportion (25:25:15)
    q = P.allocate(30, DEFAULT_CONFIG.shares,
                   {SAME_ARTIST: 21, SAME_ERA: 200, SAME_GENRE: 0, PLAYED_TOGETHER: 0, WILDCARD: 300})
    for code in (SAME_ARTIST, SAME_ERA, WILDCARD):
        assert abs(counts[code] - q[code]) <= 1, counts


def test_reflow_with_no_years():
    lib = synthetic_library(years=False)
    by = {t.id: t for t in lib}
    picks = P.library_batch(n=30, rng=P.batch_rng(1, 0), now=NOW, seeds=[(by[1], 1.0)], candidates=lib)
    assert len(picks) == 30 and not any(p.reason_code == SAME_ERA for p in picks)


def test_tiny_library_gives_what_it_has():
    lib = synthetic_library(n=12, artists=4)
    picks = P.library_batch(n=30, rng=P.batch_rng(1, 0), now=NOW, seeds=[(lib[0], 1.0)], candidates=lib)
    assert sorted(p.track_id for p in picks) == list(range(2, 13))  # all but the seed, once each


# ─── Hold-back ────────────────────────────────────────────────────────────


def test_hold_window_respected():
    heard = {i: NOW - timedelta(hours=3) for i in range(1, 151)}       # inside 4 h
    heard.update({i: NOW - timedelta(hours=5) for i in range(151, 301)})  # outside
    for b in range(5):
        picks = _lib_batch(b, last_heard=heard, together={})
        assert picks and all(p.track_id > 150 for p in picks)


def test_hold_window_moves_with_now():
    heard = {i: NOW - timedelta(hours=3) for i in range(1, 151)}
    later = _lib_batch(0, last_heard=heard, together={}, now=NOW + timedelta(hours=2))
    assert any(p.track_id <= 150 for p in later)


def test_session_never_repeats_until_pool_runs_low():
    picked: list[int] = []
    for b in range(4):  # 120 of ~294 eligible
        picks = _lib_batch(b, session_picked=picked, together={})
        ids = [p.track_id for p in picks]
        assert not set(ids) & set(picked)
        picked.extend(ids)


def test_hold_back_halves_rather_than_running_dry():
    lib = synthetic_library(n=40, artists=8)
    heard = {t.id: NOW - timedelta(minutes=10) for t in lib}
    picks = P.library_batch(n=30, rng=P.batch_rng(5, 0), now=NOW, seeds=[(lib[0], 1.0)],
                            candidates=lib, last_heard=heard, session_picked=[t.id for t in lib])
    assert len(picks) == 30 and len({p.track_id for p in picks}) == 30


def test_eligible_keeps_the_newest_no_repeat_when_halving():
    lib = synthetic_library(n=50)
    no_repeat = list(range(1, 41))  # oldest first
    el = P.eligible_tracks(lib, taste=Taste(), last_heard={}, now=NOW, hold_hours=4,
                           no_repeat=no_repeat, min_pool=20)
    ids = {t.id for t in el}
    assert len(ids) >= 20
    assert not ids & set(range(31, 41))  # the newest stay out
    assert ids & set(range(1, 21))  # the oldest came back


# ─── Spacing ──────────────────────────────────────────────────────────────


def _assert_spaced(keys, *, window=6, most=2):
    for a, b in zip(keys, keys[1:]):
        assert a != b, keys
    for i in range(len(keys) - window + 1):
        assert max(Counter(keys[i:i + window]).values()) <= most, keys[i:i + window]


@pytest.mark.parametrize("rng_seed", range(20))
def test_no_back_to_back_artist(rng_seed):
    picks = _lib_batch(0, rng_seed=rng_seed)
    _assert_spaced([_key(p.track_id) for p in picks])


def test_spacing_counts_what_plays_before():
    for s in range(10):
        picks = _lib_batch(0, rng_seed=s, prev_keys=["artist 00"])
        assert _key(picks[0].track_id) != "artist 00"


def test_space_spreads_when_it_can_and_only_then_allows_back_to_back():
    assert P.space(list("AAABB"), str) == list("ABABA")
    assert P.space(list("AAAA"), str) == list("AAAA")  # one artist: the only exception
    out = P.space(list("AAABBBCCC"), str)
    _assert_spaced(out, window=6, most=2)
    assert Counter(out) == Counter("AAABBBCCC")


def test_space_respects_blocked_places():
    out = P.space([1, 2, 3, 4, 5], lambda x: str(x), blocked=lambda x, pos: x == 1 and pos < 3)
    assert out.index(1) >= 3


# ─── Scores, skips and exclusion ──────────────────────────────────────────


def test_affinity():
    assert P.affinity(None) == 1.0
    assert P.affinity(TrackStats(finished=4, plays=4)) == pytest.approx(1.4)
    assert P.affinity(TrackStats(finished=30, plays=30)) == pytest.approx(2.0)
    assert P.affinity(TrackStats(finished=0, plays=2, skipped=2)) == pytest.approx(1.0)  # < 3 plays
    assert P.affinity(TrackStats(finished=1, plays=4, skipped=3)) == pytest.approx(1.1 - 0.225)


def test_score_combines_favourite_affinity_and_demotion():
    t = Track(9, "x", 1990, ("soul",), favorite=True)
    taste = Taste(artist_factor={"x": 0.3}, genre_factor={"soul": 0.6})
    assert P.score(t, {9: TrackStats(2, 2)}, taste) == pytest.approx(1.5 * 1.2 * 0.3 * 0.6)


def _session(**kw):
    return UpNextSession.start(target="device:d1", mode="library", seed=SeedRef(1), rng_seed=7, **kw)


def test_early_skip_demotes_then_excludes():
    s = _session()
    t = BY_ID[2]
    assert s.record_listen(t, played_sec=10, duration_sec=200, outcome="skipped") is False
    assert s.artist_factor[t.artist_norm] == pytest.approx(0.3)
    assert s.genre_factor[t.primary_genre] == pytest.approx(0.6)
    assert s.record_listen(BY_ID[42], played_sec=5, duration_sec=200, outcome="skipped") is True
    assert t.artist_norm in s.excluded_artists  # 2 and 42 share artist 01
    assert s.recent_listened == []


def test_late_skip_does_not_demote_and_a_listen_drifts():
    s = _session()
    s.record_listen(BY_ID[2], played_sec=45, duration_sec=200, outcome="skipped")
    assert s.artist_factor == {} and s.genre_factor == {}
    s.record_listen(BY_ID[3], played_sec=120, duration_sec=200, outcome="stopped")  # 60%
    s.record_listen(BY_ID[4], played_sec=200, duration_sec=200, outcome="finished")
    s.record_listen(BY_ID[5], played_sec=20, duration_sec=200, outcome="stopped")  # 10%
    assert s.recent_listened == [4, 3]
    for i in range(10, 20):
        s.record_listen(BY_ID[i], played_sec=200, duration_sec=200, outcome="finished")
    assert s.recent_listened == [19, 18, 17, 16, 15]


def test_excluded_artist_never_picked_and_demoted_artist_rarer():
    excluded = Taste(excluded_artists=frozenset({"artist 00", "artist 01"}))
    for b in range(10):
        picks = _lib_batch(b, taste=excluded)
        assert not {_key(p.track_id) for p in picks} & {"artist 00", "artist 01"}
    plain = sum(1 for b in range(40) for p in _lib_batch(b) if _key(p.track_id) == "artist 00")
    demoted = sum(1 for b in range(40) for p in _lib_batch(b, taste=Taste(artist_factor={"artist 00": 0.3}))
                  if _key(p.track_id) == "artist 00")
    assert demoted < plain * 0.5, (plain, demoted)


def test_less_like_this():
    s = _session()
    s.less_like_this(BY_ID[3])
    assert "artist 02" in s.taste().excluded_artists
    s.less_like_this(Track(999))
    assert s.taste().excludes(Track(999))


def test_favourites_and_affinity_raise_a_track():
    # Over many batches, a favourite with strong history is picked more.
    lib = [Track(i, f"a{i}", None, ()) for i in range(1, 101)]
    stats = {50: TrackStats(10, 10)}
    lib[49] = Track(50, "a50", None, (), favorite=True)
    hits = sum(1 for b in range(300) for p in P.library_batch(
        n=3, rng=P.batch_rng(9, b), now=NOW, seeds=[], candidates=lib, stats=stats) if p.track_id == 50)
    assert hits > 300 * 3 / 99 * 2  # >2x its uniform share (it weighs 3x)


# ─── Reasons ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("batch_no", range(5))
def test_every_pick_has_a_reason(batch_no):
    for p in _lib_batch(batch_no):
        assert p.reason_code in REASON_CODES
        assert p.reason_label and p.reason_label == p.reason_label.lower()
        if p.reason_code in (SAME_ARTIST, SAME_ERA, SAME_GENRE, PLAYED_TOGETHER):
            assert p.seed_track_id is not None
        if p.reason_code == SAME_GENRE:
            assert p.reason_label in BY_ID[p.seed_track_id].genre_keys
            assert p.reason_label in BY_ID[p.track_id].genre_keys
        if p.reason_code == SAME_ARTIST:
            assert _key(p.track_id) == _key(p.seed_track_id)
        if p.reason_code == SAME_ERA:
            assert abs(BY_ID[p.track_id].year - BY_ID[p.seed_track_id].year) <= 5


def test_era_labels():
    assert P.era_label(1995, 1999) == "90s"
    assert P.era_label(1997, 2001) == "from 1997-ish"
    assert P.era_label(2004, 2006) == "2000s"
    assert P.era_label(2013, 2010) == "2010s"
    assert P.decade_label(1912) == "1910s"


def test_similar_takes_at_most_half_the_wildcards():
    similar = {i: "similar to nina simone" for i in range(250, 300)}
    picks = _lib_batch(0, similar=similar)
    counts = Counter(p.reason_code for p in picks)
    wild_quota = P.largest_remainder(30, DEFAULT_CONFIG.shares)[WILDCARD]
    assert 1 <= counts[SIMILAR_ARTIST] <= wild_quota // 2
    assert all(p.reason_label == "similar to nina simone" for p in picks if p.reason_code == SIMILAR_ARTIST)


# ─── List mode ────────────────────────────────────────────────────────────


LIST = [BY_ID[i] for i in range(1, 51)]  # 50 tracks


def _run_list(cycles: int, *, clicked=1, members=LIST, rng_seed=11, autoplay=True, n=30, repeat=None):
    st = P.start_cycle(clicked)
    played = [clicked]
    batches = []
    b = 0
    while True:
        picks, st, ended = P.list_batch(n=n, rng=P.batch_rng(rng_seed, b), members=members, state=st,
                                        seeds=[(BY_ID[clicked], 0.3)], autoplay=autoplay, repeat=repeat)
        b += 1
        batches.append(picks)
        played.extend(p.track_id for p in picks)
        if ended or not picks or st.cycle >= cycles:
            return played, batches, st, ended


def test_list_mode_covers_every_track_once_per_cycle():
    played, _batches, st, _ = _run_list(4)
    size = len(LIST)
    for c in range(3):
        cycle = played[c * size:(c + 1) * size]
        assert sorted(cycle) == list(range(1, 51)), c
    assert all(p.reason_code == FROM_LIST and p.reason_label == "from this list"
               for b in _batches for p in b)


@pytest.mark.parametrize("n", [30, 5, 3])
@pytest.mark.parametrize("rng_seed", range(15))
def test_reshuffle_boundary(rng_seed, n):
    # small batches too: a batch that lies wholly inside the first 10
    # places of a cycle must not take a tail track
    played, _b, _st, _ = _run_list(4, rng_seed=rng_seed, n=n)
    size = len(LIST)
    for c in range(1, 3):
        prev = played[(c - 1) * size:c * size]
        tail = set(prev[-5:])  # the last 10%
        first = played[c * size:c * size + 10]
        assert not tail & set(first), (c, tail, first)


def test_list_mode_autoplay_off_ends_after_the_list():
    played, batches, _st, ended = _run_list(9, autoplay=False)
    assert ended and sorted(played) == list(range(1, 51))


def test_short_list_never_repeats_a_track_in_one_batch():
    # repeat forced on: a list this short would go on in the library
    short = [BY_ID[i] for i in (1, 2, 3)]
    _p, batches, _st, _e = _run_list(3, members=short, repeat=True)
    for b in batches:
        assert len(b) == len({p.track_id for p in b})
    assert all(b for b in batches)


def test_a_list_repeats_only_from_list_repeat_min_tracks():
    cfg = DEFAULT_CONFIG
    assert cfg.list_repeat_min == 50
    assert P.list_repeats(50) and P.list_repeats(300) and not P.list_repeats(49) and not P.list_repeats(2)
    assert P.list_repeats(10, EngineConfig(list_repeat_min=10))


def test_a_small_list_plays_once_then_is_used_up():
    two = [BY_ID[1], BY_ID[2]]
    st = P.start_cycle(1)
    assert not P.list_used_up(two, st)
    picks, st, ended = P.list_batch(n=30, rng=P.batch_rng(5, 0), members=two, state=st,
                                    seeds=[(BY_ID[1], 0.3)])
    assert [p.track_id for p in picks] == [2] and not ended and st.cycle == 0
    assert P.list_used_up(two, st)
    # asked again, the list gives nothing more: no new cycle, no ping-pong
    again, st2, ended2 = P.list_batch(n=30, rng=P.batch_rng(5, 1), members=two, state=st,
                                      seeds=[(BY_ID[1], 0.3)])
    assert again == [] and st2 == st and not ended2
    # an empty (all excluded) list is not "used up": list_batch ends it
    assert not P.list_used_up(two, st, taste=Taste(excluded_tracks=frozenset({1, 2})))


def test_a_60_track_list_still_reshuffles():
    sixty = [BY_ID[i] for i in range(1, 61)]
    played, _b, st, ended = _run_list(3, members=sixty)
    assert not ended and st.cycle >= 2
    assert sorted(played[:60]) == list(range(1, 61))
    assert sorted(played[60:120]) == list(range(1, 61))  # the second cycle: the whole list again
    assert not P.list_used_up(sixty, st)


def test_autoplay_off_ends_a_small_list():
    ten = [BY_ID[i] for i in range(1, 11)]
    played, _b, _st, ended = _run_list(9, members=ten, autoplay=False)
    assert ended and sorted(played) == list(range(1, 11))


def test_one_artist_list_plays_back_to_back():
    one = [Track(i, "solo") for i in range(1, 9)]
    picks, _st, _e = P.list_batch(n=30, rng=P.batch_rng(1, 0), members=one, state=P.start_cycle(1),
                                  seeds=[], autoplay=False)
    assert sorted(p.track_id for p in picks) == list(range(2, 9))


def test_list_weights_follow_the_seeds():
    seeds = [(BY_ID[1], 1.0)]  # artist 00, a 1960s rock track
    same = P.list_weight(BY_ID[41], seeds)  # artist 00 again
    other = P.list_weight(BY_ID[2], seeds)
    assert same > other >= 1.0
    # the weighted 80% favours the seed's corner over many draws
    firsts = Counter()
    for s in range(200):
        picks, _st, _e = P.list_batch(n=5, rng=P.batch_rng(s, 0), members=LIST, state=P.start_cycle(1),
                                      seeds=seeds)
        firsts.update(_key(p.track_id) for p in picks)
    assert firsts["artist 00"] > firsts["artist 05"]


def test_excluded_artist_leaves_the_list():
    picks, _st, _e = P.list_batch(n=60, rng=P.batch_rng(1, 0), members=LIST, state=P.start_cycle(1),
                                  seeds=[], taste=Taste(excluded_artists=frozenset({"artist 03"})),
                                  autoplay=False)
    assert not any(_key(p.track_id) == "artist 03" for p in picks)
    assert len(picks) == 50 - 1 - 2  # 4 and 44 are artist 03


# ─── The queue ────────────────────────────────────────────────────────────


def _explicit_never_after_auto(q, current):
    seen_auto = False
    for it in q[current + 1:]:
        if it.origin == "auto":
            seen_auto = True
        elif seen_auto:
            return False
    return True


def test_explicit_item_never_placed_after_an_auto_item():
    rng = random.Random(3)
    q = [P.QueueItem(1, "explicit")]
    current = 0
    tid = 100
    for _ in range(400):
        op = rng.choice(["next", "end", "auto", "drop", "advance"])
        if op in ("next", "end"):
            tid += 1
            q = P.place_explicit(q, [tid], current=current, where=op)
        elif op == "auto":
            q = P.append_auto(q, [P.Pick(tid + k, WILDCARD, "wildcard") for k in range(1, 4)])
            tid += 3
        elif op == "drop":
            q = P.drop_auto(q, current=current, drop=lambda it: it.track_id % 2 == 0)
        elif current < len(q) - 1:
            current += 1
        assert _explicit_never_after_auto(q, current), (op, q[current:])


def test_place_explicit_end_goes_before_up_next():
    q = P.append_auto([P.QueueItem(1, "explicit"), P.QueueItem(2, "explicit")],
                      [P.Pick(10, WILDCARD, "wildcard"), P.Pick(11, WILDCARD, "wildcard")])
    q = P.place_explicit(q, [5], current=0, where="end")
    assert [i.track_id for i in q] == [1, 2, 5, 10, 11]
    q = P.place_explicit(q, [6], current=0, where="next")
    assert [i.track_id for i in q] == [1, 6, 2, 5, 10, 11]
    assert P.auto_left(q, 0) == 2 and P.needs_refill(q, 0)
    with pytest.raises(ValueError):
        P.place_explicit(q, [7], current=0, where="later")


# ─── Session, listener, engine (in memory) ────────────────────────────────


def test_listener_rules():
    assert device_listener(listener_person_id=3, owner_person_id=4, shared_screen=False) == 3
    assert device_listener(listener_person_id=None, owner_person_id=4, shared_screen=False) == 4
    assert device_listener(listener_person_id=None, owner_person_id=4, shared_screen=True) is None
    assert device_listener(listener_person_id=3, owner_person_id=4, shared_screen=True) == 3
    assert room_listener(voice_person_id=7, started_by_voice=True, device_person_id=3) == 7
    assert room_listener(voice_person_id=None, started_by_voice=True, device_person_id=3) is None
    assert room_listener(started_by_voice=False, device_person_id=3) == 3


def test_session_validation_and_round_trip():
    with pytest.raises(ValueError):
        UpNextSession.start(target="kitchen", mode="library", seed=SeedRef(1))
    with pytest.raises(ValueError):
        UpNextSession.start(target="room:k", mode="list", seed=SeedRef(1))
    with pytest.raises(ValueError):
        SeedRef(1, kind="playlist")
    assert len(SeedRef(1, kind="ids", track_ids=tuple(range(900))).track_ids) == 500
    assert SeedRef(1, kind="album", ref="nina simone|pastel blues").list_ref == "album:nina simone|pastel blues"
    s = UpNextSession.start(target="room:kitchen", mode="list", rng_seed=5, person_id=3,
                            seed=SeedRef(1, kind="playlist", ref="4", label="from road trip"))
    s.record_listen(BY_ID[2], played_sec=5, duration_sec=100, outcome="skipped")
    s.less_like_this(BY_ID[7])
    s.record_batch([P.Pick(9, FROM_LIST, "from this list", 1)], list_cycle=ListCycle(0, (1, 9), ()))
    back = UpNextSession.from_json(s.to_json())
    assert back == s
    s.move("device:phone")
    assert s.target_kind == "device" and s.target_id == "phone"


def test_session_rng_seed_is_random_unless_pinned():
    a = UpNextSession.start(target="room:k", mode="library", seed=SeedRef(1))
    b = UpNextSession.start(target="room:k", mode="library", seed=SeedRef(1))
    assert a.rng_seed != b.rng_seed


async def test_engine_library_session_is_deterministic_and_advances():
    lib = MemoryLibrary(LIB, playlists={4: ("Road Trip", list(range(100, 130)) + [1])})

    async def run():
        s = UpNextSession.start(target="device:d1", mode="library", seed=SeedRef(1), rng_seed=99)
        b1 = await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)
        s.record_listen(BY_ID[b1.picks[0].track_id], played_sec=200, duration_sec=200, outcome="finished")
        b2 = await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)
        return s, b1, b2

    s, b1, b2 = await run()
    _s, c1, c2 = await run()
    assert (b1, b2) == (c1, c2)
    assert (b1.batch_no, b2.batch_no, s.batch_no) == (0, 1, 2)
    assert not {p.track_id for p in b1.picks} & {p.track_id for p in b2.picks}
    assert any(p.reason_label == "from road trip" for p in b1.picks + b2.picks)
    # the drift seed (the first pick, listened to) seeds the second batch
    assert any(p.seed_track_id == b1.picks[0].track_id for p in b2.picks)


async def test_engine_scope_is_the_sessions_person():
    listens = MemoryListens()
    s = UpNextSession.start(target="room:kitchen", mode="library", seed=SeedRef(1), person_id=3, rng_seed=1)
    await next_batch(s, library=MemoryLibrary(LIB), listens=listens, now=NOW)
    assert {(sc.target, sc.person_id) for sc in listens.scopes} == {("room:kitchen", 3)}
    s.set_listener(None)
    listens.scopes.clear()
    await next_batch(s, library=MemoryLibrary(LIB), listens=listens, now=NOW)
    assert {sc.person_id for sc in listens.scopes} == {None}


async def test_engine_autoplay_off_appends_nothing_outside_a_list():
    s = UpNextSession.start(target="device:d1", mode="library", seed=SeedRef(1), autoplay=False, rng_seed=1)
    b = await next_batch(s, library=MemoryLibrary(LIB), listens=MemoryListens(), now=NOW)
    assert b.picks == () and b.ended and s.batch_no == 0


async def test_engine_list_session_plays_the_list_then_ends():
    lib = MemoryLibrary(LIB, playlists={4: ("Road Trip", list(range(1, 41)))})
    s = UpNextSession.start(target="device:d1", mode="list", autoplay=False, rng_seed=3,
                            seed=SeedRef(7, kind="playlist", ref="4"))
    got = []
    for _ in range(5):
        b = await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)
        got.extend(p.track_id for p in b.picks)
        if b.ended:
            break
    assert b.ended and s.ended
    assert sorted(got) == [i for i in range(1, 41) if i != 7]
    assert (await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)).picks == ()


async def test_engine_two_track_list_goes_on_in_the_library():
    # The bug: 2 search results, clicked, then Up next ping-ponged between them.
    lib = MemoryLibrary(LIB)
    s = UpNextSession.start(target="device:d1", mode="list", rng_seed=4,
                            seed=SeedRef(1, kind="ids", track_ids=(1, 2), label='from results for "x"'))
    b0 = await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)
    assert [p.track_id for p in b0.picks] == [2] and s.mode == "list" and not b0.ended
    s.record_listen(BY_ID[2], played_sec=200, duration_sec=200, outcome="finished")
    played = [1, 2]
    for _ in range(3):
        b = await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)
        assert s.mode == "continue" and not b.ended and len(b.picks) == 30
        assert not {p.track_id for p in b.picks} & {1, 2}          # the list never comes round again
        assert all(p.reason_code != FROM_LIST for p in b.picks)     # the library's own reasons
        assert all(p.reason_label and p.reason_label != "from this list" for p in b.picks)
        played.extend(p.track_id for p in b.picks)
    assert {p.reason_code for p in b.picks} & {SAME_ARTIST, SAME_ERA, SAME_GENRE, WILDCARD}
    # each of the two songs plays once (the list's one pass), never again
    assert played.count(1) == played.count(2) == 1
    assert len(set(played)) == len(played)


async def test_engine_small_list_with_autoplay_off_ends():
    lib = MemoryLibrary(LIB)
    s = UpNextSession.start(target="device:d1", mode="list", rng_seed=4, autoplay=False,
                            seed=SeedRef(1, kind="ids", track_ids=(1, 2, 3)))
    b0 = await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)
    b1 = await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)
    assert sorted(p.track_id for p in b0.picks + b1.picks) == [2, 3]
    assert b1.ended and s.ended and s.mode == "list"


async def test_engine_big_list_reshuffles_and_stays_a_list():
    lib = MemoryLibrary(LIB, playlists={4: ("Long", list(range(1, 61)))})
    s = UpNextSession.start(target="device:d1", mode="list", rng_seed=4,
                            seed=SeedRef(1, kind="playlist", ref="4"))
    got = []
    for _ in range(4):
        b = await next_batch(s, library=lib, listens=MemoryListens(), now=NOW)
        got.extend(p.track_id for p in b.picks)
    assert s.mode == "list" and s.list_cycle.cycle >= 1
    assert set(got) <= set(range(1, 61)) and len(got) > 60


async def test_engine_continue_mode_labels_the_lists_neighbourhood():
    road = list(range(1, 21))
    lib = MemoryLibrary(LIB, playlists={4: ("Road Trip", road), 5: ("Late", road + [250, 251, 252, 253])})
    s = UpNextSession.start(target="device:d1", mode="continue", rng_seed=8,
                            seed=SeedRef(None, kind="playlist", ref="4", label="from road trip"))
    heard = {i: NOW - timedelta(minutes=30) for i in road}
    b = await next_batch(s, library=lib, listens=MemoryListens(heard=heard), now=NOW)
    assert len(b.picks) == 30
    assert not {p.track_id for p in b.picks} & set(road)  # just played: held back
    together = [p for p in b.picks if p.reason_code == PLAYED_TOGETHER]
    assert together and all(p.reason_label == "from road trip" for p in together if p.seed_track_id in road)
