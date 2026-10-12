"""Settings → Voice: which of the three voices the server speaks with, and
the owner's "say it like" list. Shared by the core (it speaks with them)
and the web backend (``/api/voice``, which the phone reads too).

* **The voices**: the three public-domain Piper voices in
  ``SHIPPED_VOICES`` (domovoi/clients/tts.py). The server's choice is the
  voice registry's default when that is one of them (Settings → Voice sets
  it; rooms without a voice of their own speak in it), else the configured
  ``TTS_PIPER_VOICE``.
* **The pronunciation list**: the shipped defaults
  (domovoi/phonemizer/data/pronunciations_default.txt) overlaid with the
  owner's changes in the ``pronunciations`` table (V027): a row with
  ``say_like`` sets or changes a word, a row without one removes a default.
  The core loads the effective list into the converter at boot and again
  when the web backend says it changed (``POST
  /v1/admin/pronunciations/reload``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from domovoi.clients.tts import SHIPPED_VOICES
from domovoi.config import settings

log = logging.getLogger(__name__)

# The shipped list's own date: what ``updated_at`` says until the owner
# changes anything.
DEFAULTS_UPDATED_AT = datetime(2026, 10, 12, tzinfo=timezone.utc)
# More words than any household needs; keeps one admin's slip from
# growing the table (and every reply's lookup) without bound. Counts the
# owner's own words; the rows of the shipped defaults (at most one each)
# come on top.
MAX_PRONUNCIATIONS = 500


def default_pronunciations() -> list[tuple[str, str]]:
    from domovoi.phonemizer import DATA_DIR, load_default_pronunciations

    return load_default_pronunciations(DATA_DIR)


def effective_pronunciations(rows: list[dict]) -> list[tuple[str, str]]:
    """Defaults (file order) with the owner's rows applied, then the
    owner's own words (in the rows' order)."""
    from domovoi.phonemizer import PronunciationError, pronunciation_key

    by_key = {r["key"]: r for r in rows}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for word, say in default_pronunciations():
        try:
            key = pronunciation_key(word)
        except PronunciationError:
            continue
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            out.append((word, say))
        elif row["say_like"]:
            out.append((row["word"], row["say_like"]))
    for r in rows:
        if r["key"] not in seen and r["say_like"]:
            out.append((r["word"], r["say_like"]))
    return out


def updated_at(rows: list[dict], changed: datetime | None = None) -> datetime:
    times = [r["updated_at"] for r in rows if r.get("updated_at") is not None]
    if changed is not None:
        times.append(changed)
    return max([DEFAULTS_UPDATED_AT, *times])


def default_keys() -> set[str]:
    from domovoi.phonemizer import PronunciationError, pronunciation_key

    keys = set()
    for word, _ in default_pronunciations():
        try:
            keys.add(pronunciation_key(word))
        except PronunciationError:
            continue
    return keys


def owner_words(rows: list[dict]) -> int:
    """How many words of the owner's own the rows hold (what
    MAX_PRONUNCIATIONS caps; a default's row doesn't count)."""
    defaults = default_keys()
    return sum(1 for r in rows if r["key"] not in defaults)


async def load_pronunciations(session) -> tuple[list[tuple[str, str]], datetime]:
    """(effective list, when it last changed)."""
    from domovoi.db.repositories import PronunciationsRepository

    repo = PronunciationsRepository(session)
    rows = await repo.all()
    return effective_pronunciations(rows), updated_at(rows, await repo.last_change())


async def apply_pronunciations() -> list[str]:
    """Load the effective list into the shared converter (core). An entry
    the converter can't use is left out and logged; returns their errors.
    A database that can't be read leaves the list as it is."""
    from domovoi.db.session import session_scope
    from domovoi.phonemizer import get_phonemizer

    try:
        async with session_scope() as s:
            pairs, _ = await load_pronunciations(s)
    except Exception as e:  # noqa: BLE001 - keep speaking with the list in force
        log.warning("pronunciations: could not read the list (%s); keeping the one in force", e)
        return []
    import asyncio

    ph = await asyncio.to_thread(get_phonemizer)
    errors = ph.set_pronunciations(pairs, skip_bad=True)
    for e in errors:
        log.warning("pronunciations: left out an entry the converter can't use: %s", e)
    log.info("pronunciations: %d in force", len(pairs) - len(errors))
    return errors


def shipped_voices() -> list[dict]:
    """The three voices, as ``GET /api/voice`` lists them."""
    return [
        {
            "id": v.model_ref,
            "name": v.name,
            "sex": v.sex,
            "sample_rate": v.sample_rate,
            "size_bytes": v.onnx_bytes,
            "sha256": v.onnx_sha256,
        }
        for v in SHIPPED_VOICES
    ]


async def server_voice(session) -> str:
    """The voice the server speaks with: the registry default when it is a
    shipped voice, else the configured Piper voice."""
    from domovoi.db.repositories import VoicesRepository

    shipped = {v.model_ref for v in SHIPPED_VOICES}
    default = await VoicesRepository(session).get_default()
    if default and default["engine"] == "piper" and default["model_ref"] in shipped:
        return default["model_ref"]
    return settings.tts_piper_voice


async def voice_payload(session) -> dict:
    """The body of ``GET /api/voice``. ``updated_at`` moves when the list
    or the server's voice changes (the newest of the two)."""
    from sqlalchemy import text

    pairs, changed = await load_pronunciations(session)
    picked = (await session.execute(text("SELECT max(updated_at) FROM voices WHERE is_default"))).scalar()
    if picked is not None and picked > changed:
        changed = picked
    return {
        "voices": shipped_voices(),
        "server_voice": await server_voice(session),
        "pronunciations": [{"word": w, "say_like": s} for w, s in pairs],
        "updated_at": changed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
