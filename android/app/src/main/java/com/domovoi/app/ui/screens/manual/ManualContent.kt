package com.domovoi.app.ui.screens.manual

/**
 * Static copy for the manual screen. Deliberately trimmed to CORE
 * features only: plugin-provided features (radio stations, media
 * providers, …) are documented live in the web dashboard's manual,
 * which renders from the server's capability manifest — a static list
 * here would go stale the moment a plugin is installed or removed.
 */

internal data class ManualNode(
    val glyph: String,
    val name: String,
    val role: String,
    val tech: String,
    val live: Boolean = false,
    val idle: Boolean = false,
)

internal val MANUAL_NODES = listOf(
    ManualNode("◈", "satellites", "the ears & mouth in each room", "pi zero 2 w · mic · aux", idle = true),
    ManualNode("▦", "web", "this dashboard", "fastapi :6369 · react"),
    ManualNode("▚", "core", "the brain — routes every request", "fastapi :6370 · handlers · plugin runtime", live = true),
    ManualNode("◟", "whisper", "turns your speech into text", "faster-whisper · cuda"),
    ManualNode("◍", "ollama", "understands & answers", "llama3.2 · qwen2.5"),
    ManualNode("◠", "tts", "gives Domovoi its voice", "edge → piper → system"),
    ManualNode("▤", "postgres", "remembers everything", "postgres 16 · migrations"),
    ManualNode("♪", "mpd", "plays music, per room", "one container / room"),
    ManualNode("⚙", "workers", "background jobs", "timers · library upkeep · plugin workers"),
)

internal data class FeatureRow(val name: String, val desc: String, val net: String? = null)

internal val MANUAL_FEATURES = listOf(
    FeatureRow("music", "library playback, playlists & favorites — kitchen and garage play independently", "offline"),
    FeatureRow("timers · reminders", "countdowns and spoken reminders", "offline"),
    FeatureRow("intercom · drop-in", "talk room-to-room across the satellites", "offline"),
    FeatureRow("voice notes", "leave yourself a spoken note", "offline"),
    FeatureRow("memory", "tell Domovoi a fact and it keeps it"),
    FeatureRow("chat mode", "open, continued conversation without re-waking"),
    FeatureRow("double-check", "fact-check the last answer against the web", "needs net"),
    FeatureRow("voice profiles", "knows who's speaking, with presence-aware urgency", "offline"),
    FeatureRow("everyday", "clock, calculator, wi-fi info, homelab status", "offline"),
    FeatureRow("plugins", "installed plugins add more — radio stations, media providers, … — managed and documented in the web dashboard"),
)

internal data class TechRow(val name: String, val desc: String)

internal val MANUAL_TECH = listOf(
    TechRow("satellites", "Pi Zero 2 W · openWakeWord · ReSpeaker 2-Mics HAT or XVF3800 USB array · WebSocket audio streaming · aux-out playback"),
    TechRow("core", "FastAPI · Python 3.11+ · native on Windows for CUDA · intent router → handlers → TTS · background workers · in-process plugin runtime"),
    TechRow("whisper", "faster-whisper large-v3 on CUDA (float16), via CTranslate2"),
    TechRow("ollama", "llama3.2:3b answers questions · qwen2.5:14b routes tool calls (stronger schema adherence)"),
    TechRow("tts", "engine chain: edge-tts (cloud neural) → Piper (local) → system voice · voices configurable per satellite"),
    TechRow("postgres", "PostgreSQL 16 (the only Dockerized piece) · migration-only schema · LISTEN/NOTIFY live state bus"),
    TechRow("mpd", "Music Player Daemon — one lazily-spawned container per room"),
    TechRow("workers", "timer watcher, library index/enrich, memory extractor, and more — plugins register their own alongside"),
    TechRow("web", "separate FastAPI on :6369 · React (Babel-in-browser) · reads the same Postgres, calls the Domovoi core for live state"),
)

internal data class TroubleRow(val symptom: String, val fix: String)

internal val MANUAL_TROUBLE = listOf(
    TroubleRow("choppy audio", "check the Pi's wi-fi rate first: iw dev wlan0 link — if rx is stuck at 1 Mbit/s, run wpa_cli reassociate before touching anything else."),
    TroubleRow("no sound at all", "only one process can hold a Pi's audio card at a time (no software mixer on Pi OS Lite). Make sure nothing else is playing."),
    TroubleRow("mpg123 crashes", "on a fresh Pi it defaults to JACK and segfaults — force ALSA with -o alsa."),
    TroubleRow("\"port not listening\"", "the music stream binds lazily — that message usually just means nothing is playing yet."),
    TroubleRow("one Pi worse than others", "a hotter-running satellite drops mic frames — suspect its SD card / dependencies, not the code."),
    TroubleRow("quiet / one-sided music", "the XVF3800's audio-out is mono — use a mono→stereo adapter for a stereo speaker pair."),
    TroubleRow("\"managed by your organization\"", "that Windows firewall popup is usually stale block rules, not a real policy."),
    TroubleRow("server can't reach a Pi", "use a LAN hostname, never localhost — on the Pi that resolves back to itself."),
)

internal data class FaqRow(val q: String, val a: String)

