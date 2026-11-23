"""Faces and people (1.5.0, design MEDIA-UNDERSTANDING §6; owner answers
2026-11-22). Off by default; an admin turns it on.

**What is stored.** Per face (``media_faces``): the item, the time in a
video (``at_s`` to ``end_s``: one appearance, from the first sighting to
the last; both None for a photo), the box as fractions of the picture,
the detector's quality figure, the 128-number SFace embedding (512 bytes,
float32, as voice profiles are kept) and where it is grouped. Groups
(``face_groups``) link to the same ``people`` rows voice ID uses; an
unnamed group is nobody's. Nothing leaves the server, there is no export
of any of it (backups include it, as ordinary tables), and no route ever
answers with an embedding or a centroid.

**Grouping** (:func:`assign`, as each face arrives):

1. A face that matches the denylist (``face_denylist``: people who said
   "forget my face") is not stored at all.
2. It is suggested for the closest NAMED person whose centroid (of their
   confirmed faces) is at least :data:`NAMED_MATCH` similar, unless the
   face was already said not to be them. Suggested, not confirmed.
3. Otherwise it joins the closest unnamed group at :data:`GROUP_MATCH`.
4. Otherwise it starts a new unnamed group.

Each night :func:`regroup` takes the faces nobody confirmed (the newest
:data:`REGROUP_MAX_FACES`) and does it again over that set (connected
components of the pairs above :data:`GROUP_MATCH`: a blocked neighbour
search in a worker thread with the maths library on one thread, pausing
while a voice turn runs; the night is marked done before it starts):
**confirmed faces never move.** A component keeps the group most of it was in, so
groups keep their ids and the household's "hide".

**Strangers.** Unnamed groups are nobody's and the household is never
asked about them (there is no "who is this?" anywhere). Groups of fewer
than :data:`SHOWN_MIN_FACES` faces aren't listed unless asked ("show
more"); hidden groups are listed for admins only.

**Training** (:mod:`web.backend.api.faces` holds the tiers): name a group
(link it to a person: an existing one or a new, face-only ``people``
row), confirm a face, "not this person" (a negative for that person,
never suggested for them again), hide, merge, split. Every one feeds the
next :func:`assign` and :func:`regroup`: centroids are recomputed, a
rejection is kept on the face, a split face is pinned where it was put.

**Forget my face** (:func:`forget_person`): every face row and group of
the person goes, and one averaged embedding tied to nobody goes on the
denylist, so new photos of them aren't grouped or suggested (as
``voice_denylist`` does for voices). Any remaining face that matches the
signature goes too. Their photos stay.

**Faces off** (:func:`delete_all`): every face and group goes; turning
faces on again starts from zero. The denylist stays: it is the people who
asked not to be learned, tied to no one, and keeping it is what makes
their "forget my face" hold if faces are turned on again.

Writes happen only while the switch is on, checked under a share lock on
the switch row in the same transaction (:func:`_faces_on_locked`), so a
face stored at the moment an admin turns faces off is deleted with the
rest rather than left behind.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import numpy as np
from sqlalchemy import text

from domovoi.media_understanding import face_models, switches
from domovoi.media_understanding.face_engine import EMBED_DIM, FaceEngine, Found, load_picture, unit

log = logging.getLogger(__name__)

MODEL = face_models.MODEL_TAG

#: SFace's documented same-person cosine threshold is 0.363; these start a
#: margin above it (design §6: tune on real household photos before release).
NAMED_MATCH = 0.45
GROUP_MATCH = 0.40
#: The denylist is matched more loosely: a near miss is a face not learned.
DENY_MATCH = 0.363
#: Unnamed groups smaller than this are behind "show more".
SHOWN_MIN_FACES = 3
#: Video: at most one frame every 2 s gets the detector (design §2), and a
#: face seen again within 30 s of its last sighting is the same appearance
#: (one row from its first moment, ``at_s``, to its last, ``end_s``, the
#: best-quality look kept).
VIDEO_MIN_GAP_S = 2.0
APPEARANCE_GAP_S = 30.0
APPEARANCE_MATCH = 0.5
REGROUP_CHUNK = 2048

#: household_settings row for the faces bookkeeping (not a switch): the
#: model fetch's last error, whether the library was queued again for
#: faces once the models arrived, and the night of the last re-group.
STATE_KEY = "media_faces_state"

ANALYSER = "faces"
CODE_MODELS = "faces-models-missing"


class FacesOff(Exception):
    """Faces are turned off: nothing is stored."""


class Refused(ValueError):
    """A training action that can't be done as asked (409 / 404 / 422)."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# ─── vectors ─────────────────────────────────────────────────────────────


def to_bytes(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).reshape(-1).tobytes()


def from_bytes(b: bytes | memoryview | None) -> np.ndarray | None:
    if b is None:
        return None
    a = np.frombuffer(bytes(b), dtype=np.float32)
    return a if a.shape == (EMBED_DIM,) else None


def mean_unit(vs: Iterable[np.ndarray]) -> np.ndarray | None:
    m = [v for v in vs if v is not None]
    if not m:
        return None
    return unit(np.mean(np.stack(m), axis=0))


# ─── bookkeeping ─────────────────────────────────────────────────────────


async def read_state(s: Any) -> dict[str, Any]:
    v = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                         {"k": STATE_KEY})).scalar()
    if isinstance(v, str):
        v = json.loads(v)
    return dict(v) if isinstance(v, dict) else {}


async def write_state(s: Any, **changes: Any) -> dict[str, Any]:
    st = {**await read_state(s), **changes}
    await s.execute(text(
        "INSERT INTO household_settings (key, value) VALUES (:k, CAST(:v AS JSONB)) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": STATE_KEY, "v": json.dumps(st)})
    return st


async def _faces_on_locked(s: Any) -> bool:
    """The switch, read under a share lock on its row: an admin's "faces
    off" (which writes that row, then deletes) waits for this transaction,
    or this one waits for it and sees faces off."""
    v = (await s.execute(text(
        "SELECT value FROM household_settings WHERE key = :k FOR SHARE"), {"k": switches.KEY})).scalar()
    return bool(switches.normalize(v)["faces"])


