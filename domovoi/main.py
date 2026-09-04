from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

# Bootstrap NVIDIA DLLs BEFORE any import that transitively loads ctranslate2 /
# faster-whisper. Safe no-op on non-Windows / when USE_STUBS=true.
from domovoi import bootstrap  # noqa: E402

bootstrap.register_nvidia_dlls()

from pathlib import Path  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.responses import Response as FastAPIResponse  # noqa: E402
from sqlalchemy import text  # noqa: E402

from domovoi import admin_auth as admin_auth_mod  # noqa: E402
from domovoi import git_version  # noqa: E402
from domovoi import self_restart  # noqa: E402
from domovoi.admin_auth import (  # noqa: E402
    check_outbound_fetch,
    require_admin_mutation,
    require_admin_read,
    token_sha256,
)
from domovoi.canned_sounds import _SOUNDS_DIR as SOUNDS_DIR  # noqa: E402
from domovoi.canned_sounds import regenerate_if_needed as regenerate_canned_sounds  # noqa: E402
from domovoi.canned_sounds import voice_dir  # noqa: E402
from domovoi.db.repositories import VoicesRepository  # noqa: E402
from domovoi.clients.ollama import get_ollama_client  # noqa: E402
from domovoi.clients.tts import get_tts_client  # noqa: E402
from domovoi.clients.whisper import get_whisper_client  # noqa: E402
from domovoi.config import settings  # noqa: E402
from domovoi.connectivity import ConnectivityProbe  # noqa: E402
from domovoi.db.session import session_scope  # noqa: E402
from domovoi.handlers import HANDLERS  # noqa: E402
from domovoi.lifecycle import install_signal_handlers, signal_shutdown  # noqa: E402
from domovoi.models import (  # noqa: E402
    ConnectivityState,
    Context,
    HandlerInfo,
    Intent,
    Response,
)
from domovoi.acquisitions import ACQUISITIONS  # noqa: E402
from domovoi.capabilities import CAPABILITIES  # noqa: E402
from domovoi.now_playing import NOW_PLAYING  # noqa: E402
from domovoi.router import route  # noqa: E402
from domovoi.streaming import StreamSession  # noqa: E402
from domovoi.workers.timer_watcher import TimerWatcher  # noqa: E402
from domovoi.workers.playback_state_sweeper import PlaybackStateSweeper  # noqa: E402
from domovoi.workers.media_plays_pruner import MediaPlaysPruner  # noqa: E402

