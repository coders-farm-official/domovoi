# Domovoi HTTP API Reference

Domovoi runs two FastAPI processes. This document covers both, endpoint by
endpoint, as implemented in `domovoi/main.py`, `domovoi/plugins_runtime/installer.py`,
and `web/backend/`. A machine-readable spec for the web process lives at
[`web/openapi.json`](../web/openapi.json) (regenerate with
`python -m web.scripts.dump_openapi`).

| Process | Base URL | Source |
|---|---|---|
| **Core voice service** | `http://<server>:6370` | `domovoi/main.py` (+ plugin routers) |
| **Web dashboard backend** | `http://<server>:6369` | `web/backend/main.py` (+ plugin routers) |

Related reading: [ARCHITECTURE.md](ARCHITECTURE.md) for how the two processes
relate, [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md) for the trust model behind
the auth tiers, [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) for writing
plugin routes, and [uml/satellite-protocol.md](uml/satellite-protocol.md) for
the satellite WebSocket frame contract.

---

## 1. Conventions

### 1.1 Auth tiers

Domovoi's v1 posture is LAN trust with a lightweight admin tier layered on
top. First boot writes an 8-word **setup code** to `~/.domovoi/setup-code.txt`
(and prints it on the core console); `POST /api/auth/setup` exchanges that
code + a chosen password for the admin credential (argon2id hash) and a
session token. Tokens are 256-bit bearer values — only their sha256 is stored
— with a 30-day sliding expiry. Both processes validate against the same
`admin_sessions` table, so a token minted by the dashboard works on the core
too. See `domovoi/admin_auth.py`.

Every endpoint below is labeled with one of these tiers:

| Tier | Meaning |
|---|---|
| **Open** | No auth. Daily-use surface, LAN trust. |
| **Admin (Bearer)** | `require_admin_mutation`: requires `Authorization: Bearer <token>`. The dashboard cookie is *never* enough for a mutation (CSRF stance). Before first-run setup completes, these endpoints allow requests (pre-setup grace) so a fresh install works. |
| **Admin read (Bearer or cookie)** | `require_admin_read`: a GET that carries secrets. Either a Bearer token or the `domovoi_admin` cookie (set at login, `HttpOnly`, `SameSite=Strict`) renders it. Same pre-setup grace. |
| **Admin, fail-closed** | `domovoi.auth.require_admin`, used only for plugin management (it is code execution). No pre-setup grace: returns **501** until admin setup completes. Bearer-only for mutations. |
| **Outbound-fetch** | `check_outbound_fetch`: the server will fetch a caller-chosen URL. Passes with an admin Bearer session, **or** when the URL matches an installed media-provider plugin's `url_matcher` allowlist *and* the caller is within a per-source rate limit (10 requests / 60 s). |

Failure codes across tiers: `401` missing/invalid/expired token, `403`
cookie-only mutation attempt (or a rejected outbound fetch), `429` login
backoff / rate limit (with a `Retry-After` header), `501` plugin management
before setup.

The web process proxies several calls to the core. Where the core applies the
gate, the web endpoint forwards `Authorization` and the real client address
(`X-Forwarded-For`) and returns the core's status + JSON verbatim.

### 1.2 Error shapes

* Standard errors are FastAPI-shaped: `{"detail": "<message>"}` with an
  appropriate 4xx/5xx status.
* Request-validation failures return `422` with FastAPI's structured
  `{"detail": [{loc, msg, type}, ...]}` list.
* Plugin install/lifecycle errors return `422` with a typed envelope:
  `{"detail": {"error": {"code": "<machine_code>", "message": "...", "details": {...}}}}`.
* Web endpoints that proxy the core pass its status and body through
  unchanged. Plugin-management proxies return `503` when the core process
  itself is unreachable.

### 1.3 Realtime WebSockets

| Socket | Process | Purpose |
|---|---|---|
| `WS /ws/state` | web :6369 | Dashboard state push. Client optionally sends `{"subscribe": ["music.now_playing", "satellites.presence", ...]}`; no frame (or an empty list) means all channels. Server pushes `{"type": "<channel>.changed", "data": <full new snapshot>}` events, driven by a 1.5 s poll loop accelerated by Postgres LISTEN/NOTIFY. Core channels: `music.now_playing`, `acquisitions`, `satellites.presence`, `satellites.wifi`, `people.last_seen`, `calendar.events`, `library.indexer`, `wake_words`. Enabled plugins add their own via manifest `[[realtime]]` entries. |
| `WS /v1/stream/{room_id}` | core :6370 | The satellite voice stream: bidirectional audio + control frames (hello, wake, audio chunks, transcripts, TTS, music start/stop handshake, drop-in, config/volume/voice status, wake-word recording). The full frame contract is documented in [uml/satellite-protocol.md](uml/satellite-protocol.md); the implementation is `domovoi/streaming.py`. One session per `room_id` — a second connect with the same id evicts the first for broadcasts. |
| `WS /v1/dropin/{room_id}` | core :6370 | Phone drop-in only: joins the intercom bridge as a call peer *without* registering as a satellite. Query param `phone_id` identifies the caller (auto-prefixed `phone-` so it can never collide with a room). See `domovoi/phone_dropin.py`. |

---

## 2. Core API (`:6370`)

### 2.1 Health and introspection

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/health` | Open | — | `{"status":"ok","bot_name","use_stubs"}`; `503` when the DB is unreachable or no handlers registered. Liveness probe. |
| `GET /v1/connectivity` | Open | — | `{online, last_checked_at, last_online_at, target}` — the internet-connectivity probe the offline-first router consults. |
| `GET /v1/handlers` | Open | — | List of `HandlerInfo`: `{name, requires_network, tool_schema, fast_path_count, priority_band, origin, display, example_phrases}`. `origin` is `"core"` or a plugin slug. Powers the dashboard's manual page. |
| `POST /v1/intent` | Open | `{transcript, room_id?, session_id?, synthesize?}` | Routes a text utterance through the full intent pipeline. Returns the `Response` JSON (`{text, session_id, matched_handler, matched_path, online, data, music_action, music_stream_url, ...}`); with `synthesize: true` returns `audio/wav` bytes instead, with the text and metadata in `X-Response-Text`, `X-Session-Id`, `X-Matched-Handler`, `X-Matched-Path`, `X-Online` headers. |

### 2.2 File-sync channels (pulled by satellites)

Three parallel manifest+file channels. A satellite hash-compares the manifest
against its local cache and downloads only changed files, verifying each
body's sha256 against the manifest.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/sounds/manifest` | Open | `?voice=<name>` (optional; defaults to the registry default voice) | `{relative_path: sha256}` for every rendered MP3 in that voice's subtree (greetings, `network_issues.mp3`, `sample.mp3`). |
| `GET /v1/sounds/{path}` | Open | `?voice=` optional | Serves one rendered clip (`audio/mpeg`). Locked to `.mp3` files strictly inside the voice subtree (no traversal). |
| `GET /v1/satellite-code/manifest` | Open | — | `{relative_path: sha256}` for allowlisted files under `satellite/` (`.py .toml .txt .md .service .sh .json`; never `__pycache__`, `.pyc`, `.bak`, `.env*`). Basis for in-field satellite upgrades. |
| `GET /v1/satellite-code/{path}` | Open | — | Serves one allowlisted satellite source file (`application/octet-stream`). Traversal-guarded. |
| `GET /v1/wake-models/manifest` | Open | — | `{relative_path: sha256}` for trained wake-word models (`.onnx` / `.onnx.json`) under the server's wake-models dir. |
| `GET /v1/wake-models/{path}` | Open | — | Serves one wake-model file. Extension-allowlisted and traversal-guarded. |