# ─── groups and centroids ────────────────────────────────────────────────


@dataclass
class Group:
    id: int
    person_id: int | None
    hidden: bool
    centroid: np.ndarray


async def _groups(s: Any) -> list[Group]:
    rows = (await s.execute(text(
        "SELECT id, person_id, hidden, centroid FROM face_groups WHERE model = :m AND centroid IS NOT NULL"),
        {"m": MODEL})).all()
    out = []
    for r in rows:
        c = from_bytes(r[3])
        if c is not None:
            out.append(Group(int(r[0]), r[1], bool(r[2]), c))
    return out


async def _denylist(s: Any) -> np.ndarray:
    rows = (await s.execute(text("SELECT embedding FROM face_denylist WHERE model = :m"), {"m": MODEL})).all()
    vs = [v for v in (from_bytes(r[0]) for r in rows) if v is not None]
    return np.stack(vs) if vs else np.zeros((0, EMBED_DIM), dtype=np.float32)


def denied(deny: np.ndarray, emb: np.ndarray) -> bool:
    return bool(len(deny)) and float((deny @ emb).max()) >= DENY_MATCH


async def refresh(s: Any, group_ids: Iterable[int | None]) -> None:
    """Recompute these groups' centroids (a named group: its confirmed
    faces, else all of them; an unnamed one: all of them) and delete any
    group left with no face."""
    ids = sorted({int(g) for g in group_ids if g is not None})
    if not ids:
        return
    rows = (await s.execute(text(
        "SELECT g.id, g.person_id, f.embedding, f.confirmed FROM face_groups g "
        "LEFT JOIN media_faces f ON f.group_id = g.id WHERE g.id = ANY(:ids)"), {"ids": ids})).all()
    by: dict[int, tuple[Any, list[np.ndarray], list[np.ndarray]]] = {}
    for gid, pid, emb, conf in rows:
        _p, all_, confirmed = by.setdefault(int(gid), (pid, [], []))
        v = from_bytes(emb)
        if v is not None:
            all_.append(v)
            if conf:
                confirmed.append(v)
    empty = []
    for gid, (pid, all_, confirmed) in by.items():
        if not all_:
            empty.append(gid)
            continue
        c = mean_unit(confirmed if (pid is not None and confirmed) else all_)
        await s.execute(text("UPDATE face_groups SET centroid = :c, model = :m, updated_at = now() WHERE id = :g"),
                        {"c": to_bytes(c), "m": MODEL, "g": gid})
    if empty:
        await s.execute(text("DELETE FROM face_groups WHERE id = ANY(:ids)"), {"ids": empty})


async def _new_group(s: Any, centroid: np.ndarray, *, person_id: int | None = None,
                     hidden: bool = False) -> int:
    return int((await s.execute(text(
        "INSERT INTO face_groups (person_id, hidden, centroid, model) VALUES (:p, :h, :c, :m) RETURNING id"),
        {"p": person_id, "h": hidden, "c": to_bytes(centroid), "m": MODEL})).scalar())


async def _person_group(s: Any, person_id: int, centroid: np.ndarray | None = None) -> int:
    """The person's group (the oldest, when an older install made more),
    made when they have none."""
    gid = (await s.execute(text(
        "SELECT id FROM face_groups WHERE person_id = :p AND model = :m ORDER BY id LIMIT 1"),
        {"p": person_id, "m": MODEL})).scalar()
    if gid is not None:
        return int(gid)
    return await _new_group(s, centroid if centroid is not None else np.zeros(EMBED_DIM, np.float32),
                            person_id=person_id)


def choose(groups: list[Group], emb: np.ndarray, rejected: Iterable[int] = ()) -> tuple[Group | None, bool]:
    """Where a free face goes: ``(group, suggested_for_its_person)``, or
    ``(None, False)`` for a new group. Pure."""
    rej = set(rejected)
    named = [g for g in groups if g.person_id is not None and g.person_id not in rej]
    if named:
        sims = np.stack([g.centroid for g in named]) @ emb
        i = int(np.argmax(sims))
        if sims[i] >= NAMED_MATCH:
            return named[i], True
    unnamed = [g for g in groups if g.person_id is None]
    if unnamed:
        sims = np.stack([g.centroid for g in unnamed]) @ emb
        i = int(np.argmax(sims))
        if sims[i] >= GROUP_MATCH:
            return unnamed[i], False
    return None, False


async def assign(s: Any, face_id: int, emb: np.ndarray, rejected: Iterable[int] = (),
                 groups: list[Group] | None = None) -> int:
    """Group one free face (steps 2-4 above); its group id."""
    groups = groups if groups is not None else await _groups(s)
    g, suggested = choose(groups, emb, rejected)
    if g is None:
        gid = await _new_group(s, emb)
        groups.append(Group(gid, None, False, emb))
        pid = None
    else:
        gid, pid = g.id, (g.person_id if suggested else None)
        if not suggested:
            n = int((await s.execute(text("SELECT count(*) FROM media_faces WHERE group_id = :g"),
                                     {"g": gid})).scalar() or 0)
            g.centroid = unit(g.centroid * max(n, 1) + emb)
            await s.execute(text("UPDATE face_groups SET centroid = :c, updated_at = now() WHERE id = :g"),
                            {"c": to_bytes(g.centroid), "g": gid})
    await s.execute(text(
        "UPDATE media_faces SET group_id = :g, person_id = :p, confirmed = false WHERE id = :f"),
        {"g": gid, "p": pid, "f": face_id})
    return gid


async def store(s: Any, item_id: int, found: list[Found], *, at_s: float | None = None) -> list[int]:
    """Save a picture's (or a video frame's) faces and group each one.
    Denylisted faces are dropped. :class:`FacesOff` when the switch is off."""
    if not await _faces_on_locked(s):
        raise FacesOff()
    if not found:
        return []
    deny = await _denylist(s)
    groups = await _groups(s)
    ids = []
    for f in found:
        emb = unit(f.embedding)
        if denied(deny, emb):
            continue
        fid = int((await s.execute(text(
            "INSERT INTO media_faces (item_id, at_s, end_s, box, quality, embedding, model) "
            "VALUES (:i, :t, :t, :b, :q, :e, :m) RETURNING id"),
            {"i": item_id, "t": at_s, "b": [float(x) for x in f.box], "q": float(f.quality),
             "e": to_bytes(emb), "m": MODEL})).scalar())
        await assign(s, fid, emb, groups=groups)
        ids.append(fid)
    return ids