logging.basicConfig(
    level=settings.log_level,
    # ISO-ish timestamp with milliseconds + level + logger name + message.
    # Milliseconds matter for tracing the rtl_fm / ffmpeg / mpd race
    # window around a tune (everything finishes inside a few hundred ms).
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


class _SuppressNoisyAccessPaths(logging.Filter):
    """Drop uvicorn access-log lines for endpoints that the web
    backend polls every ~1.5 s — without this filter the core
    log is 90 %+ snapshot/health spam and real interesting events
    (errors, rejected intents, slow turns) get drowned out. The
    requests still happen and still return 200; we just don't print
    a line per poll.

    Filtered paths are exact-match against the request path embedded
    in uvicorn's access-log format string. Any path NOT on this list
    logs as usual.
    """

    _SUPPRESSED_FRAGMENTS = (
        '"GET /v1/admin/snapshot ',
        '"GET /v1/health ',
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            line = record.getMessage()
        except Exception:
            return True
        return not any(frag in line for frag in self._SUPPRESSED_FRAGMENTS)


logging.getLogger("uvicorn.access").addFilter(_SuppressNoisyAccessPaths())


async def seed_voices() -> None:
    """Ensure the voice registry is populated. Seeds the configured
    Edge + Piper voices plus — when ``SEED_VOICE_CATALOG`` is on — the
    curated catalog from ``domovoi.voice_catalog``, so an existing
    install keeps whatever Domovoi speaks today as the default while a fresh
    set of cloud + local voices is available to list/sample/switch with no
    manual step. Idempotent (by name + model_ref); preserves any existing
    default."""
    from domovoi.db.repositories import VoicesRepository
    from domovoi.voice_catalog import seed_voices as seed_voice_registry

    async with session_scope() as s:
        created = await seed_voice_registry(
            VoicesRepository(s),
            include_catalog=settings.seed_voice_catalog,
            edge_voice=settings.tts_edge_voice,
            piper_voice=settings.tts_piper_voice,
            default_is_piper=settings.tts_engine == "piper",
        )
    if created:
        log.info(
            "voice registry: created %d voice(s) (catalog=%s)",
            created, settings.seed_voice_catalog,
        )


async def load_greeting_phrases() -> list[str]:
    """The enabled wake-word greeting texts, with ``{name}`` resolved to the
    bot name — the same lines the satellites play on wake. Stamped into
    ``app.state.greeting_phrases`` so the streaming layer can strip a
    greeting that bled past the array AEC out of a transcript (see
    domovoi/greeting_filter.py). Best-effort: a DB hiccup yields []."""
    from domovoi.db.repositories import ClientGreetingsRepository

    try:
        async with session_scope() as s:
            rows = await ClientGreetingsRepository(s).all_enabled()
    except Exception as e:
        log.warning("could not load greeting phrases for transcript filtering: %s", e)
        return []
    name = settings.bot_name
    return [text.replace("{name}", name) for text, _ in rows]


def _register_core_reapply_hooks() -> None:
    """Core ``tier="reapply"`` config fields → subsystem pokes, through
    the reapply-hook registry (design §4.6 — replaces the hardcoded
    if/elif that used to live in the config-write endpoint). Plugins
    register theirs via ``ctx.on_reapply``; core uses the same pattern.
    Keyed registration makes re-entering the lifespan idempotent."""
    import logging as _logging

    from domovoi import reapply
    from domovoi.clients.ollama import reset_ollama_client
    from domovoi.clients.tts import reset_tts_client

    def _reapply_log_level() -> None:
        level = str(settings.log_level)
        _logging.getLogger().setLevel(level)
        _logging.getLogger("domovoi").setLevel(level)

    for field in ("tts_engine", "tts_speed"):
        reapply.on_reapply(field, reset_tts_client)
    for field in ("ollama_model", "ollama_tool_model", "ollama_vision_model"):
        reapply.on_reapply(field, reset_ollama_client)
    reapply.on_reapply("log_level", _reapply_log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    install_signal_handlers()
    _register_core_reapply_hooks()

    # Pre-warm the clients so first-request latency is bounded.
    # In stub mode these are instant; with real clients this blocks for
    # Whisper load (~30 s on large-v3).
    # Pin the version label to the code this process actually imported,
    # before anything can pull underneath us. See git_version.capture_boot_state.
    await git_version.capture_boot_state()

    log.info("warming clients (use_stubs=%s)", settings.use_stubs)
    get_ollama_client()
    get_tts_client()
    if not settings.use_stubs:
        # Whisper is the slow one; load before accepting traffic.
        get_whisper_client()

    # Seed the voice registry from the live TTS settings if it's empty, so
    # the per-voice clip renderer and the streaming voice resolver have a
    # default to work from. DB-only + idempotent — safe under stubs.
    try:
        await seed_voices()
    except Exception as e:
        log.warning("voice registry seed raised: %s", e)

    # First-run admin setup code (design §7.2): while no admin credential
    # exists, write the 8-word code to ~/.domovoi/setup-code.txt and print
    # it to this console — proof of possession of the server for
    # POST /api/auth/setup. Deleted automatically once setup completes.
    try:
        await admin_auth_mod.ensure_setup_code_if_unclaimed()
    except Exception as e:
        log.warning("setup-code boot hook raised: %s", e)

    # Enabled greeting texts, for stripping a bled-in wake greeting out of
    # transcripts (greeting_filter). Refreshed by the regenerate endpoint
    # when the bank is edited.
    app.state.greeting_phrases = await load_greeting_phrases()

    # §12 startup-hook milestone: the boot DB work above (voice seed +
    # greeting load) has run, so plugin hooks with after="core.db_ready"
    # may proceed once the runner starts them below.
    from domovoi.plugins_runtime.workers import WORKERS as _WORKERS_EARLY

    _WORKERS_EARLY.mark_core_hook_done("core.db_ready")

    # Serializes background clip re-renders triggered by the web UI (adding a
    # voice / editing greetings) so overlapping triggers don't render at once.
    app.state.sounds_render_lock = asyncio.Lock()
    app.state.sounds_render_task = None
    # Serializes web-UI config saves so two concurrent PATCHes can't
    # interleave settings mutation + .env rewrite.
    app.state.config_apply_lock = asyncio.Lock()

    # Ensure the spoken-audio storage dirs exist so the MPD nested
    # bind mounts (podcasts/audiobooks → /music/…) succeed, the podcast
    # downloader can write episodes, and the audiobook indexer has a tree to
    # walk — all before the first satellite connects. Host-owned dirs,
    # mirroring how music_dir / cover_art_dir are expected to exist.
    for _spoken_dir in (settings.podcasts_dir, settings.audiobooks_dir):
        try:
            Path(_spoken_dir).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("could not create spoken-audio dir %s: %s", _spoken_dir, e)

    # Ensure the wake-word artifact dirs exist (Feature 5) so the
    # /v1/wake-models channel can serve an empty manifest, the streaming
    # clip-writer can drop clips, and the trainer can write models — all
    # before the first model is trained. Server-private runtime dirs,
    # mirroring how sounds_dir / voice_models_dir are created lazily.
    for _wake_dir in (settings.wake_models_dir, settings.wake_clips_dir):
        try:
            Path(_wake_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("could not create wake dir %s: %s", _wake_dir, e)

    # Re-render the satellite-side canned MP3s (network_issues.mp3 today)
    # if the configured TTS voice has changed since the last render.
    # No-op when up-to-date; doesn't hit the network when nothing needs
    # regenerating. Skipped under stubs to keep tests deterministic.
    if not settings.use_stubs:
        try:
            await regenerate_canned_sounds()
        except Exception as e:
            log.warning("canned sound regeneration raised: %s", e)

    # MPD: ensure the image exists and restart any rooms provisioned
    # in past runs so a Pi reconnect doesn't have to wait for a fresh
    # `docker run`. Stub mode skips both — tests don't drive docker.
    if not settings.use_stubs:
        from domovoi.mpd_provisioner import ensure_image, warm_known_rooms

        try:
            await ensure_image()
            await warm_known_rooms()
        except Exception as e:
            # Don't block domovoi startup — non-music handlers
            # (timer, QA, library metadata) should still work even if
            # docker is misbehaving.
            log.warning("MPD startup warm failed: %s", e)
    # §12 milestone: MPD image/room warm has been attempted (best-effort
    # by design) — plugin hooks with after="core.mpd_provisioner" run.
    _WORKERS_EARLY.mark_core_hook_done("core.mpd_provisioner")

    # room_id → currently-playing stream URL. Updated by streaming.py
    # when a response sets music_action="start" (recorded) or "stop"
    # (cleared). Read by streaming.py when a response has no explicit
    # music_action so playback can auto-resume after non-music turns
    # like "what time is it" — wake-word capture kills the Pi's mpg123
    # to free the mic, so without a resume frame the music stays dead.
    # In-memory by design: server restart wipes it, which lines
    # up with the user's mental model since restart kills music too.
    app.state.resumable_music = {}

    # room_id → {"url": str, "task": asyncio.Task} for the music_ready
    # handshake. Populated when the streaming layer sends `music_start`
    # to a Pi after a handler queued a paused song in MPD; cleared when
    # the Pi acks with `music_ready` (which also resumes MPD) or when
    # the fallback timer fires after `music_prepare_fallback_sec`. See
    # `streaming.schedule_music_resume_fallback` for the contract.
    app.state.pending_music_start = {}

    # room_id → active StreamSession. Populated by `StreamSession.run()`
    # on accept and torn down on disconnect. Used by IntercomHandler to
    # fan out "announce to the house" broadcasts and by the timer
    # watcher to deliver fired reminders to the originating room.
    # Misconfigured Pis sharing a room_id will overwrite each other —
    # the second connect wins and the first becomes unreachable for
    # broadcasts (still functional for its own request/response).
    # Must be initialized BEFORE the timer watcher starts ticking,
    # otherwise the first reminder dispatch race-loses on AttributeError.
    app.state.active_sessions = {}
    # Bind the SDK speech plane to the live session map (SDK 1.1):
    # sdk.speech.announce rides the same StreamSession.announce fan-out
    # as /v1/admin/announce and the timers. Local import — this module's
    # top import order is bootstrap-sensitive (DLL preloading).
    from domovoi.sdk import speech as _sdk_speech

    _sdk_speech.bind_active_sessions(lambda: app.state.active_sessions)

    # Per-room external now-playing attribution lives in the generic
    # now-playing source registry (domovoi.now_playing.NOW_PLAYING,
    # design §4.7) — provider plugins stamp it via sdk.now_playing /
    # sdk.playback.play_url, the streaming layer clears it generically
    # on music_stop, and the playback-state sweeper prunes stale stamps.
    # No per-provider app.state dict exists anymore.

    # room_id → playlist playback state, populated by
    # PlaylistHandler / the play-playlist admin endpoint and read by
    # MusicHandler._smart_skip so "next" stays inside the current
    # playlist. Entry shape:
    #   {
    #     "playlist_id": int,           # 0 == virtual Favorites
    #     "name":        str,
    #     "mode":        "ordered" | "shuffle",
    #     "last_track_id":   int,
    #     "last_position":   int | None,  # None for favorites — uses
    #                                     # library_tracks.id as the
    #                                     # ordering key instead
    #     "last_file_path":  str,         # freshness check vs MPD
    #                                     # currentsong.file
    #   }
    # Cleared by _admin_dispatch_music / streaming._send_response on
    # music_action="stop", and pruned by the music-source sweeper
    # when MPD's currentsong drifts away from last_file_path.
    app.state.current_playlist = {}

    # room_id → most-recent WiFi self-report from that room's Pi. Pushed
    # by the satellite's WiFiWatcher every poll (default 60 s) as a
    # `wifi_status` text frame, plus once on hello so a freshly-connected
    # Pi can answer "how's your wifi" right away. Read by WifiHandler
    # via Context.wifi_status, which the streaming layer stamps from
    # this dict before routing. Cleared on disconnect (the recorded
    # rate isn't trustworthy once the Pi has been gone for a while).
    app.state.wifi_status = {}

    # room_id → most-recent output-volume self-report (0-100) from that
    # room's satellite. Pushed as a `volume_status` text frame on connect
    # and after each `set_volume`. Read by MusicHandler via
    # Context.satellite_volume (stamped from this dict before routing) so a
    # relative "turn it up" bumps against the satellite's real hardware
    # level. Cleared on disconnect.
    app.state.satellite_volume = {}

    # room_id → the voice name that room's satellite reports speaking in
    # (voices registry). Pushed as a `voice_status` text frame on
    # connect and after a "switch voice" command's set_voice action lands.
    # Read by streaming when synthesizing that room's responses + announces
    # so each satellite speaks in its own voice; None / unknown falls back
    # to the registry default. Cleared on disconnect.
    app.state.satellite_voice = {}
    # Per-room cached config the Pi reports via config_status, for the web
    # dashboard's per-satellite Settings tab (Phase B).
    app.state.satellite_config = {}
    # Per-room full-duplex (on-chip AEC) capability from the hello frame —
    # gates two-way drop-in / open-mic to AEC-capable boards.
    app.state.satellite_full_duplex = {}
    # Per-room satellite type ("voice" | "video") from the hello frame; the
    # durable copy lives in the `satellites` table for offline display.
    app.state.satellite_sat_type = {}
    # Per-room voice-input state from the hello frame — false on mic-less
    # (e.g. video kiosk) builds; gates wake-recording/drop-in/chat.
    app.state.satellite_mic_enabled = {}
    # Per-room screen/kiosk state video satellites report via display_status
    # ({on, kiosk_alive, brightness, idle_mode}) — drives the dashboard's
    # Display controls. Cleared on disconnect.
    app.state.satellite_display = {}
    # Per-room code version (the synced_sha the Pi reports in its hello frame
    # after a satellite-code sync) so the dashboard can show which satellites
    # are behind the core's current SHA. None means the Pi has never
    # synced. Cleared on disconnect.
    app.state.satellite_synced_sha = {}
    # Live drop-in pairings: room_id → peer room_id (both directions).
    app.state.active_dropins = {}
    app.state.dropin_lock = asyncio.Lock()

    probe = ConnectivityProbe()
    await probe.start()
    app.state.probe = probe
    # Module-level probe registration so the SDK's ConnectivityView and
    # connectivity-gated startup hooks can read it without an app ref.
    from domovoi import connectivity as connectivity_mod

    connectivity_mod.set_current_probe(probe)

    # ── Core background work — the declarative worker registry (§4.5) ──
    # Worker start/stop is data, not hand-wired code: every core
    # worker registers against owner="core" and the shared WorkerRunner
    # owns the poll loops, stub suppression (Worker.stub_suppressed),
    # enabled gating (Worker.enabled_setting — resolved live each tick,
    # so flipping e.g. news_enabled in the dashboard takes effect on the
    # next tick without a restart for an already-running worker), and
    # reverse-registration-order shutdown. Plugin workers ride the SAME
    # runner (owner=<slug>, started by the loader) — one lifecycle
    # implementation, tested once (dossier §7 inv. 5).
    #
    # Registration order is the canonical start order (shutdown reverses it):
    #   timer_watcher → playback_state_sweeper → media_plays_pruner →
    #   memory_extractor → news_fetcher → wake_word_trainer →
    #   podcast_feed_poller → audiobook_indexer.
    #
    # Per-worker rationale lives on each class (workers/*.py); the radio
    # feature (stations, passive detection, SDR/FM, FCC import) is a
    # PLUGIN and registers its own workers through the plugin runtime.
    from domovoi.plugins_runtime.workers import WORKERS
    from domovoi.workers.audiobook_indexer import AudiobookIndexer
    from domovoi.workers.memory_extractor import MemoryExtractor
    from domovoi.workers.news_fetcher import NewsFetcher
    from domovoi.workers.podcast_feed_poller import PodcastFeedPoller
    from domovoi.workers.wake_word_trainer import WakeWordTrainer

    # `app` refs: the watcher routes fired reminders through the
    # originating room's StreamSession.announce; the sweeper prunes
    # app.state playback dicts; the news fetcher reads the shared probe.
    WORKERS.add_worker(TimerWatcher(app=app), owner="core")
    WORKERS.add_worker(PlaybackStateSweeper(app), owner="core")
    WORKERS.add_worker(MediaPlaysPruner(), owner="core")
    WORKERS.add_worker(MemoryExtractor(), owner="core")
    WORKERS.add_worker(NewsFetcher(app=app), owner="core")
    WORKERS.add_worker(WakeWordTrainer(), owner="core")
    WORKERS.add_worker(PodcastFeedPoller(), owner="core")
    WORKERS.add_worker(AudiobookIndexer(), owner="core")
    # (The former office-suite stale-lock sweeper is gone with the
    # OnlyOffice/Collabora engines — the homegrown editors don't lock.)

    # ── Boot-time startup hooks (§4.5) ──────────────────────────────────
    # Boot work runs as NAMED, ordered,
    # connectivity-gated hooks: the library index → enrich chain (the
    # enricher must see library_tracks fully populated, and enriching
    # offline would burn the polite rate-limit window against failing
    # endpoints — it now waits for the first online transition instead
    # of being skipped outright), plus the one-shot audiobook sweep.
    # Registered only outside stub mode (the suite never
    # runs boot sweeps); the §12 milestone names are marked done under
    # stubs so a plugin hook's after="core.library_index" can't hang.
    if settings.use_stubs:
        WORKERS.mark_core_hook_done("core.library_index")
        WORKERS.mark_core_hook_done("core.library_enrich")
    else:
        async def _startup_library_index() -> None:
            from domovoi.workers.library_indexer import index_music_dir

            await index_music_dir()

        async def _startup_library_enrich() -> None:
            from domovoi.workers.library_enricher import enrich_library

            await enrich_library()

        WORKERS.add_startup_hook(
            _startup_library_index, owner="core", name="library_index"
        )
        WORKERS.add_startup_hook(
            _startup_library_enrich,
            owner="core",
            name="library_enrich",
            after="core.library_index",
            requires_online=True,
        )
        if settings.audiobook_indexer_enabled:
            async def _startup_audiobook_index() -> None:
                from domovoi.workers.audiobook_indexer import index_audiobooks_dir

                await index_audiobooks_dir()

            WORKERS.add_startup_hook(
                _startup_audiobook_index, owner="core", name="audiobook_index"
            )

    await WORKERS.start_owner("core")

    # Plugin runtime (design §3.7, §4.1 loader ordering): discover bundled
    # + installed plugins and hot-load every enabled one. Runs strictly
    # AFTER the DLL bootstrap (module import order guarantees it; the
    # loader asserts the flag), after the core registries (handlers /
    # capabilities / workers / config) exist, after the connectivity
    # probe is registered so requires_online startup hooks gate
    # correctly, and BEFORE uvicorn starts serving (we're still inside
    # lifespan startup). Exception-isolated — plugin trouble must never
    # take the core down.
    from domovoi.plugins_runtime.loader import LOADER

    LOADER.bind_app(app)
    try:
        await LOADER.discover_and_load_all()
    except Exception as e:
        log.error("plugin discovery failed: %s", e)

    # Chat-tool milestone (§12): boot performs no eager Letta resync —
    # the install/enable/disable pipeline resyncs on change and
    # /v1/admin/chat/resync is the manual trigger — so the milestone
    # fires once plugin loading has settled the handler registry.
    WORKERS.mark_core_hook_done("core.letta_sync")

    log.info("domovoi started; bot_name=%s", settings.bot_name)
    try:
        yield
    finally:
        signal_shutdown()
        # Plugins first (reverse of startup: they loaded last), then the
        # core worker set in reverse registration order, then the probe.
        try:
            await LOADER.shutdown()
        except Exception as e:
            log.warning("plugin runtime shutdown raised: %s", e)
        try:
            await WORKERS.stop_owner("core")
        except Exception as e:
            log.warning("core worker shutdown raised: %s", e)
        # Drop the core registrations so a re-entered lifespan (tests
        # enter it repeatedly in one process) registers a fresh set
        # instead of accumulating duplicates.
        WORKERS.remove_owner("core")
        await probe.stop()
        connectivity_mod.set_current_probe(None)
        log.info("domovoi stopped")


app = FastAPI(title="Voice Domovoi", lifespan=lifespan)

# Plugin management API (install/confirm/enable/disable/uninstall/upgrade)
# — every mutation depends on domovoi.auth.require_admin (structurally
# gated from day one; the auth stage only adds the setup/login endpoints).
from domovoi.plugins_runtime.installer import plugins_admin_router  # noqa: E402

app.include_router(plugins_admin_router)


@app.post("/v1/intent")
async def post_intent(intent: Intent):
    probe: ConnectivityProbe = app.state.probe
    ctx = Context(
        room_id=intent.room_id,
        session_id=intent.session_id,
        online=probe.online,
        bot_name=settings.bot_name,
    )
    async with session_scope() as s:
        response = await route(intent, ctx, s)

    if not intent.synthesize:
        return response

    # Synthesize the response text to audio and return as WAV bytes. The
    # response text and metadata come back in headers so the client can log
    # or subtitle without re-parsing.
    tts = get_tts_client()
    audio_bytes = await tts.synthesize(response.text)
    headers = {
        "X-Response-Text": quote(response.text, safe=""),
        "X-Session-Id": str(response.session_id) if response.session_id else "",
        "X-Matched-Handler": response.matched_handler or "",
        "X-Matched-Path": response.matched_path or "",
        "X-Online": "true" if response.online else "false",
    }
    return FastAPIResponse(
        content=audio_bytes, media_type="audio/wav", headers=headers
    )


@app.get("/v1/health")
async def health() -> dict[str, str]:
    try:
        async with session_scope() as s:
            await s.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unreachable: {e}") from e
    if not HANDLERS:
        raise HTTPException(status_code=503, detail="no handlers registered")
    return {
        "status": "ok",
        "bot_name": settings.bot_name,
        "use_stubs": "true" if settings.use_stubs else "false",
    }


_EXAMPLE_PHRASE_RE = re.compile(r"[Ee]xamples?:\s*(.+)$")


def _example_phrases(tool_schema: dict[str, Any]) -> list[str]:
    """Pull example utterances out of the tool_schema description —
    handler descriptions double as the manual's example-phrase source
    (design §4.3.1/§12). Recognizes a trailing "Example: 'a', 'b'."
    clause and returns the quoted phrases."""
    description = str(tool_schema.get("description") or "")
    m = _EXAMPLE_PHRASE_RE.search(description)
    if not m:
        return []
    tail = m.group(1)
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", tail)
    if quoted:
        return quoted
    return [tail.strip().rstrip(".")] if tail.strip() else []


@app.get("/v1/handlers", response_model=list[HandlerInfo])
async def handlers() -> list[HandlerInfo]:
    return [
        HandlerInfo(
            name=h.name,
            requires_network=h.requires_network,
            tool_schema=h.tool_schema,
            fast_path_count=len(h.fast_paths),
            priority_band=h.priority_band,
            origin=h.plugin_slug or "core",
            display={
                "label": h.display.label,
                "tone": h.display.tone,
                "icon": h.display.icon,
            },
            example_phrases=_example_phrases(h.tool_schema),
        )
        for h in HANDLERS
    ]


@app.get("/v1/connectivity", response_model=ConnectivityState)
async def connectivity() -> ConnectivityState:
    probe: ConnectivityProbe = app.state.probe
    return ConnectivityState(
        online=probe.online,
        last_checked_at=probe.last_checked_at,
        last_online_at=probe.last_online_at,
        target=probe.target,
    )


# ─── Sound sync (satellites pull rendered clips instead of rsync) ────────
# The satellites mirror the rendered greeting / canned audio under
# satellite/sounds/ by hashing against this manifest and downloading only
# what changed — so editing greetings (web UI) + a core re-render
# is all it takes; no manual rsync of sounds/.


async def _resolve_voice_root(voice: str | None) -> Path:
    """Map a requested voice name (or None → the registry default) to its
    rendered-clip subtree under sounds/voices/<slug>/. Falls back to the
    bare sounds dir if no voice is registered yet (pre-seed)."""
    name = voice
    if not name:
        try:
            async with session_scope() as s:
                default = await VoicesRepository(s).get_default()
            name = default["name"] if default else None
        except Exception:
            name = None
    return voice_dir(name) if name else SOUNDS_DIR


@app.get("/v1/sounds/manifest")
async def sounds_manifest(voice: str | None = None) -> dict[str, str]:
    """`{relative_path: sha256}` for every rendered MP3 in a voice's subtree
    (recursive: network_issues.mp3 + sample.mp3 + greetings/*.mp3). `voice`
    defaults to the registry default. Empty before the first render. Keys
    are relative to the voice subtree, so a satellite's cache is voice-
    agnostic at canonical paths (greetings/…, network_issues.mp3)."""
    root = await _resolve_voice_root(voice)
    manifest: dict[str, str] = {}
    if root.is_dir():
        for p in sorted(root.rglob("*.mp3")):
            rel = p.relative_to(root).as_posix()
            manifest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest


@app.get("/v1/sounds/{path:path}")
async def sounds_file(path: str, voice: str | None = None) -> FileResponse:
    """Serve a rendered clip from a voice's subtree. Guarded to MP3s
    strictly inside that subtree (no path-traversal, no .voice sidecars)."""
    root = (await _resolve_voice_root(voice)).resolve()
    target = (root / path).resolve()
    if target.suffix != ".mp3" or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target, media_type="audio/mpeg")


# ─── Satellite code channel (in-field upgrades) ──────────────────────────
# A second file channel, parallel to /v1/sounds but for the satellite's own
# source tree (satellite/). A connected Pi, told to upgrade, mirrors this
# manifest the same way it mirrors the sounds manifest: hash-compare, download
# only changed files, verify each body's sha256 against the manifest, then
# self-restart. Integrity is the per-file sha256 here — NOT the git SHA, which
# is only a version label (the working tree is routinely dirty, so its bytes
# don't match any commit).
#
# The bundled satellite/sounds/*.mp3 are intentionally NOT carried here (they
# have their own /v1/sounds channel) — a documented v1 limitation. The
# allowlist below also keeps caches (__pycache__/*.pyc), editor backups
# (*.bak), and any .env secret from ever leaving the host.
SATELLITE_CODE_DIR = Path(settings.repo_dir) / "satellite"
_SAT_CODE_EXT_ALLOW = frozenset(
    {".py", ".toml", ".txt", ".md", ".service", ".sh", ".json"}
)


def _is_allowed_satellite_code(p: Path) -> bool:
    """Whether ``p`` may be served over the satellite-code channel: an
    allowlisted extension, no ``__pycache__`` segment anywhere in its path,
    not an editor backup (.bak) or compiled bytecode (.pyc), and not a .env
    secret (basename ``.env`` or any ``.env*``)."""
    if p.suffix not in _SAT_CODE_EXT_ALLOW:
        return False
    if "__pycache__" in p.parts:
        return False
    name = p.name
    if not name or name.endswith((".bak", ".pyc")):
        return False
    if name.startswith(".env"):
        return False
    return True


@app.get("/v1/satellite-code/manifest")
async def satellite_code_manifest() -> dict[str, str]:
    """`{relative_path: sha256}` for every allowlisted file under satellite/.
    Keys are POSIX-relative to satellite/ so the Pi can mirror them straight
    into its own checkout. Empty if the tree is missing (non-clone deploy)."""
    manifest: dict[str, str] = {}
    if SATELLITE_CODE_DIR.is_dir():
        for p in SATELLITE_CODE_DIR.rglob("*"):
            # Skip symlinks: read_bytes() would follow one out of the tree and
            # leak an out-of-satellite/ file's sha256 (and the Pi would then
            # fetch it). The file endpoint's resolve()+relative_to guard blocks
            # serving such a target, but the manifest must not list it either.
            # There are no legitimate symlinks in satellite/.
            if p.is_symlink() or not p.is_file() or not _is_allowed_satellite_code(p):
                continue
            rel = p.relative_to(SATELLITE_CODE_DIR).as_posix()
            manifest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest


@app.get("/v1/satellite-code/{path:path}")
async def satellite_code_file(path: str) -> FileResponse:
    """Serve one allowlisted file from satellite/. Guarded against
    path-traversal and the denylist (no __pycache__, .bak, .pyc, .env). The
    extension/denylist check runs before the traversal check, mirroring
    sounds_file."""
    root = SATELLITE_CODE_DIR.resolve()
    target = (root / path).resolve()
    if not _is_allowed_satellite_code(target) or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target, media_type="application/octet-stream")


# ─── Satellite plugin-payload channel ──────────────────────────────────────
# A fourth file channel, parallel to /v1/sounds, /v1/satellite-code, and
# /v1/wake-models — but for ENABLED plugins' [satellite] payloads (files a
# plugin wants mirrored onto every satellite; see
# domovoi/satellite_payload.py). Computed live per request so enabling/
# disabling a plugin adds/removes its subtree with no restart; the device's
# conservative prune then converges. Guarding is root-confinement +
# no-symlinks + per-plugin size cap — deliberately NOT an extension
# allowlist (payloads carry binaries like .dtbo overlays and ELF tools).


@app.get("/v1/satellite-plugins/manifest")
async def satellite_plugins_manifest() -> dict[str, Any]:
    """``{"files": {"<slug>/<rel>": sha256}, "meta": {slug: {version,
    apt_packages, pip_requirements, pip_lockfile, post_install}}}`` for
    every enabled plugin declaring a [satellite] payload."""
    from domovoi.satellite_payload import build_channel_manifest

    return await build_channel_manifest()


@app.get("/v1/satellite-plugins/{path:path}")
async def satellite_plugins_file(path: str) -> FileResponse:
    """Serve one payload file by its ``<slug>/<rel>`` channel path. Only
    enabled plugins' current payload sets resolve (the exact enumeration
    the manifest uses), so traversal/symlink escapes have no side door."""
    from domovoi.satellite_payload import resolve_channel_file

    target = await resolve_channel_file(path)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target, media_type="application/octet-stream")


# ─── Wake-models channel (custom wake words, Feature 5) ───────────────────
# A third file channel, parallel to /v1/sounds and /v1/satellite-code, but for
# trained openWakeWord models. A connected Pi, told (via set_wake_word /
# wake_models_changed) to pick up a new model, mirrors this manifest the same
# way it mirrors the sounds manifest: hash-compare, download only changed
# files, verify each body's sha256 against the manifest. A custom model is a
# single <slug>.onnx; openWakeWord may also emit an <slug>.onnx.json companion,
# so both extensions are allowlisted. Rooted at settings.wake_models_dir (a
# server-private runtime artifact dir, like sounds_dir).
_WAKE_MODEL_EXT_ALLOW = frozenset({".onnx", ".json"})


@app.get("/v1/wake-models/manifest")
async def wake_models_manifest() -> dict[str, str]:
    """`{relative_path: sha256}` for every served wake-model file. Keys are
    POSIX-relative to wake_models_dir so the Pi can mirror them straight into
    its own ~/.domovoi/wake_models/ cache. Empty before the first model is
    trained."""
    root = Path(settings.wake_models_dir)
    manifest: dict[str, str] = {}
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            # Skip symlinks (read_bytes() would follow one out of the tree and
            # leak an out-of-dir file's sha256) and anything not an allowlisted
            # model file — mirrors the satellite-code manifest guard.
            if p.is_symlink() or not p.is_file() or p.suffix not in _WAKE_MODEL_EXT_ALLOW:
                continue
            rel = p.relative_to(root).as_posix()
            manifest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return manifest


@app.get("/v1/wake-models/{path:path}")
async def wake_models_file(path: str) -> FileResponse:
    """Serve one wake-model file (.onnx / .onnx.json). Guarded against
    path-traversal and locked to the allowlisted extensions. The
    extension/existence check runs before the traversal check, mirroring
    sounds_file / satellite_code_file."""
    root = Path(settings.wake_models_dir).resolve()
    target = (root / path).resolve()
    if target.suffix not in _WAKE_MODEL_EXT_ALLOW or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(target, media_type="application/octet-stream")


# ─── Admin endpoints (used by the web management UI) ─────────────────────
# Surface the core's *running-process* state to the web backend
# (which lives in a different process and can't reach `app.state`
# directly) and expose a small set of write operations the UI surfaces
# as buttons. Same trust boundary as the rest of the core —
# LAN-only, unauthenticated. Adding auth here without doing the same
# for the voice surface would be theater.
#
# Voice-equivalent calls (admin/music/play, admin/announce) re-enter
# the regular routing pipeline so they show up in `intents_log` /
# `conversation_log` exactly like a spoken turn would, and so smart-
# skip / pending-confirmation / online gating all behave identically.


class _AdminAnnounceBody(BaseModel):
    room_id: str | None = None
    message: str = Field(..., min_length=1, max_length=500)


class _AdminPlayBody(BaseModel):
    room_id: str
    query: str = Field(..., min_length=1, max_length=500)


@app.get("/v1/admin/snapshot")
async def admin_snapshot() -> dict[str, Any]:
    """Process-state snapshot for the web UI's poll loop.

    `active_rooms` is the set of currently-connected satellite
    `room_id`s; `wifi_status` is the latest self-report from each
    Pi's WiFiWatcher; `resumable_music` is the per-room "stream the
    Pi was playing before the last non-music turn interrupted it."
    """
    # active_dropins is keyed by room_id in both directions; emit one row
    # per call (the initiator side) so the web UI can list live drop-ins
    # and offer a Hang-up button.
    active_dropins = [
        {
            "initiator": room,
            "target": v["peer"],
            "started_at": v.get("started_at"),
        }
        for room, v in app.state.active_dropins.items()
        if isinstance(v, dict) and v.get("initiator")
    ]
    return {
        "active_rooms": list(app.state.active_sessions.keys()),
        "resumable_music": dict(app.state.resumable_music),
        "wifi_status": dict(app.state.wifi_status),
        # Generic per-room now-playing stamps (design §4.7): source slug +
        # opaque data, mirrored for the dashboard's attribution pill.
        # Deliberately never carries elapsed_sec (dossier §7 inv. 8).
        "now_playing": NOW_PLAYING.snapshot(),
        "current_playlist": dict(app.state.current_playlist),
        "active_dropins": active_dropins,
        # Per-room AEC capability (from the hello frame) so the web UI can
        # offer drop-in only between full-duplex (XVF3800) satellites.
        "satellite_full_duplex": dict(app.state.satellite_full_duplex),
        # Per-room satellite type ("voice" | "video") from the hello frame, so
        # the web UI can gate type-specific controls for ONLINE satellites
        # (offline ones resolve from the `satellites` table instead).
        "satellite_sat_type": dict(app.state.satellite_sat_type),
        # Per-room voice-input state (false = mic-less build) so the web UI
        # can label mic-disabled satellites and hide mic-dependent actions.
        "satellite_mic_enabled": dict(app.state.satellite_mic_enabled),
        # Per-room screen/kiosk state ({on, kiosk_alive, brightness,
        # idle_mode}) video satellites report via display_status — drives
        # the dashboard's Display block and dead-kiosk warning.
        "satellite_display": dict(app.state.satellite_display),
        # Per-room active TTS voice (what each Pi reported via voice_status),
        # so the web UI can show which voice a device is actually speaking in.
        # None for a room means it's on the registry default.
        "satellite_voice": dict(app.state.satellite_voice),
        # Per-room master output volume (0-100) each Pi reported via
        # volume_status, so the dashboard's overview tab can show + drive it.
        # Absent for a room means the Pi hasn't reported one yet (or its board
        # has no output mixer control configured).
        "satellite_volume": dict(app.state.satellite_volume),
        # Per-room code version (the synced_sha each Pi reported in its hello
        # frame) so the dashboard can flag satellites behind the core.
        # None for a room means it has never synced its code.
        "satellite_synced_sha": dict(app.state.satellite_synced_sha),
        # The core's own version label (short HEAD SHA, +"-dirty" when
        # the working tree is dirty). "unknown" if git isn't available.
        "domovoi_version": await git_version.current_sha(),
    }


@app.post("/v1/admin/announce")
async def admin_announce(body: _AdminAnnounceBody) -> dict[str, Any]:
    """Speak `message` on one or all connected satellites.

    `room_id=None` broadcasts to every active session. Reuses
    `StreamSession.announce` — the same path IntercomHandler uses for
    voice-driven intercom — so a Pi mid-response gets skipped (its
    in-flight TTS would clip the announcement) and resumable music is
    auto-restored after the announcement plays.

    503 when no satellites are connected at all; 404 when a specific
    `room_id` isn't in the active session map.
    """
    sessions: dict[str, Any] = app.state.active_sessions
    if not sessions:
        raise HTTPException(status_code=503, detail="no satellites connected")
    if body.room_id is None:
        targets = list(sessions.values())
    else:
        target = sessions.get(body.room_id)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"room {body.room_id!r} not connected",
            )
        targets = [target]

    announced: list[str] = []
    for sess in targets:
        try:
            await sess.announce(body.message)
            announced.append(sess.room_id)
        except Exception as e:
            log.warning("admin announce to room=%s failed: %s", sess.room_id, e)
    return {"announced_to": announced}


