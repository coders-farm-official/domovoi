""""Tune Domovoi to your household": a household copy of the published
"Domovoi" wake word, trained with the house's own recordings.

The published word (``domovoi_v1``, :mod:`domovoi.wake_default`) is trained
on synthetic voices only, so it never carries anybody's voice. A household
can make its own copy that also learns how *its* people say "Domovoi",
through *its* rooms' microphones:

* **Start.** Settings → Wake words → "Tune Domovoi to your household"
  creates one ordinary trained word (``wake_words`` row) with the published
  word's phrase, under a fixed slug no other word may take
  (:func:`tuned_slug`, ``domovoi_v1_tuned``). From there the existing
  recording flow does the rest: record a few clips of everyone saying
  "Domovoi" on any room (takes append across rooms), then Train.
* **Train.** The household trainer runs the published word's recipe: the
  same trainer and datasets, the same phrase, the published word's own
  near-miss list (:data:`RECIPES`, "domino", "the movie", ...) on top of
  the ones worked out from the phrase, and the house's selected clips.
  The worker passes the near misses as ``WAKE_EXTRA_NEGATIVES``.
* **Switch.** Until the copy is ready, every room keeps the published word.
  When a training of it succeeds (and the household hasn't gone back),
  every room that wakes on the published word is given the copy, by the
  same push the dashboard uses (``set_wake_word``); a room that is offline
  then is given it on its next hello. The household default follows, so a
  new room gets the copy too.
* **Undo.** "Go back to the standard Domovoi word" gives every room on the
  copy the published word again and puts the household default back. The
  copy and its clips stay (they can be used again, or deleted like any
  word: deleting is refused while rooms use the copy).

**Privacy.** The recordings and the copy never leave the house: the clips
stay under ``wake_clips_dir``, the model under ``wake_models_dir``, served
only to the house's own rooms over the signed ``/v1/wake-models`` channel.
Nothing here uploads anything; the copy is never one of the published
words (:data:`domovoi.wake_default.PUBLISHED`), so "use a published word"
and the default-download path refuse it. Its Download (the same one every
trained word has) carries a model card that says it includes the
household's recordings and is private (:mod:`domovoi.wake_model_card`).

State: ``household_settings`` key ``wake_tuning`` = ``{"base": slug,
"use": bool}``. ``use`` is true from Start until Undo, and true again on
"Use the tuned word again". The switch happens only while ``use`` is true
AND the copy is ready.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import text

log = logging.getLogger(__name__)

KEY = "wake_tuning"
SUFFIX = "_tuned"


def _cant_train_here() -> bool:
    """True when this server can never train (wake_trainer_setup.unsupported_reason)."""
    try:
        from domovoi.wake_trainer_setup import unsupported_reason

        return unsupported_reason() is not None
    except Exception:  # noqa: BLE001 - a status read never fails on this
        return False


@dataclass(frozen=True)
class Recipe:
    """What a household copy of a published word trains from, beyond the
    trainer's defaults: the phrase and the published word's near misses."""
    base: str
    phrase: str
    extra_negatives: tuple[str, ...]


# The near misses Domovoi's published word is trained against (the
# domovoi_v2 recipe's list): words people say at home that sound close.
_DOMOVOI_NEAR_MISSES = (
    "domino", "dominoes", "the dominoes", "do move", "do move it", "the movie",
    "movie", "movies", "a movie", "dominic", "hey dominic", "domo", "demo",
    "the demo", "volvo", "convoy", "envoy", "dmitri", "domain", "doughboy",
    "don't move", "go move", "tomboy", "monday", "the mob", "hello boy",
    "tom bowie", "dumb voice", "dom", "hey dom", "dom, oh boy", "hey mom, oh boy",
    "no way", "do my voice", "demolish", "do more", "home boy", "domino's",
    "dominica", "dynamo", "romeo", "domingo", "domestic", "dumbo",
    "the moving van", "move over", "the menu", "tomorrow", "monopoly", "dora",
)

#: Published words a household may tune, by slug.
RECIPES: dict[str, Recipe] = {
    "domovoi_v1": Recipe(base="domovoi_v1", phrase="domovoi",
                         extra_negatives=_DOMOVOI_NEAR_MISSES),
}

#: The word "Tune Domovoi" tunes.
DEFAULT_BASE = "domovoi_v1"

#: The tuned copy's display name (the room card's "Wakes on: ...").
TUNED_NAME = "Domovoi (tuned to this home)"


def tuned_slug(base: str) -> str:
    return f"{base}{SUFFIX}"


def base_of(slug: str | None) -> str | None:
    """The published word ``slug`` is the household copy of, else None."""
    if not slug or not slug.endswith(SUFFIX):
        return None
    base = slug[: -len(SUFFIX)]
    return base if base in RECIPES else None


def is_tuned(slug: str | None) -> bool:
    return base_of(slug) is not None


def extra_negatives_env(slug: str) -> str:
    """``WAKE_EXTRA_NEGATIVES`` for a training of ``slug``: the published
    word's near misses for its household copy, "" for any other word."""
    base = base_of(slug)
    return ";".join(RECIPES[base].extra_negatives) if base else ""


