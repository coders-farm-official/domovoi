# Domovoi Architecture

This document describes how Domovoi is put together: the processes it runs, the
ports they bind, how a voice command travels from a satellite microphone to a
spoken answer, and the registries and invariants that keep the whole thing
predictable. It is written for people working on Domovoi itself or building
plugins against it.

Deeper diagram-first walkthroughs of individual features live in
[`docs/uml/`](uml/):

| Focus | Doc |
|---|---|
| A voice turn end to end | [uml/voice-turn.md](uml/voice-turn.md) |
| The satellite WebSocket protocol | [uml/satellite-protocol.md](uml/satellite-protocol.md) |
| Plugin install / enable / upgrade lifecycle | [uml/plugin-lifecycle.md](uml/plugin-lifecycle.md) |
| Plugin runtime classes (Handler, Worker, SDK) | [uml/plugins-runtime.md](uml/plugins-runtime.md) |
| Library, playlists, MPD, acquisition queue | [uml/media-and-library.md](uml/media-and-library.md) |
| Admin auth: setup, login, gated requests | [uml/auth.md](uml/auth.md) |

Related reading: [API_REFERENCE.md](API_REFERENCE.md) for endpoint details,
[PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) for the plugin author's view,
[SECURITY_PRIVACY.md](SECURITY_PRIVACY.md) for the threat model, and
[SATELLITE_HARDWARE.md](SATELLITE_HARDWARE.md) for the Pi side.

---

## 1. Processes and ports

Domovoi is a small constellation of processes on one server (Windows-first,
CUDA for speech-to-text), plus Raspberry Pi satellites around the house.

```mermaid
flowchart LR
    subgraph House["Rooms"]
        SAT1["Satellite Pi<br/>(kitchen)"]
        SAT2["Satellite Pi<br/>(office)"]
        BROWSER["Browser /<br/>Android app"]
    end

    subgraph Server["Domovoi server"]
        CORE["Core voice service<br/>domovoi/ — FastAPI<br/>:6370"]
        WEB["Web dashboard backend<br/>web/ — FastAPI<br/>:6369"]
        PG[("Postgres (docker)<br/>host :6432 → 5432<br/>DBs: domovoi, domovoi_test")]
        OLLAMA["Ollama<br/>:11434<br/>two models"]
        SEARX["SearXNG<br/>:6888"]
        MPD1["MPD container<br/>domovoi-mpd-kitchen<br/>ctrl 6650+N / http 8050+N"]
        MPD2["MPD container<br/>domovoi-mpd-office<br/>ctrl 6650+M / http 8050+M"]
    end

    SAT1 <-->|"WS /v1/stream/{room_id}<br/>PCM + JSON frames"| CORE
    SAT2 <-->|WS| CORE
    SAT1 -->|"mpg123 pulls<br/>MP3 http stream"| MPD1
    SAT2 --> MPD2
    BROWSER <-->|"HTTP + WS /ws/state"| WEB
    WEB -->|"reads/writes"| PG
    WEB -->|"proxies live-state actions<br/>to /v1/admin/*"| CORE
    CORE --> PG
    CORE --> OLLAMA
    CORE --> SEARX
    CORE -->|"provisions + controls"| MPD1
    CORE --> MPD2
```

| Process | Port | What it is |
|---|---|---|
| **Core voice service** (`python -m domovoi.main`) | **6370** | FastAPI app. STT → intent routing → handlers → TTS, the satellite WebSocket endpoint, background workers, the plugin runtime, admin endpoints. Whisper (faster-whisper, CUDA) runs in-process. |
| **Web dashboard** (`python -m web.backend.main`) | **6369** | A separate FastAPI process. Reads the same Postgres directly; anything touching *live* state (connected satellites, now playing) is proxied to core admin endpoints. Serves the no-build React frontend. **Never imports the plugin runtime.** |
| **Postgres** (docker compose, `domovoi/docker-compose.yml`) | **6432** (host) → 5432 (container) | Databases `domovoi` and `domovoi_test`. The non-default host port lets Domovoi coexist with any Postgres already on 5432. |
| **Per-room MPD** (docker, lazy) | control **6650+N**, http-stream **8050+N** | One MPD daemon per satellite room so queues/volume are independent. Provisioned on first WebSocket connect for an unknown `room_id` by `domovoi/mpd_provisioner.py`; **not** in the compose file. Container `domovoi-mpd-<room>` maps host ports onto in-container 6600/8001. Assignments persist in the `mpd_rooms` table (allocation is `max + 1` from the bases, serialized by a Postgres advisory lock). |
| **Ollama** | 11434 | Two model slots — see below. |
| **SearXNG** | 6888 (default `SEARXNG_URL`) | Local metasearch for the "want me to check that online?" flows. |

**Two Ollama models, not one.** `OLLAMA_MODEL` (default `llama3.2:3b`) answers
conversational Q&A — fast and cheap matters. `OLLAMA_TOOL_MODEL` (default
`qwen2.5:14b`) routes tool-call dispatch — schema adherence matters. They are
deliberately separate knobs.