class _AdminDropInStartBody(BaseModel):
    initiator_room: str
    target_room: str


class _AdminDropInEndBody(BaseModel):
    room_id: str


@app.post("/v1/admin/dropin/start")
async def admin_dropin_start(body: _AdminDropInStartBody) -> dict[str, Any]:
    """Open a live two-way drop-in between two connected satellite rooms
    (Feature 4). Web-initiated equivalent of the voice "drop in on X" path —
    the web process can't touch ``active_sessions``, so it fans out here and
    reuses ``StreamSession._begin_dropin``.

    400 when the two rooms are the same; 404 when a room isn't connected;
    409 when drop-in is disabled, a room lacks AEC, or a room is already in
    a call. Always opens immediately (auto-accept) regardless of
    ``dropin_accept_mode`` — a dashboard click is its own consent.
    """
    from domovoi.dropin_common import OK, dropin_feasibility

    if not getattr(settings, "dropin_enabled", True):
        raise HTTPException(status_code=409, detail="drop-in is disabled")

    code = dropin_feasibility(app, body.initiator_room, body.target_room)
    if code != OK:
        status = {
            "same_room": 400,
            "initiator_offline": 404,
            "target_offline": 404,
        }.get(code, 409)
        raise HTTPException(status_code=status, detail=code)

    sessions: dict[str, Any] = app.state.active_sessions
    initiator = sessions.get(body.initiator_room)
    target = sessions.get(body.target_room)
    if initiator is None or target is None:  # raced since feasibility check
        raise HTTPException(status_code=404, detail="room not connected")

    await initiator._begin_dropin(target)
    # _begin_dropin refuses under the lock (already paired) or tears down if
    # an open-mic frame fails to send — confirm the pairing actually stuck.
    if initiator.dropin_peer is not target:
        raise HTTPException(status_code=409, detail="couldn't open drop-in")
    return {
        "status": "active",
        "initiator": body.initiator_room,
        "target": body.target_room,
    }


