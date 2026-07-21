"""Radio plugin settings — env prefix ``RADIO_`` (design §4.6, §9.1).

Every radio knob lives in the plugin namespace, including the
fingerprinter's (``RADIO_FINGERPRINTER_*`` — no exceptions, design
review #4). Values persist to
``~/.domovoi/plugins/radio.env`` via the core config bridge; OS env vars
shadow the file (documented core-wide caveat).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from domovoi.sdk import FieldSpec


class RadioSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RADIO_", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Passive detection: audio sampler ───────────────────────────────
    sampler_enabled: bool = True
    # Per-station default sampling cadence; voice/web override per row.
    default_sample_interval_sec: int = 180
    # Outer-loop "any stations due?" cadence.
    sampler_inner_loop_sec: float = 30.0
    # Drop a detection when the same (station, artist, title) landed
    # within this window.
    dedup_window_sec: int = 1800
    # ffmpeg wall-clock limit per sample grab.
    ffmpeg_timeout_sec: float = 20.0
    # Concurrent ffmpeg grabs (Semaphore size).
    sample_concurrency: int = 5
    # ── Passive detection: ICY metadata poller ─────────────────────────
    icy_poller_enabled: bool = True
    icy_poll_interval_sec: float = 30.0
    icy_concurrency: int = 10
    icy_request_timeout_sec: float = 6.0

    # ── Detections retention (locked 19 — the unbounded-table fix) ─────
    # Days of radio_detections history to keep. 0 = keep forever.
    detections_retention_days: int = 90
    # Reaper tick cadence. The tick doubles as the soft-ref
    # reconciliation sweep (design §4.9/§6.1), so don't set it too slow:
    # bounded staleness of missed bus events = one interval.
    detections_reaper_interval_sec: float = 3600.0

    # ── FM market (FCC import + bare-frequency voice commands) ─────────
    market_city: str = ""
    market_state: str = ""          # 2-letter ('CO', 'WA', ...)
    # Run the FCC FM import once at startup (as a connectivity-gated
    # startup hook — never a blocking request).
    fcc_import_on_boot: bool = False

    # ── FM via RTL-SDR (optional hardware) ──────────────────────────────
    sdr_enabled: bool = False
    sdr_device_index: int = 0
    # Port ffmpeg's single-connection HTTP listener binds for the demodulated
    # FM stream. Default deliberately avoids commonly-used local ports.
    sdr_http_port: int = 6391
    # Scheme+host the room's MPD dials to reach that listener. ffmpeg
    # binds 0.0.0.0, but MPD runs in a container: ITS localhost is not
    # the Domovoi server's localhost — set this to a LAN hostname/IP that
    # resolves from inside the MPD container.
    sdr_stream_base: str = "http://127.0.0.1"

    # ── Local library fingerprinting (locked 8 — wholly in-plugin) ─────
    fingerprinter_enabled: bool = True
    fingerprinter_interval_sec: float = 60.0
    # Minimum aligned-hash count for a local fingerprint match.
    fingerprinter_match_threshold: int = 10


RADIO_FIELDSPECS: list[FieldSpec] = [
    FieldSpec(
        name="sampler_enabled", label="Audio sampler worker",
        help="Sample favorited stations with ffmpeg and identify songs.",
        group="Detection", kind="bool", tier="restart",
    ),
    FieldSpec(
        name="default_sample_interval_sec", label="Default sample interval",
        help="Seconds between samples for newly favorited stations.",
        group="Detection", kind="int",
    ),
    FieldSpec(
        name="dedup_window_sec", label="Re-detection cooldown",
        help="Ignore a repeat of the same song on the same station within this many seconds.",
        group="Detection", kind="int",
    ),
    FieldSpec(
        name="icy_poller_enabled", label="ICY metadata poller",
        help="Read now-playing titles from stations' stream metadata (cheap; preferred over sampling).",
        group="Detection", kind="bool", tier="restart",
    ),
    FieldSpec(
        name="detections_retention_days", label="Detections retention (days)",
        help="Delete detection rows older than this. 0 keeps them forever.",
        group="Detection", kind="int",
    ),
    FieldSpec(
        name="market_city", label="Market city",
        help="Preferred city for bare-frequency voice commands ('play 97.5 fm').",
        group="FM market", kind="text",
    ),
    FieldSpec(
        name="market_state", label="Market state",
        help="Two-letter state the FCC import loads and frequency lookups filter to.",
        group="FM market", kind="text",
    ),
    FieldSpec(
        name="fcc_import_on_boot", label="FCC import on boot",
        help="Refresh the FCC FM catalog once at startup (when online).",
        group="FM market", kind="bool",
    ),
    FieldSpec(
        name="sdr_enabled", label="RTL-SDR FM tuner",
        help="Enable FM tuning through an RTL-SDR dongle (optional hardware; needs rtl_fm on PATH).",
        group="FM / SDR", kind="bool", tier="restart",
    ),
    FieldSpec(
        name="sdr_stream_base", label="SDR stream base URL",
        help="Host the room's MPD dials for FM audio — MPD runs in a container, so its "
             "localhost is NOT this machine's localhost; use a LAN hostname/IP.",
        group="FM / SDR", kind="text",
    ),
    FieldSpec(
        name="sdr_http_port", label="SDR stream port",
        help="Port ffmpeg serves the demodulated FM stream on.",
        group="FM / SDR", kind="int", tier="restart",
    ),
    FieldSpec(
        name="fingerprinter_enabled", label="Library fingerprinter",
        help="Fingerprint library tracks so radio samples match songs you already own without an online lookup.",
        group="Fingerprinting", kind="bool", tier="restart",
    ),
    FieldSpec(
        name="fingerprinter_match_threshold", label="Match threshold",
        help="Minimum aligned-hash score for a local fingerprint match.",
        group="Fingerprinting", kind="int",
    ),
]
