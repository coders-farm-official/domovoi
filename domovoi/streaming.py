"""WebSocket streaming endpoint for Pi satellites.

Wire protocol v0.1 — `/v1/stream/{room_id}`. Bidirectional, mixed text+binary.

Audio format on both directions: 16 kHz mono int16 little-endian PCM.
Server declares the actual TTS sample rate per response in the
`response_start` frame (Edge ≈ 24 kHz, Piper ≈ 22 kHz, system varies);
clients should resample if their playback device can't accept it directly.

Client → Server
  text  hello              {"type":"hello","room_id":...,"wake_word":...,"synced_sha":...,"supports_full_duplex":bool,
                            "sat_type":"voice"|"video","mic_enabled":bool}
                           — MUST be the first frame. The server creates
                           and sends NOTHING for the room (no MPD
                           provisioning, no active_sessions entry, no
                           `ready`) until a hello has passed the pairing
                           check. A socket that sends no hello within
                           `satellite_hello_timeout_sec` (default 5 s), or
                           sends any other frame first, is closed (1008)
                           with nothing left behind.
                           `supports_full_duplex` (optional) reports whether
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
                           `speech_pause` (optional, default false) says the
                           satellite reports its own pauses (`speech_pause` /
                           `speech_resume` below) and its last voiced frame in
                           `utterance_end` whenever `ready.features` lists
                           "speech_pause"; the server then uses those instead
                           of judging pauses from the audio itself.
                           `capture_control` (optional, default false) says the
                           satellite honours `end_capture` (below). Only then
                           does the server ever end a capture early — for any
                           other client the reply would play into a capture
                           that is still open.
                           `announce_after_session_end` (optional, default
                           false) says a `response_start` ends whatever
                           `stop_playback` was stopping, so an announcement
                           after a reconnect plays (satellite 06f486b). The
                           server logs a WARNING per connection without it:
                           it cannot hear that such a satellite played
                           silence and records the announcement as spoken.
  text  utterance_start    {"type":"utterance_start","trigger":"wake_word"|"barge_in"|"push_to_talk"|"followup"|"wake_clip",
                            "utt":N,"backlog_ms":N,"wake_ms":N}
                           — `utt` (optional): the satellite's own number for
                           this capture, increasing per connection. Echoed in
                           the capture's hints and its `utterance_end`, so a
                           message about an older capture is recognisably
                           stale.
                           — `backlog_ms` / `wake_ms` (optional, whole ms):
                           the capture clock. `backlog_ms` is mic audio the
                           satellite had captured and not yet read when it
                           opened the capture (how far behind the room it
                           starts); `wake_ms`, after a wake word only, the
                           wake word to this message. Recorded on the turn
                           (_CaptureClock, turn_timings.CAPTURE_TIMING_KEYS);
                           `speech_pause` / `speech_resume` / `utterance_end`
                           carry `sat_ms` (since this message, on the
                           satellite's monotonic clock) and `backlog_ms`.
                           — trigger "wake_clip" (Feature 5) marks a positive
                           wake-word TRAINING clip: the Pi is in dashboard-
                           initiated recording mode and the following PCM is a
                           ~2 s sample of the wake phrase, NOT a command. The
                           server writes it as a clip WAV and bumps the clip
                           count instead of transcribing/routing (it doesn't
                           special-case the trigger — it just buffers as usual
                           and the recording-mode branch in utterance_end
                           diverts the audio).
  text  utterance_end      {"type":"utterance_end","greeting_played":bool,
                            "greeting_clip":str,"ack_before_capture":true,"utt":N,
                            "frames":N,"last_voiced_frame":N|null,"exit_reason":...,
                            "voiced_frames":N,"trailing_silent_frames":N,
                            "silence_limit_frames":N,"sat_ms":N,"backlog_ms":N}
                           — `greeting_played` (optional) marks turns where
                           the Pi played a wake greeting, so the server
                           strips a greeting that bled past the AEC out of
                           the transcript, and drops a turn that is nothing
                           but the greeting. `greeting_clip` (optional) names
                           the clip that played (greet_<hash>.mp3) so only
                           that line is matched; absent, the whole greeting
                           bank is. `ack_before_capture` (optional, sent only
                           as true) says the wake acknowledgement — greeting
                           or chime — finished BEFORE this capture opened and
                           the mic audio under it was dropped: the strip
                           still runs, the greeting-only drop does not (such
                           a capture cannot be the greeting alone). Absent —
                           an older satellite, which played the greeting over
                           the capture — both run.
                           `frames` (the 30 ms frames this capture sent),
                           `last_voiced_frame` (0-based index of the last one
                           its detector called speech) and `exit_reason`
                           ("vad_silence_after_speech", "max_record_seconds",
                           "shutdown", "server_endpoint"; an older satellite
                           may send "no_speech_after_greeting")
                           are optional too:
                           with them the server knows exactly whether a
                           speculative transcript covers everything said (see
                           "Speculative transcription" below). The reason and
                           the frame counts (all frames, the voiced ones, the
                           silent run it ended on and the run that ends one)
                           are also kept, numbers only, in an opted-in room's
                           command recording (domovoi/command_captures.py). An
                           older core reads greeting_played alone. `sat_ms` /
                           `backlog_ms`: the capture clock (utterance_start).
  text  speech_pause       {"type":"speech_pause","utt":N,"frame":N,"last_voiced_frame":N,
                            "greeting_played":bool,"greeting_clip":str,
                            "ack_before_capture":true,"sat_ms":N,"backlog_ms":N}
                           — ONLY when `ready.features` lists "speech_pause".
                           The capture has just had 8 silent frames (240 ms;
                           the satellite's `[listen] speech_pause_ms`, 90-600)
                           after speech: `frame` is the frames sent so far,
                           `last_voiced_frame` the last voiced one. Once per
                           silence run. The server may start transcribing.
                           `greeting_played` / `greeting_clip` /
                           `ack_before_capture` as in `utterance_end`, for
                           the early-commit check. `sat_ms` / `backlog_ms`:
                           the capture clock (utterance_start).
  text  speech_resume      {"type":"speech_resume","utt":N,"frame":N,"sat_ms":N,"backlog_ms":N}
                           — ONLY when `ready.features` lists "speech_pause".
                           Speech came back after a `speech_pause`. Sent
                           BEFORE the audio of the frame that resumed it
                           (`frame` = frames sent before it), so every frame
                           that arrives while a pause stands is one the
                           satellite called silence — the early-commit hold
                           counts on that.
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
  text  logs_chunk         {"type":"logs_chunk","request_id":str,"seq":int,
                            "data":str,"final":bool,"stats":{...}?}
                           - one slice of the log tail requested by `get_logs`,
                           chunked because a 10 MB frame is a bad bet at both
                           ends. `seq` starts at 0 and must arrive in order;
                           `stats` rides the frame marked `final`.
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
  text  music_failed       {"type":"music_failed","stream_url":...,
                            "reason":"<mpg123's last stderr line>",
                            "attempts":N}
                           — ONLY when `ready.features` lists
                           "music_failed". The Pi's stream player was
                           refused or dropped and it has stopped retrying
                           (it retries a refused stream for ~8 s); its
                           music LEDs are off again. Sent only for the
                           music it is still meant to be playing — a
                           music_stop, a newer music_start, a wake or a
                           response supersedes the attempt silently. The
                           server pauses MPD (so it doesn't play on to
                           nobody) unless a newer music_start's handshake
                           is pending or the room has moved on.
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
  text  ready              {"type":"ready","protocol_version":"0.1","room_id":...,"bot_name":...,"audio_sample_rate_in":16000,
                            "features":["speech_pause","end_capture","music_failed"]}
                           — sent only AFTER the client's hello was accepted
                           (and the room's MPD daemon provisioned). A socket
                           that never says hello never receives it.
                           `features` lists the message types this server
                           understands beyond protocol 0.1. A satellite sends
                           a new type only when it is listed: an older server
                           answers any unknown type with `error`, which the
                           satellite takes as the end of the turn. New fields
                           on existing messages need no listing — both sides
                           ignore fields they don't know.
  text  end_capture        {"type":"end_capture","utt":N}
                           — stop capturing now: the server has heard a whole
                           command (early commit, see below) and is answering.
                           Sent only to a satellite whose hello declared
                           `capture_control`, and only for a capture that
                           carried `utt`; the satellite ignores one whose
                           `utt` is not the capture it is running (a late one
                           must not end the next capture). It still sends
                           `utterance_end` (exit_reason "server_endpoint"),
                           which the server reads only to log whether speech
                           came after it stopped listening. An older
                           satellite ignores the type.
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
  text  get_logs           {"type":"get_logs","request_id":str,"max_bytes":int}
                           - ask the satellite for the tail of its in-RAM log
                           ring. The ONLY request/response pair in this
                           protocol; answered by `logs_chunk` frames below.
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
  text  error              {"type":"error","message":...,"reason"?:...}
                           — `reason:"pairing_rejected"` when the hello's
                           pairing check refused the socket;
                           `reason:"hello_required"` when the first frame
                           was not a hello. Both are followed by a close.
  text  pong               {"type":"pong"}
  bytes                    raw PCM TTS audio at audio_sample_rate. During a
                           drop-in, inbound bytes are instead live 16 kHz relay
                           audio from the peer room (see dropin_start).

Pi owns: wake-word detection, VAD, noise gate, barge-in detection.
Server owns: STT, intent routing, TTS, response lifecycle.

A `barge_in` (or a new `utterance_start` while a response is still streaming)
cancels the in-flight response task; clients then see a final
`response_end` with `interrupted=true`.

Speculative transcription (early endpointing, part A). The satellite ends a
capture only after `listen.silence_timeout` (1.2 s by default) of silence,
but its frames are here as they are spoken. So at the first ~240 ms pause
after speech — the satellite's own `speech_pause`, or for one that doesn't
send it `endpointing.LevelPauseDetector` on the frames — the server copies
the buffer and starts Whisper on the copy (then, once the transcript is
out, the voice embedding). At
`utterance_end` it uses that transcript if and only if no frame the
satellite called speech came after the copy: exact frame accounting, from
`last_voiced_frame` in `utterance_end` or, for a satellite that doesn't
report it, from the silence timeout it reported in `config_status`
(`endpointing.last_voiced_from_timeout`). Otherwise the copy is dropped
and the whole buffer is transcribed as before. One Whisper call (or voice
embedding) per room at a time, speculative or not; a new `utterance_start`
discards the copy.
The greeting strip, the greeting-only drop, the self-echo guard and the
blank-capture guard run on whichever transcript is used. Off with
`speculative_stt_enabled=false`.

Early commit (part B). When that early transcript is a whole, closed
command, waiting out the rest of the silence buys nothing: the server ends
the capture itself (`end_capture`) and answers. Only for a satellite that
declared `capture_control` and reports its pauses, only for wake_word and
follow-up turns (never chat, barge-in, a wake-word training clip or a
mid-call command), only when the transcript fully matches a fast path that
opted in (`FastPath.early_commit`, judged by `domovoi/early_commit.py` on
the router's own dry run, a parked yes/no answer included), and only once
the satellite's own detector has heard no speech for the tier's hold since
the last voiced frame — which must be inside the transcribed copy. On a
greeting-played turn the copy is screened for the wake greeting exactly as
the turn will be: one that is nothing but the greeting never commits (unless
the greeting played before the capture, `ack_before_capture`). Tier A
(closed phrases) holds `early_commit_hold_a_ms` (350), tier B (a duration,
a number, a clock question, any one-word command) `early_commit_hold_b_ms`
(650). A `noisy_capture` for a committed capture is ignored; the
satellite's late `utterance_end` is read only to log speech that came after
the commit. Off with `early_commit_enabled=false` (tier B alone:
`early_commit_tier_b=false`); per satellite, `[listen] early_commit=false`
on the Pi stops it declaring `capture_control`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import logging
import re
import secrets
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import text

from domovoi import command_captures
from domovoi import fast_lane
from domovoi.admin_auth import TRUSTED_PROXIES, SlidingWindowLimiter, token_sha256
from domovoi.clients.letta import get_letta_client
from domovoi.clients.tts import get_tts_client
from domovoi.clients.whisper import (
    FULL_WINDOW_SEC,
    SHORT_WINDOW_SEC,
    SttUnavailableError,
    can_decode_full_window,
    get_whisper_client,
    transcribe_full_window,
    transcribe_with_window,
    whisper_runtime,
)
from domovoi.config import settings
from domovoi.connectivity import ConnectivityProbe
from domovoi.db.repositories import (
    SatelliteApprovalRepository,
    SatellitePairingRepository,
    SatellitesRepository,
    SessionRepository,
)
from domovoi.db.session import session_scope
from domovoi.early_commit import TIER_A, early_commit_for
from domovoi.endpointing import (
    FRAME_MS,
    LevelPauseDetector,
    last_voiced_from_timeout,
    pcm_dbfs,
)
from domovoi.models import Context, Intent
from domovoi.music_pause import note_paused_by_person, paused_by_person
from domovoi.now_playing import NOW_PLAYING
from domovoi.router import (
    goes_to_the_tool_model,
    is_question_about_the_world,
    is_request_for_a_story,
    route,
)
from domovoi.satellite_media.overlay import APPROVAL_CODE_DIGITS
from domovoi.timer_delivery import AnnounceInterrupted, AnnounceNotStarted
from domovoi.turn_timings import (
    TurnTimings,
    merge_post_route,
    timings_for_row,
    whisper_block,
)

log = logging.getLogger(__name__)

# CORE-3 — per-source budget for approval requests. Parking a room name is
# the one unauthenticated write a stranger on the LAN can still cause, and
# the approvals list is a surface a person reads: without a ceiling, one
# address can fill it with invented rooms and bury the device someone is
# actually standing next to. Twenty in ten minutes is far more than a
# satellite retrying its own connect needs (it backs off between tries)
# and far less than a sweep wants. In memory, like the login backoff.
APPROVAL_HELLO_MAX_PER_WINDOW = 20
APPROVAL_HELLO_WINDOW_SEC = 600.0
APPROVAL_HELLO_LIMITER = SlidingWindowLimiter(
    max_per_window=APPROVAL_HELLO_MAX_PER_WINDOW,
    window_sec=APPROVAL_HELLO_WINDOW_SEC,
)

# CORE-22 — how long the first device to park a room name holds it against
# a DIFFERENT device. Inside the window a second device is refused as a
# conflict (the row on the dashboard keeps describing the device that made
# it); after it, a different device's request REPLACES the row, token hash
# and code together, so a request nobody can approve — a LAN host parking
# "kitchen" with a code no person will ever be told — cannot hold the name
# for good. The replacement carries the new device's own code, so the
# operator, who types the code the device in the room is saying, can only
# ever approve that device. Three minutes is long enough to read a code off
# a satellite and type it, short enough that the real device, which keeps
# retrying, gets its turn back quickly.
APPROVAL_TAKEOVER_SEC = 180.0

def _is_approval_code(code: str) -> bool:
    """Exactly :data:`APPROVAL_CODE_DIGITS` ASCII digits — the only shape
    the approve route can ever match (``str.isdigit`` alone would also
    take superscripts and other scripts' digits)."""
    return (
        len(code) == APPROVAL_CODE_DIGITS
        and code.isascii()
        and code.isdigit()
    )


# Set once when the pairing check hits a missing satellite_pairings table
# (V002 not applied yet) so we log an actionable hint ONCE instead of on
# every hello. Reset only by a process restart.
_pairing_table_warned = False

# In-flight post-route timing writes (StreamSession._finish_turn_timings).
# Each runs outside its turn's task so a barge-in can't cancel it; held
# here until done, because the event loop keeps only weak references.
_TIMING_WRITES: set[asyncio.Task[None]] = set()
# Same for opted-in rooms' command recordings (StreamSession._keep_capture).
_CAPTURE_WRITES: set[asyncio.Task[str | None]] = set()
# Same for the music restart that follows an announcement
# (StreamSession._restart_music_after_announce): it runs after `announce`
# has returned, so a broadcast loop never waits on a room's stream.
_ANNOUNCE_MUSIC_RESTARTS: set[asyncio.Task[None]] = set()
# What such a restart gets on top of MUSIC_STREAM_READY_TIMEOUT_SEC (the
# stream wait's own limit) for sending the frame and arming the handshake.
_ANNOUNCE_MUSIC_RESTART_MARGIN_SEC = 2.0
# How often such a restart looks again while another announcement is on its
# way to the room, and how long past TIMER_ANNOUNCE_MAX_WAIT_SEC (a queued
# timer delivery's own cap) it keeps waiting.
_ANNOUNCE_MUSIC_QUEUE_POLL_SEC = 0.1
_ANNOUNCE_MUSIC_QUEUE_MARGIN_SEC = 30.0
# Same for a room's held music_start (StreamSession.hold_music_start): the
# session forgets it the moment anything supersedes it, its own send
# included, while it may still be running.
_MUSIC_HOLDS: set[asyncio.Task[None]] = set()


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

# What this server understands beyond protocol 0.1, listed in `ready`. A
# satellite sends a new message type only when it is listed here (see the
# `ready` entry in the module docstring for why).
CORE_FEATURES: tuple[str, ...] = ("speech_pause", "end_capture", "music_failed")

# When a room can take an out-of-turn announcement (a timer or reminder
# going off elsewhere, domovoi/timer_delivery.py): StreamSession.
# announce_block. A capture whose last audio frame is older than this is
# over — a follow-up capture that times out with no speech sends
# utterance_start but never utterance_end.
ANNOUNCE_CAPTURE_FRESH_SEC = 1.5
# Just connected: give the satellite a moment to settle after `ready`.
ANNOUNCE_CONNECTING_SEC = 3.0
# The last reply asked a question (expect_followup): hold this long past
# the estimated end of its playback for the answer to start.
ANNOUNCE_FOLLOWUP_HOLD_SEC = 9.0
# A beat after any playback ends before the next announcement starts.
ANNOUNCE_SETTLE_SEC = 1.0

# A music_start a room cannot take yet is HELD, not dropped, and sent once
# the room is free (StreamSession.hold_music_start, music_block): a capture
# is open, a turn is being answered, a question's follow-up window is open
# (the reply asked something and the satellite is listening for the answer
# without a wake word), an announcement is on its way, or the room is in a
# call, chat mode or a wake-word recording. mpg123 started under an open
# capture is heard by it as continuous speech, so the capture runs to
# `max_record_seconds` and the lyrics get answered (dining room,
# 2026-09-30). Checked this often...
MUSIC_HOLD_POLL_SEC = 0.25
# ...and given up on after this long: a room busy for ten minutes (a long
# chat, a wake-word recording session) has moved on, and its next turn
# auto-resumes the music anyway.
MUSIC_HOLD_MAX_SEC = 600.0

# The turns a server may end early: a command after the wake word, or the
# answer to a question it just asked. Never a chat turn (Letta, long
# replies), a barge-in, or anything else.
EARLY_COMMIT_TRIGGERS = frozenset({"wake_word", "followup"})

# Speculative decodes per utterance, at most. Each is a full Whisper pass
# (faster-whisper pads every call to a 30 s window, so a partial costs what
# a whole one does), and a capture that keeps pausing and resuming — a TV,
# a long hesitant question — must not become decodes back to back. Past
# the cap the turn transcribes after `utterance_end`, as it always did.
SPECULATIVE_MAX_DECODES = 3

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


# ADD-2. How much more than the requested ``max_bytes`` a satellite's
# answer may come to before the pull is abandoned. The Pi is asked for at
# most N bytes of log; what comes back is that text inside JSON frames,
# so it is legitimately somewhat larger — escaping, and the tail of a
# chunk that straddled the boundary. Twice the ask plus 64 KiB covers a
# worst case of every character escaping to six; anything beyond it is
# not an answer to the question that was asked.
LOG_REASSEMBLY_SLACK = 2.0
LOG_REASSEMBLY_FLOOR = 64 * 1024


@dataclass
class _LogRequest:
    """One in-flight `get_logs` → `logs_chunk`… exchange.

    This is the ONLY request/response pair in an otherwise one-way
    protocol, so the correlation id, reassembly buffer and expected
    sequence live here rather than in a general mechanism with a single
    caller. ``future`` resolves on the chunk marked ``final``, or takes an
    exception if the satellite disconnects, misorders the series, or
    sends more than ``max_chars`` in total.

    ``max_chars`` is the point of the cap: the core asks for at most
    ``max_bytes`` of log, and until this existed nothing enforced that on
    the way back — a session could answer a single pull with frames for
    as long as the timeout allowed, straight into core memory.
    """

    future: "asyncio.Future[dict[str, Any]]"
    max_chars: int = 0
    chunks: list[str] = field(default_factory=list)
    next_seq: int = 0
    total_chars: int = 0

    def accept(self, data: str) -> bool:
        """Take one chunk, or refuse the whole request. Refusing stops
        the accumulation as well as failing the future — the buffer is
        dropped, not kept around holding what arrived so far."""
        if self.max_chars and self.total_chars + len(data) > self.max_chars:
            self.chunks.clear()
            self.future.set_exception(RuntimeError(
                f"satellite log exceeded the {self.max_chars}-character cap "
                f"for this request"
            ))
            return False
        self.total_chars += len(data)
        self.chunks.append(data)
        return True


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
    calls `mpd.resume()` — unless a person paused the room's music
    (domovoi/music_pause.py), which neither it nor `consume_music_ready`
    undoes. Covers:
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
        if paused_by_person(app, room_id):
            log.info(
                "music handshake: no music_ready from room=%s within %.1fs; "
                "its music was paused by a person, leaving MPD paused",
                room_id, settings.music_prepare_fallback_sec,
            )
            cur = pending.get(room_id)
            if cur is not None and cur.get("task") is asyncio.current_task():
                pending.pop(room_id, None)
            return
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


async def send_music_start(
    app: Any,
    session: Any,
    room_id: str,
    url: str,
    *,
    still_wanted: Callable[[], bool] | None = None,
) -> bool:
    """Tell a satellite to start streaming ``url``: the ONE way every
    music_start leaves the core (a voice turn's start, the auto-resume after
    a turn, an intercom tail, a drop-in restore, every admin/dashboard cast).

    Three steps, in this order:

    1. Make sure the room's stream is serving (``ensure_stream_serving``).
       The satellite's mpg123 connects the moment the frame arrives and gives
       up at once on a refused port, and a room whose MPD has not played
       since its daemon started has no stream open after a paused prepare —
       office, 2026-09-30: the first cast after the daemons restarted played
       to nobody. One local request when the stream is already up; bounded
       by ``music_stream_ready_timeout_sec`` otherwise. Never blocks the
       frame: the satellite retries, and the fallback below resumes MPD.
    2. Send the frame.
    3. Arm the music_ready handshake (``schedule_music_resume_fallback``).

    ``still_wanted``, for a caller whose start can outlive the moment it was
    decided (the restart after an announcement runs on its own): asked after
    the stream wait, with nothing awaited between it and the send, and a
    False sends nothing and arms nothing. Returns whether the frame went.

    This sends; it does not decide whether the room can take the frame now.
    Everything but a turn's own start at the end of its reply goes through
    `StreamSession.start_music` / `hold_music_start`, which hold a start
    while the room is listening, answering or asking (`music_block`).
    """
    from domovoi.clients.mpd import ensure_stream_serving

    try:
        await ensure_stream_serving(room_id, url)
    except Exception as e:  # noqa: BLE001 — a check must never cost the start
        log.warning("music stream: readiness check failed for room=%s: %s", room_id, e)
    if still_wanted is not None and not still_wanted():
        log.info(
            "music stream: room=%s moved on while its stream was readied; "
            "no music_start", room_id,
        )
        return False
    await session._safe_send_text({"type": "music_start", "stream_url": url})
    await schedule_music_resume_fallback(app, room_id, url)
    return True


async def consume_music_failed(app: Any, room_id: str, ctrl: dict[str, Any]) -> None:
    """Handle a ``music_failed`` frame: the satellite could not play the
    stream and has stopped trying (its music LEDs are off again).

    Without it the core cannot tell a playing room from a silent one: the
    music_ready fallback has usually unpaused MPD by then, so the room plays
    on to nobody and the dashboard reads "playing". So MPD is paused — which
    also keeps the place in the song for the next start (a turn's
    auto-resume re-sends music_start from ``resumable_music``, which is left
    alone).

    Only when the report is about what the room is still meant to be
    playing, and no newer start is in flight: a pending handshake means the
    core has already sent another music_start, whose own music_ready or
    fallback decides; a stopped room (no resumable entry) or another stream
    has moved on. The satellite reports only a failure no newer start has
    superseded, and the socket is ordered, so this cannot pause a start that
    came after it.
    """
    url = str(ctrl.get("stream_url") or "")
    reason = str(ctrl.get("reason") or "")[:300]
    attempts = ctrl.get("attempts")
    # Satellite-supplied: bounded, and quoted (%r) so a newline cannot
    # forge a journal line. A URL longer than any stream URL the core hands
    # out matches no resumable entry below either.
    log.warning(
        "music: satellite in room=%s could not play %r (%s attempt(s)): %r",
        room_id, url[:300] if url else "?",
        attempts if isinstance(attempts, int) and not isinstance(attempts, bool) else "?",
        reason or "?",
    )
    pending: dict[str, dict[str, Any]] = app.state.pending_music_start
    if room_id in pending:
        return
    resumable: dict[str, str] = app.state.resumable_music
    if not url or resumable.get(room_id) != url:
        return
    from domovoi.clients.mpd import get_mpd_client_for

    try:
        await get_mpd_client_for(room_id).pause()
        log.info("music: paused MPD for room=%s; nobody is listening to it", room_id)
    except Exception as e:  # noqa: BLE001 — best-effort, like resume
        log.warning("music: pause after music_failed failed for room=%s: %s", room_id, e)


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
    if paused_by_person(app, room_id):
        # The satellite's player is back on the (paused) stream, so a
        # person's "resume" is heard at once; until then it stays paused.
        log.info(
            "music handshake: music_ready from room=%s; its music was paused "
            "by a person, leaving MPD paused", room_id,
        )
        return
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


# RMS level of a 16-bit-LE PCM frame in dBFS (full-scale = 0.0; empty or
# silent → a -120 floor). Used by the drop-in relay noise gate to skip
# forwarding near-silent frames (bounced cross-room echo / room tone); the
# speculative-transcription pause detector reads the same measure.
_pcm_dbfs = pcm_dbfs


@dataclass
class _Heard:
    """A transcription of (all or part of) a capture: Whisper's raw text,
    and the voice embedding computed from the same audio, when it was.

    Only the embedding: it is the expensive, side-effect-free part of voice
    identification (``voice_identifier.embed_voice``). The matching touches
    ``last_seen`` and the drift counter, so it runs once, in the turn —
    never on a copy the turn may throw away.

    A speculative copy's embedding is computed AFTER its decode, never
    beside it (the two share the CPU, and the transcript is what the early
    commit and the turn wait on): ``embed_task`` is still running when the
    transcript is handed over, and the turn awaits it just before voice
    identification (:meth:`StreamSession._copy_embedding`)."""

    text: str
    stt_ms: int
    whisper: dict[str, Any] | None
    # False: nothing was embedded from this audio; the turn embeds it.
    embedded: bool = False
    embedding: Any = None
    # The copy's embedding, still being computed: resolves to the
    # (embedded, embedding) pair above.
    embed_task: asyncio.Task[tuple[bool, Any]] | None = None
    # The mel window (seconds) Whisper decoded this on — 10 for a short
    # capture, 30 otherwise — when the client says (clients/whisper.py).
    window_s: int | None = None


@dataclass
class _Speculation:
    """One speculative decode of a capture, started at a pause (see the
    module docstring). ``frames`` is how many frames the copy holds — the
    turn may use it only when the satellite's last voiced frame is below
    that."""

    serial: int                 # the utterance it belongs to
    frames: int
    task: asyncio.Task[_Heard | None] | None = None
    # The Whisper call's own time, once it has returned.
    stt_ms: int | None = None
    # Early commit: (tier, hold_ms) once judged, or () for "never" — the
    # decision is made once per copy (StreamSession._commit_decision).
    commit: tuple[str, int] | tuple[()] | None = None
    # For the turn's timing record (_CaptureClock.flags): when the pause
    # this copy was taken for reached the server (time.perf_counter()),
    # what the satellite's clock said on it (`sat_ms` / `backlog_ms`), the
    # last voiced frame it named, and — once the Whisper call is under way —
    # when that call started and how long it first waited for this room's
    # decoder.
    pause_rx: float | None = None
    pause_clock: dict[str, int] = field(default_factory=dict)
    last_voiced: int | None = None
    decode_started: float | None = None
    decode_wait_ms: int | None = None


# 30 ms frames, in seconds, for the capture clock's schedule arithmetic.
_FRAME_SEC = FRAME_MS / 1000


def _sat_clock(ctrl: dict[str, Any], *keys: str) -> dict[str, int]:
    """The satellite's own timing numbers on a control message (see the
    `utterance_start` / `speech_pause` / `utterance_end` entries in the
    module docstring): whole non-negative milliseconds, anything else
    dropped. An older satellite sends none."""
    out: dict[str, int] = {}
    for key in keys:
        v = ctrl.get(key)
        if type(v) is int and 0 <= v < 3_600_000:
            out[key] = v
    return out


def _lag_ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


@dataclass
class _CaptureClock:
    """When one capture's messages reached the server, against the pace
    its audio was spoken at — the numbers that say whether a speculative
    transcript started late, and whose fault that was (the satellite, the
    network or this server). Numbers only; see ``flags``.

    The schedule: frame ``k`` (0-based) of a live capture cannot arrive
    before ``t0 + k × 30 ms``, where ``t0`` is when its first frame could
    have. ``lo``, the smallest ``arrival − k × 30 ms`` over the frames so
    far, is the best estimate of ``t0`` (plus the network's floor), and a
    frame's lag is how far past ``lo + k × 30 ms`` it arrived. A capture
    that starts with a backlog — the satellite busy while the person was
    already talking — sends its first frames in a burst, and they show it
    as lag; so does a frame the network held up."""

    start_rx: float                          # utterance_start received
    sat_start: dict[str, int] = field(default_factory=dict)   # its backlog_ms / wake_ms
    first: float | None = None
    lo: float | None = None
    hi: float | None = None
    # The end: when utterance_end arrived (None for a capture this server
    # ended itself), the frames held then, and its sat_ms / backlog_ms.
    end_rx: float | None = None
    end_frames: int = 0
    end_sat: dict[str, int] = field(default_factory=dict)

    def frame(self, index: int, now: float) -> None:
        off = now - index * _FRAME_SEC
        if self.first is None or self.lo is None or self.hi is None:
            self.first = self.lo = self.hi = off
            return
        if off < self.lo:
            self.lo = off
        elif off > self.hi:
            self.hi = off

    def _due(self, frames: int) -> float | None:
        """When the last of ``frames`` frames was due on the schedule."""
        if self.lo is None or frames <= 0:
            return None
        return self.lo + (frames - 1) * _FRAME_SEC

    def flags(self, spec: _Speculation | None) -> dict[str, int]:
        """The ``intents_log.timings`` keys for this capture
        (turn_timings.CAPTURE_TIMING_KEYS), with ``spec`` the copy the
        turn used or else the latest one. Each is left out when it can't
        be known — an older satellite sends no clock of its own, a capture
        with no pause has no copy."""
        out: dict[str, int] = {}
        if self.first is not None and self.lo is not None and self.hi is not None:
            out["frame_lag_first_ms"] = _lag_ms(self.first - self.lo)
            out["frame_lag_max_ms"] = _lag_ms(self.hi - self.lo)
        if "backlog_ms" in self.sat_start:
            out["sat_start_backlog_ms"] = self.sat_start["backlog_ms"]
        if "wake_ms" in self.sat_start:
            out["sat_wake_ms"] = self.sat_start["wake_ms"]
        end_due = self._due(self.end_frames)
        if self.end_rx is not None and end_due is not None:
            out["end_rx_lag_ms"] = _lag_ms(self.end_rx - end_due)
        if "backlog_ms" in self.end_sat:
            out["sat_end_backlog_ms"] = self.end_sat["backlog_ms"]
        if spec is None or spec.pause_rx is None:
            return out
        pause_due = self._due(spec.frames)
        if pause_due is not None:
            out["pause_rx_lag_ms"] = _lag_ms(spec.pause_rx - pause_due)
        if spec.decode_started is not None:
            out["pause_to_decode_ms"] = _lag_ms(spec.decode_started - spec.pause_rx)
            if spec.last_voiced is not None and self.lo is not None:
                out["decode_start_ms"] = _lag_ms(
                    spec.decode_started - (self.lo + spec.last_voiced * _FRAME_SEC)
                )
        if spec.decode_wait_ms is not None:
            out["decode_wait_ms"] = spec.decode_wait_ms
        if "backlog_ms" in spec.pause_clock:
            out["sat_pause_backlog_ms"] = spec.pause_clock["backlog_ms"]
        sat_ms = spec.pause_clock.get("sat_ms")
        if sat_ms is not None:
            out["sat_pause_ms"] = sat_ms
            # Signed: how much longer the pause took to get here than the
            # utterance_start did, beyond the satellite's own gap between
            # sending them — the network's share.
            out["pause_net_ms"] = int(round((spec.pause_rx - self.start_rx) * 1000)) - sat_ms
        return out


@dataclass
class _Committed:
    """A capture this server ended early, until the satellite's own
    `utterance_end` for it arrives (read only to log speech after the
    commit) or the next utterance starts."""

    serial: int
    utt: Any
    frames: int                 # frames the server had when it committed
    tier: str
    timings: TurnTimings | None = None


def _elapsed_ms(t0: float) -> int:
    return max(0, int(round((time.perf_counter() - t0) * 1000)))


# A satellite's after-the-end numbers (`voiced_after_end_ms`,
# `listened_after_end_ms`) are bounded by its silence timeout (at most 5 s);
# anything else is not a report of that watch.
_AFTER_END_MAX_MS = 10_000


# An hour of 30 ms frames: more than any capture a satellite sends.
_CAPTURE_FRAMES_MAX = 3_600_000 // FRAME_MS


def _frame_count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= _CAPTURE_FRAMES_MAX


def _after_end_ms(value: Any) -> int | None:
    if type(value) is not int or not 0 <= value <= _AFTER_END_MAX_MS:
        return None
    return value


def _cancel_copy_embedding(spec: "_Speculation") -> None:
    """Cancel a finished copy's voice embedding (``_Heard.embed_task``) if
    it hasn't finished. One already running in its thread still runs to the
    end — the decode slot is held until it does (``_one_at_a_time``) — but
    one still waiting for the slot never starts."""
    task = spec.task
    if task is None or not task.done() or task.cancelled() or task.exception() is not None:
        return
    heard = task.result()
    embed = getattr(heard, "embed_task", None)
    if embed is not None and not embed.done():
        embed.cancel()


def _consume_outcome(fut: "asyncio.Future[Any]") -> None:
    # A decode nobody awaits any more (its turn was cancelled, its copy
    # discarded) must not log "exception was never retrieved".
    if not fut.cancelled():
        fut.exception()


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


class _InterimSpeech:
    """A short line spoken as the opening of a turn's reply while the rest
    of the turn is still being worked out — the router's "Just a moment,
    I'm waking up my language model." before a cold model load
    (``Context.speak_interim``). It opens the turn's one response: a
    response_start and its audio now; the reply's audio follows later in
    the same response, at the same rate, with no second response_start.
    Said at most once per turn. A TTS failure just means it isn't said."""

    def __init__(self, session: "StreamSession", voice: str | None) -> None:
        self._session = session
        self._voice = voice
        self.started = False
        self.text = ""
        self.sample_rate = 0

    async def say(self, text: str) -> None:
        if self.started:
            return
        sess = self._session
        try:
            engine, voice = await resolve_voice(self._voice)
            pcm, sr = _wav_to_pcm(
                await get_tts_client().synthesize(text, engine=engine, voice=voice)
            )
        except Exception as e:  # noqa: BLE001
            log.warning("stream %s: interim line TTS failed: %s", sess.room_id, e)
            return
        self.started, self.text, self.sample_rate = True, text, sr
        await sess._safe_send_text({
            "type": "response_start",
            "text": text,
            "matched_handler": "cold_start",
            "matched_path": "system",
            "session_id": str(sess.session_id) if sess.session_id else None,
            "online": True,
            "audio_sample_rate": sr,
        })
        for chunk in _iter_chunks(pcm):
            try:
                await sess.ws.send_bytes(chunk)
            except Exception:
                return  # Pi went away; the turn's own send path will notice
            sess._note_audio_sent(len(chunk), sr)


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
        # Whether the hello's pairing check matched (or claimed) this room
        # with a token. False for a socket accepted with none (strict
        # pairing off, or the check could not run): such a socket announces
        # only its own room's timers and reminders (timer_delivery.py), since
        # any LAN device can open /v1/stream/<a new room name>.
        self.token_authenticated = False
        self._response_task: asyncio.Task[None] | None = None
        # ── Out-of-turn announcements (announce(), announce_block) ────
        # `_playout_until`: monotonic estimate of when the audio already
        # sent to this room stops playing (bytes / rate, see
        # `_note_audio_sent`). `_followup_hold_until`: a reply that asked a
        # question holds announcements until its answer can start.
        # `_connected_at`: when `ready` went out. `_last_audio_at`: the last
        # mic frame, which tells a live capture from a follow-up capture
        # that timed out without an utterance_end. One announcement at a
        # time per room (`_announce_lock`); its frame-sending half runs as
        # `_announce_task`, which a new capture cancels like a reply.
        self._playout_until: float = 0.0
        self._followup_hold_until: float = 0.0
        self._connected_at: float = 0.0
        self._last_audio_at: float = 0.0
        self._announce_lock = asyncio.Lock()
        self._announce_task: asyncio.Task[None] | None = None
        self._announce_cut = False
        # Numbers the music restarts that follow announcements here, so an
        # older one still waiting on the stream steps aside for a newer one
        # (see `_restart_music_after_announce`).
        self._announce_music_seq = 0
        # announce() calls that have not returned yet: the one holding
        # `_announce_lock`, and any queued behind it or still synthesizing
        # its first sentence. A music restart waits while there are any.
        self._announce_callers = 0
        # The room's one held music_start (`hold_music_start`): a start the
        # room could not take when it was decided, sent once nothing here is
        # listening, answering or asking (`music_block`). Any music frame
        # that goes out to the room supersedes it (`_safe_send_text`).
        self._music_hold_task: asyncio.Task[None] | None = None
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
        # Whether a drop-in call was live at ANY point of the in-flight
        # utterance: set at utterance_start when already in a call, and by
        # _begin_dropin when one starts mid-capture. Read at utterance_end so
        # a call the peer hung up before the capture closed still keeps that
        # capture (the other room's audio on this speaker) out of an opted-in
        # room's command recordings (domovoi/command_captures.py).
        self._utterance_in_call: bool = False
        # Text of the reply most recently sent to this room's speaker.
        # Kept so a barge-triggered utterance can be checked against what the
        # satellite was saying when the mic opened — on a board whose AEC is
        # underperforming, the "interruption" is sometimes its own voice.
        # Best-effort and intentionally not cleared on response_end: the echo
        # arrives just AFTER the reply ends, so the value has to outlive it.
        self._last_spoken_text: str = ""
        # In-flight `get_logs` pulls, keyed by request id. Per-session (not
        # app.state) because the exchange is meaningless across a reconnect:
        # a new socket can't answer the old one's request.
        self._log_requests: dict[str, _LogRequest] = {}
        # ── Speculative transcription (early endpointing, part A) ────
        # See the module docstring. `_utt_serial` numbers this socket's
        # utterances so a copy from an earlier one is never mistaken for
        # the current one's; `_utt_frames` counts the frames buffered for
        # the current one — the same count the satellite keeps, frame for
        # frame, which is what makes the reuse check exact.
        self._utt_serial = 0
        self._utt_frames = 0
        # The satellite's own number for the capture (utterance_start.utt).
        self._utt_id: Any = None
        # hello.speech_pause: the satellite reports its own pauses and its
        # last voiced frame, so the loudness detector isn't needed.
        self._speech_hints = False
        # Whether the current utterance may be transcribed speculatively
        # (decided at utterance_start), and the detector finding its pauses
        # when the satellite doesn't report them.
        self._spec_on = False
        self._pause_detector: LevelPauseDetector | None = None
        # The satellite's latest `speech_pause` for this utterance as
        # (frames at the hint, last voiced frame), until `speech_resume`.
        self._pi_pause: tuple[int, int] | None = None
        # The latest copy and every one taken this utterance (for the
        # turn's record); a pause that arrived while one was decoding.
        self._spec: _Speculation | None = None
        self._spec_history: list[_Speculation] = []
        self._spec_wanted = False
        # This room's Whisper call in flight, speculative or not: the next
        # one waits for it, so a room never runs two at once.
        self._decode_inflight: asyncio.Future[str] | None = None
        # When the utterance's messages and frames arrived, against the pace
        # it was spoken at (see _CaptureClock), and the latest pause: when
        # it reached the server and what the satellite's clock said on it.
        self._clock: _CaptureClock | None = None
        self._pause_rx: float | None = None
        self._pause_clock: dict[str, int] = {}
        # ── Early commit (part B) ─────────────────────────────────────
        # hello.capture_control: the satellite honours `end_capture`.
        self._capture_control = False
        # Whether the current utterance may be ended early (decided at
        # utterance_start), and the session state its dry run needs —
        # the parked confirmation and the chat-mode flag — read once per
        # utterance while the person is still talking.
        self._commit_on = False
        self._commit_ctx_task: asyncio.Task[tuple[Any, bool] | None] | None = None
        # greeting_played as the satellite's latest `speech_pause` said it,
        # the clip it named (greeting_clip) and whether that greeting
        # finished before the capture opened (ack_before_capture) — the
        # early-commit check screens the copy for the greeting the way the
        # turn will.
        self._hint_greeting = False
        self._hint_greeting_clip: str | None = None
        self._hint_ack_before = False
        # The capture ended early, until its late `utterance_end`.
        self._committed: _Committed | None = None
        # The streaming fast lane's view of the capture in flight
        # (domovoi/fast_lane.py, shadow mode: it decides nothing here).
        # None whenever fastlane_mode is off.
        self._fastlane: fast_lane.LaneCapture | None = None

    async def run(self) -> None:
        await self.ws.accept()
        # ── Hello gate ───────────────────────────────────────────────────
        # NOTHING about this room exists until a `hello` frame has arrived
        # and passed the pairing check: no MPD provisioning, no
        # active_sessions entry, no `ready`. Before this gate the bare WS
        # handshake alone did all three — any LAN device could connect to
        # /v1/stream/<anything>, receive `ready`, and leave an mpd_rooms
        # row + ports behind without ever being asked for a token (seen
        # live on 2026-09-15 as a `probe-no-hello` room on the dashboard),
        # and a probe for an EXISTING room evicted its real satellite from
        # active_sessions. A socket that sends no hello within
        # `satellite_hello_timeout_sec`, sends anything else first, or
        # fails pairing is closed here with nothing created.
        if not await self._await_hello():
            return
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
        # Registered from here on, so a timer delivery can see this session
        # before `ready` goes out: count the handshake as `connecting` too,
        # never as an idle room an announcement may start in. Set again
        # when `ready` is sent.
        self._connected_at = time.monotonic()
        self.ws.app.state.active_sessions[self.room_id] = self
        if existing is not None and existing is not self:
            log.info(
                "ws %s connected (replacing prior session — likely a reconnect)",
                self.room_id,
            )
            # CORE-7: and CLOSE the one we replaced. Dropping it from the
            # registry only stopped it receiving broadcasts — the socket
            # itself stayed open, holding its utterance buffer and
            # uvicorn's frame buffer, and only a TCP timeout or the ping
            # watchdog would ever reclaim it. Reconnect in a bad-wifi loop
            # and those accumulate. 1001 "going away": the server is
            # dropping THIS session, and the Pi's normal backoff reconnect
            # is the right response. Best-effort — the peer is usually
            # already gone, which is why it reconnected at all. A genuine
            # duplicate-room misconfig now flaps visibly instead of
            # leaving a silent session that receives nothing forever.
            await existing._close_quietly(1001)
        else:
            log.info("ws %s connected", self.room_id)
        await self._safe_send_text({
            "type": "ready",
            "protocol_version": PROTOCOL_VERSION,
            "room_id": self.room_id,
            "bot_name": settings.bot_name,
            "audio_sample_rate_in": PCM_INPUT_SAMPLE_RATE,
            "features": list(CORE_FEATURES),
        })
        self._connected_at = time.monotonic()
        # A timer or reminder that went off while this room was away (or
        # during a core restart) is announced now, if it is still within
        # its grace window. Non-blocking: it schedules its own work.
        delivery = getattr(self.ws.app.state, "timer_delivery", None)
        if delivery is not None:
            try:
                delivery.on_room_connected(self.room_id)
            except Exception as e:  # noqa: BLE001
                log.debug("timer delivery: room-connected hook failed for %s: %s",
                          self.room_id, e)
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
            # A music_start held for this socket has nobody to go to.
            self._drop_music_hold()
            # Nobody is left to use a speculative transcript.
            self._discard_speculation()
            self._committed = None
            if self._decode_inflight is not None and not self._decode_inflight.done():
                self._decode_inflight.cancel()
            self._fastlane = fast_lane.close(self._fastlane)
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
            # Fail any in-flight log pull now. The remaining chunks are
            # never coming, so waiting out the full timeout would only make
            # the dashboard look hung when the real answer is "it left".
            for pending in self._log_requests.values():
                if not pending.future.done():
                    pending.future.set_exception(
                        ConnectionError("satellite disconnected mid-log-transfer")
                    )
            self._log_requests.clear()
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
        # Before any early return: announce_block reads it to tell a live
        # capture from one that went quiet without an utterance_end.
        self._last_audio_at = time.monotonic()
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
        if self._clock is not None:
            self._clock.frame(self._utt_frames, time.perf_counter())
        self.audio_buf.extend(data)
        self._utt_frames += 1
        # The streaming fast lane (domovoi/fast_lane.py, shadow only) reads
        # the same frames, before anything below can end the capture.
        if self._fastlane is not None:
            self._fastlane.feed(data)
        # A satellite that doesn't report its own pauses: find the first
        # short one from the frames themselves.
        if self._pause_detector is not None and self._pause_detector.feed(data):
            self._pause_rx = time.perf_counter()
            self._pause_clock = {}
            self._on_pause()
        # Every silent frame after a reported pause brings the hold closer.
        if self._commit_on and self._pi_pause is not None:
            self._maybe_commit()

    # ── Speculative transcription (early endpointing, part A) ─────────

    def _speculation_possible(self, trigger: str | None) -> bool:
        """Whether an utterance starting now may be transcribed early: only
        when the reuse check can be exact — the satellite reports its last
        voiced frame, or it told us the silence timeout its capture ends
        on. A wake-word training clip is never a command."""
        if not settings.speculative_stt_enabled or trigger == "wake_clip":
            return False
        if self._speech_hints:
            return True
        cfg = self.ws.app.state.satellite_config.get(self.room_id) or {}
        try:
            return float(cfg.get("listen.silence_timeout")) > 0
        except (TypeError, ValueError):
            return False

    def _reset_utterance_tracking(self, trigger: str | None) -> None:
        """A new utterance: forget the last one's copies (a decode still
        running finishes on its own and is ignored) and decide whether this
        one is transcribed early."""
        self._utt_serial += 1
        self._utt_frames = 0
        self._pi_pause = None
        self._pause_rx = None
        self._pause_clock = {}
        self._spec = None
        self._spec_history = []
        self._spec_wanted = False
        self._spec_on = self._speculation_possible(trigger)
        self._pause_detector = (
            LevelPauseDetector() if self._spec_on and not self._speech_hints else None
        )
        self._committed = None
        self._hint_greeting = False
        self._hint_greeting_clip = None
        self._hint_ack_before = False
        self._commit_on = self._early_commit_possible(trigger)
        self._commit_ctx_task = (
            asyncio.create_task(
                self._read_commit_context(), name=f"commit-context:{self.room_id}"
            )
            if self._commit_on
            else None
        )

    def _take_clock(
        self, *, end_rx: float | None, end_sat: dict[str, int],
    ) -> _CaptureClock | None:
        """The utterance's capture clock, closed at its end (``end_rx``:
        when utterance_end arrived, None when this server ended it) and
        handed to the turn; the session keeps none until the next
        utterance_start."""
        clock, self._clock = self._clock, None
        if clock is not None:
            clock.end_rx = end_rx
            clock.end_frames = self._utt_frames
            clock.end_sat = end_sat
        return clock

    def _discard_speculation(self) -> None:
        """Drop this utterance's copies and stop looking for pauses. The
        current decode task is cancelled, and so is a finished copy's voice
        embedding that hasn't run yet: nobody will read either."""
        spec = self._spec
        if spec is not None and spec.task is not None and not spec.task.done():
            spec.task.cancel()
        elif spec is not None:
            _cancel_copy_embedding(spec)
        self._spec = None
        self._spec_history = []
        self._spec_wanted = False
        self._spec_on = False
        self._pause_detector = None
        self._pi_pause = None
        self._commit_on = False
        ctx_task = self._commit_ctx_task
        if ctx_task is not None and not ctx_task.done():
            ctx_task.cancel()
        self._commit_ctx_task = None

    def _in_pause(self) -> bool:
        if self._speech_hints:
            return self._pi_pause is not None
        return self._pause_detector is not None and self._pause_detector.in_pause

    def _on_pause(self) -> None:
        """The capture just paused after speech: copy what we have and start
        transcribing it — unless a decode is already running, in which
        case the copy is taken when it finishes (a room never runs two)."""
        if not (self.utterance_active and self._spec_on) or self.dropped_overflow:
            return
        spec = self._spec
        if spec is not None and spec.task is not None and not spec.task.done():
            self._spec_wanted = True
            return
        self._start_speculation()

    def _start_speculation(self) -> None:
        self._spec_wanted = False
        if len(self._spec_history) >= SPECULATIVE_MAX_DECODES:
            return
        spec = _Speculation(
            serial=self._utt_serial, frames=self._utt_frames,
            pause_rx=self._pause_rx, pause_clock=dict(self._pause_clock),
            last_voiced=self._pi_pause[1] if self._pi_pause is not None else None,
        )
        spec.task = asyncio.create_task(
            self._speculate(spec, bytes(self.audio_buf)),
            name=f"speculative-stt:{self.room_id}",
        )
        spec.task.add_done_callback(lambda _t, s=spec: self._on_speculation_done(s))
        self._spec = spec
        self._spec_history.append(spec)

    def _on_speculation_done(self, spec: _Speculation) -> None:
        if spec is not self._spec or not self.utterance_active:
            return
        # A pause came and went while this one was decoding. If the
        # capture is paused again now, take the next copy straight away.
        if self._spec_wanted and self._in_pause():
            self._start_speculation()
        else:
            self._spec_wanted = False
            # On slow hardware the decode outlasts the hold: the silence
            # is already long enough by the time the transcript exists.
            self._maybe_commit()

    async def _speculate(self, spec: _Speculation, pcm: bytes) -> _Heard | None:
        """Transcribe a copy of the capture so far, then start embedding
        the voice in it. Never raises: None means "no speculative
        transcript", and the turn transcribes the whole capture as it
        always did.

        The transcript is returned as soon as it exists — the early commit
        and the turn are waiting for it — and the embedding runs after the
        decode, in the room's decode slot (``_one_at_a_time``), never
        beside it: at the same time the two share the CPU, which slowed a
        10 s-window decode by ~80 ms (240 → 321 ms, measured). Embedding
        first instead costs ~60 ms of the decode's start. The turn awaits
        the embedding only at voice identification."""
        try:
            whisper = get_whisper_client()
        except SttUnavailableError:
            return None
        marks: dict[str, Any] = {}
        try:
            text, spec.stt_ms, window = await self._whisper_call(whisper, pcm, marks=marks)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(
                "stream %s: speculative transcription failed; the turn will "
                "transcribe the whole capture: %s", self.room_id, e,
            )
            return None
        finally:
            # For the capture clock: when the decode really started, and
            # how long it first waited for this room's decode slot.
            spec.decode_started = marks.get("started")
            spec.decode_wait_ms = marks.get("wait_ms")
        heard = _Heard(
            text=text,
            stt_ms=spec.stt_ms or 0,
            whisper=whisper_block(whisper_runtime()),
            window_s=window,
        )
        # A copy shorter than the embedder's minimum gets no embedding: the
        # whole capture (the copy plus its trailing silence) may clear it,
        # and whether a turn gets an embedding at all must not depend on
        # when the copy was taken. The turn embeds its own audio then.
        #
        # Nor does a copy that is already superseded: another pause came
        # while it decoded (`_spec_wanted`), so there was speech after it
        # and no turn can use it, or a new utterance has started — and its
        # embedding, in the decode slot, would only hold up the next copy's
        # decode (10-30 ms; seconds while the voice encoder is still
        # loading after a boot). (A copy the turn is waiting for is no
        # longer `self._spec` — utterance_end hands it over — so that is
        # not a sign of anything.)
        superseded = self._spec_wanted or spec.serial != self._utt_serial
        if not superseded and (
            len(pcm) >= settings.voice_profile_min_utterance_sec * PCM_INPUT_SAMPLE_RATE * 2
        ):
            heard.embed_task = asyncio.create_task(
                self._embed_copy(pcm), name=f"speculative-embed:{self.room_id}",
            )
        return heard

    async def _embed_copy(self, pcm: bytes) -> tuple[bool, Any]:
        """The voice embedding of a speculative copy, in the room's decode
        slot. Pure: nothing here may count as having heard somebody, since
        this copy may never be used. On any failure the turn embeds the
        audio itself, exactly as it would have without this."""
        from domovoi.voice_identifier import embed_voice

        try:
            return True, await self._one_at_a_time(lambda: embed_voice(pcm))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("stream %s: speculative voice embedding failed: %s", self.room_id, e)
            return False, None

    async def _copy_embedding(self, heard: _Heard) -> None:
        """Wait for a speculative copy's embedding (usually long done: it
        takes ~10 ms after the decode) and put it on ``heard``. Shielded:
        a barge-in cancelling the turn must not cancel the embedding, which
        holds the room's decode slot until its thread ends."""
        task = heard.embed_task
        if task is None:
            return
        try:
            heard.embedded, heard.embedding = await asyncio.shield(task)
        except asyncio.CancelledError:
            # This turn being cancelled (a barge-in) propagates; the
            # embedding alone having been cancelled (its slot torn down)
            # just means the turn embeds its own audio.
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            heard.embedded, heard.embedding = False, None
        except Exception:  # _embed_copy never raises; belt and braces
            heard.embedded, heard.embedding = False, None

    async def _one_at_a_time(self, start: Any) -> Any:
        """Run ``start()`` (a coroutine factory: a Whisper decode or a voice
        embedding) in this room's decode slot — never alongside another
        of its own. Whatever is already running (a speculative decode, its
        embedding, or a call a cancelled turn left behind) is waited out
        first: faster-whisper can't stop a decode midway, and two at once
        would only share the CPU.

        The call itself is shielded from whoever awaits it: the real
        clients run in a worker thread (``asyncio.to_thread``), which a
        cancel can't stop, so a cancelled caller (a discarded copy, a
        barge-in) must leave ``_decode_inflight`` pending until the thread
        is really done — otherwise the next call would start beside it."""
        while (prev := self._decode_inflight) is not None and not prev.done():
            await asyncio.wait({prev})
        fut = asyncio.ensure_future(start())
        fut.add_done_callback(_consume_outcome)
        self._decode_inflight = fut
        return await asyncio.shield(fut)

    async def _whisper_call(
        self, whisper: Any, pcm: bytes, *, marks: dict[str, Any] | None = None,
        full_window: bool = False,
    ) -> tuple[str, int, int | None]:
        """One Whisper call in the room's decode slot (``_one_at_a_time``).
        Returns the text, the call's own time (not counting the wait for
        the slot) and the mel window it decoded on, when the client says.
        ``marks``, when given, gets the wait for the slot (``wait_ms``)
        and the moment the call itself started (``started``,
        perf_counter), so a decode that queued behind another decode or
        a copy's embedding isn't mistaken for one that started late.
        ``full_window``: faster-whisper's 30 s path whatever the length
        (the second hearing; only for a client that has it)."""
        waited = time.perf_counter()
        t0: list[float] = []

        def _start() -> Any:
            t0.append(time.perf_counter())
            if marks is not None:
                marks["wait_ms"] = _elapsed_ms(waited)
                marks["started"] = t0[0]
            if full_window:
                return transcribe_full_window(whisper, pcm)
            return transcribe_with_window(whisper, pcm)

        text, window = await self._one_at_a_time(_start)
        return text, _elapsed_ms(t0[0]), window

    def _on_speech_hint(self, t: str, ctrl: dict[str, Any]) -> None:
        """`speech_pause` / `speech_resume` from the satellite. Frames and
        control messages share one ordered queue on the satellite, so by
        the time a hint is read here the buffer holds exactly the frames
        it describes — a count that disagrees means the hint is not about
        this buffer, and it is ignored."""
        if not (self._speech_hints and self.utterance_active):
            return
        utt = ctrl.get("utt")
        if utt is not None and self._utt_id is not None and utt != self._utt_id:
            return
        if t == "speech_resume":
            self._pi_pause = None
            return
        frame, last = ctrl.get("frame"), ctrl.get("last_voiced_frame")
        if not (
            type(frame) is int and frame == self._utt_frames
            and type(last) is int and 0 <= last < frame
        ):
            log.debug(
                "stream %s: ignoring a speech_pause for frame %r (buffer has %d)",
                self.room_id, frame, self._utt_frames,
            )
            self._pi_pause = None
            return
        self._pi_pause = (frame, last)
        self._pause_rx = time.perf_counter()
        self._pause_clock = _sat_clock(ctrl, "sat_ms", "backlog_ms")
        self._hint_greeting = bool(ctrl.get("greeting_played"))
        clip = ctrl.get("greeting_clip")
        self._hint_greeting_clip = clip if self._hint_greeting and isinstance(clip, str) else None
        self._hint_ack_before = ctrl.get("ack_before_capture") is True
        self._on_pause()
        self._maybe_commit()

    def _last_voiced_frame(self, ctrl: dict[str, Any]) -> int | None:
        """The index of the capture's last voiced frame, when it can be
        known exactly (see ``endpointing``), else None."""
        if self.dropped_overflow:
            return None
        if "last_voiced_frame" in ctrl:
            # The satellite says so. Its frame count must agree with ours,
            # or the numbers aren't about the same frames.
            frames, last = ctrl.get("frames"), ctrl.get("last_voiced_frame")
            if (
                type(frames) is int and frames == self._utt_frames
                and type(last) is int and 0 <= last < frames
            ):
                return last
            return None
        cfg = self.ws.app.state.satellite_config.get(self.room_id) or {}
        timeout = cfg.get("listen.silence_timeout")
        if timeout is None:
            return None
        return last_voiced_from_timeout(
            self._utt_frames, timeout, cfg.get("listen.max_record_seconds"),
        )

    def _reusable_speculation(self, last_voiced: int | None) -> _Speculation | None:
        """The utterance's latest copy, if every voiced frame is in it."""
        spec = self._spec
        if (
            spec is None
            or spec.serial != self._utt_serial
            or last_voiced is None
            or last_voiced >= spec.frames
        ):
            return None
        return spec

    # ── Early commit (early endpointing, part B) ──────────────────────

    def _early_commit_possible(self, trigger: str | None) -> bool:
        """Whether an utterance starting now may be ended early: see the
        module docstring for every condition that must hold."""
        return bool(
            settings.early_commit_enabled
            and self._capture_control
            and self._speech_hints
            and self._spec_on
            and trigger in EARLY_COMMIT_TRIGGERS
            and self._utt_id is not None
            and self.dropin_peer is None
            and self.wake_recording is None
            and not self.conversational_mode
        )

    async def _read_commit_context(self) -> tuple[Any, bool] | None:
        """What the router dry run needs from the session: the parked
        ``pending_confirmation`` (a yes/no answer resumes it) and whether
        the session is in chat mode (then nothing commits early). A read,
        nothing more; None when it can't be read — then nothing commits
        early either."""
        if self.session_id is None:
            return None, False
        try:
            async with session_scope() as s:
                ctx = await SessionRepository(s).get_context(self.session_id) or {}
        except Exception as e:
            log.debug("stream %s: early commit can't read the session: %s", self.room_id, e)
            return None
        return ctx.get("pending_confirmation"), bool(ctx.get("conversational_mode"))

    def _commit_decision(self, spec: _Speculation, heard: _Heard) -> tuple[str, int] | None:
        """(tier, hold_ms) when this copy's transcript may end the capture
        early, else None. Judged once per copy — except while the session
        read is still out, when the answer is "not yet"."""
        if spec.commit is not None:
            return spec.commit or None
        ctx_task = self._commit_ctx_task
        if ctx_task is None or not ctx_task.done():
            return None
        context = None
        if not ctx_task.cancelled() and ctx_task.exception() is None:
            context = ctx_task.result()
        decision: tuple[str, int] | tuple[()] = ()
        if context is not None and not context[1]:
            text = heard.text
            # This runs inside the socket's receive loop (an audio frame, a
            # hint) or a done-callback: whatever goes wrong here — a parked
            # confirmation payload of an odd shape, say — means "don't end
            # early", never a dropped satellite connection.
            try:
                greeting_only = False
                if self._hint_greeting:
                    # Screened exactly as the turn will screen it. A copy
                    # that is nothing but the wake greeting coming back
                    # through the mic never ends the capture — the turn
                    # would drop it, and the request the person makes after
                    # the greeting would go into a closed microphone. (The
                    # greeting "Yes?" would otherwise answer a parked
                    # question.) A later copy, with the request in it, is
                    # judged afresh.
                    text, greeting_only = self._screen_greeting(
                        text, self._hint_greeting_clip,
                        before_capture=self._hint_ack_before,
                    )
                ec = None if greeting_only else early_commit_for(text, pending=context[0])
                if ec is not None:
                    if ec.tier == TIER_A:
                        decision = (ec.tier, int(settings.early_commit_hold_a_ms))
                    elif settings.early_commit_tier_b:
                        decision = (ec.tier, int(settings.early_commit_hold_b_ms))
            except Exception as e:
                log.warning(
                    "stream %s: early-commit check failed; this capture runs to "
                    "the satellite's own silence timeout: %s", self.room_id, e,
                )
                decision = ()
        spec.commit = decision
        return decision or None

    def _maybe_commit(self) -> None:
        """End the capture now if everything allows it: a finished copy of
        this utterance whose transcript commits early, every voiced frame
        inside it, and the satellite's own detector silent for the hold."""
        if not (self._commit_on and self.utterance_active) or self.dropped_overflow:
            return
        pause, spec = self._pi_pause, self._spec
        if pause is None or spec is None or spec.serial != self._utt_serial:
            return
        task = spec.task
        if task is None or not task.done() or task.cancelled() or task.exception() is not None:
            return
        heard = task.result()
        last_voiced = pause[1]
        if heard is None or last_voiced >= spec.frames:
            return
        decision = self._commit_decision(spec, heard)
        if decision is None:
            return
        tier, hold_ms = decision
        silence_ms = (self._utt_frames - 1 - last_voiced) * FRAME_MS
        if silence_ms < hold_ms:
            return
        self._commit_early(spec, tier=tier, hold_ms=hold_ms, silence_ms=silence_ms)

    def _commit_early(
        self, spec: _Speculation, *, tier: str, hold_ms: int, silence_ms: int
    ) -> None:
        """Stop listening: close the utterance exactly as `utterance_end`
        would, and start the turn — which tells the satellite
        (`end_capture`) before anything else."""
        received_at = time.perf_counter()
        self.utterance_active = False
        # No utterance_end to time: the server ended this one itself.
        clock = self._take_clock(end_rx=None, end_sat={})
        # The fast lane's capture ends here too (its lead is measured to
        # the moment listening stopped, whoever stopped it); a command it
        # hadn't decided yet is "preempted", not missed.
        if self._fastlane is not None:
            self._fastlane.finish(ended_at=received_at, preempted=True)
        pcm = bytes(self.audio_buf)
        self.audio_buf.clear()
        trigger = self._utterance_trigger
        self._utterance_trigger = None
        in_call = self._utterance_in_call or self.dropin_peer is not None
        self._utterance_in_call = False
        speculations = self._spec_history
        self._spec = None
        self._spec_history = []
        self._spec_on = False
        self._pause_detector = None
        self._commit_on = False
        committed = _Committed(
            serial=self._utt_serial, utt=self._utt_id, frames=self._utt_frames, tier=tier,
        )
        # For an opted-in room's command recording: the satellite's own
        # utterance_end comes after this, so the sidecar describes the
        # capture as the server ended it — the frames it holds (the whole
        # recording) and the silent run since the last voiced one.
        capture_meta = command_captures.capture_meta(
            {
                "exit_reason": "server_endpoint",
                "frames": committed.frames,
                "trailing_silent_frames": silence_ms // FRAME_MS,
            },
            in_call=in_call,
        )
        self._committed = committed
        log.info(
            "early commit room=%s trigger=%s tier=%s hold_ms=%d silence_ms=%d frames=%d",
            self.room_id, trigger, tier, hold_ms, silence_ms, committed.frames,
        )
        self._response_task = asyncio.create_task(
            self._process_utterance(
                pcm,
                greeting_played=self._hint_greeting,
                trigger=trigger,
                received_at=received_at,
                greeting_clip=self._hint_greeting_clip,
                ack_before_capture=self._hint_ack_before,
                reuse=spec,
                speculations=speculations,
                endpoint_silence_ms=silence_ms,
                early_commit=committed,
                hold_ms=hold_ms,
                capture_meta=capture_meta,
                capture_clock=clock,
            )
        )

    def _note_late_utterance_end(self, ctrl: dict[str, Any]) -> None:
        """The satellite's own `utterance_end` for a capture this server
        already ended. Nothing to do but record whether the person was still
        talking — the misfire the hold is meant to prevent, made visible.

        Two kinds of evidence. The frames already on their way when the
        satellite stopped (its `last_voiced_frame` past the server's count:
        ``post_commit_voiced_ms``) cover only the first 30-90 ms. A
        satellite from 2026-09-30 on also reads its mic on, sending nothing,
        until its own silence timeout would have ended the capture, and
        says whether speech came back (``voiced_after_end_ms``) and how long
        it listened (``listened_after_end_ms``): recorded past the server's
        stop as ``post_commit_resume_ms`` / ``post_commit_listened_ms``.
        An older satellite sends neither, and then a pause the hold misjudged
        by more than ~90 ms is never seen."""
        committed = self._committed
        if committed is None or ctrl.get("utt") != committed.utt:
            return
        self._committed = None
        last, frames = ctrl.get("last_voiced_frame"), ctrl.get("frames")
        # Satellite-supplied: whole frame counts within an hour of audio
        # (the bound `_sat_clock` puts on its clock numbers), or nothing is
        # recorded. Unbounded, one frame could write a number no float holds
        # into the turn's timings, and the open latency summary read it.
        if not (_frame_count(last) and _frame_count(frames)):
            return
        late: dict[str, int] = {
            "post_commit_voiced_ms": max(0, last - committed.frames + 1) * FRAME_MS,
        }
        # Frames the satellite sent after the server's count: the watch
        # starts where they end.
        in_flight_ms = max(0, frames - committed.frames) * FRAME_MS
        listened = _after_end_ms(ctrl.get("listened_after_end_ms"))
        if listened is not None:
            late["post_commit_listened_ms"] = in_flight_ms + listened
            resumed = _after_end_ms(ctrl.get("voiced_after_end_ms"))
            if resumed:
                late["post_commit_resume_ms"] = in_flight_ms + resumed
        timings = committed.timings
        if timings is not None:
            timings.stages.update(late)
            if timings.intents_log_id is not None:
                # The turn's row exists, and its post-route write may already
                # have gone (the watch holds this message back by up to the
                # satellite's silence timeout): write these on their own.
                self._merge_late_stages(timings.intents_log_id, late)
        voiced_after_ms = late["post_commit_voiced_ms"]
        resume_ms = late.get("post_commit_resume_ms")
        if voiced_after_ms:
            log.warning(
                "early commit room=%s tier=%s cut in on speech: the satellite "
                "heard speech %d ms past the point the server stopped "
                "listening (%d frames sent, the server had %d). If this "
                "repeats, turn early_commit_tier_b off or raise the hold.",
                self.room_id, committed.tier, voiced_after_ms, frames, committed.frames,
            )
        elif resume_ms:
            log.warning(
                "early commit room=%s tier=%s cut in on speech: the person "
                "spoke again %d ms after the server stopped listening, before "
                "the satellite's own silence timeout would have ended the "
                "capture. If this repeats, turn early_commit_tier_b off or "
                "raise the hold.",
                self.room_id, committed.tier, resume_ms,
            )

    def _merge_late_stages(self, row_id: int, late: dict[str, int]) -> None:
        """Write a committed turn's late stages into its row, in a task of
        its own (held like the post-route write, best-effort)."""
        write = asyncio.create_task(
            self._write_post_route_timings(row_id, dict(late)),
            name=f"turn-timings-late:{self.room_id}",
        )
        _TIMING_WRITES.add(write)
        write.add_done_callback(_TIMING_WRITES.discard)

    async def _await_hello(self) -> bool:
        """Block until the client's FIRST frame arrives and is an accepted
        `hello`.

        Returns True once the hello handler (``_on_control`` → pairing check
        + per-room caches) has accepted it, so the caller may provision,
        register and send `ready`. Returns False when the socket should be
        dropped with nothing created: no frame within
        ``settings.satellite_hello_timeout_sec``, a first frame that is not a
        hello, a pairing refusal (the hello handler already sent the error
        and closed), or a disconnect. Every False path leaves the socket
        closed and touches no app.state.
        """
        timeout = float(settings.satellite_hello_timeout_sec)

        async def _first_frame() -> dict[str, Any] | None:
            msg = await self.ws.receive()
            if msg.get("type") == "websocket.disconnect":
                return None
            text = msg.get("text")
            if text is None:
                # Binary before hello — nobody we know streams audio first.
                return {"type": "<binary>"}
            try:
                ctrl = json.loads(text)
            except json.JSONDecodeError:
                return {"type": "<invalid json>"}
            return ctrl if isinstance(ctrl, dict) else {"type": "<non-object>"}

        try:
            # One deadline around the whole wait (not per receive) so a
            # cancelled receive can never swallow a frame we then act on.
            ctrl = await asyncio.wait_for(_first_frame(), timeout)
        except asyncio.TimeoutError:
            log.warning(
                "ws %s sent no hello within %.1fs; closing without "
                "provisioning or registering the room",
                self.room_id, timeout,
            )
            await self._close_quietly(1008)
            return False
        except WebSocketDisconnect:
            return False
        if ctrl is None:
            log.info("ws %s disconnected before hello", self.room_id)
            return False
        if ctrl.get("type") != "hello":
            log.warning(
                "ws %s first frame was %r, not hello; closing",
                self.room_id, ctrl.get("type"),
            )
            await self._safe_send_text({
                "type": "error",
                "reason": "hello_required",
                "message": "first frame must be hello",
            })
            await self._close_quietly(1008)
            return False
        # The existing hello handler: pairing check (V002) + per-room
        # caches. Its semantics are unchanged — tokenless legacy accept
        # when strict pairing is off, approval parking, refusals — it just
        # now runs BEFORE anything is provisioned or registered.
        await self._on_control(ctrl)
        if self._pairing_refused:
            # The hello handler sent the error frame and closed the socket.
            return False
        return True

    async def _close_quietly(self, code: int) -> None:
        try:
            await self.ws.close(code=code)
        except Exception:
            # Already gone; nothing useful to do.
            pass

    def _hello_source(self) -> str:
        """Rate-limit identity for this connection.

        Same rule as :func:`admin_auth.request_source`: the forwarded
        address counts only when the peer itself is trusted, because
        otherwise it is a header anyone can rotate for a fresh bucket. A
        stand-in socket with no peer at all keys on ``"unknown"`` rather
        than raising — a throttle must never be the thing that breaks a
        connection path."""
        try:
            client = getattr(self.ws, "client", None)
            peer = getattr(client, "host", None) or "unknown"
            headers = getattr(self.ws, "headers", None) or {}
            fwd = headers.get("x-forwarded-for") if hasattr(headers, "get") else None
            if fwd and peer in TRUSTED_PROXIES:
                return fwd.split(",")[0].strip()
            return peer
        except Exception:  # pragma: no cover — defensive
            return "unknown"

    async def _park_for_approval(
        self,
        s: Any,
        ctrl: dict[str, Any],
        *,
        token_hash: str,
        code: str | None,
    ) -> bool:
        """Park an unpaired room's first connect for a human to approve.

        Always returns False: parking is not an accepted connection. The
        device is told why, and with which code, so it can say the code out
        loud — the customer reads it off the unit and types it into the
        dashboard, which is what ties the row on screen to the device in
        the room.

        Four things guard this path:

        * a per-source-IP budget, so one address cannot fill the approvals
          list with room names;
        * a code the device brings must be one the operator could ever
          type — exactly six ASCII digits — or nothing is parked (CORE-22);
        * :meth:`SatelliteApprovalRepository.request` refusing a device
          that is not the one already parked under this room name, for
          :data:`APPROVAL_TAKEOVER_SEC`; after that a different device's
          request replaces the row, token and code together, so being first
          with an unapprovable request no longer holds a room name;
        * a code minted here when the device brought none, so there is
          always something for the operator to match.
        """
        if code is not None and not _is_approval_code(code):
            # Refused before the budget is spent and before anything is
            # written: a row with this code could never be approved (the
            # approve route takes digits only), so parking it would only
            # take the room name away from the device that IS there.
            log.warning(
                "pairing: room=%s approval request refused — the code it "
                "brought is not %d digits",
                self.room_id, APPROVAL_CODE_DIGITS,
            )
            await self._safe_send_text({
                "type": "error",
                "reason": "approval_code_invalid",
                "message": (
                    f"an approval code is {APPROVAL_CODE_DIGITS} digits — "
                    "re-run the satellite's setup to get a new one"
                ),
            })
            return False
        if not APPROVAL_HELLO_LIMITER.allow(self._hello_source()):
            log.warning(
                "pairing: room=%s approval request throttled for this source",
                self.room_id,
            )
            await self._safe_send_text({
                "type": "error",
                "reason": "approval_throttled",
                "message": "too many approval requests — try again shortly",
            })
            return False
        if not code:
            from domovoi.satellite_media.overlay import generate_approval_code

            code = generate_approval_code()
        approvals = SatelliteApprovalRepository(s)
        outcome, parked_code = await approvals.request(
            self.room_id,
            token_hash=token_hash,
            code=code,
            mac=ctrl.get("mac"),
            board=ctrl.get("board"),
            sat_type=ctrl.get("sat_type") or "voice",
            takeover_after_sec=APPROVAL_TAKEOVER_SEC,
        )
        if outcome == "conflict":
            log.warning(
                "pairing: room=%s REFUSED — a different device is already "
                "waiting for approval under that room name",
                self.room_id,
            )
            await self._safe_send_text({
                "type": "error",
                "reason": "approval_conflict",
                "message": (
                    "another device is already waiting for approval for this "
                    "room — reject that request on the dashboard, or wait "
                    f"{int(APPROVAL_TAKEOVER_SEC // 60)} minutes and this one "
                    "takes its place"
                ),
            })
            return False
        log.info(
            "pairing: room=%s awaiting approval (code shown to the operator)",
            self.room_id,
        )
        # The code on FILE rides back, not the one just offered: on a retry
        # the row keeps the code the customer was already shown, and the
        # device must say that one. It lets a device that minted no code of
        # its own say the core's. The socket closes straight after and
        # nothing is provisioned.
        await self._safe_send_text({
            "type": "error",
            "reason": "awaiting_approval",
            "message": "waiting for approval on the dashboard",
            "code": parked_code or code,
        })
        return False

    async def _validate_pairing(self, ctrl: dict[str, Any]) -> bool:
        """Pairing-token WS auth for the hello frame (V002).

        Returns True to ACCEPT the connection, False to REFUSE (the caller
        closes the socket). Only the sha256 of a token is ever compared or
        stored — reusing ``admin_auth.token_sha256`` — so the raw token never
        leaves the Pi's sidecar. The five cases:

          1. token, no pairing row  -> PARK for approval when the device
                                       brings an approval code, or when
                                       ``settings.satellite_pairing_strict``
                                       is on (every first pairing is then a
                                       decision someone makes); otherwise
                                       PAIR (claim the room), accept
          2. token, row hash match  -> accept, bump last_seen_at
          3. token, row hash MISMATCH -> REFUSE (impostor / wrong token)
          4. no token, row EXISTS    -> REFUSE (a paired room requires its token)
          5. no token, no row        -> accept (older/unpaired satellite),
                                        UNLESS ``settings.satellite_pairing_strict``
                                        is on, then REFUSE. Parking needs a
                                        token to bind to, so a tokenless
                                        hello is refused rather than parked.

        Only cases 1 (paired) and 2 set ``token_authenticated``; a socket
        accepted by case 5, or by the lenient fallback when the check itself
        fails, announces only its own room's timers and reminders.

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
                        # Case 1 — nobody has claimed this room yet.
                        #
                        # A device that presents an approval_code came through
                        # the Wi-Fi setup portal, where the core was never a
                        # participant and so could not preseed a pairing. It
                        # parks for a human instead of claiming the room on
                        # trust: the customer matches the code they saw as the
                        # setup network closed. Without that, whoever guesses a
                        # room_id and connects first wins it.
                        #
                        # A device with NO code is the manual/legacy path and
                        # keeps the historical trust-on-first-use behaviour —
                        # upgrading the server must not strand satellites that
                        # were provisioned by hand.
                        #
                        # CORE-9 — unless strict pairing is on, and then
                        # EVERY first pairing for an unpaired room is a
                        # decision a person makes: bringing a token proves
                        # only that you have a token, not that you are the
                        # device in that room. The core mints a code for a
                        # device that brought none, and the device says it
                        # out loud. Strict is the default (CORE-11);
                        # lenient is only ever a household's own
                        # SATELLITE_PAIRING_STRICT=false.
                        code = ctrl.get("approval_code")
                        code = code.strip() if isinstance(code, str) else None
                        if code or settings.satellite_pairing_strict:
                            return await self._park_for_approval(
                                s, ctrl, token_hash=token_hash, code=code
                            )
                        await repo.pair(self.room_id, token_hash)
                        log.info(
                            "pairing: room=%s paired (trust-on-first-use)",
                            self.room_id,
                        )
                        self.token_authenticated = True
                        return True
                    if secrets.compare_digest(row[0], token_hash):
                        # Case 2 — token matches the paired hash.
                        await repo.touch_last_seen(self.room_id)
                        self.token_authenticated = True
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
                # Accepted, but not token-authenticated: it hears only its
                # own room's timers and reminders.
                log.info(
                    "pairing: room=%s connected without a token "
                    "(older/unpaired; strict pairing off)",
                    self.room_id,
                )
                return True
        except Exception as e:
            # A DB hiccup must not silently strip auth from a paired room, but
            # it also must not break a tokenless older fleet. Bias to the
            # configured posture: strict (the default) → fail closed;
            # lenient (the household opted out) → keep a tokenless older
            # fleet working and accept, unauthenticated.
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
                await self._close_quietly(1008)
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
            # Optional, absent on older clients: this satellite reports its
            # own pauses and last voiced frame, and honours `end_capture`
            # (see the module docstring).
            self._speech_hints = bool(ctrl.get("speech_pause", False))
            self._capture_control = bool(ctrl.get("capture_control", False))
            # A satellite from before 06f486b drops the audio of every
            # announcement after a reconnect (its stop_playback latch stays
            # set) while the core records it as spoken — and a reconnect is
            # exactly when a timer's grace delivery lands. Its hello lacks
            # this flag; say so once per connection, next to the release
            # note's "Upgrade each satellite".
            if not ctrl.get("announce_after_session_end"):
                log.warning(
                    "room %s runs satellite code without the announcement fix: "
                    "timer and reminder announcements after a reconnect will be "
                    "silent until %s is upgraded", self.room_id, self.room_id,
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
            self._clock = _CaptureClock(
                start_rx=time.perf_counter(),
                sat_start=_sat_clock(ctrl, "backlog_ms", "wake_ms"),
            )
            if self._response_task and not self._response_task.done():
                self._response_task.cancel()
            self._cut_announcement()
            # The question a reply asked is being answered (or talked over).
            self._followup_hold_until = 0.0
            self._last_audio_at = time.monotonic()
            self.utterance_active = True
            self.dropped_overflow = False
            self.audio_buf.clear()
            # Latch the trigger now; utterance_end reads it to decide
            # clip-vs-command (immune to wake_recording flipping mid-utterance).
            self._utterance_trigger = ctrl.get("trigger")
            self._utterance_in_call = self.dropin_peer is not None
            self._utt_id = ctrl.get("utt")
            self._reset_utterance_tracking(self._utterance_trigger)
            self._fastlane = fast_lane.open_capture(
                self.room_id, self._utterance_trigger,
                previous=self._fastlane, chat=self.conversational_mode,
            )
            return
        if t in ("speech_pause", "speech_resume"):
            # Pause hints from a satellite that declared them in its hello
            # (and only ever sent because `ready.features` listed them).
            # Anything stale or from an undeclared sender is dropped quietly
            # — never the unknown-type error, which ends the turn on a Pi.
            self._on_speech_hint(t, ctrl)
            return
        if t == "utterance_end":
            if not self.utterance_active:
                # Late for a capture this server ended early (or a stray).
                self._note_late_utterance_end(ctrl)
                return
            # The turn's clock starts here: everything the person waits
            # for after the satellite stops listening (turn_timings.total_ms).
            received_at = time.perf_counter()
            self.utterance_active = False
            clock = self._take_clock(
                end_rx=received_at, end_sat=_sat_clock(ctrl, "sat_ms", "backlog_ms"),
            )
            if self._fastlane is not None:
                self._fastlane.finish(ended_at=received_at)
            pcm = bytes(self.audio_buf)
            self.audio_buf.clear()
            trigger = self._utterance_trigger
            self._utterance_trigger = None
            in_call = self._utterance_in_call or self.dropin_peer is not None
            self._utterance_in_call = False
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
            # Exactly which frame was the last word, when that can be known,
            # and whether this utterance's early transcript covers it.
            last_voiced = self._last_voiced_frame(ctrl)
            endpoint_silence_ms = (
                (self._utt_frames - 1 - last_voiced) * FRAME_MS
                if last_voiced is not None
                else None
            )
            reuse = self._reusable_speculation(last_voiced)
            speculations = self._spec_history
            self._spec = None
            self._spec_history = []
            self._spec_on = False
            self._pause_detector = None
            # The Pi flags turns where it played a wake greeting; if so we
            # strip a bled-in greeting (past the AEC) from the transcript
            # before routing, or drop a turn that is nothing but the
            # greeting. A satellite that knows which clip it played names it
            # (`greeting_clip`), so only that line is matched. One that
            # played it BEFORE the capture opened says so
            # (`ack_before_capture`): the strip still runs, the drop doesn't.
            greeting_played = bool(ctrl.get("greeting_played"))
            greeting_clip = ctrl.get("greeting_clip")
            ack_before_capture = ctrl.get("ack_before_capture") is True
            # Why the capture ended, its frame counts and whether the room
            # was in a call at any point of it — for an opted-in room's
            # command recording (domovoi/command_captures.py). Old
            # satellites send none of the capture fields.
            capture_meta = command_captures.capture_meta(ctrl, in_call=in_call)
            self._response_task = asyncio.create_task(
                self._process_utterance(
                    pcm, greeting_played=greeting_played, trigger=trigger,
                    received_at=received_at,
                    greeting_clip=greeting_clip if isinstance(greeting_clip, str) else None,
                    ack_before_capture=ack_before_capture,
                    reuse=reuse,
                    speculations=speculations,
                    endpoint_silence_ms=endpoint_silence_ms,
                    capture_meta=capture_meta,
                    capture_clock=clock,
                )
            )
            return
        if t == "barge_in":
            if self._response_task and not self._response_task.done():
                self._response_task.cancel()
            self._cut_announcement()
            # The satellite stops playback and drains its queue on a barge.
            self._playout_until = time.monotonic()
            return
        if t == "music_ready":
            # Pi's mpg123 has primed against MPD's silence stream and
            # is ready for song frames. Cancel the fallback timer and
            # resume MPD now.
            await consume_music_ready(self.ws.app, self.room_id)
            return
        if t == "music_failed":
            # The Pi's stream player was refused (or dropped) and it has
            # stopped retrying. Sent only because `ready.features` lists it.
            await consume_music_failed(self.ws.app, self.room_id, ctrl)
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
            # Load that voice now, off the event loop, so the room's first
            # reply doesn't pay the model load (domovoi/speech_warmup.py).
            from domovoi.speech_warmup import schedule_room_voice_warm_up

            schedule_room_voice_warm_up(str(voice) if voice else None)
            return
        if t == "logs_chunk":
            # One slice of a log pull we asked for. Reassembly is sync and
            # cheap; the request's future resolves on the `final` chunk.
            self._on_logs_chunk(ctrl)
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
            committed = self._committed
            if committed is not None and committed.serial == self._utt_serial:
                # This server already ended the capture and is answering
                # it; a satellite that honours end_capture skips its noise
                # check on that exit, so this is a stray — the answer stands.
                log.info(
                    "stream %s: ignoring noisy_capture for a capture the server "
                    "already ended", self.room_id,
                )
                return
            if self._response_task and not self._response_task.done():
                self._response_task.cancel()
            self.utterance_active = False
            self.audio_buf.clear()
            self.dropped_overflow = False
            self._discard_speculation()
            self._clock = None
            self._fastlane = fast_lane.close(self._fastlane)
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
        await self._speak_system_line(text, matched_handler="noisy_capture")

    async def _respond_stt_unavailable(
        self, error: SttUnavailableError, *, trigger: str | None = None
    ) -> None:
        """Speech recognition failed to load at boot (see
        domovoi/clients/whisper.py), so there is no transcript to route.

        Say so out loud rather than ending the turn in silence — a room
        that just goes quiet after its wake word reads as a broken
        satellite, when the fix is a setting on the Domovoi server. Like
        the noisy-capture apology this is a system message: no router, no
        ``intents_log`` row, no follow-up armed. The turn always ends with
        ``response_end`` — including when TTS fails too — so the Pi's mic
        is never left parked.

        A barge-in turn ends quietly instead. Its capture opened while the
        speaker was playing — most likely this very notice — and with no
        transcript the self-echo guard can't tell the satellite hearing
        itself from a person, so speaking again would let the echo re-trip
        barge-in and replay the notice in a loop."""
        log.warning(
            "stream %s: speech recognition is unavailable — dropping the "
            "utterance (fix the Whisper settings and restart the core): %s",
            self.room_id, str(error).splitlines()[0] if str(error) else "",
        )
        if trigger == "barge_in":
            await self._safe_send_text({
                "type": "response_end",
                "interrupted": True,
                "expect_followup": False,
            })
            return
        text = (
            "Sorry, I can't understand speech right now. Speech recognition "
            "didn't start on the Domovoi server. The Models page in the "
            "dashboard says why."
        )
        if not await self._speak_system_line(text, matched_handler="stt_unavailable"):
            await self._safe_send_text({
                "type": "response_end",
                "interrupted": True,
                "expect_followup": False,
            })

    def _greeting_candidates(self, clip: str | None) -> tuple[list[str], bool]:
        """The greeting lines a greeting-played transcript is checked
        against, and whether that is the one line known to have played.
        A satellite that names its clip (``greeting_clip``, the rendered
        file ``greet_<hash>.mp3``) gets exactly that clip's text; an older
        satellite, or a clip this server's bank doesn't know, gets the
        whole enabled bank as before."""
        state = self.ws.app.state
        text = (getattr(state, "greeting_clips", None) or {}).get(clip) if clip else None
        if text:
            return [text], True
        return list(getattr(state, "greeting_phrases", None) or []), False

    def _screen_greeting(
        self, transcript: str, clip: str | None, *, before_capture: bool = False,
    ) -> tuple[str, bool]:
        """A greeting-played transcript with a bled-in wake greeting
        stripped off its front, and whether it is nothing BUT that greeting
        (``greeting_filter.is_greeting_only``) — Domovoi's own words, never
        a command. Matched against ``_greeting_candidates(clip)``. Pure:
        the turn (``_clean_transcript``) and the early-commit check
        (``_commit_decision``) both screen with it, so a transcript the
        turn would drop is never one a capture is ended early for.

        Only a transcript nothing was stripped from can be the greeting
        alone. The greeting plays once: when it has been found at the front
        and stripped, what follows it was said after it — by the person —
        even if those words are also a greeting line ("Yes? Yes." is the
        greeting "Yes?" and then a "yes" answering a parked question).

        ``before_capture`` (the satellite's ``ack_before_capture``): the
        greeting finished before the capture opened, and the satellite
        dropped what its mic heard under it. Such a capture cannot hold the
        whole greeting, so it is never "only the greeting" — "Yes." after
        the greeting "Yes?" is the person answering. The strip still runs:
        it is the safety net for an echo tail that outlasted the satellite's
        drain, and its clause-boundary rule keeps it off a sentence that
        merely begins like a greeting."""
        from domovoi.greeting_filter import is_greeting_only, strip_leading_greeting

        phrases, played = self._greeting_candidates(clip)
        if not phrases:
            return transcript, False
        cleaned = strip_leading_greeting(transcript, phrases)
        if cleaned != transcript:
            return cleaned, False
        if before_capture:
            return transcript, False
        return transcript, is_greeting_only(transcript, phrases, played=played)

    async def _speak_system_line(self, text: str, *, matched_handler: str) -> bool:
        """Speak a fixed line that isn't an intent's answer (the
        noisy-capture apology, the STT-unavailable notice): response_start,
        the audio, response_end. Returns False when TTS failed — an
        ``error`` frame has been sent and the caller decides how to end
        the turn; True otherwise (including a Pi that went away
        mid-broadcast, where there is nobody left to tell)."""
        try:
            tts = get_tts_client()
            n_engine, n_voice = await resolve_voice(
                self.ws.app.state.satellite_voice.get(self.room_id)
            )
            pcm, sr = _wav_to_pcm(
                await tts.synthesize(text, engine=n_engine, voice=n_voice)
            )
        except Exception as e:
            log.warning(
                "stream %s: %s TTS failed: %s",
                self.room_id, matched_handler.replace("_", "-"), e,
            )
            await self._safe_send_text({"type": "error", "message": str(e)})
            return False

        await self._safe_send_text({
            "type": "response_start",
            "text": text,
            "matched_handler": matched_handler,
            "matched_path": "system",
            "session_id": str(self.session_id) if self.session_id else None,
            "online": True,
            "audio_sample_rate": sr,
        })
        for chunk in _iter_chunks(pcm):
            try:
                await self.ws.send_bytes(chunk)
            except Exception:
                return True  # Pi went away mid-broadcast
            self._note_audio_sent(len(chunk), sr)
        await self._safe_send_text({
            "type": "response_end",
            "interrupted": False,
            "expect_followup": False,
        })
        return True

    async def _transcribe_turn(
        self,
        pcm_bytes: bytes,
        *,
        timings: TurnTimings,
        trigger: str | None,
        reuse: _Speculation | None,
        speculations: list[_Speculation],
    ) -> _Heard | None:
        """The transcribe half of a turn: the speculative transcript when
        it covers the whole capture (``reuse``, already checked frame by
        frame), else Whisper on the whole buffer. Records the STT stages.
        None when speech recognition is unavailable — the turn has been
        answered already."""
        wait_t0 = time.perf_counter()
        heard: _Heard | None = None
        if reuse is not None and reuse.task is not None:
            # Shielded: a barge-in cancelling this turn must not take the
            # room's decode bookkeeping down with it.
            heard = await asyncio.shield(reuse.task)
        reused = heard is not None
        if heard is None:
            try:
                whisper = get_whisper_client()
            except SttUnavailableError as e:
                # Whisper didn't load at boot. Explain out loud and end the
                # turn; the helper sends its own response_end.
                await self._respond_stt_unavailable(e, trigger=trigger)
                return None
            marks: dict[str, Any] = {}
            text, stt_ms, window = await self._whisper_call(whisper, pcm_bytes, marks=marks)
            if "wait_ms" in marks:
                # Behind a copy of this capture still decoding or being
                # embedded (or a cancelled turn's call): part of
                # stt_wait_ms, not stt_ms.
                timings.flags["stt_decode_wait_ms"] = marks["wait_ms"]
            heard = _Heard(
                text=text, stt_ms=stt_ms, whisper=whisper_block(whisper_runtime()),
                window_s=window,
            )
        timings.stages["stt_ms"] = heard.stt_ms
        timings.stages["stt_wait_ms"] = _elapsed_ms(wait_t0)
        timings.whisper = heard.whisper
        if heard.window_s is not None:
            timings.flags["stt_window_s"] = heard.window_s
        if speculations:
            timings.flags["stt_reused"] = reused
            timings.flags["speculative_decodes"] = len(speculations)
            timings.flags["speculative_ms"] = sum(s.stt_ms or 0 for s in speculations)
        return heard

    async def _clean_transcript(
        self,
        transcript: str,
        *,
        greeting_played: bool,
        trigger: str | None,
        audio_bytes: int,
        greeting_clip: str | None = None,
        ack_before_capture: bool = False,
    ) -> str | None:
        """The clean half of transcription: strip a bled-in greeting and a
        barge-in's own echo, and end a turn with nothing in it — or with
        nothing but the wake greeting. None when the turn has been ended
        here (the satellite got its response_end)."""
        cleaned, drop = self._screen_transcript(
            transcript, greeting_played=greeting_played, trigger=trigger,
            greeting_clip=greeting_clip, ack_before_capture=ack_before_capture,
        )
        if drop is None:
            return cleaned
        if drop == "greeting":
            # Nothing BUT the greeting: the capture closed on the pause
            # after it, before the user said anything (office row #187:
            # "Back so soon?" came back as "Back so soon." and was answered
            # as a command). Domovoi's own words are never a command — end
            # the turn the way the blank guard does: no speech, no DB row,
            # mic released. The same whether the transcript is a
            # speculative copy's or the whole capture's: an early commit
            # never takes such a copy (_commit_decision), and a copy that
            # is reused here says nothing the whole capture wouldn't.
            log.warning(
                "greeting echo: dropping %s turn in room=%s — the "
                "transcript %r is only the wake greeting%s coming "
                "back through the mic. This satellite plays its "
                "greeting over the capture: upgrade it (a current "
                "satellite plays the greeting before it listens), or "
                "check [music] alsa_device.",
                trigger, self.room_id, cleaned,
                f" ({greeting_clip})" if greeting_clip else "",
            )
        elif drop == "echo":
            log.warning(
                "self-echo: dropping barge-triggered turn in room=%s — "
                "transcript %r is the satellite's own reply %r coming "
                "back through the mic. Barge-in is firing on speaker "
                "echo; raise [barge_in] min_speech_ms or set "
                "require_wake_word=true for this room.",
                self.room_id, cleaned, self._last_spoken_text,
            )
        else:
            log.warning(
                "blank capture in room=%s (trigger=%s, %.1fs of audio): "
                "nothing transcribed, not routing. If this repeats after "
                "every wake word the satellite's capture is silent - "
                "check its log for the 'capture ended' stats line.",
                self.room_id, trigger, audio_bytes / (16_000 * 2),
            )
        # End the turn with no speech and no DB row. interrupted=True (not
        # False) because without a response_start this turn the Pi's
        # `_response_audio_received` may still be set from the PREVIOUS
        # response — and the deferred-drain branch would then park the mic
        # thread waiting on a drain that never comes. interrupted=True
        # takes the immediate-release path.
        await self._safe_send_text({
            "type": "response_end",
            "interrupted": True,
            "expect_followup": False,
        })
        return None

    def _screen_transcript(
        self,
        transcript: str,
        *,
        greeting_played: bool,
        trigger: str | None,
        greeting_clip: str | None = None,
        ack_before_capture: bool = False,
    ) -> tuple[str, str | None]:
        """What ``_clean_transcript`` decides, without acting on it: the
        transcript with a bled-in greeting and a barge-in's own echo
        stripped, and why the turn must end instead — "greeting" (nothing
        but the wake greeting), "echo" (the satellite quoting its own
        reply), "blank" (nothing to route) — or None. The second hearing
        screens its transcript with it too."""
        # If the Pi played a wake greeting this turn, the array's AEC may
        # have let it bleed into the capture ("Hi there. Say something
        # mean." → greeting + command). Strip a known leading greeting so
        # only the real command routes. A greeting played before the capture
        # opened (`ack_before_capture`) can't be all there is: see
        # `_screen_greeting`.
        if greeting_played:
            cleaned, greeting_only = self._screen_greeting(
                transcript, greeting_clip, before_capture=ack_before_capture,
            )
            if cleaned != transcript:
                log.info("stripped bled-in greeting: %r → %r", transcript, cleaned)
                transcript = cleaned
            if greeting_only:
                return transcript, "greeting"

        # Self-echo guard. A barge-triggered capture opens WHILE the
        # speaker is still playing and carries the frames that tripped
        # the barge (`_barge_prefix`) into the utterance — so when the
        # thing that tripped it was residual echo rather than a person,
        # what arrives here is the satellite quoting itself. Only barge
        # turns are checked: a wake-word or follow-up capture starts
        # after playback has drained, and gating on the trigger keeps
        # this away from the case where a user genuinely repeats a word
        # the bot just said.
        if trigger == "barge_in" and self._last_spoken_text:
            from domovoi.self_echo_filter import (
                is_self_echo,
                strip_leading_echo,
            )
            if is_self_echo(transcript, self._last_spoken_text):
                return transcript, "echo"
            cleaned = strip_leading_echo(transcript, self._last_spoken_text)
            if cleaned != transcript:
                log.info(
                    "self-echo: stripped leading echo in room=%s: %r → %r",
                    self.room_id, transcript, cleaned,
                )
                transcript = cleaned
        # Nothing to route. A capture that Whisper hears as silence -
        # the user said the wake word and nothing else, the VAD
        # endpointed on a noise burst, or the mic delivered nothing
        # usable - must end HERE. Routed, an empty transcript reaches
        # the LLM fallback, which answers "I didn't quite catch that";
        # the double-check heuristic appends "Want me to check that
        # online?" and arms a follow-up capture, the Pi reopens the mic
        # without a wake word, hears nothing again, and the room loops
        # apology after apology at one LLM call and one TTS per lap.
        # Seen on hardware: every conversation_log row for the room
        # had user_text "" and the same reply. The turn ends with no
        # speech and no DB row. A transcript of punctuation alone (office
        # row #114 was ".") is just as empty.
        if not any(ch.isalnum() for ch in transcript):
            return transcript, "blank"
        return transcript, None

    def _wants_second_hearing(
        self, heard: _Heard, transcript: str, *, early_commit: bool,
    ) -> bool:
        """Whether a turn's transcript is heard again on the 30 s path before
        it is routed: it came from the 10 s window, the capture wasn't ended
        early (an early commit is a closed command by construction), and
        route() would hand it to the tool model (``goes_to_the_tool_model``)
        — likely a command the short decode misheard, or a turn about to
        spend seconds in the tool router anyway. A fast-path command, a
        yes/no answer or a plain question keeps the short window's speed,
        and so does a question about the world
        (``is_question_about_the_world``: "What is the capital of
        France?"), which the streamed router gives up on in ~0.7 s — a
        second decode would add more than that for nothing — and a request
        for a joke or a story however its article was heard
        (``is_request_for_a_story``: "Tell me it a joke."). Not in chat
        mode either: those turns go to the chat agent, not the router.

        Why (2026-09-30 review, small.en on the 333-clip corpus made far-
        field, 252 tier A/B/B1 commands, router-level outcome right):
        30 s / 10 s / 10 s with the review's rule — 202 / 191 / 205,
        206 / 201 / 212 and 165 / 170 / 181 over three noise conditions,
        224 / 222 / 227 on the clean clips; at or above both windows every
        time, for a second decode on 20-34% of captures. This rule scores
        the same as the review's on every condition of that corpus (the
        two skips lost nothing there), hearing 15-34% twice."""
        return (
            not early_commit
            and not self.conversational_mode
            and heard.window_s == SHORT_WINDOW_SEC
            and settings.whisper_short_window_recheck
            and goes_to_the_tool_model(transcript)
            and not is_question_about_the_world(transcript)
            and not is_request_for_a_story(transcript)
        )

    async def _hear_again(
        self,
        pcm_bytes: bytes,
        heard: _Heard,
        transcript: str,
        *,
        timings: TurnTimings,
        trigger: str | None,
        greeting_played: bool,
        greeting_clip: str | None,
        ack_before_capture: bool,
    ) -> tuple[_Heard, str]:
        """The second hearing (``_wants_second_hearing``): the whole capture
        on faster-whisper's 30 s path, in the room's decode slot, screened
        like the first. Returns the hearing the turn goes on with — the new
        one, or the first when the second can't be had, fails, or hears
        nothing usable (the first did hear speech). Records it on the turn:
        ``stt_rechecked``, ``stt_recheck_ms``, the window the text came
        from, and the extra wait in ``stt_ms`` / ``stt_wait_ms``."""
        try:
            whisper = get_whisper_client()
        except SttUnavailableError:
            return heard, transcript
        if not can_decode_full_window(whisper):
            return heard, transcript
        t0 = time.perf_counter()
        try:
            text, recheck_ms, _window = await self._whisper_call(
                whisper, pcm_bytes, full_window=True,
            )
        except Exception as e:  # noqa: BLE001 — the first hearing stands
            log.warning(
                "stream %s: the second hearing failed; routing the first: %s",
                self.room_id, e,
            )
            return heard, transcript
        timings.flags["stt_rechecked"] = True
        timings.flags["stt_recheck_ms"] = recheck_ms
        timings.stages["stt_ms"] = heard.stt_ms + recheck_ms
        timings.stages["stt_wait_ms"] = timings.stages.get("stt_wait_ms", 0) + _elapsed_ms(t0)
        cleaned, drop = self._screen_transcript(
            text, greeting_played=greeting_played, trigger=trigger,
            greeting_clip=greeting_clip, ack_before_capture=ack_before_capture,
        )
        if drop is not None:
            log.info(
                "stream %s: the second hearing heard nothing usable (%s); "
                "routing the first", self.room_id, drop,
            )
            return heard, transcript
        timings.flags["stt_window_s"] = FULL_WINDOW_SEC
        if cleaned != transcript:
            log.info(
                "stream %s: second hearing on the 30 s window: %r -> %r",
                self.room_id, transcript, cleaned,
            )
        return (
            dataclasses.replace(
                heard, text=text, stt_ms=heard.stt_ms + recheck_ms, window_s=FULL_WINDOW_SEC,
            ),
            cleaned,
        )

    async def _process_utterance(
        self,
        pcm_bytes: bytes,
        *,
        greeting_played: bool = False,
        trigger: str | None = None,
        received_at: float | None = None,
        greeting_clip: str | None = None,
        ack_before_capture: bool = False,
        reuse: _Speculation | None = None,
        speculations: list[_Speculation] | None = None,
        endpoint_silence_ms: int | None = None,
        early_commit: _Committed | None = None,
        hold_ms: int | None = None,
        capture_meta: dict[str, Any] | None = None,
        capture_clock: _CaptureClock | None = None,
    ) -> None:
        interrupted = False
        response = None
        transcript = ""
        # Per-stage stopwatch (domovoi/turn_timings.py). `received_at` is the
        # utterance_end arrival (or the early commit); a direct call starts
        # the clock now.
        timings = TurnTimings(started=received_at, audio_bytes=len(pcm_bytes))
        if endpoint_silence_ms is not None:
            timings.stages["endpoint_silence_ms"] = endpoint_silence_ms
        if early_commit is not None:
            # This server ended the capture: tell the satellite first, so
            # it stops listening before the reply starts playing.
            early_commit.timings = timings
            timings.flags["early_commit"] = early_commit.tier
            if hold_ms is not None:
                timings.stages["early_commit_hold_ms"] = hold_ms
            await self._safe_send_text({"type": "end_capture", "utt": early_commit.utt})
        try:
            # ── Transcribe and clean ─────────────────────────────────────
            heard = await self._transcribe_turn(
                pcm_bytes, timings=timings, trigger=trigger,
                reuse=reuse, speculations=speculations or [],
            )
            if capture_clock is not None:
                # When the capture's frames, its pause and its decode
                # happened against the pace it was spoken at — the copy the
                # turn used, else the latest one (turn_timings).
                timings.flags.update(capture_clock.flags(
                    reuse or (speculations[-1] if speculations else None)
                ))
            if heard is None:
                return
            transcript = await self._clean_transcript(
                heard.text, greeting_played=greeting_played, trigger=trigger,
                audio_bytes=len(pcm_bytes), greeting_clip=greeting_clip,
                ack_before_capture=ack_before_capture,
            )
            if transcript is None:
                return
            if self._wants_second_hearing(
                heard, transcript, early_commit=early_commit is not None,
            ):
                heard, transcript = await self._hear_again(
                    pcm_bytes, heard, transcript, timings=timings, trigger=trigger,
                    greeting_played=greeting_played, greeting_clip=greeting_clip,
                    ack_before_capture=ack_before_capture,
                )

            # ── Respond ──────────────────────────────────────────────────
            await self._safe_send_text({"type": "transcript", "text": transcript})

            probe: ConnectivityProbe = self.ws.app.state.probe

            # Pre-router voice identification: embed the utterance, look
            # up a matching `people` row, compute the presence tier so
            # downstream handlers and audit queries know who's talking
            # and how aggressively to prompt for identity. Best-effort —
            # any failure leaves person_id=None / presence_tier="high"
            # rather than blocking the response cycle. A speculative
            # transcript brings the embedding of its copy, computed right
            # after its decode; the matching (and its last_seen and
            # per-room learning) runs here, once per turn, either way.
            from domovoi.voice_identifier import identify
            stage_t0 = time.perf_counter()
            try:
                await self._copy_embedding(heard)
                if heard.embedded:
                    ident = await identify(
                        pcm_bytes, embedding=heard.embedding, room_id=self.room_id,
                    )
                else:
                    ident = await identify(pcm_bytes, room_id=self.room_id)
            except Exception as e:
                log.warning("voice identification failed: %s", e)
                ident = None
            timings.stage("identify_ms", stage_t0)

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
            # Lets the router say a line before a slow stage (a cold model
            # load) instead of leaving the room silent through it.
            interim = _InterimSpeech(self, satellite_voice)
            ctx = Context(
                room_id=self.room_id,
                session_id=self.session_id,
                online=probe.online,
                bot_name=settings.bot_name,
                person_id=person_id,
                presence_tier=presence_tier,
                embedding_bytes=embedding_bytes,
                trigger=trigger,
                wifi_status=wifi_status,
                satellite_volume=satellite_volume,
                voice=satellite_voice,
                app=self.ws.app,
                timings=timings,
                speak_interim=interim.say,
                stream_qa=True,
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
                    # The chat row carries the pre-route stages (see
                    # _persist_chat_turn); nothing to merge afterwards.
                    self._log_turn_timings(timings, trigger=trigger, matched_path="chat")
                    return

            # Shadow only (domovoi/fast_lane.py): what the streaming fast lane
            # would have done, against what Whisper heard, logged and noted
            # on the timings. The routing below is untouched.
            self._fastlane = fast_lane.settle(self._fastlane, transcript, timings)

            stage_t0 = time.perf_counter()
            async with session_scope() as s:
                response = await route(intent, ctx, s)
                streamed = getattr(response, "qa_stream", None) is not None
                if not streamed:
                    # A streamed answer is recorded once it has been said
                    # (_speak_streamed_answer); these hooks run with it.
                    await self._voice_profile_hooks(
                        s, response=response, ctx=ctx, ident=ident,
                        person_id=person_id, transcript=transcript,
                        audio_seconds=audio_seconds,
                    )
            self.session_id = response.session_id

            if streamed:
                # A Q&A answer: its first sentence is spoken while the model
                # is still writing the rest. route_ms ends at that sentence.
                response = await self._speak_streamed_answer(
                    response, ctx=ctx, ident=ident, person_id=person_id,
                    transcript=transcript, audio_seconds=audio_seconds,
                    timings=timings, stage_t0=stage_t0, interim=interim,
                    satellite_voice=satellite_voice,
                )
            else:
                # The whole routing transaction, commit included.
                timings.stage("route_ms", stage_t0)
                await self._speak_response(
                    response, timings=timings, interim=interim,
                    satellite_voice=satellite_voice,
                )
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
            # A turn can fail after its row committed (TTS down): record
            # how far it got.
            timing_write = self._finish_turn_timings(
                timings, trigger=trigger, response=response,
            )
            if timing_write is not None:
                await asyncio.shield(timing_write)
            return

        # The turn's timings, before anything below can be cancelled: the
        # log line now, and the post-route write in a task of its own that
        # runs alongside the end frame and the fan-outs and is awaited
        # last. Its own task because a barge-in cancels this one and the
        # satellite's utterance_start, right behind it, cancels it again —
        # which would land on the write and lose the stages of exactly the
        # turns somebody talked over.
        timing_write = self._finish_turn_timings(timings, trigger=trigger, response=response)
        # An opted-in room's command recording, scheduled here for the same
        # reason: its own task, so the cancels below can't lose it.
        capture_write = self._keep_capture(
            pcm_bytes, trigger=trigger, meta=capture_meta, transcript=transcript,
            response=response, timings=timings, interrupted=interrupted,
        )

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
        # `expect_followup` HOLDS every music_start emission (both the
        # explicit start and the auto-resume) for this turn. The bot just
        # asked the user a question and the satellite is about to capture
        # the wake-word-free reply; respawning mpg123 in that window
        # saturates the Pi's mic, trips noisy_capture, and traps the user
        # in a "having trouble hearing you" loop. The 2026-05-08 12:35
        # incident: "Add that to my library" → "should I add it too?" →
        # music auto-resumed → mic saturated → 4 noisy retries before the
        # user gave up. Music_stop still fires under expect_followup
        # because that's the safe direction. The URL stays in `resumable`,
        # so a followup turn (which will NOT carry expect_followup itself)
        # auto-resumes normally; and the start is held for the room
        # (`hold_music_start`) until the follow-up window has closed, so a
        # question nobody answers — the satellite's follow-up capture times
        # out with no utterance_end, and no turn follows — still gets the
        # music back (it used to stay off until the next wake word).
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
                # Recorded FIRST, as `_admin_dispatch_music` does: the send
                # below can wait up to MUSIC_STREAM_READY_TIMEOUT_SEC for the
                # stream, and a wake word or a barge-in in that wait cancels
                # this turn. Recorded after, a room's first-ever play would
                # then be left paused on the new song with nothing for the
                # next turn to auto-resume.
                resumable[self.room_id] = response.music_stream_url
                # Something new was asked for: a pause from before no
                # longer holds (domovoi/music_pause.py).
                note_paused_by_person(self.ws.app, self.room_id, False)
                if not suppress_music_start:
                    # Handlers prepared MPD paused; the helper makes sure the
                    # stream is serving, sends music_start and arms the
                    # music_ready handshake so MPD resumes once the Pi's
                    # mpg123 has primed against the always-on silence stream.
                    await send_music_start(
                        self.ws.app, self, self.room_id, response.music_stream_url,
                    )
                else:
                    # No music_start to the satellite now means no
                    # music_ready either. Resume MPD immediately so the next
                    # non-followup turn auto-resumes mpg123 against a stream
                    # that's actually emitting song frames instead of an
                    # indefinitely-paused queue.
                    await _resume_mpd_for_room(self.room_id)
                    # And bring the player back once the question's
                    # follow-up window has closed with no turn to do it.
                    self.hold_music_start(response.music_stream_url, "followup")
                # See _admin_dispatch_music for the symmetric comment:
                # now-playing stamps / current_playlist aren't cleared
                # on start because matched_handler isn't a reliable
                # source signal at this layer. The freshness check
                # plus the playback-state sweeper handle invalidation.
            elif response.music_action == "stop":
                # The resume intent goes BEFORE the frame, as the sweeper
                # and `_admin_dispatch_music` do it: a music restart still
                # waiting on this room's stream (an announcement's, see
                # `announce`) checks it last thing before its music_start,
                # and must not find it while this music_stop is on its way.
                resumable.pop(self.room_id, None)
                note_paused_by_person(self.ws.app, self.room_id, False)
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
                await self._safe_send_text({"type": "music_stop"})
            elif self.room_id in resumable and not suppress_music_start:
                # Auto-resume after a non-music turn (e.g. "what time is
                # it" between songs). MPD may be paused (carried over
                # from a prior expect_followup turn that left it queued)
                # or playing — `mpd.resume()` is a no-op in the latter
                # case, so re-running the handshake is safe. A room whose
                # music a person paused gets its player back and stays
                # paused (the handshake checks domovoi/music_pause.py).
                await send_music_start(
                    self.ws.app, self, self.room_id, resumable[self.room_id],
                )
            elif self.room_id in resumable:
                # The turn asked a question: the same resume, held until
                # its follow-up window has closed (see above).
                self.hold_music_start(resumable[self.room_id], "followup")

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
            and self._may_reach_other_rooms("intercom")
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

        # Last, so waiting on the write never delays anything the satellite
        # is waiting for (the end frame, music, a fan-out). Shielded: a
        # cancel here leaves the write to finish on its own.
        if timing_write is not None:
            await asyncio.shield(timing_write)
        if capture_write is not None:
            await asyncio.shield(capture_write)

    async def _voice_profile_hooks(
        self,
        s: Any,
        *,
        response: Any,
        ctx: Context,
        ident: Any,
        person_id: int | None,
        transcript: str,
        audio_seconds: float,
    ) -> None:
        """The voice-profile hooks that run in the transaction that records
        a turn: after route() for a whole reply, after the answer has been
        said for a streamed one (the conversation_log row they may rewrite
        exists only then)."""
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
            # A voice too close to two known people to name is still
            # someone known, not the newcomer being introduced.
            and not ident.ambiguous
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

    async def _speak_response(
        self,
        response: Any,
        *,
        timings: TurnTimings,
        interim: "_InterimSpeech",
        satellite_voice: str | None,
    ) -> None:
        """Speak a routed response whose text is complete: synthesize the
        first sentence, open the response, stream it, and synthesize each
        next sentence while the one before it is sent."""
        tts_t0 = time.perf_counter()
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

        if interim.started:
            # An interim line (the cold-start notice) already opened
            # this turn's response: the reply carries on in it, at the
            # rate the Pi is already playing, with no second
            # response_start. The self-echo guard hears both.
            if sr != interim.sample_rate:
                first_pcm = _resample_pcm(first_pcm, sr, interim.sample_rate)
                sr = interim.sample_rate
            self._last_spoken_text = f"{interim.text} {response.text}"
        else:
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
                self._note_audio_sent(len(chunk), sr)
                # First call only: tts_first_ms + total_ms.
                timings.first_audio(tts_t0)

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
                    self._note_audio_sent(len(chunk), sr)
                    # A first sentence that rendered to no audio at all.
                    timings.first_audio(tts_t0)
        finally:
            # Cancel any in-flight synth on early exit (barge-in,
            # WS drop, exception) so we don't leak a background
            # task waiting on edge-tts to return audio nobody will
            # ever play.
            if next_task is not None and not next_task.done():
                next_task.cancel()

    async def _speak_streamed_answer(
        self,
        routed: Any,
        *,
        ctx: Context,
        ident: Any,
        person_id: int | None,
        transcript: str,
        audio_seconds: float,
        timings: TurnTimings,
        stage_t0: float,
        interim: "_InterimSpeech",
        satellite_voice: str | None,
    ) -> Any:
        """Speak a Q&A answer while the model is still writing it
        (``routed.qa_stream``, domovoi/spoken_answer.py): each sentence is
        synthesized and sent as soon as it is complete, so the first one
        plays while the rest is generated. On a CPU host that is the
        difference between ~0.5 s and the whole reply (0.7-6 s on
        llama3.2:3b) before the first word.

        When the answer has been said, the turn is recorded — the full
        text, the online-check or memory offer appended and parked, the
        voice-profile hooks — and whatever that added is spoken last. A
        barge-in (or a failure) mid-answer stops the model and still
        records what was said. Returns the turn's final response.

        ``route_ms`` runs to the first sentence (the routing plus the time
        the model took to write it); ``tts_first_ms`` from there to its
        audio on the socket."""
        streamed = routed.qa_stream
        tts: Any = None
        engine: str | None = None
        voice: str | None = None
        # An interim line (the cold-start notice) already opened the
        # response: carry on in it, at its rate.
        rate: int | None = interim.sample_rate if interim.started else None
        said: list[str] = [interim.text] if interim.started else []
        tts_t0: float | None = None
        recording: asyncio.Task[tuple[Any, str]] | None = None

        def first_sentence_ready() -> None:
            nonlocal tts_t0
            if tts_t0 is None:
                timings.stage("route_ms", stage_t0)
                tts_t0 = time.perf_counter()

        async def speak(sentence: str) -> None:
            nonlocal rate
            pcm, s_sr = _wav_to_pcm(
                await tts.synthesize(sentence, engine=engine, voice=voice)
            )
            if rate is None:
                rate = s_sr
                await self._safe_send_text({
                    "type": "response_start",
                    "text": sentence,
                    "matched_handler": routed.matched_handler,
                    "matched_path": routed.matched_path,
                    "session_id": str(routed.session_id) if routed.session_id else None,
                    "online": routed.online,
                    "audio_sample_rate": s_sr,
                })
            elif s_sr != rate:
                pcm = _resample_pcm(pcm, s_sr, rate)
            said.append(sentence)
            # What a barge-triggered capture may hear back
            # (self_echo_filter): everything said so far, not just the
            # sentence that opened the response.
            self._last_spoken_text = " ".join(said)
            for chunk in _iter_chunks(pcm):
                await self.ws.send_bytes(chunk)
                self._note_audio_sent(len(chunk), rate)
                timings.first_audio(tts_t0)

        async def record() -> tuple[Any, str]:
            # The turn's records, in a task of its own awaited through
            # asyncio.shield: a barge-in's cancel — or the next
            # utterance_start's, right behind it — must not abort the
            # transaction and lose the turn. Started once, whichever way
            # the answer ends.
            nonlocal recording
            if recording is None:
                recording = asyncio.create_task(
                    self._finish_streamed_answer(
                        streamed, ctx=ctx, ident=ident, person_id=person_id,
                        transcript=transcript, audio_seconds=audio_seconds,
                    ),
                    name=f"qa-answer-record:{self.room_id}",
                )
                _TIMING_WRITES.add(recording)
                recording.add_done_callback(_TIMING_WRITES.discard)
            return await asyncio.shield(recording)

        sentences = streamed.answer.sentences()
        try:
            tts = get_tts_client()
            engine, voice = await resolve_voice(satellite_voice)
            async for sentence in sentences:
                first_sentence_ready()
                await speak(sentence)
            final, rest = await record()
            # Nothing was said (no answer at all): the router's fixed line
            # is the whole reply. Else: the offer, the third-party ask.
            first_sentence_ready()
            for sentence in _split_sentences(rest):
                await speak(sentence)
            return final
        except BaseException:
            # A barge-in cancelled the turn, or TTS / the socket failed:
            # stop the model, make sure what was said is recorded, then let
            # the turn end the way it would have.
            try:
                await sentences.aclose()
                final, _rest = await record()
                routed.text = final.text
                routed.matched_path = final.matched_path
            except BaseException as e:  # noqa: BLE001 — never mask the original
                log.warning(
                    "stream %s: recording an interrupted answer failed: %r", self.room_id, e,
                )
            raise

    async def _finish_streamed_answer(
        self,
        streamed: Any,
        *,
        ctx: Context,
        ident: Any,
        person_id: int | None,
        transcript: str,
        audio_seconds: float,
    ) -> tuple[Any, str]:
        """The router's end of a streamed answer (offers, persistence) and
        the voice-profile hooks, in one transaction. Returns the final
        response and the text still to be said after the answer."""
        async with session_scope() as s:
            response, rest = await streamed.finish(s)
            before = response.text
            await self._voice_profile_hooks(
                s, response=response, ctx=ctx, ident=ident, person_id=person_id,
                transcript=transcript, audio_seconds=audio_seconds,
            )
            if response.text != before and response.text.startswith(before):
                rest = f"{rest} {response.text[len(before):].strip()}".strip()
        return response, rest

    def _keep_capture(
        self,
        pcm: bytes,
        *,
        trigger: str | None,
        meta: dict[str, Any] | None,
        transcript: str,
        response: Any,
        timings: TurnTimings,
        interrupted: bool,
    ) -> asyncio.Task[str | None] | None:
        """Start keeping this turn's audio when its room may be recording
        (domovoi/command_captures.py decides, after asking the database).
        Only a command turn that reached the router is a candidate: a
        wake_word or followup trigger, a transcript, a response, and no
        live drop-in call. Returns the write's task (None when the turn is
        not a candidate) for the caller to await through ``asyncio.shield``;
        like the timing write, it belongs to no turn task."""
        if response is None or not transcript.strip():
            return None
        in_call = bool(meta.get("in_call")) if meta else self.dropin_peer is not None
        if not command_captures.eligible(trigger, in_call=in_call):
            return None
        meta = meta or command_captures.capture_meta({}, in_call=in_call)
        config = getattr(self.ws.app.state, "satellite_config", {}).get(self.room_id)
        sidecar = command_captures.build_sidecar(
            room_id=self.room_id,
            pcm_len=len(pcm),
            trigger=trigger,
            meta=meta,
            listen=command_captures.listen_settings(config),
            transcript=transcript,
            matched_handler=getattr(response, "matched_handler", None),
            matched_path=getattr(response, "matched_path", None),
            interrupted=interrupted,
            timings=timings.row_document(),
        )
        write = asyncio.create_task(
            command_captures.keep(self.room_id, pcm, sidecar),
            name=f"command-capture:{self.room_id}",
        )
        _CAPTURE_WRITES.add(write)
        write.add_done_callback(_CAPTURE_WRITES.discard)
        return write

    def _log_turn_timings(
        self, timings: TurnTimings, *, trigger: str | None, matched_path: str | None
    ) -> None:
        """One INFO line per voice turn with its stage timings — numbers,
        the trigger and the route taken; never the transcript. This is the
        line to grep on the Domovoi server (``journalctl -u domovoi-core |
        grep 'turn timings'``)."""
        log.info(
            "turn timings room=%s trigger=%s path=%s %s",
            self.room_id, trigger, matched_path, timings.describe(),
        )

    def _finish_turn_timings(
        self, timings: TurnTimings, *, trigger: str | None, response: Any
    ) -> asyncio.Task[None] | None:
        """Log the turn's timings and start merging the stages that only
        exist after routing (route_ms, tts_first_ms, total_ms) into its
        intents_log row. Returns the write's task (None when there is
        nothing to merge) for the caller to await through
        ``asyncio.shield``: the write belongs to no turn task, so no cancel
        of the turn can abort it. Best-effort: the row already holds the
        pre-route stages, and a failure here must never touch the turn."""
        self._log_turn_timings(
            timings, trigger=trigger,
            matched_path=getattr(response, "matched_path", None),
        )
        patch = timings.post_route_patch()
        if timings.intents_log_id is None or not patch:
            return None
        write = asyncio.create_task(
            self._write_post_route_timings(timings.intents_log_id, patch),
            name=f"turn-timings:{self.room_id}",
        )
        # The loop keeps only weak references to tasks; hold this one until
        # it is done, whether or not anyone is still awaiting it.
        _TIMING_WRITES.add(write)
        write.add_done_callback(_TIMING_WRITES.discard)
        return write

    async def _write_post_route_timings(self, row_id: int, patch: dict[str, int]) -> None:
        try:
            async with session_scope() as s:
                await merge_post_route(s, row_id, patch)
        except Exception as e:
            log.warning(
                "turn timings: could not record the post-route stages for "
                "room=%s: %s", self.room_id, e,
            )

    def _may_reach_other_rooms(self, what: str) -> bool:
        """False for a socket accepted WITHOUT a pairing token (lenient
        pairing's case 5, or its fallback when the check could not run):
        whatever LAN device named itself this room may use its own room and
        nothing else, so it never opens another room's microphone or
        speaks in it (CORE-11). Logs the refusal."""
        if self.token_authenticated:
            return True
        log.warning(
            "%s: refused for room=%s — it connected without a pairing token, "
            "so it cannot reach another room",
            what, self.room_id,
        )
        return False

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
            # The initiator's own socket must be a paired one (CORE-11).
            # DropInHandler already refuses aloud; this is the backstop for
            # anything else that ever hands this layer a request.
            if not self._may_reach_other_rooms("drop-in"):
                return
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
            if mode in ("confirm", "ring"):
                await self._prompt_target_for_dropin(target)
            else:
                await self._begin_dropin(target)
            return

        if action == "accept":
            initiator_room = response.dropin_room
            initiator = sessions.get(initiator_room) if initiator_room else None
            if initiator is None and initiator_room:
                # Ring mode parks phone callers here instead: a phone is
                # never in active_sessions, so without this the room's
                # "yeah" would find nobody to connect it to.
                initiator = self.ws.app.state.pending_dropins.get(initiator_room)
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
        sil = self._chat_silence_task
        self._chat_silence_task = None
        # The silence watchdog calls this from INSIDE its own task, so cancel
        # it only when we're not it: cancelling the running task raises
        # CancelledError at the very next await (the context write below, or
        # the watchdog's own ``send_chat_end`` after we return), which swallows
        # the ``chat_end`` frame and leaves the Pi's mic open forever — exactly
        # the stuck-open-mic case chat_silence_timeout_sec exists to prevent.
        # Same guard as ``_end_dropin``.
        if sil is not None and not sil.done() and sil is not asyncio.current_task():
            sil.cancel()
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
                            self._note_audio_sent(len(chunk), sr)
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
                    self._note_audio_sent(len(chunk), sr)
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
                self._note_audio_sent(len(chunk), sr)
            for sentence in sentences[1:]:
                pcm, _sr = _wav_to_pcm(
                    await tts.synthesize(sentence, engine=synth_engine, voice=synth_voice)
                )
                for chunk in _iter_chunks(pcm):
                    await self.ws.send_bytes(chunk)
                    self._note_audio_sent(len(chunk), _sr)
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
                # The voice turn's pre-route stages (speech-to-text, voice
                # identification), same as a routed turn's row. Nothing is
                # merged in later: Letta streams the reply, so there is no
                # route or first-sentence stage.
                await IntentLogRepository(s).log(
                    room_id=ctx.room_id,
                    transcript=transcript,
                    matched_handler="chat_mode",
                    matched_path="chat",
                    online=ctx.online,
                    latency_ms=latency_ms,
                    person_id=ctx.person_id,
                    presence_tier=ctx.presence_tier,
                    timings=await timings_for_row(s, ctx.timings),
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

    # ── Out-of-turn announcements ─────────────────────────────────────────

    def _note_audio_sent(
        self, nbytes: int, sample_rate: int, *, now: float | None = None,
    ) -> None:
        """Push the playout estimate out by the audio just sent to this
        room's speaker (16-bit mono PCM: 2 bytes a sample). Not called for
        the drop-in relay, which is a live call on another session."""
        if nbytes <= 0 or sample_rate <= 0:
            return
        if now is None:
            now = time.monotonic()
        self._playout_until = max(now, self._playout_until) + nbytes / (2 * sample_rate)

    def _note_response_end(self, frame: dict[str, Any]) -> None:
        """A response_end just went out. Interrupted: the satellite has
        dropped what it was playing. Asking a follow-up: hold announcements
        until the answer can start."""
        now = time.monotonic()
        if frame.get("interrupted"):
            self._playout_until = now
        if frame.get("expect_followup"):
            self._followup_hold_until = (
                max(self._playout_until, now) + ANNOUNCE_FOLLOWUP_HOLD_SEC
            )

    def _cut_announcement(self) -> None:
        """A new capture (utterance_start) or a barge-in ends the
        announcement being sent, exactly as it ends a reply."""
        if self._announce_lock.locked():
            # Also while the first sentence is still synthesizing: announce()
            # then gives up before sending anything.
            self._announce_cut = True
        task = self._announce_task
        if task is not None and not task.done():
            self._announce_cut = True
            task.cancel()

    def _capturing(self, now: float) -> bool:
        """A capture is live: its utterance_start came, and its audio is
        still arriving. A follow-up capture nobody answered ends on the
        satellite with no utterance_end at all, so `utterance_active` alone
        stays True until the next wake word."""
        return self.utterance_active and now - self._last_audio_at < ANNOUNCE_CAPTURE_FRESH_SEC

    def _announcement_coming(self) -> bool:
        """Another announcement is on its way to this room: an `announce`
        call queued on the room's lock or still synthesizing its first
        sentence (`_announce_callers`), a drop-in prompt holding the lock, or
        a timer or reminder the delivery is still to announce here
        (`TimerDelivery.announcing_to`). Its own end restarts the music."""
        if self._announce_callers or self._announce_lock.locked():
            return True
        delivery = getattr(self.ws.app.state, "timer_delivery", None)
        coming = getattr(delivery, "announcing_to", None)
        return bool(coming is not None and coming(self.room_id))

    def music_block(self, now: float | None = None) -> str | None:
        """Why a music_start must not go to this room right now, or None
        when it may.

        The satellite starts its player as soon as its speaker is free, and
        a satellite older than the music hold (satellite/client.py
        `_music_hold_reason`) does so whatever else it is doing — into an
        open capture, which then hears the music as one long sentence. So
        the room is busy while: it is in a call (the call's end restores
        the music), recording wake-word clips, or in chat mode; a capture
        is live (a follow-up nobody answered sends no utterance_end — its
        audio stopping ends it); a turn is being answered (its end makes
        its own music decision); another announcement is on its way (its
        end restarts the music); or a reply asked a question and its answer
        may still start (`_followup_hold_until`, which the answer's
        utterance_start clears). Called from a task: the turn that asks is
        not "busy" to itself."""
        if now is None:
            now = time.monotonic()
        if self.dropin_peer is not None:
            return "in_call"
        if self.wake_recording is not None:
            return "recording"
        if self.conversational_mode:
            return "chat"
        if self._capturing(now):
            return "capturing"
        turn = self._response_task
        if turn is not None and not turn.done() and turn is not asyncio.current_task():
            return "responding"
        if self._announcement_coming():
            return "announcing"
        if now < self._followup_hold_until:
            return "followup"
        return None

    async def start_music(self, url: str) -> bool:
        """A music_start decided outside this room's turns — a dashboard or
        app cast (`main._admin_dispatch_music`), a drop-in's restore: sent
        now when the room is free (`music_block`), held for when it is
        otherwise (`hold_music_start`). The room's `resumable_music` entry
        must already be ``url``. Returns whether it went now."""
        reason = self.music_block()
        if reason is None:
            if await send_music_start(
                self.ws.app, self, self.room_id, url,
                still_wanted=lambda: self.music_block() is None,
            ):
                return True
            # Busy again by the time its stream was ready.
            reason = self.music_block() or "busy"
        self.hold_music_start(url, reason)
        return False

    def hold_music_start(self, url: str, reason: str = "busy") -> None:
        """Send ``url``'s music_start once this room is free (`music_block`)
        instead of now; replaces any start already held here.

        Held, never dropped: a question's follow-up that nobody answers ends
        on the satellite with no utterance_end and no turn, so nothing else
        would ever bring the music back. Superseded by anything that decides
        the room's music in the meantime — a turn's own music_start or
        music_stop, a cast, a call (`_safe_send_text` drops the hold for
        every music frame) — and dropped when the room's resume intent is
        gone or changed (a stop), the socket is replaced or closed, or after
        `MUSIC_HOLD_MAX_SEC`."""
        self._drop_music_hold()
        task = asyncio.create_task(
            self._send_held_music(url, reason), name=f"music-hold-{self.room_id}",
        )
        self._music_hold_task = task
        _MUSIC_HOLDS.add(task)
        task.add_done_callback(_MUSIC_HOLDS.discard)

    def _drop_music_hold(self) -> None:
        """Forget the start held for this room, if any (see
        `hold_music_start`). Safe from the held task itself: it is then
        let finish, not cancelled."""
        task, self._music_hold_task = self._music_hold_task, None
        if task is None or task.done():
            return
        try:
            me = asyncio.current_task()
        except RuntimeError:
            me = None
        if task is not me:
            task.cancel()

    async def _send_held_music(self, url: str, reason: str) -> None:
        """The held start's task: wait until the room is free, then send it
        through `send_music_start`, which asks again last thing before the
        frame; busy again by then, it holds on."""
        app = self.ws.app
        me = asyncio.current_task()

        def mine() -> bool:
            return (
                self._music_hold_task is me
                and app.state.resumable_music.get(self.room_id) == url
                and app.state.active_sessions.get(self.room_id) is self
            )

        log.info(
            "music: room=%s is busy (%s); its music_start waits until it is free",
            self.room_id, reason,
        )
        give_up = time.monotonic() + MUSIC_HOLD_MAX_SEC
        bound = settings.music_stream_ready_timeout_sec + _ANNOUNCE_MUSIC_RESTART_MARGIN_SEC
        try:
            while True:
                while mine() and self.music_block() is not None:
                    if time.monotonic() >= give_up:
                        log.warning(
                            "music: room=%s stayed busy for %.0f s; its held "
                            "music_start is dropped", self.room_id, MUSIC_HOLD_MAX_SEC,
                        )
                        return
                    await asyncio.sleep(MUSIC_HOLD_POLL_SEC)
                if not mine():
                    return
                try:
                    # Bounded in THIS task (`asyncio.timeout`), never in one
                    # of its own: the send drops this hold
                    # (`_safe_send_text` → `_drop_music_hold`), which lets
                    # the hold's own task finish and cancels any other.
                    # `asyncio.wait_for` runs its awaitable in a new task
                    # before Python 3.12 (requires-python is 3.11), so
                    # wrapped in it the hold cancelled itself mid-send and
                    # the start was lost.
                    async with asyncio.timeout(bound):
                        sent = await send_music_start(
                            app, self, self.room_id, url,
                            still_wanted=lambda: mine() and self.music_block() is None,
                        )
                except TimeoutError:
                    log.warning(
                        "music: held music_start for room=%s gave up after %.1f s",
                        self.room_id, bound,
                    )
                    return
                if sent or not mine():
                    return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — nobody awaits this task
            log.warning("music: held music_start for room=%s failed: %s", self.room_id, e)
        finally:
            if self._music_hold_task is me:
                self._music_hold_task = None

    def announce_block(self, now: float | None = None) -> tuple[str, bool] | None:
        """Why an out-of-turn announcement should wait right now, as
        ``(reason, hard)``, or None when the room can take one. Hard
        reasons are always waited out; soft ones only for a while
        (domovoi/timer_delivery.py). First hit wins, in this order."""
        if now is None:
            now = time.monotonic()
        if self.dropin_peer is not None:
            return ("in_call", True)
        if self.wake_recording is not None:
            return ("recording", True)
        if self._response_task is not None and not self._response_task.done():
            return ("responding", True)
        if self._announce_lock.locked():
            return ("announcing", True)
        if now < self._playout_until:
            return ("playing", True)
        if self._capturing(now):
            return ("capturing", True)
        if now < self._connected_at + ANNOUNCE_CONNECTING_SEC:
            return ("connecting", False)
        if now < self._followup_hold_until:
            return ("followup", False)
        if now < self._playout_until + ANNOUNCE_SETTLE_SEC:
            return ("settling", False)
        return None

    def _capture_block(self, now: float | None = None) -> str | None:
        """The part of ``announce_block`` an announcement already under way
        re-checks: the room is listening (a live capture), in a call, or
        recording wake-word clips. None when it is none of those."""
        if now is None:
            now = time.monotonic()
        if self.dropin_peer is not None:
            return "in_call"
        if self.wake_recording is not None:
            return "recording"
        if self._capturing(now):
            return "capturing"
        return None

    def _refuse_during_capture(self) -> None:
        """announce(defer_to_capture=True): nothing has been sent yet, and
        the room is listening, in a call or recording — step aside."""
        reason = self._capture_block()
        if reason is not None:
            log.info("intercom: room=%s %s, deferring announce", self.room_id, reason)
            raise AnnounceNotStarted(
                f"room {self.room_id} {reason}, announce deferred", reason=reason,
            )

    async def announce(self, text: str, *, defer_to_capture: bool = False) -> None:
        """Inject a TTS-synthesized announcement into this Pi's stream.

        Reuses the normal response wire format (response_start →
        PCM chunks → response_end) so the satellite handles it as if it
        were a regular reply — no protocol changes needed Pi-side. Bypasses
        the request/response state machine so it can fire while no
        utterance is in flight.

        One announcement at a time per room: the whole call holds
        ``_announce_lock``, so two callers (a timer from the garage and an
        intercom broadcast, say) play one after the other instead of
        interleaving their PCM.

        Raises ``AnnounceNotStarted`` — nothing reached the satellite, safe
        to try again — when the Pi is mid-response (``reason='responding'``,
        message "room X mid-response, announce skipped": queueing audio on
        top of an in-flight TTS would clip it) or the first sentence would
        not synthesize (``reason='tts_failed'``). Both are RuntimeErrors,
        so callers that catch those keep working.

        The frames go out from an inner task (``_announce_task``) that a new
        capture or a barge-in cancels, exactly as it cancels a reply; the
        satellite then gets an interrupted ``response_end`` and this raises
        ``AnnounceInterrupted`` (it had started playing: counts as heard).

        ``defer_to_capture`` (the timer delivery passes it): also refuse,
        with ``AnnounceNotStarted(reason='capturing'|'in_call'|'recording')``,
        when the room is listening, in a call or recording wake-word clips —
        checked on entry and again once the first sentence is synthesized,
        right before the first frame. The caller checked ``announce_block``
        first, but a capture can begin in the gap (a database round trip),
        and an announcement's ``response_end`` then releases the
        satellite's wait for the real reply. Intercom, the admin announce
        and ``sdk.speech`` keep today's behaviour.

        A first sentence that synthesizes to no audio at all (every TTS
        engine down: the client returns an empty WAV) raises
        ``AnnounceNotStarted(reason='tts_failed')`` instead of sending a
        silent response and reporting it delivered.

        After the announcement plays, if the room had music recorded for
        auto-resume, we emit a music_start so the Pi respawns mpg123 —
        otherwise the announcement permanently kills the music in that
        room (same failure mode as the wake-word music-resume case, just triggered by
        intercom instead of wake-word capture). That music_start is sent
        from its own task (`_restart_music_after_announce`) and is not
        awaited here: this returns once the announcement is delivered, so
        a broadcast that announces room by room never waits on one room's
        stream. When another announcement is already on its way to this
        room (queued on the lock, still synthesizing, or a timer delivery
        waiting for its turn here), the music waits for it: the last one's
        end brings it back.

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
        # Counted from the call, not from the lock: a music restart that
        # follows the announcement before this one waits while any are
        # still to come (`_restart_music_after_announce`).
        self._announce_callers += 1
        try:
            await self._announce(text, defer_to_capture)
        finally:
            self._announce_callers -= 1

    async def _announce(self, text: str, defer_to_capture: bool) -> None:
        """`announce`'s body, run while it is counted in
        `_announce_callers`; see there."""
        async with self._announce_lock:
            self._announce_cut = False
            if self._response_task is not None and not self._response_task.done():
                log.warning(
                    "intercom: room=%s mid-response, skipping announce", self.room_id
                )
                raise AnnounceNotStarted(
                    f"room {self.room_id} mid-response, announce skipped",
                    reason="responding",
                )
            if defer_to_capture:
                self._refuse_during_capture()

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
                raise AnnounceNotStarted(
                    f"room {self.room_id}: announcement TTS failed: {e}",
                    reason="tts_failed",
                ) from e
            if not first_pcm:
                # RealTTSClient answers an empty WAV when every engine
                # failed. Sending it would be a silent "announcement" the
                # caller counts as heard.
                log.warning("intercom: TTS produced no audio for room=%s", self.room_id)
                raise AnnounceNotStarted(
                    f"room {self.room_id}: announcement TTS produced no audio",
                    reason="tts_failed",
                )
            if defer_to_capture:
                self._refuse_during_capture()
            # A capture that began while the first sentence synthesized would
            # have cut the announcement off anyway, and nothing has been sent
            # yet: step aside rather than talk over it.
            if self._announce_cut:
                log.info(
                    "intercom: room=%s started listening, skipping announce", self.room_id
                )
                raise AnnounceNotStarted(
                    f"room {self.room_id} started listening, announce skipped",
                    reason="capturing",
                )
            # A turn that began while the first sentence synthesized owns
            # the speaker now.
            if self._response_task is not None and not self._response_task.done():
                log.warning(
                    "intercom: room=%s mid-response, skipping announce", self.room_id
                )
                raise AnnounceNotStarted(
                    f"room {self.room_id} mid-response, announce skipped",
                    reason="responding",
                )

            task = asyncio.create_task(
                self._send_announcement(text, sentences, first_pcm, sr, a_engine, a_voice),
                name=f"announce-{self.room_id}",
            )
            self._announce_task = task
            try:
                await task
            except asyncio.CancelledError:
                me = asyncio.current_task()
                cut = self._announce_cut and task.cancelled()
                # Tell the satellite this response is over (the same frame a
                # cancelled turn sends) and forget its queued audio.
                await self._safe_send_text({
                    "type": "response_end",
                    "interrupted": True,
                    "expect_followup": False,
                })
                self._playout_until = time.monotonic()
                if cut and (me is None or not me.cancelling()):
                    log.info("intercom: room=%s announcement cut off by a capture",
                             self.room_id)
                    raise AnnounceInterrupted(
                        f"room {self.room_id}: announcement interrupted"
                    ) from None
                raise
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
            finally:
                if self._announce_task is task:
                    self._announce_task = None
                self._announce_cut = False

            # Resume music if this room had something playing — on its own
            # task (see `_restart_music_after_announce`).
            url = self.ws.app.state.resumable_music.get(self.room_id)
            if url:
                self._restart_music_after_announce(url)

    async def _send_announcement(
        self,
        text: str,
        sentences: list[str],
        first_pcm: bytes,
        sr: int,
        a_engine: Any,
        a_voice: Any,
    ) -> None:
        """announce()'s frames: response_start, the audio, response_end.
        Raw `self.ws.send_*` (not `_safe_send_text`) so any
        ConnectionClosed / send error propagates to the caller. The
        `_safe_send_text` helper exists for the normal response path
        where swallowing makes sense (the response task is already
        cleaning up); broadcasts don't have that luxury.

        A socket that is already gone when the FIRST frame goes out means
        not one frame reached the satellite: that raises
        ``AnnounceNotStarted(reason='send_failed')``, which a timer delivery
        puts back to pending for the room's reconnect (or the next boot's
        resume) instead of recording a failure nobody heard. A core
        shutdown lands here: uvicorn closes every satellite socket before
        the teardown, often while a first sentence is synthesizing."""
        tts = get_tts_client()
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
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — any send failure on a dead socket
            raise AnnounceNotStarted(
                f"room {self.room_id}: socket closed before the announcement started: {e}",
                reason="send_failed",
            ) from e
        for chunk in _iter_chunks(first_pcm):
            await self.ws.send_bytes(chunk)
            self._note_audio_sent(len(chunk), sr)
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
                self._note_audio_sent(len(chunk), _sr)
        await self.ws.send_text(json.dumps({
            "type": "response_end",
            "interrupted": False,
            "expect_followup": False,
        }))

    def _restart_music_after_announce(self, url: str) -> None:
        """Send the music_start that follows an announcement, without making
        the announcement wait for it.

        Every broadcast — the intercom fan-out, POST /v1/admin/announce,
        sdk.speech.announce, a house-wide timer or reminder — calls
        `announce` room by room, and `send_music_start` can spend up to
        MUSIC_STREAM_READY_TIMEOUT_SEC opening a room's stream first.
        Awaited in `announce`, one room whose stream would not open held up
        the announcement in every room after it by that long. So it runs on
        a task of its own, held in `_ANNOUNCE_MUSIC_RESTARTS`, bounded, and
        anything that goes wrong in it is logged, never raised into a
        caller that has long since returned.

        First it waits while another announcement is on its way to this
        room: an `announce` call queued on the room's lock or still
        synthesizing its first sentence (`_announce_callers`), a drop-in
        prompt holding the lock, or a timer or reminder the delivery is
        still to announce here (`TimerDelivery.announcing_to` — two timers
        due together: the second waits out the first one's playback and a
        settle before it starts). A music_start in between would respawn
        mpg123 for the moment before the next response_start kills it
        again. The one that plays starts a restart of its own, which
        replaces this one; one that gives up without a sound lets this one
        go on. The wait ends at the delivery's own cap on waiting for a
        busy room plus a margin.

        Running after `announce` has returned, it can find the room moved
        on by the time the stream is ready, so it asks last thing before
        the frame (`send_music_start`'s ``still_wanted``). It sends nothing
        when the room was stopped (its resume intent is gone or changed),
        this socket was replaced or closed, or a later announcement here
        started a restart of its own. Another announcement that began while
        the stream was readied is waited for again, as above. And when the
        room is otherwise busy (`music_block`) — a live capture, a turn
        being answered, a question's follow-up window, a call, chat mode — a
        music_start now would spawn mpg123 over the capture (a capture whose
        audio stopped arriving is over: a follow-up nobody answered sends no
        utterance_end), so the start is held for the room instead
        (`hold_music_start`): a turn's own music decision or the call's
        restore supersedes it, and a capture that ends with no turn — the
        follow-up nobody answered — gets the music back.
        """
        app = self.ws.app
        self._announce_music_seq += 1
        seq = self._announce_music_seq

        def current() -> bool:
            return (
                seq == self._announce_music_seq
                and app.state.resumable_music.get(self.room_id) == url
                and app.state.active_sessions.get(self.room_id) is self
            )

        another_announcement_coming = self._announcement_coming

        def still_wanted() -> bool:
            return current() and self.music_block() is None

        bound = settings.music_stream_ready_timeout_sec + _ANNOUNCE_MUSIC_RESTART_MARGIN_SEC

        async def restart() -> None:
            wait_until = time.monotonic() + (
                float(settings.timer_announce_max_wait_sec) + _ANNOUNCE_MUSIC_QUEUE_MARGIN_SEC
            )
            while True:
                while current() and another_announcement_coming():
                    if time.monotonic() >= wait_until:
                        log.warning(
                            "intercom: music restart for room=%s gave up waiting "
                            "for the announcements queued there", self.room_id,
                        )
                        return
                    await asyncio.sleep(_ANNOUNCE_MUSIC_QUEUE_POLL_SEC)
                if not current():
                    return
                busy = self.music_block()
                if busy is not None:
                    # A capture, a turn, a question, a call or a chat has
                    # the room: its start waits for it (see above).
                    self.hold_music_start(url, busy)
                    return
                try:
                    sent = await asyncio.wait_for(
                        send_music_start(
                            app, self, self.room_id, url, still_wanted=still_wanted,
                        ),
                        timeout=bound,
                    )
                except asyncio.TimeoutError:
                    log.warning(
                        "intercom: music restart for room=%s gave up after %.1f s",
                        self.room_id, bound,
                    )
                    return
                except Exception as e:  # noqa: BLE001 — nobody awaits this task
                    log.warning(
                        "intercom: music restart for room=%s failed: %s", self.room_id, e,
                    )
                    return
                if sent or not current():
                    return
                # The room got busy while its stream was readied: another
                # announcement began (waited for as above), or something
                # else that the start is held for (the check above).

        task = asyncio.create_task(restart(), name=f"announce-music-{self.room_id}")
        _ANNOUNCE_MUSIC_RESTARTS.add(task)
        task.add_done_callback(_ANNOUNCE_MUSIC_RESTARTS.discard)

    async def _safe_send_text(self, payload: dict[str, Any]) -> None:
        # Remember the reply we're about to speak, so a barge-triggered
        # capture can be tested against it (see `self_echo_filter`). Hooked
        # here rather than at each call site because every response_start on
        # the command path goes through this helper, and a missed site would
        # silently disarm the guard for that path.
        if payload.get("type") == "response_start":
            self._last_spoken_text = str(payload.get("text") or "")
        if payload.get("type") in ("music_start", "music_stop"):
            # Whatever music this room gets now supersedes a start held for
            # it (`hold_music_start`) — dropped BEFORE the send, so the held
            # one cannot go out behind this frame.
            self._drop_music_hold()
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            # Connection may be gone; nothing useful to do.
            pass
        if payload.get("type") == "response_end":
            self._note_response_end(payload)

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

    def _on_logs_chunk(self, ctrl: dict[str, Any]) -> None:
        """Reassemble one `logs_chunk` frame into its pending request.

        A gap or a repeat in the sequence FAILS the request rather than
        stitching the pieces anyway: a log whose ordering you can't trust
        is worse than an error, because it reads as evidence.
        """
        rid = str(ctrl.get("request_id") or "")
        pending = self._log_requests.get(rid)
        if pending is None or pending.future.done():
            # A late chunk from a pull that already timed out, or one
            # addressed to a session we replaced. Nothing to do with it.
            return
        seq = ctrl.get("seq")
        if seq != pending.next_seq:
            pending.future.set_exception(RuntimeError(
                f"satellite sent log chunk {seq!r}, expected {pending.next_seq}"
            ))
            return
        pending.next_seq += 1
        if not pending.accept(str(ctrl.get("data") or "")):
            log.warning(
                "log pull from room=%s abandoned: over the %d-character cap",
                self.room_id, pending.max_chars,
            )
            return
        if ctrl.get("final"):
            stats = ctrl.get("stats")
            pending.future.set_result({
                "text": "".join(pending.chunks),
                "stats": stats if isinstance(stats, dict) else {},
                "chunks": pending.next_seq,
            })

    async def request_logs(
        self, *, max_bytes: int, timeout: float = 60.0
    ) -> dict[str, Any]:
        """Pull this satellite's recent log output over the live WS.

        The Pi keeps its own log in a RAM ring (`satellite/log_buffer.py`)
        and answers with a numbered series of `logs_chunk` frames, which
        this reassembles. Raises on a dead socket, `TimeoutError` if the
        satellite goes quiet mid-transfer, so the admin endpoint can tell
        the difference between "offline" and "stopped answering" — and
        (ADD-2) if the answer comes to more than ``max_bytes`` allows for,
        rather than letting the reassembly buffer follow whatever the
        session decides to send.
        """
        request_id = secrets.token_hex(8)
        cap = max(
            LOG_REASSEMBLY_FLOOR, int(int(max_bytes) * LOG_REASSEMBLY_SLACK)
        )
        pending = _LogRequest(
            future=asyncio.get_running_loop().create_future(), max_chars=cap
        )
        self._log_requests[request_id] = pending
        try:
            await self.ws.send_text(json.dumps({
                "type": "get_logs",
                "request_id": request_id,
                "max_bytes": int(max_bytes),
            }))
            return await asyncio.wait_for(pending.future, timeout)
        finally:
            self._log_requests.pop(request_id, None)

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
            # A capture already open on either side now carries call audio:
            # never an opted-in room's command recording, even if the call
            # ends before that capture does. (getattr: the peer may be a
            # phone, which duck-types only the drop-in surface.)
            for side in (self, peer):
                if getattr(side, "utterance_active", False):
                    side._utterance_in_call = True
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
            else:
                # A phone leg (PhoneDropinSession) has no speaker estimate.
                note = getattr(sess, "_note_audio_sent", None)
                if note is not None:
                    note(len(START_CHIME_PCM), PCM_INPUT_SAMPLE_RATE)

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
            else:
                # A phone leg (PhoneDropinSession) has no speaker estimate.
                note = getattr(sess, "_note_audio_sent", None)
                if note is not None:
                    note(len(END_CHIME_PCM), PCM_INPUT_SAMPLE_RATE)

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
        # The call (or its ring prompt) owns the target's music from here: a
        # restart still pending from an announcement there steps aside
        # (`_restart_music_after_announce`); the call's end, or a declined
        # ring's turn, brings the music back. (A phone peer has neither.)
        if isinstance(getattr(target, "_announce_music_seq", None), int):
            target._announce_music_seq += 1
        # So does a start held for the room (`hold_music_start`).
        drop_hold = getattr(target, "_drop_music_hold", None)
        if drop_hold is not None:
            drop_hold()
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
        the call (mirrors announce()'s music-resume tail). Best-effort.
        Through `start_music`: a room that is listening (its "hang up"
        command, a follow-up) or answering gets it once it is free."""
        resumable: dict[str, str] = self.ws.app.state.resumable_music
        url = resumable.get(target.room_id)
        if url:
            start = getattr(target, "start_music", None)
            if start is not None:
                await start(url)
            else:
                await send_music_start(self.ws.app, target, target.room_id, url)

    async def prompt_dropin(self, text: str) -> None:
        """Confirm-mode invite to the target. Like ``announce()`` but (a)
        sends ``response_end`` with ``expect_followup=True`` so the target
        can answer "yeah"/"no" without a wake word, and (b) skips the
        music-resume tail (music must stay suppressed while they decide).
        Raises on a dead socket or a mid-response target so the caller can
        treat the target as busy.

        Holds ``_announce_lock`` like ``announce()``: a timer or reminder
        due while this prompt synthesizes waits for it (``announce_block``
        reads the lock as ``announcing``) instead of interleaving its
        frames with the prompt's, and the satellite never takes the
        announcement's ``response_end`` for the prompt's."""
        async with self._announce_lock:
            await self._prompt_dropin_locked(text)

    async def _prompt_dropin_locked(self, text: str) -> None:
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
        # A turn that began while the first sentence synthesized owns the
        # speaker now (as in announce()).
        if self._response_task is not None and not self._response_task.done():
            raise RuntimeError(
                f"room {self.room_id} mid-response, dropin prompt skipped"
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
            self._note_audio_sent(len(chunk), sr)
        for sentence in sentences[1:]:
            pcm, _sr = _wav_to_pcm(
                await tts.synthesize(sentence, engine=p_engine, voice=p_voice)
            )
            for chunk in _iter_chunks(pcm):
                await self.ws.send_bytes(chunk)
                self._note_audio_sent(len(chunk), _sr)
        end_frame = {
            "type": "response_end",
            "interrupted": False,
            "expect_followup": True,
        }
        await self.ws.send_text(json.dumps(end_frame))
        self._note_response_end(end_frame)

    async def _prompt_target_for_dropin(self, target: "StreamSession") -> None:
        """Confirm-mode: park a ``pending_confirmation`` in the target's
        session so its next "yeah" routes to ``DropInHandler.handle_confirmation``,
        then prompt it with a no-wake-word followup. The auto path skips all
        this (which is why auto is the default)."""
        await ring_target_for_dropin(self.room_id, target)


# ─── Ringing a room before a call opens ──────────────────────────────────


async def ring_target_for_dropin(initiator_room: str, target: "StreamSession") -> bool:
    """Ask ``target``'s room whether it will take a call from
    ``initiator_room``, and open nothing.

    Parks a ``core.dropin_invite`` pending confirmation in the target's
    session so its next "yeah" routes to
    ``DropInHandler.handle_confirmation``, then prompts it with a
    no-wake-word followup. The bridge is built later, by the target's own
    accept turn (``_handle_dropin_action``) — never here.

    Module-level rather than a ``StreamSession`` method because the
    initiator is not always a session: ``dropin_accept_mode='ring'``
    routes HTTP- and phone-initiated calls through here too, and a phone
    (``phone_dropin.PhoneDropinSession``) has no session of its own. Only
    the TARGET has to be a real satellite, which it always is.

    Returns True when the prompt went out.
    """
    from domovoi.confirmations import request_confirmation
    from domovoi.db.repositories import SessionRepository

    peer_label = initiator_room.replace("_", " ")
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
                    "initiator_room": initiator_room,
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
        return False

    # Free the card so the prompt is audible; a decline auto-resumes
    # music on that turn, an accept keeps it suppressed for the call.
    await target._suppress_music_for(target)
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
        await target._restore_music_for(target)
        return False
    return True