@app.post("/v1/admin/dropin/end")
async def admin_dropin_end(body: _AdminDropInEndBody) -> dict[str, Any]:
    """Hang up whatever drop-in ``room_id`` is in (Feature 4). 404 when the
    room isn't in a call."""
    sessions: dict[str, Any] = app.state.active_sessions
    sess = sessions.get(body.room_id)
    if sess is None or sess.dropin_peer is None:
        raise HTTPException(status_code=404, detail="room not in a call")
    peer_room = sess.dropin_peer.room_id
    await sess._end_dropin(ended_by=body.room_id, status="ended")
    return {"ended": True, "peer": peer_room}


class _AdminSatelliteRestartBody(BaseModel):
    room_id: str


@app.post("/v1/admin/satellite/restart")
async def admin_satellite_restart(body: _AdminSatelliteRestartBody) -> dict[str, Any]:
    """Ask a connected satellite to restart its own service. Used after a
    config edit that needs a fresh satellite process (Phase B) and as a
    manual 'restart this Pi' action. The Pi drains TTS playback then runs a
    sudo'ed systemctl restart (see satellite/PROVISIONING.md self-restart
    sudoers). 503 when nothing is connected, 404 when this room isn't."""
    import time

    from domovoi.db.repositories import IntentLogRepository

    sessions: dict[str, Any] = app.state.active_sessions
    if not sessions:
        raise HTTPException(status_code=503, detail="no satellites connected")
    target = sessions.get(body.room_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"room {body.room_id!r} not connected"
        )

    started = time.monotonic()
    try:
        await target.request_restart()
    except Exception as e:
        log.warning("admin restart to room=%s failed: %s", body.room_id, e)
        raise HTTPException(
            status_code=502, detail=f"couldn't reach satellite: {e}"
        ) from e

    async with session_scope() as s:
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript="[ui] restart satellite",
            matched_handler="satellite",
            matched_path=None,
            online=True,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return {"requested": True, "room_id": body.room_id}


class _AdminSetVolumeBody(BaseModel):
    room_id: str
    level: int = Field(..., ge=0, le=100)


@app.post("/v1/admin/satellite/set-volume")
async def admin_satellite_set_volume(body: _AdminSetVolumeBody) -> dict[str, Any]:
    """Set a connected satellite's master output volume (0-100). Drives the
    Pi's hardware mixer, which scales BOTH TTS playback and music — the same
    single master volume MusicHandler's spoken "turn it up" nudges. 503 when
    nothing is connected, 404 when this specific room isn't."""
    import time

    from domovoi.db.repositories import IntentLogRepository

    sessions: dict[str, Any] = app.state.active_sessions
    if not sessions:
        raise HTTPException(status_code=503, detail="no satellites connected")
    target = sessions.get(body.room_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"room {body.room_id!r} not connected"
        )

    started = time.monotonic()
    try:
        await target.set_output_volume(body.level)
    except Exception as e:
        log.warning("admin set-volume for room=%s failed: %s", body.room_id, e)
        raise HTTPException(
            status_code=502, detail=f"couldn't reach satellite: {e}"
        ) from e

    async with session_scope() as s:
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript=f"[ui] set volume {body.level}",
            matched_handler="satellite",
            matched_path=None,
            online=True,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return {"room_id": body.room_id, "level": body.level}


class _AdminSatelliteDisplayBody(BaseModel):
    room_id: str
    action: Literal["on", "off", "restart_kiosk"]


@app.post("/v1/admin/satellite/display")
async def admin_satellite_display(body: _AdminSatelliteDisplayBody) -> dict[str, Any]:
    """Drive a video satellite's screen: switch the panel on/off or restart
    the kiosk browser service. 503 when nothing is connected, 404 when this
    room isn't, 409 when the connected room isn't a video satellite (the
    frame would be meaningless — voice builds run no kiosk)."""
    import time

    from domovoi.db.repositories import IntentLogRepository

    sessions: dict[str, Any] = app.state.active_sessions
    if not sessions:
        raise HTTPException(status_code=503, detail="no satellites connected")
    target = sessions.get(body.room_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"room {body.room_id!r} not connected"
        )
    sat_type = app.state.satellite_sat_type.get(body.room_id, "voice")
    if sat_type != "video":
        raise HTTPException(
            status_code=409,
            detail=f"room {body.room_id!r} is not a video satellite",
        )

    started = time.monotonic()
    try:
        await target.set_display(body.action)
    except Exception as e:
        log.warning("admin display for room=%s failed: %s", body.room_id, e)
        raise HTTPException(
            status_code=502, detail=f"couldn't reach satellite: {e}"
        ) from e

    async with session_scope() as s:
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript=f"[ui] display {body.action}",
            matched_handler="satellite",
            matched_path=None,
            online=True,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return {"room_id": body.room_id, "action": body.action}


# ─── Domovoi version / update endpoints ─────────────────────────────
# Surface the running domovoi's git version and let the dashboard check
# for / pull updates. The SHA is a label only — integrity of satellite-code
# downloads is the per-file manifest sha256, not this commit (see the
# satellite-code channel above and domovoi/git_version.py).


@app.get("/v1/admin/version")
async def admin_version() -> dict[str, Any]:
    """What this process is RUNNING, and what's checked out on disk.

    ``sha``/``running_sha`` is captured at boot, so it reflects the code
    actually loaded. ``checkout_sha`` is read live from the working tree.
    After a pull without a restart the two differ and ``restart_required``
    is true — the panel must not claim a pulled fix is live when the old
    modules are still serving requests.
    """
    return await git_version.version_state()


@app.post("/v1/admin/version/check")
async def admin_version_check() -> dict[str, Any]:
    """Fetch the upstream and report how far behind/ahead HEAD is. Best-effort
    — offline / no tracking branch comes back with upstream=False and an error
    string rather than a 500. Read-only: never mutates the tree."""
    return await git_version.commits_behind()


@app.post("/v1/admin/version/pull")
async def admin_version_pull() -> dict[str, Any]:
    """`git pull --ff-only` — a deliberate, separate action (never invoked by
    the check). A dirty or diverged tree returns pulled=False plus the git
    stderr; we never force. The core process is NOT restarted here."""
    return await git_version.pull()


@app.post(
    "/v1/admin/version/restart",
    # Admin-tier: this bounces the host's services. Same gate as the
    # satellite code push, for the same reason — it changes what runs.
    dependencies=[Depends(require_admin_mutation)],
)
async def admin_version_restart() -> dict[str, Any]:
    """Bounce domovoi-core + domovoi-web so pulled code actually loads.

    Returns immediately; the restart fires a beat later so this response
    reaches the client before systemd kills the process. A host without the
    sudoers grant gets ``ok: false`` and the reason — never a prompt, never a
    half-restart."""
    return await self_restart.restart()


class _AdminSatelliteUpgradeBody(BaseModel):
    room_id: str


@app.post(
    "/v1/admin/satellite/upgrade",
    # §7.3 gated list: satellite code push is admin-tier (it makes a Pi
    # execute freshly-synced code). Bearer-only post-setup.
    dependencies=[Depends(require_admin_mutation)],
)
async def admin_satellite_upgrade(
    body: _AdminSatelliteUpgradeBody,
) -> dict[str, Any]:
    """Ask a connected satellite to sync its code from the Domovoi server and
    self-restart. The Pi tarballs its satellite/ tree, mirrors the
    /v1/satellite-code manifest (verifying each file's sha256), records the
    new synced SHA, then restarts — rolling back from the tarball if it
    doesn't reconnect within the deadline. 503 when nothing is connected,
    404 when this room isn't."""
    import time

    from domovoi.db.repositories import IntentLogRepository

    sessions: dict[str, Any] = app.state.active_sessions
    if not sessions:
        raise HTTPException(status_code=503, detail="no satellites connected")
    target = sessions.get(body.room_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"room {body.room_id!r} not connected"
        )

    expected_sha = await git_version.current_sha()
    started = time.monotonic()
    try:
        await target.request_upgrade(
            expected_sha=expected_sha,
            reconnect_timeout=settings.satellite_upgrade_reconnect_timeout_sec,
        )
    except Exception as e:
        log.warning("admin upgrade to room=%s failed: %s", body.room_id, e)
        raise HTTPException(
            status_code=502, detail=f"couldn't reach satellite: {e}"
        ) from e

    async with session_scope() as s:
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript="[ui] upgrade satellite",
            matched_handler="satellite",
            matched_path=None,
            online=True,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return {"requested": True, "room_id": body.room_id, "expected_sha": expected_sha}


# ─── Custom wake-word admin endpoints (Feature 5) ────────────────────────
# Reach a connected Pi to record positive training clips, stop recording, or
# push a trained model. These are registry/management actions (like the Voices
# surface), so — unlike the announce/restart/upgrade satellite channels — they
# write NO intents_log row. The web backend (a separate process) fans out here
# because it can't touch app.state.active_sessions directly. 404 when the room
# isn't connected; 502 on a send failure to the Pi.


class _AdminWakeRecordStartBody(BaseModel):
    room_id: str
    wake_word_id: int


class _AdminWakeRecordStopBody(BaseModel):
    room_id: str


class _AdminWakePushBody(BaseModel):
    room_id: str
    wake_word_id: int


class _AdminWakeScoreBody(BaseModel):
    wake_word_id: int


class _AdminChatToolBody(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


@app.post("/v1/admin/chat-tool")
async def admin_chat_tool(body: _AdminChatToolBody) -> dict[str, str]:
    """Execute a chat-mode tool call on behalf of the Letta agent (#8).

    Letta tools run in Letta's OWN container sandbox and can't reach this
    domovoi's handlers/DB/MPD directly, so the generated proxy tool POSTs
    ``{tool, args}`` here instead. ``dispatch_tool`` gates to ``chat_exposed``
    handlers and degrades to a short apology on error, so this never 500s the
    agent's tool round-trip. LAN-trusted like the other admin endpoints.
    """
    from domovoi.letta_tools import dispatch_tool

    text = await dispatch_tool(body.tool, body.args or {}, app=app)
    return {"text": text}


@app.post(
    "/v1/admin/chat/resync",
    # §7.3 gated list: the Letta resync trigger regenerates + uploads
    # proxy-tool source to the chat agent — admin-tier, Bearer-only.
    dependencies=[Depends(require_admin_mutation)],
)
async def admin_chat_resync() -> dict[str, Any]:
    """Rebuild the chat tool surface and re-attach it to every agent
    (design §4.4). The install/enable/disable pipeline runs this
    automatically; this endpoint is the manual trigger for the settings
    page's "resync chat tools" button."""
    from domovoi.plugins_runtime.letta_resync import resync_tools

    try:
        return await resync_tools()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"resync failed: {e}") from e


@app.post("/v1/admin/wake/record/start")
async def admin_wake_record_start(
    body: _AdminWakeRecordStartBody,
) -> dict[str, Any]:
    """Tell a connected satellite to record positive clips for ``wake_word_id``.
    The Pi suspends its normal wake loop and captures ``wake_word_clip_seconds``
    of audio per clip, framed so the server saves each one to the training set
    and bumps the clip count. 404 when the room isn't connected or the wake
    word doesn't exist; 502 on a send failure."""
    from domovoi.db.repositories import WakeWordsRepository

    target = app.state.active_sessions.get(body.room_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"room {body.room_id!r} not connected"
        )
    # A mic-disabled satellite (mic-less video build) has no capture stack —
    # it would just ignore the recording frames. Refuse up front.
    if app.state.satellite_mic_enabled.get(body.room_id) is False:
        raise HTTPException(
            status_code=400,
            detail=f"room {body.room_id!r} has voice input disabled (no microphone)",
        )
    # The open mic is single-tenant: a room mid drop-in can't also record (the
    # Pi's mic thread runs one sub-mode at a time), and the Pi would silently
    # defer the recording until the call ends. Refuse up front.
    if body.room_id in (app.state.active_dropins or {}):
        raise HTTPException(
            status_code=409, detail=f"room {body.room_id!r} is in a drop-in call"
        )
    async with session_scope() as s:
        repo = WakeWordsRepository(s)
        row = await repo.get(body.wake_word_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"wake word {body.wake_word_id} not found"
            )
        # Only a fresh ('recording') or retryable ('failed') word may be
        # recorded — a 'ready'/'training' word would be clobbered. Each take is
        # FRESH: reset clip_count to 0 here and clear the on-disk clip dir below
        # so the file set and the DB count never drift (the desync the review
        # flagged). A re-record is a clean re-take, not an append.
        if row["status"] not in ("recording", "failed"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"wake word {body.wake_word_id} is {row['status']!r}; "
                    "delete + recreate it to record a new take"
                ),
            )
        await repo.reset_for_recording(body.wake_word_id)
        await s.execute(
            text("SELECT pg_notify('wake_words_changed', :p)"),
            {"p": str(body.wake_word_id)},
        )
    # Truncate the slug's clip dir so the fresh take starts at clip_001.wav with
    # clip_count=0 — file set and count back in lockstep.
    import shutil
    clip_dir = Path(settings.wake_clips_dir) / row["slug"]
    if clip_dir.is_dir():
        shutil.rmtree(clip_dir, ignore_errors=True)

    # How many clips this take captures before the Pi self-terminates. An explicit
    # wake_word_record_target_clips wins (set it if you want a specific auto-stop
    # point); otherwise the take runs until the user clicks Stop, with a giant
    # safety cap so a forgotten/abandoned session can't record forever. 30-ish was
    # too small — a hard mic like the XVF3800 wants hundreds of real clips.
    target_count = settings.wake_word_record_target_clips or 15000
    try:
        await target.start_wake_recording(
            wake_word_id=body.wake_word_id,
            slug=row["slug"],
            clip_seconds=settings.wake_word_clip_seconds,
            target_count=target_count,
        )
    except Exception as e:
        log.warning("admin wake record start to room=%s failed: %s", body.room_id, e)
        raise HTTPException(
            status_code=502, detail=f"couldn't reach satellite: {e}"
        ) from e
    return {
        "recording": True,
        "room_id": body.room_id,
        "wake_word_id": body.wake_word_id,
        "target_count": target_count,
    }


