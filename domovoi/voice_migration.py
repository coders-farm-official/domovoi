"""Move an existing install off the Piper voices Domovoi no longer offers.

Until 2026-10 the default voice was ``en_US-lessac-medium`` (research-only
licence), and the catalog offered amy, ryan and voices fine-tuned from
them. Domovoi now speaks only the three public-domain voices in
``SHIPPED_VOICES`` (domovoi/clients/tts.py). At boot, before the TTS client
is built:

* ``TTS_PIPER_VOICE`` naming a retired voice moves to
  ``DEFAULT_PIPER_VOICE`` (en_US-norman-medium): rewritten in
  ``domovoi/.env`` when the line is there, and changed in the live settings
  either way (an OS environment variable can't be rewritten from here; the
  log says so). A voice that is one of the three, or an owner's own
  uploaded model, is kept as it is.
* The retired voices' model files (``<name>.onnx`` / ``.onnx.json``, exactly
  those names) are deleted from the voices folder, ``voice_models_dir``.
  Nothing outside that folder is touched.

The registry rows of the retired voices are removed by
:func:`domovoi.voice_catalog.seed_voices`, and the new voice is fetched by
the speech warm-up (or on first use) through the egress gate.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from domovoi.clients.tts import DEFAULT_PIPER_VOICE, RETIRED_PIPER_VOICES
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


def remove_retired_voice_files(voices_dir: Path | None = None) -> list[str]:
    """Delete the retired voices' downloaded model files from the voices
    folder. Only ``<retired name>.onnx`` and ``.onnx.json`` directly inside
    it; returns the file names removed."""
    vdir = Path(voices_dir or settings.voice_models_dir)
    removed: list[str] = []
    try:
        root = vdir.resolve()
    except OSError:
        return removed
    if not root.is_dir():
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