# ─── the nightly re-group ────────────────────────────────────────────────


#: Faces one night's re-group takes at most (the newest unconfirmed ones);
#: the rest keep the groups :func:`assign` gave them until a later night.
REGROUP_MAX_FACES = 20000
#: Rows written per statement while the re-group applies its plan.
REGROUP_WRITE_BATCH = 500
#: How long the worker sleeps between checks while a voice turn runs.
REGROUP_VOICE_POLL_SEC = 0.25


def _voice_busy() -> bool:
    try:
        from domovoi import voice_activity

        return voice_activity.busy()
    except Exception:  # noqa: BLE001 - no voice front end: never busy
        return False


def _yield_to_voice(pause: Any) -> None:
    """In the worker thread, between blocks: sleep while a voice turn runs."""
    import time

    if pause is None:
        return
    while pause():
        time.sleep(REGROUP_VOICE_POLL_SEC)


def components(E: np.ndarray, threshold: float = GROUP_MATCH, chunk: int = REGROUP_CHUNK,
               *, pause: Any = None) -> np.ndarray:
    """Connected components of "cosine at least ``threshold``" over the
    rows of ``E`` (unit vectors): a label per row (the smallest index in
    its component).

    A blocked neighbour search: every pair of ``chunk``-row blocks is
    compared once (memory ``chunk x chunk``, never ``chunk x n``), and its
    pairs are merged into a union-find by hooking roots onto the smaller
    root, vectorised. Pure CPU: call it in a worker thread
    (:func:`regroup` does, with the maths library held to one thread).
    ``pause``, when given, is asked between blocks and the work sleeps
    while it says yes (a voice turn)."""
    n = len(E)
    parent = np.arange(n)
    if n == 0:
        return parent
    E = np.ascontiguousarray(E, dtype=np.float32)
    chunk = max(1, int(chunk))

    def roots(ix: np.ndarray) -> np.ndarray:
        r = parent[ix]
        while True:
            nr = parent[r]
            if np.array_equal(nr, r):
                return r
            r = nr

    def compress() -> np.ndarray:
        p = parent
        while True:
            nxt = p[p]
            if np.array_equal(nxt, p):
                return p
            p = nxt

    for a in range(0, n, chunk):
        for b in range(a, n, chunk):
            _yield_to_voice(pause)
            sims = E[a:a + chunk] @ E[b:b + chunk].T
            ii, jj = np.nonzero(sims >= threshold)
            if not len(ii):
                continue
            u = ii + a
            v = jj + b
            keep = u != v
            u, v = u[keep], v[keep]
            while len(u):
                ru, rv = roots(u), roots(v)
                diff = ru != rv
                if not diff.any():
                    break
                u, v, ru, rv = u[diff], v[diff], ru[diff], rv[diff]
                np.minimum.at(parent, np.maximum(ru, rv), np.minimum(ru, rv))
            # Compress so later blocks walk short paths.
            parent = compress()
    return compress()


def _one_blas_thread() -> Any:
    """A context that holds numpy's maths library to one thread (the core's
    other work keeps its cores); a no-op without threadpoolctl."""
    import contextlib

    try:
        from threadpoolctl import threadpool_limits
    except Exception:  # noqa: BLE001
        return contextlib.nullcontext()
    return threadpool_limits(limits=1, user_api="blas")


@dataclass
class _Plan:
    gone: list[int]
    suggest: list[tuple[int, int, int]]          # (face, group, person)
    free: list[tuple[int, np.ndarray, Any]]
    labels: np.ndarray


def plan_regroup(rows: list[Any], deny: np.ndarray, named: list[Group], *, pause: Any = None) -> _Plan:
    """The re-group's maths, pure (run in a worker thread): which faces go
    (denied), which are suggested for a named person, and the components of
    the rest."""
    with _one_blas_thread():
        gone: list[int] = []
        faces: list[tuple[int, np.ndarray, Any, list[int]]] = []
        for k, (fid, emb, gid, rej) in enumerate(rows):
            if k % REGROUP_WRITE_BATCH == 0:
                _yield_to_voice(pause)
            v = from_bytes(emb)
            if v is None or denied(deny, v):
                gone.append(int(fid))
            else:
                faces.append((int(fid), unit(v), gid, list(rej or [])))
        suggest: list[tuple[int, int, int]] = []
        free: list[tuple[int, np.ndarray, Any]] = []
        for k, (fid, v, gid, rej) in enumerate(faces):
            if k % REGROUP_WRITE_BATCH == 0:
                _yield_to_voice(pause)
            g, _ = choose(named, v, rej)
            if g is not None:
                suggest.append((fid, g.id, int(g.person_id)))
            else:
                free.append((fid, v, gid))
        labels = (components(np.stack([v for _f, v, _g in free]), pause=pause)
                  if free else np.zeros(0, dtype=np.int64))
    return _Plan(gone, suggest, free, labels)


async def regroup(s: Any, *, max_faces: int = REGROUP_MAX_FACES, pause: Any = _voice_busy) -> dict[str, int]:
    """Re-group the faces nobody confirmed (confirmed faces never move): at
    most ``max_faces`` of them, the newest. The maths runs in a worker
    thread (:func:`plan_regroup`), so the event loop keeps answering, and
    the writes go in batches; a face confirmed meanwhile is left alone."""
    rows, deny, named = await _load_regroup(s, max_faces)
    plan = await asyncio.to_thread(plan_regroup, rows, deny, named, pause=pause)
    return await _apply_regroup(s, rows, named, plan)