@app.post("/v1/admin/wake/record/stop")
async def admin_wake_record_stop(
    body: _AdminWakeRecordStopBody,
) -> dict[str, Any]:
    """Stop an in-progress wake-word recording on ``room_id`` and let the Pi
    resume its normal wake loop. 404 when the room isn't connected; 502 on a
    send failure."""
    target = app.state.active_sessions.get(body.room_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"room {body.room_id!r} not connected"
        )
    try:
        await target.stop_wake_recording()
    except Exception as e:
        log.warning("admin wake record stop to room=%s failed: %s", body.room_id, e)
        raise HTTPException(
            status_code=502, detail=f"couldn't reach satellite: {e}"
        ) from e
    return {"recording": False, "room_id": body.room_id}


@app.post("/v1/admin/wake/push")
async def admin_wake_push(body: _AdminWakePushBody) -> dict[str, Any]:
    """Push a trained wake model to a connected satellite. The wake word must
    be ``ready`` with a ``model_ref`` (a trained ``<slug>.onnx`` exists). The
    Pi writes the slug to its wake sidecar, syncs the model from
    /v1/wake-models, then self-restarts to load it. 404 when the room isn't
    connected or the wake word doesn't exist; 409 when it isn't trained yet;
    502 on a send failure."""
    from domovoi.db.repositories import WakeWordsRepository

    target = app.state.active_sessions.get(body.room_id)
    if target is None:
        raise HTTPException(
            status_code=404, detail=f"room {body.room_id!r} not connected"
        )
    async with session_scope() as s:
        row = await WakeWordsRepository(s).get(body.wake_word_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"wake word {body.wake_word_id} not found"
        )
    if row["status"] != "ready" or not row["model_ref"]:
        raise HTTPException(
            status_code=409,
            detail=f"wake word {body.wake_word_id} is not trained (status={row['status']!r})",
        )

    try:
        # set_wake_word already triggers a model sync + restart on the Pi;
        # sending wake_models_changed too would just race a second concurrent
        # sync against the restart, so we don't. Carry the per-word threshold
        # so the Pi applies it instead of its local config default.
        await target.request_set_wake_word(
            slug=row["slug"], threshold=row.get("threshold")
        )
    except Exception as e:
        log.warning("admin wake push to room=%s failed: %s", body.room_id, e)
        raise HTTPException(
            status_code=502, detail=f"couldn't reach satellite: {e}"
        ) from e
    return {
        "pushed": True,
        "room_id": body.room_id,
        "wake_word_id": body.wake_word_id,
        "slug": row["slug"],
    }


@app.post("/v1/admin/wake/score")
async def admin_wake_score(body: _AdminWakeScoreBody) -> dict[str, Any]:
    """Offline-score a wake word's recorded clips against its trained model —
    the decisive real-vs-harness check. Feeds each clip (raw AND auto-trimmed)
    through openWakeWord as a stream of 1280-sample frames and takes the
    max-over-clip, plus a silence baseline. Runs on the core (it owns
    openWakeWord + the model files), off the event loop. The per-clip raw score
    is persisted into the clip's sidecar so the dashboard can show it.

    404 when the wake word doesn't exist, 409 when it has no trained model yet,
    501 when openWakeWord isn't installed (onnx inference is available on
    Windows, but the package is an optional extra)."""
    from pathlib import Path as _Path

    from domovoi import wake_clip_quality as _wq
    from domovoi import wake_eval as _we
    from domovoi.db.repositories import WakeWordsRepository

    async with session_scope() as s:
        row = await WakeWordsRepository(s).get(body.wake_word_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"wake word {body.wake_word_id} not found"
        )
    if row["status"] != "ready" or not row["model_ref"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"wake word {body.wake_word_id} has no trained model yet "
                f"(status={row['status']!r})"
            ),
        )

    slug = row["slug"]
    threshold = float(row.get("threshold") or _we.DEFAULT_THRESHOLD)
    slug_dir = _Path(settings.wake_clips_dir) / slug

    def _run() -> dict[str, Any]:
        model = _we.load_model(slug, model_ref=row.get("model_ref"))
        clip_scores = _we.score_dir(model, slug_dir)
        for c in clip_scores:
            try:
                _wq.set_score(slug_dir / c["name"], c["raw_score"])
            except Exception:
                pass  # a sidecar hiccup mustn't drop the score result
        silence = _we.silence_score(model)
        return {
            "available": True,
            "slug": slug,
            "threshold": threshold,
            "clips": clip_scores,
            "summary": _we.summarize(
                clip_scores, threshold=threshold, silence=silence
            ),
        }

    try:
        return await asyncio.to_thread(_run)
    except _we.WakeEvalUnavailable as e:
        raise HTTPException(status_code=501, detail=str(e)) from e


@app.get("/v1/admin/satellite/{room_id}/config")
async def admin_get_satellite_config(room_id: str) -> dict[str, Any]:
    """Editable satellite config (the schema joined with the values the Pi
    reported via config_status) for the per-satellite Settings tab. 404 when
    the room isn't connected — you can't edit an offline Pi."""
    from domovoi.satellite_config_schema import EDITABLE_FIELDS

    if room_id not in app.state.active_sessions:
        raise HTTPException(status_code=404, detail=f"room {room_id!r} not connected")
    reported: dict[str, Any] = app.state.satellite_config.get(room_id) or {}
    fields = [
        {
            "name": spec.name, "label": spec.label, "group": spec.group,
            "section": spec.section, "tier": spec.tier, "type": spec.type,
            "min": spec.min, "max": spec.max, "choices": spec.choices,
            "unit": spec.unit, "help": spec.help,
            "value": reported.get(spec.name),
        }
        for spec in EDITABLE_FIELDS
    ]
    return {"room_id": room_id, "reported": bool(reported), "fields": fields}


class _AdminSatelliteConfigBody(BaseModel):
    changes: dict[str, Any]


@app.post("/v1/admin/satellite/{room_id}/config")
async def admin_update_satellite_config(
    room_id: str, body: _AdminSatelliteConfigBody
) -> dict[str, Any]:
    """Validate satellite config edits and push them to the Pi, which
    rewrites its config.toml (preserving comments), validates + backs it up,
    and restarts to apply. 404 when the room isn't connected; rejects
    unknown / out-of-range fields and only sends the valid ones."""
    import time

    from domovoi.db.repositories import IntentLogRepository
    from domovoi.satellite_config_schema import (
        FIELD_BY_NAME,
        coerce_and_validate,
    )

    target = app.state.active_sessions.get(room_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"room {room_id!r} not connected")

    accepted: dict[str, Any] = {}
    rejected: dict[str, str] = {}
    for name, raw in (body.changes or {}).items():
        spec = FIELD_BY_NAME.get(name)
        if spec is None:
            rejected[name] = "not an editable setting"
            continue
        try:
            accepted[name] = coerce_and_validate(spec, raw)
        except ValueError as e:
            rejected[name] = str(e)

    if not accepted:
        return {"sent": [], "rejected": rejected, "restarting": False}

    started = time.monotonic()
    try:
        await target.send_config(accepted)
    except Exception as e:
        log.warning("admin set_config room=%s failed: %s", room_id, e)
        raise HTTPException(
            status_code=502, detail=f"couldn't reach satellite: {e}"
        ) from e

    async with session_scope() as s:
        await IntentLogRepository(s).log(
            room_id=room_id,
            transcript=f"[ui] update satellite config {','.join(sorted(accepted))}",
            matched_handler="satellite",
            matched_path=None,
            online=True,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return {"sent": sorted(accepted), "rejected": rejected, "restarting": True}


@app.delete(
    "/v1/admin/satellites/{room_id}/pairing",
    # Resetting a pairing is a SECURITY op (it lets the next connection
    # re-pair as this room), so it's admin-tier, Bearer-only. The web
    # backend forwards the caller's credentials; both processes validate
    # against the same admin_sessions table.
    dependencies=[Depends(require_admin_mutation)],
)
async def admin_reset_satellite_pairing(room_id: str) -> dict[str, Any]:
    """Delete a room's satellite pairing row (V002) so the NEXT `hello` for
    that room re-pairs trust-on-first-use. Needed after re-flashing a Pi or
    moving a room to a new device (the old device's token no longer matches).

    Works whether or not the room is currently connected — the pairing lives
    in the DB, not the live session. Returns whether a row was actually
    removed (``reset``: false means the room had no pairing to clear)."""
    from domovoi.db.repositories import SatellitePairingRepository

    async with session_scope() as s:
        removed = await SatellitePairingRepository(s).reset_pairing(room_id)
    log.info(
        "pairing: admin reset room=%s (row_existed=%s)", room_id, removed
    )
    return {"room_id": room_id, "reset": removed}


# Room ids are used as WS paths, MPD container-name components, and config
# values — keep them boring. Mirrored client-side by the AdoptModal.
_ROOM_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class _PreseedPairingBody(BaseModel):
    sat_type: str = "voice"
    room_label: str | None = Field(default=None, max_length=80)
    hardware: str | None = Field(default=None, max_length=200)
    board: str | None = Field(default=None, max_length=80)
    mac: str | None = Field(default=None, max_length=32)
    force: bool = False


@app.post(
    "/v1/admin/satellites/{room_id}/pairing/preseed",
    # Pre-seeding mints the room's WS-auth token — a SECURITY op like the
    # reset above: admin-tier, Bearer-only, credentials forwarded by the web.
    dependencies=[Depends(require_admin_mutation)],
)
async def admin_preseed_satellite_pairing(
    room_id: str, body: _PreseedPairingBody
) -> dict[str, Any]:
    """USB-adoption pre-seed: generate this room's pairing token, store its
    sha256 (so the device's FIRST connect matches as an already-paired room
    — no trust-on-first-use race, works under strict pairing), and upsert
    the `satellites` inventory row (type/hardware/label metadata).

    The RAW token is returned exactly once, for the adoption flow to write
    into the device's provision file; it is never logged or stored. 409
    when the room is already paired unless ``force`` (the re-provision
    path — rotates the token, so the OLD device stops matching)."""
    from domovoi.db.repositories import SatellitePairingRepository, SatellitesRepository

    if not _ROOM_ID_RE.fullmatch(room_id):
        raise HTTPException(
            status_code=422,
            detail="room_id must match ^[a-z][a-z0-9_]{0,31}$",
        )
    if body.sat_type not in ("voice", "video"):
        raise HTTPException(
            status_code=422, detail=f"unknown sat_type {body.sat_type!r}"
        )
    token = secrets.token_hex(32)
    async with session_scope() as s:
        pairings = SatellitePairingRepository(s)
        existing = await pairings.get_pairing(room_id)
        if existing is not None and not body.force:
            raise HTTPException(
                status_code=409,
                detail=f"room {room_id!r} is already paired (use force to rotate)",
            )
        await pairings.pair(room_id, token_sha256(token))
        await SatellitesRepository(s).preseed_upsert(
            room_id,
            sat_type=body.sat_type,
            room_label=body.room_label,
            hardware=body.hardware,
            board=body.board,
            mac=body.mac.lower() if body.mac else None,
            adopted_via="usb",
        )
    log.info(
        "pairing: preseeded room=%s sat_type=%s (rotated=%s)",
        room_id, body.sat_type, existing is not None,
    )
    return {"room_id": room_id, "token": token, "rotated": existing is not None}


@app.delete(
    "/v1/admin/satellites/{room_id}",
    dependencies=[Depends(require_admin_mutation)],
)
async def admin_delete_satellite(room_id: str) -> dict[str, Any]:
    """Remove a never-connected satellite: the inventory row AND its
    preseeded pairing. The adopt flow's rollback (a mid-adopt unplug) and
    the dashboard's Remove action for `waiting` rooms. 409 when the room
    has an `mpd_rooms` row — a provisioned room isn't deletable this way
    (its MPD container and history exist; that's a different, deliberate
    operation)."""
    from domovoi.db.repositories import SatellitePairingRepository, SatellitesRepository

    async with session_scope() as s:
        provisioned = (
            await s.execute(
                text("SELECT 1 FROM mpd_rooms WHERE room_id = :r"),
                {"r": room_id},
            )
        ).first()
        if provisioned is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"room {room_id!r} is provisioned (has an MPD instance) — "
                    "not deletable via adoption rollback"
                ),
            )
        removed_meta = await SatellitesRepository(s).delete(room_id)
        removed_pairing = await SatellitePairingRepository(s).reset_pairing(room_id)
    log.info(
        "satellites: admin delete room=%s (meta=%s pairing=%s)",
        room_id, removed_meta, removed_pairing,
    )
    return {"room_id": room_id, "deleted": removed_meta or removed_pairing}


class _RoomLabelBody(BaseModel):
    room_label: str | None = Field(default=None, max_length=80)


@app.post("/v1/admin/satellites/{room_id}/label")
async def admin_set_satellite_label(
    room_id: str, body: _RoomLabelBody
) -> dict[str, Any]:
    """Set (or clear, with null) a satellite's display room label — the
    grouping tag for several satellites sharing a physical room. Cosmetic
    metadata, daily-tier like volume/restart."""
    from domovoi.db.repositories import SatellitesRepository

    label = body.room_label.strip() if body.room_label else None
    async with session_scope() as s:
        await SatellitesRepository(s).set_room_label(room_id, label or None)
    return {"room_id": room_id, "room_label": label or None}


