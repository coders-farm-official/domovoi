"""The household's default wake word, and Domovoi's published wake word.

A new install's rooms wake on "Domovoi": a wake-word model Coders Farm
publishes in its own repository (:data:`MODELS_REPO`), one file per
release under a tag that never moves, pinned here by SHA-256 and size. It
is not part of Domovoi (MIT): its training data makes it CC BY-NC-SA 4.0
(docs/LICENSES.md), so, like openWakeWord's "Hey Jarvis", each server
downloads it itself, through the internet gate, and keeps it with the
household's trained models (``settings.wake_models_dir``). From there the
signed ``/v1/wake-models`` channel carries it to every room kind: a Pi, a
PC and a phone in room mode take it the way they take a trained word.

**Which rooms get it.** Nothing changes the word of a room that already
exists. The rule (decided 2026-11-14, V054):

* The server keeps one *household default* (``household_settings``
  ``wake_default``). V054 set it once: the household's own default trained
  word if it had one, else ``hey_jarvis`` on a server that had ever had a
  room (an upgrade), else ``domovoi_v1`` (a new install). An admin changes
  it on Settings → Voice → Wake words.
* Every room has a row in ``room_wake_words``. Rooms that existed before
  V054 are ``kept``: never touched. A room connecting for the first time
  is given the household default, by the same push the dashboard uses for
  a trained word (``set_wake_word``), and becomes ``assigned``. An admin
  pushing any word to a room makes it ``chosen``. Neither is touched again.
* A room's own boot word (``[wake] wake_word`` in its config, the phone's
  built-in) stays ``hey_jarvis``: it is the word a device can load from the
  stock openWakeWord files before it has ever reached its server.

**When the model isn't here** (internet access "never", no connection, the
release unreachable, or not published yet): a new room is ``pending``. It
keeps waking on its boot word, "Hey Jarvis", while that model is on the
server, and the dashboard says why. When the model arrives, the pending
rooms are given it. When neither model is on the server, the dashboard
says plainly that rooms can't wake yet and what would fix it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from domovoi import egress
from domovoi.config import settings

log = logging.getLogger(__name__)

#: Where the published wake words live (CC BY-NC-SA 4.0, not part of Domovoi).
MODELS_REPO = "coders-farm-official/domovoi-wake-word-models"

#: The word a room boots on before it has reached its server, and what a
#: new room waits on while the household default can't be given yet.
BOOT_WORD = "hey_jarvis"

#: The household default a new install starts with (V054).
NEW_INSTALL_WORD = "domovoi_v1"

#: openWakeWord's own words, which every room loads from the stock files
#: (domovoi/satellite_media/fetchers.py OWW_MODEL_FILES). Only these are
#: offered; the others ship with openWakeWord but Domovoi doesn't use them.
BUILTIN_WORDS: dict[str, tuple[str, str]] = {
    "hey_jarvis": ("Hey Jarvis", "hey jarvis"),
}

#: openWakeWord's stock model names: no trained word may take one of these
#: slugs, or a room would load the stock model for it.
STOCK_SLUGS = frozenset({"hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy",
                         "timer", "weather"})


@dataclass(frozen=True)
class PublishedWord:
    slug: str
    name: str
    phrase: str
    tag: str
    #: Pinned when the release exists; None = not published yet.
    sha256: str | None
    size: int | None

    @property
    def file(self) -> str:
        return f"{self.slug}.onnx"

    @property
    def url(self) -> str:
        return f"https://github.com/{MODELS_REPO}/releases/download/{self.tag}/{self.file}"

    @property
    def pinned(self) -> bool:
        return bool(self.sha256) and bool(self.size)


#: Domovoi's published wake words. A release's tag never moves and a new
#: model is a new slug and tag (domovoi_v2), never a new file under an old
#: one, so a room that was given domovoi_v1 keeps exactly that model.
PUBLISHED: dict[str, PublishedWord] = {
    "domovoi_v1": PublishedWord(
        slug="domovoi_v1", name="Domovoi", phrase="domovoi", tag="domovoi-v1",
        sha256=None, size=None,
    ),
}

#: Slugs a household's trained word may not take.
RESERVED_SLUGS = STOCK_SLUGS | frozenset(PUBLISHED)


def is_reserved_slug(slug: str) -> bool:
    return slug in RESERVED_SLUGS


# ─── the published model on this server ──────────────────────────────────

_DIGESTS: dict[tuple[str, int, int], str] = {}


def _models_dir() -> Path:
    return Path(settings.wake_models_dir)


def published_path(word: PublishedWord) -> Path:
    return _models_dir() / word.file


def _digest(p: Path) -> str:
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)
    hit = _DIGESTS.get(key)
    if hit is None:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for block in iter(lambda: f.read(1 << 16), b""):
                h.update(block)
        hit = _DIGESTS[key] = h.hexdigest()
    return hit


def published_ready(slug: str) -> bool:
    """The published word's model is here, at its pinned size and digest."""
    word = PUBLISHED.get(slug)
    if word is None or not word.pinned:
        return False
    p = published_path(word)
    try:
        return (not p.is_symlink() and p.is_file() and p.stat().st_size == word.size
                and _digest(p) == word.sha256)
    except OSError:
        return False