async def regroup_night(*, max_faces: int = REGROUP_MAX_FACES, pause: Any = _voice_busy) -> dict[str, int] | None:
    """The nightly pass as :func:`housekeeping` runs it: read in one short
    transaction, the maths in a worker thread with no transaction open
    (so nothing waits on a lock it holds: "faces off" and the runner's
    Pause go through), then apply under the faces switch's share lock.
    None when faces were turned off meanwhile."""
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        rows, deny, named = await _load_regroup(s, max_faces)
    plan = await asyncio.to_thread(plan_regroup, rows, deny, named, pause=pause)
    async with session_scope() as s:
        if not await _faces_on_locked(s):
            return None
        out = await _apply_regroup(s, rows, named, plan)
        await write_state(s, regroup_attempts=0)
    return out


async def _load_regroup(s: Any, max_faces: int) -> tuple[list[Any], np.ndarray, list[Group]]:
    rows = (await s.execute(text(
        "SELECT id, embedding, group_id, rejected_person_ids FROM media_faces "
        "WHERE model = :m AND NOT confirmed ORDER BY id DESC LIMIT :n"),
        {"m": MODEL, "n": int(max_faces)})).all()
    deny = await _denylist(s)
    named = [g for g in await _groups(s) if g.person_id is not None]
    return [tuple(r) for r in rows], deny, named


async def _apply_regroup(s: Any, rows: list[Any], named: list[Group], plan: _Plan) -> dict[str, int]:
    touched: set[int] = {int(r[2]) for r in rows if r[2] is not None}
    for i in range(0, len(plan.gone), REGROUP_WRITE_BATCH):
        await s.execute(text("DELETE FROM media_faces WHERE id = ANY(:ids) AND NOT confirmed"),
                        {"ids": plan.gone[i:i + REGROUP_WRITE_BATCH]})
        await asyncio.sleep(0)
    # Named people's centroids come from confirmed faces only, so they are
    # fixed for this pass; each free face is suggested on its own first.
    params = [{"g": g, "p": p, "f": f} for f, g, p in plan.suggest]
    for i in range(0, len(params), REGROUP_WRITE_BATCH):
        await s.execute(text(
            "UPDATE media_faces SET group_id = :g, person_id = :p WHERE id = :f AND NOT confirmed"),
            params[i:i + REGROUP_WRITE_BATCH])
        await asyncio.sleep(0)
    touched.update(g for _f, g, _p in plan.suggest)
    made = 0
    free = plan.free
    if free:
        comps: dict[int, list[int]] = {}
        for i, lab in enumerate(plan.labels):
            comps.setdefault(int(lab), []).append(i)
        named_ids = {g.id for g in named}
        used: set[int] = set()
        for k, members in enumerate(sorted(comps.values(), key=len, reverse=True)):
            if k % 50 == 0:
                await asyncio.sleep(0)
            before = Counter(free[i][2] for i in members
                             if free[i][2] is not None and free[i][2] not in named_ids)
            gid = next((g for g, _n in before.most_common() if g not in used), None)
            if gid is None or not await _exists(s, gid):
                gid = await _new_group(s, mean_unit(free[i][1] for i in members))
                made += 1
            used.add(gid)
            touched.add(gid)
            ids = [free[i][0] for i in members]
            for j in range(0, len(ids), REGROUP_WRITE_BATCH):
                await s.execute(text(
                    "UPDATE media_faces SET group_id = :g, person_id = NULL "
                    "WHERE id = ANY(:ids) AND NOT confirmed"),
                    {"g": gid, "ids": ids[j:j + REGROUP_WRITE_BATCH]})
    await refresh(s, touched)
    # Groups a change of file or a training action left with no face.
    await s.execute(text(
        "DELETE FROM face_groups g WHERE NOT EXISTS (SELECT 1 FROM media_faces f WHERE f.group_id = g.id)"))
    return {"faces": len(rows) - len(plan.gone), "suggested": len(plan.suggest), "groups_made": made,
            "dropped": len(plan.gone)}


async def _exists(s: Any, gid: int) -> bool:
    return (await s.execute(text("SELECT 1 FROM face_groups WHERE id = :g AND person_id IS NULL"),
                            {"g": gid})).first() is not None


# ─── training ────────────────────────────────────────────────────────────


async def _group(s: Any, gid: int) -> Any:
    g = (await s.execute(text("SELECT id, person_id, hidden FROM face_groups WHERE id = :g FOR UPDATE"),
                         {"g": gid})).mappings().first()
    if g is None:
        raise Refused("no such group", 404)
    return g


async def _face(s: Any, fid: int) -> Any:
    f = (await s.execute(text(
        "SELECT id, group_id, person_id, confirmed, rejected_person_ids, embedding FROM media_faces "
        "WHERE id = :f FOR UPDATE"), {"f": fid})).mappings().first()
    if f is None:
        raise Refused("no such face", 404)
    return f


async def _person_exists(s: Any, pid: int) -> bool:
    return (await s.execute(text("SELECT 1 FROM people WHERE id = :p"), {"p": pid})).first() is not None


async def person_for_name(s: Any, name: str) -> int:
    """The person called ``name`` (case and space aside), made as a
    face-only ``people`` row when nobody is; under the names lock voice
    enrolment and rename take. :class:`Refused` when two people share it."""
    from domovoi.db.repositories import PeopleRepository

    name = " ".join(str(name).split())
    if not name or not name.isprintable() or len(name) > 100:
        raise Refused("give the person a name", 422)
    repo = PeopleRepository(s)
    await repo.lock_names()
    found = await repo.find_by_name(name)
    if len(found) > 1:
        raise Refused(f"two people are called {name}; pick one of them instead")
    if found:
        return found[0][0]
    return int((await s.execute(text(
        "INSERT INTO people (name, notes) VALUES (:n, NULL) RETURNING id"), {"n": name})).scalar())


async def group_person(s: Any, gid: int) -> int | None:
    g = (await s.execute(text("SELECT person_id FROM face_groups WHERE id = :g"), {"g": gid})).first()
    if g is None:
        raise Refused("no such group", 404)
    return g[0]


