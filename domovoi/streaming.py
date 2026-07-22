"""WebSocket streaming endpoint for Pi satellites.

Wire protocol v0.1 — `/v1/stream/{room_id}`. Bidirectional, mixed text+binary.

Audio format on both directions: 16 kHz mono int16 little-endian PCM.
Server declares the actual TTS sample rate per response in the
`response_start` frame (Edge ≈ 24 kHz, Piper ≈ 22 kHz, system varies);
clients should resample if their playback device can't accept it directly.

Client → Server
  text  hello              {"type":"hello","room_id":...,"wake_word":...,"synced_sha":...,"supports_full_duplex":bool,
                            "sat_type":"voice"|"video","mic_enabled":bool}
                           — `supports_full_duplex` (optional) reports whether
                           the board has on-chip AEC (XVF3800 true, 2-Mic HAT
                           false). The server caches it per room and refuses a
                           drop-in for any room that can't capture-while-playing
                           without howling. `synced_sha` (optional) supersedes
                           the old `client_version` field: it's the version
                           label the Pi recorded after its last satellite-code
                           sync (None if it has never synced). The server
                           caches it per room so the dashboard can flag
                           satellites behind the core's SHA.
                           `sat_type` (optional, default "voice") declares the
                           satellite kind — "video" for screen-bearing kiosk
                           builds. Cached per room AND persisted to the
                           `satellites` table when EXPLICITLY present (an old
                           client omitting it never resets a stored type).
                           `mic_enabled` (optional, default true) reports
                           whether the voice-input stack (wake word/VAD/
                           capture) is running; false on mic-less builds. The
                           server refuses wake-recording/drop-in/chat for
                           mic-disabled rooms. Both cleared on disconnect.
  text  utterance_start    {"type":"utterance_start","trigger":"wake_word"|"barge_in"|"push_to_talk"|"followup"|"wake_clip"}
                           — trigger "wake_clip" (Feature 5) marks a positive
                           wake-word TRAINING clip: the Pi is in dashboard-
                           initiated recording mode and the following PCM is a
                           ~2 s sample of the wake phrase, NOT a command. The
                           server writes it as a clip WAV and bumps the clip
                           count instead of transcribing/routing (it doesn't
                           special-case the trigger — it just buffers as usual
                           and the recording-mode branch in utterance_end
                           diverts the audio).
  text  utterance_end      {"type":"utterance_end","greeting_played":bool}
                           — `greeting_played` (optional) marks turns where
                           the Pi played a wake greeting concurrent with
                           capture, so the server strips a greeting that bled
                           past the AEC out of the transcript.
  text  barge_in           {"type":"barge_in"} — sent during TTS playback
  text  noisy_capture      {"type":"noisy_capture"} — Pi-side noise-gate auto-tune
                           detected an unusably-loud capture and bailed.
                           Server should respond with a stock apology TTS
                           rather than transcribing the audio buffer.
  text  wifi_status        {"type":"wifi_status","rx_mbits":N,"tx_mbits":N,"ssid":...}
                           — Pi pushes its current link rate every poll
                           (60 s default) so WifiHandler's diagnostic
                           ("how's your wifi?") can answer without a
                           round-trip. Server caches per-room.
  text  volume_status      {"type":"volume_status","level":N} — Pi reports
                           its current hardware output volume (0-100) on
                           connect and after each set_volume. Server caches
                           per-room so MusicHandler's relative "turn it up"
                           bumps against the satellite's real level.
  text  voice_status       {"type":"voice_status","voice":NAME} — Pi reports
                           which registered voice it speaks in, on connect
                           and after a set_voice action. Server caches
                           per-room and synthesizes that room's responses +
                           greetings in it. None/unknown → registry default.
  text  config_status      {"type":"config_status","config":{"section.key":val,...}}
                           — Pi reports its current EFFECTIVE editable config
                           (flat, keyed by section.key) on connect. Server
                           caches per-room so the dashboard's per-satellite
                           Settings tab can render real values. Cleared on
                           disconnect.
  text  music_ready        {"type":"music_ready"} — sent after the Pi's
                           mpg123 subprocess has spawned and primed its
                           buffer against MPD's always-on silence stream.
                           Server responds by calling `mpd.resume()` so
                           song frames flow into an already-primed buffer
                           — eliminates the first-second underrun
                           stutter that comes from joining a real-time
                           MP3 stream mid-song. Old satellites that
                           don't send this still get music — the server
                           falls back to an unconditional resume after
                           `music_prepare_fallback_sec`.
  text  dropin_end         {"type":"dropin_end"} — Pi asks to end the
                           active drop-in call (a hardware button or a
                           client-detected hang-up). The spoken "hang up"
                           path instead routes a normal utterance to
                           DropInHandler, which ends the call server-side.
  text  display_status     {"type":"display_status","on":bool,"kiosk_alive":bool,
                            "brightness":int|null,"idle_mode":"clock"|"blank"|"art"}
                           — video satellites only: reports screen power
                           state, whether the kiosk browser process is
                           alive, backlight brightness percent when the
                           hardware exposes one (else null), and the
                           configured idle behavior. Sent after connect,
                           after each applied set_display, and whenever the
                           kiosk-alive watcher observes a change. Cached
                           per room for the dashboard; cleared on
                           disconnect.
  text  ping               {"type":"ping"}
  bytes                    raw PCM. Normally meaningful only between
                           utterance_start/utterance_end. DURING A DROP-IN
                           (open-mic mode) the Pi streams every mic frame
                           continuously with NO utterance framing, and the
                           server relays each frame verbatim to the peer
                           room — except while an utterance IS active (a
                           mid-call wake-word command like "hang up"), when
                           the server captures it for STT instead of relaying.

Server → Client
  text  ready              {"type":"ready","protocol_version":"0.1","room_id":...,"bot_name":...,"audio_sample_rate_in":16000}
  text  transcript         {"type":"transcript","text":...}
  text  set_volume         {"type":"set_volume","level":N} — set the Pi's
                           hardware output volume (0-100). Sent BEFORE
                           response_start so the spoken confirmation plays
                           at the new level. Scales both TTS and music.
  text  sounds_changed     {"type":"sounds_changed"} — the core
                           re-rendered the sound clips (a greeting was
                           edited); the Pi re-syncs its sound cache.
  text  start_wake_recording {"type":"start_wake_recording","wake_word_id":N,
                           "slug":...,"clip_seconds":F,"target_count":N}
                           — Feature 5. Enter wake-word clip-recording mode:
                           suspend the normal wake loop and capture
                           `target_count` positive clips of ~`clip_seconds`
                           each, framing each one with utterance_start
                           (trigger="wake_clip") / PCM / utterance_end so the
                           server saves it to the training set.
  text  stop_wake_recording {"type":"stop_wake_recording"} — Feature 5. Leave
                           clip-recording mode early and resume the normal
                           wake loop. Sent on a dashboard Stop (recording also
                           self-terminates once target_count clips are sent).
  text  set_wake_word      {"type":"set_wake_word","slug":...} — Feature 5.
                           Push a trained model: the Pi writes `slug` to its
                           wake sidecar (~/.domovoi/wake), syncs the model
                           from /v1/wake-models, then self-restarts to load
                           it. `slug` is the model file stem AND the Pi's new
                           effective wake word AND the openWakeWord
                           prediction-dict key — they must all agree.
  text  wake_models_changed {"type":"wake_models_changed"} — Feature 5. The
                           served wake models changed; the Pi re-syncs its
                           ~/.domovoi/wake_models cache from /v1/wake-models.
  text  set_config         {"type":"set_config","changes":{"section.key":val,...}}
                           — push web-edited config to the Pi. It merges the
                           changes into config.toml (preserving comments),
                           validates the result parses + writes a .bak, then
                           self-restarts to apply (see `restart` below).
  text  set_display        {"type":"set_display","action":"on"|"off"|"restart_kiosk"}
                           — video satellites only: switch the screen on/off
                           (wlopm / xset dpms / backlight sysfs, per the
                           [display] power_method config) or restart the
                           kiosk browser service. The Pi applies the action
                           and re-reports via display_status. Only ever sent
                           to sessions whose hello declared sat_type=video.
  text  restart            {"type":"restart"} — the core asks this
                           satellite to restart its own systemd service
                           (e.g. a config change that only takes effect on
                           a fresh process). The Pi drains TTS playback,
                           then runs a sudo'ed `systemctl --no-block
                           restart domovoi-satellite.service` (see the
                           self-restart sudoers entry in
                           satellite/PROVISIONING.md).
  text  upgrade            {"type":"upgrade","expected_sha":...,"manifest_path":...,
                           "files_base":...,"reconnect_timeout_sec":N}
                           — the core asks this satellite to sync its
                           own code and self-restart. The Pi tarballs its
                           satellite/ tree (rollback backup), mirrors the
                           manifest at `manifest_path` (downloading each
                           changed file from `files_base/<rel>` and verifying
                           its sha256 against the manifest), records
                           `expected_sha` as its new synced version label,
                           then restarts. `manifest_path`/`files_base` are
                           domovoi-relative PATHS only — the Pi derives
                           the HTTP base from its own live WS URL (as the
                           sound sync does). If it doesn't reconnect within
                           `reconnect_timeout_sec`, its on-Pi watchdog rolls
                           back to the tarball.
  text  response_start     {"type":"response_start","text":...,"matched_handler":...,"matched_path":...,"session_id":...,"online":...,"audio_sample_rate":24000}
  text  response_end       {"type":"response_end","interrupted":bool,"expect_followup":bool,"pi_action":?,"pi_action_arg":?}
                           — `pi_action` (optional) requests a Pi-local
                           side-effect after playback drains: "reassociate_wifi"
                           (WifiHandler), "set_voice" (VoiceHandler, with
                           `pi_action_arg` = the new voice name), or
                           "restart" (bounce the satellite service).
  text  dropin_start       {"type":"dropin_start","peer_room":...,"peer_label":...,"audio_sample_rate":16000,"full_duplex":bool}
                           — enter open-mic mode: stream every mic frame
                           continuously (no utterance_start/_end) so the
                           server relays it to the peer room, and play inbound
                           binary PCM straight through at `audio_sample_rate`
                           (16 kHz — NOT the last response_start rate, or it
                           chipmunks) without the response_start/response_end
                           lifecycle. openWakeWord keeps running so "hey
                           jarvis, hang up" can end the call.
  text  dropin_end         {"type":"dropin_end","reason":...} — exit open-mic
                           mode, restore the wake-word loop and any suppressed
                           music.
  text  chat_start         {"type":"chat_start"} — enter CONVERSATIONAL chat
                           mode (Feature 8): a wake-word-triggered open mic
                           where the satellite loops normal STT→reply turns
                           WITHOUT re-waking between them. Unlike dropin_start
                           there is NO peer relay — each captured utterance is
                           a normal turn (utterance_start/PCM/utterance_end)
                           that the server routes to a Letta agent and answers
                           with TTS. Requires an AEC board (supports_full_duplex);
                           a non-AEC Pi must refuse and bounce back to idle. The
                           server clears chat mode + sends chat_end when the user
                           says an intent-explicit exit phrase ("stop",
                           "we're done", "end the chat").
  text  chat_end           {"type":"chat_end","reason":...} — exit chat mode,
                           restore the wake-word loop. Mirrors dropin_end.
  text  error              {"type":"error","message":...}
  text  pong               {"type":"pong"}
  bytes                    raw PCM TTS audio at audio_sample_rate. During a
                           drop-in, inbound bytes are instead live 16 kHz relay
                           audio from the peer room (see dropin_start).

Pi owns: wake-word detection, VAD, noise gate, barge-in detection.
Server owns: STT, intent routing, TTS, response lifecycle.

A `barge_in` (or a new `utterance_start` while a response is still streaming)
cancels the in-flight response task; clients then see a final
`response_end` with `interrupted=true`.
"""

from __future__ import annotations

import array
import asyncio
import io
import json
import logging
import math
import re
import secrets
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import text

from domovoi.admin_auth import token_sha256
from domovoi.clients.letta import get_letta_client
from domovoi.clients.tts import get_tts_client
from domovoi.clients.whisper import get_whisper_client
from domovoi.config import settings
from domovoi.connectivity import ConnectivityProbe
from domovoi.db.repositories import (
    SatellitePairingRepository,
    SatellitesRepository,
    SessionRepository,
)
from domovoi.db.session import session_scope
from domovoi.models import Context, Intent
from domovoi.now_playing import NOW_PLAYING
from domovoi.router import route

log = logging.getLogger(__name__)

# Set once when the pairing check hits a missing satellite_pairings table
# (V002 not applied yet) so we log an actionable hint ONCE instead of on
# every hello. Reset only by a process restart.
_pairing_table_warned = False


def _is_missing_pairing_table(exc: Exception) -> bool:
    """True when ``exc`` is 'relation satellite_pairings does not exist' — the
    signature of a not-yet-applied migration, worth a distinct one-time hint
    rather than a per-connect warning flood."""
    orig = getattr(exc, "orig", None)
    if type(orig).__name__ == "UndefinedTableError":
        return "satellite_pairings" in str(orig)
    text = str(exc).lower()
    return "satellite_pairings" in text and "does not exist" in text


# Same one-time-hint latch for the V003 satellites inventory table.
_satellites_table_warned = False


def _is_missing_satellites_table(exc: Exception) -> bool:
    """True when ``exc`` is 'relation satellites does not exist' (V003 not
    applied yet) — one actionable hint, not a per-hello warning flood."""
    orig = getattr(exc, "orig", None)
    if type(orig).__name__ == "UndefinedTableError":
        return "satellites" in str(orig)
    text = str(exc).lower()
    return "satellites" in text and "does not exist" in text


