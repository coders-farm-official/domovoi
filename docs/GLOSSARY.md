# Glossary

Every Domovoi term you'll meet in these docs, in the dashboard, or in the code — alphabetical, defined in plain English. Cross-references in *italics* point at other entries here.

---

**Acquisition** — a structured request to obtain media for the local library ("get me this song", by search text or URL). Acquisitions go into a queue table where a *fulfiller* picks them up; with no fulfiller installed they simply wait, visibly, until one is. See *media acquisition queue*.

**Admin session** — the 30-day bearer token you get by logging in with the admin password. Risky operations on both the dashboard (port 6369) and the core API (port 6370) accept only this token; it's minted once against a shared table, so one login covers both.

**AEC (acoustic echo cancellation)** — removing the satellite's own speaker output from its microphone signal. The XVF3800 array does this on-chip, which is what makes talking over the greeting, reliable *barge-in*, and open-mic *chat mode* possible; the 2-mic HAT has no AEC.

**Band** — see *priority band*.

**Barge-in** — interrupting Domovoi while it's talking: the satellite keeps listening during TTS playback and cuts playback the moment it hears real speech (or, in the stricter mode, only the wake word). Tunable per satellite in `~/.domovoi/config.toml`.

**Bundled plugin** — a plugin that ships in the Domovoi repository itself and installs from there, rather than from a third-party zip. The radio plugin (`plugins/radio`, publisher Coders Farm) is the bundled reference example.

**Capability** — a named service a plugin *provides* or *consumes*, declared in its manifest (e.g. the radio plugin provides `now-playing-source:radio` and consumes `media-acquisition-queue`). Capabilities are how core matches features to whichever plugin implements them without knowing provider specifics.

**Chat mode** — an optional open-mic conversation mode ("let's have a chat"): turns flow back and forth without repeating the wake word, backed by a self-hosted Letta agent on the local Ollama. Off by default, and requires a full-duplex satellite (XVF3800) because the open mic needs *AEC*.

**Confirmation kind** — the namespaced label (`core.<kind>` or `<plugin-slug>.<kind>`) on a pending yes/no question Domovoi has asked you ("Should I remember that?"). Each session holds at most one pending confirmation, and the kind routes your "yes" back to the feature that asked.

**Core** — the central voice service (`domovoi/`, FastAPI, port **6370**): speech-to-text → intent routing → handlers → text-to-speech, plus satellite WebSockets, persistence, background workers, and the plugin runtime. When docs say "the core" or "the Domovoi server," this is the process they mean.

**Dashboard** — the web management UI (`web/`, separate FastAPI process, port **6369**). It reads the same Postgres as the core and proxies live actions (restart a satellite, play music) to the core's admin endpoints.

**Dev install** — registering a plugin directory in place with `domovoi plugin dev <path>` for local development: same manifest validation as a real install, but no zip, no lockfile requirement, no trust screen. See [Plugin Development](PLUGIN_DEVELOPMENT.md).

**Drop-in** — a live two-way audio call into a satellite room ("drop in on the kitchen"), relayed by the core as raw audio with no STT or TTS in the path. The receiving room either auto-answers or is asked first, depending on the configured accept mode. Usually room-to-room, but the Android app can also drop in *from your phone*: it joins the room's audio bridge over `WS /v1/dropin/{room_id}` as a call peer without registering as a satellite (see [API_REFERENCE.md](API_REFERENCE.md)).

**Event bus** — the core's in-process publish/subscribe channel (`core.*` events, `plugin.<slug>.*` for plugins). Fire-and-forget with no delivery guarantee — anything that must survive a crash uses a database queue instead, and bus-driven cleanup is always backed by a periodic reconciliation sweep.

**Fast path** — a hand-written regex pattern on a handler that matches a command directly ("set a timer for ten minutes") and dispatches with no LLM involved. Fast paths are the low-latency spine of the router; only unmatched utterances fall through to *tool routing* and Q&A.