async def _run_sounds_regenerate() -> None:
    """Render all registered voices' clips, refresh greeting phrases, and tell
    connected satellites to re-sync. Serialized by a lock so overlapping
    triggers (several voices added quickly) don't render concurrently — the
    second waits, then finds everything up to date and returns fast."""
    async with app.state.sounds_render_lock:
        log.info("sound clip regeneration: rendering all registered voices…")
        await regenerate_canned_sounds()
        # The greeting bank may have changed — refresh the cached phrases used
        # to strip a bled-in greeting from transcripts.
        app.state.greeting_phrases = await load_greeting_phrases()
        notified: list[str] = []
        for sess in list(app.state.active_sessions.values()):
            try:
                await sess.notify_sounds_changed()
                notified.append(sess.room_id)
            except Exception as e:
                log.warning("sounds_changed notify to room=%s failed: %s", sess.room_id, e)
        log.info(
            "sound clip regeneration complete; notified %d satellite(s)", len(notified)
        )


@app.post("/v1/admin/sounds/regenerate")
async def admin_sounds_regenerate() -> dict[str, Any]:
    """Kick off a re-render of the sound clips (greetings + per-voice clips)
    from the DB, then tell every connected satellite to re-sync. Called by the
    web Greetings/Voices pages after a change.

    Runs in the BACKGROUND and returns immediately: rendering a freshly-added
    voice is dozens of TTS calls and can take a minute, which would otherwise
    blow the caller's HTTP timeout and hide the work. Watch the core
    log for progress (one line per clip)."""
    app.state.sounds_render_task = asyncio.create_task(_run_sounds_regenerate())
    return {"started": True}


class _VoiceSampleBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


@app.post("/v1/admin/voices/sample")
async def admin_voice_sample(body: _VoiceSampleBody) -> FastAPIResponse:
    """Synthesize a sample line (intro + random fun fact) in a registered
    voice and return it as WAV. Powers the web Voices page play button — the
    web backend proxies this since it has no TTS of its own. Live synthesis,
    so each call picks a fresh fact and needs no pre-rendered clip."""
    from domovoi.voice_sample import build_sample_text

    async with session_scope() as s:
        voice = await VoicesRepository(s).get_by_name(body.name)
    if voice is None:
        raise HTTPException(status_code=404, detail=f"no voice named {body.name!r}")

    text = build_sample_text(voice["name"])
    wav = await get_tts_client().synthesize(
        text, engine=voice["engine"], voice=voice["model_ref"]
    )
    return FastAPIResponse(
        content=wav,
        media_type="audio/wav",
        headers={"X-Sample-Text": quote(text, safe="")},
    )


async def _admin_route_intent(transcript: str, room_id: str) -> Response:
    """Re-enter the regular routing pipeline with a synthetic transcript.

    Goes through the same `intents_log` / `conversation_log` / handler
    dispatch as a voice turn — so a "play creep" admin call shows up
    in the audit trail just like the spoken version, and smart-skip
    / pending-confirmation / online gating all behave identically.
    """
    probe: ConnectivityProbe = app.state.probe
    intent = Intent(transcript=transcript, room_id=room_id)
    ctx = Context(
        room_id=room_id,
        online=probe.online,
        bot_name=settings.bot_name,
        app=app,
    )
    async with session_scope() as s:
        return await route(intent, ctx, s)


def _admin_response_dict(response: Response) -> dict[str, Any]:
    return {
        "text": response.text,
        "matched_handler": response.matched_handler,
        "matched_path": response.matched_path,
        "music_action": response.music_action,
        "online": response.online,
    }


async def _admin_dispatch_music(response: Response, room_id: str) -> None:
    """Deliver the music_start / music_stop frame to the room's Pi.

    Voice turns get this for free in ``StreamSession._send_response`` —
    the frame rides the same WebSocket the utterance came in on. The
    admin path has no inbound WS, so without this helper MPD starts
    decoding on Domovoi but the Pi never spawns mpg123 and the speaker
    stays silent. Symptom: the dashboard's now-playing card shows the
    stream as ``play`` with the elapsed time advancing while no audio
    reaches the room.

    Updates ``resumable_music`` regardless of whether the Pi is currently
    connected — a later reconnect (or the next response turn) will pull
    from there and auto-resume.
    """
    from domovoi.streaming import (
        _resume_mpd_for_room,
        schedule_music_resume_fallback,
    )

    sessions: dict[str, Any] = app.state.active_sessions
    resumable: dict[str, str] = app.state.resumable_music
    current_pl: dict[str, Any] = app.state.current_playlist
    sess = sessions.get(room_id)
    if response.music_action == "start" and response.music_stream_url:
        resumable[room_id] = response.music_stream_url
        # No now-playing stamp clear on start: matched_handler can't be
        # trusted here — "play creep" routes through MusicHandler,
        # which may delegate to a streaming provider that overwrites
        # matched_handler back to "music" (music.py cascade).
        # The web backend's stream_url-vs-file freshness check makes
        # the dashboard pill follow the real source instantly anyway;
        # the playback-state sweeper handles stamp hygiene within 5 s.
        # Same logic for current_playlist — the sweeper invalidates
        # entries whose last_file_path doesn't match MPD currentsong
        # once the new track is actually playing.
        if sess is not None:
            await sess._safe_send_text({
                "type": "music_start",
                "stream_url": response.music_stream_url,
            })
            # Pair the music_start with the same prepare/resume
            # handshake the voice path uses so admin "Play in {room}"
            # clicks don't stutter either.
            await schedule_music_resume_fallback(
                app, room_id, response.music_stream_url,
            )
        else:
            # No Pi connected to consume the stream. The handler queued
            # MPD paused; without a satellite to send music_ready, the
            # song would sit paused indefinitely. Resume now so the
            # next reconnect's auto-resume joins a live stream.
            await _resume_mpd_for_room(room_id)
    elif response.music_action == "stop":
        resumable.pop(room_id, None)
        # Generic stamp pop (design §4.7) — no provider-specific
        # state dicts to clear.
        NOW_PLAYING.clear(room_id)
        current_pl.pop(room_id, None)
        pending: dict[str, dict[str, Any]] = app.state.pending_music_start
        stale = pending.pop(room_id, None)
        if stale is not None:
            stale_task = stale.get("task")
            if stale_task is not None and not stale_task.done():
                stale_task.cancel()
        if sess is not None:
            await sess._safe_send_text({"type": "music_stop"})


@app.post("/v1/admin/music/play")
async def admin_music_play(body: _AdminPlayBody) -> dict[str, Any]:
    response = await _admin_route_intent(f"play {body.query}", body.room_id)
    await _admin_dispatch_music(response, body.room_id)
    return _admin_response_dict(response)


class _AdminPlayTrackBody(BaseModel):
    room_id: str
    track_id: int = Field(..., ge=1)


@app.post("/v1/admin/music/play-track")
async def admin_music_play_track(body: _AdminPlayTrackBody) -> dict[str, Any]:
    """Play a specific library_tracks row by id, bypassing the router.

    The dashboard's "Play in {room}" button on a library row already
    knows the exact track id; routing through ``/v1/admin/music/play``
    means a synthetic "play title artist" transcript runs through the
    full voice pipeline, writes to ``conversation_log`` as if the user
    had spoken, and — when the synthetic query doesn't tag-match well
    in MPD's tag DB — falls through to an external streaming provider.
    Neither of those is
    what the user asked for when they clicked a row in their own
    library.

    This endpoint:
      * looks up file metadata directly from ``library_tracks``,
      * hands the file to MPD using the same three-stage lookup
        ``MusicHandler._play_random`` uses (tag search → filename
        substring → basename fallback), with NO external fallback,
      * writes one ``intents_log`` row (``transcript='[ui] play
        library track #N'``, ``matched_handler='music'``,
        ``matched_path=NULL``) for audit, and
      * does NOT write to ``conversation_log`` — UI clicks aren't
        conversation.

    Music_start frame still goes to the Pi via the existing
    :py:func:`_admin_dispatch_music` helper so the satellite spawns
    its mpg123 just like every other admin music path.
    """
    import time
    from pathlib import PurePath

    from domovoi.clients.mpd import get_mpd_client_for, mpd_stream_url_for
    from domovoi.db.repositories import IntentLogRepository
    from domovoi.handlers.shared.play_history import record_media_play

    started = time.monotonic()

    async with session_scope() as s:
        row = await s.execute(
            text(
                """
                SELECT id, file_path, title, artist
                FROM library_tracks
                WHERE id = :id
                """
            ),
            {"id": body.track_id},
        )
        track = row.first()
    if track is None:
        raise HTTPException(
            status_code=404,
            detail=f"track {body.track_id} not found in library_tracks",
        )
    title = track[2]
    artist = track[3]
    file_path = track[1]

    await _ensure_room_mpd(body.room_id)
    mpd = get_mpd_client_for(body.room_id)
    # Three-stage lookup mirroring MusicHandler._play_random's
    # bulletproof pattern (music.py:329-352): tag → filename
    # substrings → basename. The basename fallback is what hits in
    # practice for libraries with empty/messy ID3 tags — MPD inside
    # docker sees the same file at /music/<basename>.
    song: dict[str, Any] | None = None
    try:
        if title and artist:
            song = await mpd.prepare_search({"title": title, "artist": artist})
        elif title:
            song = await mpd.prepare_search({"title": title})
        if song is None:
            substrings = [s_ for s_ in (title, artist) if s_]
            if substrings:
                song = await mpd.prepare_filename(*substrings)
        if song is None and file_path:
            basename = PurePath(file_path).name
            if basename:
                log.info(
                    "admin play-track id=%s fell through to basename %r",
                    body.track_id, basename,
                )
                song = await mpd.prepare_filename(basename)
    except Exception as e:
        log.warning("admin play-track id=%s MPD raised: %s", body.track_id, e)
        raise HTTPException(
            status_code=502,
            detail=f"MPD error playing track {body.track_id}: {e}",
        ) from e

    if not song:
        raise HTTPException(
            status_code=404,
            detail=(
                f"MPD couldn't find track {body.track_id} (file_path "
                f"{file_path!r}) — try 'rescan my library'"
            ),
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    async with session_scope() as s:
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript=f"[ui] play library track #{body.track_id}",
            matched_handler="music",
            # matched_path=None: this didn't traverse the router. The
            # baseline CHECK on intents_log permits NULL, and the
            # `[ui]` transcript prefix makes the path obvious in
            # audits without burning a new enum value on a migration.
            matched_path=None,
            online=True,
            latency_ms=latency_ms,
        )
        # Play history for the "Recently played" tab (UI-initiated plays
        # bypass the router, so record explicitly here).
        await record_media_play(
            s,
            room_id=body.room_id,
            source="library",
            title=title,
            artist=artist,
            library_track_id=body.track_id,
        )

    # Build a synthetic Response so _admin_dispatch_music can send
    # the music_start frame to the Pi. Reuses the same one-liner
    # every other admin music path uses.
    response = Response(
        text="ok",
        matched_handler="music",
        music_action="start",
        music_stream_url=mpd_stream_url_for(body.room_id),
    )
    await _admin_dispatch_music(response, body.room_id)

    return {
        "played": True,
        "track_id": body.track_id,
        "title": title,
        "artist": artist,
        "latency_ms": latency_ms,
    }


async def _ensure_room_mpd(room_id: str) -> None:
    """Make sure ``room_id``'s MPD container is up before we connect to it.

    The admin music endpoints (the dashboard's cast / play buttons) connect
    straight to the room's MPD control port. ``warm_known_rooms()`` restarts
    known rooms at boot and ``ensure_room()`` runs on satellite connect — but a
    container can still be DOWN at cast time: Docker wasn't ready when the
    core booted (the boot warm caught the error and moved on), or the
    room's satellite hasn't connected this session to trigger a connect-time
    ensure. ``get_mpd_client_for`` only reads the cached port map, so casting to
    a stopped container fails with a bare connection-refused (WinError 1225 →
    502). ``ensure_room`` is idempotent and fast when the container is already
    running, so calling it here makes a cast self-healing rather than dead.
    """
    if settings.use_stubs:
        return
    try:
        from domovoi.mpd_provisioner import ensure_room

        await ensure_room(room_id)
    except Exception as e:  # noqa: BLE001 — non-fatal; the play call surfaces 502
        log.warning("admin music: ensure_room(%s) failed: %s", room_id, e)


class _AdminPlayTracksBody(BaseModel):
    room_id: str
    # Ordered list of library_tracks ids — the queue the browser player
    # built, handed off to a room. Capped so a runaway client can't ask MPD
    # to resolve an unbounded list in one request.
    track_ids: list[int] = Field(..., min_length=1, max_length=500)


