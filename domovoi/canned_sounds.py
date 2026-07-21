"""Per-voice pre-rendered MP3s the satellites play locally.

Two kinds of clip live here, both rendered satellite-side-but-authored
domovoi-side:

- **Canned** (``network_issues.mp3``) — played when the core
  isn't reachable. Committing a static file would mean it's in a different
  voice than everything else the bot says; rendering it keeps it in the
  satellite's own voice.
- **Greetings** — short wake-word acknowledgments (bank lives in the
  ``client_greetings`` table, managed from the web dashboard).
- **sample.mp3** — a fixed "this is how I sound" line, used by the
  voice-sampling flow's fallback.

Everything is rendered **per registered voice** (the ``voices`` registry)
into ``<sounds_dir>/voices/<slug>/…`` (``settings.sounds_dir``, a
runtime artifact dir under ``~/.domovoi/`` — NOT the repo's ``satellite/``
tree) so each satellite syncs its own voice's clips and the greeting voice
always matches the response voice. Rendering goes through the engine-aware
TTS client (Piper or Edge, per the voice's ``engine``), and the resulting
WAV is encoded to MP3 (``lameenc``) because the Pi plays clips via
``mpg123``.

How it works:

1. On domovoi startup (and on any web-dashboard mutation),
   ``regenerate_if_needed()`` walks every registered voice.
2. For each clip it checks a sidecar marker next to the MP3; if the
   marker is missing or disagrees with the voice's engine/model or the
   clip text, it re-renders the MP3 + rewrites the marker.
3. Satellites pull their voice's subtree via ``/v1/sounds/manifest?voice=``
   (see ``satellite/sound_sync.py``), so a voice/greeting change reaches
   the Pi with no manual rsync.

Failures are non-fatal: a missing dep / network drop / DB hiccup logs and
continues, leaving whatever MP3 the Pi already has — better than silence.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import wave
from pathlib import Path

from domovoi.config import settings
from domovoi.db.repositories import ClientGreetingsRepository, VoicesRepository
from domovoi.db.session import session_scope

log = logging.getLogger(__name__)


# Server-private runtime artifact dir (settings.sounds_dir, default
# ~/.domovoi/sounds). The core renders here and serves it over
# /v1/sounds/; satellites pull into their own ~/.domovoi/sounds/ cache. Kept
# out of the repo's satellite/ tree — these are generated, not source.
_SOUNDS_DIR = Path(settings.sounds_dir)
# Per-voice rendered clips live under sounds/voices/<slug>/.
_VOICES_ROOT = _SOUNDS_DIR / "voices"


def voice_slug(name: str) -> str:
    """Filesystem-safe directory name for a voice's spoken ``name``.

    Lowercased, non-alphanumerics collapsed to ``_``. The renderer and the
    HTTP serving endpoints both use this so a satellite asking for
    ``?voice=Ryan`` lands in the same ``voices/ryan/`` subtree the renderer
    wrote."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "voice"


def voice_dir(name: str) -> Path:
    return _VOICES_ROOT / voice_slug(name)


# Each entry: (filename, sidecar_filename, text). Add new canned lines
# here and they'll regenerate alongside on the next voice change.
_CANNED: list[tuple[str, str, str]] = [
    (
        "network_issues.mp3",
        "network_issues.voice",
        (
            "Sorry, I'm having trouble reaching the network. "
            "Try moving me closer to the WiFi router, or have someone "
            "check on me later."
        ),
    ),
]


def _sample_entry() -> tuple[str, str, str]:
    """The per-voice sample clip the voice-sampling flow falls back to.

    Text uses the configured bot name so a BOT_NAME change re-renders it
    (the resolved text is what's hashed into the sidecar)."""
    return (
        "sample.mp3",
        "sample.voice",
        f"Hi, I'm {settings.bot_name}. This is how I sound.",
    )