### 2.3 Capabilities, plugins, and observability

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/capabilities` | Open | — | `{"capabilities": {<slug>: [provider, ...]}}` — the live capability registry (what the dashboard "+ add" affordances and the Android app gate on). |
| `GET /v1/plugins` | Open | — | `{"plugins": [{slug, name, version, publisher, enabled, bundled, install_source, status, last_error, installed_at, updated_at}]}` — the plugin registry rows. |
| `GET /v1/plugins/{slug}/status` | Open | — | Per-plugin live status: registry row + handlers registered under the slug + live worker and startup-hook state (`workers`, `startup_hooks`) from the shared worker registry. `404` for an unknown slug. This is also where **worker observability** lives — there is no separate `/v1/admin/workers` endpoint. |

Plugin lifecycle (from `domovoi/plugins_runtime/installer.py`; all **Admin,
fail-closed** — `501` until first-run setup, Bearer-only after). Install and
upgrade are two-phase: stage → preview → confirm.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `POST /v1/plugins/install` | Admin, fail-closed | multipart zip in field `file`, **or** JSON `{"github_url": "..."}` | Phase A: stage + validate. Returns `{staged_id, preview}` (the preview lists permissions, migrations, open endpoints — the trust screen). |
| `POST /v1/plugins/install/{staged_id}/confirm` | Admin, fail-closed | — | Phase B: run pip + plugin migrations + hot-load. Also confirms staged *upgrades*. |
| `POST /v1/plugins/{slug}/enable` | Admin, fail-closed | — | Enable and hot-load a disabled plugin. |
| `POST /v1/plugins/{slug}/disable` | Admin, fail-closed | — | Disable: unload handlers/workers; the plugin's HTTP routes start returning `404`. |
| `POST /v1/plugins/{slug}/uninstall` | Admin, fail-closed | `{"data": "keep" \| "purge"}` (default `keep`) | Remove the plugin; `purge` also drops its Postgres schema. |
| `POST /v1/plugins/{slug}/upgrade` | Admin, fail-closed | zip or `{"github_url", "force"?}` | Stage an upgrade (returns `{staged_id, preview}`; confirm via the shared confirm endpoint). Dev-mode installs refuse upgrade. |

### 2.4 Acquisitions (generic media queue)

The acquisition queue exists even with no media-provider plugin installed —
rows wait `pending` and responses carry graceful-absence copy; installing a
fulfiller later drains the backlog.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/acquisitions` | Open | `?status=&limit=50` (max 200) | `{"acquisitions": [...], "fulfillers": [...], "can_fulfill_query", "can_fulfill_url"}` — rows from `media_acquisitions`, newest first. |
| `POST /v1/admin/music/add-by-query` | Open | `{room_id, query, artist?, attach_to_playlist_id?}` | Enqueue a free-text acquisition. Returns `{queued, outcome, message, already_in_library, already_downloading, acquisition_id, fulfiller_available, title}`. |
| `POST /v1/admin/music/add-by-url` | **Outbound-fetch** | `{room_id, url, title?, dedup_key?, attach_to_playlist_id?}` | Enqueue an acquisition for an exact external URL (skips fuzzy library dedup; honors `dedup_key`). Gated because it triggers provider code against a caller-chosen URL. |

### 2.5 Admin: live state and satellite actions