def remove_unverified_published() -> list[str]:
    """Drop a published word's file that doesn't match its pin (a partial
    copy, a replaced file): rooms must never be served it. Names removed."""
    gone = []
    for word in PUBLISHED.values():
        p = published_path(word)
        if (p.exists() or p.is_symlink()) and not published_ready(word.slug):
            try:
                p.unlink()
                gone.append(word.file)
            except OSError as e:
                log.warning("wake default: could not remove unverified %s: %s", p, e)
    return gone


#: Why the last download failed, per slug (for the dashboard).
_LAST_FAILURE: dict[str, str] = {}
_LAST_TRY: dict[str, float] = {}
_FETCH_LOCK = threading.Lock()
#: A pending room's connection retries a failed download at most this often.
RETRY_SEC = 15 * 60
#: Under USE_STUBS (the test suite) nothing is downloaded unless a test
#: sets this (with the download itself replaced).
ALLOW_DOWNLOAD_UNDER_STUBS = False


def not_here_reason(slug: str) -> str:
    """Why a published word's model is not on this server, in a sentence."""
    word = PUBLISHED[slug]
    if not word.pinned:
        return (f"the {word.name} wake word isn't published yet, so this version of "
                "Domovoi can't download it")
    if egress.internet_turned_off():
        return f"{egress.TURNED_OFF_REASON}, so the {word.name} wake word can't be downloaded"
    failed = _LAST_FAILURE.get(slug)
    if failed:
        return f"downloading the {word.name} wake word failed: {failed}"
    return f"the {word.name} wake word hasn't been downloaded yet"


def fetch_published(slug: str) -> tuple[bool, str]:
    """Download a published word into the wake-models folder, held to its
    pin (blocking; run it in a thread). Refused under internet access
    "never" without any request. (ok, message)."""
    from domovoi.satellite_media import fetchers

    word = PUBLISHED[slug]
    _LAST_TRY[slug] = time.monotonic()
    if published_ready(slug):
        _LAST_FAILURE.pop(slug, None)
        return True, f"the {word.name} wake word is here"
    if not word.pinned or egress.internet_turned_off():
        return False, not_here_reason(slug)
    if settings.use_stubs and not ALLOW_DOWNLOAD_UNDER_STUBS:
        # The test suite never downloads: a test that means to sets this.
        return False, "downloads are off under USE_STUBS"
    dest = published_path(word)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        fetchers._fetch_pinned(word.url, dest, sha256=word.sha256, size=word.size)
    except Exception as e:  # noqa: BLE001 - network, HTTP, pin: the page says which
        _LAST_FAILURE[slug] = str(e)[:300]
        log.warning("wake default: %s download refused: %s", word.file, e)
        return False, not_here_reason(slug)
    _LAST_FAILURE.pop(slug, None)
    log.info("wake default: %s downloaded and verified", word.file)
    return True, f"the {word.name} wake word is downloaded and verified"


async def ensure_published(slug: str, *, force: bool = False) -> tuple[bool, str]:
    """:func:`fetch_published`, one at a time, and not again within
    :data:`RETRY_SEC` of a failed try unless ``force``."""
    if published_ready(slug):
        return True, f"the {PUBLISHED[slug].name} wake word is here"
    last = _LAST_TRY.get(slug)
    if not force and last is not None and time.monotonic() - last < RETRY_SEC:
        return False, not_here_reason(slug)
    def locked() -> tuple[bool, str]:
        with _FETCH_LOCK:
            return fetch_published(slug)

    return await asyncio.to_thread(locked)