# ─── state ───────────────────────────────────────────────────────────────

async def read_setting(s) -> dict[str, Any]:
    row = (await s.execute(
        text("SELECT value FROM household_settings WHERE key = :k"), {"k": KEY})).first()
    value = (row[0] if row else None) or {}
    base = value.get("base") if isinstance(value.get("base"), str) else None
    if base not in RECIPES:
        return {}
    return {"base": base, "use": bool(value.get("use"))}


async def _write_setting(s, base: str, use: bool) -> None:
    await s.execute(
        text("INSERT INTO household_settings (key, value, updated_at) "
             "VALUES (:k, CAST(:v AS JSONB), now()) "
             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": KEY, "v": json.dumps({"base": base, "use": bool(use)})},
    )


async def _word(s, base: str) -> dict | None:
    from domovoi.db.repositories import WakeWordsRepository

    return await WakeWordsRepository(s).get_by_slug(tuned_slug(base))


def _ready(row: dict | None) -> bool:
    """The copy has a trained model rooms can use. A retrain keeps the
    current model in use (status "training", model_ref kept), and so does a
    failed retrain, so rooms stay on it meanwhile."""
    return bool(row) and bool(row.get("model_ref"))


async def active_target(s) -> tuple[str, str] | None:
    """(base, tuned slug) when rooms should wake on the copy now: tuning is
    in use and the copy is trained. None otherwise."""
    st = await read_setting(s)
    if not st or not st["use"]:
        return None
    row = await _word(s, st["base"])
    return (st["base"], row["slug"]) if _ready(row) else None


async def in_use(s, slug: str) -> bool:
    """Is ``slug`` a household copy that rooms are (to be) using now?"""
    base = base_of(slug)
    if base is None:
        return False
    st = await read_setting(s)
    return bool(st) and st["base"] == base and st["use"]


# ─── what the dashboard shows ────────────────────────────────────────────

async def status(s, room_words: dict[str, str] | None = None,
                 room_lists: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Settings → Wake words' "Tune Domovoi" card: whether it can start,
    the copy's state, whether rooms use it, and the rooms on each word."""
    from domovoi import wake_default
    from domovoi.config import settings

    st = await read_setting(s)
    base = st.get("base") or DEFAULT_BASE
    published = wake_default.PUBLISHED.get(base)
    row = await _word(s, base)
    use = bool(st.get("use"))
    ready = _ready(row)
    rooms = await _rooms_on(s, (base, tuned_slug(base)), room_words or {}, room_lists or {})
    can_start, why_not = True, None
    if published is None or not published.pinned:
        can_start, why_not = False, "the Domovoi wake word isn't published yet"
    elif _cant_train_here():
        # An ARM server (the Mac installer's VM among them) can't train at
        # all: pointing at "Turn on wake-word training" would be a control
        # that can't do what it says; the trainer card above says why.
        can_start, why_not = False, "this server can't train wake words (the card above says why)"
    elif not settings.wake_word_trainer_enabled:
        can_start, why_not = False, ("wake-word training isn't turned on on this server "
                                     "(Turn on wake-word training, above)")
    if ready and use:
        state = "in_use"
    elif ready:
        state = "standard"         # trained, but the household went back
    elif row is not None:
        state = {"training": "training", "failed": "failed"}.get(row["status"], "recording")
    else:
        state = "off"
    return {
        "base": base,
        "base_name": published.name if published else base,
        "slug": tuned_slug(base),
        "name": TUNED_NAME,
        "state": state,
        "use": use,
        "ready": ready,
        "wake_word_id": row["id"] if row else None,
        "status": row["status"] if row else None,
        "clip_count": row["clip_count"] if row else 0,
        "min_clips": settings.wake_word_min_clips,
        "error": row.get("error") if row else None,
        "can_start": can_start,
        "reason": why_not,
        "rooms_on_standard": rooms[base],
        "rooms_on_tuned": rooms[tuned_slug(base)],
        "private": True,
    }


def room_words_list(row: dict | None, reported: str | None,
                    reported_list: list[str] | None) -> list[str]:
    """A room's wake words, main word first (V063): what its device said it
    listens for, then any the server gave it that the device isn't running
    (more than it can run); else the server's record with the reported main
    word first; else its one word."""
    stored = list((row or {}).get("slugs") or [])
    if not stored and (row or {}).get("slug"):
        stored = [row["slug"]]
    running = list(reported_list or [])
    if running and reported and running[0] != reported:
        running = []          # a hello from before the word's health report
    if running:
        return running + [w for w in stored if w not in running]
    if reported:
        return [reported] + [w for w in stored if w != reported]
    return stored


def replace_word(words: list[str], frm: str, to: str) -> list[str] | None:
    """``words`` with ``frm`` replaced by ``to`` in its own place (the main
    word stays first when it was first, an extra word stays an extra one).
    None when ``frm`` isn't there. The room's other words stay; when ``to``
    is in the list already, ``frm`` just goes (no repeats)."""
    if frm not in words:
        return None
    if to in words:
        out = [w for w in words if w != frm]
    else:
        out = [to if w == frm else w for w in words]
    return out or [to]


async def _rooms_on(s, slugs: tuple[str, ...], room_words: dict[str, str],
                    room_lists: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """Room ids per word: the rooms with that word anywhere in their list."""
    from domovoi import wake_default

    room_lists = room_lists or {}
    states = await wake_default.room_states(s)
    out: dict[str, list[str]] = {sl: [] for sl in slugs}
    for room in sorted(set(states) | set(room_words) | set(room_lists)):
        words = room_words_list(states.get(room), room_words.get(room), room_lists.get(room))
        for sl in slugs:
            if sl in words:
                out[sl].append(room)
    return out


# ─── start / undo / use again ────────────────────────────────────────────

async def start(s, base: str = DEFAULT_BASE) -> dict[str, Any]:
    """"Tune Domovoi to your household": make the household copy's word (in
    "recording", ready for clips) if it isn't there, and say the household
    wants it. Rooms change only once it is trained. ValueError when the
    word can't be tuned here."""
    from domovoi import wake_default
    from domovoi.db.repositories import WakeWordsRepository

    recipe = RECIPES.get(base)
    published = wake_default.PUBLISHED.get(base)
    if recipe is None or published is None or not published.pinned:
        raise ValueError("that wake word can't be tuned")
    repo = WakeWordsRepository(s)
    row = await repo.get_by_slug(tuned_slug(base))
    if row is None:
        await repo.create(name=TUNED_NAME, slug=tuned_slug(base), phrase=recipe.phrase,
                          threshold=0.7, source_room_id=None, synthetic_only=False)
    await _write_setting(s, base, True)
    return await repo.get_by_slug(tuned_slug(base))


async def set_use(s, use: bool) -> dict[str, Any] | None:
    """Undo (``use`` False) or use again (True). The rooms are moved by
    :func:`apply`, after the commit. None when tuning was never started."""
    st = await read_setting(s)
    if not st:
        return None
    await _write_setting(s, st["base"], use)
    return {**st, "use": use}


async def _sync_default(s, base: str, tuned: str, to_tuned: bool) -> None:
    """The household default follows: the published word → the copy, or
    back. Any other default (a word of the household's own) stays."""
    from domovoi import wake_default

    current = await wake_default.household_default(s)
    if to_tuned and current == base:
        await wake_default.set_household_default(s, tuned)
    elif not to_tuned and current == tuned:
        await wake_default.set_household_default(s, base)


# ─── moving the rooms ────────────────────────────────────────────────────
#
# Several words per room (V063): the word the household moves away from is
# replaced INSIDE the room's list, in its own place, so the main word stays
# slugs[0] (= ``slug``) and the room's other words stay. The device is sent
# the whole list, no more than it said it can run; an older device, which
# never said, gets the main word alone (StreamingSession.request_set_wake_word).

#: ``push(words)``: send a room its list, ``[{"slug", "threshold"}]``, main
#: word first.
PushWords = Callable[[list[dict[str, Any]]], Awaitable[None]]


async def desired_switch(s) -> tuple[str, str, bool] | None:
    """(from, to, to_tuned) the rooms should make now, or None when tuning
    was never started."""
    st = await read_setting(s)
    if not st:
        return None
    base, tuned = st["base"], tuned_slug(st["base"])
    if st["use"] and _ready(await _word(s, base)):
        return base, tuned, True
    return tuned, base, False


async def _record_room(s, room_id: str, words: list[str]) -> None:
    """The room's row now holds ``words`` (``slug`` = words[0]); its state
    stays (an 'assigned' room is still the household's, a 'chosen' one
    still the admin's). A room with no row, or a 'kept' one, becomes
    'chosen': it was moved on purpose and the household default never
    applies to it again. ``device_max`` is left as it is."""
    from domovoi import wake_default

    row = await wake_default.room_state(s, room_id)
    state = row["state"] if row and row["state"] in ("assigned", "chosen", "pending") else "chosen"
    await wake_default._put(s, room_id, state, words[0], words)


def session_push(target) -> PushWords:
    """A connected room's push: its whole list, by the frame V063 defined."""
    async def push(words: list[dict[str, Any]]) -> None:
        await target.request_set_wake_word(
            slug=words[0]["slug"], threshold=words[0]["threshold"],
            words=words if len(words) > 1 else None)
    return push


async def _move_room(room_id: str, current: list[str], frm: str, to: str, push: PushWords,
                     session_scope) -> list[str] | None:
    """Replace ``frm`` with ``to`` in the room's list, push it, record it.
    The new list, or None when the room doesn't have ``frm``. Raises when a
    word can't be given or the push fails (nothing is recorded then)."""
    from domovoi import wake_default

    new = replace_word(current, frm, to)
    if new is None:
        return None
    async with session_scope() as s:
        words = await wake_default.word_payload(s, new)
    await push(words)
    async with session_scope() as s:
        await _record_room(s, room_id, new)
    return new


async def on_hello(room_id: str, reported: str | None, can_push: bool, push: PushWords,
                   *, reported_list: list[str] | None = None,
                   session_scope=None) -> list[str] | None:
    """A room said hello: if one of its words is the word the household
    moved away from (the published word while the copy is in use, or the
    copy after an undo), it is given the other one in that word's place.
    Returns the room's new list, or None. Never raises for the caller's
    hello path."""
    if not can_push:
        return None
    if session_scope is None:
        from domovoi.db.session import session_scope
    try:
        from domovoi import wake_default

        async with session_scope() as s:
            plan = await desired_switch(s)
            if plan is None:
                return None
            frm, to, to_tuned = plan
            row = await wake_default.room_state(s, room_id)
        current = room_words_list(row, reported, reported_list)
        if frm not in current:
            return None
        if not to_tuned:
            ok, _msg = await wake_default.ensure_published(to)
            if not ok:
                return None
        new = await _move_room(room_id, current, frm, to, push, session_scope)
        log.info("wake tuning: room %s now wakes on %s", room_id, new)
        return new
    except Exception as e:  # noqa: BLE001 - a hello never fails over this
        log.warning("wake tuning: room %s not handled: %s", room_id, e)
        return None


async def apply(sessions: dict, room_words: dict[str, str],
                room_lists: dict[str, list[str]] | None = None, *,
                session_scope=None) -> dict[str, Any]:
    """Move every room to the word it should wake on now (after a training
    of the copy, an undo, or "use again"), and the household default with
    them. Connected rooms are pushed now; an offline room's record is
    changed only when it says hello (:func:`on_hello`), so the record never
    claims a word the device hasn't got. Returns {moved, waiting, failed}."""
    from domovoi import satellite_abilities, wake_default

    room_lists = room_lists or {}
    if session_scope is None:
        from domovoi.db.session import session_scope
    async with session_scope() as s:
        plan = await desired_switch(s)
        if plan is None:
            return {"moved": [], "waiting": [], "failed": []}
        frm, to, to_tuned = plan
        st = await read_setting(s)
        await _sync_default(s, st["base"], tuned_slug(st["base"]), to_tuned)
        rooms = (await _rooms_on(s, (frm,), room_words, room_lists))[frm]
        rows = await wake_default.room_states(s)
    if not to_tuned and rooms:
        # Back to the published word: it must be on this server first, or
        # the rooms would be told to load a model they can't get.
        ok, msg = await wake_default.ensure_published(to, force=True)
        if not ok:
            log.warning("wake tuning: rooms stay on %s for now: %s", frm, msg)
            return {"moved": [], "waiting": [], "failed": rooms, "reason": msg}
    moved, waiting, failed = [], [], []
    for room in rooms:
        target = sessions.get(room)
        if target is None:
            waiting.append(room)
            continue
        if not satellite_abilities.session_abilities(target).allows("wake_models"):
            continue
        current = room_words_list(rows.get(room), room_words.get(room), room_lists.get(room))
        try:
            await _move_room(room, current, frm, to, session_push(target), session_scope)
        except Exception as e:  # noqa: BLE001 - the next hello tries again
            log.warning("wake tuning: room %s not moved to %s: %s", room, to, e)
            failed.append(room)
            continue
        moved.append(room)
    if moved:
        log.info("wake tuning: rooms %s moved from %s to %s", moved, frm, to)
    return {"moved": moved, "waiting": waiting, "failed": failed}


# ─── the core process's rooms, for the trainer worker ────────────────────

_APP = None


def attach(app) -> None:
    """The core's app, so the trainer worker can move the rooms when the
    copy finishes training (main.py's lifespan)."""
    global _APP
    _APP = app


async def after_trained(slug: str) -> dict[str, Any] | None:
    """The trainer worker trained ``slug``: if it is the household copy in
    use, move the rooms to it. None for any other word."""
    if not is_tuned(slug) or _APP is None:
        return None
    sessions = getattr(_APP.state, "active_sessions", None) or {}
    words = dict(getattr(_APP.state, "satellite_wake_word", None) or {})
    lists = dict(getattr(_APP.state, "satellite_wake_words", None) or {})
    return await apply(sessions, words, lists)