internal val MANUAL_FAQ = listOf(
    FaqRow(
        "What's a domovoi?",
        "A household guardian spirit from Slavic folklore — it often takes the form of a cat, which is why a cat lives in the UI. Domovoi is the assistant you talk to; the wake name is configurable.",
    ),
    FaqRow(
        "Does it work without internet?",
        "Yes — it's local-first. Speech-to-text, understanding, local voices, the music library, timers, intercom and more all run offline. Only web search, cloud voices and some plugin features need the network, and they degrade gracefully instead of breaking.",
    ),
    FaqRow(
        "Is my data private?",
        "Everything runs on your own hardware. There's no cloud account and nothing leaves the house unless a network feature asks for it.",
    ),
    FaqRow(
        "Can I change the wake word?",
        "Yes. Record a handful of clips on a satellite and train a custom model — Settings → Wake Words. (Training is Linux-only, so it runs off-box.)",
    ),
    FaqRow(
        "Can I add my own voice?",
        "Upload a Piper .onnx model for a fully-local voice, or register a Microsoft Edge neural voice — Settings → Voices.",
    ),
    FaqRow(
        "Why two different LLMs?",
        "A small fast model handles open questions; a stronger model routes tool calls, where strict schema adherence matters more than speed.",
    ),
    FaqRow(
        "How do rooms play different music?",
        "Each room gets its own music daemon, so the kitchen and the garage can play completely independent tracks at the same time.",
    ),
    FaqRow(
        "How do I add features?",
        "Install plugins from the web dashboard (Settings → Plugins). A plugin can add voice commands, background workers, dashboard pages and app screens — the Stations screen here, for example, appears when the radio plugin is installed.",
    ),
)

internal data class HowtoRow(val title: String, val act: List<String>, val diag: List<String>)

internal val MANUAL_HOWTO = listOf(
    HowtoRow(
        "satellites",
        listOf(
            "Add one: flash the Pi and drop in its config — it shows up here once it connects.",
            "Change its voice or wake word in Settings, then push to the room.",
            "Set playback volume per room from the Music page.",
        ),
        listOf(
            "No response? Check the wake loop is running and the mic board matches its trained model.",
            "Choppy audio? Check wi-fi rx rate (iw dev wlan0 link); reassociate if stuck at 1 Mbit/s.",
            "No sound? Make sure nothing else is holding the Pi's single audio card.",
        ),
    ),
    HowtoRow(
        "the web dashboard",
        listOf(
            "Switch pages from the nav — each surface manages one area.",
            "Change server settings under the gear → Configuration.",
            "Install and manage plugins under Settings → Plugins.",
            "Check versions / pull updates under Configuration → Version.",
        ),
        listOf(
            "Stale data? Hard-refresh (Ctrl+Shift+R).",
            "Actions failing? The dashboard must reach the Domovoi core on :6370 — confirm it is running.",
        ),
    ),
    HowtoRow(
        "the core service",
        listOf(
            "Restart it on the server to apply restart-tier config changes.",
            "Check health at /v1/health and connectivity at /v1/connectivity.",
            "Update under Configuration → Version: check, pull, then restart to apply.",
        ),
        listOf(
            "Nothing responds? Confirm the process is up and Postgres is reachable.",
            "Bad routing? Make sure both Ollama models are installed and loaded.",
        ),
    ),
    HowtoRow(
        "whisper (speech-to-text)",
        listOf(
            "Switch the speech-to-text model or pre-download a size from the Models page.",
            "Prefer large-v3 for accuracy; a smaller size if VRAM is tight.",
        ),
        listOf(
            "Slow or garbled? Confirm it is on CUDA (float16), not the CPU.",
            "First use slow? The model may be cold-downloading — pre-stage it on the Models page.",
        ),
    ),
    HowtoRow(
        "ollama (the language model)",
        listOf(
            "Switch the Q&A or tool-routing model, or pull a new one, from the Models page.",
            "Two models by design: a fast one answers, a stronger one routes tools.",
        ),
        listOf(
            "LLM commands failing? Check the Ollama server is reachable and the model is installed.",
            "See what's loaded in VRAM right now on the hardware panel.",
        ),
    ),
    HowtoRow(
        "text-to-speech",
        listOf(
            "Pick or upload a voice under Settings → Voices and set a default.",
            "Register an Edge cloud voice by id, or upload a local Piper .onnx.",
        ),
        listOf(
            "No voice? Play a sample — the engine falls back edge → piper → system.",
            "Wrong voice? Confirm the intended one is set as default.",
        ),
    ),
    HowtoRow(
        "postgres (state)",
        listOf(
            "Mostly hands-off — schema changes are migrations applied on deploy; plugins keep their own schemas.",
        ),
        listOf(
            "State missing or errors? Confirm the Postgres container is up and reachable.",
            "Check the latest migration has been applied to both prod and test DBs.",
        ),
    ),
    HowtoRow(
        "music playback",
        listOf(
            "Play, queue and favorite music from the Music page — each room is independent.",
        ),
        listOf(
            "\"port not listening\" usually just means nothing is playing (it binds lazily).",
            "No music in a room? Check that room's MPD container is running.",
        ),
    ),
    HowtoRow(
        "background workers",
        listOf(
            "Enable/disable background workers (library upkeep, memory, plugin workers…) under Configuration.",
        ),
        listOf(
            "A feature isn't updating? Check its worker's enabled flag — and that the core service was restarted after the change.",
        ),
    ),
    HowtoRow(
        "plugins",
        listOf(
            "Install, enable/disable and remove plugins from the web dashboard (Settings → Plugins).",
            "Plugin screens in this app (like Stations) appear automatically once the matching plugin is installed.",
        ),
        listOf(
            "A screen is missing here? Check the plugin is installed AND enabled on the server, then pull to refresh.",
            "Plugin misbehaving? Its logs and worker status show in the web dashboard's observability panel.",
        ),
    ),
)