# Wake-word greetings — short acknowledgments the satellite plays the
# instant the wake word fires (see satellite/client.py `_play_greeting`).
# The bank lives in the `client_greetings` table; each enabled row renders
# to greetings/greet_<hash>.mp3 (generic) or greet_funny_<hash>.mp3 (the
# prefix is how the Pi weights selection). `{name}` is the configured bot
# name, filled in at render time so it tracks BOT_NAME.
def _greeting_entries(rows: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """Build (mp3_name, sidecar_name, resolved_text) from ``(text, category)``
    greeting rows. ``{name}`` → ``settings.bot_name``; deduped by filename.
    Pure — no DB — so it unit-tests without Postgres. mp3 names are relative
    to a voice's ``greetings/`` subdir."""
    name = settings.bot_name
    seen: set[str] = set()
    entries: list[tuple[str, str, str]] = []
    for template, category in rows:
        prefix = "greet_funny" if category == "funny" else "greet"
        resolved = template.replace("{name}", name)
        stem = f"{prefix}_{_hash(resolved)[:8]}"
        mp3 = f"{stem}.mp3"
        if mp3 in seen:
            continue
        seen.add(mp3)
        entries.append((mp3, f"{stem}.voice", resolved))
    return entries


def _wav_to_mp3(wav_bytes: bytes, *, bitrate: int = 128) -> bytes | None:
    """Encode int16 WAV bytes (what the TTS client returns) to MP3 via
    lameenc. Returns None on empty audio or a missing/failing encoder so
    the caller falls through to the existing (possibly stale) clip."""
    try:
        import lameenc
    except ImportError:
        log.warning("lameenc not installed; cannot encode clips to MP3")
        return None
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            nch = wf.getnchannels()
            sr = wf.getframerate()
            sampwidth = wf.getsampwidth()
            pcm = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError) as e:
        log.warning("clip WAV could not be read for MP3 encode: %s", e)
        return None
    if not pcm or sampwidth != 2:
        return None
    try:
        enc = lameenc.Encoder()
        enc.set_bit_rate(bitrate)
        enc.set_in_sample_rate(sr)
        enc.set_channels(nch)
        enc.set_quality(2)
        mp3 = enc.encode(pcm) + enc.flush()
    except Exception as e:
        log.warning("lameenc encode failed: %s", e)
        return None
    return bytes(mp3) or None


async def _synth_clip_mp3(text: str, engine: str, model_ref: str) -> bytes | None:
    """Render ``text`` in a specific voice to MP3 bytes. Routes through the
    engine-aware TTS client (so the clip voice matches response voice) and
    encodes the returned WAV to MP3. None on any failure."""
    from domovoi.clients.tts import get_tts_client

    try:
        wav = await get_tts_client().synthesize(text, engine=engine, voice=model_ref)
    except Exception as e:
        log.warning("clip synth failed (engine=%s voice=%s): %s", engine, model_ref, e)
        return None
    return _wav_to_mp3(wav)


def _marker(engine: str, model_ref: str) -> str:
    """First sidecar line — identifies the rendering voice so an engine or
    model change re-renders."""
    return f"{engine}|{model_ref}"


def _needs_regen(mp3: Path, sidecar: Path, marker: str, text: str) -> bool:
    """True if the MP3 is missing or the sidecar disagrees with the current
    voice marker or clip text. Sidecar is two lines: voice marker, then a
    short hash of the text."""
    if not mp3.exists() or not sidecar.exists():
        return True
    try:
        recorded = sidecar.read_text(encoding="utf-8").splitlines()
    except OSError:
        return True
    if len(recorded) < 2:
        return True
    return recorded[0].strip() != marker or recorded[1].strip() != _hash(text)


def _hash(text: str) -> str:
    """Short, stable identifier for the text body."""
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