Core settings load from environment variables and `domovoi/.env` in the repo
checkout — dashboard edits persist back to that file (see
[§8](#8-configuration)). `~/.domovoi/` on the server
holds runtime artifacts and per-plugin config: rendered greeting sounds, Piper
voice models, wake-word models and clips, plugin installs, data, and `.env`
files, plus the one-time setup code. Each Pi keeps its own config in
`~/.domovoi/`.

---

## 2. The voice turn pipeline

A full sequence diagram is in [uml/voice-turn.md](uml/voice-turn.md); the wire
protocol is in [uml/satellite-protocol.md](uml/satellite-protocol.md). In
prose:

1. **The Pi owns wake.** Wake-word detection (openWakeWord, default
   `hey_jarvis`; custom wake words such as "Hey Domovoi" are trained
   in-product), VAD, noise gate, and barge-in detection all run on the
   satellite. On a wake it acknowledges first — a spoken greeting, a chime
   or the LED alone (`[wake] ack_mode`, per satellite) — plays that to the
   end, drops what its mic heard meanwhile, and only then listens.
   Captured audio streams to the core as 16 kHz mono int16 PCM between
   `utterance_start` / `utterance_end` frames on `WS /v1/stream/{room_id}`.
2. **STT.** The core transcribes the buffered utterance with Whisper
   (faster-whisper on CUDA or CPU; deterministic stub under `USE_STUBS=true`). If the
   Pi flagged `greeting_played`, a wake greeting that bled past the mic
   array's echo cancellation is stripped from the transcript (the Pi names
   the clip it played, `greeting_clip`, so only that line matches), and a
   transcript that is nothing but the greeting ends the turn unrouted. A
   current Pi plays the greeting before it opens the capture and says so
   (`ack_before_capture`): such a capture cannot be the greeting alone — a
   "Yes." in it is the person answering — so only the strip applies, as a
   safety net. Whisper loads
   once at boot and a failed load is never fatal: the core drops to
   `whisper_cpu_fallback_model` on cpu/int8, and failing that runs without
   STT — a turn then gets a spoken "can't understand speech" notice and is
   not routed (`domovoi/clients/whisper.py`; state on `/v1/admin/hardware`).
   **Speculative transcription:** the Pi only sends `utterance_end` after
   `listen.silence_timeout` (1.2 s) of silence, but its frames arrive as
   they are spoken, so at the first ~240 ms pause the core copies the
   buffer and starts Whisper on the copy (the voice embedding follows the
   decode, never beside it). At
   `utterance_end` it uses that transcript if and only if the Pi's last
   voiced frame is inside the copy — exact frame accounting, from
   `last_voiced_frame` in `utterance_end` (new satellites, which also send
   `speech_pause` hints) or from the silence timeout an older satellite
   reported in `config_status` (`domovoi/endpointing.py`); otherwise the
   whole buffer is transcribed as before. One Whisper call (or embedding)
   per room at a time. The silence and the transcription overlap instead
   of adding up (`speculative_stt_enabled`). On an English-only model a
   capture of up to 9 s — any command — decodes on a 10 s mel window
   instead of the 30 s one faster-whisper pads to, calling CTranslate2
   directly: about a quarter of the CPU time, falling back to the 30 s
   path for longer captures or a blank or unsure result
   (`whisper_short_window_enabled`). A short-window transcript the router
   would hand to the tool model (no fast path, not a yes/no, not a plain
   question: `router.goes_to_the_tool_model`) is heard a second time on
   the 30 s path and that text routed (`whisper_short_window_recheck`) —
   on far-field audio about one command outcome in ten differs between
   the two windows, and hearing these again scored above both.
   A question about the world (`router.is_question_about_the_world`) and
   a request for a joke or a story (`router.is_request_for_a_story`) are
   spared: the streamed router gives those up in ~0.7 s.
   **Early commit:** when that early transcript is a whole closed command,
   the core doesn't wait for the rest of the silence — it sends the
   satellite `end_capture` and answers. Only for satellites that declared
   `capture_control` in their hello (and report their pauses), only for
   wake-word and follow-up turns, only for a transcript the router's own
   dry run (`router.plan_route`) sends down a fast path that opted in with
   `FastPath.early_commit` — tier A for closed phrases ("pause the
   music", 350 ms hold), tier B for phrases a pause can split (a timer's
   duration, "volume 40", the clock, any one-word command, a whole yes/no
   to a parked question; 650 ms) — and only after the satellite's own
   detector has been silent that long since the last word
   (`domovoi/early_commit.py`; `early_commit_enabled`,
   `early_commit_tier_b`, and `[listen] early_commit` per satellite). On a
   greeting turn the copy is screened for the greeting exactly as the turn
   will be (the Pi names the clip in `speech_pause` too): from an older Pi,
   a copy that is only the greeting never commits.
   Anything said after the hold is lost, so the tiers are checked in CI
   against a corpus of real commands for a shorter prefix that is a
   different command (`domovoi/tests/test_early_commit.py`).
3. **Voice identification** (best-effort, pre-router): the utterance is
   embedded and matched against enrolled voice profiles, yielding
   `person_id` + `presence_tier` in the turn's `Context`.
4. **Chat-mode bypass.** If the session is in conversational chat mode, the
   turn skips the command router entirely and goes to the Letta agent (or ends
   the chat on an exit phrase). Command mode pays only one session-context
   read for this check.
5. **Routing** (`domovoi/router.py`) — the stages below.
6. **TTS.** The response text is split into sentences and synthesized through
   the engine chain **piper → system** (**edge → piper → system** only when
   Edge is the chosen engine and the internet answer isn't `never`; Edge is
   never a fallback rung) — per-sentence fallback; a sentence rendered at a
   different native rate is resampled to the announced rate. Sentence synthesis is pipelined so the Pi's playback buffer never
   drains between sentences.
7. **Post-turn coordination.** `response_end` carries `expect_followup` /
   `pi_action`; music suppressed by wake capture auto-resumes; intercom
   announcements fan out to target rooms.

### Router stages, in dispatch order

Verified against `domovoi/router.py` and `domovoi/streaming.py`. Each stage
either returns a response (logging the turn with the `matched_path` shown) or
falls through to the next.

| # | Stage | `matched_path` | What happens |
|---|---|---|---|
| 0 | **Pending-confirmation pre-empt** | `confirmation` | If the previous turn parked a `pending_confirmation` in the session context and this turn is a clear yes/no, the answer is dispatched to the owning handler's `handle_confirmation()` — but only for a `kind` the handler declares in `confirmation_kinds` (namespaced `core.<kind>` / `<slug>.<kind>`). One outstanding question per session; the payload is cleared one-shot. Ambiguous answers fall through and the payload survives one more turn. |
| — | *Filler strip* | — | Leading politeness ("please", "can you", "yeah,") is stripped so anchored fast-path regexes still match. Done **after** the yes/no pre-empt (a bare "yes" must still resolve a confirmation) and **before** fast paths. |
| 1 | **Fast paths, by priority band** | `fast` / `fast_offline` | The band-sorted handler registry is scanned; the first fast-path regex to match wins. The **offline gate**: a `requires_network="yes"` handler auto-falls back to `fallback_offline()` while offline; a `"degraded"` handler is gated *per fast path* — only paths marked `offline_ok=False` fall back. |
| 2 | **LLM tool-call** | `llm` / `llm_offline` | The tool-routing Ollama model sees each handler's `tool_schema` and may pick one; the handler's `execute_from_tool()` runs (or `fallback_offline()` if it needs the network and we're offline). A handler can withhold its schema for an utterance that provably can't be its own (`Handler.offers_tool`, `router.offered_tool_schemas`): double_check without a verification word (doubt said outright — "that can't be right", "check online" — counts), news without a news word, calculator and library only from a plain who/why/where question about the world, reminder and timer on talk about a timer or reminder ("my reminder didn't go off", "you have a reminder in 10 minutes") (`handlers/shared/tool_gate.py`) — a small tool model otherwise reads a bare factual question as a claim to verify or a subject to look up, and a remark about a reminder as a new one. Such utterances never reach the router, so every routed turn sees calculator, library, reminder and timer: the list varies only at its end. A plain question about the world (who/why/where with nothing about the house, the speaker or something pointed at), a request for a joke, fact, story or explanation, or talk about a timer or reminder skips this stage altogether and goes to the QA fallthrough (`answers_without_tools`). Gated schemas go last in the list — the usually-offered ones (calculator, library) before the on-request ones (double_check, news), so asking for a check or the news only appends a tool — to keep the prompt prefix Ollama caches stable; reminder and timer keep their band place among the ungated tools (`router._GATES_KEEP_BAND_PLACE`: moved to the end, qwen3:8b stopped routing "Can you check online?" to double_check); a call naming a withheld tool is treated as no call. The call is streamed and dropped at the first text that isn't a `<think>` block (no tool is coming; 0.9+ sends a real call parsed, so text that merely looks like one is text too) or at the first tool call, and capped at `num_predict` 256 when `think=false` went out on it; an Ollama older than 0.9 gets the plain request, and it — like a router allowed to think, or one whose `think` flag latched off — is capped at 1024, room for a hybrid model's reasoning before its call. `scripts/eval_routing.py` replays a fixed corpus (`scripts/routing_corpus.json`) against a live core or straight at Ollama (`--live-tools` adds a core's plugin tools). |
| 3 | **Auto-search short-circuit** | `auto_search` | For a time-sensitive question category, a *known* speaker who previously opted in to auto-search for that category gets an answer straight from SearXNG — skipping local QA entirely. |
| 4 | **Volatile-question gate** | `volatile_offer` / `qa` | For freshness-critical categories (weather, prices, scores, current events) the local model's confident-but-stale guess is exactly the failure mode, so Domovoi doesn't guess: online it asks "want me to check <subject>?" and parks a `core.self_doubt_offer` confirmation; offline it says plainly that it can't answer. |
| 5 | **QA fallthrough** | `qa` | General Q&A via the local Ollama QA model, with session history and a per-speaker profile prefix (memories, favorites, preferences). The answer is the model's plain-text reply, spoken whole — on a voice turn sentence by sentence as the model writes it (`Context.stream_qa` → `Response.qa_stream`, `domovoi/spoken_answer.py`), the turn recorded with the full text once it has been said; an empty reply is retried once (while nothing has been said; a cut tail is never spoken) and then answered with a short "no answer" line (the "language model isn't answering" line is only for a failed call). For a question, an answer that admits it may be out of date, or a heuristic category, appends "Want me to check that online?" as its own sentence (parking `core.self_doubt_offer`); an answer that already ends by offering to look it up has that offer parked instead. Otherwise the implicit memory extractor may surface one pending "should I remember that?" offer. |

**Every routed turn is persisted** by `router._persist_turn`: one `intents_log`
row (routing decision, latency, presence), one `conversation_log` row (full
user/assistant text), and an append to the session's `recent_turns`. This is
centralized and non-optional — see [Invariants](#9-invariants).

A turn spoken to a satellite also carries a per-stage stopwatch
(`domovoi/turn_timings.py`, on `Context.timings`): capture length, the
silence waited out after the last word, speech-to-text (the call, and the
wait it actually cost), voice identification and the Whisper that ran are
written in that same `intents_log` insert (`timings`, V015), with whether a
speculative transcript was used and whether the core ended the capture
early (and on which tier); the routing transaction, the first reply
audio, the total from `utterance_end` and the last word to the first reply
audio are merged into the row by id once the reply is playing, off the
latency path. `latency_ms` stays the router's share alone.
`GET /v1/stats/latency` summarises the stages, numbers only.

In a room an admin opted in to **command recording** (V016, off for every
room by default), a routed wake-word or follow-up command also keeps its
audio and a sidecar (transcript, route, timings, why the satellite ended the
capture) under `~/.domovoi/captures/`, written after the reply is on its way
and deleted after 14 days (`domovoi/command_captures.py`). It exists to tune
end-of-turn detection; SECURITY_PRIVACY.md has what is never kept and who
can read it.

With `fastlane_mode=shadow` (off by default; the `fastlane` extra) a
second, streaming recognizer (`domovoi/fast_lane.py`, sherpa-onnx) reads
the same 30 ms frames while the person is still talking. One worker thread
serves every room: `StreamSession` opens a capture at `utterance_start`
(wake-word and follow-up turns only), feeds it each frame from `_on_audio`,
ends it at `utterance_end`, and just before routing asks it what it would
have done. When its partial transcript was a complete closed command
(a tiered fast path, matched the router's way) followed by the tier's hold
of quiet (350 ms, or 650 ms for numbers, the clock and bare words), it
logs that decision against Whisper's transcript and adds `fastlane_*` keys
to the turn's `timings`. It never routes, replies or ends a capture: the
turn is exactly the same with it on or off.

---

## 3. Handler priority bands

Dispatch order is **ascending `priority_band`**; ties break core-before-plugin,
then plugin slug, then handler name (`domovoi/handlers/base.py::registry_sort_key`).
There is no hand-ordered list — every handler carries its own band. The core
map (from `domovoi/handlers/__init__.py`):

| Band | Handler | Why it sits here |
|---|---|---|
| 100 | `dismiss` | Brush-offs win before anything acts |
| 110 | `voice_profile` | "I'm Sarah" before any greedy capture |
| 120 | `wifi` | "Fix the wifi" wins over any future "fix" |
| 130 | `voice` | Device-control cluster near the top |
| 140 | `reminder` | Before timer ("remind" collision) |
| 150 | `calculator` | Digit-anchored, well before media |
| 160 | `timer` | |
| 170 | `clock` | |
| 180 | `repeat` | Clusters with double_check |
| 190 | `double_check` | Owns "verify" / "are you sure" |
| 200 | `dropin` | Immediately before intercom |
| 210 | `intercom` | Before voice_notes ("tell the kitchen X") |
| 220 | `chat_mode` | |
| 230 | `voice_notes` | |
| 240 | `memory` | |
| 250 | `homelab` | |
| 260 | `news` | Before all greedy media |
| 270 | `spoken_audio` | Anchored media before playlist/music |
| 280 | *(radio plugin)* | Before music so "play 97.5 fm" isn't poached |
| 290 | `playlist` | Before music's `^play` catch-all |
| 300 | `music` | Greedy `^play` catch-all |
| 310 | `library` | "Find X in my library" before a greedier "find X" |
| 900 | *(media-provider plugin)* | Greedy `^find` catch-all — LAST band |

Named ranges for plugins: **100–199** brush-off/identity (anchored patterns
only), **200–269** device control & comms, **270–349** anchored media,
**350–899** general plugin space (the default home), **900–999** greedy
catch-alls (required for any unanchored `(.+)$` fast path — enforced at plugin
install time).

Ordering is correctness: a greedy pattern in a low band silently poaches other
handlers' phrasings.

---

## 4. Workers and startup hooks

Background work uses two declarative shapes (`domovoi/workers/base.py`), run by
the `WorkerRunner` singleton (`domovoi/plugins_runtime/workers.py`). Core and
plugin workers are identical in shape; the runner keys everything by **owner**
(a plugin slug, or `core`) so disable/uninstall tears down exactly one owner's
set.

* **`Worker`** (poll shape) — implements only `async tick()`. Declarative
  attributes resolved live each tick: `interval_setting` (the settings field
  holding the cadence — required), `enabled_setting`, `stub_suppressed`
  (skipped entirely under `USE_STUBS=true`), `requires_online` (skip ticks,
  keep cadence, while offline). A raising tick is caught, logged, and counted
  — it never kills the loop.
* **`LongRunWorker`** (persistent-connection shape) — implements
  `async run(shutdown)`. In-loop reconnects are the worker's own job; the
  runner restarts a crashed `run()` with exponential backoff (1 s doubling to
  a 60 s cap, reset after 10 minutes healthy).
* **Startup hooks** — named (`<slug>.<name>`), ordered via `after=`, and
  connectivity-gated: a `requires_online` hook fires immediately when online,
  else on the first `core.connectivity_changed → online` event. Core lifespan
  milestones (e.g. `core.library_index`) are markable so plugin hooks can
  sequence after them.

Worker health (state, last tick, last error, consecutive failures, next
attempt) is surfaced per plugin on `GET /v1/plugins/<slug>/status`.

Everything runs on the **single core event loop** — deliberately. In-memory
state assumptions (registries, `app.state` caches) stay valid because there is
exactly one process and one loop that mutate them.

Core's own workers live in `domovoi/workers/`: the timer watcher, library
indexer + enricher, playback-state sweeper, media-plays pruner, news fetcher,
podcast feed poller, audiobook indexer, memory extractor, wake-word trainer,
document lock sweeper, the MusicBrainz alias fetch, and the three lyrics
workers (`lyrics_scan`, `lyrics_fetch`, `lyrics_index` — §6 "Lyrics").

---

## 5. Database ownership

One Postgres instance, two databases (`domovoi`, `domovoi_test`), and a strict
ownership split:

* **Core owns the `public` schema.** Migrations are Flyway
  (`domovoi/db/migrations/`, run via `docker compose run --rm flyway` /
  `flyway-test`), append-only, with `V001__baseline.sql` frozen once cut.
* **Each plugin owns exactly one schema: `plugin_<slug>`.** Plugin migrations
  are plain `V###__name.sql` files run by the core-owned
  `PluginMigrationRunner` (`domovoi/plugins_runtime/migrations.py`), not
  Flyway. The runner:
  * keeps a ledger at `plugin_<slug>.schema_history` (version, filename,
    checksum, applied_at);
  * creates a `NOLOGIN` role `plugin_<slug>` (idempotent; the application's
    DB user is a superuser or holds `CREATEROLE`) with `USAGE` on `public`
    and `ALL` on the plugin schema, and wraps each file in one transaction
    with `SET LOCAL search_path = "plugin_<slug>"` (the plugin schema
    only) + `SET LOCAL ROLE plugin_<slug>` — an unqualified name cannot
    reach `public`, and the role has no privilege on core tables, `COPY`,
    `ALTER SYSTEM` or role creation;
  * applies to **both** databases — prod first, then `domovoi_test`; a fresh
    install is both-or-neither;
  * validates sha256 checksums — an already-applied file that changed on disk
    refuses to run (append-only, no down-migrations);
  * enforces an install-time **SQL lint**: no `CREATE SCHEMA`, no
    `CREATE EXTENSION`, no DDL or DML naming `public.` or a foreign
    `plugin_*` schema, no cross-schema `REFERENCES`, no `SET/RESET ROLE`,
    `DO`, `COPY`, `ALTER SYSTEM`, `CREATE ROLE` or `LOAD`. (A tripwire in
    front of the role; plugins are trusted code once installed, but a
    migration file is confined to its schema by Postgres, not by review.)

Plugins never run DDL against core tables. Cross-schema references are **soft
refs** (e.g. `media_acquisitions.attach_to_playlist_id` has no FK); consumers
re-check at use time and reconcile periodically.

**Open enums** (`domovoi/registered_values.py`): extensible vocabulary columns
(`intents_log.matched_path`, `library_tracks.source`, `media_plays.source`,
`voices.engine`, `media_acquisitions.status`) carry no CHECK constraint, so
plugins can add values without core DDL. Validation happens app-side against
an in-process registry at write time; the `registered_values` *table* is an
informational mirror for DBAs, never consulted on the hot path.

---

## 6. The extension seams

Four in-process registries form the boundary between core and plugins. All are
process-wide singletons; all record registrations against an owner slug so
plugin disable/uninstall is a clean teardown. The class-level view is in
[uml/plugins-runtime.md](uml/plugins-runtime.md).

### Event bus (`domovoi/events.py`)

Fire-and-forget, with per-subscriber exception isolation — each delivery is
its own asyncio task, so a broken subscriber never blocks the emitter or its
peers. **No delivery guarantee, no cross-event ordering, no replay.** The
`core.*` event catalog (v1) is a closed, versioned set — emitting an unknown
`core.*` name raises; plugin events are the open namespace
`plugin.<slug>.<event>` (the SDK force-prefixes the slug).

The normative consequence: any state kept consistent *only* by a bus
subscription may go stale across a crash. Bus-driven cleanup must pair the
subscription (the fast path) with a **periodic reconciliation sweep** (the
correct path). The bus is latency; the sweep is truth.

### Capability registry (`domovoi/capabilities.py`)

Core never imports provider code. Providers register implementations under
well-known capability slugs; core call sites `resolve()` at use time and
**degrade gracefully when nothing is registered** — absence is a supported
state, never an error. The seams core consumes today:

* `streaming-search-provider` — the music handler's local-miss cascade and
  smart-skip; absent ⇒ "no streaming provider is installed" voice copy.
* `media-acquisition-fulfiller` — presence check for the acquisition queue's
  voice copy (the queue itself always accepts rows).
* `now-playing-matcher` — favorites attribution.

Determinism: an explicit `prefer()` pin wins; otherwise ascending registration
slug.

### Media acquisition queue (`domovoi/acquisitions.py`)

A durable Postgres-backed queue (`media_acquisitions`) for "get this media
into my library" requests. Four producers — voice handlers, the web
add-by-query/url endpoints, chat tools, and plugins — enqueue **structured**
requests (`{kind: query|url, text, metadata, …}`), never a provider wire
format. Registered fulfillers (media-provider plugins) claim rows with
`SELECT … FOR UPDATE SKIP LOCKED` and complete or fail them (retry with
backoff up to `acquisition_max_attempts`, or terminal
`failed`/`unfulfillable`).

Graceful absence: enqueue always succeeds; with no fulfiller enabled, rows sit
`pending` and the user hears *"I've noted that down, but no media provider is
installed to fetch it."* Installing a fulfiller later drains the backlog — a
feature, not a bug. Dedup is three-layered: library fuzzy match (pg_trgm) at
enqueue, live-queue identity via a partial-unique `dedup_key` index, and the
fulfiller's own post-resolve knowledge. Every state change fires
`pg_notify('acquisitions_changed', …)` in the caller's transaction and emits a
`core.acquisition_*` event. Full ER + flow in
[uml/media-and-library.md](uml/media-and-library.md).

### Now-playing registry (`domovoi/now_playing.py`)

Any feature that starts external playback in a room stamps
`(source slug, opaque data)`; the streaming layer, the playback-state sweeper,
the web now-playing card, and favorites all read and clear stamps
**generically** — core never learns a provider's vocabulary. Sources must be
explicitly registered (stamping with an unregistered source raises); one stamp
per room, replace semantics; storage is in-memory on the core process.
Per-source `register_matcher` hooks drive "favorite what's playing"
attribution, walked in ascending slug order.

### Spoken names (`domovoi/handlers/shared/spoken_names.py`, `library_match.py`)

Whisper writes what it hears ("suicide boys", "generation"); the library
stores how an artist styles a name (`$uicideboy$`, `GENER8ION`). A substring
search between the two misses, so "play X" first goes through a **spoken-name
resolver** that matches names by how they SOUND, and only then through the
MPD text search that was always there.

* **One normalization** (`spoken_names.py`, pure, safe in both processes):
  `spoken_forms` gives every way a name may be said (accents and lookalike
  letters folded with anyascii, `$` → s, `!` inside a word → i, `&`/`+` →
  "and", dotted initials joined, numbers as words both ways, a digit inside a
  word read as a letter, as its number word and as its sound — `n9ne` → nine,
  `gener8ion` → generation; leading zeros also said, "0417" → "zero four
  seventeen"; "Pt." also "part"). The readings are generated lazily, fewest
  departures from the primary first, and stop at eight — a name of sixty
  two-reading words costs what a short one does. `alias_key` — the primary
  form, space-less, "the" dropped — is the **frozen identity** of a name
  (`KEY_VERSION`): two aliases with one key are the same alias. Number words
  are hand-written (no LGPL dependency); Double Metaphone comes from the BSD
  `Metaphone` package. `speakable` leaves emoji and other symbols out of a
  readback (anyascii would say "💿" as ":cd:").
* **The index** (`spoken_index.py`, pure; `library_match.py` holds the one
  process-wide instance). Entities: an `artist` (every spelling merged by
  `alias_key`, all rows whose credit names it), a whole multi-artist `credit`,
  a `title` per primary artist, an `album` per folder, and a `track` (one row —
  only ever a song alias's target). Each is indexed under all its spoken forms
  (exact table + one rapidfuzz pool per type, de-duplicated), plus one
  "title artist" phrase per title for requests whose "by" Whisper dropped.
  Forms of three letters or fewer match exactly only (TI, U2). ~0.3 s to build
  for 5,232 tracks; queries p50 ≈ 10 ms / p95 ≈ 25 ms there (gate run, dev i9).
* **The scorer** is the 2026-10-02 audit's untuned ranker with fixed
  constants: exact spoken form = 1.0, else 0.6 × spelling + 0.4 × sound; an
  entity that leaves a content word of the request unexplained is never played
  outright; "X by Y" weighs title 0.6 / artist 0.4 and also scores the whole
  phrase ("stand by me" stays a title); one leading carrier ("songs by", "the
  album", "the song") narrows the kinds, and the unstripped request is scored
  too. Decision: ≥ `music_match_play_threshold` (0.90) plays; ≥
  `music_match_ask_threshold` (0.75) asks; else nothing. Exact ties rank
  household alias > library name > a credit FRAGMENT > MusicBrainz alias,
  then the bigger entity; two entities of one type tied at the top (the same
  title by two artists) are asked about. A fragment is a performer the
  library only ever names as one part of a ","/"&" credit ("Fire" of
  "Earth, Wind & Fire", "The Creator" of "Tyler, The Creator"): it plays on an
  exact match only when no real name says the same, and a near match to it
  is at most asked about. A one-word request plays on a near match only when
  it is also spelled close (ratio ≥ 0.9 — "wallows" never plays "Walls"),
  and a request of more than 24 words is no name at all. Single-function
  hooks for later phases: `_candidate_pool` (narrowing — linear in library
  size today: p95 ≈ 25 ms at 5k tracks, ≈ 100 ms at 20k), `_score` (a
  household-plays prior), `resolve`'s query (N-best).
* **Freshness**: every request reads a one-query fingerprint of
  `library_tracks` (count, max id, newest `added_at`/`enriched_at`) and
  `library_aliases` (count, max id, newest `updated_at`). Changed aliases are
  folded in on the spot (the next turn hears a new "also called"); a changed
  library is rebuilt in a worker thread in the background (2 s debounce) while
  the old index answers — inline when the library is ≤ 1,000 tracks. A change
  the fingerprint cannot see — the provider pipeline re-ingesting a track and
  renaming its row in place (`LibraryAPI.ingest_track`) — calls
  `library_match.invalidate(library=True)`. Boot hook `core.spoken_index`
  (after `core.library_index`) warms it. No V019 table → library names only,
  one warning. Any error → "none" and today's search.
* **The hook** is `MusicHandler._play`, the funnel every "play X" goes through
  (fast paths, tool router, chat mode, the dashboard's play box): a sure match
  plays **by exact file path** (`MPDClient.prepare_files`; rows outside
  `music_dir` fall back to `prepare_tracks`); a middle-band match goes to the
  did-you-mean dialog when it is there; anything else runs the old cascade (MPD
  tag search → filename → podcast → streaming provider → "couldn't find")
  — whose filename leg no longer counts a hit inside a bracketed video id
  (`[FQKdHGgKygo]` is no match for "kygo"). `music_match_enabled = False` is
  the old path. `prepare_files` appends the new songs and drops the old
  queue only once one was accepted, so a refused batch never loses what was
  playing.
* **Playing a match** (`MusicHandler.play_candidate`): an artist or credit
  queues ALL its tracks shuffled (≤ 500), an album its tracks in file order, a
  title or track the one song. A queue of two or more is what "next" and
  "previous" follow (`_skip_in_queue`), so they stay with the artist and the
  queue ends after its last song. The play is recorded with its
  `library_track_id`. The reply says a speakable name — the household's own
  words when the match was exact ("Playing Suicide Boys, shuffled."), else a
  household or MusicBrainz alias that sounds like the name, else the tag made
  sayable (`speakable`: "P!nk" → "Pink", "T.I." → "T I"). Never echoed: a
  household name that is also something else's library name (the reply
  says the target's own name, so a takeover can be heard).
* **Did you mean** (`handlers/music_choice.py`, kind `core.music_choice`,
  parked through the confirmation mechanism for 60 s): "Did you mean X, or
  Y?" accepts yes (also "uh, yes", "mm-hmm", "either one"), "the first /
  second / other one", plain no ("OK." — and the same words are not asked
  about again in that conversation for 5 minutes: they go on to today's
  search), "no, play Z" / "I meant Z", and a bare name
  (`library_match.match_reply` — a parked title or album is also named by
  its artist). After a "no", the same words never pick the candidate just
  turned down ("no, I said subtract" goes to today's search, not to
  SBTRKT). A bare reply that is neither a candidate nor a name the library
  knows ("good night") is not about the question: it drops it and routes
  normally, as does anything else. Only a whole "yes" ends the capture
  early; a bare "no" waits for the correction that often follows it. The
  mechanism is generic and core-only: a handler lists
  the confirmation kinds whose reply may be free text in `choice_kinds` and
  answers them in `handle_choice_reply`; `route()` checks a parked choice
  FIRST, then the yes/no pre-empt, then the fast paths (so "no, play X" is
  not read as a plain "no"). The router checks an in-memory set of sessions
  with a parked choice (`confirmations.CHOICE_PARKED`), so ordinary turns
  gain no DB read. Never asked from the dashboard's play box or chat mode
  (`Context.answerable`).
* **Household aliases** ("also called", V019 `library_aliases`): one row per
  name → target, so an artist, album or song has any number of names; a
  unique `alias_key` means each name means ONE thing household-wide (re-using
  it answers "replace it?"). Any paired device adds; removing or replacing
  someone else's needs an admin (household policy — device ids are
  self-asserted). Dashboard chips in the track drawer, REST under
  `/api/music/aliases`, and voice ("when I say X I mean Y", "forget that
  name", "what else is X called", band 295 — its "Do you mean …?" about a
  target is a choice too, so "no, I mean Y" works). A phrase whose subject
  is not in the library ("what else is the moon called", "when I say
  goodnight I mean turn off the lights") is DECLINED: the fast path returns
  None and `route()` goes on to the tool model and Q&A as if it had not
  matched — the router's general "a fast path / a tool call may decline"
  rule. The web writes plain rows; the core sees them through the
  fingerprint. Open reads name the device that added a name only to a
  caller on the device tier.
* **MusicBrainz aliases** (`workers/library_alias_fetch.py`): OFF by default
  (`music_alias_fetch_enabled`, switchable without a restart), online only,
  through the shared 1 request/s MusicBrainz client. A hit must name the
  library's spelling; a strict filter drops legal names, non-English and
  non-Latin forms, and for people any alias sharing a word with their legal
  name or not sounding like the stage name; it never takes a key a library
  name or a household alias holds. Only a 200 with a JSON body is a verdict:
  a 5xx, an HTML 200 or a 4xx is an error row (retried after 2^attempts
  hours, five times at most), never "no such artist"; one artist whose
  result cannot be saved gets an error row and the batch goes on.
* **The gate** (`scripts/eval_spoken_match.py`, test `spoken_gate`): the
  frozen held-out request set, parsed as production parses it, measures the
  resolver — and is never used to tune it. It reports every group over all
  transcripts and over the ones production routes to music (the ordinary
  floor is on the latter: a misheard "play" is the parser's loss, Phase 4),
  and in production mode with the dialog on and off.

### Lyrics (`domovoi/lyrics/`, `workers/lyrics_*.py`, `handlers/shared/lyric_search.py`)

Lyrics are shown in the players (line by line when they are timed) and are
a way to ask for a song ("play the song that goes …"). They are the
household's: served on the household tier only, never logged, never on an
open page, the realtime socket or the core snapshot (counts and states
only).

* **Storage (V021).** `track_lyrics` holds one row per library track, with
  three sources in the owner's order: the owner's own `.lrc` beside the song
  (same base name, any case; an unambiguous "Artist - Title.lrc" too), the
  lyrics inside the file (ID3 SYLT/USLT, Vorbis LYRICS/UNSYNCEDLYRICS, MP4
  `©lyr`, WM/Lyrics, APE; LRC-formatted text counts as timed), then LRCLIB.
  What is SHOWN is derived, never written: the owner's `.lrc` whatever it
  holds, else timed beats plain, and within a kind the file's tags beat
  LRCLIB (generated columns + the view `track_lyrics_shown`). Timed lyrics
  are `[[ms, "line"], …]` with the file's `[offset:]` applied.
  `track_lyric_lines` is lyric search's index. Each of the three workers
  writes only its own columns, so they never undo each other.
* **`lyrics_scan`** (always on, local disk only, every 5 minutes): lists each
  folder once per tick, re-reads a song only when its own or its `.lrc`'s
  mtime/size changed, in batches of 100 inside `sidecar.SIDECAR_LOCK`, at
  most 120 s a tick. It never writes a file.
* **`lyrics_fetch`** (LRCLIB, opt-in): its default follows the internet
  answer (`PROFILE_DEFAULTS`: on for always and sometimes, off for never,
  off while unanswered; a hand-set value wins), and it re-checks that, the
  answer itself, the connectivity probe and shutdown before every request
  (`egress` choke point, `clients/lrclib.py`: ~1 request/s, the Domovoi
  User-Agent, 2 MiB cap, no retries). It asks `/api/get` by title, artist,
  album and length, then `/api/search` accepted only within −2…+3 s and on
  matching names. Only an ANSWER stamps a row (a rate limit, an outage or
  the internet turned off leave it as it was); "not found" is asked again
  after 28 days, doubling to 112. Songs played in the last 30 days go first.
* **The `.lrc` writer** (`lyrics/sidecar.py`, run by `lyrics_fetch`):
  LRCLIB's TIMED lyrics are also saved as `<stem>.lrc` beside a song that
  has none (`lyrics_write_lrc`, only while LRCLIB is on), marked
  `[re:Domovoi]` and recorded by its sha256. A file is Domovoi's own only
  when the name AND the hash match what it wrote: every other `.lrc` is the
  household's, read as source 1 and never written; Domovoi's own file that
  the household edits or deletes is left alone for good. New files go
  through an exclusive temp file and a hard link (never clobbering); a
  refresh re-checks ownership right before `os.replace`. Nothing is ever
  deleted or renamed. The library indexer ignores `.lrc`.
* **`lyrics_index`** (always on, every 10 s) keeps `track_lyric_lines`
  current: every distinct shown line and every pair of consecutive lines,
  normalized by `spoken_names.lyric_words`, with how often it is sung.
* **Lyric search** (`library_match.resolve_lyrics` → `lyric_search.py`):
  candidate lines through the trigram GIN index (`<%`), then a word
  alignment that tolerates a word left out, added, swapped or misheard,
  weighted to the chorus (a line sung once counts 0.97). The same play
  (0.90) and ask (0.75) bands as names, the same did-you-mean dialog. Three
  modes: **explicit** ("play the song that goes …", the first fast paths of
  music, and the tool router's `lyrics` argument), **identify** ("what's the
  song that goes …": names it and offers to play it), and **fallback** — the
  last resort of `MusicHandler._play`, after the spoken-name resolver and
  MPD's tag and file-name searches found nothing and before the podcast /
  streaming cascade: it plays only an EXACT phrase of six words or more and
  otherwise at most asks. `lyrics_search_enabled` switches all three off.
* **The web** (`web/backend/api/lyrics.py`): `GET
  /api/music/library/{id}/lyrics`, `/api/music/now-playing/{room}/lyrics`
  (the room's MPD position with the lyrics, for room playback) and
  `/api/music/lyrics/status` (the Jobs card), all `require_device_read`
  and `no-store`; the `lyrics.changed` channel carries counts only. Players
  follow local playback on their own clock and a room on MPD's elapsed time
  re-anchored by the 2 s poll, with a per-room timing nudge.
* **The gate** (`scripts/eval_lyric_search.py`): a held-out request set
  built from the household's library and LRCLIB, kept outside the repo
  (it holds real lyrics), measured once and never used to tune anything.

---

## 7. Realtime: how a DB change reaches the dashboard

The web backend (`web/backend/realtime.py`) runs two cooperating tasks:

* a **poll loop** that snapshots Postgres + the core's admin snapshot every
  1.5 s (`WEB_POLL_INTERVAL_SEC`), diffs against the previous snapshot, and
  pushes change events to subscribed dashboard WebSockets;
* a **LISTEN task** holding one long-lived asyncpg connection that `LISTEN`s
  on every mapped NOTIFY channel and, on a notification, triggers the matching
  snapshot immediately — sub-second freshness for things like acquisition
  status flips.

The contract on the write side is **commit-coupled NOTIFY**: mutation sites
call `SELECT pg_notify('<channel>', '<reason>')` *in the same transaction* as
their UPDATE/INSERT. Postgres delivers NOTIFY only on COMMIT, so a rollback
never produces a phantom event.

Core channels map NOTIFY → realtime like `acquisitions_changed →
acquisitions`, `playlists_changed → playlists`, `library_changed →
library.indexer`, `wake_words_changed → wake_words`, `timers_changed →
timers` (a timer set, cancelled or fired), `plugins_changed` (the plugin
registry), and more. Enabled plugins add their own pairs via manifest
`[[realtime]]` entries — the web process wires them from the manifest JSONB in
the `plugins` table and **never imports plugin code**.

The poll loop stays authoritative: LISTEN is an accelerator, not a
replacement. A missed notification is caught by the next 1.5 s poll.

---

## 8. Configuration

Three layers, all visible in code:

1. **`Settings`** (`domovoi/config.py`) — a pydantic `BaseSettings` loaded
   from environment variables and `domovoi/.env` (pinned to an absolute path
   so it loads regardless of cwd). This is the single source of typed config
   for the core; the web process imports the same object for shared values.
2. **`FieldSpec` registry** (`domovoi/config_schema.py`) — the editable-config
   layer for the dashboard's settings UI. Each spec names a real `Settings`
   field (a test asserts they never drift) and adds a label, group, tooltip,
   bounds, a `section` (`common`, or `advanced` behind a warning fold), and a
   **tier** describing how a change applies:
   * `hot` — mutate the live singleton; consumers re-read per tick/call.
   * `reapply` — mutate + run registered reapply hooks (below).
   * `restart` — persisted to `.env` (via `domovoi/config_env_writer.py`) but
     read only at startup; the API reports it in `restart_required` and the UI
     badges it.
3. **Reapply hooks** (`domovoi/reapply.py`) — `tier="reapply"` fields run
   registered callbacks after the settings mutation (reset the TTS client,
   reset the Ollama client, re-set the log level) instead of the endpoint
   hardcoding which subsystem each field pokes. Hooks are deduped per write
   batch, and a hook failure never aborts the write. Plugin config fields go
   through a parallel per-slug registry
   (`domovoi/plugins_runtime/config_bridge.py`, the `ctx.on_reapply` path).

Satellites have their own TOML config (`satellite/config.toml.example`,
installed to `~/.domovoi/config.toml` on the Pi). The dashboard edits it
remotely: the server pushes a `set_config` frame; the Pi merges changes
preserving comments, writes a `.bak`, and self-restarts.

### The internet answer, and the egress choke point

`INTERNET_ACCESS` (`always` | `sometimes` | `never`, unset by default;
[INTERNET.md](INTERNET.md)) does two jobs.

* **It sets defaults.** `config.PROFILE_DEFAULTS` lists the settings that
  follow the answer (the news worker, MusicBrainz names, podcast
  downloads, the library enricher, the extra voices). A model validator
  fills each one only when nobody set it — not in the environment, not in
  `.env` — so a hand-set value always wins; Settings → Internet can hand a
  field back by commenting its `.env` line out. Unset fills nothing.
* **It is a gate.** `domovoi/egress.py` is the one place the answer is read
  (process environment, then `domovoi/.env`, cached on the file's mtime),
  in both processes, so the core and the web process never disagree. Under
  `never` every way out goes through it:
  * `net_safety.check_outbound_url` asks it first, so every caller of the
    outbound-URL check (feeds, streams, ICY, add-by-URL, a model registry)
    refuses a non-local host before any DNS lookup;
  * the SDK's and the web plugin host's HTTP clients carry its request
    hook, so plugins are covered without an edit;
  * the paths that never went through `net_safety` call it directly:
    `egress.require_destination(url)` for a URL,
    `egress.require_internet("what")` for egress without one (Edge speech,
    a SearXNG search, `git fetch`, a docker build, pip). The connectivity
    probe stops dialing (`reason: turned_off`), which closes every
    `ctx.online` path too.

  A refusal is `egress.InternetTurnedOff`, a subclass of
  `net_safety.UnsafeOutboundURL`, so existing `except` clauses refuse rather
  than crash; HTTP routes answer `egress.http_exception()` (`409`,
  `X-Domovoi-Refusal: internet-off`). The rule that comes with it: a
  refusal is never recorded as a permanent failure (no feed marked dead,
  no track "no match"). "Local" means no DNS and no guessing: an IP literal
  in a private, loopback or link-local range, `localhost`, a single-label
  name, or a `.local`/`.lan`/`.home.arpa`/`.internal` name. The SearXNG
  container follows the answer through the `internet_access` reapply hook
  (`domovoi/searxng_service.py`), never at boot.

---

## 9. Invariants

These are load-bearing. Tests enforce most of them; breaking any of them is a
bug even if nothing fails immediately.

* **Local-first.** Every handler declares
  `requires_network: "no" | "degraded" | "yes"`. Anything not `"no"` must
  implement `fallback_offline()` — enforced by
  `domovoi/tests/test_registry.py`. The router consults `ctx.online` (fed by
  the connectivity probe) and applies the offline gate described above. The
  house keeps working when the internet doesn't.
* **One way out.** Anything that can reach past the house network goes
  through `domovoi/egress.py` (directly, or via `net_safety`, the SDK's
  HTTP client or the probe), so `INTERNET_ACCESS=never` means none of it
  leaves. A new outbound path that skips it is a bug.
* **Intent logging is non-optional.** Every routed turn writes one
  `intents_log` row AND one `conversation_log` row, centrally in
  `router._persist_turn`. No routing path may skip it.
* **The DB is migration-only.** Core: Flyway, append-only, `V001` frozen.
  Plugins: own schema, own runner, checksummed, both DBs. Nobody mutates
  schema at runtime.
* **Tests must use the test DB.** `domovoi/tests/conftest.py` derives
  `<dbname>_test` from `DATABASE_URL` and refuses to run otherwise. It also
  forces `USE_STUBS=true`, making Whisper/Ollama/TTS deterministic fakes.
* **MPD is lazy-provisioned per room** by `domovoi/mpd_provisioner.py`; the
  `mpd_rooms` table is the source of truth for port/container assignments.
  Not in the compose file.
* **Handler ordering is correctness.** Priority bands are explicit; greedy
  catch-all patterns must sit in the last band (900+) or they silently poach
  other handlers' phrasings. Plugin bands are contract-checked at install.
* **DLL bootstrap order (Windows host).** `domovoi/bootstrap.py` registers
  NVIDIA DLL directories **before** anything imports
  `ctranslate2`/`faster-whisper`. Import order in `domovoi/main.py` is
  deliberate; the plugin loader asserts `bootstrap.dlls_registered` before
  importing any plugin module, so a reordering fails loudly at boot.
* **`NullPool` stays** in `domovoi/db/session.py`. One connection per session:
  sidesteps cross-event-loop reuse errors in tests (Windows
  ProactorEventLoop) and stale-socket pain after a Postgres restart. This is
  a low-QPS homelab service; the ~5 ms per-connection overhead is immaterial.
* **Single process, single loop.** Registries, worker state, and `app.state`
  caches are in-memory and not thread-safe by design. Durability belongs in
  Postgres (queue tables, commit-coupled NOTIFY), not in the bus or in RAM.
* **Commit-coupled NOTIFY.** `pg_notify` fires in the same transaction as the
  data change, never after or outside it.
* **Web process stays out of the plugin runtime.** It reads plugin state from
  the `plugins` table (manifest JSONB) and talks to core over HTTP. It never
  imports `domovoi.plugins_runtime` or plugin code.