PROTOCOL_VERSION = "0.1"
PCM_INPUT_SAMPLE_RATE = 16_000  # Whisper requirement; clients must send at this rate
DEFAULT_AUDIO_CHUNK_BYTES = 16 * 1024
MAX_UTTERANCE_BYTES = 60 * PCM_INPUT_SAMPLE_RATE * 2  # 60s of int16 PCM

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# ─── Conversational chat mode (Feature 8) ─────────────────────────────────
# Exit phrases that END an active conversational chat (clear
# ``conversational_mode``, send a ``chat_end`` frame, speak a short goodbye).
# Matched against the lower-cased, punctuation-stripped transcript of a turn
# that arrives while the session is already in chat mode — at that point the
# router is bypassed, so this is the ONLY thing standing between "keep talking
# to Letta" and "we're done". Deliberately kept to INTENT-EXPLICIT leave phrases
# only — stop / done / end-the-chat. Courtesy + wind-down phrases ("thanks",
# "thank you", "goodbye", "bye", "that's all", "never mind") are EXCLUDED on
# purpose: they're too easily said mid-conversation without meaning to leave, and
# an accidental exit is worse than occasionally needing one more explicit "stop".
# A bare "stop"/"done" as the WHOLE turn still ends it — chat mode owns the turn,
# so there's no router collision like DropInHandler's end regex.
# Whole-turn exit phrases — short and a little ambiguous, so they only end
# the chat when they ARE the whole turn (a bare "stop"/"done" said mid-
# conversation is an exit only when it's all the user said).
_CHAT_EXIT_WHOLE_RE = re.compile(
    r"^(?:let'?s )?(?:"
    r"stop(?: (?:chatting|talking|the chat))?"
    r"|(?:i'?m |we'?re )?(?:all )?done(?: (?:chatting|talking|now))?"
    r")$"
)
# Explicit multi-word exit COMMANDS — unambiguous enough to honor even when
# tacked onto the end of a sentence ("take care and have a great day, end
# the chat"), so these are SEARCHED anywhere in the turn, not anchored. This
# was the #1 reason a user couldn't leave chat mode: they kept appending
# "end the chat" to a goodbye and the old whole-turn-anchored regex never
# matched it — only a bare "stop" got them out. "talking"/"stop" stay out of
# the anywhere-search set (too easy to say mid-conversation, e.g. "stop
# talking about that"); they remain whole-turn-only above.
_CHAT_EXIT_COMMAND_RE = re.compile(
    r"\b(?:"
    r"(?:let'?s )?(?:end|exit|quit|leave) (?:the )?(?:chat|conversation)"
    r"|(?:exit|quit|leave) chat(?: mode)?"
    r"|end (?:the )?chat(?: mode)?"
    r")\b"
)


def _is_chat_exit(transcript: str) -> bool:
    """True if a chat-mode turn's transcript asks to leave chat mode.
    Normalizes the way the router does (lower-case, strip trailing
    punctuation) so the regex sees the same shape regardless of how the
    caller passes the transcript. A whole-turn short phrase ("stop", "done")
    OR an explicit exit command anywhere in the turn ("…, end the chat")
    counts."""
    norm = transcript.lower().strip().rstrip(".,!?")
    return bool(
        _CHAT_EXIT_WHOLE_RE.match(norm) or _CHAT_EXIT_COMMAND_RE.search(norm)
    )


@dataclass
class WakeRecordingState:
    """Live state of an in-progress wake-word clip-recording session
    (Feature 5). Set on a :class:`StreamSession` while the server is
    collecting positive clips from this satellite; ``None`` otherwise.

    While set, each ``utterance_end`` writes the buffered PCM as one
    ``clip_NNN.wav`` under ``<wake_clips_dir>/<slug>/`` and bumps the
    wake word's ``clip_count`` — STT / routing are skipped entirely (the
    Pi is feeding training audio, not commands). ``slug`` is the
    load-bearing identifier the whole feature keys off of (= the served
    ``<slug>.onnx`` stem and the Pi's effective wake word)."""

    wake_word_id: int
    slug: str
    clip_seconds: float
    target_count: int
    clips_written: int = 0


async def _resume_mpd_for_room(room_id: str) -> None:
    """Best-effort `mpd.resume()` for a room. No-op when MPD isn't reachable.

    `resume()` is a no-op when MPD is already playing, so this is safe
    to call from both the music_ready ack path and the fallback timer
    even when they race (only one will win the pending-entry pop).
    """
    from domovoi.clients.mpd import get_mpd_client_for

    try:
        mpd = get_mpd_client_for(room_id)
        await mpd.resume()
    except Exception as e:
        log.warning("music handshake: resume failed for room=%s: %s", room_id, e)


async def schedule_music_resume_fallback(app: Any, room_id: str, url: str) -> None:
    """Register a pending music_ready entry + start the fallback timer.

    Cancels any existing pending entry for this room first so a back-to-
    back "play X, play Y" sequence doesn't leave two timers racing.

    The fallback fires after `settings.music_prepare_fallback_sec` and
    calls `mpd.resume()` unconditionally. Covers:
      * old satellites that don't speak the `music_ready` frame,
      * a Pi that drops WiFi between music_start and music_ready,
      * the admin path emitting music_start with no Pi connected
        (resumable_music still records the URL for a later reconnect,
        but MPD shouldn't sit paused indefinitely on the assumption
        that someone eventually shows up to consume it).
    """
    pending: dict[str, dict[str, Any]] = app.state.pending_music_start
    existing = pending.get(room_id)
    if existing is not None:
        prior_task = existing.get("task")
        if prior_task is not None and not prior_task.done():
            prior_task.cancel()

    async def _fallback_resume() -> None:
        try:
            await asyncio.sleep(settings.music_prepare_fallback_sec)
        except asyncio.CancelledError:
            # music_ready arrived in time and cancelled us. Clean exit.
            raise
        log.info(
            "music handshake: no music_ready from room=%s within %.1fs; "
            "resuming MPD as fallback",
            room_id, settings.music_prepare_fallback_sec,
        )
        try:
            await _resume_mpd_for_room(room_id)
        finally:
            cur = pending.get(room_id)
            if cur is not None and cur.get("task") is asyncio.current_task():
                pending.pop(room_id, None)

    task = asyncio.create_task(
        _fallback_resume(), name=f"music-fallback-{room_id}"
    )
    pending[room_id] = {"url": url, "task": task}


async def consume_music_ready(app: Any, room_id: str) -> None:
    """Handle an incoming `music_ready` frame: cancel fallback, resume MPD."""
    pending: dict[str, dict[str, Any]] = app.state.pending_music_start
    entry = pending.pop(room_id, None)
    if entry is None:
        # No prepared track waiting — either the fallback timer just
        # fired or the Pi double-sent. Either way, nothing to do.
        log.debug(
            "music handshake: music_ready from room=%s without pending entry",
            room_id,
        )
        return
    task = entry.get("task")
    if task is not None and not task.done():
        task.cancel()
    log.info("music handshake: music_ready from room=%s; resuming MPD", room_id)
    await _resume_mpd_for_room(room_id)


def _split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [p for p in _SENTENCE_SPLIT.split(text) if p]


def _wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    """Strip WAV header → (raw int16 PCM, sample_rate). Empty WAV → (b"", 16000)."""
    if not wav_bytes:
        return b"", PCM_INPUT_SAMPLE_RATE
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            sr = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())
        return pcm, sr
    except wave.Error as e:
        log.warning("malformed WAV from TTS (%d bytes): %s", len(wav_bytes), e)
        return b"", PCM_INPUT_SAMPLE_RATE