Used primarily by the web backend (a separate process that can't read the
core's `app.state`). Unlabeled endpoints here are **Open** — the v1 LAN-trust
posture; the specifically dangerous ones carry the Bearer gate.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/admin/snapshot` | Open | — | Process-state snapshot for the dashboard's poll loop: `{active_rooms, resumable_music, wifi_status, now_playing, current_playlist, active_dropins, satellite_full_duplex, satellite_sat_type, satellite_mic_enabled, satellite_display, satellite_voice, satellite_volume, satellite_synced_sha, domovoi_version}`. |
| `POST /v1/admin/announce` | Open | `{room_id?, message}` (1–500 chars) | Speak `message` on one satellite, or all when `room_id` is null. `{"announced_to": [rooms]}`; `503` if nothing is connected, `404` for an unknown room. |
| `POST /v1/admin/dropin/start` | Open | `{initiator_room, target_room}` | Open a two-way drop-in between two connected, AEC-capable rooms. `400` same room, `404` room offline, `409` disabled / no AEC / already in a call. |
| `POST /v1/admin/dropin/end` | Open | `{room_id}` | Hang up whatever call the room is in. `404` when not in a call. |
| `POST /v1/admin/satellite/restart` | Open | `{room_id}` | Ask a connected satellite to restart its own service. `503`/`404`/`502` as above. Writes an `intents_log` audit row. |
| `POST /v1/admin/satellite/set-volume` | Open | `{room_id, level}` (0–100) | Set the satellite's master hardware output volume (scales both TTS and music). |
| `POST /v1/admin/satellite/display` | Open | `{room_id, action}` (`on` \| `off` \| `restart_kiosk`) | Drive a **video** satellite's screen (panel power via its configured mechanism, or a kiosk-browser restart). `409` when the room isn't a video satellite; `503`/`404`/`502` as above. Writes an `intents_log` audit row. |
| `POST /v1/admin/satellite/upgrade` | **Admin (Bearer)** | `{room_id}` | Tell a satellite to mirror `/v1/satellite-code`, verify sha256s, self-restart, and roll back if it doesn't reconnect in time. Returns `{requested, room_id, expected_sha}`. |
| `POST /v1/admin/satellites/{room_id}/pairing/preseed` | **Admin (Bearer)** | `{sat_type?, room_label?, hardware?, board?, mac?, force?}` | USB adoption: mint the room's pairing token (sha256 stored; RAW token returned once, never logged) + upsert the inventory row. `409` already paired unless `force` (rotates). |
| `DELETE /v1/admin/satellites/{room_id}` | **Admin (Bearer)** | — | Remove a never-connected satellite (inventory + preseeded pairing). `409` when the room is provisioned (has an MPD instance). |
| `POST /v1/admin/satellites/{room_id}/label` | Open | `{room_label}` (null clears) | Set the satellite's display room label (grouping tag; cosmetic, daily-tier). |
| `GET /v1/admin/satellite/{room_id}/config` | Open | — | Editable satellite config: the schema joined with the values the Pi reported. `404` when the room isn't connected. |
| `POST /v1/admin/satellite/{room_id}/config` | Open | `{"changes": {field: value}}` | Validate and push config edits; the Pi rewrites its `config.toml` and restarts. Returns `{sent, rejected, restarting}`. |

### 2.6 Admin: version, config, chat, hardware

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/admin/version` | Open | — | `{"sha": "<short HEAD sha>[-dirty]"}` (`"unknown"` without git). |
| `POST /v1/admin/version/check` | Open | — | Fetch upstream and report behind/ahead counts. Read-only, best-effort. |
| `POST /v1/admin/version/pull` | Open | — | `git pull --ff-only`; a dirty/diverged tree returns `pulled: false` + stderr. Never restarts the process. |
| `GET /v1/admin/config` | **Admin read (Bearer or cookie)** | — | The editable-config registry joined with live values (`{fields, plugin_fields}`). Gated because plugin config can carry secrets (returned pre-masked). |
| `POST /v1/admin/config` | **Admin (Bearer)** | `{"changes": {...}, "plugin": "<slug>"?}` | Validate, persist to `.env` (or the plugin's `~/.domovoi/plugins/<slug>.env`), live-apply `hot`/`reapply` tiers, and report `{applied, restart_required, rejected}`. |
| `POST /v1/admin/chat-tool` | Open | `{tool, args}` | Execute a chat-mode tool call on behalf of the chat agent's sandboxed proxy tools. Degrades to an apology string rather than 500ing; returns `{"text": "..."}`. |
| `POST /v1/admin/chat/resync` | **Admin (Bearer)** | — | Rebuild the chat tool surface and re-attach it to every chat agent. The install/enable/disable pipeline runs this automatically; this is the manual trigger. |
| `GET /v1/admin/hardware` | Open | — | Host hardware snapshot for the Models page: `{gpus, cpu, ram, disk}`; each field degrades to empty/null independently. |

### 2.7 Admin: voices, sounds, wake words

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `POST /v1/admin/sounds/regenerate` | Open | — | Kick a background re-render of all voices' greeting/canned clips, then notify satellites to re-sync. Returns `{"started": true}` immediately. |
| `POST /v1/admin/voices/sample` | Open | `{"name": "<voice>"}` | Live-synthesize a sample line in a registered voice; returns `audio/wav` with the text in `X-Sample-Text`. `404` for an unknown voice. |
| `POST /v1/admin/wake/record/start` | Open | `{room_id, wake_word_id}` | Tell a connected satellite to record positive training clips (fresh take: clip dir + count reset). `404` room/word unknown, `409` mid drop-in or wrong status, `502` send failure. |
| `POST /v1/admin/wake/record/stop` | Open | `{room_id}` | Stop an in-progress recording; the Pi resumes its wake loop. |
| `POST /v1/admin/wake/push` | Open | `{room_id, wake_word_id}` | Push a trained model: the Pi writes the slug to its wake sidecar, syncs from `/v1/wake-models`, and restarts. `409` when not trained yet. |
| `POST /v1/admin/wake/score` | Open | `{wake_word_id}` | Offline-score recorded clips against the trained model (per-clip max score + silence baseline). `409` no model, `501` when openWakeWord isn't installed. |

### 2.8 Admin: music and library

Voice-equivalent calls (`play`, the transport actions) re-enter the regular
routing pipeline so they land in `intents_log`/`conversation_log` exactly like
a spoken turn. Direct-play endpoints bypass the router, write only an
`intents_log` row (transcript prefixed `[ui]`), and never fall through to an
external streaming provider. All are **Open**.

| Method & path | Request | Response / purpose |
|---|---|---|
| `POST /v1/admin/music/play` | `{room_id, query}` | Route `"play <query>"` through the full pipeline; dispatches the music-start frame to the room's Pi. Returns `{text, matched_handler, matched_path, music_action, online}`. |
| `POST /v1/admin/music/play-track` | `{room_id, track_id}` | Play one `library_tracks` row directly via MPD (tag → filename → basename lookup). `404` unknown/unfindable track, `502` MPD error. |
| `POST /v1/admin/music/play-tracks` | `{room_id, track_ids: [..]}` (≤500) | Load an ordered queue of library tracks into the room's MPD and start playback (the browser player's "cast to room"). |
| `POST /v1/admin/music/play-playlist` | `{room_id, playlist_id, shuffle?}` | Start a playlist (`playlist_id` 0 = the virtual Favorites). Ordered mode resumes from the saved position; stamps in-room playlist state so "next" stays in-playlist. |
| `POST /v1/admin/music/{action}/{room_id}` | — | Transport controls; `action` ∈ `pause`, `resume`, `stop`, `skip`, `next`, `previous` (routed as the spoken equivalents). `400` for anything else. |
| `POST /v1/admin/library/reindex` | — | Background: sweep the music dir into `library_tracks`, then make every per-room MPD rescan. Returns `{"queued": true}` immediately. |
| `POST /v1/admin/library/enrich` | — | Background: metadata enrichment pass (rate-limited; can take minutes). `{"queued": true}`. |

---

## 3. Web API (`:6369`)

All routes are `/api/...` (plus `/plugins/{slug}/static/*` for plugin assets
and `WS /ws/state`). The frontend itself is served statically from `/`.
Unlabeled endpoints are **Open** (LAN trust). CORS allows localhost, RFC 1918
ranges, and `*.local` origins only.

### 3.1 Auth

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/auth/status` | Open | — | `{setup_complete, authenticated}` — the dashboard's setup-vs-login probe. `authenticated` reflects cookie or Bearer. |
| `POST /api/auth/setup` | Open (requires setup code) | `{setup_code, password}` (password ≥10 chars) | First-run claim of the admin tier. `403` wrong/missing code, `409` already set up. Returns `{ok, token}` and sets the session cookie. Deletes the code file. |
| `POST /api/auth/login` | Open (backoff-throttled) | `{password, label?}` | Verify the password (per-source exponential backoff; `429` + `Retry-After` while throttled, `401` wrong password), mint a token, set the cookie. Returns `{ok, token}`. |
| `POST /api/auth/logout` | Bearer only | — | Revoke the *calling* session and clear the cookie. `401` without a Bearer (a cross-site POST with just the cookie can't log you out). |
| `GET /api/auth/sessions` | Bearer or cookie | — | `{"sessions": [{token_hash, label, created_at, expires_at, last_used_at, current}]}` for the revoke UI. |
| `DELETE /api/auth/sessions/{token_hash}` | Bearer only | — | Revoke a session by hash. `404` unknown hash. |
| `POST /api/auth/password` | Bearer only | `{old_password, new_password}` | Change the admin password (old one re-verified). |

### 3.2 Plugins (management proxies + host)

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/plugins/manifest` | Open | — | Everything the frontend shell needs to render plugin pages without a rebuild (pages, nav entries, realtime channels, load errors). Served by the plugin host router, ahead of the `/{slug}` matchers. |
| `GET /plugins/{slug}/static/{path}` | Open | — | A plugin's `web/static` assets. Containment-checked; `404` when the plugin is disabled. |
| `GET /api/plugins` | Open | — | Installed plugins with the fields the admin list renders: manifest metadata, permissions, capabilities, handlers, pages, `web_load_error`. |
| `GET /api/plugins/{slug}/purge-preview` | Open | — | What uninstall-with-purge would drop: `{schema: "plugin_<slug>", tables: [{table, rows}]}`. |
| `POST /api/plugins/install` | Admin, fail-closed (core gates) | zip upload or `{"github_url"}` | Proxy to core `POST /v1/plugins/install`, auth forwarded, response verbatim. `503` when the core is down. |
| `POST /api/plugins/install/{staged_id}/confirm` | Admin, fail-closed (core) | — | Proxy of the confirm phase. |
| `POST /api/plugins/{slug}/enable` | Admin, fail-closed (core) | — | Proxy. |
| `POST /api/plugins/{slug}/disable` | Admin, fail-closed (core) | — | Proxy. |
| `POST /api/plugins/{slug}/uninstall` | Admin, fail-closed (core) | `{"data": "keep"\|"purge"}` | Proxy. |
| `POST /api/plugins/{slug}/upgrade` | Admin, fail-closed (core) | zip or `{"github_url", "force"?}` | Proxy. |

### 3.3 Capabilities

| Method & path | Auth | Response / purpose |
|---|---|---|
| `GET /api/capabilities` | Open | The live capability map (proxied view of the core registry) the sidebar's "+ add" affordances gate on. |
| `GET /api/capabilities/manual` | Open | Handler metadata + example phrases for the "What can I say?" manual page (from core `GET /v1/handlers`). |

### 3.4 Acquisitions

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/acquisitions` | Open | `?status=&limit=100` | The generic acquisition queue for the Downloads-style page (reads the DB directly; fulfiller availability included). |

### 3.5 Music: library, player, transport

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/music/library` | Open | `?q=&source=&favorited=&sort=added_desc&limit=50&offset=0` | Paged library listing. |
| `GET /api/music/library/stats` | Open | — | Library totals for the Stats card. |
| `GET /api/music/library/{track_id}` | Open | — | One track. |
| `PATCH /api/music/library/{track_id}` | Open | `TrackPatch` (title/artist/favorited/...) | Edit track metadata. |
| `DELETE /api/music/library/{track_id}` | Open | `?also_file=false` | Remove a track row (optionally the file too). `204`. |
| `GET /api/music/library/{track_id}/playlists` | Open | — | Playlists containing this track. |
| `POST /api/music/library/upload` | Open | multipart audio file(s) | Upload straight into the library; triggers indexing. |
| `GET /api/music/library/{track_id}/audio` | Open | `?download=` | Stream the file to the browser player (range requests). `?download=1` serves it as an attachment (save to device) named from the on-disk basename. |
| `GET /api/music/library/{track_id}/cover` | Open | — | Cover art. |
| `DELETE /api/music/acquisitions/{acq_id}` | Open | — | Cancel a pending acquisition. `204`. |
| `GET /api/music/now-playing` | Open | — | Per-room now-playing (from the cached core snapshot + MPD), with source attribution. |
| `POST /api/music/now-playing/{room_id}/favorite` | Open | — | Heart whatever the room is playing (re-searches by title into the library/queue). |
| `POST /api/music/play` | Open | `{room_id, query}` | Proxy → core `/v1/admin/music/play` (full voice pipeline). |
| `POST /api/music/play-track` | Open | `{room_id, track_id}` | Proxy → core direct-play (no conversation log, no external fallback). |
| `POST /api/music/play-tracks` | Open | `{room_id, track_ids}` | Proxy → core queue cast. |
| `POST /api/music/play-playlist` | Open | `{room_id, playlist_id, shuffle?}` | Proxy → core playlist start. |
| `POST /api/music/add-by-query` | Open | `{room_id, query, artist?, attach_to_playlist_id?}` | Proxy → core acquisition enqueue. |
| `POST /api/music/add-by-url` | **Outbound-fetch (core decides)** | `{room_id, url, title?, dedup_key?, attach_to_playlist_id?}` | Proxy with credentials + source address forwarded; the core's verdict passes back verbatim. |
| `POST /api/music/{pause\|resume\|stop\|skip}/{room_id}` | Open | — | Transport proxies → core `/v1/admin/music/{action}/{room_id}`. |
| `POST /api/music/library/reindex` | Open | — | Proxy → core background reindex. |
| `POST /api/music/library/enrich` | Open | — | Proxy → core background enrichment. |

### 3.6 People

All **Open**. Person-centric views over the voice-profile / memory tables.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/people` | — | Everyone Domovoi has voice-identified. |
| `GET /api/people/{person_id}` | — | One person. |
| `DELETE /api/people/{person_id}` | — | Forget a person (profiles, memories, links). |
| `GET /api/people/{person_id}/sessions` | `?limit=20` | Recent conversation sessions. |
| `GET /api/people/{person_id}/conversations` | `?limit=50` | Recent conversation turns. |
| `GET /api/people/{person_id}/notes` | — | Notes mentioning them. |
| `GET /api/people/{person_id}/profiles` | — | Their voice profiles (embeddings metadata). |
| `DELETE /api/people/{person_id}/profiles/{profile_id}` | — | Drop one voice profile. |
| `GET /api/people/{person_id}/memories` | `?status=` | Extracted memories. |
| `POST /api/people/{person_id}/memories` | `MemoryCreate` | Add a memory manually. |
| `PATCH /api/people/{person_id}/memories/{memory_id}` | `MemoryPatch` | Edit/confirm/reject a memory. |
| `DELETE /api/people/{person_id}/memories/{memory_id}` | — | Delete a memory. |
| `GET /api/people/{person_id}/favorites` | `?kind=` | Favorites (songs, stations, …). |
| `POST /api/people/{person_id}/favorites` | `FavoriteCreate` | Add a favorite. |
| `DELETE /api/people/{person_id}/favorites/{favorite_id}` | — | Remove a favorite. |
| `GET /api/people/{person_id}/preferences` | — | Per-person preferences. |
| `PATCH /api/people/{person_id}/preferences` | `PreferencesPatch` | Update preferences. |

Related: `GET /api/denylist` and `DELETE /api/denylist/{entry_id}` (Open) —
the voice-identification denylist.

### 3.7 Satellites

All **Open** except `upgrade`. Live state comes from the cached core snapshot;
actions proxy to the core admin endpoints.

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/satellites` | Open | — | All known rooms with presence, wifi, volume, active voice, synced code SHA, full-duplex capability. |
| `GET /api/satellites/{room_id}` | Open | — | One room. |
| `GET /api/satellites/{room_id}/sessions` | Open | `?limit=20` | Recent sessions in this room. |
| `GET /api/satellites/{room_id}/conversations` | Open | `?limit=50` | Recent turns in this room. |
| `GET /api/satellites/{room_id}/notes` | Open | — | Notes taken in this room. |
| `GET /api/satellites/{room_id}/recently-played` | Open | `?limit=100` | Play history for the room. |
| `GET /api/satellites/{room_id}/timers` | Open | — | Active timers/reminders. |
| `DELETE /api/satellites/{room_id}/timers/{timer_id}` | Open | — | Cancel a timer. |
| `POST /api/satellites/{room_id}/announce` | Open | `{message}` | Proxy → core announce (one room). |
| `POST /api/satellites/announce-all` | Open | `{message}` | Proxy → core announce (broadcast). |
| `POST /api/satellites/{room_id}/dropin/start` | Open | `{target_room}` | Proxy → core drop-in start. |
| `POST /api/satellites/{room_id}/dropin/end` | Open | — | Proxy → core drop-in end. |
| `GET /api/satellites/{room_id}/dropin/phone-info` | Open | — | What a phone client needs to join this room's drop-in (`/v1/dropin/...` URL + capability info). |
| `GET /v1/satellite-plugins/manifest` (core) | Open | — | `{files: {"<slug>/<rel>": sha256}, meta: {slug: {...}}}` — enabled plugins' `[satellite]` payloads; satellites mirror it like the code channel. |
| `GET /v1/satellite-plugins/{path}` (core) | Open | — | One payload file by its `<slug>/<rel>` channel path. |
| `GET /api/satellites/media/status` | Open | — | Media-prep card data: boards, cache state, docker availability, per-plugin payload summary. |
| `GET /api/satellites/media/targets` | Open | — | Removable drives that look like a flashed Pi boot partition. |
| `POST /api/satellites/media/prepare` | **Admin (Bearer)** | `{board, mic_profile, target: {kind: drive\|zip, token?}, offline?}` | Start (or attach to) a media build; progress rides the `satellites.media` realtime channel. |
| `GET /api/satellites/media/jobs` | Open | `?limit` | Recent build jobs (no server paths; `has_artifact` flags downloadables). |
| `POST /api/satellites/media/jobs/{id}/cancel` | **Admin (Bearer)** | — | Mark a build cancelled (best-effort). |
| `GET /api/satellites/media/jobs/{id}/download` | Open | — | The overlay zip for a `kind=zip` build. |
| `POST /api/satellites/media/cache/refresh` | **Admin (Bearer)** | — | Refresh the wheel/deb/model caches (slow on a cold cache). |
| `GET /api/satellites/pending` | Open | — | Unprovisioned satellites presenting a USB adoption volume on the server (empty when adoption is off). |
| `POST /api/satellites/pending/{pending_id}/adopt` | **Admin (Bearer)** | `{room_id, room_label?, wifi_ssid, wifi_psk, wifi_country?, wifi_hidden?, device_profile?, initial_volume?, force?}` | Adopt: preseed pairing on the core and write the provision file to the device. `409` room exists / device re-nonced, `410` device unplugged. |
| `DELETE /api/satellites/{room_id}` | **Admin (Bearer)** | — | Proxy → core delete (remove a `waiting` room). |
| `PATCH /api/satellites/{room_id}` | Open | `{room_label}` | Proxy → core room-label update. |
| `POST /api/satellites/{room_id}/volume` | Open | `{level}` | Proxy → core set-volume. |
| `POST /api/satellites/{room_id}/display` | Open | `{action}` (`on` \| `off` \| `restart_kiosk`) | Proxy → core satellite display (video satellites only; `409` otherwise). |
| `POST /api/satellites/{room_id}/restart` | Open | — | Proxy → core satellite restart. |
| `POST /api/satellites/{room_id}/upgrade` | **Admin (Bearer)** | — | Proxy → core satellite code sync + self-restart; the core applies the same gate (credentials forwarded). |
| `GET /api/satellites/{room_id}/config` | Open | — | Proxy → core per-satellite editable config. |
| `PATCH /api/satellites/{room_id}/config` | Open | `{"changes": {...}}` | Proxy → core config push (Pi rewrites `config.toml`, restarts). |

### 3.8 Calendar

All **Open**.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/calendar/events` | `?start=&end=&limit=500` | Events in a window (ISO datetimes). |
| `POST /api/calendar/events` | `CalendarEventCreate` | Create an event. |
| `GET /api/calendar/events/{event_id}` | — | One event. |
| `PATCH /api/calendar/events/{event_id}` | `CalendarEventPatch` | Edit an event. |
| `DELETE /api/calendar/events/{event_id}` | — | Delete an event. |

### 3.9 Config and version

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/config` | Open | — | Static UI bootstrap: `{bot_name, tts_voice, rooms, web_version, wake_word_min_clips}`. |
| `GET /api/config/editable` | **Admin read (Bearer or cookie)** | — | Proxy → core `GET /v1/admin/config` (live values; plugin secrets pre-masked). |
| `PATCH /api/config/editable` | **Admin (Bearer)** | `{"changes": {...}}` | Proxy → core `POST /v1/admin/config`. Returns `{applied, restart_required, rejected}`. |
| `GET /api/config/version` | Open | — | Proxy → core version label. |
| `POST /api/config/version/check` | Open | — | Proxy → core upstream check. |
| `POST /api/config/version/pull` | Open | — | Proxy → core `git pull --ff-only`. |

### 3.10 Playlists

All **Open**.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/playlists` | — | All playlists (plus the virtual Favorites). |
| `POST /api/playlists` | `PlaylistCreate` | Create a playlist. |
| `PATCH /api/playlists/{playlist_id}` | `PlaylistPatch` | Rename / edit. |
| `DELETE /api/playlists/{playlist_id}` | — | Delete. |
| `GET /api/playlists/{playlist_id}/tracks` | — | Ordered tracks. |
| `POST /api/playlists/{playlist_id}/tracks` | `PlaylistTrackAdd` | Add a library track. |
| `PATCH /api/playlists/{playlist_id}/order` | `PlaylistReorder` | Reorder. |
| `DELETE /api/playlists/{playlist_id}/tracks/{track_id}` | — | Remove a track. |

### 3.11 Voices and greetings

All **Open**. Mutations trigger the core's background clip re-render
(`/v1/admin/sounds/regenerate`) so satellites pick up new audio.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/greetings` | — | The wake-greeting bank (`{name}` placeholder resolves to the bot name). |
| `POST /api/greetings` | `GreetingCreate` | Add a greeting line. |
| `PATCH /api/greetings/{greeting_id}` | `GreetingPatch` | Edit / enable / disable. |
| `DELETE /api/greetings/{greeting_id}` | — | Delete. |
| `GET /api/voices` | — | The voice registry (engine, model ref, default flag). |
| `GET /api/voices/{voice_id}/sample` | — | WAV sample — proxies the core's live TTS (`/v1/admin/voices/sample`); the web process has no TTS of its own. |
| `POST /api/voices/edge` | `EdgeVoiceCreate` | Register a cloud (edge) voice. `201`. |
| `POST /api/voices/piper` | multipart (`.onnx` + config) | Upload a local piper voice. `201`. |
| `PATCH /api/voices/{voice_id}` | `VoicePatch` | Rename / set default. |
| `DELETE /api/voices/{voice_id}` | — | Remove a voice. `204`. |

### 3.12 Wake words

All **Open**. Recording, scoring, and pushing proxy to the core (which owns
the satellite sessions, openWakeWord, and the model files); training is
picked up by the core's background trainer. The default wake word is
`hey_jarvis`; this surface is how you train a custom one (e.g. "Hey Domovoi").

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/wake-words` | — | Registry: slug, status (`recording`/`training`/`ready`/`failed`), clip counts, threshold. |
| `POST /api/wake-words` | `WakeWordCreate` | Create a wake word (starts in `recording`). `201`. |
| `POST /api/wake-words/{id}/record/start` | `{room_id}` | Proxy → core: satellite starts capturing positive clips. |
| `POST /api/wake-words/{id}/record/stop` | `{room_id}` | Proxy → core: stop capturing. |
| `POST /api/wake-words/{id}/train` | — | Queue training (the background trainer picks it up). |
| `GET /api/wake-words/{id}/clips` | — | Recorded clips with quality analysis + selection state. |
| `GET /api/wake-words/{id}/clips/{name}/audio` | `?variant=raw\|trimmed` | Listen to one clip. |
| `PATCH /api/wake-words/{id}/clips/{name}` | `{selected}` | Include/exclude one clip from training. |
| `POST /api/wake-words/{id}/clips/selection` | `ClipSelectionBody` | Bulk select/deselect. |
| `POST /api/wake-words/{id}/clips/reanalyze` | — | Re-run clip quality analysis. |
| `DELETE /api/wake-words/{id}/clips/{name}` | — | Delete a clip. `204`. |
| `POST /api/wake-words/{id}/score` | — | Proxy → core offline scoring (`501` when openWakeWord isn't installed). |
| `POST /api/wake-words/{id}/push` | `{room_id}` | Proxy → core: deploy the trained model to a satellite. |
| `PATCH /api/wake-words/{id}` | `WakeWordPatch` | Edit threshold / metadata. |
| `DELETE /api/wake-words/{id}` | — | Delete the wake word (+ artifacts). `204`. |

### 3.13 Files (multi-library browser)

All **Admin** — GET routes take `require_admin_read` (Bearer **or** dashboard
cookie), POST routes take `require_admin_mutation` (Bearer only). This is the
generic surface behind the **Files** tab: one router browses/downloads/uploads/
deletes/imports across every root the dashboard exposes — the core media dirs
(music / audiobooks / podcasts / documents), enabled-plugin `[[media_libraries]]`
roots, and present removable drives. It is **additive** and does not touch
`/api/documents/*` (§3.14), which the Files page still calls for the Documents
library's in-place editing.

The client only ever sends a `library_id` + a **relative** `path`; the absolute
`root_path` of each library is resolved and validated server-side and never
serialized. Containment rejects `..`, drive-absolute (`C:/…`), UNC (`//host/…`)
and symlink escapes; secret-shaped names under `~/.domovoi` are filtered from
every listing/serve/copy.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/files/libraries` | — | The library registry: `{ "libraries": [ … ] }`, ordered core, plugin, removable. Each record carries `id, label, kind (core\|plugin\|removable), icon, kind_icon, owner, editable, importable, doc_editing, reindex_kind, present` — `root_path` is stripped. |
| `GET /api/files/browse` | `?library_id=&path=` | One directory level (dirs-first, then name). Returns `{ library_id, path, editable, importable, doc_editing, breadcrumb:[…], entries:[…] }`; each entry is `{ name, rel, is_dir, size, mtime, kind (folder\|audio\|doc-office\|doc-text\|image\|pdf\|other), locked_by }` (`locked_by` non-null only for `core:documents`). `400` traversal · `404` missing dir / unknown library · `410` ejected removable. |
| `GET /api/files/download` | `?library_id=&path=` | Serve a file as an attachment (audio via Range/`206`) or a directory as a streamed zip (`{name}.zip`, 5000-member cap). `404` missing · `413` cap · `400` traversal. |
| `POST /api/files/upload` | multipart: `library_id`, `path`, `files[]` | Upload into the browsed directory. `200 {saved, skipped, reindex_triggered}`. `403` non-editable · `404` bad dest · `400` none saved. Each name is sanitized to a bare basename, deduped, and re-containment-checked before write. |
| `POST /api/files/delete` | `{ library_id, paths:[…], recursive:false }` | Delete files; folders need `recursive:true` (bounded, symlink-confined). Refuses to delete a library root. `200 {deleted, failed, reindex_triggered}`. `403` non-editable. For `core:documents`, releases any editor lock on a deleted path. |
| `POST /api/files/import` | `{ source_library_id, source_path, target_library_id, target_path }` | Copy a file/dir from a **removable** source into an **importable** library (server-side, member+byte capped). `200 {copied, skipped, reindex_triggered}`. `409` source not removable / target not importable · `410` ejected source · `404` missing. |

After a successful write to an indexed library (`reindex_kind == "music"`) the
web process proxies the core `POST /v1/admin/library/reindex` with the caller's
credentials forwarded; `audiobooks` runs the in-process indexer; `podcasts` /
`documents` / removable are no-ops.

### 3.14 Documents (homegrown editors)

All **Open**. The former OnlyOffice/Collabora sidecars — and with them the
open/close locks, JWT capability tokens, save callbacks, and WOPI routes —
are retired. Editing is homegrown/in-page: a markdown doc editor
(`/text` + `/export/doc`), a spreadsheet grid (`/sheet` + `/export/sheet`,
.xlsx/.csv round-trip via openpyxl), and Excalidraw for drawings. Every
row's `category` tells the UI how to open it
(`doc | sheet | drawing | newtab | download | text`); legacy office formats
(.docx/.doc/.odt/.rtf/.xls/.ods) list and download but don't edit in-app.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/documents` | `?kind=all` | List documents with `category` routing (also `/api/documents/`). |
| `POST /api/documents/create` | `CreateRequest` | Create a blank file (`doc` → .md, `sheet` → .xlsx, `drawing` → .excalidraw, `text` → verbatim name). |
| `POST /api/documents/upload` | multipart | Upload documents. |
| `POST /api/documents/delete` | `DeleteRequest` | Delete documents. |
| `POST /api/documents/download-zip` | `ZipRequest` | Zip + download a selection. |
| `GET /api/documents/text/{rel_path}` | — | Read a text/markdown file (415 for binary/too-large). |
| `PUT /api/documents/text/{rel_path}` | `TextWriteRequest` | Write a text/markdown file. |
| `GET /api/documents/sheet/{rel_path}` | — | The sheet grid model (`rows[[{v,f}]]`); 415 for non-.xlsx/.csv. |
| `PUT /api/documents/sheet/{rel_path}` | `SheetWriteRequest` | Write the grid back (.xlsx keeps formulas as formulas). |
| `GET /api/documents/export/doc/{rel_path}` | `?fmt=docx` | Export markdown/text as .docx (python-docx). |
| `GET /api/documents/export/sheet/{rel_path}` | `?fmt=csv\|xlsx` | Export a sheet as .csv or .xlsx. |
| `GET /api/documents/raw/{rel_path}` | — | Raw file bytes (inline). |
| `POST /api/documents/drawings/read` | `DrawingReadRequest` | Read a drawing document. |
| `POST /api/documents/drawings/write` | `DrawingWriteRequest` | Save a drawing. |

### 3.15 Podcasts and audiobooks

All **Open**. Feeds are polled by core background workers; audio is served by
this process.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/podcasts/subscriptions` | — | Subscribed feeds. |
| `POST /api/podcasts/subscriptions` | `SubscribeRequest` | Subscribe to a feed URL. |
| `DELETE /api/podcasts/subscriptions/{sub_id}` | — | Unsubscribe. |
| `GET /api/podcasts/subscriptions/{sub_id}/episodes` | — | Episodes for one subscription. Each row includes `has_file` and `file_ext` (e.g. `".mp3"`, or `null` when not downloaded) instead of the private server path. |
| `GET /api/podcasts/discover` | `?q=` (required) | Search a podcast directory. |
| `POST /api/podcasts/poll` | — | Poll feeds now (instead of waiting for the worker). |
| `GET /api/podcasts/episodes/{episode_id}/audio` | `?download=` | Stream a downloaded episode. `?download=1` serves it as an attachment named from the episode title plus its on-disk extension. |
| `GET /api/podcasts/positions/{episode_id}` | `?device_id=&person_id=` | Resume position. |
| `POST /api/podcasts/positions/{episode_id}` | `PositionSave` | Save position. |
| `GET /api/audiobooks` | — | Indexed books (also `/api/audiobooks/`). Each row includes `is_folder` and `file_ext` (single-file books; `null` for folder books, which download as a zip) instead of the private server path. |
| `GET /api/audiobooks/{book_id}` | — | One book (files/chapters), with `is_folder` / `file_ext` as above. |
| `POST /api/audiobooks/reindex` | — | Re-walk the audiobooks dir. |
| `GET /api/audiobooks/{book_id}/audio` | `?file=&download=` | Stream a book file (`?file=` selects a chapter for folder books). `?download=1` serves it as an attachment — a single chapter for folder books, the whole file for single-file books. |
| `GET /api/audiobooks/{book_id}/download` | — | Save a whole book to the device: single-file books come back as the file itself (attachment); folder books are zipped (one `<title>/<chapter>` entry per chapter) into a temp file removed after the response is sent. |
| `GET /api/audiobooks/{book_id}/position` | `?device_id=&person_id=` | Resume position. |
| `POST /api/audiobooks/{book_id}/position` | `PositionSave` | Save position. |

### 3.16 Videos

Videos are discovered live from the same media-library registry the Files
tab uses — any video file (`.mp4` `.m4v` `.mov` `.webm` `.mkv`) inside any
core / plugin / removable library appears, keyed by `(library_id, rel_path)`.
Nothing is indexed into the DB except resume positions (`video_positions`,
per device × person, like the podcasts store). File-content endpoints are
**Admin-read** (the dashboard cookie is enough for `<video>`/`<img>` tags);
the position store is Open like the podcast one. Position saves fire the
`video_positions.changed` WS event.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/videos/list` | — | Every video across every present library (bounded walk; hidden dirs skipped). |
| `GET /api/videos/stream` | `?library_id=&path=&download=` | Range/206 playback with the container's native MIME. `.mkv` serves as `x-matroska` — Chromium-family browsers and ExoPlayer play it; others fall back to `?download=1` (attachment). |
| `GET /api/videos/poster` | `?library_id=&path=` | Cached ffmpeg-extracted poster frame (`video_posters_dir`); `204` when no frame could be extracted. |
| `GET /api/videos/position` | `?library_id=&path=&device_id=&person_id=` | Resume position (`{position_sec, duration_sec}`). |
| `POST /api/videos/position` | `PositionSave` | Upsert a resume row (also carries `duration_sec` + `title` for the recent strip). |
| `DELETE /api/videos/position` | `PositionClear` | Drop one resume row ("remove from recently played"). |
| `GET /api/videos/recent` | `?device_id=&person_id=&limit=` | Newest resume rows for a device, existence-checked against the current registry (rows for ejected drives / deleted files are skipped, not deleted). |

### 3.17 Images (library-image serving)

Two generic endpoints over the media-library registry, keyed by
`(library_id, rel_path)` with the same containment as the Files surface
(**Admin-read**; the dashboard cookie is enough for `<img>` tags). The
Files tab's per-row **Open** action for images uses `/raw`; `/thumb`
backs image tiles anywhere the dashboard needs one.

Image *generation* is not a core feature — it ships as the separately
installed **Image Generation plugin** (Coders Farm,
`domovoi-plugin-imagegen`), which manages a local ComfyUI engine
(in-dashboard install + supervised process), curated model downloads,
and its own Images page/history under `/api/plugins/imagegen/…`. The
plugin declares the `imagegen` capability, which gates the Android
Images screen.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/images/thumb` | `?library_id=&path=&size=s\|m\|l\|xl` | Pillow-resized WebP thumbnail from the size-bucketed cache; `204` for undecodable files. |
| `GET /api/images/raw` | `?library_id=&path=` | The original, inline (the Files tab's Open target). |

### 3.18 Chat

Claude-desktop-style threaded text chat answered by the local Ollama
(`clients/ollama.chat_stream`). Independent of the voice pipeline's chat
mode (Letta, per-room sessions); `chat_threads.letta_agent_id` is the
parked bridge for backing a thread with a stateful agent later. A message
carrying image uploads is answered by `ollama_vision_model` (the Vision
role slot on the Models page) instead of the Q&A model. Mutations fire the
`chat.changed` WS event.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/chat/threads` | `?archived=` | Thread list, newest first, with message counts + last snippet. |
| `POST /api/chat/threads` | `ThreadCreate` | New thread (first user message titles it). |
| `PATCH /api/chat/threads/{id}` | `ThreadPatch` | Rename / archive. |
| `DELETE /api/chat/threads/{id}` | — | Delete thread + messages; unreferenced upload files are removed. |
| `GET /api/chat/threads/{id}/messages` | — | Full transcript. |
| `POST /api/chat/threads/{id}/messages` | `SendBody` | Persist the user turn and stream the reply as **SSE** (`delta` events per chunk, one final `done` with the persisted row, `error` on model failure). |
| `POST /api/chat/uploads` | multipart `file` | Stage an image (20 MB cap, image types only) → `{token, name}`. |
| `GET /api/chat/uploads/{token}` | — | Serve a chat image inline. |
| `GET /api/chat/models` | — | Installed Ollama models + configured default/vision models for the composer. |

### 3.19 Models (LLM management)

All **Open**. Talks to the local Ollama instance; hardware facts proxy to the
core (which owns the CUDA context). Domovoi runs **two** models — the
conversational one and the tool-routing one — switchable independently.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/models/catalog` | — | The curated model catalog with size/VRAM hints. |
| `GET /api/models/installed` | — | Models present in Ollama. |
| `GET /api/models/active` | — | Which models are active for each role. |
| `POST /api/models/active` | `SetActiveBody` | Switch the active model for a role. |
| `GET /api/models/hardware` | — | Proxy → core `GET /v1/admin/hardware` (GPU/CPU/RAM/disk fit badges). |
| `DELETE /api/models/{name}` | — | Remove an installed model. |
| `GET /api/models/jobs` | — | Running/finished pull jobs with progress. |
| `POST /api/models/pull` | `PullBody` | Start downloading a model (background job). |
| `POST /api/models/pull/{job_id}/cancel` | — | Cancel a pull. |

### 3.20 News

All **Open**. Fetching runs in a core background worker.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/news/categories` | — | Available topic categories. |
| `GET /api/news/people/{person_id}/topics` | — | A person's followed topics. |
| `POST /api/news/people/{person_id}/topics` | `NewsTopicCreate` | Follow a topic. |
| `DELETE /api/news/topics/{topic_id}` | — | Unfollow. |
| `GET /api/news/topics/{topic_id}/feeds` | — | Feeds attached to a topic. |
| `POST /api/news/topics/{topic_id}/feeds` | `NewsFeedCreate` | Attach a feed. |
| `DELETE /api/news/topics/{topic_id}/feeds/{feed_id}` | — | Detach a feed. |
| `POST /api/news/feeds/{feed_id}/validate` | — | Fetch-test a feed. |
| `GET /api/news/people/{person_id}/items` | `?limit=50` | Fetched items for a person. |
| `POST /api/news/items/{item_id}/favorite` | `NewsItemFavorite` | Star an item. |
| `GET /api/news/people/{person_id}/briefing` | — | The assembled spoken-style briefing. |
| `POST /api/news/poll` | `?person_id=` | Fetch now. |

### 3.21 Health

| Method & path | Auth | Response / purpose |
|---|---|---|
| `GET /api/health` | Open | `{status: "ok"\|"degraded", db_reachable, domovoi_reachable}`. Returns `200` even when degraded so the UI can render a partial-degradation banner. |

---

## 4. Plugin-mounted routes

Plugins ship their own HTTP surfaces, mounted by each process at a
slug-namespaced prefix. See [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md)
for how to declare them.

### 4.1 Conventions

**Core process — `/v1/plugins/<slug>/...`** (`domovoi/plugin_http.py`):

* **Enable gate**: a disabled plugin's routes return `404` (FastAPI can't
  remove routes, so a per-slug flag guards them; the router is reused on
  re-enable).
* **Auth gate, default-deny for mutations**: every non-GET route requires an
  admin session unless the plugin author explicitly opted out with the
  `@open_endpoint` decorator (each opt-out is listed on the install preview's
  trust screen). GETs are open unless the plugin adds its own
  `Depends(admin_required)`.
* Pre-setup grace applies: until the admin credential exists, the gate
  allows everything (LAN-trust, matching the core's daily-use surfaces).
* Reserved path: the core itself serves `GET /v1/plugins/{slug}/status`
  (section 2.3), so a plugin cannot mount its own `/status`.

**Web process — `/api/plugins/<slug>/...`** (`web/backend/plugin_host.py`):

* Routers registered via the plugin's web entry point (`register_web(ctx)`,
  `ctx.add_router(...)`) mount behind a slug-enable gate (`404` when
  disabled).
* Static assets serve from `GET /plugins/<slug>/static/<path>` (containment-
  checked). Pages, nav entries, and realtime channels are announced through
  `GET /api/plugins/manifest` so the frontend needs no rebuild.
* Plugin web code runs under an import guard: it can never pull core runtime
  modules into the web process.

### 4.2 Live example: the bundled radio plugin

`plugins/radio` (publisher Coders Farm) mounts both surfaces and is the
reference implementation.

Core router → mounted at `/v1/plugins/radio` (mutations admin-gated by
default — none opt out):

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /v1/plugins/radio/fcc-import` | Admin (default mutation gate) | Start the FCC FM bulk import as a background job (`?state=` optional); returns immediately. |
| `GET /v1/plugins/radio/fcc-import` | Open | Import job progress. |
| `POST /v1/plugins/radio/stations/{station_id}/resolve-simulcast` | Admin (default mutation gate) | Find an online simulcast stream for an FM station. |
| `GET /v1/plugins/radio/state` | Open | Live tuner state: `{sdr_available, sdr_frequency_mhz, fcc_import}`. (Named `/state` because the core reserves `/status`.) |

Web router → mounted at `/api/plugins/radio` (slug-enable gate; all open —
the dashboard's daily-use radio pages):

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/plugins/radio/search` | `?q=&country_code=&tag=&language=&limit=30&offset=0` | Station-directory search; hits already saved locally carry their row id + favorited flag. |
| `GET /api/plugins/radio/stations` | `?favorited_only=&source=online\|fm&q=&frequency_mhz=&limit=200&offset=0` | Saved stations. |
| `GET /api/plugins/radio/stations/{station_id}` | — | One station. |
| `POST /api/plugins/radio/stations` | station body | Save a station. `201`. |
| `PATCH /api/plugins/radio/stations/{station_id}` | patch body | Edit / favorite. |
| `DELETE /api/plugins/radio/stations/{station_id}` | — | Delete. `204`. |
| `POST /api/plugins/radio/stations/{station_id}/resolve-simulcast` | — | Simulcast resolution from the dashboard. |
| `POST /api/plugins/radio/fcc-import` / `GET .../fcc-import` | — | Start / poll the FCC import. |
| `GET /api/plugins/radio/detections` | filters | Passive song-detection history. |
| `GET /api/plugins/radio/badge` | — | `{favorites: N}` — the sidebar badge count. |
| `GET /api/plugins/radio/stations/{station_id}/stream` | — | Proxy a station's audio through the web backend for the browser player (dodges CORS/mixed-content). FM/SDR stations return `409` — those play only through a satellite room. |

---

*Generated against the v1.0.0 tree; when this document and the code disagree,
the code wins — and please open an issue. See also
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) for what common error responses mean
in practice.*