@app.post("/v1/admin/music/play-tracks")
async def admin_music_play_tracks(body: _AdminPlayTracksBody) -> dict[str, Any]:
    """Load an arbitrary ordered list of library tracks into ``room_id``'s
    MPD queue and start playback — the browser music player's "cast this
    queue to a room" hand-off.

    Distinct from ``play-track`` (single row) and ``play-playlist`` (a DB
    playlist): here the browser owns the ordering, so we take the exact
    id list and preserve its order. Like the other UI-initiated admin
    music paths it writes ONE ``intents_log`` row (no ``conversation_log``
    — a cast isn't conversation) and records the first track as a play in
    the Recently-played tab. Subsequent in-queue advancement is MPD's own
    queue, so no ``current_playlist`` stamping is needed.
    """
    import time

    from domovoi.clients.mpd import get_mpd_client_for, mpd_stream_url_for
    from domovoi.db.repositories import IntentLogRepository
    from domovoi.handlers.shared.play_history import record_media_play

    started = time.monotonic()

    # Load metadata for the requested ids, then re-order to match the
    # request (SELECT ... = ANY returns rows in an arbitrary order).
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, file_path, title, artist
                FROM library_tracks
                WHERE id = ANY(:ids)
                """
            ),
            {"ids": body.track_ids},
        )
        by_id = {int(r[0]): r for r in rows.all()}
    if not by_id:
        raise HTTPException(
            status_code=404,
            detail="none of the requested track_ids exist in library_tracks",
        )
    ordered = [by_id[tid] for tid in body.track_ids if tid in by_id]

    specs = [
        {
            "title": r[2] or "",
            "artist": r[3] or "",
            "file_path": r[1] or "",
        }
        for r in ordered
    ]

    await _ensure_room_mpd(body.room_id)
    mpd = get_mpd_client_for(body.room_id)
    try:
        queued = await mpd.prepare_tracks(specs)
    except Exception as e:
        log.warning("admin play-tracks MPD raised: %s", e)
        raise HTTPException(status_code=502, detail=f"MPD error: {e}") from e

    if not queued:
        raise HTTPException(
            status_code=404,
            detail=(
                f"MPD couldn't find any of the {len(ordered)} requested "
                "tracks — try 'rescan my library'"
            ),
        )

    first = ordered[0]
    latency_ms = int((time.monotonic() - started) * 1000)
    async with session_scope() as s:
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript=f"[ui] cast {len(queued)} track(s) to queue",
            matched_handler="music",
            matched_path=None,
            online=True,
            latency_ms=latency_ms,
        )
        await record_media_play(
            s,
            room_id=body.room_id,
            source="library",
            title=first[2],
            artist=first[3],
            library_track_id=int(first[0]),
        )

    response = Response(
        text="ok",
        matched_handler="music",
        music_action="start",
        music_stream_url=mpd_stream_url_for(body.room_id),
    )
    await _admin_dispatch_music(response, body.room_id)

    return {
        "played": True,
        "queued": len(queued),
        "requested": len(body.track_ids),
        "latency_ms": latency_ms,
    }


class _AdminAddByQueryBody(BaseModel):
    room_id: str
    query: str = Field(..., min_length=1, max_length=500)
    artist: str | None = None
    attach_to_playlist_id: int | None = Field(None, ge=1)


@app.post("/v1/admin/music/add-by-query")
async def admin_music_add_by_query(body: _AdminAddByQueryBody) -> dict[str, Any]:
    """Queue a *generic* media acquisition by free-text query (design
    §4.8/§4.11). This endpoint exists even with no media provider
    installed — the row waits ``pending`` and the response carries the
    graceful-absence copy; installing a fulfiller later drains the
    backlog. Provider-specific search/play UI ships as plugin routers
    under ``/v1/plugins/<slug>/...``, never here.
    """
    from domovoi.db.repositories import IntentLogRepository

    async with session_scope() as s:
        result = await ACQUISITIONS.enqueue(
            s,
            kind="query",
            text=body.query,
            metadata={"title": body.query, "artist": body.artist},
            requested_by="web",
            attach_to_playlist_id=body.attach_to_playlist_id,
        )
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript=f"[ui] add by query: {body.query}",
            matched_handler="music", matched_path=None, online=True,
            latency_ms=None,
        )
    availability = ACQUISITIONS.availability()
    return {
        "queued": result.outcome == "enqueued",
        "outcome": result.outcome,
        "message": result.user_message,
        "already_in_library": result.outcome == "already_in_library",
        "already_downloading": result.outcome == "duplicate",
        "acquisition_id": (
            result.acquisition.id if result.acquisition else result.duplicate_of_id
        ),
        "fulfiller_available": availability.can_fulfill_query,
        "title": body.query,
    }


class _AdminAddByUrlBody(BaseModel):
    room_id: str
    url: str = Field(..., min_length=1, max_length=2000)
    title: str | None = None
    dedup_key: str | None = None
    attach_to_playlist_id: int | None = Field(None, ge=1)


@app.post("/v1/admin/music/add-by-url")
async def admin_music_add_by_url(
    body: _AdminAddByUrlBody, request: Request
) -> dict[str, Any]:
    """Queue a media acquisition for an EXACT external URL (design §4.8).

    Unlike add-by-query (which lets the fulfiller re-search and can
    resolve a *different* upload), this fetches the precise item the
    caller has a URL for — the Recently-played tab's "+ add to library"
    passes the original URL plus a provider-namespaced ``dedup_key`` so
    repeat clicks / in-flight rows don't queue duplicates. The fuzzy
    library-title layer is deliberately skipped: the caller picked this
    specific item, so a similar-but-different library track must not
    block it.

    SECURITY (§7.3 outbound-fetch tier): url-kind enqueues trigger
    provider code against caller-chosen URLs (SSRF + hostile-input-to-
    extractor surface), so this endpoint requires an admin session OR a
    URL a registered fulfiller's ``url_matcher`` allowlist recognizes,
    plus a per-source rate limit for the unauthenticated path.
    """
    from domovoi.db.repositories import IntentLogRepository

    decision = await check_outbound_fetch(
        request, body.url,
        url_allowed_by_fulfillers=ACQUISITIONS.url_allowed_by_fulfillers,
    )
    if not decision.allowed:
        raise HTTPException(status_code=decision.status, detail=decision.detail)

    async with session_scope() as s:
        result = await ACQUISITIONS.enqueue(
            s,
            kind="url",
            text=body.url,
            metadata={"title": body.title} if body.title else {},
            requested_by="web",
            dedup_key=body.dedup_key,
            attach_to_playlist_id=body.attach_to_playlist_id,
            skip_library_dedup=True,
        )
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript=f"[ui] add by url: {body.title or body.url}",
            matched_handler="music", matched_path=None, online=True,
            latency_ms=None,
        )
    availability = ACQUISITIONS.availability()
    return {
        "queued": result.outcome == "enqueued",
        "outcome": result.outcome,
        "message": result.user_message,
        "already_in_library": False,
        "already_downloading": result.outcome == "duplicate",
        "acquisition_id": (
            result.acquisition.id if result.acquisition else result.duplicate_of_id
        ),
        "fulfiller_available": availability.can_fulfill_url,
        "title": body.title,
    }


@app.get("/v1/acquisitions")
async def list_acquisitions(
    status: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Generic read view of the acquisition queue (design §4.8) — the
    web "Downloads"-style page renders this; provider plugins layer
    richer state on top from their own routers."""
    limit = max(1, min(int(limit), 200))
    where = "WHERE status = :status" if status else ""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    f"""
                    SELECT id, kind, text, metadata, requested_by, origin_ref,
                           attach_to_playlist_id, status, claimed_by, attempts,
                           error, requested_at, completed_at
                    FROM media_acquisitions {where}
                    ORDER BY requested_at DESC
                    LIMIT :limit
                    """
                ),
                {"status": status, "limit": limit} if status else {"limit": limit},
            )
        ).all()
    availability = ACQUISITIONS.availability()
    return {
        "acquisitions": [
            {
                "id": int(r.id), "kind": r.kind, "text": r.text,
                "metadata": dict(r.metadata or {}),
                "requested_by": r.requested_by, "origin_ref": r.origin_ref,
                "attach_to_playlist_id": r.attach_to_playlist_id,
                "status": r.status, "claimed_by": r.claimed_by,
                "attempts": int(r.attempts), "error": r.error,
                "requested_at": (
                    r.requested_at.isoformat() if r.requested_at else None
                ),
                "completed_at": (
                    r.completed_at.isoformat() if r.completed_at else None
                ),
            }
            for r in rows
        ],
        "fulfillers": availability.fulfillers,
        "can_fulfill_query": availability.can_fulfill_query,
        "can_fulfill_url": availability.can_fulfill_url,
    }


# ─── Plugin runtime introspection (design §4.14, §12) ─────────────────────


@app.get("/v1/capabilities")
async def capabilities() -> dict[str, Any]:
    """Live capability registry: which capability slugs are provided and
    by whom (what the web "+add" affordance and Android gate on)."""
    return {
        "capabilities": {
            name: sorted(CAPABILITIES.providers_for(name))
            for name in CAPABILITIES.names()
        }
    }


@app.get("/v1/plugins")
async def list_plugins() -> dict[str, Any]:
    """Plugin registry rows (open read; design §12)."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT slug, name, version, publisher, enabled, bundled,
                           install_source, status, last_error, installed_at,
                           updated_at
                    FROM plugins ORDER BY slug
                    """
                )
            )
        ).all()
    return {
        "plugins": [
            {
                "slug": r.slug, "name": r.name, "version": r.version,
                "publisher": r.publisher, "enabled": bool(r.enabled),
                "bundled": bool(r.bundled), "install_source": r.install_source,
                "status": r.status, "last_error": r.last_error,
                "installed_at": r.installed_at.isoformat() if r.installed_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    }


@app.get("/v1/plugins/{slug}/status")
async def plugin_status(slug: str) -> dict[str, Any]:
    """Per-plugin live status (design §4.14): registry row + handlers
    registered under the slug. Worker/startup-hook live state is
    scaffolded here and filled in by the worker-registry stage — the
    shape is stable API."""
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    "SELECT slug, name, version, enabled, status, last_error "
                    "FROM plugins WHERE slug = :slug"
                ),
                {"slug": slug},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"plugin {slug!r} not found")
    plugin_handlers = [
        {
            "name": h.name,
            "band": h.priority_band,
            "fast_path_count": len(h.fast_paths),
            "registered": True,
        }
        for h in HANDLERS
        if h.plugin_slug == slug
    ]
    from domovoi.plugins_runtime.workers import WORKERS

    live = WORKERS.status(slug)
    return {
        "slug": row.slug,
        "name": row.name,
        "version": row.version,
        "enabled": bool(row.enabled),
        "status": row.status,
        "last_error": row.last_error,
        "handlers": plugin_handlers,
        # Live per-worker / per-startup-hook state from the declarative
        # worker registry (design §4.14 — stable API keys).
        "workers": live["workers"],
        "startup_hooks": live["startup_hooks"],
    }


@app.get(
    "/v1/admin/config",
    # §7.3: config READS are gated too (plugin config carries secrets).
    # GETs may render via the dashboard cookie; Bearer also accepted.
    dependencies=[Depends(require_admin_read)],
)
async def admin_get_config() -> dict[str, Any]:
    """Editable-config registry joined with the live settings values, for
    the web settings gear. The web backend passes this through rather than
    reading its own (separate-process, stale) settings copy."""
    from domovoi.config_schema import EDITABLE_FIELDS

    fields = [
        {
            "name": spec.name,
            "label": spec.label,
            "group": spec.group,
            "section": spec.section,
            "tier": spec.tier,
            "type": spec.type,
            "min": spec.min,
            "max": spec.max,
            "choices": spec.choices,
            "unit": spec.unit,
            "help": spec.help,
            "value": getattr(settings, spec.name, None),
        }
        for spec in EDITABLE_FIELDS
    ]
    # One group per plugin (design §4.6): FieldSpec rows joined with the
    # plugin's live settings; kind="secret" values arrive pre-masked.
    from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG

    plugin_fields: list[dict[str, Any]] = []
    for _slug in PLUGIN_CONFIG.slugs():
        plugin_fields.extend(PLUGIN_CONFIG.dashboard_group(_slug))
    return {"fields": fields, "plugin_fields": plugin_fields}


class _AdminConfigUpdateBody(BaseModel):
    changes: dict[str, Any]
    # When set, `changes` targets THIS plugin's settings model (§4.6):
    # values validate through the plugin model, persist to
    # ~/.domovoi/plugins/<slug>.env, and run registered reapply hooks.
    plugin: str | None = None


@app.post(
    "/v1/admin/config",
    # §7.3: config writes are admin-tier mutations — Bearer-only.
    dependencies=[Depends(require_admin_mutation)],
)
async def admin_update_config(body: _AdminConfigUpdateBody) -> dict[str, Any]:
    """Validate, persist, and (where the tier allows) live-apply config
    changes. 'hot'/'reapply' fields mutate the live settings singleton now;
    'restart' fields are written to .env but returned in restart_required.
    Unknown or out-of-range fields are rejected and nothing is applied for
    them. Serialized behind app.state.config_apply_lock."""
    import time

    from domovoi import reapply as reapply_registry
    from domovoi.config_env_writer import write_env_values
    from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate
    from domovoi.db.repositories import IntentLogRepository

    started = time.monotonic()
    applied: list[str] = []
    restart_required: list[str] = []
    rejected: dict[str, str] = {}
    persist: dict[str, Any] = {}
    reapply_fields: list[str] = []

    if body.plugin:
        # Plugin-config write path (§4.6): the config bridge owns
        # validation, live apply, per-plugin .env persistence, and the
        # reapply-hook registry. Secrets never echo in errors.
        from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG

        async with app.state.config_apply_lock:
            try:
                restart = PLUGIN_CONFIG.write_values(
                    body.plugin, body.changes or {}
                )
                return {
                    "applied": [
                        k for k in (body.changes or {}) if k not in restart
                    ],
                    "restart_required": restart,
                    "rejected": {},
                }
            except KeyError:
                raise HTTPException(
                    status_code=404,
                    detail=f"plugin {body.plugin!r} has no registered config",
                )
            except ValueError as e:
                return {
                    "applied": [], "restart_required": [],
                    "rejected": {"__plugin__": str(e)},
                }

    async with app.state.config_apply_lock:
        for name, raw in (body.changes or {}).items():
            spec = FIELD_BY_NAME.get(name)
            if spec is None:
                rejected[name] = "not an editable setting"
                continue
            try:
                coerced = coerce_and_validate(spec, raw)
            except ValueError as e:
                rejected[name] = str(e)
                continue
            persist[name] = coerced
            if spec.tier == "restart":
                restart_required.append(name)
                continue
            # hot / reapply — mutate the live singleton now.
            setattr(settings, name, coerced)
            applied.append(name)
            if spec.tier == "reapply":
                reapply_fields.append(name)

        # Persist every accepted change (incl. restart-tier) to .env.
        if persist:
            try:
                write_env_values(persist)
            except Exception as e:
                log.warning("config save: .env write failed: %s", e)
                rejected["__persist__"] = f".env write failed: {e}"

        # tier="reapply" — run the registered hooks (§4.6). The registry
        # dedupes shared callbacks (tts_engine + tts_speed reset the TTS
        # client once) and isolates hook failures from the write.
        if reapply_fields:
            ran = reapply_registry.run_for(reapply_fields)
            if ran:
                log.info("config save: reapply hooks ran: %s", ", ".join(ran))

        if persist:
            latency_ms = int((time.monotonic() - started) * 1000)
            async with session_scope() as s:
                await IntentLogRepository(s).log(
                    room_id="",
                    transcript=f"[ui] update config {','.join(sorted(persist))}",
                    matched_handler="config",
                    matched_path=None,
                    online=True,
                    latency_ms=latency_ms,
                )

    return {
        "applied": applied,
        "restart_required": restart_required,
        "rejected": rejected,
    }