async def regenerate_if_needed() -> None:
    """Render every registered voice's clips (canned + sample + greetings)
    into its own subtree, idempotently. No-op for clips already up to date.

    Called from ``main.lifespan`` at startup and from the
    ``/v1/admin/sounds/regenerate`` admin endpoint. A DB hiccup loading the
    registry or the greeting bank skips that work without deleting anything.
    """
    _VOICES_ROOT.mkdir(parents=True, exist_ok=True)

    try:
        async with session_scope() as s:
            voices = await VoicesRepository(s).all()
            greeting_rows = await ClientGreetingsRepository(s).all_enabled()
    except Exception as e:
        log.warning("could not load voices/greetings from DB; skipping render: %s", e)
        return

    if not voices:
        log.info("no voices registered; nothing to render")
        return

    greeting_entries = _greeting_entries(greeting_rows)
    keep_slugs: set[str] = set()

    for v in voices:
        slug = voice_slug(v["name"])
        keep_slugs.add(slug)
        vdir = _VOICES_ROOT / slug
        gdir = vdir / "greetings"
        vdir.mkdir(parents=True, exist_ok=True)
        gdir.mkdir(parents=True, exist_ok=True)
        marker = _marker(v["engine"], v["model_ref"])

        # Canned + per-voice sample at the voice root.
        for mp3_name, sidecar_name, text in [*_CANNED, _sample_entry()]:
            await _regen_entry(vdir, mp3_name, sidecar_name, text, marker, v)

        # Greetings under the voice's greetings/ subdir.
        for mp3_name, sidecar_name, text in greeting_entries:
            await _regen_entry(gdir, mp3_name, sidecar_name, text, marker, v)
        _prune_greetings(gdir, {name for name, _, _ in greeting_entries})

    _prune_voice_dirs(keep_slugs)


async def _regen_entry(
    directory: Path,
    mp3_name: str,
    sidecar_name: str,
    text: str,
    marker: str,
    voice: dict,
) -> None:
    """Render one (mp3, sidecar, text) entry in ``directory`` for ``voice``
    if its sidecar marker is missing / disagrees. No-op when up to date;
    failures are logged and non-fatal (the Pi keeps its old copy)."""
    mp3_path = directory / mp3_name
    sidecar_path = directory / sidecar_name
    if not _needs_regen(mp3_path, sidecar_path, marker, text):
        return
    log.info(
        "clip %s for voice %r (engine=%s) missing or changed; regenerating",
        mp3_name, voice["name"], voice["engine"],
    )
    mp3_bytes = await _synth_clip_mp3(text, voice["engine"], voice["model_ref"])
    if mp3_bytes is None:
        log.warning(
            "clip %s for voice %r could not be regenerated; keeping existing copy",
            mp3_name, voice["name"],
        )
        return
    try:
        mp3_path.write_bytes(mp3_bytes)
        sidecar_path.write_text(f"{marker}\n{_hash(text)}\n", encoding="utf-8")
        log.info("clip %s regenerated (%d bytes)", mp3_path, len(mp3_bytes))
    except OSError as e:
        log.warning("failed to write clip %s: %s", mp3_path, e)


def _prune_greetings(gdir: Path, keep_mp3: set[str]) -> None:
    """Delete greeting clips (and sidecars) in ``gdir`` no longer in the
    bank, so editing the list doesn't leave orphan files the Pi plays."""
    keep = set(keep_mp3) | {Path(n).with_suffix(".voice").name for n in keep_mp3}
    try:
        existing = list(gdir.iterdir())
    except OSError:
        return
    for f in existing:
        if f.name.startswith("greet_") and f.name not in keep:
            try:
                f.unlink()
                log.info("pruned stale greeting %s", f)
            except OSError:
                pass


def _prune_voice_dirs(keep_slugs: set[str]) -> None:
    """Remove whole voice subtrees for voices that are no longer registered
    (deleted from the web dashboard), so the manifest can't list them."""
    try:
        existing = [p for p in _VOICES_ROOT.iterdir() if p.is_dir()]
    except OSError:
        return
    for d in existing:
        if d.name not in keep_slugs:
            try:
                import shutil
                shutil.rmtree(d)
                log.info("pruned clips for removed voice dir %s", d.name)
            except OSError:
                pass


def regenerate_blocking() -> None:
    """Sync entry point for one-off CLI use. The lifespan path uses the
    async one directly."""
    asyncio.run(regenerate_if_needed())
