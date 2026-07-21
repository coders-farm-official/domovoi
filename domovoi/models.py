from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Intent(BaseModel):
    transcript: str
    room_id: str | None = None
    session_id: UUID | None = None
    synthesize: bool = False   # if true, /v1/intent returns audio/wav bytes


class Context(BaseModel):
    room_id: str | None = None
    session_id: UUID | None = None
    online: bool = True
    bot_name: str = "Domovoi"
    # VoiceProfileHandler fills these in during the pre-router pass
    # (see ``streaming.StreamSession._process_utterance``). Handlers can
    # personalize responses with ``person_id``; the router stamps both
    # into ``intents_log`` for the audit trail. ``embedding_bytes`` is
    # the raw float32 vector for the current utterance, kept as bytes
    # so pydantic can carry it without arbitrary_types_allowed and so
    # it round-trips trivially into voice_profiles.embedding (BYTEA).
    # VoiceProfileHandler decodes via numpy.frombuffer when enrolling.
    person_id: int | None = None
    presence_tier: str | None = None  # "low" | "medium" | "high"
    embedding_bytes: bytes | None = None
    # Most recent WiFi self-report from the originating Pi (rx_mbits,
    # tx_mbits, ssid). The streaming layer stamps it from
    # ``app.state.wifi_status[room_id]`` before routing so WifiHandler's
    # diagnostic path can speak without another round-trip. Empty dict
    # when the Pi hasn't pushed a status yet (first 60 s after connect).
    wifi_status: dict[str, Any] = Field(default_factory=dict)
    # Most recent output-volume self-report (0-100) from the originating
    # Pi's hardware mixer, stamped from ``app.state.satellite_volume[room_id]``
    # before routing. MusicHandler reads it to compute relative "turn it
    # up / down" bumps against the satellite's real level. None when the Pi
    # hasn't reported one yet.
    satellite_volume: int | None = None
    # The voice this room's satellite reports speaking in (voices
    # registry), stamped from ``app.state.satellite_voice[room_id]``. None
    # when the Pi hasn't reported a voice → the registry default is used.
    # VoiceHandler reads it to answer "what voice are you using".
    voice: str | None = None
    # FastAPI app reference for handlers that need to fan out
    # asynchronous announcements after the original response cycle has
    # ended (e.g. library enricher → "done!" announcement when the
    # worker finishes ~13 min later). The streaming layer stamps it;
    # background workers look up ``app.state.active_sessions[room_id]``
    # at announcement time so a Pi reconnect / disconnect across the
    # async boundary is handled cleanly. None when called via direct
    # /v1/intent (no streaming context).
    app: Any = None


class Response(BaseModel):
    text: str
    session_id: UUID | None = None
    matched_handler: str | None = None
    # OPEN enum (design §6.4): core stamps fast / fast_offline / llm /
    # llm_offline / qa / error / confirmation / auto_search /
    # volatile_offer / chat; plugins may register more. Validated
    # app-side against domovoi.registered_values ("matched_path") in
    # router._persist_turn — not by a closed Literal, and no DB CHECK.
    matched_path: str | None = None
    online: bool = True
    data: dict[str, Any] = Field(default_factory=dict)
    # Music coordination — emitted as out-of-band control frames to the Pi
    # *after* response_end. "start" with a stream URL → Pi spawns its music
    # subprocess. "stop" → Pi kills it. None → no change to current state.
    music_action: Literal["start", "stop"] | None = None
    music_stream_url: str | None = None
    # Intercom fan-out — the streaming layer reads these after sending
    # the response to the requesting Pi and synthesizes ``announce_text``
    # into each listed room's WebSocket. ``announce_to_rooms = []`` means
    # no fan-out; populated by IntercomHandler with the resolved target
    # room list.
    announce_to_rooms: list[str] = Field(default_factory=list)
    announce_text: str | None = None
    # Follow-up signal — when set, the streaming layer adds an
    # ``expect_followup`` flag to the response_end frame, which tells
    # the Pi to skip its wake-word gate and capture the user's answer
    # immediately. Set by handlers that explicitly ask a question
    # ("did I get that right?", "what should I play?") and want the
    # response to count as a turn without "Hey Jarvis" friction.
    # Skipped when the response was interrupted — the bot's question
    # got cut off, so the user wasn't given a chance to answer it.
    expect_followup: bool = False
    # Pi-side action to trigger after the response audio drains. The
    # streaming layer copies this into the response_end frame; the
    # satellite executes the action once playback has fully drained
    # so the spoken response ("OK, reconnecting now.") finishes before
    # any side-effect (e.g. WiFi reassociate, which briefly drops the
    # WS). Currently the only value is "reassociate_wifi" set by
    # WifiHandler — but the field is open-ended so future Pi-local
    # actions can reuse the same plumbing.
    pi_action: Literal["reassociate_wifi", "set_voice"] | None = None
    # Argument for ``pi_action`` when it needs one. For "set_voice" this is
    # the new voice name the satellite should persist (~/.domovoi/voice) and
    # re-report. Copied into the response_end frame alongside ``pi_action``.
    pi_action_arg: str | None = None
    # Per-response TTS voice override (a voice name from the registry). When
    # set, the streaming layer synthesizes THIS turn in that voice instead
    # of the room's current voice — used by VoiceHandler to sample a voice
    # ("here's how Ryan sounds") or to speak the switch confirmation in the
    # newly-selected voice before the satellite has re-reported it.
    voice_override: str | None = None
    # Master output volume (0-100) to apply on the originating satellite.
    # The streaming layer sends a ``set_volume`` frame *before* response_start
    # so the spoken confirmation itself plays at the new level. It drives the
    # Pi's hardware output gain (the ALSA ``PCM`` control on the XVF3800),
    # which scales BOTH the TTS playback and music — the single master
    # volume. Set by MusicHandler's volume commands.
    satellite_volume: int | None = None
    # Two-way drop-in (Feature 4). DropInHandler sets these; the streaming
    # layer acts on them AFTER the originating turn (mirroring announce_to_rooms)
    # because it — not the handler — owns the live StreamSession pairing and
    # the raw-PCM relay. "request": this room wants to drop in on ``dropin_room``
    # (streaming opens the call in auto mode, or prompts the target in confirm
    # mode). "accept": the target answered yes (from handle_confirmation);
    # ``dropin_room`` is the initiator to pair with. "end": tear down this
    # room's active call. ``dropin_peer_label`` is the spoken room name for
    # prompts. The relay audio itself never routes — it bypasses the router
    # and _persist_turn entirely; only the initiation/accept/hang-up turns are
    # logged (by the router) plus one dropin_calls audit row per call.
    dropin_action: Literal["request", "accept", "end"] | None = None
    dropin_room: str | None = None
    dropin_peer_label: str | None = None


class ConnectivityState(BaseModel):
    online: bool
    last_checked_at: datetime | None = None
    last_online_at: datetime | None = None
    target: str


class HandlerInfo(BaseModel):
    name: str
    requires_network: Literal["no", "degraded", "yes"]
    tool_schema: dict[str, Any]
    fast_path_count: int
    # Registry metadata (design §4.2/§12): dispatch band, origin
    # ("core" or the plugin slug), and the registered display metadata
    # web filters / the Android tone map render from. `name` stays the
    # stable identifier that lands in intents_log.matched_handler.
    priority_band: int
    origin: str = "core"
    display: dict[str, Any] = Field(default_factory=dict)
    # Extracted from the tool_schema description ("Example: '...'") —
    # the source the web manual page renders from (design §12).
    example_phrases: list[str] = Field(default_factory=list)
