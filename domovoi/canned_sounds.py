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
- **Setup clips** (``sounds/setup/*.wav``) — the lines a satellite speaks
  while it is being set up. WAV, not MP3: see ``SETUP_LINES``.

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

The marker written is the voice that ACTUALLY rendered the clip (the
TTS client's ``synthesize_detailed`` reports the rung), not the voice that
was asked for. A clip that a fallback rung rendered — Piper standing in for
an unreachable Edge voice, or the system voice for a broken Piper model —
therefore never matches its voice's marker, and is rendered again on the
next pass where the real engine works, instead of sticking forever.

Microsoft Edge voices are only rendered when Edge is usable: the internet
answer allows it (``INTERNET_ACCESS`` is not ``never``) and the
connectivity probe, when there is one, says online. Otherwise an Edge
voice's MISSING clip is rendered at once with the default Piper voice as a
stand-in (tagged as Piper), and an existing clip whose text is current is
kept as it is — no Edge attempt, and no network timeout per clip. The same
keep rule covers a Piper voice whose model isn't on disk while the internet
isn't usable, so an offline pass doesn't re-render its system-voice clips
every boot.

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
from typing import TYPE_CHECKING

from domovoi import egress
from domovoi.config import settings
from domovoi.db.repositories import ClientGreetingsRepository, VoicesRepository
from domovoi.db.session import session_scope

if TYPE_CHECKING:  # pragma: no cover — typing only
    from domovoi.clients.tts import SynthResult

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
# moment the wake word fires, before it listens (see satellite/client.py
# `_acknowledge_wake`, `[wake] ack_mode = "greeting"`).
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


async def _synth_detailed(text: str, engine: str, model_ref: str | None) -> "SynthResult":
    """Render through the engine-aware TTS client and say which rung did it.

    A client without ``synthesize_detailed`` (a test double) can't say, so
    the requested voice is assumed — what every clip assumed before."""
    from domovoi.clients.tts import SynthResult, get_tts_client

    client = get_tts_client()
    detailed = getattr(client, "synthesize_detailed", None)
    if detailed is None:
        wav = await client.synthesize(text, engine=engine, voice=model_ref)
        return SynthResult(wav=wav, engine=engine, voice=model_ref)
    return await detailed(text, engine=engine, voice=model_ref)


def _rendered_marker(result: "SynthResult") -> str:
    """The sidecar marker for what a render ACTUALLY produced."""
    return _marker(result.engine, result.voice or "")


async def _synth_clip_mp3(
    text: str, engine: str, model_ref: str | None
) -> tuple[bytes, str] | None:
    """Render ``text`` in a specific voice to MP3 bytes. Routes through the
    engine-aware TTS client (so the clip voice matches response voice) and
    encodes the returned WAV to MP3. Returns ``(mp3, marker)``, where the
    marker names the voice that actually rendered it. None on any failure."""
    try:
        result = await _synth_detailed(text, engine, model_ref)
    except Exception as e:
        log.warning("clip synth failed (engine=%s voice=%s): %s", engine, model_ref, e)
        return None
    if not result.engine:
        return None  # every rung failed: the WAV is empty
    mp3 = _wav_to_mp3(result.wav)
    if mp3 is None:
        return None
    return mp3, _rendered_marker(result)


async def _synth_clip_wav(
    text: str, engine: str, model_ref: str | None
) -> tuple[bytes, str] | None:
    """Render ``text`` in a specific voice as the WAV the TTS client already
    returns — no encode step. Validated as readable 16-bit WAV so a broken
    render never lands on a card as a file `aplay` will refuse. Returns
    ``(wav, marker)`` like :func:`_synth_clip_mp3`. None on any failure."""
    try:
        result = await _synth_detailed(text, engine, model_ref)
    except Exception as e:
        log.warning("clip synth failed (engine=%s voice=%s): %s", engine, model_ref, e)
        return None
    if not result.engine:
        return None
    wav = result.wav
    try:
        with wave.open(io.BytesIO(wav), "rb") as wf:
            if wf.getsampwidth() != 2 or wf.getnframes() == 0:
                return None
    except (wave.Error, EOFError) as e:
        log.warning("clip WAV unreadable (engine=%s): %s", engine, e)
        return None
    return wav, _rendered_marker(result)


def _marker(engine: str, model_ref: str) -> str:
    """First sidecar line — identifies the rendering voice so an engine or
    model change re-renders."""
    return f"{engine}|{model_ref}"


def edge_usable() -> bool:
    """Whether a Microsoft Edge voice can render right now: the internet
    answer allows it, and the connectivity probe (when the core has one
    running) says online. Without a probe — a CLI render, or before the
    probe starts — only the answer counts. The same test decides whether a
    Piper voice that isn't on disk yet could be fetched."""
    if not egress.internet_allowed():
        return False
    from domovoi import connectivity

    probe = connectivity.current_probe()
    return probe is None or bool(probe.online)


def _recorded_text_hash(sidecar: Path) -> str | None:
    try:
        lines = sidecar.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    return lines[1].strip() if len(lines) >= 2 else None


def _voice_can_render(engine: str, model_ref: str, *, edge_ok: bool) -> bool:
    """Whether a voice's own engine can render right now, judged without
    trying: an Edge voice needs Edge usable; a Piper voice needs its model
    on disk, or the internet usable to fetch it (the same test as Edge)."""
    eng = (engine or "").strip().lower()
    if eng == "edge":
        return edge_ok
    if eng == "piper":
        from domovoi.clients.tts import piper_voice_on_disk

        return edge_ok or piper_voice_on_disk(model_ref)
    return True


def _render_voice(
    engine: str,
    model_ref: str,
    clip: Path,
    sidecar: Path,
    text: str,
    *,
    edge_ok: bool,
) -> tuple[str, str | None] | None:
    """The ``(engine, model_ref)`` to render a clip that needs it with, or
    None to keep the clip that is there.

    A voice whose own engine can render now renders in it. One that can't
    (:func:`_voice_can_render`) KEEPS its clip when the clip exists and its
    text is current — a real render from earlier, or an earlier stand-in —
    so a pass while offline re-renders nothing. A missing or out-of-date
    clip is rendered at once: an Edge voice's with the default Piper voice
    (``model_ref`` None) as a stand-in, with no Edge attempt; a Piper
    voice's through the router, which falls to the system voice. Either
    way the sidecar names the real renderer, so the first pass where the
    voice's own engine works renders it again."""
    if _voice_can_render(engine, model_ref, edge_ok=edge_ok):
        return engine, model_ref
    if clip.exists() and _recorded_text_hash(sidecar) == _hash(text):
        return None
    if (engine or "").strip().lower() == "edge":
        return "piper", None
    return engine, model_ref


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
    # Judged once per pass, so every clip of a pass agrees.
    edge_ok = edge_usable()
    if not edge_ok and any((v["engine"] or "").lower() == "edge" for v in voices):
        log.info(
            "clips: Microsoft Edge voices can't render right now (%s); their "
            "missing clips use the default Piper voice until Edge works",
            egress.TURNED_OFF_REASON if egress.internet_turned_off() else "offline",
        )

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
            await _regen_entry(
                vdir, mp3_name, sidecar_name, text, marker, v, edge_ok=edge_ok
            )

        # Greetings under the voice's greetings/ subdir.
        for mp3_name, sidecar_name, text in greeting_entries:
            await _regen_entry(
                gdir, mp3_name, sidecar_name, text, marker, v, edge_ok=edge_ok
            )
        _prune_greetings(gdir, {name for name, _, _ in greeting_entries})

    _prune_voice_dirs(keep_slugs)


async def _regen_entry(
    directory: Path,
    mp3_name: str,
    sidecar_name: str,
    text: str,
    marker: str,
    voice: dict,
    *,
    edge_ok: bool = True,
) -> None:
    """Render one (mp3, sidecar, text) entry in ``directory`` for ``voice``
    if its sidecar marker is missing / disagrees. No-op when up to date;
    failures are logged and non-fatal (the Pi keeps its old copy). The
    sidecar records the voice that actually rendered the clip (see the
    module docstring); ``edge_ok`` is :func:`edge_usable` for this pass."""
    mp3_path = directory / mp3_name
    sidecar_path = directory / sidecar_name
    if not _needs_regen(mp3_path, sidecar_path, marker, text):
        return
    plan = _render_voice(
        voice["engine"], voice["model_ref"], mp3_path, sidecar_path, text,
        edge_ok=edge_ok,
    )
    if plan is None:
        log.debug(
            "clip %s for Edge voice %r kept until Edge is usable", mp3_name, voice["name"],
        )
        return
    engine, model_ref = plan
    log.info(
        "clip %s for voice %r (engine=%s) missing or changed; regenerating%s",
        mp3_name, voice["name"], voice["engine"],
        "" if engine == voice["engine"] else f" with a {engine} stand-in",
    )
    rendered = await _synth_clip_mp3(text, engine, model_ref)
    if rendered is None:
        log.warning(
            "clip %s for voice %r could not be regenerated; keeping existing copy",
            mp3_name, voice["name"],
        )
        return
    mp3_bytes, actual = rendered
    if actual != marker:
        log.info(
            "clip %s for voice %r rendered by %s, not %s; it renders again "
            "once that voice works", mp3_name, voice["name"], actual, marker,
        )
    try:
        mp3_path.write_bytes(mp3_bytes)
        sidecar_path.write_text(f"{actual}\n{_hash(text)}\n", encoding="utf-8")
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


# ─── Setup clips — the satellite's voice before it has one ────────────────
#
# Everything above renders PER REGISTERED VOICE, because a satellite knows
# which voice it answers in. During setup it does not: there is no config,
# no pairing, and — until the very last moment — no network to ask over.
#
# So these render once, in the household's default voice, at the moment a
# card is prepared. `settings.tts_engine` is that default: it is `piper`
# unless the household has deliberately chosen the web engine, and prep runs
# here on the server where the network for that exists. They land in
# sounds/setup/, which media prep already copies into the payload and stage 1
# already lands at ~/.domovoi/sounds — so the delivery path needs nothing new.
#
# The phrase list is frozen when the card is written. That is the trade for
# being able to speak with no server: adding a line later means re-prepping.

_SETUP_DIR = _SOUNDS_DIR / "setup"

_DIGIT_WORDS = (
    "zero", "one", "two", "three", "four",
    "five", "six", "seven", "eight", "nine",
)

# (wav, text). Spoken at four moments only — ready, joining, joined, and the
# code — plus the two failures a customer can actually act on. Anything more
# and a device you set up three of becomes tiresome by the second.
#
# WAV, not MP3, on purpose: these play during setup, BEFORE stage 2's
# online apt has run, and the only player a fresh card can be sure of is
# `aplay` from alsa-utils (already there for `amixer`). mpg123's Trixie
# build wants six libraries a stock Pi OS Lite lacks, none of which
# `apt-get download` fetches — found on hardware as a silent portal and
# "no mpg123" on every line of setup-status.log. Sixteen short clips of
# 16-bit mono run to a couple of megabytes on the card; nobody will notice.
SETUP_LINES: list[tuple[str, str]] = [
    ("ready.wav",
     "I'm ready to set up. Connect to the Wi-Fi network named on the box."),
    ("joining.wav",
     "Thanks. Joining your network now."),
    ("join_failed.wav",
     "I couldn't join that network. Connect to my setup network and try again."),
    ("on_network.wav",
     "I'm on your network. One moment while I finish setting up."),
    ("no_microphone.wav",
     "I can't find my microphone. Check that it's plugged in."),
    ("your_code_is.wav", "Your setup code is"),
    *[(f"digit_{d}.wav", word) for d, word in enumerate(_DIGIT_WORDS)],
]


def default_voice() -> tuple[str, str]:
    """The engine and model the household actually speaks in.

    ``tts_engine`` defaults to ``piper`` because local-first is the product
    promise; a household that has deliberately chosen the web engine is
    honoured here too, since prep runs on the server where the network is.
    While Edge isn't usable (:func:`edge_usable`) the clips render with the
    default Piper voice instead, tagged as Piper, and render again in Edge
    on a later prep once it is.
    """
    engine = (settings.tts_engine or "piper").strip().lower()
    if engine == "edge":
        return "edge", settings.tts_edge_voice
    return "piper", settings.tts_piper_voice


async def render_setup_clips() -> tuple[int, list[str]]:
    """Render the setup phrase set in the default voice. (written, problems).

    Idempotent on the same sidecar rule as everything else here, so a
    re-prep with an unchanged voice re-renders nothing. Never raises: a card
    with no clips is a quiet satellite, not a broken one.
    """
    engine, model_ref = default_voice()
    marker = _marker(engine, model_ref)
    problems: list[str] = []
    written = 0
    try:
        _SETUP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return 0, [f"could not create {_SETUP_DIR}: {e}"]
    edge_ok = edge_usable()

    for wav_name, text in SETUP_LINES:
        wav_path = _SETUP_DIR / wav_name
        sidecar = _SETUP_DIR / f"{wav_name.rsplit('.', 1)[0]}.voice"
        if not _needs_regen(wav_path, sidecar, marker, text):
            continue
        plan = _render_voice(engine, model_ref, wav_path, sidecar, text, edge_ok=edge_ok)
        if plan is None:
            continue  # an Edge-voiced clip, kept until Edge is usable
        rendered = await _synth_clip_wav(text, *plan)
        if rendered is None:
            problems.append(wav_name)
            continue
        wav_bytes, actual = rendered
        try:
            wav_path.write_bytes(wav_bytes)
            sidecar.write_text(f"{actual}\n{_hash(text)}\n", encoding="utf-8")
            written += 1
        except OSError as e:
            problems.append(f"{wav_name}: {e}")
    # An earlier build wrote these as MP3; a stale one next to its WAV
    # would ship both and confuse anyone reading the card.
    for stale in _SETUP_DIR.glob("*.mp3"):
        try:
            stale.unlink()
        except OSError:
            pass
    if written:
        log.info(
            "rendered %d setup clip(s) in the default voice (engine=%s)",
            written, engine,
        )
    return written, problems