**Fulfiller** (acquisition fulfiller) — a *provider plugin* that registers the `media-acquisition-fulfiller` capability: it claims pending rows from the *media acquisition queue*, obtains the media from an external source, and completes or fails the row.

**Flyway** — the migration tool that owns the database schema. Core migrations are append-only SQL files under `domovoi/db/migrations/`; you run them with `docker compose run --rm flyway`.

**Handler** — the unit of voice functionality: a class that declares its *fast paths*, a tool schema for LLM routing, its network needs (`requires_network: no | degraded | yes`), and an offline fallback. Timers, music, intercom, news — each is a handler; plugins add more.

**ICY metadata** — the `StreamTitle` now-playing tags many internet radio streams embed. The radio plugin's ICY poller reads them as the cheap first tier of song detection, with audio sampling as the fallback.

**Intercom** — one-way announcements to one room or every room ("announce to the house: dinner's ready", "tell the kitchen the package arrived"). One-way; for two-way audio see *drop-in*.

**Intents log** — the audit table (`intents_log`) that records every routed voice turn: transcript, which handler matched, via which *matched path*, and who was speaking. Writing it is non-optional by design; the conversation log is its sibling.

**Letta** — the self-hosted agent framework that backs *chat mode*, running in its own container with its own database, entirely on the local Ollama. Only conversational turns touch it; normal commands never do.