def reset_for_tests() -> None:
    _LAST_FAILURE.clear()
    _LAST_TRY.clear()
    _DIGESTS.clear()


# ─── the household default ───────────────────────────────────────────────

KEY = "wake_default"


async def household_default(s) -> str:
    """The word a new room is given. No setting (it was removed by hand)
    reads as the boot word: a missing row never switches anyone."""
    row = (await s.execute(
        text("SELECT value FROM household_settings WHERE key = :k"), {"k": KEY})).first()
    slug = (row[0] or {}).get("slug") if row else None
    return slug if isinstance(slug, str) and slug else BOOT_WORD


async def _trained(s, slug: str) -> dict | None:
    from domovoi.db.repositories import WakeWordsRepository

    return await WakeWordsRepository(s).get_by_slug(slug)


async def describe(s, slug: str) -> dict[str, Any]:
    """A word as the dashboard shows it: name, phrase, kind, whether rooms
    can be given it now, and why not."""
    if slug in BUILTIN_WORDS:
        name, phrase = BUILTIN_WORDS[slug]
        return {"slug": slug, "name": name, "phrase": phrase, "kind": "builtin",
                "available": True, "reason": None, "threshold": None}
    if slug in PUBLISHED:
        word = PUBLISHED[slug]
        ok = published_ready(slug)
        return {"slug": slug, "name": word.name, "phrase": word.phrase, "kind": "published",
                "available": ok, "reason": None if ok else not_here_reason(slug),
                "threshold": None}
    row = await _trained(s, slug)
    if row is None:
        return {"slug": slug, "name": slug, "phrase": None, "kind": "unknown",
                "available": False, "reason": "no wake word with that name is on this server",
                "threshold": None}
    ok = row["status"] == "ready" and bool(row["model_ref"])
    return {"slug": slug, "name": row["name"], "phrase": row["phrase"], "kind": "trained",
            "available": ok, "reason": None if ok else "it isn't trained yet",
            "threshold": row.get("threshold")}


