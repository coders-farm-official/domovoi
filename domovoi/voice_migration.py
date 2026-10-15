"""Move an existing install off the Piper voices Domovoi no longer offers,
and off Norman, the house voice until 2026-10-12, onto Kristin.

Until 2026-10 the default voice was ``en_US-lessac-medium`` (research-only
licence), and the catalog offered amy, ryan and voices fine-tuned from
them. Domovoi now speaks only the three public-domain voices in
``SHIPPED_VOICES`` (domovoi/clients/tts.py). At boot, before the TTS client
is built:

* ``TTS_PIPER_VOICE`` naming a retired voice moves to
  ``DEFAULT_PIPER_VOICE`` (en_US-kristin-medium): rewritten in
  ``domovoi/.env`` when the line is there, and changed in the live settings
  either way (an OS environment variable can't be rewritten from here; the
  log says so). A voice that is one of the three, or an owner's own
  uploaded model, is kept as it is.
* The retired voices' model files (``<name>.onnx`` / ``.onnx.json``, exactly
  those names) are deleted from the voices folder, ``voice_models_dir`` —
  but only once one of the three shipped voices is on disk there, so a box
  whose new voice hasn't downloaded yet (no internet, a failed fetch) keeps
  the old files; a later boot deletes them. Nothing outside that folder is
  touched, and nothing reached through a link.

The registry rows of the retired voices are removed by
:func:`domovoi.voice_catalog.seed_voices`, and the new voice is fetched by
the speech warm-up (or on first use) through the egress gate.

**Norman to Kristin** (the owner's pick, 2026-10-12): the house voice moves
from Norman (``PREVIOUS_DEFAULT_PIPER_VOICE``) to Kristin
(``DEFAULT_PIPER_VOICE``) once, after the boot's registry seeding
(:func:`move_default_voice`, from :func:`run_default_voice_move`):

* the registry's default row, when it is Norman, becomes Kristin (her row
  is added when missing), and ``TTS_PIPER_VOICE``, when it is Norman,
  becomes Kristin the same way a retired voice's does above;
* only once Kristin is on disk: until then Norman keeps speaking, and the
  boot fetches her in the background through the egress gate (nothing
  under ``INTERNET_ACCESS=never``; the next boot tries again);
* once: a marker file (``voice-default-kristin.done`` beside the setup
  code, ``~/.domovoi``) records that the check ran, so a household that
  picks Norman again afterwards keeps him. Before this release an admin's
  pick of Norman and the shipped default looked the same in the registry
  (nothing recorded which), so every install still on Norman moves, and the
  release note says how to pick him again;
* a room that chose its own voice (Norman included) keeps it: rooms
  report their voice by name and only the default changes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from domovoi.clients.tts import (
    DEFAULT_PIPER_VOICE,
    PREVIOUS_DEFAULT_PIPER_VOICE,
    RETIRED_PIPER_VOICES,
    SHIPPED_VOICES,
)
from domovoi.config import settings

log = logging.getLogger(__name__)


def migrate_configured_voice(env_path: Path | None = None) -> str | None:
    """Move ``settings.tts_piper_voice`` off a retired voice. Returns the
    old value when it moved, else None."""
    old = (settings.tts_piper_voice or "").strip()
    if old not in RETIRED_PIPER_VOICES:
        return None
    from domovoi.config_env_writer import env_file_keys, write_env_values

    if "TTS_PIPER_VOICE" in {k.upper() for k in os.environ}:
        log.warning(
            "TTS_PIPER_VOICE=%s is set in the environment and names a voice Domovoi no longer "
            "offers; speaking with %s instead (change the environment to keep this)",
            old, DEFAULT_PIPER_VOICE,
        )
    elif "TTS_PIPER_VOICE" in env_file_keys(env_path):
        try:
            write_env_values({"tts_piper_voice": DEFAULT_PIPER_VOICE}, env_path)
        except OSError as e:
            log.warning("could not rewrite TTS_PIPER_VOICE in domovoi/.env: %s", e)
    settings.tts_piper_voice = DEFAULT_PIPER_VOICE
    log.info("voice: %s is no longer offered; the Piper voice is now %s", old, DEFAULT_PIPER_VOICE)
    return old


def shipped_voice_on_disk(root: Path) -> bool:
    """Whether one of the three shipped voices (model and config) sits in
    the voices folder ``root``."""
    return any((root / f"{v.model_ref}.onnx").is_file() and (root / f"{v.model_ref}.onnx.json").is_file()
               for v in SHIPPED_VOICES)


def remove_retired_voice_files(voices_dir: Path | None = None) -> list[str]:
    """Delete the retired voices' downloaded model files from the voices
    folder: ``<retired name>.onnx`` and ``.onnx.json`` directly inside it,
    not links, and only once a shipped voice is on disk there (until then
    an old voice file is the box's only neural voice on disk). Returns the
    file names removed."""
    vdir = Path(voices_dir or settings.voice_models_dir)
    removed: list[str] = []
    try:
        root = vdir.resolve()
    except OSError:
        return removed
    if not root.is_dir():
        return removed
    if not shipped_voice_on_disk(root):
        if any((root / f"{n}.onnx").exists() for n in RETIRED_PIPER_VOICES):
            log.info("voice: keeping the retired voice files in %s until a shipped voice is on disk", root)
        return removed
    for name in sorted(RETIRED_PIPER_VOICES):
        for suffix in (".onnx", ".onnx.json"):
            f = root / f"{name}{suffix}"
            try:
                if f.is_file() and not f.is_symlink() and f.resolve().parent == root:
                    f.unlink()
                    removed.append(f.name)
            except OSError as e:
                log.warning("could not remove %s: %s", f, e)
    if removed:
        log.info("voice: removed the retired voice files %s from %s", ", ".join(removed), root)
    return removed


# ─── Norman to Kristin ───────────────────────────────────────────────────

DEFAULT_MOVE_MARKER = "voice-default-kristin.done"


def default_move_marker() -> Path:
    """The file that records the Norman-to-Kristin check ran (beside
    ``setup-code.txt``)."""
    from domovoi.admin_auth import CONFIG_DIR

    return CONFIG_DIR / DEFAULT_MOVE_MARKER


def _write_marker(what: str) -> None:
    path = default_move_marker()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{what} {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n",
                        encoding="utf-8")
    except OSError as e:
        log.warning("voice: could not record the default-voice move in %s: %s", path, e)


def _move_configured(env_path: Path | None) -> None:
    """``TTS_PIPER_VOICE`` from Norman to Kristin, as for a retired voice."""
    from domovoi.config_env_writer import env_file_keys, write_env_values

    if "TTS_PIPER_VOICE" in {k.upper() for k in os.environ}:
        log.warning("TTS_PIPER_VOICE=%s is set in the environment; the house voice is now %s "
                    "(change the environment to keep this)", PREVIOUS_DEFAULT_PIPER_VOICE, DEFAULT_PIPER_VOICE)
    elif "TTS_PIPER_VOICE" in env_file_keys(env_path):
        try:
            write_env_values({"tts_piper_voice": DEFAULT_PIPER_VOICE}, env_path)
        except OSError as e:
            log.warning("could not rewrite TTS_PIPER_VOICE in domovoi/.env: %s", e)
    settings.tts_piper_voice = DEFAULT_PIPER_VOICE


async def move_default_voice(repo, *, env_path: Path | None = None) -> str:
    """The Norman-to-Kristin move, once (module docstring). ``repo``: a
    VoicesRepository (or a stand-in). Returns what happened: ``"done"``
    (the marker says it ran), ``"kept"`` (nothing on Norman; marked),
    ``"waiting"`` (Kristin not on disk yet; not marked), ``"moved"``
    (marked), or ``"unseeded"`` (no default row yet; not marked)."""
    from domovoi.clients.tts import piper_voice_on_disk

    if default_move_marker().exists():
        return "done"
    default = await repo.get_default()
    if default is None:
        return "unseeded"
    registry_norman = default["engine"] == "piper" and default["model_ref"] == PREVIOUS_DEFAULT_PIPER_VOICE
    config_norman = (settings.tts_piper_voice or "").strip() == PREVIOUS_DEFAULT_PIPER_VOICE
    if not (registry_norman or config_norman):
        _write_marker("kept")
        return "kept"
    if not piper_voice_on_disk(DEFAULT_PIPER_VOICE):
        log.info("voice: the house voice moves from Norman to Kristin once she is on disk")
        return "waiting"
    if registry_norman:
        rows = await repo.all()
        kristin = next((v for v in rows if v["engine"] == "piper" and v["model_ref"] == DEFAULT_PIPER_VOICE), None)
        if kristin is None:
            name = next(v.name for v in SHIPPED_VOICES if v.model_ref == DEFAULT_PIPER_VOICE)
            taken = {v["name"].lower() for v in rows}
            vid = await repo.create(name=name if name.lower() not in taken else DEFAULT_PIPER_VOICE,
                                    engine="piper", model_ref=DEFAULT_PIPER_VOICE)
        else:
            vid = kristin["id"]
        await repo.set_default(vid)
    if config_norman:
        _move_configured(env_path)
    _write_marker("moved")
    log.info("voice: the house voice is now Kristin (it was Norman); pick Norman again in "
             "Settings -> Voices to keep him")
    return "moved"


async def run_default_voice_move() -> str:
    """The boot's Norman-to-Kristin move, after the registry seeding: at
    once when Kristin is on disk, else after fetching her in a worker thread
    (through the egress gate; nothing under ``never``). Never raises."""
    from domovoi import egress
    from domovoi.clients import tts
    from domovoi.db.repositories import VoicesRepository
    from domovoi.db.session import session_scope

    async def attempt() -> str:
        async with session_scope() as s:
            return await move_default_voice(VoicesRepository(s))

    try:
        result = await attempt()
        if result == "waiting" and egress.internet_allowed():
            try:
                await asyncio.to_thread(tts._piper_voice_path, DEFAULT_PIPER_VOICE)
            except Exception as e:  # noqa: BLE001 - a failed fetch is tried again next boot
                log.warning("voice: could not fetch Kristin (%s); Norman keeps speaking until she is on disk", e)
                return result
            result = await attempt()
        if result == "moved":
            tts.reset_tts_client()   # its fallback voice is the configured one
        return result
    except Exception as e:  # noqa: BLE001 - never fatal at boot
        log.warning("voice: the default-voice move raised: %s", e)
        return "error"