def _resample_pcm(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-resample int16 mono PCM from ``src_rate`` to ``dst_rate``.

    A response announces ONE ``audio_sample_rate`` (the first sentence's) at
    ``response_start`` and the Pi plays the whole PCM stream at that rate. If
    a later sentence falls back to a different TTS engine (edge 24 kHz →
    piper 22 kHz on a transient edge failure mid-response), its PCM is at a
    different native rate — played back at the announced rate it runs too
    fast/slow, garbling the tail of the response. Converting every sentence
    to the announced rate keeps playback correct without a wire-protocol /
    satellite change. Linear interpolation is inaudible for speech at these
    near-ratios; a no-op when the rates already match or the PCM is empty."""
    if src_rate == dst_rate or not pcm:
        return pcm
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return pcm
    n_dst = max(1, int(round(samples.size * dst_rate / src_rate)))
    src_idx = np.linspace(0.0, samples.size - 1, num=n_dst)
    resampled = np.interp(src_idx, np.arange(samples.size), samples.astype(np.float32))
    return np.rint(resampled).astype(np.int16).tobytes()


# Floor below which a "wake_clip" utterance is dropped rather than written —
# guards against a zero/near-zero-frame clip (a fluke utterance_end with no
# audio) landing as a bogus positive and inflating the clip count. ~0.3 s.
_MIN_WAKE_CLIP_BYTES = int(0.3 * PCM_INPUT_SAMPLE_RATE * 2)


def _write_wav_clip(path: Path, pcm: bytes) -> None:
    """Write raw 16 kHz mono int16 PCM as a WAV file (Feature 5 wake-word
    clips). Clone of ``scripts/record_wake_word_clips.py:_save_wav`` — the
    same header (1 channel, sampwidth=2, 16000 Hz) the openWakeWord trainer
    expects of its positive set, so on-Pi and recorded-on-a-satellite clips
    are byte-format identical. The parent dir is created if missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(PCM_INPUT_SAMPLE_RATE)
        w.writeframes(pcm)


def _iter_chunks(data: bytes, size: int = DEFAULT_AUDIO_CHUNK_BYTES) -> list[bytes]:
    if not data:
        return []
    return [data[i : i + size] for i in range(0, len(data), size)]


def _pcm_dbfs(data: bytes) -> float:
    """RMS level of a 16-bit-LE PCM frame in dBFS (full-scale = 0.0). Empty or
    silent → a -120 floor. Cheap stdlib (no numpy) — used by the drop-in relay
    noise gate to skip forwarding near-silent frames (bounced cross-room echo
    / room tone)."""
    n = len(data) // 2
    if n == 0:
        return -120.0
    samples = array.array("h")
    samples.frombytes(data[: n * 2])
    acc = 0
    for s in samples:
        acc += s * s
    rms = math.sqrt(acc / n)
    if rms < 1.0:
        return -120.0
    return 20.0 * math.log10(rms / 32768.0)


async def resolve_voice(voice_name: str | None) -> tuple[str | None, str | None]:
    """Resolve a voice ``name`` to ``(engine, model_ref)`` for per-call TTS.

    ``name`` is what a room reports speaking in (``app.state.satellite_voice``)
    or a per-response override. Unknown / None falls back to the registry
    default. ``(None, None)`` when nothing is registered yet (pre-seed) or
    the DB is unreachable — the caller then synthesizes with the TTS
    client's construct-time globals, i.e. today's behavior."""
    from domovoi.db.repositories import VoicesRepository

    try:
        async with session_scope() as s:
            repo = VoicesRepository(s)
            v = await repo.get_by_name(voice_name) if voice_name else None
            if v is None:
                v = await repo.get_default()
    except Exception as e:
        log.debug("voice resolve failed for %r: %s", voice_name, e)
        return (None, None)
    if v is None:
        return (None, None)
    return (v["engine"], v["model_ref"])


class StreamSession:
    """Per-connection state machine.

    The receive loop is the only thing that touches `audio_buf` /
    `utterance_active` — when an utterance ends, it spawns a response task and
    keeps draining the socket so a mid-response `barge_in` is delivered
    promptly. The response task owns all server-→client traffic for its turn.
    """

    def __init__(self, ws: WebSocket, room_id: str) -> None:
        self.ws = ws
        self.room_id = room_id
        self.session_id: UUID | None = None
        self.audio_buf = bytearray()
        self.utterance_active = False
        self.dropped_overflow = False
        # Set when the hello frame's pairing check refused the connection
        # (V002) — the receive loop breaks so a rejected impostor's socket
        # tears down promptly after the error frame + close.
        self._pairing_refused = False
        self._response_task: asyncio.Task[None] | None = None
        # ── Two-way drop-in (Feature 4) ──────────────────────────────
        # When paired, `dropin_peer` is the live StreamSession on the
        # other end of the call. While it's set (and no utterance is
        # actively being captured), `_on_audio` relays raw mic PCM to
        # the peer instead of buffering it for STT. `dropin_call_id` is
        # the shared `dropin_calls` audit row; `_dropin_last_audio` is
        # the monotonic timestamp of the last relayed frame (either
        # direction), read by the silence-timeout watchdog.
        self.dropin_peer: "StreamSession | None" = None
        self.dropin_call_id: int | None = None
        self._dropin_last_audio: float = 0.0
        self._dropin_silence_task: asyncio.Task[None] | None = None
        # ── Conversational chat mode (Feature 8) ─────────────────────
        # In-memory mirror of sessions.context["conversational_mode"], refreshed
        # at the top of every turn so the tool bridge + watchdog can read it
        # without a DB hit. The DB context stays authoritative across reconnects.
        self.conversational_mode: bool = False
        self._chat_silence_task: asyncio.Task[None] | None = None
        self._chat_last_activity: float = 0.0
        # ── Wake-word clip recording (Feature 5) ─────────────────────
        # When set, the server is collecting positive training clips from
        # this satellite (a dashboard-initiated "Record on <room>"). Each
        # utterance_end writes one clip WAV + bumps clip_count instead of
        # transcribing/routing. None when not recording. See
        # `WakeRecordingState`.
        self.wake_recording: WakeRecordingState | None = None
        # Trigger of the in-flight utterance (captured at utterance_start).
        # The clip-vs-command divert at utterance_end keys off THIS immutable
        # value, not the live `wake_recording`, so a stop_wake_recording racing
        # the final clip can never route a training clip through STT/route().
        self._utterance_trigger: str | None = None

    async def run(self) -> None:
        await self.ws.accept()
        # Spin up the per-room MPD daemon before sending `ready` so the
        # first music command after connect doesn't race a slow first-boot
        # `docker run`. ensure_room is idempotent — known rooms hit the
        # in-memory cache and return in microseconds. New rooms allocate
        # ports + spawn a container. Failures here are non-fatal: we log
        # and continue so non-music handlers still work.
        if not settings.use_stubs:
            try:
                from domovoi.mpd_provisioner import ensure_room
                await ensure_room(self.room_id)
            except Exception as e:
                log.warning(
                    "MPD provisioning failed for room=%s (music will be unavailable): %s",
                    self.room_id, e,
                )
        # Register so IntercomHandler / TimerWatcher can reach this Pi by
        # room_id. A second connect for the same room overwrites the first
        # — misconfig stays functional but only the latest connection
        # receives broadcasts.
        # Track whether this session previously overwrote a stale entry
        # — when it does, the prior connection's finally block will
        # find `sessions.get(room_id) is not self_prev` and skip
        # eviction, but it's still useful for ops to see "kitchen
        # reconnected" in the log rather than silently swapping.
        existing = self.ws.app.state.active_sessions.get(self.room_id)
        self.ws.app.state.active_sessions[self.room_id] = self
        if existing is not None and existing is not self:
            log.info(
                "ws %s connected (replacing prior session — likely a reconnect)",
                self.room_id,
            )
        else:
            log.info("ws %s connected", self.room_id)
        await self._safe_send_text({
            "type": "ready",
            "protocol_version": PROTOCOL_VERSION,
            "room_id": self.room_id,
            "bot_name": settings.bot_name,
            "audio_sample_rate_in": PCM_INPUT_SAMPLE_RATE,
        })
        try:
            while True:
                msg = await self.ws.receive()
                kind = msg.get("type")
                if kind == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await self._on_audio(msg["bytes"])
                elif msg.get("text") is not None:
                    try:
                        ctrl = json.loads(msg["text"])
                    except json.JSONDecodeError:
                        await self._safe_send_text({"type": "error", "message": "invalid json"})
                        continue
                    await self._on_control(ctrl)
                    # A refused pairing (V002) closed the socket inside the
                    # hello handler; stop reading so the connection tears down.
                    if self._pairing_refused:
                        break
        except WebSocketDisconnect:
            pass
        except Exception as e:
            # Non-disconnect errors land here. Log at warning so a
            # ping-timeout / unexpected close is visible — the
            # `websockets` library raises ConnectionClosed{Error,OK}
            # which we want to see when debugging WS-vs-wifi issues.
            log.warning("ws %s receive loop ended with %s: %s",
                        self.room_id, type(e).__name__, e)
        finally:
            if self._response_task and not self._response_task.done():
                self._response_task.cancel()
            # Tear down any live drop-in this session was in so the peer
            # isn't left streaming into a void (and its suppressed music is
            # restored). Runs regardless of whether a reconnect overwrote us
            # — a stale session still holds the pairing until we clear it.
            if self.dropin_peer is not None:
                try:
                    await self._end_dropin(ended_by=self.room_id, status="ended")
                except Exception as e:
                    log.warning(
                        "drop-in: teardown on disconnect failed for %s: %s",
                        self.room_id, e,
                    )
            # Drop any in-progress wake-word recording (Feature 5). The Pi is
            # gone, so there's nothing to stop on its end — just clear our
            # state so a reconnect starts clean. Clips already written survive.
            if self.wake_recording is not None:
                log.info(
                    "wake recording: connection dropped mid-record for %s "
                    "(slug=%s, %d clips captured)",
                    self.room_id, self.wake_recording.slug,
                    self.wake_recording.clips_written,
                )
                self.wake_recording = None
            # Deregister, but only if we're still the active session for
            # this room. A reconnect that overwrote us shouldn't have its
            # entry yanked when the stale instance unwinds.
            sessions = self.ws.app.state.active_sessions
            were_active = sessions.get(self.room_id) is self
            if were_active:
                sessions.pop(self.room_id, None)
                log.info("ws %s disconnected; evicted from active_sessions", self.room_id)
                # Drop the cached WiFi reading too — a Pi that's been gone
                # for any length of time is not authoritative on its
                # current rate. Skip when we were already overwritten by
                # a newer session, which has its own (fresher) data.
                self.ws.app.state.wifi_status.pop(self.room_id, None)
                # Same for the cached output volume — a reconnecting Pi
                # re-reports its real level via volume_status on connect.
                self.ws.app.state.satellite_volume.pop(self.room_id, None)
                # And the reported voice — re-reported via voice_status on
                # connect; until then the room falls back to the default.
                self.ws.app.state.satellite_voice.pop(self.room_id, None)
                # And the reported config — re-reported via config_status on
                # connect (so the Settings tab shows "satellite offline"
                # rather than stale values for a disconnected Pi).
                self.ws.app.state.satellite_config.pop(self.room_id, None)
                self.ws.app.state.satellite_full_duplex.pop(self.room_id, None)
                # And the reported code version — re-reported in the hello
                # frame on reconnect.
                self.ws.app.state.satellite_synced_sha.pop(self.room_id, None)
                # And the reported satellite type / mic state — re-reported in
                # the hello frame on reconnect (the persistent copy lives in
                # the `satellites` table for offline display).
                self.ws.app.state.satellite_sat_type.pop(self.room_id, None)
                self.ws.app.state.satellite_mic_enabled.pop(self.room_id, None)
                # And the screen/kiosk state — re-reported via display_status
                # on reconnect (video satellites only).
                self.ws.app.state.satellite_display.pop(self.room_id, None)

    async def _on_audio(self, data: bytes) -> None:
        # ── Drop-in relay (Feature 4) ───────────────────────────────
        # While paired AND not capturing a command utterance, every raw
        # mic frame is forwarded verbatim to the peer room's socket — no
        # STT, no utterance buffer, no _persist_turn. This branch sits
        # BEFORE the utterance gate because open-mic audio is continuous
        # and unframed. A mid-call wake-word command (e.g. "hey jarvis,
        # hang up") sends utterance_start, which sets `utterance_active`
        # — so the very same audio path falls through to normal buffering
        # below and the command reaches the router (DropInHandler ends
        # the call). The peer's own frames keep flowing the other way.
        peer = self.dropin_peer
        if peer is not None and not self.utterance_active:
            # Relay noise gate: don't forward near-silent frames (bounced
            # cross-room echo / room tone between nearby rooms), and DON'T
            # reset the silence timer for them — so genuine quiet lets
            # dropin_silence_timeout_sec fire. -120 ~ disabled.
            gate = float(getattr(settings, "dropin_relay_gate_dbfs", -120.0))
            if gate > -120.0 and _pcm_dbfs(data) < gate:
                return
            self._dropin_last_audio = asyncio.get_running_loop().time()
            try:
                await peer.ws.send_bytes(data)
            except Exception as e:
                log.warning(
                    "drop-in relay %s→%s failed; tearing down: %s",
                    self.room_id, peer.room_id, e,
                )
                await self._end_dropin(ended_by="system", status="failed")
            return
        if not self.utterance_active or self.dropped_overflow:
            return
        if len(self.audio_buf) + len(data) > MAX_UTTERANCE_BYTES:
            self.dropped_overflow = True
            log.warning(
                "stream %s: utterance exceeded %d bytes; further audio dropped",
                self.room_id,
                MAX_UTTERANCE_BYTES,
            )
            return
        self.audio_buf.extend(data)

    async def _validate_pairing(self, ctrl: dict[str, Any]) -> bool:
        """Trust-on-first-use WS auth for the hello frame (V002).

        Returns True to ACCEPT the connection, False to REFUSE (the caller
        closes the socket). Only the sha256 of a token is ever compared or
        stored — reusing ``admin_auth.token_sha256`` — so the raw token never
        leaves the Pi's sidecar. The five cases:

          1. token, no pairing row  -> PAIR (claim the room), accept
          2. token, row hash match  -> accept, bump last_seen_at
          3. token, row hash MISMATCH -> REFUSE (impostor / wrong token)
          4. no token, row EXISTS    -> REFUSE (a paired room requires its token)
          5. no token, no row        -> accept (older/unpaired satellite),
                                        UNLESS ``settings.satellite_pairing_strict``
                                        is on, then REFUSE.

        On a REFUSE we send a text ``error`` frame ({reason:"pairing_rejected"})
        and provision/relay NOTHING — the caller closes the socket.
        """
        raw = ctrl.get("pairing_token")
        presented = raw.strip() if isinstance(raw, str) and raw.strip() else None

        async def _reject(reason_log: str) -> bool:
            log.warning("pairing: REFUSED room=%s — %s", self.room_id, reason_log)
            await self._safe_send_text(
                {"type": "error", "reason": "pairing_rejected",
                 "message": "pairing rejected"}
            )
            return False

        try:
            async with session_scope() as s:
                repo = SatellitePairingRepository(s)
                row = await repo.get_pairing(self.room_id)
                if presented is not None:
                    token_hash = token_sha256(presented)
                    if row is None:
                        # Case 1 — first token wins: claim the room.
                        await repo.pair(self.room_id, token_hash)
                        log.info(
                            "pairing: room=%s paired (trust-on-first-use)",
                            self.room_id,
                        )
                        return True
                    if secrets.compare_digest(row[0], token_hash):
                        # Case 2 — token matches the paired hash.
                        await repo.touch_last_seen(self.room_id)
                        return True
                    # Case 3 — a token, but the WRONG one.
                    return await _reject("pairing_token mismatch (possible impostor)")
                # No token presented.
                if row is not None:
                    # Case 4 — a paired room must present its token; a
                    # tokenless impostor must not take over.
                    return await _reject(
                        "paired room connected without a pairing_token "
                        "(possible impostor)"
                    )
                # Case 5 — no token, no row.
                if settings.satellite_pairing_strict:
                    return await _reject(
                        "no pairing_token and strict pairing is enabled"
                    )
                log.info(
                    "pairing: room=%s connected without a token "
                    "(older/unpaired; strict pairing off)",
                    self.room_id,
                )
                return True
        except Exception as e:
            # A DB hiccup must not silently strip auth from a paired room, but
            # it also must not break a tokenless older fleet. Bias to the
            # configured posture: strict → fail closed, lenient (default) →
            # preserve the zero-breakage default and accept.
            if _is_missing_pairing_table(e):
                # Almost always a not-yet-applied migration. Warn ONCE with an
                # actionable hint instead of once per hello for every room.
                global _pairing_table_warned
                if not _pairing_table_warned:
                    _pairing_table_warned = True
                    log.warning(
                        "pairing: the satellite_pairings table is missing — "
                        "run DB migrations (docker compose run --rm flyway) to "
                        "enable pairing. Accepting connections meanwhile "
                        "(strict=%s).",
                        settings.satellite_pairing_strict,
                    )
            else:
                log.warning(
                    "pairing: validation error for room=%s: %s", self.room_id, e
                )
            return not settings.satellite_pairing_strict

    async def _persist_sat_type(self, sat_type: str) -> None:
        """Best-effort upsert of an EXPLICIT hello ``sat_type`` into the
        `satellites` inventory table (V003) so the dashboard renders the
        right type for offline satellites. Never raises — a DB hiccup or a
        not-yet-applied migration must not break the connect."""
        try:
            async with session_scope() as s:
                await SatellitesRepository(s).upsert_type(self.room_id, sat_type)
        except Exception as e:
            if _is_missing_satellites_table(e):
                global _satellites_table_warned
                if not _satellites_table_warned:
                    _satellites_table_warned = True
                    log.warning(
                        "satellites: the satellites table is missing — run DB "
                        "migrations (docker compose run --rm flyway) to persist "
                        "satellite types. Continuing without persistence."
                    )
            else:
                log.warning(
                    "satellites: sat_type persist failed for room=%s: %s",
                    self.room_id, e,
                )

    async def _on_control(self, ctrl: dict[str, Any]) -> None:
        t = ctrl.get("type")
        if t == "hello":
            # ── Pairing-token validation (V002) ──────────────────────────
            # Trust-on-first-use WS auth: verify (or claim) this room's
            # pairing before we cache anything or treat the socket as this
            # room's satellite. A refusal closes the connection and skips all
            # provisioning/caching below — an impostor gets nothing.
            if not await self._validate_pairing(ctrl):
                self._pairing_refused = True
                try:
                    await self.ws.close(code=1008)
                except Exception:
                    pass
                return
            # Cache whether this room's board has on-chip AEC (full duplex),
            # so drop-in / open-mic gating can refuse a no-AEC board rather
            # than let it howl. Cleared on disconnect.
            self.ws.app.state.satellite_full_duplex[self.room_id] = bool(
                ctrl.get("supports_full_duplex", False)
            )
            # Cache the code version the Pi recorded after its last
            # satellite-code sync (None if never synced), so the dashboard can
            # flag satellites behind the core's SHA. Cleared on
            # disconnect.
            self.ws.app.state.satellite_synced_sha[self.room_id] = ctrl.get(
                "synced_sha"
            )
            # Cache the satellite type and whether voice input is running.
            # Both are OPTIONAL hello fields (absent on older clients) and
            # default to the historical behavior: a voice satellite with a
            # live mic. Cleared on disconnect.
            raw_sat_type = ctrl.get("sat_type")
            if raw_sat_type is not None and raw_sat_type not in ("voice", "video"):
                log.warning(
                    "hello: room=%s reported unknown sat_type %r — treating as "
                    "'voice'", self.room_id, raw_sat_type,
                )
                raw_sat_type = None
            self.ws.app.state.satellite_sat_type[self.room_id] = (
                raw_sat_type or "voice"
            )
            self.ws.app.state.satellite_mic_enabled[self.room_id] = bool(
                ctrl.get("mic_enabled", True)
            )
            # Persist the type for offline dashboard display — but ONLY when
            # the frame carried it explicitly, so an old client that omits
            # the field never resets an adoption-preseeded row to 'voice'.
            if raw_sat_type is not None:
                await self._persist_sat_type(raw_sat_type)
            return
        if t == "ping":
            await self._safe_send_text({"type": "pong"})
            return
        if t == "utterance_start":
            if self._response_task and not self._response_task.done():
                self._response_task.cancel()
            self.utterance_active = True
            self.dropped_overflow = False
            self.audio_buf.clear()
            # Latch the trigger now; utterance_end reads it to decide
            # clip-vs-command (immune to wake_recording flipping mid-utterance).
            self._utterance_trigger = ctrl.get("trigger")
            return
        if t == "utterance_end":
            if not self.utterance_active:
                return
            self.utterance_active = False
            pcm = bytes(self.audio_buf)
            self.audio_buf.clear()
            trigger = self._utterance_trigger
            self._utterance_trigger = None
            # Wake-word recording mode (Feature 5): a turn the Pi flagged with
            # trigger="wake_clip" is one positive training clip, NOT a command.
            # Branch on the LATCHED trigger (not the live `wake_recording`) so a
            # stop_wake_recording that races this final clip can never let a
            # training clip fall through to STT/route(). Save it when a session
            # is still armed; if it was just stopped, drop it silently — but
            # never route it.
            if trigger == "wake_clip":
                if self.wake_recording is not None:
                    await self._save_wake_clip(pcm)
                return
            # The Pi flags turns where it played a wake greeting concurrent
            # with capture; if so we strip a bled-in greeting (past the AEC)
            # from the transcript before routing.
            greeting_played = bool(ctrl.get("greeting_played"))
            self._response_task = asyncio.create_task(
                self._process_utterance(pcm, greeting_played=greeting_played)
            )
            return
        if t == "barge_in":
            if self._response_task and not self._response_task.done():
                self._response_task.cancel()
            return
        if t == "music_ready":
            # Pi's mpg123 has primed against MPD's silence stream and
            # is ready for song frames. Cancel the fallback timer and
            # resume MPD now.
            await consume_music_ready(self.ws.app, self.room_id)
            return
        if t == "wifi_status":
            # Periodic self-report from the Pi's WiFiWatcher (~every
            # poll, default 60 s). Cached by room_id so WifiHandler's
            # "how's your wifi" diagnostic can answer without another
            # round-trip. Best-effort field extraction — a malformed
            # frame from a buggy Pi shouldn't kill the session.
            try:
                rx = ctrl.get("rx_mbits")
                tx = ctrl.get("tx_mbits")
                ssid = ctrl.get("ssid")
                self.ws.app.state.wifi_status[self.room_id] = {
                    "rx_mbits": float(rx) if rx is not None else None,
                    "tx_mbits": float(tx) if tx is not None else None,
                    "ssid": str(ssid) if ssid else None,
                }
            except (TypeError, ValueError) as e:
                log.debug("ignoring malformed wifi_status from %s: %s", self.room_id, e)
            return
        if t == "volume_status":
            # Satellite reporting its current hardware output volume
            # (0-100), on connect and after each set_volume. Cached by
            # room so MusicHandler's relative "turn it up" bumps against
            # the real level. Best-effort — a malformed frame is ignored.
            try:
                level = int(ctrl.get("level"))
                self.ws.app.state.satellite_volume[self.room_id] = max(0, min(100, level))
            except (TypeError, ValueError) as e:
                log.debug("ignoring malformed volume_status from %s: %s", self.room_id, e)
            return
        if t == "voice_status":
            # Satellite reporting which registered voice it speaks in, on
            # connect and after a set_voice action lands. Cached by room so
            # the synth path renders this room's responses + greetings in
            # that voice. A blank/missing name clears the cache (→ default).
            voice = ctrl.get("voice")
            if voice:
                self.ws.app.state.satellite_voice[self.room_id] = str(voice)
            else:
                self.ws.app.state.satellite_voice.pop(self.room_id, None)
            return
        if t == "config_status":
            # Satellite reporting its current editable config (on connect),
            # so the dashboard's per-satellite Settings tab renders real
            # values. Cached by room; cleared on disconnect.
            cfg = ctrl.get("config")
            if isinstance(cfg, dict):
                self.ws.app.state.satellite_config[self.room_id] = cfg
            return
        if t == "display_status":
            # Video satellite reporting its screen/kiosk state (on connect,
            # after each applied set_display, and on kiosk-alive changes).
            # Cached by room for the dashboard's Display controls; cleared
            # on disconnect. Best-effort — a malformed frame is ignored.
            try:
                brightness = ctrl.get("brightness")
                self.ws.app.state.satellite_display[self.room_id] = {
                    "on": bool(ctrl.get("on", True)),
                    "kiosk_alive": bool(ctrl.get("kiosk_alive", False)),
                    "brightness": (
                        max(0, min(100, int(brightness)))
                        if brightness is not None
                        else None
                    ),
                    "idle_mode": str(ctrl.get("idle_mode") or "clock"),
                }
            except (TypeError, ValueError) as e:
                log.debug(
                    "ignoring malformed display_status from %s: %s",
                    self.room_id, e,
                )
            return
        if t == "noisy_capture":
            # Pi-side noise-gate auto-tune detected an unusually loud
            # capture and bailed before sending utterance_end. We don't
            # have audio worth transcribing — instead, kick a static
            # apology TTS so the user hears feedback (rather than dead
            # air) and the satellite has already recalibrated against
            # the new ambient on its end.
            if self._response_task and not self._response_task.done():
                self._response_task.cancel()
            self.utterance_active = False
            self.audio_buf.clear()
            self.dropped_overflow = False
            self._response_task = asyncio.create_task(
                self._respond_noisy_capture(), name="noisy-apology"
            )
            return
        if t == "dropin_end":
            # Pi-side end of an active drop-in (hardware button or a
            # client-detected hang-up). The spoken "hang up" path doesn't
            # come through here — it's a normal routed utterance that
            # DropInHandler ends server-side. No-op when not in a call.
            if self.dropin_peer is not None:
                await self._end_dropin(ended_by=self.room_id, status="ended")
            return
        if t == "chat_end":
            # Pi-side exit of conversational chat mode — a HAT room's AEC refusal
            # of chat_start, or any client-detected end. Tear the mode down
            # SERVER-side so the next turn routes through the command router
            # again. Without this, a refused/abandoned chat would leave
            # conversational_mode set in the session context and silently hijack
            # every subsequent command turn into Letta (no recovery short of an
            # exit phrase). No-op when not in chat mode.
            await self._clear_chat_mode()
            return
        await self._safe_send_text({"type": "error", "message": f"unknown control type: {t!r}"})

    async def _respond_noisy_capture(self) -> None:
        """Stock-apology TTS path for the Pi-side noisy-capture trigger.

        The Pi has already auto-recalibrated its noise gate by the time
        we get here; our job is just to give the user audible feedback
        so they know to retry. Doesn't hit the router and doesn't write
        to ``intents_log`` — this is a system message about audio
        conditions, not an intent. We deliberately keep ``expect_followup``
        false: the user needs a beat to mute the TV / move closer, and
        forcing them through wake-word again gives them that beat.
        """
        text = (
            "I'm having trouble hearing you over the background noise — "
            "give it another try."
        )
        try:
            tts = get_tts_client()
            n_engine, n_voice = await resolve_voice(
                self.ws.app.state.satellite_voice.get(self.room_id)
            )
            pcm, sr = _wav_to_pcm(
                await tts.synthesize(text, engine=n_engine, voice=n_voice)
            )
        except Exception as e:
            log.warning("stream %s: noisy-capture TTS failed: %s", self.room_id, e)
            await self._safe_send_text({"type": "error", "message": str(e)})
            return

        await self._safe_send_text({
            "type": "response_start",
            "text": text,
            "matched_handler": "noisy_capture",
            "matched_path": "system",
            "session_id": str(self.session_id) if self.session_id else None,
            "online": True,
            "audio_sample_rate": sr,
        })
        for chunk in _iter_chunks(pcm):
            try:
                await self.ws.send_bytes(chunk)
            except Exception:
                return  # Pi went away mid-broadcast
        await self._safe_send_text({
            "type": "response_end",
            "interrupted": False,
            "expect_followup": False,
        })

    async def _process_utterance(
        self, pcm_bytes: bytes, *, greeting_played: bool = False
    ) -> None:
        interrupted = False
        response = None
        try:
            transcript = await get_whisper_client().transcribe(pcm_bytes)
            # If the Pi played a wake greeting this turn, the array's AEC may
            # have let it bleed into the capture ("Hi there. Say something
            # mean." → greeting + command). Strip a known leading greeting so
            # only the real command routes.
            if greeting_played:
                from domovoi.greeting_filter import strip_leading_greeting
                phrases = getattr(self.ws.app.state, "greeting_phrases", [])
                if phrases:
                    cleaned = strip_leading_greeting(transcript, phrases)
                    if cleaned != transcript:
                        log.info("stripped bled-in greeting: %r → %r", transcript, cleaned)
                        transcript = cleaned
            await self._safe_send_text({"type": "transcript", "text": transcript})

            probe: ConnectivityProbe = self.ws.app.state.probe

            # Pre-router voice identification: embed the utterance, look
            # up a matching `people` row, compute the presence tier so
            # downstream handlers and audit queries know who's talking
            # and how aggressively to prompt for identity. Best-effort —
            # any failure leaves person_id=None / presence_tier="high"
            # rather than blocking the response cycle.
            from domovoi.voice_identifier import identify
            try:
                ident = await identify(pcm_bytes)
            except Exception as e:
                log.warning("voice identification failed: %s", e)
                ident = None

            person_id = ident.person_id if ident else None
            presence_tier = ident.presence_tier if ident else None
            embedding_bytes: bytes | None = None
            if ident is not None and ident.embedding is not None:
                embedding_bytes = ident.embedding.astype("float32").tobytes()

            # Stamp the most-recent WiFi self-report from this room's Pi
            # into the Context so WifiHandler's diagnostic path can
            # answer "how's your wifi" without another round-trip. Empty
            # dict when the Pi hasn't pushed yet (first 60 s after connect).
            wifi_status = self.ws.app.state.wifi_status.get(self.room_id, {})
            # Same for the satellite's current output volume, so a relative
            # "turn it up" bumps against the real level (None until the Pi
            # reports via volume_status on connect).
            satellite_volume = self.ws.app.state.satellite_volume.get(self.room_id)
            # The voice this room reports speaking in (None until the Pi
            # pushes voice_status, or if it never sets one → registry
            # default). VoiceHandler reads it via Context.voice to answer
            # "what voice are you using".
            satellite_voice = self.ws.app.state.satellite_voice.get(self.room_id)
            ctx = Context(
                room_id=self.room_id,
                session_id=self.session_id,
                online=probe.online,
                bot_name=settings.bot_name,
                person_id=person_id,
                presence_tier=presence_tier,
                embedding_bytes=embedding_bytes,
                wifi_status=wifi_status,
                satellite_volume=satellite_volume,
                voice=satellite_voice,
                app=self.ws.app,
            )
            intent = Intent(
                transcript=transcript,
                room_id=self.room_id,
                session_id=self.session_id,
            )
            audio_seconds = len(pcm_bytes) / (16_000 * 2)

            # ── Conversational chat-mode bypass (Feature 8) ──────────────
            # BEFORE running the command-mode router, check whether this
            # session is in conversational chat mode (set by ChatModeHandler
            # into sessions.context). If so, this turn does NOT route through
            # the handler registry at all — it either ends the chat (exit
            # phrase) or goes to the Letta agent. Command mode is completely
            # untouched: when not in chat mode, `conversational_mode` is
            # falsey and we fall straight through to the normal route() path,
            # so the only added cost on a command turn is one cheap
            # session-context read.
            if self.session_id is not None:
                async with session_scope() as s:
                    chat_ctx = await SessionRepository(s).get_context(
                        self.session_id
                    )
                # Refresh the in-memory mirror from the authoritative DB flag
                # (covers a reconnect that re-bound this session mid-chat).
                self.conversational_mode = bool(chat_ctx.get("conversational_mode"))
                if chat_ctx.get("conversational_mode"):
                    await self._run_chat_turn(
                        transcript=transcript,
                        ctx=ctx,
                        session_id=self.session_id,
                        exiting=_is_chat_exit(transcript),
                    )
                    return

            async with session_scope() as s:
                response = await route(intent, ctx, s)
                # Third-party intro hybrid hook. Two mutually-exclusive
                # paths run in the same transaction as routing so the
                # post-state is consistent:
                #   * unknown voice + active expectation → buffer this
                #     turn into the expectation's candidate clusters.
                #   * introducer voice + buffered clusters → maybe
                #     append "By the way, was that <name>?" to the
                #     response and park pending_confirmation.
                # The hooks no-op cleanly when no expectation is parked.
                from domovoi.handlers.voice_profile import (
                    buffer_unknown_voice_turn,
                    maybe_inject_third_party_ask,
                )
                ctx_with_session = ctx.model_copy(
                    update={"session_id": response.session_id}
                )
                if (
                    person_id is None
                    and ident is not None
                    and ident.embedding is not None
                    # Respect the opt-out: a denylisted ("never save my voice")
                    # speaker must not be buffered into a third-party enrollment
                    # cluster, or someone else's introduction could re-enroll
                    # them without consent.
                    and not ident.denylisted
                ):
                    await buffer_unknown_voice_turn(
                        s,
                        session_id=response.session_id,
                        transcript=transcript,
                        embedding=ident.embedding,
                        audio_seconds=audio_seconds,
                    )
                elif person_id is not None:
                    injected = await maybe_inject_third_party_ask(
                        s,
                        ctx=ctx_with_session,
                        response=response,
                    )
                    if injected:
                        # The conversation_log row for this turn was
                        # written by route() with the *pre-injection*
                        # text. Update it so the audit trail matches
                        # what the user actually hears. Targets the
                        # most recent row for this session — safe
                        # because we're inside the same transaction
                        # and no concurrent turn can interleave.
                        from sqlalchemy import text as sql_text
                        await s.execute(
                            sql_text(
                                """
                                UPDATE conversation_log
                                SET assistant_text = :t
                                WHERE id = (
                                    SELECT id FROM conversation_log
                                    WHERE session_id = :sid
                                    ORDER BY id DESC
                                    LIMIT 1
                                )
                                """
                            ),
                            {"t": response.text, "sid": str(response.session_id)},
                        )
            self.session_id = response.session_id

            tts = get_tts_client()
            sentences = _split_sentences(response.text) or [response.text or ""]

            # Resolve which voice to synthesize this turn in. A per-response
            # override (VoiceHandler sampling / switching) wins; otherwise
            # the room's reported voice; otherwise the registry default.
            # (None, None) → the TTS client's construct-time globals.
            override = getattr(response, "voice_override", None)
            synth_engine, synth_voice = await resolve_voice(override or satellite_voice)

            # Synthesize the first sentence so we know the sample rate before
            # emitting response_start. Subsequent sentences inherit it.
            first_pcm, sr = _wav_to_pcm(
                await tts.synthesize(sentences[0], engine=synth_engine, voice=synth_voice)
            )

            # Master output-volume change (MusicHandler), applied BEFORE the
            # response audio so the spoken confirmation ("Volume up to 80
            # percent.") is itself heard at the new level. The satellite
            # drives its hardware mixer, which scales both TTS and music.
            if response.satellite_volume is not None:
                await self._safe_send_text({
                    "type": "set_volume",
                    "level": max(0, min(100, int(response.satellite_volume))),
                })

            await self._safe_send_text({
                "type": "response_start",
                "text": response.text,
                "matched_handler": response.matched_handler,
                "matched_path": response.matched_path,
                "session_id": str(response.session_id) if response.session_id else None,
                "online": response.online,
                "audio_sample_rate": sr,
            })

            async def _synth(s: str) -> tuple[bytes, int]:
                # Keep each sentence's OWN sample rate — the engine fallback
                # chain can render a later sentence with a different engine
                # (and rate) than the first, so the caller must reconcile it
                # against the response's announced rate rather than assume
                # uniformity.
                pcm, s_sr = _wav_to_pcm(
                    await tts.synthesize(s, engine=synth_engine, voice=synth_voice)
                )
                return pcm, s_sr

            # Pipeline sentence synthesis so the Pi never sees a gap in
            # the WS audio stream. The naive serial pattern
            # (`for s: synth → stream`) blocks for 100-500 ms per
            # sentence between sends, which drains the Pi's playback
            # queue and produces an audible click between sentences.
            # Instead, kick off the next synthesis as a background task
            # before we start streaming the current one — by the time
            # the current chunks are sent, the next one's PCM is
            # usually already done.
            next_task: asyncio.Task[tuple[bytes, int]] | None = (
                asyncio.create_task(_synth(sentences[1]))
                if len(sentences) > 1
                else None
            )
            try:
                for chunk in _iter_chunks(first_pcm):
                    await self.ws.send_bytes(chunk)

                for i, _ in enumerate(sentences[1:], start=1):
                    assert next_task is not None
                    pcm, pcm_sr = await next_task
                    next_task = (
                        asyncio.create_task(_synth(sentences[i + 1]))
                        if i + 1 < len(sentences)
                        else None
                    )
                    # Reconcile against the rate the Pi is playing at (the
                    # first sentence's). A mismatch means this sentence hit a
                    # different engine via the fallback chain; resample so its
                    # audio doesn't play too fast/slow (garbled response tail).
                    if pcm_sr != sr:
                        log.warning(
                            "TTS sentence %d rate %d != response rate %d; "
                            "resampling (engine fallback mid-response?)",
                            i, pcm_sr, sr,
                        )
                        pcm = _resample_pcm(pcm, pcm_sr, sr)
                    for chunk in _iter_chunks(pcm):
                        await self.ws.send_bytes(chunk)
            finally:
                # Cancel any in-flight synth on early exit (barge-in,
                # WS drop, exception) so we don't leak a background
                # task waiting on edge-tts to return audio nobody will
                # ever play.
                if next_task is not None and not next_task.done():
                    next_task.cancel()
        except asyncio.CancelledError:
            interrupted = True
        except Exception as e:
            log.exception("stream %s: response task failed", self.room_id)
            await self._safe_send_text({"type": "error", "message": str(e)})
            # Pair the error with a terminal response_end so the
            # satellite's mic thread unblocks `_await_response_with_barge`
            # and returns to wake-word listen. Without this pairing the
            # Pi can hang on a single backend hiccup (DB unreachable
            # mid-turn, handler exception, etc.); see the 2026-05-08
            # 19:09 incident — DB went down, sat got `error` only, mic
            # stayed parked until the WS dropped. `interrupted=True`
            # because the user didn't get a real response.
            await self._safe_send_text({
                "type": "response_end",
                "interrupted": True,
                "expect_followup": False,
            })
            return

        # `expect_followup` lets handlers ask the Pi to capture the
        # user's reply without requiring a fresh wake word. Skipped on
        # `interrupted=True` because the bot's question got cut off —
        # we never asked, so we shouldn't expect.
        expect_followup = bool(
            not interrupted
            and response is not None
            and getattr(response, "expect_followup", False)
        )
        # `pi_action` carries a Pi-local side-effect for after playback
        # drains (currently only WifiHandler's reassociate_wifi). Same
        # interrupted-skip as expect_followup: if the user cut us off,
        # we never finished saying "OK, reconnecting now" so don't
        # silently kick the WS.
        pi_action: str | None = (
            getattr(response, "pi_action", None)
            if response is not None and not interrupted
            else None
        )
        pi_action_arg = (
            getattr(response, "pi_action_arg", None)
            if response is not None and not interrupted
            else None
        )
        end_frame: dict[str, Any] = {
            "type": "response_end",
            "interrupted": interrupted,
            "expect_followup": expect_followup,
        }
        if pi_action is not None:
            end_frame["pi_action"] = pi_action
            if pi_action_arg is not None:
                end_frame["pi_action_arg"] = pi_action_arg
        await self._safe_send_text(end_frame)

        # Chat-mode open-mic kickoff (Feature 8). ChatModeHandler flips the
        # session into conversational_mode and parks a one-shot
        # `chat_start_pending` marker in sessions.context (it has no socket to
        # send the frame itself). Now that the ENTER ack has fully streamed,
        # send the `chat_start` frame so the Pi opens its open mic, and clear
        # the marker. Skipped on `interrupted` — if the user barged over the
        # "let's chat" ack, they never heard it, so don't silently open the
        # mic; the marker stays parked and a later non-interrupted turn (or a
        # re-entry) opens it. Mirrors the dropin fan-out's "act after the turn"
        # pattern but is driven by a context flag rather than a Response field.
        # Only after a chat-ENTER turn (ChatModeHandler) is the marker possibly
        # set — gate the context read on that so an ordinary command turn never
        # pays it (command-mode latency invariant).
        if (
            not interrupted
            and self.session_id is not None
            and response is not None
            and response.matched_handler == "chat_mode"
        ):
            await self._maybe_send_chat_start()

        # Music coordination — only fire when the response completed
        # cleanly. Skipped on barge-in / cancel because the user just
        # interrupted, and skipped on exception because `response` is None.
        # The resume branch covers the most common surprise: music
        # playing, user says "what time is it", wake-word capture kills
        # mpg123 on the Pi, the response has no music_action, and
        # without resume the music would stay dead.
        #
        # `expect_followup` SUPPRESSES every music_start emission (both
        # the explicit start and the auto-resume) for this turn. The
        # bot just asked the user a question and the satellite is about
        # to capture the wake-word-free reply; respawning mpg123 in
        # that window saturates the Pi's mic, trips noisy_capture, and
        # traps the user in a "having trouble hearing you" loop. The
        # 2026-05-08 12:35 incident: "Add that to my library" → "should
        # I add it too?" → music auto-resumed → mic saturated → 4 noisy
        # retries before the user gave up. Music_stop still fires under
        # expect_followup because that's the safe direction. The URL
        # is still recorded in `resumable` so the followup turn (which
        # will NOT carry expect_followup itself) auto-resumes normally.
        # Drop-in turns are excluded entirely: while a call is live
        # (`dropin_peer` set) the Pi's output belongs to the relay, and a
        # turn that starts/ends a call (`dropin_action` set) has its music
        # suppress/restore handled by _begin_dropin / _end_dropin. Letting
        # the auto-resume branch fire here would respawn mpg123 and collide
        # with the call audio on the single ALSA card.
        if (
            not interrupted
            and response is not None
            and self.dropin_peer is None
            and not getattr(response, "dropin_action", None)
        ):
            resumable: dict[str, str] = self.ws.app.state.resumable_music
            current_pl: dict[str, Any] = self.ws.app.state.current_playlist
            suppress_music_start = bool(getattr(response, "expect_followup", False))
            if response.music_action == "start" and response.music_stream_url:
                if not suppress_music_start:
                    await self._safe_send_text({
                        "type": "music_start",
                        "stream_url": response.music_stream_url,
                    })
                    # Handlers prepared MPD paused; arm the music_ready
                    # handshake so MPD resumes once the Pi's mpg123 has
                    # primed against the always-on silence stream.
                    await schedule_music_resume_fallback(
                        self.ws.app, self.room_id, response.music_stream_url,
                    )
                else:
                    # No music_start to the satellite means no music_ready
                    # is coming. Resume MPD immediately so the next non-
                    # followup turn auto-resumes mpg123 against a stream
                    # that's actually emitting song frames instead of an
                    # indefinitely-paused queue.
                    await _resume_mpd_for_room(self.room_id)
                resumable[self.room_id] = response.music_stream_url
                # See _admin_dispatch_music for the symmetric comment:
                # now-playing stamps / current_playlist aren't cleared
                # on start because matched_handler isn't a reliable
                # source signal at this layer. The freshness check
                # plus the playback-state sweeper handle invalidation.
            elif response.music_action == "stop":
                await self._safe_send_text({"type": "music_stop"})
                resumable.pop(self.room_id, None)
                # Generic now-playing stamp pop (design §4.7) — no
                # provider-specific state dicts to clear.
                NOW_PLAYING.clear(self.room_id)
                current_pl.pop(self.room_id, None)
                # Any pending handshake is moot once the user has asked
                # to stop. The receiver-side fallback also clears its
                # own entry on cancel.
                pending = self.ws.app.state.pending_music_start
                stale = pending.pop(self.room_id, None)
                if stale is not None:
                    stale_task = stale.get("task")
                    if stale_task is not None and not stale_task.done():
                        stale_task.cancel()
            elif self.room_id in resumable and not suppress_music_start:
                await self._safe_send_text({
                    "type": "music_start",
                    "stream_url": resumable[self.room_id],
                })
                # Auto-resume after a non-music turn (e.g. "what time is
                # it" between songs). MPD may be paused (carried over
                # from a prior expect_followup turn that left it queued)
                # or playing — `mpd.resume()` is a no-op in the latter
                # case, so re-running the handshake is safe.
                await schedule_music_resume_fallback(
                    self.ws.app, self.room_id, resumable[self.room_id],
                )

        # Intercom fan-out — synthesize the announcement once and inject
        # it into every target room's WebSocket. The originating Pi
        # already heard the response ("Announcing in the kitchen"); the
        # listed rooms get the actual announcement payload audio. Skips
        # rooms that aren't currently connected (Pi offline) and skips
        # the originating room when it's in the target list to avoid
        # double-playing the announcement.
        if (
            not interrupted
            and response is not None
            and response.announce_to_rooms
            and response.announce_text
        ):
            sessions: dict[str, "StreamSession"] = self.ws.app.state.active_sessions
            for target_room in response.announce_to_rooms:
                target = sessions.get(target_room)
                if target is None or target is self:
                    continue
                try:
                    await target.announce(response.announce_text)
                except Exception as e:
                    log.warning(
                        "intercom: failed to announce to room=%s: %s",
                        target_room, e,
                    )

        # Drop-in fan-out — a handler set `dropin_action` on the Response
        # (DropInHandler.execute for "drop in on X", or handle_confirmation
        # for the target's "yeah"/"no"). The streaming layer owns the live
        # pairing + relay, so it acts on the field here, mirroring the
        # intercom block above. The handler already did feasibility gating
        # (it has ctx.app) and produced the spoken text the user just heard.
        if (
            not interrupted
            and response is not None
            and getattr(response, "dropin_action", None)
        ):
            await self._handle_dropin_action(response)

    async def _handle_dropin_action(self, response: Any) -> None:
        """Act on `response.dropin_action` after the originating turn.

        'request' — this session is the initiator. In auto mode, open the
        call immediately; in confirm mode, prompt the target (no-wake-word
        followup) and park a pending_confirmation it can answer "yeah" to.
        'accept'  — this session is the TARGET answering yes; pair with the
        initiator named in `dropin_room`.
        'end'     — tear down whatever call this session is in.
        """
        action = response.dropin_action
        sessions: dict[str, "StreamSession"] = self.ws.app.state.active_sessions

        if action == "end":
            if self.dropin_peer is not None:
                await self._end_dropin(ended_by=self.room_id, status="ended")
            return

        if action == "request":
            target_room = response.dropin_room
            target = sessions.get(target_room) if target_room else None
            if target is None or target.dropin_peer is not None:
                # Raced — the target dropped or entered another call in the
                # ms between routing (where feasibility was gated) and here.
                # The initiator already heard "Dropping in on the <room>";
                # we can't announce a correction from inside our own
                # response task (announce() refuses mid-response), so log it.
                # The initiator simply never enters open-mic and stays in
                # the wake loop — a stale confirmation, not a wedged call.
                log.warning(
                    "drop-in: target %s unavailable at begin (raced); aborting",
                    target_room,
                )
                return
            mode = getattr(settings, "dropin_accept_mode", "auto")
            if mode == "confirm":
                await self._prompt_target_for_dropin(target)
            else:
                await self._begin_dropin(target)
            return

        if action == "accept":
            initiator_room = response.dropin_room
            initiator = sessions.get(initiator_room) if initiator_room else None
            if initiator is None or initiator.dropin_peer is not None:
                # They hung up (or got into another call) before the target
                # answered. Same mid-response constraint as above — log
                # rather than announce; the target stays idle.
                log.warning(
                    "drop-in: initiator %s gone before %s accepted; aborting",
                    initiator_room, self.room_id,
                )
                return
            # `self` is the target; the initiator made the request.
            await initiator._begin_dropin(self)
            return

    # ── Conversational chat mode (Feature 8) ─────────────────────────────

    async def _clear_chat_mode(self) -> None:
        """Tear down conversational chat mode for this session: cancel the
        silence watchdog, drop the in-memory mirror, and clear the
        ``sessions.context`` flags so the next turn routes as a normal command.
        Idempotent + best-effort (a context-write hiccup never crashes a turn).
        The single teardown path used by the exit phrase, the inbound
        ``chat_end`` frame, the silence watchdog, and an ensure_agent failure."""
        self.conversational_mode = False
        if self._chat_silence_task is not None:
            self._chat_silence_task.cancel()
            self._chat_silence_task = None
        if self.session_id is None:
            return
        try:
            async with session_scope() as s:
                repo = SessionRepository(s)
                await repo.set_context_key(self.session_id, "conversational_mode", None)
                await repo.set_context_key(self.session_id, "letta_agent_id", None)
                await repo.set_context_key(self.session_id, "chat_start_pending", None)
        except Exception as e:
            log.warning("chat: clearing chat mode failed: %s", e)

    async def _chat_silence_watchdog(self, timeout: float) -> None:
        """Auto-exit an abandoned chat open-mic after ``timeout`` seconds with no
        conversational turn — so a forgotten chat can't leave a wake-word-less hot
        mic streaming room tone to Letta forever. ``_chat_last_activity`` is
        bumped on chat entry and on every conversational turn, so any utterance
        resets the clock. On timeout: clear chat mode + send ``chat_end`` so the
        Pi drops back to its wake loop. Mirrors ``_dropin_silence_watchdog``."""
        try:
            interval = max(1.0, min(timeout, 5.0))
            while True:
                await asyncio.sleep(interval)
                if not self.conversational_mode:
                    return
                now = asyncio.get_running_loop().time()
                if now - self._chat_last_activity >= timeout:
                    log.info(
                        "chat: room=%s idle %.0fs; auto-ending", self.room_id, timeout
                    )
                    await self._clear_chat_mode()
                    await self.send_chat_end(reason="silence_timeout")
                    return
        except asyncio.CancelledError:
            return

    async def _maybe_send_chat_start(self) -> None:
        """If ChatModeHandler parked a one-shot ``chat_start_pending`` marker
        in this session's context, send the ``chat_start`` frame (open the Pi's
        chat open-mic) and clear the marker.

        Driven by a context flag rather than a ``Response`` field because the
        handler has no socket and ``models.py`` (the matched_path Literal +
        Response fields) is owned by another agent for this feature. Best-effort
        on the DB read — a transient failure just leaves the marker parked for
        the next turn rather than wedging the response cycle."""
        try:
            async with session_scope() as s:
                repo = SessionRepository(s)
                ctx_data = await repo.get_context(self.session_id)  # type: ignore[arg-type]
                if not ctx_data.get("chat_start_pending"):
                    return
                await repo.set_context_key(
                    self.session_id, "chat_start_pending", None  # type: ignore[arg-type]
                )
        except Exception as e:
            log.warning("chat: reading chat_start_pending failed: %s", e)
            return
        log.info("chat: opening chat mode on room=%s", self.room_id)
        await self.send_chat_start()
        # Arm the abandoned-chat watchdog: if no conversational turn lands within
        # chat_silence_timeout_sec, auto-exit so the open mic can't run forever.
        self.conversational_mode = True
        self._chat_last_activity = asyncio.get_running_loop().time()
        if self._chat_silence_task is None or self._chat_silence_task.done():
            self._chat_silence_task = asyncio.create_task(
                self._chat_silence_watchdog(settings.chat_silence_timeout_sec),
                name="chat-silence",
            )

    async def _run_chat_turn(
        self,
        *,
        transcript: str,
        ctx: Context,
        session_id: UUID,
        exiting: bool,
    ) -> None:
        """Handle ONE turn while the session is in conversational chat mode.

        This entirely REPLACES the command-mode router + response pipeline for
        a conversational turn. Two shapes:

          * ``exiting=True`` — the transcript was an exit phrase
            ("that's all", "stop", "never mind"). Clear ``conversational_mode``
            (+ the bound ``letta_agent_id`` + any stale ``chat_start_pending``)
            from the session context, send a ``chat_end`` frame so the Pi drops
            back to its wake loop, and speak a short goodbye.
          * otherwise — dispatch the utterance to the Letta agent: resolve (or
            lazily create + bind) the household agent, stream its ASSISTANT
            text deltas, and pipe them through the SAME per-sentence TTS path a
            normal response uses (response_start → PCM → response_end). Letta's
            reasoning/tool-call chunks are filtered out by the client so Domovoi
            never speaks its chain of thought.

        Either shape writes exactly ONE ``intents_log`` + ONE
        ``conversation_log`` row with ``matched_path="chat"`` (the audit
        invariant — every routed turn logs one of each), bypassing the router's
        ``_persist_turn`` since the router was bypassed.

        When chat mode is disabled or stubs are forced, ``get_letta_client``
        returns the deterministic stub, so this path still produces speakable
        replies in tests and on a deployment that hasn't brought Letta up.
        """
        t0 = time.monotonic()
        # Any conversational turn (including the exit) resets the abandoned-chat
        # clock so the silence watchdog only fires on a genuinely idle open mic.
        self.conversational_mode = True
        self._chat_last_activity = asyncio.get_running_loop().time()
        if exiting:
            spoken = "Okay, talk to you later."
            await self._clear_chat_mode()
            await self.send_chat_end(reason="user_exit")
            try:
                await self._speak_chat_text(spoken, session_id, expect_followup=False)
            except asyncio.CancelledError:
                # User barged over the goodbye — fine, the chat is already
                # cleared; we still log the turn below.
                pass
            except Exception as e:
                log.warning("chat: goodbye TTS failed: %s", e)
            await self._persist_chat_turn(
                transcript=transcript,
                assistant_text=spoken,
                ctx=ctx,
                session_id=session_id,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return

        # ── Letta dispatch ──────────────────────────────────────────────
        client = get_letta_client()
        # One agent per household (the deployment is single-household), bound
        # to this session so subsequent turns reuse it. agent_key is stable so
        # ensure_agent resolves the same agent across sessions/restarts.
        agent_key = settings.bot_name.lower() or "domovoi"
        try:
            agent_id = await client.ensure_agent(agent_key=agent_key)
            # Bind the resolved agent onto the session context for visibility /
            # future reuse (the key is the same per household today, but a
            # per-session binding keeps the door open for per-room agents).
            async with session_scope() as s:
                await SessionRepository(s).set_context_key(
                    session_id, "letta_agent_id", agent_id
                )
        except Exception as e:
            log.warning("chat: ensure_agent failed: %s", e)
            # A Letta that can't even resolve an agent won't stream a reply, so
            # don't trap the user in a dead open mic — drop them back to command
            # mode (clear the flag + send chat_end) after the apology.
            spoken = "Sorry, I'm having trouble with chat mode right now."
            try:
                await self._speak_chat_text(spoken, session_id, expect_followup=False)
            except asyncio.CancelledError:
                pass
            await self._clear_chat_mode()
            await self.send_chat_end(reason="letta_unavailable")
            await self._persist_chat_turn(
                transcript=transcript,
                assistant_text=spoken,
                ctx=ctx,
                session_id=session_id,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return

        # Stream Letta's assistant deltas → per-sentence TTS. We buffer deltas
        # into whole sentences before synth (TTS wants sentences, not tokens)
        # while keeping first-speech latency low: flush as soon as a sentence
        # terminator lands. The whole reply is also accumulated for the
        # conversation_log audit row.
        # `_stream_chat_reply` owns the entire response lifecycle
        # (response_start → PCM → response_end, with the correct interrupted
        # flag) AND the error frame, so we don't re-send a terminal frame here.
        # We swallow both barge-in (CancelledError) and any stream failure AND
        # STILL record the (possibly cut-short) turn below, so the audit row
        # always lands — matching the command path's "log even on interrupt".
        full_text = ""
        try:
            full_text = await self._stream_chat_reply(
                client, agent_id=agent_id, user_text=transcript,
                session_id=session_id,
            )
        except asyncio.CancelledError:
            log.debug("chat: turn barged-in on room=%s", self.room_id)
        except Exception:
            log.exception("chat: Letta stream failed for room=%s", self.room_id)

        # Audit: exactly one intents_log + conversation_log row, matched_path
        # "chat", regardless of interruption (we still consumed a turn).
        await self._persist_chat_turn(
            transcript=transcript,
            assistant_text=full_text,
            ctx=ctx,
            session_id=session_id,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def _stream_chat_reply(
        self,
        client: Any,
        *,
        agent_id: str,
        user_text: str,
        session_id: UUID,
    ) -> str:
        """Stream a Letta reply through the per-sentence TTS pipeline and
        return the full assistant text.

        Sends ONE response_start (with the first sentence's sample rate), then
        a PCM stream per sentence, then ONE response_end. Mirrors the normal
        response path's framing so the Pi handles a chat reply exactly like any
        other turn. ``expect_followup=True`` on the response_end keeps the Pi's
        chat open mic live for the next turn (it's already open from chat_start,
        but the flag is harmless and keeps non-relay clients consistent)."""
        tts = get_tts_client()
        synth_engine, synth_voice = await resolve_voice(
            self.ws.app.state.satellite_voice.get(self.room_id)
        )

        async def _synth(sentence: str) -> tuple[bytes, int]:
            return _wav_to_pcm(
                await tts.synthesize(sentence, engine=synth_engine, voice=synth_voice)
            )

        full_parts: list[str] = []
        pending = ""           # delta buffer not yet a full sentence
        started = False        # have we emitted response_start yet?
        interrupted = False     # barge-in mid-stream → response_end interrupted
        try:
            async for delta in client.chat_stream(
                agent_id=agent_id, user_text=user_text
            ):
                if not delta:
                    continue
                full_parts.append(delta)
                pending += delta
                # Flush every COMPLETE sentence as it forms so first-speech is
                # quick and the Pi never starves mid-reply. _split_sentences
                # only breaks AFTER a terminator + whitespace, so the last
                # element is the still-incomplete tail (no terminator seen yet,
                # or no trailing space) — keep it buffered, synth the rest.
                sentences = _split_sentences(pending)
                if len(sentences) > 1:
                    complete, pending = sentences[:-1], sentences[-1]
                    for sentence in complete:
                        sentence = sentence.strip()
                        if not sentence:
                            continue
                        pcm, sr = await _synth(sentence)
                        if not started:
                            await self._send_chat_response_start(sentence, sr, session_id)
                            started = True
                        for chunk in _iter_chunks(pcm):
                            await self.ws.send_bytes(chunk)
            # Flush whatever's left in the buffer (the final, unterminated
            # sentence — Letta often ends without trailing punctuation).
            tail = pending.strip()
            if tail:
                pcm, sr = await _synth(tail)
                if not started:
                    await self._send_chat_response_start(tail, sr, session_id)
                    started = True
                for chunk in _iter_chunks(pcm):
                    await self.ws.send_bytes(chunk)
        except asyncio.CancelledError:
            # Barge-in: the user cut us off. Mark interrupted so the
            # response_end below is truthful, then re-raise so the response
            # task unwinds and `_run_chat_turn` records the turn as cut short.
            interrupted = True
            raise
        except Exception:
            # Letta / TTS / WS failure mid-stream. Own the terminal frame here
            # (the finally below sends it) so `_run_chat_turn` doesn't
            # double-send a second response_end. Send an error frame for
            # diagnostics, then re-raise so the caller logs + persists.
            interrupted = True
            await self._safe_send_text({
                "type": "error",
                "message": "chat reply failed",
            })
            raise
        finally:
            # Always terminate the response so the Pi unblocks its playback /
            # capture loop, even if Letta yielded nothing or we errored
            # mid-stream. If we never emitted response_start (empty reply),
            # emit a minimal one so the Pi sees a well-formed lifecycle. Keep
            # the chat mic open on a clean turn (expect_followup=True); a
            # barge-in / error sets interrupted so the Pi knows the reply was
            # cut short (it stays in chat mode either way — the server clears
            # chat mode only on an explicit exit phrase).
            if not started:
                await self._send_chat_response_start("", PCM_INPUT_SAMPLE_RATE, session_id)
            await self._safe_send_text({
                "type": "response_end",
                "interrupted": interrupted,
                "expect_followup": not interrupted,
            })
        return "".join(full_parts).strip()

    async def _send_chat_response_start(
        self, text: str, sr: int, session_id: UUID
    ) -> None:
        await self.ws.send_text(json.dumps({
            "type": "response_start",
            "text": text,
            "matched_handler": "chat_mode",
            "matched_path": "chat",
            "session_id": str(session_id) if session_id else None,
            "online": True,
            "audio_sample_rate": sr,
        }))

    async def _speak_chat_text(
        self, text: str, session_id: UUID, *, expect_followup: bool
    ) -> None:
        """Synthesize a single non-streamed chat utterance (the exit goodbye or
        an error apology) through the normal response framing."""
        tts = get_tts_client()
        synth_engine, synth_voice = await resolve_voice(
            self.ws.app.state.satellite_voice.get(self.room_id)
        )
        sentences = _split_sentences(text) or [text]
        first_pcm, sr = _wav_to_pcm(
            await tts.synthesize(sentences[0], engine=synth_engine, voice=synth_voice)
        )
        await self._send_chat_response_start(text, sr, session_id)
        try:
            for chunk in _iter_chunks(first_pcm):
                await self.ws.send_bytes(chunk)
            for sentence in sentences[1:]:
                pcm, _sr = _wav_to_pcm(
                    await tts.synthesize(sentence, engine=synth_engine, voice=synth_voice)
                )
                for chunk in _iter_chunks(pcm):
                    await self.ws.send_bytes(chunk)
        finally:
            await self._safe_send_text({
                "type": "response_end",
                "interrupted": False,
                "expect_followup": expect_followup,
            })

    async def _persist_chat_turn(
        self,
        *,
        transcript: str,
        assistant_text: str,
        ctx: Context,
        session_id: UUID,
        latency_ms: int,
    ) -> None:
        """Write the one intents_log + conversation_log row a conversational
        turn owes the audit trail (matched_path="chat"), plus thread the turn
        into recent_turns. The router's ``_persist_turn`` would normally do
        this, but the router is bypassed for chat turns — so we mirror it here
        (both matched_path CHECKs allow "chat").
        Wrapped broadly so an audit-write hiccup never crashes the turn."""
        from domovoi.db.repositories import (
            ConversationLogRepository,
            IntentLogRepository,
        )

        try:
            async with session_scope() as s:
                await IntentLogRepository(s).log(
                    room_id=ctx.room_id,
                    transcript=transcript,
                    matched_handler="chat_mode",
                    matched_path="chat",
                    online=ctx.online,
                    latency_ms=latency_ms,
                    person_id=ctx.person_id,
                    presence_tier=ctx.presence_tier,
                )
                await ConversationLogRepository(s).record_turn(
                    session_id=session_id,
                    room_id=ctx.room_id,
                    person_id=ctx.person_id,
                    user_text=transcript,
                    assistant_text=assistant_text,
                    matched_handler="chat_mode",
                    matched_path="chat",
                    presence_tier=ctx.presence_tier,
                    online=ctx.online,
                    latency_ms=latency_ms,
                )
                await SessionRepository(s).record_exchange(
                    session_id,
                    transcript,
                    assistant_text,
                    settings.session_recent_turns_cap,
                )
        except Exception as e:
            log.warning("chat: persisting chat turn failed: %s", e)

    async def send_chat_start(self) -> None:
        """Tell this satellite to enter conversational chat open-mic mode
        (Feature 8). Mirrors ``request_restart``'s raw send — raises on a dead
        socket so the caller can react (here ``_maybe_send_chat_start`` lets it
        propagate into the response task's logging). The Pi opens its mic and
        loops STT→reply turns without re-waking until a ``chat_end`` arrives."""
        await self.ws.send_text(json.dumps({"type": "chat_start"}))

    async def send_chat_end(self, *, reason: str = "ended") -> None:
        """Tell this satellite to leave chat mode and restore its wake loop.
        Safe-send (mirrors ``notify_sounds_changed``): an end frame that fails
        to deliver is recoverable — the Pi also drops chat mode on any
        wake-loop resync, and a dead socket is about to be cleaned up anyway."""
        await self._safe_send_text({"type": "chat_end", "reason": reason})

    async def announce(self, text: str) -> None:
        """Inject a TTS-synthesized announcement into this Pi's stream.

        Reuses the normal response wire format (response_start →
        PCM chunks → response_end) so the satellite handles it as if it
        were a regular reply — no protocol changes needed Pi-side. Bypasses
        the request/response state machine so it can fire while no
        utterance is in flight.

        Skipped (silently — raises a SkipAnnounce so the caller can
        distinguish "I couldn't" from "I did") if the target Pi is
        mid-response: queueing audio on top of an in-flight TTS would
        clip the original.

        After the announcement plays, if the room had music recorded for
        auto-resume, we emit a music_start so the Pi respawns mpg123 —
        otherwise the announcement permanently kills the music in that
        room (same failure mode as the wake-word music-resume case, just triggered by
        intercom instead of wake-word capture).

        **Failure-visibility contract**: raises on any send failure
        rather than silently returning. The 2026-05-10 bug was that
        `_safe_send_text` + try/except-return on `send_bytes` made a
        dead WS (which happens when the Pi's wifi flaps) look
        indistinguishable from a successful announce — the admin API
        reported success, but no audio reached the speaker. Callers
        catch the exception and exclude this room from their
        ``announced_to`` list. The dead session is also removed from
        ``active_sessions`` so the next broadcast doesn't try again.
        """
        if self._response_task is not None and not self._response_task.done():
            log.warning(
                "intercom: room=%s mid-response, skipping announce", self.room_id
            )
            raise RuntimeError(f"room {self.room_id} mid-response, announce skipped")

        tts = get_tts_client()
        sentences = _split_sentences(text) or [text]
        # Announce in this room's own voice, like its normal responses.
        a_engine, a_voice = await resolve_voice(
            self.ws.app.state.satellite_voice.get(self.room_id)
        )
        try:
            first_pcm, sr = _wav_to_pcm(
                await tts.synthesize(sentences[0], engine=a_engine, voice=a_voice)
            )
        except Exception as e:
            log.warning("intercom: TTS synth failed for room=%s: %s", self.room_id, e)
            raise

        # Raw `self.ws.send_*` (not `_safe_send_text`) so any
        # ConnectionClosed / send error propagates to the caller. The
        # `_safe_send_text` helper exists for the normal response path
        # where swallowing makes sense (the response task is already
        # cleaning up); broadcasts don't have that luxury.
        try:
            await self.ws.send_text(json.dumps({
                "type": "response_start",
                "text": text,
                "matched_handler": "intercom",
                "matched_path": "intercom_broadcast",
                "session_id": None,
                "online": True,
                "audio_sample_rate": sr,
            }))
            for chunk in _iter_chunks(first_pcm):
                await self.ws.send_bytes(chunk)
            for sentence in sentences[1:]:
                try:
                    pcm, _sr = _wav_to_pcm(
                        await tts.synthesize(sentence, engine=a_engine, voice=a_voice)
                    )
                except Exception as e:
                    log.warning(
                        "intercom: TTS synth failed mid-stream for room=%s "
                        "sentence=%r: %s",
                        self.room_id, sentence[:60], e,
                    )
                    # First sentence already streamed — let the
                    # already-delivered audio land and stop here.
                    break
                for chunk in _iter_chunks(pcm):
                    await self.ws.send_bytes(chunk)
            await self.ws.send_text(json.dumps({
                "type": "response_end",
                "interrupted": False,
                "expect_followup": False,
            }))
        except Exception as e:
            log.warning(
                "intercom: WS send failed for room=%s (likely dead "
                "connection); evicting from active_sessions and "
                "re-raising for caller. cause=%s",
                self.room_id, e,
            )
            # Evict the dead session so subsequent broadcasts don't
            # try to send to a corpse. The receiver loop's finally
            # block also evicts, but if the loop is stuck (e.g.,
            # awaiting on an already-dead recv()), we may beat it.
            sessions: dict[str, "StreamSession"] = self.ws.app.state.active_sessions
            if sessions.get(self.room_id) is self:
                sessions.pop(self.room_id, None)
            raise

        # Resume music if this room had something playing.
        resumable: dict[str, str] = self.ws.app.state.resumable_music
        if self.room_id in resumable:
            await self._safe_send_text({
                "type": "music_start",
                "stream_url": resumable[self.room_id],
            })
            await schedule_music_resume_fallback(
                self.ws.app, self.room_id, resumable[self.room_id],
            )

    async def _safe_send_text(self, payload: dict[str, Any]) -> None:
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            # Connection may be gone; nothing useful to do.
            pass

    async def notify_sounds_changed(self) -> None:
        """Tell this satellite its sound clips changed (greetings edited +
        re-rendered) so it re-syncs them from the core without
        waiting for a reconnect."""
        await self._safe_send_text({"type": "sounds_changed"})

    # ── Wake-word clip recording / model push (Feature 5) ─────────────────

    async def _save_wake_clip(self, pcm: bytes) -> None:
        """Persist one captured positive clip for the in-progress wake-word
        recording and bump its ``clip_count``.

        Writes ``<wake_clips_dir>/<slug>/clip_NNN.wav`` (16 kHz mono int16,
        the trainer's expected positive-set format) and notifies the
        dashboard's realtime channel so the live clip-count ticks up. Never
        runs STT / route — the caller (the utterance_end branch) already
        decided this audio is training data, not a command. Best-effort: a
        write or DB error is logged and swallowed so a single bad clip
        doesn't kill the recording session."""
        state = self.wake_recording
        if state is None:
            return
        if len(pcm) < _MIN_WAKE_CLIP_BYTES:
            # A fluke empty/near-empty utterance — don't write a junk positive
            # or bump the count; the Pi has its own per-clip guard too.
            log.info(
                "wake recording: dropped a near-empty clip (%d bytes) for slug=%s",
                len(pcm), state.slug,
            )
            return
        n = state.clips_written + 1
        clip_path = Path(settings.wake_clips_dir) / state.slug / f"clip_{n:03d}.wav"
        try:
            await asyncio.to_thread(_write_wav_clip, clip_path, pcm)
        except OSError as e:
            log.warning(
                "wake recording: writing clip for slug=%s failed: %s",
                state.slug, e,
            )
            return
        state.clips_written = n
        # Score quality + write an auto-trimmed (end-aligned) audit copy for the
        # just-recorded clip, so the dashboard shows its acoustics the moment it
        # lands. Best-effort — an analysis hiccup must never drop a good clip or
        # break the count; the web list backfills any clip missing a sidecar.
        try:
            from domovoi.wake_clip_quality import ensure_analysis
            await asyncio.to_thread(ensure_analysis, clip_path)
        except Exception as e:
            log.warning(
                "wake recording: analysis failed for %s: %s", clip_path.name, e
            )
        try:
            from domovoi.db.repositories import WakeWordsRepository

            async with session_scope() as s:
                await WakeWordsRepository(s).bump_clip_count(state.wake_word_id)
                # Tell the dashboard's LISTEN task the clip count moved (the
                # Wake Words tab shows a live clip count vs the minimum). Same
                # session as the bump so the post-COMMIT delivery is consistent.
                await s.execute(
                    text("SELECT pg_notify('wake_words_changed', :p)"),
                    {"p": str(state.wake_word_id)},
                )
        except Exception as e:
            log.warning(
                "wake recording: bump_clip_count for id=%s failed: %s",
                state.wake_word_id, e,
            )
        log.info(
            "wake recording: saved %s (%d/%d) for slug=%s room=%s",
            clip_path.name, n, state.target_count, state.slug, self.room_id,
        )

    async def start_wake_recording(
        self, *, wake_word_id: int, slug: str, clip_seconds: float, target_count: int
    ) -> None:
        """Begin collecting positive training clips from this satellite
        (dashboard "Record on <room>"). Arms the server-side recording state
        and tells the Pi to enter its clip-capture loop. Raw send so a dead
        socket propagates to the admin caller."""
        self.wake_recording = WakeRecordingState(
            wake_word_id=wake_word_id,
            slug=slug,
            clip_seconds=clip_seconds,
            target_count=target_count,
        )
        await self.ws.send_text(
            json.dumps(
                {
                    "type": "start_wake_recording",
                    "wake_word_id": wake_word_id,
                    "slug": slug,
                    "clip_seconds": clip_seconds,
                    "target_count": target_count,
                }
            )
        )

    async def stop_wake_recording(self) -> None:
        """Stop an in-progress wake-word recording and tell the Pi to leave
        its clip-capture loop (and resume the normal wake loop). Idempotent —
        clears the state whether or not one was active. Raw send so a dead
        socket propagates to the admin caller."""
        self.wake_recording = None
        await self.ws.send_text(json.dumps({"type": "stop_wake_recording"}))

    async def request_set_wake_word(self, *, slug: str, threshold: float | None = None) -> None:
        """Push a trained wake model to this satellite: the Pi writes ``slug``
        to its wake sidecar, syncs the model from /v1/wake-models, and
        self-restarts to load it. ``slug`` is the load-bearing identifier —
        the served ``<slug>.onnx`` stem, the Pi's effective wake word, and the
        openWakeWord prediction-dict key all equal it. ``threshold`` carries the
        registry's per-word detection threshold so the Pi applies it instead of
        its local config default (None → the Pi keeps its config threshold).
        Raw send so a dead socket propagates to the admin caller."""
        frame: dict[str, Any] = {"type": "set_wake_word", "slug": slug}
        if threshold is not None:
            frame["threshold"] = threshold
        await self.ws.send_text(json.dumps(frame))

    async def notify_wake_models_changed(self) -> None:
        """Tell this satellite the served wake models changed so it re-syncs
        its ~/.domovoi/wake_models cache from /v1/wake-models without waiting
        for a reconnect. Safe-send — a transient miss is recoverable on the
        next sync."""
        await self._safe_send_text({"type": "wake_models_changed"})

    async def request_restart(self) -> None:
        """Ask this satellite to restart its own service to apply a config
        change that needs a fresh process. The Pi drains TTS playback then
        runs a sudo'ed `systemctl --no-block restart` (see the self-restart
        sudoers entry in satellite/PROVISIONING.md). Raises on a dead
        socket so the caller can report the failure."""
        await self.ws.send_text(json.dumps({"type": "restart"}))

    async def set_output_volume(self, level: int) -> None:
        """Set this satellite's master output volume (0-100). Sends the same
        ``set_volume`` frame MusicHandler's voice path uses; the Pi drives its
        hardware mixer (scaling BOTH TTS and music) and re-reports via
        ``volume_status``. We also optimistically update the cached level so
        the dashboard reflects the change immediately, before the Pi's echo
        arrives. Raises on a dead socket so the admin endpoint can report it."""
        clamped = max(0, min(100, int(level)))
        await self.ws.send_text(json.dumps({"type": "set_volume", "level": clamped}))
        self.ws.app.state.satellite_volume[self.room_id] = clamped

    async def set_display(self, action: str) -> None:
        """Drive a video satellite's screen: "on" / "off" switch the panel's
        power, "restart_kiosk" bounces the kiosk browser service. The Pi
        applies and re-reports via ``display_status``; for on/off we also
        optimistically update the cached state so the dashboard toggle
        reflects immediately. Raises on a dead socket so the admin endpoint
        can report it. The admin endpoint refuses non-video rooms before
        this is ever reached."""
        await self.ws.send_text(
            json.dumps({"type": "set_display", "action": action})
        )
        if action in ("on", "off"):
            cached = dict(
                self.ws.app.state.satellite_display.get(self.room_id) or {}
            )
            cached["on"] = action == "on"
            self.ws.app.state.satellite_display[self.room_id] = cached

    async def request_upgrade(
        self, *, expected_sha: str, reconnect_timeout: int
    ) -> None:
        """Ask this satellite to sync its code from the core and
        self-restart. Carries only PATHS (not absolute URLs) — the Pi derives
        the HTTP base from its own live WS URL, exactly as the sound sync does.
        `expected_sha` is a version label the Pi records on success;
        integrity is the per-file manifest sha256, not this SHA. Raises on a
        dead socket so the admin endpoint reports the failure."""
        await self.ws.send_text(
            json.dumps(
                {
                    "type": "upgrade",
                    "expected_sha": expected_sha,
                    "manifest_path": "/v1/satellite-code/manifest",
                    "files_base": "/v1/satellite-code",
                    "reconnect_timeout_sec": reconnect_timeout,
                }
            )
        )

    async def send_config(self, changes: dict[str, Any]) -> None:
        """Push web-edited config ({"section.key": value}) to this
        satellite. The Pi merges it into config.toml (preserving comments),
        validates it parses + backs up the old file, then self-restarts to
        apply. Raises on a dead socket so the caller can report it."""
        await self.ws.send_text(
            json.dumps({"type": "set_config", "changes": changes})
        )

    # ── Two-way drop-in (Feature 4) ──────────────────────────────────────

    async def _begin_dropin(self, peer: "StreamSession") -> None:
        """Pair this session (initiator) with ``peer`` (target) for a live
        call. Sets the bidirectional ``dropin_peer`` link + the
        ``app.state.active_dropins`` map under ``dropin_lock``, records the
        ``dropin_calls`` audit row, suppresses music on both Pis (the single
        ALSA card can't carry music + call audio at once), sends each a
        ``dropin_start`` frame (forcing the 16 kHz inbound rate so relay
        audio isn't chipmunked), and arms the silence-timeout watchdog.

        Refused under the lock if either side is already paired, so two
        simultaneous initiations can't leave a half-open call.
        """
        app = self.ws.app
        async with app.state.dropin_lock:
            if self.dropin_peer is not None or peer.dropin_peer is not None:
                log.warning(
                    "drop-in: begin refused — %s or %s already in a call",
                    self.room_id, peer.room_id,
                )
                return
            self.dropin_peer = peer
            peer.dropin_peer = self
            # active_dropins is keyed by room_id (both directions, so a
            # membership check works for either participant). The value
            # carries enough for the web snapshot to render one row per
            # call: peer, who initiated, and when.
            active = app.state.active_dropins
            started_at = datetime.now(timezone.utc).isoformat()
            active[self.room_id] = {
                "peer": peer.room_id, "initiator": True, "started_at": started_at,
            }
            active[peer.room_id] = {
                "peer": self.room_id, "initiator": False, "started_at": started_at,
            }
            now = asyncio.get_running_loop().time()
            self._dropin_last_audio = now
            peer._dropin_last_audio = now

        # Audit row — best-effort; a logging failure must never break a call.
        call_id: int | None = None
        try:
            from domovoi.db.repositories import DropInCallsRepository
            async with session_scope() as s:
                call_id = await DropInCallsRepository(s).start(
                    self.room_id, peer.room_id
                )
        except Exception as e:
            log.warning("drop-in: failed to record start row: %s", e)
        self.dropin_call_id = call_id
        peer.dropin_call_id = call_id

        # Free the single ALSA card on both Pis for the call audio.
        await self._suppress_music_for(self)
        await self._suppress_music_for(peer)

        # Enter open-mic on both ends. full_duplex is true for both by
        # construction (feasibility gated on AEC); pass it through so the
        # Pi can assert/relax its own half-duplex guard.
        fd = app.state.satellite_full_duplex
        started_ok = True
        for near, far in ((self, peer), (peer, self)):
            frame = {
                "type": "dropin_start",
                "peer_room": far.room_id,
                "peer_label": far.room_id.replace("_", " "),
                "audio_sample_rate": PCM_INPUT_SAMPLE_RATE,
                "full_duplex": bool(
                    fd.get(near.room_id, False) and fd.get(far.room_id, False)
                ),
            }
            try:
                await near.ws.send_text(json.dumps(frame))
            except Exception as e:
                log.warning("drop-in: dropin_start to %s failed: %s", near.room_id, e)
                started_ok = False
        if not started_ok:
            # A leg never got the open-mic frame — don't leave it half-open.
            # Tearing down also restores the suppressed music on both sides.
            await self._end_dropin(ended_by="system", status="failed")
            return

        # Soft "connected" chime to both ends. Both Pis are in open-mic now,
        # so this plays straight through the relay/playback path (16 kHz).
        from domovoi.dropin_chimes import START_CHIME_PCM
        for sess in (self, peer):
            try:
                await sess.ws.send_bytes(START_CHIME_PCM)
            except Exception as e:
                log.debug("drop-in: start chime to %s failed: %s", sess.room_id, e)

        log.info(
            "drop-in: %s ↔ %s connected (call_id=%s)",
            self.room_id, peer.room_id, call_id,
        )

        # Silence-timeout watchdog: one per call, owned by the initiator.
        timeout = float(getattr(settings, "dropin_silence_timeout_sec", 0) or 0)
        if timeout > 0:
            self._dropin_silence_task = asyncio.create_task(
                self._dropin_silence_watchdog(timeout),
                name=f"dropin-silence-{self.room_id}",
            )

    async def _end_dropin(self, *, ended_by: str, status: str = "ended") -> None:
        """Tear down the call this session is in. Safe to call from either
        side, on disconnect, from the relay-failure path, the admin endpoint,
        or the silence watchdog — and more than once (idempotent under
        ``dropin_lock``: a second call sees ``dropin_peer is None`` and
        returns)."""
        app = self.ws.app
        async with app.state.dropin_lock:
            peer = self.dropin_peer
            if peer is None:
                return  # already ended, or never started
            call_id = self.dropin_call_id
            # Capture the watchdog from whichever side owns it before we
            # null state, so it can be cancelled outside the lock.
            sil = self._dropin_silence_task or peer._dropin_silence_task
            # Clear both sides first so any concurrent relay / watchdog /
            # disconnect observes "not in a call" and bails immediately.
            self.dropin_peer = None
            peer.dropin_peer = None
            self.dropin_call_id = None
            peer.dropin_call_id = None
            self._dropin_silence_task = None
            peer._dropin_silence_task = None
            active = app.state.active_dropins
            active.pop(self.room_id, None)
            active.pop(peer.room_id, None)

        # Outside the lock: the watchdog may itself be ending the call, so
        # don't cancel ourselves.
        if sil is not None and not sil.done() and sil is not asyncio.current_task():
            sil.cancel()

        # Soft "disconnected" chime BEFORE the end frame. The Pi doesn't
        # force-drain on dropin_end, so the queued chime plays out as the
        # call closes — no server-side sleep needed.
        from domovoi.dropin_chimes import END_CHIME_PCM
        for sess in (self, peer):
            try:
                await sess.ws.send_bytes(END_CHIME_PCM)
            except Exception as e:
                log.debug("drop-in: end chime to %s failed: %s", sess.room_id, e)

        for sess in (self, peer):
            try:
                await sess.ws.send_text(
                    json.dumps({"type": "dropin_end", "reason": status})
                )
            except Exception as e:
                log.debug(
                    "drop-in: dropin_end to %s failed (likely gone): %s",
                    sess.room_id, e,
                )

        # Restore any music suppressed at call start.
        await self._restore_music_for(self)
        await self._restore_music_for(peer)

        if call_id is not None:
            try:
                from domovoi.db.repositories import DropInCallsRepository
                async with session_scope() as s:
                    await DropInCallsRepository(s).finish(
                        call_id, status=status, ended_by=ended_by
                    )
            except Exception as e:
                log.warning("drop-in: failed to finish call row %s: %s", call_id, e)

        log.info(
            "drop-in: %s ↔ %s ended (by=%s, status=%s)",
            self.room_id, peer.room_id, ended_by, status,
        )

    async def _dropin_silence_watchdog(self, timeout: float) -> None:
        """Auto-end a call after ``timeout`` seconds with no relayed audio
        from EITHER side. ``_dropin_last_audio`` is bumped on every relayed
        frame (both directions), so any speech resets the clock."""
        try:
            interval = max(1.0, min(timeout, 3.0))
            while True:
                await asyncio.sleep(interval)
                peer = self.dropin_peer
                if peer is None:
                    return  # call already ended
                now = asyncio.get_running_loop().time()
                last = max(self._dropin_last_audio, peer._dropin_last_audio)
                if now - last >= timeout:
                    log.info(
                        "drop-in: %s ↔ %s silent for %.0fs; auto-ending",
                        self.room_id, peer.room_id, timeout,
                    )
                    await self._end_dropin(ended_by="timeout", status="timed_out")
                    return
        except asyncio.CancelledError:
            return

    async def _suppress_music_for(self, target: "StreamSession") -> None:
        """Stop the target Pi's music playback for the duration of a call
        WITHOUT clearing ``resumable_music`` — so ``_restore_music_for`` can
        bring it back when the call ends. Best-effort."""
        resumable: dict[str, str] = self.ws.app.state.resumable_music
        if target.room_id in resumable:
            await target._safe_send_text({"type": "music_stop"})
            # Cancel any pending music_ready handshake so a late resume
            # doesn't fight the call audio.
            pending = self.ws.app.state.pending_music_start
            stale = pending.pop(target.room_id, None)
            if stale is not None:
                stale_task = stale.get("task")
                if stale_task is not None and not stale_task.done():
                    stale_task.cancel()

    async def _restore_music_for(self, target: "StreamSession") -> None:
        """Resume music on the target Pi if it had something playing before
        the call (mirrors announce()'s music-resume tail). Best-effort."""
        resumable: dict[str, str] = self.ws.app.state.resumable_music
        url = resumable.get(target.room_id)
        if url:
            await target._safe_send_text(
                {"type": "music_start", "stream_url": url}
            )
            await schedule_music_resume_fallback(self.ws.app, target.room_id, url)

    async def prompt_dropin(self, text: str) -> None:
        """Confirm-mode invite to the target. Like ``announce()`` but (a)
        sends ``response_end`` with ``expect_followup=True`` so the target
        can answer "yeah"/"no" without a wake word, and (b) skips the
        music-resume tail (music must stay suppressed while they decide).
        Raises on a dead socket or a mid-response target so the caller can
        treat the target as busy."""
        if self._response_task is not None and not self._response_task.done():
            raise RuntimeError(
                f"room {self.room_id} mid-response, dropin prompt skipped"
            )
        tts = get_tts_client()
        sentences = _split_sentences(text) or [text]
        p_engine, p_voice = await resolve_voice(
            self.ws.app.state.satellite_voice.get(self.room_id)
        )
        first_pcm, sr = _wav_to_pcm(
            await tts.synthesize(sentences[0], engine=p_engine, voice=p_voice)
        )
        await self.ws.send_text(json.dumps({
            "type": "response_start",
            "text": text,
            "matched_handler": "dropin",
            "matched_path": "dropin_invite",
            "session_id": str(self.session_id) if self.session_id else None,
            "online": True,
            "audio_sample_rate": sr,
        }))
        for chunk in _iter_chunks(first_pcm):
            await self.ws.send_bytes(chunk)
        for sentence in sentences[1:]:
            pcm, _sr = _wav_to_pcm(
                await tts.synthesize(sentence, engine=p_engine, voice=p_voice)
            )
            for chunk in _iter_chunks(pcm):
                await self.ws.send_bytes(chunk)
        await self.ws.send_text(json.dumps({
            "type": "response_end",
            "interrupted": False,
            "expect_followup": True,
        }))

    async def _prompt_target_for_dropin(self, target: "StreamSession") -> None:
        """Confirm-mode: park a ``pending_confirmation`` in the target's
        session so its next "yeah" routes to ``DropInHandler.handle_confirmation``,
        then prompt it with a no-wake-word followup. The auto path skips all
        this (which is why auto is the default)."""
        from domovoi.confirmations import request_confirmation
        from domovoi.db.repositories import SessionRepository

        peer_label = self.room_id.replace("_", " ")
        try:
            async with session_scope() as s:
                repo = SessionRepository(s)
                target_session_id = await repo.get_or_create(
                    target.session_id, target.room_id
                )
                await request_confirmation(
                    s,
                    target_session_id,
                    kind="core.dropin_invite",
                    handler="dropin",
                    data={
                        "initiator_room": self.room_id,
                        "peer_label": peer_label,
                    },
                )
            # Keep the target's in-memory session_id aligned so its followup
            # turn reuses the sessions row the pending lives in.
            target.session_id = target_session_id
        except Exception as e:
            log.warning(
                "drop-in: failed to park confirmation for %s: %s",
                target.room_id, e,
            )
            return

        # Free the card so the prompt is audible; a decline auto-resumes
        # music on that turn, an accept keeps it suppressed for the call.
        await self._suppress_music_for(target)
        try:
            await target.prompt_dropin(
                f"The {peer_label} wants to drop in. Is that okay?"
            )
        except Exception as e:
            log.warning(
                "drop-in: prompt to %s failed (busy?): %s", target.room_id, e
            )
            # Clear the parked confirmation so a later stray "yes" can't open
            # a call nobody is waiting on, and restore the target's music.
            try:
                async with session_scope() as s:
                    await SessionRepository(s).set_context_key(
                        target.session_id, "pending_confirmation", None
                    )
            except Exception:
                pass
            await self._restore_music_for(target)
