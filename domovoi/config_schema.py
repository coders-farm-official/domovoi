"""Editable-config registry — the connective tissue for the web UI's
settings gear (Phase A of "edit domovoi configs from the web UI").

Each :class:`FieldSpec` names a real ``Settings`` field and layers
editing metadata on top: which UI group it shows under, whether it's a
``common`` knob or lives behind the folded ``advanced`` (danger) section,
how a change is APPLIED (the ``tier``), validation bounds, and a
human-readable tooltip explaining what changing it actually does.

The field's TYPE, DEFAULT, and CURRENT value are pulled from the live
``Settings`` object at request time — this registry only adds the
editability layer. A test asserts every ``name`` here is a real
``Settings.model_fields`` key so the two never drift.

Tiers — how a saved change takes effect:
  * ``hot``     — mutate the live ``settings`` singleton; the consumer
                  re-reads it per tick/per call, so it applies immediately.
  * ``reapply`` — mutate + poke the affected subsystem (reset the TTS
                  client, re-set the log level). Applies immediately.
  * ``restart`` — persisted to ``.env`` but NOT applied live (the value
                  is read once at startup). The API returns it in
                  ``restart_required`` and the UI shows a badge.

Sections:
  * ``common``   — shown by default. Safe to tune.
  * ``advanced`` — folded behind a warning. Infra knobs (DB, ports,
                   paths, STT device) that can break the Domovoi server or
                   need manual reconciliation if set wrong.

Deliberately EXCLUDED: ``tts_edge_voice`` / ``tts_piper_voice``. The
voice a satellite actually speaks is resolved from the Voices registry
table (managed on the Voices page), not from these settings — editing
them here would be a silent no-op in normal operation.

Also deliberately EXCLUDED, and for a different reason: the two keys
that decide who may talk to this box and what it may reach out to —
``trusted_hosts`` (which Host headers are accepted) and
``outbound_allow_hosts`` (which otherwise-refused endpoints the server
may fetch, see ``domovoi/net_safety.py``). This registry is the ONLY way
a setting becomes writable over HTTP, so leaving them out is what makes
them server configuration rather than a request parameter: they are set
in the environment or ``.env`` by whoever owns the machine, and a
compromised dashboard session cannot widen either gate. Do not add
them here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Tier = Literal["hot", "reapply", "restart"]
Section = Literal["common", "advanced"]
FieldType = Literal["int", "float", "bool", "str", "choice"]


@dataclass(frozen=True)
class FieldSpec:
    name: str                       # must be a Settings.model_fields key
    label: str
    group: str
    help: str                       # tooltip — what changing it impacts
    type: FieldType
    section: Section = "common"
    tier: Tier = "hot"
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None
    unit: str | None = None
    pattern: str | None = None      # str fields: full-match regex the value must satisfy
    pattern_help: str | None = None  # the user-facing "expected ..." when it doesn't

    @property
    def secret(self) -> bool:
        """Whether this field's VALUE is a credential (see
        :data:`SECRET_SETTING_NAMES`). Derived from the name rather than
        declared per row, so a secret added to the registry later is
        masked without anyone having to remember a flag."""
        return is_secret_setting(self.name)


# Settings whose VALUE is a credential, whether or not they are editable
# from the dashboard: the config read masks these for every caller but a
# live admin Bearer (the plugin config bridge masks its own
# ``kind="secret"`` fields the same way). Listed by settings name so a
# field that is not in the editable registry today is still recognized if
# it is added later.
SECRET_SETTING_NAMES: frozenset[str] = frozenset(
    {
        "database_url",      # carries the Postgres password
        "acoustid_api_key",  # third-party API key
        "letta_token",       # chat-agent server token
    }
)


def is_secret_setting(name: str) -> bool:
    """True when ``name`` names a settings value that must never be read
    back to anything short of an admin Bearer."""
    return name in SECRET_SETTING_NAMES


def mask_secret(value: object) -> str:
    """What a secret's value reads as when the caller may not see it.
    Matches the plugin config bridge's wording so the settings page shows
    one convention."""
    return "•••• set" if value not in (None, "") else "not set"


# Ollama's ``keep_alive`` syntax: a Go duration ("24h", "90m", "1h30m",
# "500ms"), a bare number of seconds, "-1" (any negative) for forever, "0"
# to unload immediately. Blank is also valid — it means "don't send it".
# Validated here because a value Ollama can't parse would fail EVERY chat
# call, and pydantic doesn't validate on assignment (see coerce_and_validate).
OLLAMA_KEEP_ALIVE_PATTERN = (
    r"\s*(?:-?\d+(?:\.\d+)?|-?(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+)?\s*"
)


# Every value the dashboard offers for ``whisper_compute_type``: "auto"
# plus CTranslate2's own compute types. Which of them a device can run is
# checked as a pair with ``whisper_device`` on save
# (domovoi/clients/whisper.py ``compute_pair_problem``), not here — a
# single field can't know the other half.
WHISPER_COMPUTE_TYPE_CHOICES = (
    "auto", "int8", "int8_float32", "int8_float16", "int8_bfloat16",
    "int16", "float16", "bfloat16", "float32",
)


# Order here is the display order within each group.
EDITABLE_FIELDS: list[FieldSpec] = [
    # ─── Identity ──────────────────────────────────────────────────────
    FieldSpec(
        "bot_name", "Bot name", "Identity",
        "What the assistant calls itself in responses (NOT the wake word — "
        "that's trained per-satellite). Takes effect after a restart.",
        "str", tier="restart",
    ),
    FieldSpec(
        "log_level", "Log level", "Identity",
        "How verbose the Domovoi server log is. DEBUG is very noisy (every "
        "frame/turn); INFO is the normal level; WARNING/ERROR quiet it down.",
        "choice", tier="reapply",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    ),

    # ─── Models (LLM role slots) ───────────────────────────────────────
    # Managed richly on the Models page; also editable here. 'reapply' tier:
    # Ollama swaps models per-request, so clearing the cached client
    # (reset_ollama_client) is enough — no server restart, unlike
    # Whisper (whisper_model is 'restart', loaded once at boot).
    FieldSpec(
        "ollama_model", "Q&A model", "Models",
        "The Ollama model for conversational Q&A ('tell me a joke' fallthrough). "
        "Fast + cheap matters more than schema strength here. Applies immediately "
        "(the next question uses it); it must already be pulled.",
        "str", tier="reapply",
    ),
    FieldSpec(
        "ollama_tool_model", "Tool-routing model", "Models",
        "The Ollama model that routes voice commands to handlers via tool-calls. "
        "Strong schema adherence is what matters here. Applies immediately; it "
        "must already be pulled.",
        "str", tier="reapply",
    ),
    FieldSpec(
        "ollama_tool_think", "Tool model thinks first", "Models",
        "Let the tool-routing model emit reasoning tokens before it answers "
        "(only affects models that have a thinking mode). Routing is the "
        "latency-critical step of every non-fast-path turn, so this is OFF by "
        "default — leave it off on a CPU host. Ignored by models without a "
        "thinking mode. Applies immediately.",
        "bool", tier="reapply",
    ),
    FieldSpec(
        "ollama_vision_model", "Vision model", "Models",
        "The vision-capable Ollama model the text-chat surface uses when a "
        "message carries images. Applies immediately; it must already be "
        "pulled.",
        "str", tier="reapply",
    ),
    FieldSpec(
        "ollama_keep_alive", "Keep models loaded for", "Models",
        "How long Ollama keeps a model in memory after its last request. "
        "Ollama's own default is only 5 minutes, so on a CPU host the models "
        "unload between sporadic questions and the next one pays a cold start "
        "of up to a minute (model load plus the tool-schema prefill). Use an "
        "Ollama duration: '24h', '90m', a bare number of seconds, '-1' to keep "
        "them loaded until Ollama restarts, '0' to unload after every reply. "
        "Leave blank to let the Ollama server's own setting govern. Applies "
        "immediately.",
        "str", tier="reapply",
        pattern=OLLAMA_KEEP_ALIVE_PATTERN,
        pattern_help="expected an Ollama duration such as 24h, 90m, -1 or 0",
    ),

    # ─── Voice & speech ────────────────────────────────────────────────
    FieldSpec(
        "tts_engine", "TTS engine", "Voice & speech",
        "Preferred text-to-speech engine. 'piper' (the default) = fully "
        "local neural voices, nothing leaves your network. 'edge' = "
        "Microsoft's cloud voices — nicer sounding and free of CPU cost, "
        "but the TEXT OF EVERY SPOKEN RESPONSE is sent to Microsoft, so "
        "choose it deliberately. 'system' = the OS's own synthesizer "
        "(espeak-ng on Linux, SAPI on Windows), robotic but always there. "
        "Falls through the chain if the preferred one fails.",
        "choice", tier="reapply", choices=["piper", "edge", "system"],
    ),
    FieldSpec(
        "tts_speed", "Speaking rate", "Voice & speech",
        "How fast the bot talks. 1.0 = normal, 1.2 = a bit quicker, 0.9 = "
        "a bit slower. Applies to the next thing it says.",
        "float", tier="reapply", min=0.5, max=2.0,
    ),

    # ─── Voice profiles (speaker ID) ───────────────────────────────────
    FieldSpec(
        "voice_profile_match_threshold", "Match strictness", "Voice profiles",
        "How close a voice must match an enrolled profile to be recognized "
        "as that person. Higher = stricter (fewer wrong IDs, but more "
        "'who's speaking?'); lower = looser (more IDs, more mistakes).",
        "float", min=0.5, max=0.95,
    ),
    FieldSpec(
        "voice_profile_drift_reenroll_after", "Drift re-enroll after",
        "Voice profiles",
        "After this many borderline matches in a row for the same person, a "
        "fresh voice sample is auto-saved so recognition keeps up as their "
        "voice changes (cold, aging). Higher = more conservative.",
        "int", min=1, max=10,
    ),
    FieldSpec(
        "voice_profile_soft_tier_sec", "Recent-speaker window", "Voice profiles",
        "If a known speaker was heard within this many seconds, the bot is "
        "more relaxed about re-confirming who they are. Raising it means it "
        "trusts a recent identification for longer.",
        "int", min=60, max=86400, unit="sec",
    ),

    # ─── Conversation & memory ─────────────────────────────────────────
    FieldSpec(
        "session_recent_turns_cap", "Conversation memory", "Conversation",
        "How many recent back-and-forth turns are fed to the LLM as context. "
        "Higher = it remembers more of the conversation, at the cost of more "
        "tokens and a little latency.",
        "int", min=2, max=50, unit="turns",
    ),
    FieldSpec(
        "memory_extractor_min_confidence", "Memory confidence floor",
        "Conversation",
        "Minimum confidence before the bot saves a long-term fact it inferred "
        "about you (a preference, a name). Higher = fewer but surer memories; "
        "lower = it remembers more, including shakier guesses.",
        "float", min=0.3, max=0.95,
    ),

    # ─── Radio ─────────────────────────────────────────────────────────
    FieldSpec(
        "radio_dedup_window_sec", "Re-download cooldown", "Radio",
        "Don't auto-queue the same detected song again within this many "
        "seconds — stops a song on heavy rotation from queueing repeatedly.",
        "int", min=60, max=86400, unit="sec",
    ),
    FieldSpec(
        "radio_default_sample_interval_sec", "Default sample interval", "Radio",
        "For a newly favorited station, how often to sample its audio to "
        "identify the current song. Only affects stations favorited AFTER "
        "the change.",
        "int", min=30, max=3600, unit="sec",
    ),

    # ─── Library ───────────────────────────────────────────────────────
    FieldSpec(
        "library_enricher_acoustid_min_score", "AcoustID match floor", "Library",
        "Minimum fingerprint-match score to accept metadata (artist/album) "
        "the enricher pulls for a local track. Higher = only confident "
        "matches are written; lower risks wrong tags.",
        "float", min=0.5, max=1.0,
    ),
    FieldSpec(
        "media_plays_retention_days", "Play-history retention", "Library",
        "How long the Satellites 'Recently played' history is kept before "
        "the pruner deletes old rows. 0 = keep forever.",
        "int", min=0, max=3650, unit="days",
    ),

    # ─── Drop-in (live room-to-room audio) ─────────────────────────────
    FieldSpec(
        "dropin_enabled", "Enable drop-in", "Drop-in",
        "Master switch for the two-way live-audio 'drop in on the <room>' "
        "feature. Off = the handler and admin endpoints refuse.",
        "bool",
    ),
    FieldSpec(
        "dropin_accept_mode", "Accept mode", "Drop-in",
        "How a target room accepts: 'auto' opens its mic immediately "
        "(Alexa-style); 'confirm' prompts the target to say yes first "
        "when the call was asked for out loud; 'ring' prompts it for "
        "every caller, including the dashboard and a phone, so nothing "
        "opens until someone in that room answers.",
        "choice", choices=["auto", "confirm", "ring"],
    ),
    FieldSpec(
        "dropin_ring_timeout_sec", "Ring timeout", "Drop-in",
        "How long a phone waits for a rung room to answer before giving "
        "up. Ring mode only.",
        "float", min=5.0, max=300.0, unit="sec",
    ),
    FieldSpec(
        "dropin_silence_timeout_sec", "Silence auto-end", "Drop-in",
        "End a call automatically after this many seconds with no relayed "
        "audio from either side. Effective only when the relay noise gate is "
        "on, so true silence is detectable.",
        "float", min=0.0, max=600.0, unit="sec",
    ),
    FieldSpec(
        "dropin_relay_gate_dbfs", "Relay noise gate", "Drop-in",
        "Audio quieter than this (dBFS) isn't relayed to the other room — "
        "silences bounced echo and room tone. Lower = more permissive "
        "(relays quieter audio); higher = stricter.",
        "float", min=-120.0, max=0.0, unit="dBFS",
    ),

    # ─── News ──────────────────────────────────────────────────────────
    FieldSpec(
        "news_auto_fetch", "Auto-fetch topic news", "News",
        "Automatically fetch news for everyone's topics of interest during "
        "the daily background job. Off = only the general local/national/"
        "global briefing is pre-fetched; topic feeds are pulled only when "
        "someone asks and confirms. Does NOT authorize verbal fetches.",
        "bool",
    ),
    FieldSpec(
        "news_items_per_ask", "Stories per ask", "News",
        "How many stories each news ask reads back — per geographic scope "
        "in the general briefing and per topics/subject query. Editable by "
        "voice too ('give me 5 stories').",
        "int", min=1, max=10, unit="items",
    ),
    FieldSpec(
        "news_location", "Local news location", "News",
        "City or region used to build the 'local' news scope. Empty = the "
        "general briefing skips the local block. Used to discover/select the "
        "local RSS feed.",
        "str",
    ),
    FieldSpec(
        "news_retention_days", "News retention", "News",
        "How long fetched news items are kept before the daily sweep deletes "
        "them. Favorited items are never auto-deleted.",
        "int", min=1, max=3650, unit="days",
    ),
    FieldSpec(
        "news_fetch_hour", "News fetch hour", "News",
        "Hour of day (0-23) the daily news fetch runs. Early morning keeps "
        "the briefing ready before the first 'what's the news' of the day.",
        "int", min=0, max=23,
    ),

    # ─── Workers (restart to apply) ────────────────────────────────────
    FieldSpec(
        "radio_sampler_enabled", "Radio sampler worker", "Workers",
        "Enable passive song detection on favorited stations. Off = no "
        "now-playing detection. Takes effect after a restart.",
        "bool", tier="restart",
    ),
    FieldSpec(
        "memory_extractor_enabled", "Memory extractor worker", "Workers",
        "Enable the worker that mines conversations for long-term memories. "
        "Off = no new memories are inferred. Takes effect after a restart.",
        "bool", tier="restart",
    ),
    FieldSpec(
        "news_enabled", "News worker", "Workers",
        "Enable the daily news fetcher (general briefing + feed discovery + "
        "retention sweep). Off = no background news fetching. Takes effect "
        "after a restart.",
        "bool", tier="restart",
    ),
    FieldSpec(
        "memory_extractor_loop_sec", "Memory extractor interval", "Workers",
        "How often the memory extractor scans for new facts. Takes effect "
        "after a restart.",
        "float", tier="restart", min=10, max=3600, unit="sec",
    ),

    # ─── Advanced (folded + warning) ───────────────────────────────────
    FieldSpec(
        "database_url", "Database URL", "Connections",
        "PostgreSQL connection string the Domovoi server uses for ALL state. "
        "A wrong value means it can't reach its database and nothing works. "
        "Takes effect after a restart.",
        "str", section="advanced", tier="restart",
    ),
    FieldSpec(
        "ollama_url", "Ollama URL", "Connections",
        "Where the local Ollama server lives (LLM routing + Q&A). Wrong = "
        "voice commands that need the LLM fail. Takes effect after a restart.",
        "str", section="advanced", tier="restart",
    ),
    FieldSpec(
        "searxng_url", "SearxNG URL", "Connections",
        "Where the SearxNG metasearch instance lives (powers 'check that "
        "online'). Takes effect after a restart.",
        "str", section="advanced", tier="restart",
    ),
    FieldSpec(
        "mpd_host", "MPD host", "Connections",
        "Address the Domovoi server uses to reach the per-room MPD music "
        "containers. Almost always 127.0.0.1 — the containers publish "
        "their control port on IPv4 loopback only, so the name "
        "'localhost' is rewritten to 127.0.0.1 rather than resolved. "
        "Takes effect after a restart.",
        "str", section="advanced", tier="restart",
    ),
    FieldSpec(
        "mpd_port_base_control", "MPD control port base", "Connections",
        "Starting port for per-room MPD control sockets (each room gets the "
        "next port up). Changing this after rooms are provisioned can collide "
        "with running containers. Takes effect after a restart.",
        "int", section="advanced", tier="restart", min=1024, max=65000,
    ),
    FieldSpec(
        "music_dir", "Music directory", "Paths",
        "Folder the library is indexed from and downloads are written to. "
        "Wrong path = an empty library and failed downloads. Takes effect "
        "after a restart.",
        "str", section="advanced", tier="restart",
    ),
    FieldSpec(
        "sounds_dir", "Sounds directory", "Paths",
        "Where rendered greeting/notification clips are stored and served "
        "from. Takes effect after a restart.",
        "str", section="advanced", tier="restart",
    ),
    FieldSpec(
        "whisper_model", "Whisper model", "Speech-to-text",
        "faster-whisper model used for transcription (e.g. large-v3, or "
        "small.en on a CPU). A model that can't load at startup drops to the "
        "CPU fallback model below, and the Models page says so. Takes effect "
        "after a restart.",
        "str", section="advanced", tier="restart",
    ),
    FieldSpec(
        "whisper_device", "Whisper device", "Speech-to-text",
        "Where Whisper runs: 'cuda' (NVIDIA GPU) or 'cpu'. cuda on a machine "
        "without one can't load, so startup drops to the CPU fallback model. "
        "Saving a device whose saved compute type can't run there (float16 on "
        "cpu) resets the compute type to 'auto'. Takes effect after a restart.",
        "choice", section="advanced", tier="restart", choices=["cuda", "cpu"],
    ),
    FieldSpec(
        "whisper_compute_type", "Whisper compute type", "Speech-to-text",
        "The number format Whisper runs in. 'auto' (the default) follows the "
        "device: float16 on cuda, int8 on cpu. int8 halves memory at "
        "near-identical accuracy and is the CPU choice; float16, bfloat16 "
        "and int8_float16 are GPU-only, so they are refused together with "
        "the cpu device. Takes effect after a restart.",
        "choice", section="advanced", tier="restart",
        choices=list(WHISPER_COMPUTE_TYPE_CHOICES),
    ),
    FieldSpec(
        "whisper_cpu_fallback_model", "Whisper CPU fallback model", "Speech-to-text",
        "What startup loads instead — on cpu, at int8 — when the configured "
        "Whisper can't load (no NVIDIA GPU, too big to fit, a compute type "
        "the device can't run). small.en suits an English-speaking household; "
        "use small for other languages; blank turns the fallback off. If "
        "this fails too, the Domovoi server starts without speech "
        "recognition. Takes effect after a restart.",
        "str", section="advanced", tier="restart",
    ),
    FieldSpec(
        "ws_ping_interval_sec", "WS ping interval", "Networking",
        "How often the Domovoi server pings each satellite's WebSocket to "
        "detect a dead connection. Lower = faster dead-socket detection, "
        "slightly more traffic. Takes effect after a restart.",
        "float", section="advanced", tier="restart", min=1, max=120, unit="sec",
    ),

    # ─── Security ──────────────────────────────────────────────────────
    FieldSpec(
        "satellite_pairing_strict", "Strict satellite pairing", "Security",
        "Require every satellite to be let in deliberately. ON (the default "
        "for a new install) = a tokenless connection is refused, and a "
        "room's first pairing waits under 'waiting for approval' until you "
        "type in the six-digit code the satellite is showing and saying — "
        "so a new or re-flashed Pi needs you at the dashboard. OFF = "
        "trust-on-first-use: a room that has never paired still accepts a "
        "tokenless connection, while any room that HAS paired is fully "
        "protected. Takes effect after a restart.",
        "bool", section="advanced", tier="restart",
    ),
    FieldSpec(
        "satellite_hello_timeout_sec", "Satellite hello deadline", "Security",
        "How long a newly connected satellite socket gets to send its hello "
        "frame before the server drops it. Nothing is provisioned or listed "
        "for a room until its hello has passed the pairing check, so a bare "
        "connection from any device on the LAN never creates a room. Real "
        "satellites say hello immediately; raise this only on a very slow "
        "link. Takes effect after a restart.",
        "float", section="advanced", tier="restart", min=1, max=60, unit="sec",
    ),
    FieldSpec(
        "satellite_adoption_enabled", "USB satellite adoption", "Security",
        "Scan the Domovoi server's USB ports for unprovisioned satellites "
        "(the plug-in-and-adopt flow on the Satellites page). Turn off to "
        "stop scanning removable drives entirely.",
        "bool", tier="hot",
    ),
    FieldSpec(
        "satellite_adoption_advertise_url", "Adoption server URL", "Security",
        "The core WebSocket URL written into adopted satellites' config "
        "(e.g. ws://192.168.1.50:6370). Leave empty to autodetect the LAN "
        "address — set it only when the server has several network "
        "interfaces and autodetection picks the wrong one.",
        "str", section="advanced", tier="hot",
    ),
]


FIELD_BY_NAME: dict[str, FieldSpec] = {f.name: f for f in EDITABLE_FIELDS}


def editable_field_names() -> set[str]:
    return set(FIELD_BY_NAME)


def coerce_and_validate(spec: FieldSpec, value: object) -> object:
    """Coerce a raw (JSON) value to the field's type and validate it
    against the spec's bounds/choices. Returns the coerced value, or
    raises ``ValueError`` with a user-facing message.

    pydantic's ``Settings`` does NOT validate on attribute assignment, so
    this is the only gate before the value is written onto the live
    singleton — it must reject a bad type/range rather than let it stick.
    """
    t = spec.type
    if t == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1", "yes", "on"):
                return True
            if v in ("false", "0", "no", "off"):
                return False
        raise ValueError("expected true or false")

    if t == "int":
        try:
            fv = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("expected a whole number")
        if fv != int(fv):
            raise ValueError("expected a whole number")
        coerced: object = int(fv)
    elif t == "float":
        try:
            coerced = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("expected a number")
    elif t in ("str", "choice"):
        coerced = str(value)
    else:  # pragma: no cover — guarded by the registry's Literal type
        raise ValueError(f"unknown field type {t!r}")

    if t == "choice" and spec.choices and coerced not in spec.choices:
        raise ValueError(f"must be one of: {', '.join(spec.choices)}")
    if t == "str" and spec.pattern and not re.fullmatch(spec.pattern, coerced):  # type: ignore[arg-type]
        raise ValueError(spec.pattern_help or "does not match the expected format")
    if spec.min is not None and coerced < spec.min:  # type: ignore[operator]
        raise ValueError(f"must be at least {spec.min}")
    if spec.max is not None and coerced > spec.max:  # type: ignore[operator]
        raise ValueError(f"must be at most {spec.max}")
    return coerced