async def name_group(s: Any, gid: int, person_id: int) -> dict[str, Any]:
    """Link a group to a person: its faces (except any already said not
    to be them) become theirs, confirmed; a person who already has a group
    gets these faces in it. Unhides the group."""
    g = await _group(s, gid)
    if not await _person_exists(s, person_id):
        raise Refused("no such person", 404)
    target = await _person_group(s, person_id)
    rows = (await s.execute(text(
        "SELECT id, rejected_person_ids FROM media_faces WHERE group_id = :g"), {"g": gid})).all()
    take = [int(r[0]) for r in rows if person_id not in (r[1] or [])]
    leave = [int(r[0]) for r in rows if person_id in (r[1] or [])]
    await s.execute(text(
        "UPDATE media_faces SET group_id = :t, person_id = :p, confirmed = true WHERE id = ANY(:ids)"),
        {"t": target, "p": person_id, "ids": take})
    if leave:
        await s.execute(text(
            "UPDATE media_faces SET group_id = NULL, person_id = NULL, confirmed = false WHERE id = ANY(:ids)"),
            {"ids": leave})
    await s.execute(text("UPDATE face_groups SET hidden = false WHERE id = :t"), {"t": target})
    if target != g["id"]:
        await s.execute(text("DELETE FROM face_groups WHERE id = :g"), {"g": g["id"]})
    await refresh(s, [target])
    await _assign_loose(s, leave)
    return {"group_id": target, "person_id": person_id, "faces": len(take)}


async def confirm_face(s: Any, fid: int, person_id: int | None = None) -> dict[str, Any]:
    """"Yes, this is them": the face's suggested person (or ``person_id``)
    confirmed; it moves into their group and their centroid updates."""
    f = await _face(s, fid)
    pid = person_id if person_id is not None else f["person_id"]
    if pid is None:
        raise Refused("this face isn't suggested for anyone yet; name its group or pick a person")
    if not await _person_exists(s, pid):
        raise Refused("no such person", 404)
    target = await _person_group(s, pid)
    rej = [p for p in (f["rejected_person_ids"] or []) if p != pid]
    await s.execute(text(
        "UPDATE media_faces SET group_id = :g, person_id = :p, confirmed = true, rejected_person_ids = :r "
        "WHERE id = :f"), {"g": target, "p": pid, "r": rej, "f": fid})
    await refresh(s, [target, f["group_id"]])
    return {"face_id": fid, "person_id": pid, "group_id": target}


async def reject_face(s: Any, fid: int) -> dict[str, Any]:
    """"Not this person": the face leaves them, is never suggested for
    them again, and is grouped again without them."""
    f = await _face(s, fid)
    pid = f["person_id"]
    if pid is None:
        raise Refused("this face isn't anyone's yet")
    rej = sorted(set(f["rejected_person_ids"] or []) | {pid})
    await s.execute(text(
        "UPDATE media_faces SET group_id = NULL, person_id = NULL, confirmed = false, "
        "rejected_person_ids = :r WHERE id = :f"), {"r": rej, "f": fid})
    await refresh(s, [f["group_id"]])
    gid = await assign(s, fid, unit(from_bytes(f["embedding"])), rej)
    return {"face_id": fid, "rejected_person_id": pid, "group_id": gid}


async def hide_group(s: Any, gid: int, hidden: bool) -> dict[str, Any]:
    g = await _group(s, gid)
    if g["person_id"] is not None and hidden:
        raise Refused("a named person's faces can't be hidden; forget their face instead")
    await s.execute(text("UPDATE face_groups SET hidden = :h, updated_at = now() WHERE id = :g"),
                    {"h": hidden, "g": gid})
    return {"group_id": gid, "hidden": hidden}


async def merge_groups(s: Any, target: int, sources: list[int]) -> dict[str, Any]:
    """Several groups become ``target``. A named source makes an unnamed
    target theirs; two named people become the target's person (their
    voice and memories are not touched: faces only)."""
    sources = [x for x in dict.fromkeys(sources) if x != target]
    if not sources:
        raise Refused("pick at least one other group", 422)
    t = await _group(s, target)
    srcs = [await _group(s, x) for x in sources]
    persons = {g["person_id"] for g in [t, *srcs] if g["person_id"] is not None}
    pid = t["person_id"] if t["person_id"] is not None else (next(iter(persons)) if len(persons) == 1 else None)
    if t["person_id"] is None and len(persons) > 1:
        raise Refused("merge into one of the named people, not into an unnamed group")
    hidden = bool(t["hidden"]) and all(g["hidden"] for g in srcs)
    rows = (await s.execute(text(
        "SELECT id, rejected_person_ids FROM media_faces WHERE group_id = ANY(:g)"), {"g": sources})).all()
    leave = [int(r[0]) for r in rows if pid is not None and pid in (r[1] or [])]
    await s.execute(text(
        "UPDATE media_faces SET group_id = :t, person_id = :p WHERE group_id = ANY(:g) AND NOT (id = ANY(:l))"),
        {"t": target, "p": pid, "g": sources, "l": leave})
    if pid is not None:
        await s.execute(text("UPDATE media_faces SET person_id = :p WHERE group_id = :t"),
                        {"p": pid, "t": target})
    if leave:
        await s.execute(text(
            "UPDATE media_faces SET group_id = NULL, person_id = NULL, confirmed = false WHERE id = ANY(:ids)"),
            {"ids": leave})
    await s.execute(text("UPDATE face_groups SET person_id = :p, hidden = :h WHERE id = :t"),
                    {"p": pid, "h": hidden, "t": target})
    await s.execute(text("DELETE FROM face_groups WHERE id = ANY(:g)"), {"g": sources})
    await refresh(s, [target])
    await _assign_loose(s, leave)
    return {"group_id": target, "person_id": pid, "merged": len(sources)}