**Local-first** — Domovoi's core contract: the voice pipeline (wake word, STT, routing, LLM, local TTS) runs entirely on your hardware, every handler declares its network needs, and anything that needs the internet must degrade honestly when it's absent. See the [FAQ](FAQ.md#does-it-work-offline).

**Manifest** — a plugin's `domovoi-plugin.toml`: identity (slug, publisher, version), entry points, declared handlers and workers, capabilities, permissions and warnings, dependencies, web pages. It's what the install preview renders and what the runtime validates.

**Matched path** — how a turn got routed, as recorded in the *intents log*: `fast` (regex fast path), `llm` (LLM tool call), `qa` (LLM answer); offline fallbacks log `fast_offline` / `llm_offline`. Other core values include `confirmation`, `auto_search`, and `volatile_offer`. An open vocabulary — plugins can register their own values.

**Media acquisition queue** — the `media_acquisitions` table: the single funnel through which voice commands, dashboard actions, chat tools, and plugins request media, in a generic format no provider ever leaks into. States: `pending → claimed → done`, or `failed` / `unfulfillable`.

**MPD (Music Player Daemon)** — the per-room music engine. Each room gets its own MPD container (queue, current track, independent playback state), lazily created on the room's first connection: control ports start at **6650** and HTTP stream ports at **8050**, counting up per room. The satellite plays the room's HTTP stream through `mpg123`.

**music_ready handshake** — the start-of-playback protocol: the core queues the track paused, tells the Pi to spawn its stream player, waits for the Pi's `music_ready` frame, then unpauses — so the first second of a song doesn't stutter. A fallback timer resumes playback anyway if the frame never arrives.

**Noise gate** — the satellite-side loudness floor: frames quieter than a threshold are ignored even if they sound like speech, so the TV two rooms away doesn't trigger captures. Self-calibrating by default from real ambient audio.

**Now-playing source** — the registry entry a feature stamps when it starts external (non-library) playback in a room, so the dashboard's now-playing card, the sweeper, and favorites all work generically. The radio plugin registers `radio`; core never learns provider vocabulary.

**Ollama** — the local LLM server (port 11434). Domovoi runs two models through it: a small fast one for conversational Q&A and a larger schema-reliable one for *tool routing*.

**openWakeWord** — the on-satellite wake-word engine. Ships prebuilt models (`hey_jarvis` is Domovoi's default) and supports custom models trained from your own recorded clips.

**Piper** — the local neural TTS engine — second link in the TTS chain and the fully-offline voice. Voice models download once or can be uploaded on the dashboard's Voices page.

**Plugin schema** — the private Postgres schema (`plugin_<slug>`) where a plugin keeps its tables, with its own migration ledger. Plugins never run DDL against core tables; a linter enforces the boundary.

**Priority band** — a handler's explicit dispatch-order number: the router tries fast paths in ascending band order, so a greedy catch-all pattern in a late band can't poach phrasings from more specific handlers. Ties break deterministically (core first, then plugin slug).

**Provider plugin** — a separately installed plugin that connects Domovoi to an external media source, by acting as a *fulfiller*, a streaming search provider, or both. Core ships with none; the generic queue means you choose what, if anything, to install.

**Realtime channel** — the live-update path from database to browser: a feature fires a Postgres NOTIFY on commit, the dashboard's listener maps it to a named channel (e.g. `radio.stations`) and pushes a fresh snapshot over WebSocket. Plugins declare theirs in the manifest.

**Room** — the logical unit of the household, identified by the stable `room_id` in a satellite's config. Rooms are what the intercom, drop-in, music playback, and the Satellites page address; each room owns one satellite and one *MPD* instance.

**Satellite** — the per-room listening device: a Raspberry Pi (Zero 2 W / Pi 4) with a ReSpeaker mic board, running the client in `satellite/`. It owns the local audio loop — wake word, VAD endpointing, *barge-in*, LEDs — and streams audio to the core over a WebSocket (`ws://<server>:6370`). Config lives in `~/.domovoi/config.toml` on the Pi; it runs as the `domovoi-satellite` systemd service.

**Session context** — the per-room short-term memory in the `sessions` table: recent turns, the pending confirmation, expectation state ("waiting for a name"). It's what makes "yes", "repeat that", and follow-up questions work.

**Setup code** — the 8-word one-time code written to `~/.domovoi/setup-code.txt` (and printed to the core console) on first boot. Entering it on the dashboard proves you control the server and lets you set the admin password; the file is deleted once used. `python -m domovoi.main --reset-admin` regenerates it.

**Stub mode** — `USE_STUBS=true`: Whisper, Ollama, and TTS are replaced by deterministic fakes so the full test suite (and a hardware-less dev loop) runs without CUDA, models, or audio. The test config forces it on.

**Tool routing** — the router's second tier: when no *fast path* matches, the tool-call model picks a handler and arguments from the handlers' tool schemas. Slower than a fast path, smarter about phrasing; logged as `matched_path = llm`.

**Trust screen** — the unskippable confirmation step of a plugin install: publisher, permissions, the plugin's own warnings, and the blunt statement that a plugin runs with full access to your server. No plugin code executes before you accept it.

**VAD (voice activity detection)** — the classifier that decides which audio frames contain speech. The satellite uses it to find the end of your utterance and to detect *barge-in* during playback.

**Voice profile** — a locally stored voice embedding tied to a person, enrolled by introduction ("I'm Sarah") and matched on every utterance so Domovoi knows who's talking. Powers per-person memory and personalization; "forget me" deletes it.

**Wake word** — the phrase that opens the mic (default `hey_jarvis`). Detection runs on the satellite via *openWakeWord*; custom words ("Hey Domovoi") are recorded, trained, and pushed to rooms from the dashboard's Wake Words tab, with trained models synced into the Pi's `~/.domovoi/wake_models/` cache.

**Whisper** — the local speech-to-text model (`faster-whisper`, default `large-v3` on CUDA). All transcription happens on the server; no audio goes to any cloud STT.

**Worker** — a background loop in the core (or a plugin): the timer watcher, news fetcher, library enricher, playback-state sweeper, wake-word trainer, and friends. Plugins declare theirs in the manifest as `poll`, `longrun`, or `startup` workers.

**XVF3800** — the ReSpeaker XVF3800 USB 4-mic array, the premium satellite mic board: on-chip *AEC*, 60 dB auto gain, beamforming, and a 12-LED ring. The alternative is the simpler ReSpeaker 2-Mics Pi HAT. See [Satellite Hardware](SATELLITE_HARDWARE.md).

---

*Missing a term? The [Architecture](ARCHITECTURE.md) and [Plugin Development](PLUGIN_DEVELOPMENT.md) docs go deeper on most of these.*