@app.get("/v1/admin/hardware")
async def admin_hardware() -> dict[str, Any]:
    """Host hardware snapshot for the web Models page's fit badges.

    GPUs come from the same ``nvidia-smi`` probe HomelabHandler uses
    (reused, not re-implemented); CPU / RAM / model-storage disk come from
    ``psutil``. Gathered HERE, on the core, because it owns the CUDA
    context — the separate web process proxies this rather than shelling out
    to nvidia-smi itself.

    Every field degrades individually: no GPUs (CPU-only host / nvidia-smi
    absent) → empty ``gpus``; psutil missing → null cpu/ram/disk. The page's
    fit math treats a missing GPU set as "no VRAM denominator" and labels its
    badges as estimates.
    """
    from domovoi.handlers.homelab import _query_gpus

    gpus_raw = await _query_gpus()
    gpus = [
        {
            "name": g.name,
            "util_pct": g.util_pct,
            "mem_used_mb": g.mem_used_mb,
            "mem_total_mb": g.mem_total_mb,
            "mem_free_mb": max(0, g.mem_total_mb - g.mem_used_mb),
            "temp_c": g.temp_c,
        }
        for g in gpus_raw
    ]

    cpu: dict[str, Any] | None = None
    ram: dict[str, Any] | None = None
    disk: dict[str, Any] | None = None
    try:
        import psutil

        # cpu_percent(interval=None) is non-blocking (delta since last call);
        # a single reading right after import reads ~0, so take a short
        # sampled interval in a thread to avoid blocking the event loop.
        cpu_pct = await asyncio.to_thread(psutil.cpu_percent, 0.15)
        cpu = {
            "percent": round(float(cpu_pct), 1),
            "logical_cores": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
        }
        vm = psutil.virtual_memory()
        ram = {
            "used_mb": int(vm.used / (1024 * 1024)),
            "total_mb": int(vm.total / (1024 * 1024)),
            "percent": round(float(vm.percent), 1),
        }
        # Disk free where Ollama/HF models actually land. Ollama stores under
        # the user profile on Windows (~/.ollama); probe the drive that holds
        # the configured music/home root so the number reflects real headroom.
        probe_path = str(Path.home())
        du = psutil.disk_usage(probe_path)
        disk = {
            "path": probe_path,
            "free_mb": int(du.free / (1024 * 1024)),
            "total_mb": int(du.total / (1024 * 1024)),
            "percent": round(float(du.percent), 1),
        }
    except Exception as e:  # psutil missing or a probe raised — degrade.
        log.warning("hardware probe (psutil) failed: %s", e)

    return {"gpus": gpus, "cpu": cpu, "ram": ram, "disk": disk}


class _AdminPlayPlaylistBody(BaseModel):
    room_id: str
    playlist_id: int = Field(..., ge=0)  # 0 == virtual Favorites
    shuffle: bool = False


@app.post("/v1/admin/music/play-playlist")
async def admin_music_play_playlist(body: _AdminPlayPlaylistBody) -> dict[str, Any]:
    """Start a playlist (or the Favorites virtual playlist) in
    ``room_id``. Ordered mode (default) plays the first track and
    stamps ``current_playlist[room_id]`` so subsequent voice/web
    "next" advances by position; shuffle mode picks a random row
    and stamps ``mode=shuffle`` so "next" continues randomly.

    Reuses :func:`domovoi.handlers.shared.playlist_pick.
    pick_next_track` to keep the SELECT logic identical to the one
    ``MusicHandler._smart_skip`` uses for in-playlist advancement —
    same join, same ordering, same favorites-bridging behavior.

    Writes one ``intents_log`` row (no ``conversation_log``) so UI
    clicks stay out of the satellites' conversation feed.
    """
    import time
    from pathlib import PurePath

    from domovoi.clients.mpd import get_mpd_client_for, mpd_stream_url_for
    from domovoi.db.repositories import IntentLogRepository
    from domovoi.handlers.shared.play_history import record_media_play
    from domovoi.handlers.shared.playlist_pick import (
        persist_resume_position,
        pick_next_track,
        read_resume_position,
    )

    started = time.monotonic()
    mode = "shuffle" if body.shuffle else "ordered"

    async with session_scope() as s:
        # 404-guard the playlist row first so the dashboard gets a
        # clean error rather than a "this playlist is empty" message.
        if body.playlist_id != 0:
            exists = (
                await s.execute(
                    text("SELECT name FROM playlists WHERE id = :id"),
                    {"id": body.playlist_id},
                )
            ).first()
            if exists is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"playlist {body.playlist_id} not found",
                )
            playlist_name = exists[0]
        else:
            playlist_name = "Favorites"

        # Durable resume — ordered mode continues from the saved position.
        resume = await read_resume_position(s, body.playlist_id, mode)
        picked = await pick_next_track(
            session=s,
            playlist_id=body.playlist_id,
            mode=mode,
            last_track_id=None,
            last_position=resume,
        )
        if picked is not None:
            await persist_resume_position(s, body.playlist_id, mode, picked.position)

    if picked is None:
        raise HTTPException(
            status_code=400,
            detail=f"playlist {playlist_name!r} has no tracks",
        )

    await _ensure_room_mpd(body.room_id)
    mpd = get_mpd_client_for(body.room_id)
    song: dict[str, Any] | None = None
    try:
        if picked.title and picked.artist:
            song = await mpd.prepare_search({"title": picked.title, "artist": picked.artist})
        elif picked.title:
            song = await mpd.prepare_search({"title": picked.title})
        if song is None:
            substrings = [s_ for s_ in (picked.title, picked.artist) if s_]
            if substrings:
                song = await mpd.prepare_filename(*substrings)
        if song is None and picked.file_path:
            basename = PurePath(picked.file_path).name
            if basename:
                log.info(
                    "admin play-playlist id=%s fell through to basename %r",
                    body.playlist_id, basename,
                )
                song = await mpd.prepare_filename(basename)
    except Exception as e:
        log.warning(
            "admin play-playlist id=%s MPD raised: %s",
            body.playlist_id, e,
        )
        raise HTTPException(
            status_code=502,
            detail=f"MPD error: {e}",
        ) from e

    if not song:
        raise HTTPException(
            status_code=404,
            detail=(
                f"MPD couldn't find {picked.title or 'track'} "
                f"({picked.file_path!r}) — try 'rescan my library'"
            ),
        )

    # Stamp playlist state for subsequent "next" calls. last_file_path
    # is what MPD just reported — use that for the freshness check
    # rather than the library_tracks.file_path (those are absolute
    # host paths; MPD's file is the relative URI it returns).
    app.state.current_playlist[body.room_id] = {
        "playlist_id": body.playlist_id,
        "name": playlist_name,
        "mode": mode,
        "last_track_id": picked.track_id,
        "last_position": picked.position,
        "last_file_path": str(song.get("file", "")),
    }

    latency_ms = int((time.monotonic() - started) * 1000)
    async with session_scope() as s:
        await IntentLogRepository(s).log(
            room_id=body.room_id,
            transcript=(
                f"[ui] play playlist #{body.playlist_id} "
                f"(mode={mode})"
            ),
            matched_handler="playlist",
            matched_path=None,
            online=True,
            latency_ms=latency_ms,
        )
        # Play history for the "Recently played" tab (UI-initiated).
        await record_media_play(
            s,
            room_id=body.room_id,
            source="playlist",
            title=picked.title,
            artist=picked.artist,
            library_track_id=picked.track_id,
        )

    response = Response(
        text="ok",
        matched_handler="playlist",
        music_action="start",
        music_stream_url=mpd_stream_url_for(body.room_id),
    )
    await _admin_dispatch_music(response, body.room_id)

    return {
        "played": True,
        "playlist_id": body.playlist_id,
        "playlist_name": playlist_name,
        "mode": mode,
        "track_id": picked.track_id,
        "title": picked.title,
        "artist": picked.artist,
        "latency_ms": latency_ms,
    }


_MUSIC_ACTIONS = {
    "pause": "pause the music",
    "resume": "resume",
    "stop": "stop the music",
    "skip": "next",
    "next": "next",
    "previous": "previous",
}


@app.post("/v1/admin/music/{action}/{room_id}")
async def admin_music_action(action: str, room_id: str) -> dict[str, Any]:
    transcript = _MUSIC_ACTIONS.get(action)
    if transcript is None:
        raise HTTPException(
            status_code=400,
            detail=f"invalid action {action!r}; valid: {sorted(_MUSIC_ACTIONS)}",
        )
    response = await _admin_route_intent(transcript, room_id)
    await _admin_dispatch_music(response, room_id)
    return _admin_response_dict(response)


@app.post("/v1/admin/library/reindex")
async def admin_library_reindex() -> dict[str, Any]:
    """Kick the library indexer in the background, then tell every
    per-room MPD to rescan MUSIC_DIR.

    Returns immediately with a queued ack — the sweep (~1 s per 1 k
    files) plus the per-room MPD updates run as one asyncio task so the
    request returns while it works.

    The MPD update half is what makes hand-placed and web-uploaded
    files *playable*: on the Windows + Docker Desktop host, inotify
    events from the bind-mounted MUSIC_DIR don't reach the MPD
    containers, so their ``auto_update`` never fires and a freshly
    dropped file stays invisible to ``play-track`` until something
    issues ``update``. This mirrors the two-step the voice "rescan my
    library" path (``MusicHandler._rescan_library``) already runs, so
    the web "Rescan library" button and uploads behave the same as the
    spoken command.
    """
    from domovoi.clients.mpd import iter_mpd_clients
    from domovoi.workers.library_indexer import index_music_dir

    async def _reindex_then_update_mpd() -> None:
        try:
            await index_music_dir()
        except Exception as e:
            log.warning("admin reindex: library indexer failed: %s", e)
        # Each per-room MPD has its own database file, so every daemon
        # has to rescan — `update` on one doesn't index the new file in
        # the others.
        for room_id, mpd in iter_mpd_clients():
            try:
                await mpd.update_library()
            except Exception as e:
                log.warning(
                    "admin reindex: MPD update failed for room=%s: %s", room_id, e
                )

    asyncio.create_task(_reindex_then_update_mpd(), name="admin-library-reindex")
    return {"queued": True, "worker": "library_indexer"}


@app.post("/v1/admin/library/enrich")
async def admin_library_enrich() -> dict[str, Any]:
    """Kick the library enricher in the background.

    The enricher is rate-limited (~1 req/sec against AcoustID +
    Shazam), so this can take ~13 minutes for a fresh 750-track
    library. Voice path replies with an ETA + final announcement;
    the web caller doesn't get the announcement, just the queued ack.
    """
    from domovoi.workers.library_enricher import enrich_library

    asyncio.create_task(enrich_library(), name="admin-library-enrich")
    return {"queued": True, "worker": "library_enricher"}


# ─── Streaming ────────────────────────────────────────────────────────────


@app.websocket("/v1/stream/{room_id}")
async def stream(ws: WebSocket, room_id: str) -> None:
    """Bidirectional audio + control stream for Pi satellites.

    See `domovoi/streaming.py` for the wire protocol.
    """
    session = StreamSession(ws, room_id)
    await session.run()


@app.websocket("/v1/dropin/{room_id}")
async def phone_dropin(ws: WebSocket, room_id: str) -> None:
    """Drop-in-only stream for phones — joins the existing intercom bridge
    as a call peer WITHOUT registering as a satellite (no wake-word
    routing, no MPD, no eviction of the room's Pi).

    See `domovoi/phone_dropin.py` for the wire protocol. ``phone_id``
    (query param) is the caller's identity in ``active_dropins``; give it
    a stable per-device value so busy-checks work.
    """
    from domovoi.phone_dropin import PhoneDropinSession

    phone_id = ws.query_params.get("phone_id") or "phone"
    # Namespace the id so a phone can never collide with (or masquerade
    # as) a real room in the active_dropins registry.
    if not phone_id.startswith("phone-"):
        phone_id = f"phone-{phone_id}"
    session = PhoneDropinSession(ws, phone_id=phone_id, target_room=room_id)
    await session.run()


def _reset_admin_cli() -> None:
    """``python -m domovoi.main --reset-admin`` (design §7.2 password
    recovery): drop the admin credential + every bearer session, then
    regenerate the one-time setup code so the operator can run first-run
    setup again from the dashboard. Local CLI only — this never has an
    HTTP surface."""
    code = asyncio.run(admin_auth_mod.reset_admin())
    # Plain ASCII output — Windows consoles default to cp1252.
    print("admin credential and all sessions cleared.")
    print(f"new setup code: {code}")
    print(f"(also written to {admin_auth_mod.setup_code_path()})")
    print("open the dashboard to choose a new admin password.")


def main() -> None:
    import sys

    if "--reset-admin" in sys.argv[1:]:
        _reset_admin_cli()
        return

    import uvicorn

    uvicorn.run(
        "domovoi.main:app",
        host="0.0.0.0",
        port=6370,
        log_level=settings.log_level.lower(),
        # Ping each Pi's WebSocket every ws_ping_interval_sec; if no
        # pong comes back within ws_ping_timeout_sec, uvicorn closes
        # the WS, which trips StreamSession's receiver-loop
        # finally block and evicts the stale session from
        # `active_sessions`. Without this, dead WSes accumulated under
        # flaky wifi and broadcast/intercom writes vanished silently.
        ws_ping_interval=settings.ws_ping_interval_sec,
        ws_ping_timeout=settings.ws_ping_timeout_sec,
    )


if __name__ == "__main__":
    main()
