import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives next to config.py (in domovoi/), but `python -m
# domovoi.main` is typically run from the repo root, where
# pydantic-settings' default cwd-relative lookup wouldn't find it. Pin to
# an absolute path so settings load consistently regardless of cwd.
_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    # Host port 6432, not 5432 — the compose file publishes the Domovoi
    # Postgres on 6432 so it can coexist with any other Postgres already
    # bound to the default port on the same machine.
    database_url: str = "postgresql+asyncpg://domovoi:domovoi@localhost:6432/domovoi"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"         # QA / conversational — fast, cheap
    ollama_tool_model: str = "qwen2.5:14b"    # tool-call routing — needs strong schema adherence
    # Vision-capable model for the text-chat surface: any chat message that
    # carries images is answered by this model instead of ollama_model.
    ollama_vision_model: str = "qwen2.5vl:7b"
    # Per-request read timeout (seconds) for Ollama. A stalled Ollama (model
    # still loading, GPU wedged) must not pin a user-facing voice turn open
    # forever — this bounds the wait so the turn fails gracefully instead.
    ollama_timeout_sec: float = 120.0
    searxng_url: str = "http://localhost:6888"

    connectivity_probe_target: str = "1.1.1.1:443"
    connectivity_probe_interval_sec: float = 30.0
    connectivity_probe_timeout_sec: float = 2.0

    bot_name: str = "Domovoi"
    log_level: str = "INFO"

    # ─── WebSocket keep-alives ─────────────────────────────────────────
    # Without periodic pings, a Pi's WS can go silently dead under flaky
    # wifi — the core believes the session is alive (it's still
    # in `active_sessions`) but writes vanish into a corpse socket.
    # 2026-05-10 broadcast bug traced to exactly that. uvicorn / the
    # `websockets` library on both ends handle ping/pong natively as long
    # as we tell uvicorn how often to ping; the Pi client responds with
    # zero code changes.
    #
    # 10s interval + 5s timeout means a dead WS surfaces as a
    # disconnect within ~15s, which then trips the receiver loop's
    # finally block and evicts the stale `active_sessions` entry.
    # Tighten to 5/3 if the Pi's wifi is rough enough that 15s feels
    # long; loosen if the ping traffic becomes annoying (it won't —
    # one tiny frame every 10s is nothing).
    ws_ping_interval_sec: float = 10.0
    ws_ping_timeout_sec: float = 5.0

    timer_watcher_interval_sec: float = 1.0

    # ─── Client stubs vs real implementations ───────────────────────────
    # When true, whisper/ollama/tts clients return deterministic fakes.
    # Tests set this to true so the suite runs without CUDA/Ollama/TTS deps.
    # The running app defaults to false — real clients load at startup.
    use_stubs: bool = False

    # ─── Whisper (faster-whisper on CUDA) ──────────────────────────────
    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"

    # ─── TTS engine router (edge → piper → system) ─────────────────────
    tts_engine: str = "edge"                         # preferred engine
    tts_edge_voice: str = "en-US-AriaNeural"
    tts_piper_voice: str = "en_US-lessac-medium"
    tts_speed: float = 1.0
    # Wall-clock cap on the `system` engine's subprocess (espeak-ng / say on
    # non-Windows; the Windows SAPI path is in-process and unaffected). This
    # is the last rung of the fallback chain, so a wedged binary must not
    # hold a voice turn open — fail fast and let the turn end silently.
    tts_system_timeout_sec: float = 10.0
    # Where uploaded Piper voice models (.onnx + .onnx.json) live. Reuses
    # the cache dir tts.py already downloads HF Piper voices into, so an
    # uploaded model and an auto-downloaded one sit side by side.
    voice_models_dir: str = str(Path.home() / ".domovoi" / "piper_voices")
    # Where the core renders per-voice greeting/canned clips and
    # serves them from over /v1/sounds/. A runtime artifact dir (one MP3 per
    # greeting × voice — thousands with the full catalog), NOT source: kept
    # out of the repo's satellite/ tree. Satellites pull from here over HTTP
    # into their own ~/.domovoi/sounds/ cache, so the path is server-private.
    sounds_dir: str = str(Path.home() / ".domovoi" / "sounds")
    # Root of the git clone (the working tree the core runs from).
    # domovoi/ is parents[0], the repo root is parents[1]. Used by the
    # satellite-code channel (serves satellite/ files for in-field upgrades)
    # and git_version.py (version label + behind/ahead checks run with this
    # as cwd).
    repo_dir: str = str(Path(__file__).resolve().parents[1])
    # How long a satellite gets to reconnect after an upgrade+self-restart
    # before its on-Pi watchdog rolls back to the pre-upgrade tarball. Bounds
    # the window an import-clean-but-behaviourally-broken upgrade can wedge a
    # satellite before it self-heals.
    satellite_upgrade_reconnect_timeout_sec: int = 90
    # ─── Satellite pairing tokens (WS auth, V002) ──────────────────────
    # Lenient trust-on-first-use: the FIRST satellite to present a pairing
    # token for a room claims it (a `satellite_pairings` row is written with
    # the token's sha256); thereafter that room's WS `hello` must carry the
    # matching token or the connection is refused. A room that has never
    # paired still accepts a tokenless `hello` (older/unpaired satellite) —
    # DEFAULT FALSE keeps every existing tokenless satellite working while
    # still fully protecting any room that HAS paired. Flip this to True to
    # require pairing for EVERY room (a tokenless hello for an unpaired room
    # is then refused too) — a hardening posture for an all-paired fleet.
    satellite_pairing_strict: bool = False
    # USB satellite adoption: the web backend scans removable volumes for
    # unprovisioned satellites presenting a DOMOVOI-SET gadget drive and
    # surfaces them as pending on the Satellites page. Kill switch below;
    # the advertise URL overrides the ws://<lan-ip>:6370 the adopt flow
    # derives for the device (set it when the server has several NICs and
    # the autodetected address is the wrong one).
    satellite_adoption_enabled: bool = True
    satellite_adoption_advertise_url: str = ""
    # On startup, pre-populate the voices registry with the curated catalog
    # (domovoi/voice_catalog.py) of Edge cloud + Piper local voices, so
    # they're available to list/sample/switch without manual registration.
    # Idempotent by name. NOTE: the next boot after enabling this renders
    # each registered voice's greeting clips — a slow, one-time, bandwidth-
    # heavy boot (Piper models download, Edge clips synth). Turn off once
    # you've curated the list so deleted voices don't reappear on reboot.
    seed_voice_catalog: bool = True

    # ─── Custom wake words ──────────────────────────────────────
    # Where trained openWakeWord models (<slug>.onnx, optional .onnx.json
    # companion) live, served from over /v1/wake-models/ for satellites to
    # pull into their own ~/.domovoi/wake_models/ cache. A server-private
    # runtime artifact dir, like sounds_dir — kept out of the repo tree.
    wake_models_dir: str = str(Path.home() / ".domovoi" / "wake_models")
    # Where positive clips recorded on a satellite land before training, one
    # subdir per <slug>. The trainer reads <wake_clips_dir>/<slug>/ as its
    # positive set. Also server-private.
    wake_clips_dir: str = str(Path.home() / ".domovoi" / "wake_clips")
    # Wake-word TRAINER hardware/toolchain gate. Off by default. openWakeWord
    # automatic training does NOT run on native Windows (piper-sample-
    # generator is Linux-only) and Domovoi is Windows, so the trainer never
    # trains in-process — it shells out to wake_word_train_command below.
    # When false the trainer worker is not even started; a wake word queued
    # for training just sits in 'training' until enabled. Mirrors the
    # radio_sdr_enabled "off until the toolchain is present" pattern.
    wake_word_trainer_enabled: bool = False
    # Trainer outer-loop cadence (seconds). Training itself is a long shell-
    # out, so polling the queue head infrequently is fine.
    wake_word_trainer_loop_sec: float = 30.0
    # Minimum positive clips before a wake word may be promoted to training.
    # Too few and the model won't generalize; mark_training refuses below
    # this and the web Train button stays disabled.
    wake_word_min_clips: int = 15
    # Auto-stop target for a satellite recording session — how many positive clips
    # the Pi captures before it self-terminates the take. 0 => no practical auto-stop
    # (a giant 15k safety cap; the user ends the take with the Stop button). Set an
    # explicit value only if you want the session to auto-stop at a specific count —
    # e.g. a hard mic like the XVF3800 wants HUNDREDS of real clips, so don't set
    # this low; the old 2×min (~30) default was far too small.
    wake_word_record_target_clips: int = 0
    # Seconds of audio captured per recorded clip on the satellite.
    wake_word_clip_seconds: float = 2.0
    # External Linux train-command TEMPLATE. openWakeWord automatic training
    # is Linux-only (piper-sample-generator), so on Windows Domovoi this must
    # point at a WSL2 / docker-Linux / Colab pipeline that produces the
    # model. Placeholders {clips_dir} {phrase} {slug} {out} are substituted
    # before the command is split + run (subprocess, off the event loop). On
    # success the trainer expects <wake_models_dir>/<slug>.onnx to exist.
    # EMPTY (the default) disables training even when the trainer is enabled:
    # a queued row is marked failed with a runbook pointer. See
    # scripts/wake_word/README.md for the operator-supplied command.
    wake_word_train_command: str = ""

    # ─── MPD (lazy per-room provisioning) ──────────────────────────────
    # An MPD daemon per voice-satellite room keeps playback queues / current
    # track / volume independent across rooms. Containers are spawned on
    # first WebSocket connect for an unknown room_id (see
    # `mpd_provisioner.py`) and persisted in the `mpd_rooms` table so port
    # assignments survive server restart. No teardown on disconnect —
    # idle MPDs cost ~20 MB RAM each and Pis routinely drop WiFi.
    #
    # mpd_host is where MPD's control port lives from the core's
    # POV (always localhost since the containers run on the same host).
    # mpd_http_base is the URL prefix the Pi uses to reach the per-room
    # HTTP stream — needs a LAN-routable hostname (not localhost, which
    # resolves to the Pi itself).
    mpd_host: str = "localhost"
    mpd_http_base: str = "http://localhost"

    # Image tag the provisioner builds + runs. Kept stable so existing
    # containers can be `docker start`-ed without recreating them.
    mpd_image_tag: str = "domovoi-mpd:latest"

    # Container + volume name prefixes. The provisioner appends a docker-
    # safe form of the room_id. Example: room "kitchen" → container
    # `domovoi-mpd-kitchen`, volume `domovoi-mpd-kitchen-data`.
    mpd_container_prefix: str = "domovoi-mpd-"
    mpd_volume_prefix: str = "domovoi-mpd-"

    # First room gets these ports; subsequent rooms get the next free pair
    # (max + 1). Bases sit at 6650/8050 — clear of MPD's conventional 6600
    # range and the 8001+ range — so Domovoi's per-room daemons can coexist
    # with any other MPD fleet already running on the same machine.
    mpd_port_base_control: int = 6650
    mpd_port_base_http: int = 8050

    # How long to wait for a freshly-spawned MPD's control port after
    # `docker run`. First boot does library scan + httpd init; tens of
    # seconds is normal on a big library. We log a warning instead of
    # raising on timeout so non-music handlers keep working.
    mpd_provision_timeout_sec: float = 30.0

    # Volume every freshly (re)started MPD daemon is pinned to. The
    # satellite's hardware mixer is the single volume control for both TTS
    # and music, so MusicHandler keeps MPD at 100% to avoid double
    # attenuation (hardware × MPD). MPD persists volume in its state file,
    # so a room whose data volume was wiped — or one created before the
    # provisioner learned to pin — can boot at a stale/low level and
    # silently attenuate music. The provisioner re-asserts this on every
    # container start so that invariant holds without waiting for the user
    # to issue a volume command. Keep at 100 unless you deliberately want
    # MPD, not the satellite, to be the attenuator.
    mpd_startup_volume: int = 100

    # Fallback timeout for the music_ready handshake. Handlers queue the
    # song into MPD paused; the streaming layer asks the Pi to spawn
    # mpg123 (music_start frame) and waits for a `music_ready` frame back
    # before resuming MPD. If the Pi never replies — old satellite that
    # doesn't speak the new frame, dead WS, etc. — we resume after this
    # many seconds so playback isn't held hostage. Must comfortably exceed
    # the satellite's `music_prime_sec` plus worst-case TTS drain.
    music_prepare_fallback_sec: float = 5.0

    # ─── Music library path (provider downloads land here; MPD reads same dir) ──
    # Host path. MPD container mounts this read-only as /music.
    music_dir: str = os.path.expanduser("~/Music")

    # ─── Album-art cache (browser music player) ─────────────────────────
    # Where the web backend caches album art extracted from library files
    # (mutagen) so the browser player and Media Session can render real
    # cover images. Mirrors ``music_dir`` as a plain host path; the web
    # process owns writes here (source files in ``music_dir`` are only
    # ever read). Files are keyed by ``<track_id>`` with the embedded
    # image's native extension; a ``<track_id>.none`` sentinel records
    # "checked, no embedded art" so artless tracks aren't re-probed on
    # every request. The GET /api/music/library/{id}/cover endpoint reads
    # and writes this dir; nothing else in the core touches it.
    cover_art_dir: str = os.path.expanduser("~/.domovoi/cover_art")

    # ─── Video poster cache (browser/Android Videos tab) ────────────────
    # Mirrors cover_art_dir: the web backend caches one ffmpeg-extracted
    # poster frame per video here, keyed by a hash of (library, path,
    # mtime, size) with a ``.none`` sentinel for files ffmpeg can't read,
    # so unplayable/artless files aren't re-probed on every request. The
    # GET /api/videos/poster endpoint owns this dir; nothing else touches it.
    video_posters_dir: str = os.path.expanduser("~/.domovoi/video_posters")

    # ─── Pictures library + Images tab ──────────────────────────────────
    # Host path exposed as the core:pictures Files library and walked by the
    # Images tab. Generated images land in the "Domovoi Generated" subfolder.
    pictures_dir: str = os.path.expanduser("~/Pictures")
    # Browser-thumbnail cache for the Images tab (Pillow-resized, keyed by
    # size bucket + content hash with .none sentinels — cover-art pattern).
    image_thumbs_dir: str = os.path.expanduser("~/.domovoi/image_thumbs")

    # (Image GENERATION is not a core feature — it ships as the separately
    # installed Image Generation plugin, which manages its own ComfyUI
    # engine and config under the IMAGEGEN_ prefix.)

    # ─── Spoken audio (Podcasts + Audiobooks) ─────────────────────────
    # Host paths, mirroring music_dir. Downloaded podcast episodes land in
    # podcasts_dir; local audiobook files (.m4b / per-chapter folders) live
    # in audiobooks_dir. Both are bind-mounted READ-ONLY into every per-room
    # MPD container as nested subdirs of /music (see mpd_provisioner.py) so
    # MPD indexes and can stream them to satellites, exactly like music_dir.
    podcasts_dir: str = os.path.expanduser("~/.domovoi/podcasts")
    audiobooks_dir: str = os.path.expanduser("~/.domovoi/audiobooks")

    # Podcast feed poller. OFF by default (toolchain-gated like the radio
    # SDR / wake-word trainer): polling subscribed feeds needs network and a
    # working download toolchain, so an unconfigured deployment doesn't spin the
    # loop. When enabled, the poller walks podcast_subscriptions, records
    # new episodes, enqueues the newest-N for download, and LRU-evicts older
    # downloaded episodes past each sub's keep_n.
    podcast_feed_poller_enabled: bool = False
    # Outer-loop cadence — how often the poller checks every subscription's
    # feed. Feeds update on the order of hours/days, so 30 min is generous;
    # per-feed last_polled_at isn't separately throttled (the whole set is
    # cheap: one HTTP GET + parse each).
    podcast_feed_poller_interval_sec: float = 1800.0
    # Default newest-N episodes to keep downloaded per show. Per-subscription
    # keep_n overrides this; used only as the default when a subscription
    # doesn't set its own. LRU eviction removes downloaded episodes beyond
    # this window (the file is deleted, the row flips to 'skipped').
    podcast_keep_n: int = 5
    # Audiobook indexer cadence (like library_fingerprinter_inner_loop_sec).
    # Books are added rarely (drop an .m4b in, import a LibriVox title), so a
    # slow poll is fine; the startup sweep + a manual reindex endpoint cover
    # the interactive cases.
    audiobook_indexer_enabled: bool = True
    audiobook_indexer_interval_sec: float = 300.0

    # ─── Documents suite (Documents / Spreadsheets / Drawings) ─────────
    # Root of user document storage. Mirrors music_dir: a single FLAT dir
    # holding every type mixed together (a .md, .xlsx, and .excalidraw
    # all sit side-by-side). The pages are filtered VIEWS over this one
    # directory, never separate stores. The web backend reads this via
    # `from domovoi.config import settings as core_settings` exactly like
    # music.py reads music_dir. Editing is fully homegrown/in-page
    # (markdown doc editor, x-spreadsheet grid, Excalidraw) — the former
    # OnlyOffice/Collabora sidecar containers are retired.
    documents_dir: str = os.path.expanduser("~/Documents")

    # ─── Media acquisition queue (design §4.8) ─────────────────────────
    # The queue holds USER-requested media (voice adds, dashboard adds,
    # save-to-device) until an installed provider plugin fulfills them —
    # core never initiates acquisitions on its own.
    # Terminal-failure ceiling: a fulfiller retrying via `fail(retry_in=…)`
    # flips the row to 'failed' once attempts reaches this. Fulfiller poll
    # cadence is each provider plugin's own setting, not core's.
    acquisition_max_attempts: int = 3
    # Cadence of the core playback-state sweeper (stale now-playing stamps,
    # current_playlist hygiene, natural-end music_stop pushes to the Pi).
    playback_sweeper_interval_sec: float = 5.0

    # ─── Two-way drop-in (live room-to-room audio) ─────────────────────
    dropin_enabled: bool = True
    # How a target room accepts an incoming drop-in:
    #   'auto'    → the target's mic opens immediately (Alexa-style)
    #   'confirm' → the target hears a prompt and must say yes first
    dropin_accept_mode: str = "auto"
    # Auto-end a call after this many seconds of two-way silence.
    dropin_silence_timeout_sec: float = 20.0
    # Echo mitigation for calls between NEARBY rooms. The XVF3800's on-chip
    # AEC cancels each Pi's own speaker, but not a neighbor's speaker bleeding
    # in through a doorway — so close rooms can echo/ring. Relay frames quieter
    # than this (dBFS) aren't forwarded to the peer — silences bounced echo /
    # room tone — and (since only gate-passing frames reset the silence timer)
    # this lets dropin_silence_timeout_sec actually fire on a genuinely quiet
    # call. Lower = more permissive; -120 ~ disabled. To lower call loudness on
    # the fly, just use the normal voice volume command mid-call ("hey jarvis,
    # turn it down to 3") — same wake-during-call path as "hang up". 'hot'-
    # editable via the web gear.
    dropin_relay_gate_dbfs: float = -55.0

    # ─── Media-play history ─────────────────────────
    # The Satellites "Recently played" tab records one row per play.
    # The pruner deletes rows older than this many days so the table
    # doesn't grow unbounded; <= 0 disables pruning (keep forever).
    media_plays_retention_days: int = 90
    media_plays_pruner_interval_sec: float = 21600.0  # 6h

    # ─── Library enricher ──────────────────────────────────────────────
    # Walks library_tracks for files with no enriched_at and tries to
    # identify each one via AcoustID (Chromaprint fingerprint → MusicBrainz)
    # and shazamio (Shazam's API). Updates title/artist/album/MB-ID
    # with the canonical values. See domovoi/workers/library_enricher.py.
    library_enricher_enabled: bool = True
    # Free key from https://acoustid.org/api-key (10s to register).
    # Empty string = AcoustID is skipped, shazamio carries the load alone.
    acoustid_api_key: str = ""
    # Polite delay between API calls. AcoustID's free tier is "be
    # reasonable" — 1/sec keeps us well under any threshold.
    library_enricher_delay_sec: float = 1.0
    # Minimum AcoustID match score (0–1). Below this, ignore the
    # match and fall through to Shazam — protects against AcoustID's
    # occasional confident-but-wrong responses on noisy fingerprints.
    library_enricher_acoustid_min_score: float = 0.7

    # ─── Session context ───────────────────────────────────────────────
    session_recent_turns_cap: int = 20

    # ─── Voice profiling (VoiceProfileHandler) ────────────────────────
    voice_profile_soft_tier_sec: int = 3600       # below → LOW urgency
    voice_profile_hard_tier_sec: int = 14400      # above → HIGH urgency

    # Cosine-similarity threshold for "this embedding matches a known
    # person." Resemblyzer's authors recommend ~0.75 for same-speaker
    # acceptance — lower lets cousins / siblings collide, higher rejects
    # the same person on a cold or distant mic. Tunable per-household.
    voice_profile_match_threshold: float = 0.75

    # Minimum utterance length (in seconds of int16 PCM @ 16 kHz) before
    # we even attempt embedding. Resemblyzer needs ~1 s of voiced audio
    # to produce a stable vector; sub-second clips are mostly noise and
    # produce embeddings that match nobody, polluting the audit trail.
    voice_profile_min_utterance_sec: float = 1.0

    # ─── Drift handling ─────────────────────────────────
    # Embeddings shift over time — colds, mic distance, room acoustics,
    # age. A profile enrolled at high similarity might gradually hover
    # near the match threshold under shifting conditions, producing
    # inconsistent identification. When the matcher sees N consecutive
    # confident-but-near-threshold matches for the same person, we
    # append a fresh sample so the next round has a closer reference
    # vector to match against. Both knobs tuned conservatively — the
    # cost of a stale extra sample is one row of BYTEA; the cost of
    # missing a drift event is just delaying the re-enroll.
    #
    # `near_threshold_margin` defines the band above the match threshold
    # that counts as "near": [match_threshold, match_threshold + margin].
    # `reenroll_after` is how many consecutive near-threshold matches
    # before we append a sample.
    voice_profile_drift_near_threshold_margin: float = 0.05
    voice_profile_drift_reenroll_after: int = 3

    # ─── Third-party intro flow ────────────────────────────────────────
    # When a known speaker says "Domovoi, this is Alex," we park an
    # expectation in sessions.context for the rest of the window and
    # buffer any unknown-voice utterances into clusters. When the
    # introducer next speaks AND a cluster scores above the classifier
    # threshold, we append "By the way, was that <name>?" to their
    # response and enroll on a yes.
    third_party_intro_ttl_sec: int = 600  # 10 minutes
    # Cosine similarity above which two unknown-voice samples merge
    # into the same cluster. Looser than the match threshold because
    # we'd rather pool same-speaker samples than split them.
    third_party_cluster_merge_threshold: float = 0.7
    # Minimum classifier confidence to surface a "was that Alex?" ask.
    # Below this, the cluster stays buffered for the next introducer
    # turn; the TTL eventually drops it if nothing materializes.
    third_party_classifier_threshold: float = 0.6
    # Minimum total audio across a cluster before we even bother running
    # the classifier — Resemblyzer-grade enrollment needs ~1.5 s.
    third_party_min_cluster_audio_sec: float = 1.2

    # ─── Radio streaming + passive song detection ──────────────────────
    # The radio feature has two halves: a background sampler that
    # captures audio from favorited stations and IDs songs via dejavu
    # (local fingerprint) → shazamio (online), and a voice/web surface
    # for streaming a station to a satellite. Both halves are gated so
    # an unconfigured deployment doesn't burn ffmpeg cycles on stations
    # nobody favorited.
    radio_sampler_enabled: bool = True
    # Per-station "how often to sample" default. Voice / web override
    # per-favorite. 180 s catches a typical 3-min song reliably without
    # paying for back-to-back identifies of the same track.
    radio_default_sample_interval_sec: int = 180
    # How often the sampler's outer loop checks "any stations due?"
    # The per-station interval is enforced via last_sampled_at; this
    # is just the polling cadence. 30 s means a freshly-favorited
    # station starts sampling within a half-minute.
    radio_sampler_inner_loop_sec: float = 30.0
    # Drop a detection if the same (station, artist, title) was
    # written within this window. Stops one song playing on a station
    # for 4 minutes from producing 4× the same detection at the 60 s
    # sampler cadence.
    radio_dedup_window_sec: int = 1800
    # ffmpeg subprocess wall-clock limit per sample grab. Stream
    # connect + 15 s of capture + transcode usually finishes in
    # ~17 s; 20 s gives a small margin without leaving zombie
    # subprocesses around.
    radio_ffmpeg_timeout_sec: float = 20.0
    # Concurrent stations the sampler will grab from at once. Each
    # sample spawns one ffmpeg subprocess; capping at 5 keeps the host
    # responsive even when the user has favorited 20+ stations on the
    # same tick.
    radio_sample_concurrency: int = 5
    # ICY metadata poller — lighter-weight companion to the audio
    # sampler. Reads SHOUTcast/Icecast ``StreamTitle`` headers off the
    # stream every interval. Cheaper than ffmpeg+Shazam by orders of
    # magnitude; the sampler stays in place as a fallback for stations
    # that don't advertise ICY metadata.
    radio_icy_poller_enabled: bool = True
    # Outer-loop cadence for the poller. 30 s is short enough that a
    # song transition lands in the UI within tens of seconds of the
    # actual track change but doesn't hammer any one station.
    radio_icy_poll_interval_sec: float = 30.0
    # Concurrent HTTP fetches the poller will run at once. ICY polls
    # are cheap (one HTTP GET each), so this can run much higher than
    # the audio sampler — bottleneck is bandwidth, not CPU.
    radio_icy_concurrency: int = 10
    # Per-request timeout for one ICY fetch. Generous enough to ride
    # out a slow TLS handshake on a distant station but tight enough
    # that a hung station doesn't stall its semaphore slot.
    radio_icy_request_timeout_sec: float = 6.0
    # Configured location. Used to filter the FCC FM Query import to
    # the user's actual market and to disambiguate bare frequency
    # voice commands ("play 97.5 fm" → which 97.5 in this state?).
    # Both empty = no FCC filter and frequency commands resolve via
    # whichever 97.5 was imported first.
    radio_market_city: str = ""
    radio_market_state: str = ""           # 2-letter ('CO', 'WA', etc.)
    # If true, the core triggers the FCC FM import once on
    # startup. Off by default because the import hits the FCC for
    # ~30 s and most deployments will trigger it manually from the
    # dashboard's "Import FCC" button.
    radio_fcc_import_on_boot: bool = False
    # RTL-SDR hardware. Off until the dongle physically arrives + the
    # WinUSB driver is installed. The core probes for the
    # device on startup; absence logs a clear disabled-with-reason
    # message rather than raising.
    radio_sdr_enabled: bool = False
    radio_sdr_device_index: int = 0
    # Port the rtl_fm + ffmpeg pipeline exposes its HTTP audio stream
    # on. Pi satellites pull from here via mpd.play_url(). 8090
    # because the room-MPD HTTP ports start at 8050 and we want room
    # for those to expand.
    radio_sdr_http_port: int = 8090
    # Base URL (scheme + host) the MPD client should connect to when
    # playing the FM stream. The ffmpeg listener always binds to
    # 0.0.0.0 (so any consumer on the LAN can reach it), but the URL
    # we hand to MPD must resolve from MPD's perspective. For Docker
    # Desktop on Windows the container's localhost != Domovoi's
    # localhost, so 127.0.0.1 won't work — use the same LAN hostname
    # MPD_HTTP_BASE uses. Default keeps the simplest dev case
    # (domovoi + MPD both on the host without Docker) working.
    radio_sdr_stream_base: str = "http://127.0.0.1"

    # ─── Implicit memory extraction ────────────────────────
    # Background worker that walks ``conversation_log`` for known
    # people and surfaces long-term-worthwhile facts as
    # ``memories`` rows with ``source='implicit', status='pending'``.
    # Confirmed pending rows promote to ``active`` via the router-side
    # surfacing flow ("hey, you've mentioned X — should I remember
    # that?"). Off-by-default switch lets a household opt out
    # entirely without removing the worker.
    memory_extractor_enabled: bool = True
    # Outer-loop cadence. The threshold check is cheap so polling
    # frequently is fine, but extraction itself ties up the QA model
    # for a few seconds — 60s keeps the worker from contending with
    # interactive turns when the household is chatty.
    memory_extractor_loop_sec: float = 60.0
    # New ``conversation_log`` rows since last extract needed before
    # we re-run. 20 is roughly one substantial conversation; tune up
    # if extracts feel too frequent, down if the household talks
    # constantly and we miss patterns.
    memory_extractor_threshold_turns: int = 20
    # Minimum LLM confidence to write a pending memory. The extractor
    # asks Ollama for a {fact, confidence} JSON list; rows below this
    # threshold are dropped on the floor (logged at DEBUG). Anything
    # we DO write is awaiting user confirmation, so this is a quality
    # filter, not a safety boundary.
    memory_extractor_min_confidence: float = 0.6
    # Cooldown before re-offering a pending memory whose previous
    # offer the user didn't answer. 24h means a "Should I remember
    # X?" that fell into background noise gets one fresh shot the
    # next day instead of pestering the user every turn.
    memory_extractor_offer_cooldown_sec: int = 86400
    # Per-extraction transcript budget. Pull at most N turns of
    # conversation_log for each pass — keeps the Ollama prompt under
    # a few KB even when the threshold is bumped high.
    memory_extractor_max_turns_per_pass: int = 80

    # ─── News ───────────────────────────────────────────────────
    # Per-person topics of interest + house-scope geographic briefings,
    # fed by RSS with SearxNG-assisted feed discovery, summarized per-person
    # by the local Ollama. Gating mirrors the radio worker pattern.
    #
    # `news_enabled` is the master switch: it gates the daily background
    # fetch of the general (local/national/global) briefing AND the news
    # worker's registration. Off = no background fetch, and the verbal
    # briefing has only whatever's already cached.
    news_enabled: bool = True
    # Hour of day (0-23, local time) the daily fetch runs. Early morning so
    # the briefing is ready before anyone asks "what's the news today".
    news_fetch_hour: int = 5
    # Rolling retention window. Items older than this are swept after each
    # fetch — EXCEPT favorited items, which are never auto-deleted.
    news_retention_days: int = 90
    # Gates ONLY the daily background job's fetching of topics-of-interest
    # feeds. It does NOT authorize any verbal fetch — those confirm per
    # request (see NewsHandler). Off by default: a household opts in to
    # having Domovoi pull their topic feeds every morning. The general
    # local/national/global briefing rides `news_enabled`, not this.
    news_auto_fetch: bool = False
    # How many items each ask returns (per geographic scope in the general
    # briefing, and per topics/subject query). Editable by voice ("give me
    # 5 stories") and in web settings.
    news_items_per_ask: int = 3
    # Locale for LOCAL news (city / region). Needed to make "local"
    # meaningful — used to build or SearxNG-discover the local-scope feed.
    # Empty = no local scope (the general briefing drops the local block).
    news_location: str = ""
    # Feed-discovery caps. When a free-form topic is added we query SearxNG,
    # probe the top result URLs for RSS autodiscovery, validate by parsing,
    # and attach up to this many feeds. Keeps a noisy topic from accreting
    # dozens of junk feeds.
    news_discovery_max_feeds: int = 4
    # How many SearxNG result URLs to probe for RSS per discovery run.
    news_discovery_max_probe: int = 8
    # News fetcher outer-loop cadence (seconds). The worker wakes on this
    # cadence and only actually fetches when the configured fetch hour has
    # arrived and it hasn't already run today. 15 min keeps the "did the
    # hour tick over" check cheap without a cron dependency.
    news_fetcher_loop_sec: float = 900.0

    # ─── Conversational chat mode (#8) ─────────────────────────────────
    # A wake-word-triggered open-mic conversation backed by a self-hosted
    # Letta agent on the LOCAL Ollama, distinct from default command mode
    # (which stays on the fast-path router). Only conversational turns hit
    # Letta; command latency is untouched. The whole feature is gated OFF
    # by default: when False, get_letta_client() returns the stub and the
    # ChatModeHandler's enter phrases are inert, so the core runs
    # exactly as before. Mirrors the radio_sdr_enabled "off until the
    # plumbing is present" pattern — flipping this on assumes the Letta
    # container is up (docker compose up letta) and the required Ollama
    # models are pulled. The LIVE path (real Letta client + local-model
    # tool-calling) is a documented SPIKE; see domovoi/README.md.
    chat_mode_enabled: bool = False
    # How long the Pi's open mic stays in chat mode without a fresh
    # utterance before the conversation auto-ends. A continuous open mic is
    # a hot mic; this bounds an abandoned chat so the satellite returns to
    # the wake-word gate instead of streaming room tone to Letta forever.
    chat_silence_timeout_sec: float = 30.0
    # Where the self-hosted Letta server listens. Its OWN container with
    # its OWN bundled Postgres+pgvector — do NOT point it at the
    # domovoi DB (that would self-manage a schema outside Flyway and
    # break the migration-only invariant). On Windows the container reaches
    # the native Ollama via host.docker.internal; this URL is how the
    # domovoi (on the host) reaches Letta.
    letta_base_url: str = "http://localhost:6283"
    # Shared secret for the Letta server (LETTA_SERVER_PASSWORD in the
    # compose service). Must match the container's env. Local-only;
    # Letta is never exposed off-LAN.
    letta_token: str = "domovoi-local"
    # Letta LLM handle for the conversational agent — routes both the chat
    # turns and the bridged tool-calls. qwen2.5:14b mirrors the tool-call
    # router model (strong schema adherence is what matters for the tool
    # bridge). Served by the native Windows Ollama via host.docker.internal.
    letta_model: str = "ollama/qwen2.5:14b"
    # Embedding handle for the Letta agent's archival/recall memory.
    # Embeddings are REQUIRED on self-hosted Letta — agent creation fails
    # without a working embedding config — so this must resolve to a model
    # the local Ollama has pulled (ollama pull nomic-embed-text).
    letta_embedding_model: str = "ollama/nomic-embed-text:latest"
    # Base URL a Letta TOOL (running inside Letta's own container sandbox) uses to
    # call BACK into the core for chat-mode tool execution — the
    # POST /v1/admin/chat-tool endpoint. From inside the Letta container the host
    # is reached at host.docker.internal (same as OLLAMA_BASE_URL), NOT localhost.
    letta_tool_callback_url: str = "http://host.docker.internal:6370"


settings = Settings()
