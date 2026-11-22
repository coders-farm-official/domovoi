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

**Several words per room** (1.4.0, V063). A room may wake on up to
:data:`MAX_ROOM_WORDS` words, in order; the first is its main word, the one
everything above is about. ``room_wake_words.slugs`` keeps the list the
server last gave the room (``slug`` is always its first entry). The
household default stays one word: a new room gets one, and adding more is
an admin's choice on the room's Settings tab. Each word keeps its own
threshold, and each can fire on its own, so every extra word makes an
accidental wake-up a little more likely: the dashboard says so where words
are added.

A device says how many words it can run at once in its hello
(``wake_words_max``; satellite/wake_words.py: a Pi Zero 2 W runs 3, a
bigger Pi or a PC 5). The server never pushes more than that
(:func:`words_for_device`), and keeps the number
(``room_wake_words.device_max``) so the dashboard can say it while the room
is offline. A device that doesn't say (software from before 1.4.0, the
phone apps until they learn it) runs one word: it is sent the main word
only, in the ``set_wake_word`` frame it has always understood, and keeps
working. Which word woke a turn comes with the wake
(``utterance_start.wake_word``) and is kept on the turn's history row
(``conversation_log.wake_word``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
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
    #: The release asset's SHA-256 and size; None = not published yet.
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
        # Trained 2026-11-14 on synthetic voices only (its model card in the
        # release lists every input); the release asset's digest and size.
        sha256="71405e3858af1382a8ec06dd0d57dbcb5dabea829d67b083e8b084dbb175b3e3",
        size=205430,
    ),
}

#: Slugs a household's trained word may not take.
RESERVED_SLUGS = STOCK_SLUGS | frozenset(PUBLISHED)


def is_reserved_slug(slug: str) -> bool:
    """A slug no household word may be created under: the stock and
    published words', and a published word's household copy
    (``domovoi_v1_tuned``, made only by "Tune Domovoi", domovoi/wake_tuning.py)."""
    from domovoi import wake_tuning

    return slug in RESERVED_SLUGS or wake_tuning.is_tuned(slug)


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


# ─── several words per room (V063) ───────────────────────────────────────

#: The most wake words a room may have.
MAX_ROOM_WORDS = 5

#: What the dashboard says where words are added.
FALSE_WAKE_NOTE = "Each extra word makes accidental wake-ups a little more likely."

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def clean_slug(raw: object) -> str | None:
    """A wake word's slug as a device or the dashboard may send it (an
    openWakeWord model stem), else None."""
    if isinstance(raw, str) and _SLUG_RE.match(raw.strip()):
        return raw.strip()
    return None


def device_max(raw: object) -> int | None:
    """A hello's ``wake_words_max``: how many words the device can run at
    once, 1..MAX_ROOM_WORDS (more reads as the most a room may have). None
    when it didn't say or said something unusable: then it runs one."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    # CORE-47: json takes Infinity and NaN; int(inf) raised OverflowError
    # after pairing had passed, ending the room's own session.
    if isinstance(raw, float) and (not math.isfinite(raw) or raw != int(raw)):
        return None
    n = int(raw)
    if n < 1:
        return None
    return min(n, MAX_ROOM_WORDS)


def device_limit(declared: int | None) -> int:
    """How many words a device runs: what it said, else one."""
    return declared if declared else 1


def reported_words(raw: object) -> list[str] | None:
    """A hello's ``wake_words`` (the words the device listens for, main
    word first), cleaned: slug-shaped, no repeats, at most MAX_ROOM_WORDS.
    None when it didn't send a usable list."""
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for item in raw[:MAX_ROOM_WORDS * 2]:
        slug = clean_slug(item)
        if slug and slug not in out:
            out.append(slug)
    return out[:MAX_ROOM_WORDS] or None