async def split_faces(s: Any, face_ids: list[int]) -> dict[str, Any]:
    """Pick faces out of one group into a new unnamed group. They are
    pinned there (confirmed: the re-group won't move them back), and
    when they left a named person they are never suggested for them again."""
    ids = sorted(set(int(x) for x in face_ids))
    if not ids:
        raise Refused("pick the faces to split off", 422)
    rows = (await s.execute(text(
        "SELECT id, group_id, embedding, rejected_person_ids FROM media_faces WHERE id = ANY(:ids) FOR UPDATE"),
        {"ids": ids})).all()
    if len(rows) != len(ids):
        raise Refused("no such face", 404)
    groups = {r[1] for r in rows}
    if len(groups) != 1 or None in groups:
        raise Refused("the faces to split off must all be in one group", 422)
    src = await _group(s, int(groups.pop()))
    pid = src["person_id"]
    new = await _new_group(s, mean_unit(unit(from_bytes(r[2])) for r in rows), hidden=bool(src["hidden"]))
    for r in rows:
        rej = sorted(set(r[3] or []) | ({pid} if pid is not None else set()))
        await s.execute(text(
            "UPDATE media_faces SET group_id = :g, person_id = NULL, confirmed = true, "
            "rejected_person_ids = :r WHERE id = :f"), {"g": new, "r": rej, "f": int(r[0])})
    await refresh(s, [new, src["id"]])
    return {"group_id": new, "from_group_id": src["id"], "faces": len(ids)}


async def _assign_loose(s: Any, face_ids: list[int]) -> None:
    if not face_ids:
        return
    rows = (await s.execute(text(
        "SELECT id, embedding, rejected_person_ids FROM media_faces WHERE id = ANY(:ids)"),
        {"ids": face_ids})).all()
    groups = await _groups(s)
    for fid, emb, rej in rows:
        v = from_bytes(emb)
        if v is not None:
            await assign(s, int(fid), unit(v), list(rej or []), groups=groups)


# ─── forget, and faces off ───────────────────────────────────────────────


async def forget_person(s: Any, person_id: int) -> dict[str, int]:
    """Forget one person's face: their face rows and groups go, one
    averaged embedding (tied to nobody) goes on the denylist, and any
    other face that matches it goes too. Their photos and their ``people``
    row stay."""
    rows = (await s.execute(text(
        "SELECT embedding, confirmed FROM media_faces WHERE person_id = :p AND model = :m"),
        {"p": person_id, "m": MODEL})).all()
    confirmed = [from_bytes(r[0]) for r in rows if r[1]]
    sig = mean_unit(confirmed) if any(v is not None for v in confirmed) else \
        mean_unit(from_bytes(r[0]) for r in rows)
    deleted = int((await s.execute(text("DELETE FROM media_faces WHERE person_id = :p"),
                                   {"p": person_id})).rowcount or 0)
    groups = int((await s.execute(text("DELETE FROM face_groups WHERE person_id = :p"),
                                  {"p": person_id})).rowcount or 0)
    look_alikes = 0
    if sig is not None:
        await s.execute(text("INSERT INTO face_denylist (embedding, model) VALUES (:e, :m)"),
                        {"e": to_bytes(sig), "m": MODEL})
        rest = (await s.execute(text(
            "SELECT id, embedding, group_id FROM media_faces WHERE model = :m"), {"m": MODEL})).all()
        hit = [(int(r[0]), r[2]) for r in rest
               if (v := from_bytes(r[1])) is not None and float(unit(v) @ sig) >= DENY_MATCH]
        if hit:
            await s.execute(text("DELETE FROM media_faces WHERE id = ANY(:ids)"), {"ids": [h[0] for h in hit]})
            await refresh(s, [h[1] for h in hit])
            look_alikes = len(hit)
    return {"faces": deleted + look_alikes, "groups": groups, "denylisted": int(sig is not None)}


async def delete_all(s: Any) -> dict[str, int]:
    """Faces off: every face, group and faces stage goes (the denylist
    stays; see the module note). Call it after writing the switch, in the
    same transaction."""
    n = int((await s.execute(text("DELETE FROM media_faces"))).rowcount or 0)
    g = int((await s.execute(text("DELETE FROM face_groups"))).rowcount or 0)
    await s.execute(text(f"DELETE FROM media_item_stages WHERE analyser = '{ANALYSER}'"))
    await s.execute(text("DELETE FROM household_settings WHERE key = :k"), {"k": STATE_KEY})
    return {"faces": n, "groups": g}


# ─── reads (the People pages, and the search hook for slices 5-6) ────────


async def people_with_faces(s: Any) -> list[dict[str, Any]]:
    rows = (await s.execute(text(
        "SELECT p.id, p.name, count(f.id) AS faces, count(f.id) FILTER (WHERE f.confirmed) AS confirmed, "
        "min(g.id) AS group_id FROM face_groups g JOIN people p ON p.id = g.person_id "
        "LEFT JOIN media_faces f ON f.group_id = g.id WHERE g.model = :m "
        "GROUP BY p.id, p.name ORDER BY p.name"), {"m": MODEL})).mappings().all()
    return [dict(r) for r in rows]


async def unnamed_groups(s: Any, *, more: bool = False, hidden: bool = False) -> list[dict[str, Any]]:
    rows = (await s.execute(text(
        "SELECT g.id AS group_id, g.hidden, count(f.id) AS faces FROM face_groups g "
        "JOIN media_faces f ON f.group_id = g.id WHERE g.person_id IS NULL AND g.model = :m AND g.hidden = :h "
        "GROUP BY g.id, g.hidden HAVING count(f.id) >= :min ORDER BY count(f.id) DESC, g.id"),
        {"m": MODEL, "h": hidden, "min": 1 if (more or hidden) else SHOWN_MIN_FACES})).mappings().all()
    return [dict(r) for r in rows]


FACE_COLUMNS = ("f.id AS face_id, f.item_id, i.library_id, i.rel_path, i.kind, f.at_s, f.end_s, f.box, f.quality, "
                "f.group_id, f.person_id, f.confirmed")


def _face_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["box"] = [float(x) for x in (d.get("box") or [])]
    d["suggested"] = d.get("person_id") is not None and not d.get("confirmed")
    return d