async def set_household_default(s, slug: str) -> dict[str, Any]:
    """Make ``slug`` the word new rooms are given. Refused (ValueError) for
    a word that doesn't exist or a trained word that isn't ready; a
    published word that isn't downloaded yet is allowed (new rooms wait on
    their boot word until it is). Keeps ``wake_words.is_default`` in step.
    Existing rooms are not changed."""
    import json

    word = await describe(s, slug)
    if word["kind"] == "unknown":
        raise ValueError(word["reason"])
    if word["kind"] == "trained" and not word["available"]:
        raise ValueError(f"{word['name']} isn't trained yet")
    await s.execute(
        text("INSERT INTO household_settings (key, value, updated_at) "
             "VALUES (:k, CAST(:v AS JSONB), now()) "
             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
        {"k": KEY, "v": json.dumps({"slug": slug, "set_by": "admin"})},
    )
    await s.execute(text("UPDATE wake_words SET is_default = FALSE WHERE is_default"))
    if word["kind"] == "trained":
        await s.execute(
            text("UPDATE wake_words SET is_default = TRUE, updated_at = now() WHERE slug = :s"),
            {"s": slug})
    return word


# ─── rooms ───────────────────────────────────────────────────────────────

async def room_state(s, room_id: str) -> dict | None:
    row = (await s.execute(
        text("SELECT state, slug FROM room_wake_words WHERE room_id = :r"),
        {"r": room_id})).first()
    return {"state": row[0], "slug": row[1]} if row else None


async def _put(s, room_id: str, state: str, slug: str | None) -> None:
    await s.execute(
        text("INSERT INTO room_wake_words (room_id, state, slug, updated_at) "
             "VALUES (:r, :st, :sl, now()) "
             "ON CONFLICT (room_id) DO UPDATE SET state = EXCLUDED.state, "
             "slug = EXCLUDED.slug, updated_at = now()"),
        {"r": room_id, "st": state, "sl": slug},
    )


async def mark_chosen(s, room_id: str, slug: str) -> None:
    """An admin pushed ``slug`` to the room: it is the room's own choice
    from now on, and the household default never applies to it again."""
    await _put(s, room_id, "chosen", slug)


Push = Callable[[str, "float | None"], Awaitable[None]]


async def on_hello(room_id: str, reported: str | None, can_push: bool, push: Push,
                   *, session_scope=None) -> str:
    """A room said hello. Apply the household default if this room may
    still get it (a room seen for the first time, or a ``pending`` one).
    ``push(slug, threshold)`` sends ``set_wake_word``. Returns the room's
    state afterwards. Never raises for the caller's hello path."""
    if session_scope is None:
        from domovoi.db.session import session_scope
    try:
        async with session_scope() as s:
            row = await room_state(s, room_id)
            if row is not None and row["state"] != "pending":
                return row["state"]
            if not can_push:
                # This device can't take a pushed word: it keeps its own,
                # and an update that teaches it to won't switch it later.
                await _put(s, room_id, "kept", None)
                return "kept"
            desired = await household_default(s)
            if reported == desired:
                await _put(s, room_id, "assigned", desired)
                return "assigned"
            word = await describe(s, desired)
        if not word["available"] and word["kind"] == "published":
            ok, _msg = await ensure_published(desired)
            if ok:
                word["available"] = True
        if not word["available"]:
            async with session_scope() as s:
                await _put(s, room_id, "pending", desired)
            log.info("wake default: room %s waits on its boot word (%s)", room_id,
                     word["reason"])
            return "pending"
        await push(desired, word["threshold"])
        async with session_scope() as s:
            await _put(s, room_id, "assigned", desired)
        log.info("wake default: room %s given the household word %s", room_id, desired)
        return "assigned"
    except Exception as e:  # noqa: BLE001 - a hello never fails over this
        log.warning("wake default: room %s not handled: %s", room_id, e)
        return "error"


async def pending_rooms(s) -> list[dict[str, Any]]:
    rows = (await s.execute(text(
        "SELECT room_id, slug FROM room_wake_words WHERE state = 'pending' ORDER BY room_id"
    ))).all()
    return [{"room_id": r[0], "slug": r[1]} for r in rows]


async def give_pending(sessions: dict, *, session_scope=None) -> list[str]:
    """Give every connected pending room its household word, now that it
    may be here (after a download, or a change of the household default).
    Room ids given it."""
    from domovoi import satellite_abilities

    if session_scope is None:
        from domovoi.db.session import session_scope
    given = []
    async with session_scope() as s:
        waiting = await pending_rooms(s)
    for row in waiting:
        target = sessions.get(row["room_id"])
        if target is None:
            continue
        state = await on_hello(
            row["room_id"], None,
            satellite_abilities.session_abilities(target).allows("wake_models"),
            lambda slug, thr, t=target: t.request_set_wake_word(slug=slug, threshold=thr),
            session_scope=session_scope)
        if state == "assigned":
            given.append(row["room_id"])
    return given


async def status(s, room_words: dict[str, str]) -> dict[str, Any]:
    """What Settings → Voice → Wake words shows about the household word:
    the default, the words it can be set to, rooms waiting for it, and
    whether rooms can wake at all on this server."""
    from domovoi import wake_base_models
    from domovoi.db.repositories import WakeWordsRepository

    default = await household_default(s)
    current = await describe(s, default)
    options = [await describe(s, slug) for slug in (*PUBLISHED, *BUILTIN_WORDS)]
    for row in await WakeWordsRepository(s).all():
        if row["status"] == "ready" and row["model_ref"] and row["slug"] not in RESERVED_SLUGS:
            options.append(await describe(s, row["slug"]))
    waiting = await pending_rooms(s)
    boot_here = f"{BOOT_WORD}_v0.1.onnx" in await asyncio.to_thread(wake_base_models.served_models)
    if current["available"]:
        note = None
    elif boot_here:
        note = (f"New rooms wake on \"{BUILTIN_WORDS[BOOT_WORD][0]}\" until they can be given "
                f"\"{current['name']}\": {current['reason']}.")
    else:
        note = (f"New rooms can't wake yet: {current['reason']}, and the \"Hey Jarvis\" model "
                "they would wake on meanwhile isn't on this server either. "
                + ("Turn internet access on (Settings → Internet) so the server can download "
                   "them." if egress.internet_turned_off() else
                   "Refresh the satellite caches when the internet works "
                   "(Settings → Rooms → prepare satellite media)."))
    return {
        "default": current, "options": options, "pending": waiting, "note": note,
        "boot_word": BOOT_WORD, "boot_word_here": boot_here,
        "rooms": dict(sorted(room_words.items())),
    }
