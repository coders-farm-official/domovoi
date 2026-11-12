"""One refill: read the inputs, pick, advance the session (§2.2).

:func:`next_batch` is what the web (device sessions, P3) and the core
(rooms, P4) call. It reads through a :class:`~.data.LibrarySource` and a
:class:`~.data.ListenSource`, so tests can hand it in-memory ones, and
it changes only the session object it was given (the caller saves it).

* ``list`` mode reads the list as it is now and draws the next part of
  the cycle (:func:`~.pick.list_batch`). Autoplay off still plays the
  rest of the list, then ends. With autoplay on, a list shorter than
  ``list_repeat_min`` (50) plays through once, then the session switches
  to ``continue`` for good (``sess.mode`` changes; the caller saves it)
  instead of reshuffling the same few songs.
* ``library`` and ``continue`` modes read the candidates near the drift
  seeds, a wildcard sample, the playlist and listening-session
  neighbours, the listener's stats and the hold-back, then fill by quota
  (:func:`~.pick.library_batch`). Autoplay off appends nothing.
  ``continue`` (a list played on purpose that is ending) spreads the
  original seed's weight over up to five of the list's tracks and takes
  the together bucket from the list's own neighbourhood first, labelled
  with the list (``from road trip``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from .data import LibrarySource, ListenSource, Scope
from .model import DEFAULT_CONFIG, Batch, EngineConfig, Track
from .pick import (
    ORIGINAL_SEED_WEIGHT,
    batch_rng,
    drift_weights,
    library_batch,
    list_batch,
    list_used_up,
)
from .session import UpNextSession

#: Continue mode spreads the original seed over this many list tracks.
CONTINUE_CONTEXT_SEEDS = 5


async def next_batch(
    sess: UpNextSession,
    *,
    library: LibrarySource,
    listens: ListenSource,
    now: datetime,
    prev_keys: Sequence[str] = (),
    config: EngineConfig = DEFAULT_CONFIG,
) -> Batch:
    """The session's next batch; ``sess`` is advanced (``batch_no``, the
    no-repeat memory, the list cycle). ``prev_keys``: the artists
    (``artist_norm``) of what plays just before the batch, newest last,
    for spacing across the seam."""
    batch_no = sess.batch_no
    if sess.ended:
        return Batch(batch_no, (), True)
    if not sess.autoplay and sess.mode != "list":
        return Batch(batch_no, (), True)
    rng = batch_rng(sess.rng_seed, batch_no)
    salt = format(rng.getrandbits(32), "08x")
    scope = Scope(sess.target, sess.person_id)
    n = config.up_next_batch_size

    members: list[Track] | None = None
    if sess.mode == "list":
        members = await library.list_members(sess.seed)
        if sess.autoplay and list_used_up(members, sess.list_cycle, taste=sess.taste(), config=config):
            # A list too short to repeat played through: carry on in the
            # library like a list played on purpose that ended (below). The
            # clicked song joins the no-repeat memory beside the list's picks.
            sess.mode = "continue"
            known_picked = set(sess.picked)
            sess.picked.extend(i for i in sess.list_cycle.done if i not in known_picked)
    if sess.mode == "list":
        assert members is not None
        weights = drift_weights(sess.recent_listened, sess.seed.track_id)
        known = {t.id: t for t in members}
        missing = [tid for tid, _ in weights if tid not in known]
        if missing:
            known.update(await library.tracks(missing))
        seeds = [(known[tid], w) for tid, w in weights if tid in known]
        stats = await listens.stats(scope, [t.id for t in members], now=now)
        picks, cycle, ended = list_batch(
            n=n, rng=rng, members=members, state=sess.list_cycle, seeds=seeds, stats=stats,
            taste=sess.taste(), autoplay=sess.autoplay, seed_track_id=sess.seed.track_id,
            prev_keys=prev_keys, config=config,
        )
        sess.record_batch(picks, list_cycle=cycle, ended=ended, config=config)
        return Batch(batch_no, tuple(picks), ended)

    # library / continue
    original = sess.seed.track_id
    context: list[Track] = []
    if sess.mode == "continue":
        if members is None:
            members = await library.list_members(sess.seed)
        if members:
            ordered = sorted(members, key=lambda t: t.id)
            context = rng.sample(ordered, min(CONTINUE_CONTEXT_SEEDS, len(ordered)))
    weights = dict(drift_weights(sess.recent_listened, None if context else original))
    for t in context:
        weights[t.id] = weights.get(t.id, 0.0) + ORIGINAL_SEED_WEIGHT / len(context)
    known = {t.id: t for t in context}
    known.update(await library.tracks([tid for tid in weights if tid not in known]))
    seeds = [(known[tid], w) for tid, w in weights.items() if tid in known]
    seed_tracks = [t for t, _ in seeds]
    seed_ids = [t.id for t in seed_tracks]

    near = await library.near(seed_tracks, salt=salt, era_years=config.era_years)
    wild = await library.wildcards(salt=salt, rng=rng)
    pl_hits, pl_tracks = await library.playlist_together(seed_ids, salt=salt)
    ls_hits = await listens.together(scope, seed_ids, now=now)
    similar = await library.similar(seed_tracks)

    context_ids = {t.id for t in context}
    together: dict[int, dict[int, str]] = {}
    for hits in (ls_hits, pl_hits):  # a playlist's name beats "played together"
        for sid, m in hits.items():
            for tid, lab in m.items():
                together.setdefault(sid, {})[tid] = lab
    if sess.seed.label:
        for sid in context_ids & together.keys():
            together[sid] = {tid: sess.seed.label for tid in together[sid]}

    cands: dict[int, Track] = {t.id: t for t in (*near, *wild, *pl_tracks)}
    need = {tid for m in ls_hits.values() for tid in m} | set(similar)
    need -= cands.keys()
    if need:
        cands.update(await library.tracks(need))
    stats = await listens.stats(scope, cands.keys(), now=now)
    heard = await listens.last_heard(scope, now=now, hours=config.up_next_hold_hours)

    picks = library_batch(
        n=n, rng=rng, now=now, seeds=seeds, candidates=cands.values(),
        wildcard_ids={t.id for t in wild}, together=together, prefer_together_seeds=context_ids,
        similar=similar, stats=stats, last_heard=heard, session_picked=sess.picked,
        taste=sess.taste(), prev_keys=prev_keys, config=config,
    )
    sess.record_batch(picks, config=config)
    return Batch(batch_no, tuple(picks), not picks)