def check_word_list(raw: object) -> list[str]:
    """An admin's list of words for a room, or ValueError saying why not:
    1 to MAX_ROOM_WORDS slugs, no repeats."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("a room needs at least one wake word")
    # CORE-47: the length first. Each slug is checked against the list so
    # far, so a long list cost its square on the event loop before this
    # said "at most 5".
    if len(raw) > MAX_ROOM_WORDS:
        raise ValueError(f"a room can have at most {MAX_ROOM_WORDS} wake words")
    out: list[str] = []
    for item in raw:
        slug = clean_slug(item)
        if slug is None:
            raise ValueError(f"{str(item)[:40]!r} isn't a wake word")
        if slug in out:
            raise ValueError(f"{word_name(slug)} is in the list twice")
        out.append(slug)
    if len(out) > MAX_ROOM_WORDS:
        raise ValueError(f"a room can have at most {MAX_ROOM_WORDS} wake words")
    return out


def words_for_device(words: list[dict[str, Any]], declared: int | None) -> list[dict[str, Any]]:
    """The words a device is sent: no more than it said it can run (one
    when it didn't say)."""
    return list(words[:device_limit(declared)])


def with_main_word(current: list[str] | None, slug: str) -> list[str]:
    """A room's list after one word is pushed to it the old way (Wake words
    → Push, "Use … here"): that word becomes the main word, the extras an
    admin added stay after it."""
    rest = [w for w in (current or [])[1:] if w != slug]
    return [slug, *rest][:MAX_ROOM_WORDS]


async def word_payload(s, slugs: list[str]) -> list[dict[str, Any]]:
    """``[{"slug", "threshold"}]`` for a push: a trained word carries its
    own threshold, the others none (the room's Wake sensitivity applies).
    ValueError naming the first word that can't be given now."""
    out = []
    for slug in slugs:
        word = await describe(s, slug)
        if not word["available"]:
            raise ValueError(f"{word['name']}: {word['reason']}")
        t = word["threshold"]
        # REAL in the registry: 0.62 reads back 0.6200000047...
        out.append({"slug": slug, "threshold": round(float(t), 4) if t is not None else None})
    return out


# ─── rooms ───────────────────────────────────────────────────────────────

#: A room's ``device_max``: the least any of its devices said it can run
#: (V072 moved it to ``satellites.wake_max``); NULL when none said.
_DEVICE_MAX = (
    "(SELECT min(sat.wake_max) FROM satellites sat "
    "WHERE sat.room_id = room_wake_words.room_id)"
)

async def room_state(s, room_id: str) -> dict | None:
    row = (await s.execute(
        text(f"SELECT state, slug, slugs, {_DEVICE_MAX} "
             "FROM room_wake_words WHERE room_id = :r"),
        {"r": room_id})).first()
    return ({"state": row[0], "slug": row[1], "slugs": list(row[2]) if row[2] else None,
             "device_max": row[3]} if row else None)


async def _put(s, room_id: str, state: str, slug: str | None,
               slugs: list[str] | None = None) -> None:
    """Write a room's row. ``slugs`` defaults to ``[slug]`` (one word);
    ``device_max`` is never touched here (:func:`record_device_max`)."""
    if slug is None:
        slugs = None
    elif not slugs or slugs[0] != slug:
        slugs = [slug]
    await s.execute(
        text("INSERT INTO room_wake_words (room_id, state, slug, slugs, updated_at) "
             "VALUES (:r, :st, :sl, :sls, now()) "
             "ON CONFLICT (room_id) DO UPDATE SET state = EXCLUDED.state, "
             "slug = EXCLUDED.slug, slugs = EXCLUDED.slugs, updated_at = now()"),
        {"r": room_id, "st": state, "sl": slug, "sls": slugs},
    )


async def mark_chosen(s, room_id: str, slug: str, slugs: list[str] | None = None) -> None:
    """An admin pushed ``slug`` (or the list ``slugs``, ``slug`` first) to
    the room: its own choice from now on, and the household default never
    applies to it again."""
    await _put(s, room_id, "chosen", slug, slugs)


async def record_device_max(s, room_id: str, declared: int | None) -> None:
    """Keep what the device said it can run (None: it didn't say, an older
    device). Kept on the device's row (V072 ``satellites.wake_max``);
    ``room_id`` is the device's id. A room's ``device_max`` is the least
    any of its devices can run."""
    from domovoi.db.repositories import SatellitesRepository, ensure_satellite

    await ensure_satellite(s, room_id)
    await SatellitesRepository(s).set_wake_max(room_id, declared)


Push = Callable[[str, "float | None"], Awaitable[None]]


async def on_hello(room_id: str, reported: str | None, can_push: bool, push: Push,
                   *, session_scope=None, wake_max: int | None = None,
                   record_max: bool = False) -> str:
    """A room said hello. Apply the household default if this room may
    still get it (a room seen for the first time, or a ``pending`` one).
    ``push(slug, threshold)`` sends ``set_wake_word``. Returns the room's
    state afterwards. Never raises for the caller's hello path.
    ``record_max``: keep ``wake_max`` (what the device said it can run,
    None for a device that didn't say) as the room's ``device_max``."""
    if session_scope is None:
        from domovoi.db.session import session_scope
    state = await _on_hello(room_id, reported, can_push, push, session_scope)
    if record_max and state != "error":
        try:
            async with session_scope() as s:
                await record_device_max(s, room_id, wake_max)
        except Exception as e:  # noqa: BLE001 - a hello never fails over this
            log.warning("wake words: room %s's limit not kept: %s", room_id, e)
    return state


async def _on_hello(room_id: str, reported: str | None, can_push: bool, push: Push,
                    session_scope) -> str:
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


# ─── which word a room answers to, by name ───────────────────────────────

#: What the dashboard says when nothing tells the server a room's word: a
#: room that existed before V054 (``kept``) on software that doesn't report
#: the model it loaded.
UNKNOWN_WORD_TEXT = "unknown until this room updates"

_STOCK_VERSION_RE = re.compile(r"^(?P<base>[a-z_]+)_v\d+(?:\.\d+)+\Z")


def word_name(slug: str, names: dict[str, str] | None = None) -> str:
    """A wake word's display name: a trained word's name (``names``, from
    the registry), Domovoi's published and built-in words' names, else the
    slug in words ("hey_mycroft" -> "Hey Mycroft"). A stock model file's
    stem ("hey_jarvis_v0.1") reads as its word."""
    names = names or {}
    if slug not in names and (m := _STOCK_VERSION_RE.match(slug)) and m.group("base") in STOCK_SLUGS:
        slug = m.group("base")
    if names.get(slug):
        return names[slug]
    if slug in PUBLISHED:
        return PUBLISHED[slug].name
    if slug in BUILTIN_WORDS:
        return BUILTIN_WORDS[slug][0]
    return " ".join(w.capitalize() for w in re.split(r"[_\-.\s]+", slug) if w) or slug


def room_word(reported: str | None, row: dict | None,
              names: dict[str, str] | None = None, *,
              reported_list: list[str] | None = None,
              declared_max: int | None = None) -> dict[str, Any]:
    """Which word a room wakes on, for the dashboard: ``{slug, name,
    source, pending_slug, pending_name}``.

    The word the room device reports (``source`` "device") wins: it is the
    model it loaded. Without one, the server's own record: the household
    default it was given (``assigned``) or the word an admin pushed
    (``chosen``); a room waiting for its word (``pending``) shows that word,
    as pending. Otherwise (a ``kept`` room on older software, or no record
    at all) the word is ``unknown``. ``pending_*`` names the word a room
    that reports its own is still waiting to be given. ``text`` is the
    line after "Wakes on:".

    Several words (V063): ``words`` is the room's list, main word first,
    each ``{slug, name, running}``: the words its device reported
    (``reported_list``, from its hello) are running; a word the server gave
    the room that the device isn't running (more than it can run, or its
    model not there yet) is listed with ``running`` false. ``device_max``
    is what the device said it can run (``declared_max``, else the row's
    ``device_max``), None when it never said; ``limit`` what it runs
    (one when it never said); ``max_words`` the most a room may have."""
    out = _room_main_word(reported, row, names)
    declared = declared_max if declared_max is not None else (row or {}).get("device_max")
    stored = list((row or {}).get("slugs") or [])
    running = list(reported_list or [])
    if out["source"] == "device" and running and running[0] != out["slug"]:
        running = []      # a hello from before the word's health report: not this list
    if running:
        slugs = running + [w for w in stored if w not in running]
        on = set(running)
    else:
        slugs = stored or ([out["slug"]] if out["slug"] else [])
        if out["slug"] and slugs and slugs[0] != out["slug"]:
            slugs = [out["slug"], *[w for w in slugs if w != out["slug"]]]
        on = set(slugs[:device_limit(declared)])
    out["words"] = [{"slug": w, "name": word_name(w, names), "running": w in on}
                    for w in slugs[:MAX_ROOM_WORDS]]
    out["device_max"] = declared
    out["limit"] = device_limit(declared)
    out["max_words"] = MAX_ROOM_WORDS
    if len(out["words"]) > 1 and out["source"] != "unknown":
        extra = ", ".join(w["name"] for w in out["words"][1:])
        out["text"] = f"{out['text']} (also {extra})"
    return out


def _room_main_word(reported: str | None, row: dict | None,
                    names: dict[str, str] | None = None) -> dict[str, Any]:
    state = (row or {}).get("state")
    slug = (row or {}).get("slug") or None
    out: dict[str, Any] = {"slug": None, "name": None, "source": "unknown",
                           "pending_slug": None, "pending_name": None,
                           "text": UNKNOWN_WORD_TEXT}
    if reported:
        name = word_name(reported, names)
        out.update(slug=reported, name=name, source="device", text=name)
        if state == "pending" and slug and slug != reported:
            waiting = word_name(slug, names)
            out.update(pending_slug=slug, pending_name=waiting,
                       text=f"{name} (waiting to switch to {waiting})")
        return out
    if state in ("assigned", "chosen", "pending") and slug:
        name = word_name(slug, names)
        out.update(slug=slug, name=name, source=state,
                   text=f"{name} (pending)" if state == "pending" else name)
    return out


async def word_names(s) -> dict[str, str]:
    """slug -> name of the household's trained words (the registry)."""
    rows = (await s.execute(text("SELECT slug, name FROM wake_words"))).all()
    return {r[0]: r[1] for r in rows if r[0] and r[1]}


async def room_states(s, room_id: str | None = None) -> dict[str, dict]:
    """room -> ``{state, slug, slugs, device_max}`` from ``room_wake_words``
    (one room, or all)."""
    cols = f"room_id, state, slug, slugs, {_DEVICE_MAX}"
    if room_id is None:
        rows = (await s.execute(text(f"SELECT {cols} FROM room_wake_words"))).all()
    else:
        rows = (await s.execute(
            text(f"SELECT {cols} FROM room_wake_words WHERE room_id = :r"),
            {"r": room_id})).all()
    return {r[0]: {"state": r[1], "slug": r[2], "slugs": list(r[3]) if r[3] else None,
                   "device_max": r[4]} for r in rows}


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
                + ("Turn internet access on (Settings → Privacy → Internet) so the server can download "
                   "them." if egress.internet_turned_off() else
                   "Refresh the satellite caches when the internet works "
                   "(Settings → Rooms → prepare satellite media)."))
    return {
        "default": current, "options": options, "pending": waiting, "note": note,
        "boot_word": BOOT_WORD, "boot_word_here": boot_here,
        "rooms": dict(sorted(room_words.items())),
    }