async def group_faces(s: Any, gid: int, *, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    g = (await s.execute(text("SELECT id, person_id, hidden FROM face_groups WHERE id = :g"),
                         {"g": gid})).mappings().first()
    if g is None:
        raise Refused("no such group", 404)
    rows = (await s.execute(text(
        f"SELECT {FACE_COLUMNS} FROM media_faces f JOIN media_items i ON i.id = f.item_id "
        "WHERE f.group_id = :g ORDER BY f.confirmed DESC, f.quality DESC NULLS LAST, f.id "
        "LIMIT :l OFFSET :o"), {"g": gid, "l": limit, "o": offset})).mappings().all()
    return {"group_id": gid, "person_id": g["person_id"], "hidden": g["hidden"],
            "faces": [_face_row(r) for r in rows]}


async def items_for_person(s: Any, person_id: int, *, confirmed_only: bool = False,
                           kind: str | None = None, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    """The search hook (slices 5-6): every photo and video ``person_id`` is
    in, newest first, each with whether every sighting is confirmed and,
    for a video, their appearances in time order: ``appearances``
    (``[{"at_s", "end_s"}]``, first to last sighting) and ``moments`` (each
    appearance's start, where a hit opens the player)."""
    rows = (await s.execute(text(
        "SELECT i.id AS item_id, i.library_id, i.rel_path, i.kind, i.taken_at, "
        "array_agg(f.at_s ORDER BY f.at_s) FILTER (WHERE f.at_s IS NOT NULL) AS moments, "
        "array_agg(COALESCE(f.end_s, f.at_s) ORDER BY f.at_s) FILTER (WHERE f.at_s IS NOT NULL) AS ends, "
        "bool_and(f.confirmed) AS confirmed, count(f.id) AS faces "
        "FROM media_faces f JOIN media_items i ON i.id = f.item_id "
        "WHERE f.person_id = :p AND (NOT :c OR f.confirmed) AND (CAST(:k AS TEXT) IS NULL OR i.kind = :k) "
        "GROUP BY i.id ORDER BY i.taken_at DESC NULLS LAST, i.mtime DESC, i.id DESC LIMIT :l OFFSET :o"),
        {"p": person_id, "c": confirmed_only, "k": kind, "l": limit, "o": offset})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["moments"] = [float(x) for x in (d["moments"] or [])]
        ends = [float(x) for x in (d.pop("ends") or [])]
        d["appearances"] = [{"at_s": a, "end_s": max(a, e)} for a, e in zip(d["moments"], ends)]
        d["taken_at"] = d["taken_at"].isoformat() if d["taken_at"] else None
        out.append(d)
    return out


async def person_ids_with_faces(s: Any, name: str) -> list[int]:
    """People called ``name`` who have confirmed faces (the search's
    person filter: "photos of Sarah")."""
    rows = (await s.execute(text(
        "SELECT DISTINCT p.id FROM people p JOIN media_faces f ON f.person_id = p.id AND f.confirmed "
        "WHERE lower(btrim(p.name)) = lower(btrim(:n))"), {"n": name})).all()
    return [int(r[0]) for r in rows]



async def named_people_with_faces(s: Any) -> list[tuple[int, str]]:
    """Every named person with confirmed faces, as (id, name): what the
    chat and voice search (:mod:`.ask`) match a name in a question against."""
    rows = (await s.execute(text(
        "SELECT DISTINCT p.id, p.name FROM people p JOIN media_faces f ON f.person_id = p.id AND f.confirmed "
        "WHERE p.name IS NOT NULL AND btrim(p.name) <> ''"))).all()
    return [(int(r[0]), str(r[1])) for r in rows]

# ─── the analyser (photos) and the hook for video frames (slice 3) ───────


_engine: FaceEngine | None = None


def engine() -> FaceEngine:
    global _engine
    if _engine is None:
        _engine = FaceEngine()
    return _engine


def set_engine(e: FaceEngine | None) -> None:
    """Tests: a fake engine (or None for the real one)."""
    global _engine
    _engine = e


async def _detect(ctx: Any, image: Any) -> list[Found]:
    """The engine on one picture, voice first (waits for quiet, cancelled
    and asked again when a turn starts) and counted against the item's
    time budget; ONNX Runtime runs in a thread, one intra-op thread."""
    eng = engine()

    def work() -> list[Found]:
        return eng.faces(image)

    if ctx is None:
        return await asyncio.to_thread(work)
    return await ctx.model_step(lambda: asyncio.to_thread(work), needs_ai=False)


class FaceAnalyser:
    """Faces in photos. Registered with the runner; runs only while the
    switch is on and the pinned models are on disk."""

    name = ANALYSER
    kinds = frozenset({"image"})
    order = 1           # cheap: right after the fingerprint (design §4)
    deepens = False

    def wanted(self, item: Any, sw: dict[str, Any]) -> bool:
        return bool(sw.get("faces")) and face_models.ready()

    async def run(self, item: Any, ctx: Any) -> None:
        from domovoi import executors

        async with ctx.charge():
            data = await ctx.read(0, int(item.size_bytes))
        image = await executors.run("pictures", load_picture, data)
        found = await _detect(ctx, image)
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            # A retried photo starts clean (confirmed faces are the
            # household's and stay).
            await s.execute(text("DELETE FROM media_faces WHERE item_id = :i AND NOT confirmed"),
                            {"i": item.id})
            try:
                ids = await store(s, item.id, found)
            except FacesOff:
                from domovoi.media_understanding.runner import Skip

                raise Skip("faces-off") from None
        await ctx.save({"faces": len(ids)})


@dataclass
class _Sighting:
    face_id: int
    emb: np.ndarray
    quality: float
    last_s: float


class VideoFaces:
    """The hook slice 3 calls for a video's keyframes.

    ::

        vf = await faces.VideoFaces.start(item.id)     # None: faces off / no models
        for at_s, image in keyframes:                  # in time order
            if vf is not None:
                await vf.frame(at_s, image, ctx=ctx)   # a Pillow RGB image (or bytes)

    At most one frame per :data:`VIDEO_MIN_GAP_S` is looked at (call it on
    every candidate frame, before the caption cap: faces are cheap). One
    person's sightings within :data:`APPEARANCE_GAP_S` of each other are
    one row, an *appearance*, from its first moment (``at_s``) to its last
    (``end_s``), keeping the best-looking face, so a 10-minute clip makes a
    few rows, not hundreds. A restart carries on after the last saved
    moment.
    """

    def __init__(self, item_id: int, resume_after: float | None) -> None:
        self.item_id = item_id
        self.resume_after = resume_after
        self.last_frame: float | None = None
        self.open: list[_Sighting] = []

    @classmethod
    async def start(cls, item_id: int) -> "VideoFaces | None":
        from domovoi.db.session import session_scope

        if not face_models.ready():
            return None
        async with session_scope() as s:
            if not (await switches.read(s))["faces"]:
                return None
            last = (await s.execute(text("SELECT max(COALESCE(end_s, at_s)) FROM media_faces WHERE item_id = :i"),
                                    {"i": item_id})).scalar()
        return cls(item_id, float(last) if last is not None else None)

    async def frame(self, at_s: float, image: Any, *, ctx: Any = None) -> int:
        """Faces in one keyframe; how many new appearances were saved."""
        at_s = float(at_s)
        if self.resume_after is not None and at_s <= self.resume_after:
            return 0
        if self.last_frame is not None and at_s - self.last_frame < VIDEO_MIN_GAP_S:
            return 0
        self.last_frame = at_s
        if isinstance(image, (bytes, bytearray)):
            from domovoi import executors

            image = await executors.run("pictures", load_picture, bytes(image))
        found = await _detect(ctx, image)
        self.open = [o for o in self.open if at_s - o.last_s <= APPEARANCE_GAP_S]
        new: list[Found] = []
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            for f in found:
                emb = unit(f.embedding)
                same = max(self.open, key=lambda o: float(o.emb @ emb), default=None)
                if same is not None and float(same.emb @ emb) >= APPEARANCE_MATCH:
                    same.last_s = at_s
                    # The appearance now runs to this sighting (confirmed or not).
                    await s.execute(text(
                        "UPDATE media_faces SET end_s = GREATEST(COALESCE(end_s, at_s), :t) WHERE id = :f"),
                        {"t": at_s, "f": same.face_id})
                    if f.quality > same.quality:
                        # A better look at the same appearance: keep it (the
                        # start stays the first moment). Group it again only
                        # when nobody has confirmed it.
                        same.quality, same.emb = f.quality, emb
                        await s.execute(text(
                            "UPDATE media_faces SET box = :b, quality = :q, embedding = :e "
                            "WHERE id = :f AND NOT confirmed"),
                            {"b": [float(x) for x in f.box], "q": float(f.quality), "e": to_bytes(emb),
                             "f": same.face_id})
                    continue
                new.append(f)
            try:
                ids = await store(s, self.item_id, new, at_s=at_s)
            except FacesOff:
                return 0
            rows = (await s.execute(text(
                "SELECT id, embedding, quality FROM media_faces WHERE id = ANY(:ids)"), {"ids": ids})).all()
        for fid, emb, q in rows:
            self.open.append(_Sighting(int(fid), unit(from_bytes(emb)), float(q or 0), at_s))
        return len(ids)


# ─── the runner's housekeeping: model fetch, re-queue, nightly re-group ──


_fetch_task: asyncio.Task | None = None


async def _fetch_models() -> None:
    from domovoi import egress
    from domovoi.db.session import session_scope

    try:
        await asyncio.to_thread(face_models.download)
        err = None
    except egress.InternetTurnedOff:
        err = "internet access is turned off"
    except face_models.ModelsUnavailable as e:
        err = str(e)
    except Exception as e:  # noqa: BLE001
        err = type(e).__name__
    async with session_scope() as s:
        await write_state(s, fetch_error=err)


async def requeue_photos(s: Any) -> int:
    """Photos analysed before faces were on (or before the models came):
    queued again; only the faces stage runs (the others are done)."""
    r = await s.execute(text(
        "UPDATE media_items i SET state = 'pending', attempts = 0, updated_at = now() "
        "WHERE i.kind = 'image' AND i.state IN ('done', 'skipped') AND NOT EXISTS ("
        f"SELECT 1 FROM media_item_stages st WHERE st.item_id = i.id AND st.analyser = '{ANALYSER}')"))
    return int(r.rowcount or 0)


async def requeue_faceless(s: Any) -> int:
    """After a forgotten face is undone: photos the faces stage finished
    with no face stored are queued again for faces (only that stage
    runs)."""
    await s.execute(text(
        f"DELETE FROM media_item_stages st WHERE st.analyser = '{ANALYSER}' AND NOT EXISTS ("
        "SELECT 1 FROM media_faces f WHERE f.item_id = st.item_id)"))
    return await requeue_photos(s)


async def housekeeping(runner: Any) -> None:
    """Each library step, with the switches read: fetch the models when
    faces are on and they aren't there (only then: never on a house that
    didn't opt in); once they are, queue the photos again; in the night
    window, re-group once a night."""
    global _fetch_task
    from domovoi.db.session import session_scope

    if not runner.sw.get("faces"):
        return
    if not face_models.ready():
        if (_fetch_task is None or _fetch_task.done()) and face_models.may_retry():
            _fetch_task = asyncio.ensure_future(_fetch_models())
        return
    async with session_scope() as s:
        st = await read_state(s)
        if not st.get("requeued"):
            n = await requeue_photos(s)
            await write_state(s, requeued=True, fetch_error=None)
            log.info("faces: %d photo%s queued for faces", n, "" if n == 1 else "s")
    tonight = date.today().isoformat()
    if runner.in_night() and st.get("last_regroup") != tonight:
        # The night's mark goes first, in its own transaction: a re-group
        # that dies (a restart, the watchdog) is not tried again tonight.
        async with session_scope() as s:
            if not await _faces_on_locked(s):
                return
            await write_state(s, last_regroup=tonight,
                              regroup_attempts=int(st.get("regroup_attempts") or 0) + 1)
        out = await regroup_night()
        if out is None:
            return
        log.info("faces: nightly re-group: %d free faces, %d suggested, %d new groups",
                 out["faces"], out["suggested"], out["groups_made"])


def install() -> None:
    """Register the analyser and the housekeeping with the runner."""
    from domovoi.media_understanding import runner

    runner.register(FaceAnalyser())
    runner.register_housekeeping("faces", housekeeping)
