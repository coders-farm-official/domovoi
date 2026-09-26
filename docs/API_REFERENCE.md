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

Domovoi's v1 posture is LAN trust with two credentials layered on top: a
per-household **device token** for ordinary actions and an **admin
password** for destructive / code-adjacent ones. First boot writes an 8-word
**setup code** to `~/.domovoi/setup-code.txt` (mode 0600, valid for 24 h —
a restart after that prints a fresh one) and prints it on the core console;
`POST /api/auth/setup` exchanges that code + a chosen password for the admin
credential (argon2id hash) and a session token. Tokens are 256-bit bearer
values — only their sha256 is stored — with a 30-day sliding expiry under a
90-day absolute cap; changing the password revokes every other session. Both
processes validate against the same `admin_sessions` table, so a token
minted by the dashboard works on the core too. See `domovoi/admin_auth.py`.

The **device token** is one secret per install, minted at the first boot of
either process into the `household_device_tokens` table and mirrored to
`~/.domovoi/device-token.txt` (mode 0600, next to the setup code). Clients
present it as the `X-Device-Token` header; an admin Bearer always passes the
same gate. Admins read it from `GET /v1/admin/device-token` /
`GET /api/auth/device-token`, replace it with one of their own on the bare
`POST` and rotate it from the `/rotate` siblings. It is rotated
automatically when first-run setup completes, so a token read during the
pre-setup window does not outlive it — read the file *after* claiming admin.

**Format.** Treat the value as **opaque**. A *generated* token is eight
words from a 256-word bank, hyphen-joined and lowercase —
`acorn-maple-river-thistle-harbor-quartz-willow-ember`, 64 bits, the same
strength and the same bank as the setup code. An admin may replace it with
anything they like, so a parser must not assume that shape: the rule the
server enforces is **printable ASCII (0x20–0x7E), 12 to 128 characters**,
stored verbatim with the outer whitespace trimmed. `Maple Street, 1984!` is
a valid household token. Do not match `^[a-z-]+$`, do not assume a fixed
length, and do not lowercase it.

Non-ASCII is refused at the point it is set, and clients should refuse it
too: HTTP header values are decoded as latin-1, so a UTF-8 token arrives
mojibake'd and silently never matches, and OkHttp throws on the request
builder rather than returning an error.

**Matching.** Two forms are accepted. The *exact* stored value always (after
trimming what the client sent), and the *canonical* form — trimmed,
lowercased, every run of whitespace / underscores / hyphens collapsed to one
hyphen — but only when the stored token is itself canonical. A generated
phrase is, so typing one back with spaces or capitals still pairs; a
`MyT0ken!!` an admin chose is not, so it is matched exactly and its capitals
carry their entropy. Installs created before 2026-09-23 hold a 64-hex token,
which is canonical and keeps validating unchanged.

**Wrong tokens are throttled.** Presenting a wrong `X-Device-Token` (in the
header, the query or the WebSocket subprotocol) is charged to a per-source
exponential backoff after five free attempts: 1 s doubling to a 5-minute
cap. While a source is throttled the gates answer **429** with
`Retry-After` — including for a *correct* token — and the value presented
is not looked at. A correct token clears the counter, and a request that
also carries a live admin Bearer is never charged.

Clients carry it without being asked twice. The dashboard stores the token
per server in `localStorage` and attaches the header to every call; a refusal
whose detail names the header opens a "pair this browser" prompt and the
refused request is replayed once the phrase is entered (the browser
canonicalises it first, so the value it stores and offers as a WebSocket
subprotocol is always the hyphenated form), while an admin login
fetches `GET /api/auth/device-token` and pairs the browser silently. The
Android app keeps it in `EncryptedSharedPreferences` and sends it on every
request, on the `/ws/state` socket and on the drop-in call socket. Browser
WebSockets cannot set headers, so the dashboard's first `/ws/state` frame
carries `{"subscribe": [...], "device_token": "..."}` instead; the Android
socket uses the header.

Every endpoint below is labeled with one of these tiers:

| Tier | Meaning |
|---|---|
| **Open** | No auth. Daily-use surface, LAN trust. |
| **Device (`X-Device-Token` or Bearer)** | `require_device`: a valid `X-Device-Token` header **or** an admin Bearer. `401` with neither or with a stale token; `403` with only the dashboard cookie. Keeps the pre-setup grace so a fresh install works. This is the tier for ordinary household actions on BOTH hops: a turn, an announcement, playback and the room queue, and on the dashboard also the calendar, playlists, chat, news, podcasts, audiobooks, a person's memories and favorites, device registration, and the satellite verbs the core puts on the same tier (label, timers, announce, volume). |
| **Chat callback** | `require_chat_callback`: the per-boot secret the chat agent's generated proxy tools carry in `X-Chat-Callback`. One endpoint (`POST /v1/admin/chat-tool`) wears it, because Letta's sandbox holds no admin session. A core restart mints a new secret, so the tools must be regenerated (`POST /v1/admin/chat/resync`). |
| **Admin (Bearer)** | `require_admin_mutation`: requires `Authorization: Bearer <token>`. The dashboard cookie is *never* enough for a mutation (CSRF stance). Before first-run setup completes, these endpoints allow requests (pre-setup grace) so a fresh install works. |
| **Admin read (Bearer or cookie)** | `require_admin_read`: a GET that carries secrets. Either a Bearer token or the `domovoi_admin` cookie (set at login, `HttpOnly`, `SameSite=Strict`) renders it. Same pre-setup grace. |
| **Admin, security tier** | `require_admin_security` (mutations) / `require_admin_security_read` (the device-token read): Bearer-only for mutations, cookie may render the read. **No pre-setup grace — 501 until admin setup completes**, and `--reset-admin` closes them again. Config write, service restart, satellite code push, pairing preseed / reset, satellite delete, device-token rotation. |
| **Admin, fail-closed** | `domovoi.auth.require_admin`, plugin management (it is code execution). Same posture as the security tier: **501** until admin setup completes, Bearer-only for mutations. |
| **Outbound-fetch** | `check_outbound_fetch`: the server will fetch a caller-chosen URL. Passes with an admin Bearer session, **or** when the URL matches an installed media-provider plugin's `url_matcher` allowlist *and* the caller is within a per-source rate limit (10 requests / 60 s). |

Whatever the tier, a URL the **server** then fetches also has to pass
`domovoi.net_safety.check_outbound_url`: `http`/`https` only, every hostname
resolved, and refused when any address it resolves to is loopback,
link-local, RFC 1918, CGNAT, an IPv6 ULA, multicast or unspecified —
including the shorthand spellings of those (`127.1`, `0x7f000001`, `[::1]`)
and IPv6 forms that wrap an IPv4. Redirects are followed one hop at a time
(five at most) and each target is checked before it is opened, and each
fetcher caps how many bytes it will read. Endpoints that merely *store* a
URL (subscribe to a feed, favorite a station) apply the same rules but skip
the "must resolve right now" part, so an offline household can still save
one; the fetch re-checks. A refused URL is `400` where a caller typed it and
`409` where it came off a stored row. The only way past the address rules is
`OUTBOUND_ALLOW_HOSTS` — server configuration, empty by default, naming
specific `host` / `host:port` endpoints matched on the host **as written in
the URL** and exactly (see SECURITY_PRIVACY.md §"Where the server will go").
It is not an editable setting, so no request can add to it.

Failure codes across tiers: `401` missing/invalid/expired token (Bearer,
device or chat callback), `403` cookie-only mutation attempt (or a
rejected outbound fetch),
`403` a write that arrived without `X-Requested-With` (see 1.2),
`413` a request body whose declared `Content-Length` is over its route's
budget (one that arrives without a declared length is cut off instead, and
the route reports the interrupted read),
`429` login backoff / rate limit (with a `Retry-After` header), `501` a
security-tier or plugin-management endpoint before setup.

The web process proxies several calls to the core. Where the core applies the
gate, the web endpoint forwards `Authorization`, `X-Device-Token` and the real
client address (`X-Forwarded-For`) and returns the core's status + JSON
verbatim. `domovoi/tests/test_route_auth_matrix.py` walks every mutating
route of both apps and fails when one lacks a gate and is not allowlisted
with a reason.

### 1.2 `X-Requested-With` on every write

Every `POST` / `PUT` / `PATCH` / `DELETE` under `/api/` must carry an
`X-Requested-With` header. Any non-empty value does; the dashboard sends
`XMLHttpRequest` and the Android app sends `DomovoiApp`. Without it the
request is refused **403** by
`web.backend.middleware.RequireRequestedWithMiddleware`, in front of the
router — no endpoint runs, no body is read, nothing is written.

The reason is the shape of the request rather than who sent it: a form
post, a multipart upload and a body-less POST are "simple requests", which
a browser sends to another origin without asking this server first. A
header outside that set makes the browser preflight instead, and a
preflight is something the server can refuse. `GET` and `HEAD` are
untouched, and so is the CORS `OPTIONS` preflight itself.

This is a backstop, not a gate: it answers "could a page on another origin
have caused this", not "may this caller do it". The auth tiers above are
what decide the second question.

Any non-browser client — a script, a harness, `curl` — has to send the
header too:

```bash
curl -X POST http://domovoi.local:6369/api/podcasts/poll \
     -H 'X-Requested-With: curl'
```

Plugin routes mounted under `/api/plugins/<slug>/...` are covered by the
same rule; a plugin page that uses the dashboard's `apiPost` / `apiPatch` /
`apiDelete` helpers inherits the header, and one that builds its own
`fetch` must add it.

### 1.3 Response headers

Every response carries `Content-Security-Policy`, `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. The
CSP allows inline and `eval`'d script (the dashboard compiles JSX in the
browser) but forbids framing, objects and `<base>`; see
`docs/SECURITY_PRIVACY.md`.

Credentialed cross-origin requests are allowed only from a LAN host on
**this process's own port** — the server switcher, and nothing else.

### 1.4 Error shapes

* Standard errors are FastAPI-shaped: `{"detail": "<message>"}` with an
  appropriate 4xx/5xx status.
* Request-validation failures return `422` with FastAPI's structured
  `{"detail": [{loc, msg, type}, ...]}` list.
* Plugin install/lifecycle errors return `422` with a typed envelope:
  `{"detail": {"error": {"code": "<machine_code>", "message": "...", "details": {...}}}}`.
* Web endpoints that proxy the core pass its status and body through
  unchanged. Plugin-management proxies return `503` when the core process
  itself is unreachable.

### 1.5 Realtime WebSockets

**Request bodies.** Both processes refuse a body over 1 MiB
(`MAX_REQUEST_BYTES`) with `413` before reading it — a declared
`Content-Length` is rejected outright, an undeclared one is cut off as it
streams. The upload routes (music library zip, Piper voice, satellite
media, documents, files, chat uploads, plugin install) carry their own much
larger ceilings. `POST /v1/intent` additionally bounds `transcript`
(4096 chars) and `room_id` (120), and a config push is bounded by key
count (500) — all `422`. Both processes run uvicorn with
`--limit-concurrency` (`MAX_CONCURRENT_CONNECTIONS`, default 128).

**Host and Origin.** Both processes answer only to a `Host` that names this
box on the LAN — a private/loopback IP, a single-label name, anything under
`.local` / `.lan` / `.home.arpa` / `.internal`, or a name listed in
`TRUSTED_HOSTS`. Anything else gets `400` before routing (the DNS-rebinding
guard). WebSocket upgrades are judged on `Origin` instead, against the same
LAN pattern the dashboard's CORS policy uses; an absent `Origin` passes,
which is how satellites and every non-browser client connect.

| Socket | Process | Purpose |
|---|---|---|
| `WS /ws/state` | web :6369 | Dashboard state push. **The handshake needs a household credential** — an `X-Device-Token` header, an admin `Bearer`, the dashboard's session cookie, or (for a browser, which can set no header here) the `domovoi.device-token-b64.<base64url(token)>` subprotocol, which the server echoes back **exactly as offered** or the browser drops the socket. The token is base64url-encoded (padding stripped) because RFC 6455 requires each subprotocol element to be an RFC 9110 *token*, which a chosen household token need not be; the legacy `domovoi.device-token.<token>` spelling is still accepted for clients on a cached bundle. Every offered element is scanned for the b64 prefix before any is read as the legacy form. Without one the upgrade is answered **403** and nothing is ever pushed to that socket. The pre-setup grace applies, so a fresh install's dashboard connects. Client optionally sends `{"subscribe": ["music.now_playing", "satellites.presence", ...], "device_token": "..."}` (the `device_token` field is accepted and ignored — the credential is settled in the handshake); no frame (or an empty list) means all channels. Server pushes `{"type": "<channel>.changed", "data": <full new snapshot>}` events, driven by a 1.5 s poll loop accelerated by Postgres LISTEN/NOTIFY. Core channels: `music.now_playing`, `acquisitions`, `satellites.presence`, `satellites.wifi`, `people.last_seen`, `calendar.events`, `library.indexer`, `wake_words`. Enabled plugins add their own via manifest `[[realtime]]` entries. |
| `WS /v1/stream/{room_id}` | core :6370 | The satellite voice stream: bidirectional audio + control frames (hello, wake, audio chunks, transcripts, TTS, music start/stop handshake, drop-in, config/volume/voice status, wake-word recording). The full frame contract is documented in [uml/satellite-protocol.md](uml/satellite-protocol.md); the implementation is `domovoi/streaming.py`. The client's first frame must be `hello`; the server provisions nothing, lists nothing, and sends no `ready` until that `hello` has passed the pairing check (a socket that stays silent for `SATELLITE_HELLO_TIMEOUT_SEC`, default 5 s, or sends any other frame first is closed with nothing created). One session per `room_id` — a second connect with the same id evicts the first for broadcasts (and closes the socket it replaced), but only once its own `hello` is accepted. `Origin`, when present, must be a LAN origin; satellites send none, which passes. |
| `WS /v1/dropin/{room_id}` | core :6370 | Phone drop-in only: joins the intercom bridge as a call peer *without* registering as a satellite. Query param `phone_id` identifies the caller (auto-prefixed `phone-` so it can never collide with a room). **Device tier, checked on the upgrade:** send the household token as the `X-Device-Token` header, or as `?token=` when the client is a browser and cannot set headers, or an admin `Authorization: Bearer`. Anything else gets `{"type":"error","code":"unauthorized"}` and a `1008` close before any session exists, so the target room is never disturbed and `active_dropins` gains no entry. With `DROPIN_ACCEPT_MODE=ring` the call does not open on connect: the server sends `{"type":"dropin_ringing"}`, rings the room, and only bridges when someone there says yes (`dropin_end` with `reason: "no_answer"` after `DROPIN_RING_TIMEOUT_SEC`). `Origin`, when present, must be a LAN origin. See `domovoi/phone_dropin.py`. |

---

## 2. Core API (`:6370`)

### 2.1 Health and introspection

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/health` | Open | `challenge` (optional) | `{"status":"ok","bot_name","use_stubs","stt","identity"}`; `503` when the DB is unreachable or no handlers registered. Liveness probe. `stt` is the speech-recognition state — `ok`, `fallback` (the configured Whisper didn't load and `whisper_cpu_fallback_model` is transcribing on cpu/int8), `unavailable` (nothing loaded; the core runs without STT and a spoken turn gets a "can't understand speech" reply), `stub` or `not_loaded`. It never makes health fail; the details are on `GET /v1/admin/hardware`. `identity` is `{algorithm:"ed25519", fingerprint:"SHA256:…", public_key}` — this install's server identity. Pass `?challenge=<nonce>` (≤128 chars, `400` beyond) and the block also carries that `challenge` back plus a `signature` over it, which is how a satellite tells this household's core from anything else listening on 6370. The pre-identity fields are unchanged. |
| `GET /v1/time` | Open | — | `{tz, epoch, iso, utc_offset_sec}` — this host's IANA zone name (`null` if the host cannot name it) and its clock. Satellites copy both on every connect, and stage 2 of a prepared card calls it before anything else, through the root helper `domovoi-sync-time`; a Pi has no battery clock and Pi OS boots in Europe/London. The helper takes the address from its root-owned pin (`/etc/domovoi/server.url`) rather than from the unprivileged caller, and where a fingerprint is pinned it makes the server sign a nonce first. |
| `GET /v1/connectivity` | Open | — | `{online, last_checked_at, last_online_at, target}` — the internet-connectivity probe the offline-first router consults. |
| `GET /v1/handlers` | Open | — | List of `HandlerInfo`: `{name, requires_network, tool_schema, fast_path_count, priority_band, origin, display, example_phrases}`. `origin` is `"core"` or a plugin slug. Powers the dashboard's manual page. |
| `POST /v1/intent` | **Device (`X-Device-Token` or Bearer)** | `{transcript, room_id?, session_id?, synthesize?}` | Routes a text utterance through the full intent pipeline. Returns the `Response` JSON (`{text, session_id, matched_handler, matched_path, online, data, music_action, music_stream_url, ...}`); with `synthesize: true` returns `audio/wav` bytes instead, with the text and metadata in `X-Response-Text`, `X-Session-Id`, `X-Matched-Handler`, `X-Matched-Path`, `X-Online` headers. |

### 2.2 File-sync channels (pulled by satellites)

Three parallel manifest+file channels. A satellite hash-compares the manifest
against its local cache and downloads only changed files, verifying each
body's sha256 against the manifest.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/sounds/manifest` | Open | `?voice=<name>` (optional; defaults to the registry default voice) | `{relative_path: sha256}` for every rendered MP3 in that voice's subtree (greetings, `network_issues.mp3`, `sample.mp3`). |
| `GET /v1/sounds/{path}` | Open | `?voice=` optional | Serves one rendered clip (`audio/mpeg`). Locked to `.mp3` files strictly inside the voice subtree (no traversal). |
| `GET /v1/satellite-code/manifest` | Open | — | `{relative_path: sha256}` for allowlisted files under `satellite/` (`.py .toml .txt .md .service .sh .json`; never `__pycache__`, `.pyc`, `.bak`, `.env*`). Basis for in-field satellite upgrades. |
| `GET /v1/satellite-code/manifest.sig` | Open | — | The same list inside a signed envelope: `{algorithm, fingerprint, public_key, channel:"satellite-code", manifest, signature}`. A satellite prepared with this server's fingerprint asks for this instead and installs nothing if the signature does not verify. The manifest travels INSIDE the envelope so the list that was signed and the list that is used are the same object. The unsigned `manifest` above keeps serving devices prepared before server identities. |
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
| `POST /v1/plugins/install` | Admin, fail-closed | multipart zip in field `file`, **or** JSON `{"github_url": "..."}` | Phase A: stage + validate. Returns `{staged_id, preview}` — the trust screen: `{slug, name, version, publisher, license, description, permissions, requirements: {direct, transitive: [{name, version, hashed, origin, origin_ok}]}, handlers, migration_count, capabilities, open_endpoints: [{method, path, module, function, process}], device_endpoints: [same shape], satellite: {apt_packages, post_install, pip_requirements, files_count, payload_mb} \| null, trust_statement}`. `422` with `detail.error.code` on rejection: `endpoint_tier_conflict` (a route decorated both `@open_endpoint` and `@device_endpoint`), `lockfile_option` (a global pip option in the lockfile), `lockfile_requirement` (a line that is not an exact `name==version` pin — direct URLs, local paths, ranges), plus the zip / manifest / layout / migration-lint codes. |
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
| `POST /v1/admin/music/add-by-query` | **Device (`X-Device-Token` or Bearer)** | `{room_id, query, artist?, attach_to_playlist_id?}` | Enqueue a free-text acquisition. Returns `{queued, outcome, message, already_in_library, already_downloading, acquisition_id, fulfiller_available, title}`. |
| `POST /v1/admin/music/add-by-url` | **Outbound-fetch** | `{room_id, url, title?, dedup_key?, attach_to_playlist_id?}` | Enqueue an acquisition for an exact external URL (skips fuzzy library dedup; honors `dedup_key`). Gated because it triggers provider code against a caller-chosen URL. |

### 2.5 Admin: live state and satellite actions

Used primarily by the web backend (a separate process that can't read the
core's `app.state`). Unlabeled endpoints here are **Open** — the v1 LAN-trust
posture; the specifically dangerous ones carry the Bearer gate.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/admin/snapshot` | Open | — | Process-state snapshot for the dashboard's poll loop: `{active_rooms, resumable_music, wifi_status, now_playing, current_playlist, active_dropins, satellite_full_duplex, satellite_sat_type, satellite_mic_enabled, satellite_display, satellite_voice, satellite_volume, satellite_synced_sha, domovoi_version}`. |
| `POST /v1/admin/announce` | **Device (`X-Device-Token` or Bearer)** | `{room_id?, message}` (1–500 chars) | Speak `message` on one satellite, or all when `room_id` is null. `{"announced_to": [rooms]}`; `503` if nothing is connected, `404` for an unknown room. |
| `POST /v1/admin/dropin/start` | **Admin (Bearer)** | `{initiator_room, target_room}` | Open a two-way drop-in between two connected, AEC-capable rooms — a live microphone bridge nobody in either room was asked about, so it takes an admin Bearer. `400` same room, `404` room offline, `409` disabled / no AEC / already in a call. Under `DROPIN_ACCEPT_MODE=ring` it returns `{"status": "ringing"}` and opens nothing until the target room answers. |
| `POST /v1/admin/dropin/end` | **Device (`X-Device-Token` or Bearer)** | `{room_id}` | Hang up whatever call the room is in. `404` when not in a call. |
| `POST /v1/admin/satellite/restart` | **Admin (Bearer)** | `{room_id}` | Ask a connected satellite to restart its own service. `503`/`404`/`502` as above. Writes an `intents_log` audit row. |
| `POST /v1/admin/satellite/set-volume` | **Device (`X-Device-Token` or Bearer)** | `{room_id, level}` (0–100) | Set the satellite's master hardware output volume (scales both TTS and music). |
| `POST /v1/admin/satellite/display` | **Admin (Bearer)** | `{room_id, action}` (`on` \| `off` \| `restart_kiosk`) | Drive a **video** satellite's screen (panel power via its configured mechanism, or a kiosk-browser restart). `409` when the room isn't a video satellite; `503`/`404`/`502` as above. Writes an `intents_log` audit row. |
| `POST /v1/admin/satellite/upgrade` | **Admin, security tier** | `{room_id}` | Tell a satellite to mirror `/v1/satellite-code`, verify sha256s, self-restart, and roll back if it doesn't reconnect in time. Returns `{requested, room_id, expected_sha}`. |
| `POST /v1/admin/satellites/{room_id}/pairing/preseed` | **Admin, security tier** | `{sat_type?, room_label?, hardware?, board?, mac?, force?}` | USB adoption: mint the room's pairing token (sha256 stored; RAW token returned once, never logged) + upsert the inventory row. `409` already paired unless `force` (rotates). |
| `DELETE /v1/admin/satellites/{room_id}/pairing` | **Admin, security tier** | — | Delete the room's pairing row so the NEXT `hello` for that room re-pairs. Returns `{room_id, reset}` (`reset: false` when there was nothing to clear). |
| `DELETE /v1/admin/satellites/{room_id}` | **Admin, security tier** | — | Remove a never-connected satellite (inventory + preseeded pairing). `409` when the room is provisioned (has an MPD instance). |
| `POST /v1/admin/satellites/{room_id}/label` | **Device (`X-Device-Token` or Bearer)** | `{room_label}` (null clears) | Set the satellite's display room label (grouping tag; cosmetic, daily-tier). |
| `GET /v1/admin/satellite/{room_id}/config` | Open | — | Editable satellite config: the schema joined with the values the Pi reported. `404` when the room isn't connected. |
| `POST /v1/admin/satellite/{room_id}/config` | **Admin (Bearer)** | `{"changes": {field: value}}` | Validate and push config edits; the Pi rewrites its `config.toml` and restarts. Returns `{sent, rejected, restarting}`. |
| `GET /v1/admin/satellite/{room_id}/logs` | **Admin read (Bearer or cookie)** | `?max_bytes=` (1 KB–10 MB, default 10 MB) | Tail of the satellite's in-RAM log ring, pulled live over its WS (`get_logs` → chunked `logs_chunk`). Gated like a config read: the ring holds what the room said (the satellite logs each transcript). `404` not connected, `503` disconnected mid-transfer, `504` stopped answering, `502` when the answer comes to more than `max_bytes` allows for (the reassembly buffer is capped per request, so a session cannot answer one pull with frames for as long as the timeout permits). |

### 2.6 Admin: version, config, chat, hardware

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/admin/version` | Open | — | `{sha, running_sha, checkout_sha, restart_required, restart_capable, restart_hint, restart_mode, started_at, uptime_sec, last_update, bad_sha}`. `sha`/`running_sha` are captured at boot (the code actually loaded); `checkout_sha` is read live from the tree. `restart_mode` is `update` when the restart starts `domovoi-update.service`, else `restart`. `last_update` is that unit's last run (`null` on a host without it): `{status, mode, from_sha, to_sha, prev_source, bad_sha, started_at, finished_at, duration_sec, deps_changed, mpd_changed, migrations_before, migrations_after, db_restored, error, steps: [{name, status, duration_sec}]}`, where `status` is `ok`, `running`, `rolled_back`, `rollback_failed`, `failed`, `aborted` or `refused`. `bad_sha` is the commit the unit rolled back; don't offer it again. See [LINUX_HOST.md](LINUX_HOST.md#updates-from-the-dashboard). |
| `POST /v1/admin/version/check` | **Device (`X-Device-Token` or Bearer)** | — | Fetch upstream and report `{behind, ahead, upstream, upstream_sha, error}`. `upstream_sha` is the full SHA a pull would move to, for comparing with `bad_sha`. Read-only, best-effort. |
| `POST /v1/admin/version/pull` | **Admin (Bearer)** | — | `git pull --ff-only`; a dirty/diverged tree returns `pulled: false` + stderr. Admin-tier: it changes the code on disk that this process and `/v1/satellite-code` serve from. Never restarts the process — that's the separate restart below. A successful pull also returns `prev_sha` and records it in `UPDATE_STATE_DIR/prev_sha`: the SHA the update unit rolls back to before it has a record of its own (the running SHA while an earlier pull is still waiting for its restart, else the pre-pull HEAD). |
| `GET /v1/admin/satellites/approvals` | **Admin read** | — | Satellites waiting for a human (portal onboarding). Returns `room_id`, `has_code`, `mac`, `board`, `sat_type`, `attempts`, timestamps. Neither the pairing token hash nor the code is returned: the operator reads the code off the device. |
| `POST /v1/admin/satellites/approvals/{room_id}/approve` | **Admin (Bearer)** | `{code}` | Promote a pending satellite into a real pairing, copying the token hash it presented — approval binds a device, not just a room name. `code` is the six digits the satellite is showing and saying; it is compared in constant time. `400` without a numeric code, `422` when it does not match (the request stays parked), `409` when nothing is pending or the pending row carries no code, `429` after five attempts for that room in five minutes. A wrong code is never `401`/`403`: those two mean the CALLER is not admitted, and the dashboard turns them into the admin-password prompt, so a mistyped digit answered with `403` cost the operator a re-login (F-050). |
| `POST /v1/admin/satellites/approvals/{room_id}/reject` | **Admin (Bearer)** | — | Drop a pending request. Not a ban: the device keeps retrying until approved or powered off. |
| `POST /v1/admin/version/restart` | **Admin, security tier** | — | Load pulled code. With `domovoi-update.service` installed it starts that unit (back up, sync dependencies, rebuild MPD, migrate, restart, health-check, roll back on failure); otherwise it bounces `domovoi-core` + `domovoi-web`. Returns `{ok, mode, units, delay_sec, error}` **before** the restart fires, so the client can tell "restarting" from "the server broke". Needs the sudoers grant for the current `mode` in [LINUX_HOST.md](LINUX_HOST.md); without it returns `ok: false` and the reason rather than prompting, and never falls back from `update` to a plain bounce. |
| `GET /v1/admin/config` | **Admin read (Bearer or cookie)** | `?section=common\|advanced` (optional) | The editable-config registry joined with live values (`{fields, plugin_fields, advanced_available}`). What comes back depends on how the caller authenticated: a **Bearer** reads everything; a **cookie-only** caller gets the `common` section with every credential value replaced by a mask (`masked: true` on the field) and no `advanced` section at all — `?section=advanced` answers `401` for it. Masked settings: `database_url`, `acoustid_api_key`, `letta_token` (`config_schema.SECRET_SETTING_NAMES`); plugin secrets arrive pre-masked as before. |
| `POST /v1/admin/config` | **Admin, security tier** | `{"changes": {...}, "plugin": "<slug>"?}` | Validate, persist to `.env` (or the plugin's `~/.domovoi/plugins/<slug>.env`), live-apply `hot`/`reapply` tiers, and report `{applied, restart_required, rejected, normalized}`. `whisper_device` and `whisper_compute_type` are checked as a pair as the next boot will read them: a write that names a compute type the device can't run (`float16` on `cpu`) is `rejected` together with any device in the same write; a write that only changes the device resets a saved compute type that can't run there to `auto` and says so in `normalized` (`{field: note}`, `{}` when nothing was adjusted). |
| `GET /v1/admin/device-token` | **Admin, security tier** (read: Bearer or cookie) | — | `{token, header}` — the household device token (`header` is `X-Device-Token`). Opaque: a generated one is an eight-word hyphenated phrase, a chosen one is any printable ASCII. `501` before setup. |
| `POST /v1/admin/device-token` | **Admin, security tier** | `{"token": "..."}` | Replace the household token with one an admin chose: printable ASCII 0x20–0x7E, 12 to 128 characters, stored verbatim with the outer whitespace trimmed. Returns `{token, header, rotated: true}` — setting IS a rotation. `400` with a message a person can act on (`at least 12 characters`, `at most 128 characters`, `letters, digits, punctuation and spaces only — …`) and the row unchanged; the rejected value is never echoed back. `501` before setup. **Restart both processes before setting one:** core and web share this row, and a process still running a build from before the two-form match canonicalises the presented candidate first, so it refuses every device-tier call until it is restarted. A generated phrase is canonical and is accepted by either build, which is why rotating is the way back. |
| `POST /v1/admin/device-token/rotate` | **Admin, security tier** | — | Mint a replacement household token in the eight-word phrase format: `{token, header, rotated: true}`. The previous token is refused from now on; `~/.domovoi/device-token.txt` is rewritten. Every household client has to be re-enrolled. This is also the way back from a chosen token. |
| `POST /v1/admin/chat-tool` | **Chat callback** | `{tool, args}` + `X-Chat-Callback` | Execute a chat-mode tool call on behalf of the chat agent's sandboxed proxy tools. The header must carry this boot's callback secret, which the generated tool source embeds — `401` otherwise. Degrades to an apology string rather than 500ing; returns `{"text": "..."}`. Regenerate the tools after a core restart (`POST /v1/admin/chat/resync`). |
| `POST /v1/admin/chat/resync` | **Admin (Bearer)** | — | Rebuild the chat tool surface and re-attach it to every chat agent. The install/enable/disable pipeline runs this automatically; this is the manual trigger. |
| `GET /v1/admin/hardware` | Open | — | Host hardware snapshot for the Models page: `{gpus, cpu, ram, disk, stt}`; each field degrades to empty/null independently. `stt` is what speech recognition loaded at boot: `{state, configured: {model, device, compute_type, compute_type_resolved}, loaded: {model, device, compute_type} \| null, fallback_reason, error}` — `state` as in `/v1/health`; the Models page shows a banner for `fallback` and `unavailable`. |

### 2.7 Admin: voices, sounds, wake words

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `POST /v1/admin/sounds/regenerate` | **Admin (Bearer)** | — | Kick a background re-render of all voices' greeting/canned clips, then notify satellites to re-sync. Returns `{"started": true}` immediately. |
| `POST /v1/admin/voices/sample` | **Device (`X-Device-Token` or Bearer)** | `{"name": "<voice>"}` | Live-synthesize a sample line in a registered voice; returns `audio/wav` with the text in `X-Sample-Text`. `404` for an unknown voice. |
| `POST /v1/admin/wake/record/start` | **Admin (Bearer)** | `{room_id, wake_word_id}` | Tell a connected satellite to record positive training clips (fresh take: clip dir + count reset). `404` room/word unknown, `409` mid drop-in or wrong status, `502` send failure. |
| `POST /v1/admin/wake/record/stop` | **Admin (Bearer)** | `{room_id}` | Stop an in-progress recording; the Pi resumes its wake loop. |
| `POST /v1/admin/wake/push` | **Admin (Bearer)** | `{room_id, wake_word_id}` | Push a trained model: the Pi writes the slug to its wake sidecar, syncs from `/v1/wake-models`, and restarts. `409` when not trained yet. |
| `POST /v1/admin/wake/score` | **Admin (Bearer)** | `{wake_word_id}` | Offline-score recorded clips against the trained model (per-clip max score + silence baseline). `409` no model, `501` when openWakeWord isn't installed. |

### 2.8 Admin: music and library

Voice-equivalent calls (`play`, the transport actions) re-enter the regular
routing pipeline so they land in `intents_log`/`conversation_log` exactly like
a spoken turn. Direct-play endpoints bypass the router, write only an
`intents_log` row (transcript prefixed `[ui]`), and never fall through to an
external streaming provider. All are **Open**.

The `queue/*` endpoints are the exception to "music actions replace the
queue": they edit the room's existing MPD queue in place. Entries are
always addressed by MPD **songid**, never by position — a songid is stable
for the life of a queue entry, so a concurrent reorder can't turn "remove
entry 3" into "remove the wrong song". They know nothing about devices;
the "added by" provenance and the device blocklist live in the web
process (§3.5a).

Everything in this table is on the **Device** tier (`X-Device-Token` or an admin Bearer) — ordinary household playback — except the two library sweeps at the end, which are **Admin (Bearer)**.

| Method & path | Request | Response / purpose |
|---|---|---|
| `POST /v1/admin/music/play` | `{room_id, query}` | Route `"play <query>"` through the full pipeline; dispatches the music-start frame to the room's Pi. Returns `{text, matched_handler, matched_path, music_action, online}`. |
| `POST /v1/admin/music/play-track` | `{room_id, track_id}` | Play one `library_tracks` row directly via MPD (tag → filename → basename lookup). `404` unknown/unfindable track, `502` MPD error. |
| `POST /v1/admin/music/play-tracks` | `{room_id, track_ids: [..]}` (≤500) | Load an ordered queue of library tracks into the room's MPD and start playback (the browser player's "cast to room"). |
| `POST /v1/admin/music/play-playlist` | `{room_id, playlist_id, shuffle?}` | Start a playlist (`playlist_id` 0 = the virtual Favorites). Ordered mode resumes from the saved position; stamps in-room playlist state so "next" stays in-playlist. |
| `POST /v1/admin/music/{action}/{room_id}` | — | Transport controls; `action` ∈ `pause`, `resume`, `stop`, `skip`, `next`, `previous` (routed as the spoken equivalents). `400` for anything else. |
| `GET /v1/admin/music/queue/{room_id}` | — | The room's live MPD queue in order: `{room_id, items:[{song_id, pos, file, title, artist, album, duration_sec}], current_song_id}`. `502` MPD error. |
| `POST /v1/admin/music/queue/{room_id}/add` | `{track_ids: [..]}` (≤500) | **Append** library tracks without clearing — the one music path that doesn't replace the queue. Starts playback when the room was idle with an empty queue. `{queued:[{song_id, file}], requested, started}`. `404` when MPD can't resolve any of them. |
| `POST /v1/admin/music/queue/{room_id}/remove` | `{song_ids: [..]}` | Drop entries by songid. `{removed:[..], skipped:[..]}` — an id MPD no longer has is reported, not an error. |
| `POST /v1/admin/music/queue/{room_id}/move` | `{song_id, to_position}` | Reorder. `404` when the id isn't in the queue any more. |
| `POST /v1/admin/music/queue/{room_id}/clear` | — | Empty the queue and stop the room (dispatches the music-stop frame). |
| `POST /v1/admin/music/mpd-rescan` | — | Tell every provisioned room's MPD to rescan the music dir — the playability half of a reindex, without the library sweep. `{rooms, updated}`. Device tier on purpose: no caller input, no database, and the web calls it after an upload has already written the files. |
| `POST /v1/admin/library/reindex` (**Admin**) | — | Background: sweep the music dir into `library_tracks`, then make every per-room MPD rescan. Returns `{"queued": true}` immediately. |
| `POST /v1/admin/library/enrich` (**Admin**) | — | Background: metadata enrichment pass (rate-limited; can take minutes). `{"queued": true}`. |

---

## 3. Web API (`:6369`)

All routes are `/api/...` (plus `/plugins/{slug}/static/*` for plugin assets
and `WS /ws/state`). The frontend itself is served statically from `/`.
Unlabeled endpoints are **Open** (LAN trust). CORS allows localhost, RFC 1918
ranges, and `*.local` origins only.

**Device** in these tables is the tier from §1.1: a valid `X-Device-Token`
header **or** an admin Bearer, `401` with neither, `403` with only the
dashboard cookie, and the pre-setup grace kept so a fresh install works. The
dashboard's ordinary mutations are on it — the reads beside them are not, and
stay Open unless the row says otherwise.

An **Open** label here describes this hop only. Every web route that forwards
to the core passes the caller's `Authorization`, `Cookie`, `X-Device-Token`
and real client address along with it, so the tier on the core route (§2) is
what finally decides — a proxied playback call needs the device token or an
admin Bearer even though the web hop asks for nothing. The web process's own
timer-driven calls (the state poll, a background media build) present the
household device token instead of a caller's credential.

### 3.1 Auth

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/auth/status` | Open | — | `{setup_complete, authenticated}` — the dashboard's setup-vs-login probe. `authenticated` reflects cookie or Bearer. |
| `POST /api/auth/setup` | Open (requires setup code) | `{setup_code, password}` (password ≥10 chars) | First-run claim of the admin tier. `403` wrong/missing/expired code (the code is valid for 24 h from when it was written), `409` already set up. Returns `{ok, token}` and sets the session cookie. Deletes the code file and rotates the household device token. |
| `POST /api/auth/login` | Open (backoff-throttled) | `{password, label?}` | Verify the password (per-source exponential backoff; `429` + `Retry-After` while throttled, `401` wrong password), mint a token, set the cookie. Returns `{ok, token}`. |
| `POST /api/auth/logout` | Bearer only | — | Revoke the *calling* session and clear the cookie. `401` without a Bearer (a cross-site POST with just the cookie can't log you out). |
| `GET /api/auth/sessions` | Bearer or cookie | — | `{"sessions": [{token_hash, label, created_at, expires_at, last_used_at, current}]}` for the revoke UI. |
| `DELETE /api/auth/sessions/{token_hash}` | Bearer only | — | Revoke a session by hash. `404` unknown hash. |
| `POST /api/auth/password` | Bearer only | `{old_password, new_password}` | Change the admin password (old one re-verified). Every other session is revoked; returns `{ok, revoked_sessions}`. |
| `GET /api/auth/device-token` | **Admin, security tier** (read: Bearer or cookie) | — | `{token, header}` — the household device token, from the same table the core reads. `401` unauthenticated, `501` before setup. The dashboard calls it right after a login to pair the browser, and Settings → Devices renders it for an admin. |
| `POST /api/auth/device-token` | **Admin, security tier** | `{"token": "..."}` | Set the household token to one an admin chose — the web mirror of the core's `POST /v1/admin/device-token`, same table, same rule, same 400s. Settings → Devices → Household token → `set…` calls this. It writes the shared row directly, so the same restart-both warning applies: a web on the new build can set a chosen token that a core still on the old build will refuse. |
| `POST /api/auth/device-token/rotate` | **Admin, security tier** | — | Rotate the household token (`{token, header, rotated}`); the core sees the new one immediately and the file mirror is rewritten. |

### 3.2 Plugins (management proxies + host)

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/plugins/manifest` | Open | — | Everything the frontend shell needs to render plugin pages without a rebuild (pages, nav entries, realtime channels, load errors). Served by the plugin host router, ahead of the `/{slug}` matchers. |
| `GET /plugins/{slug}/static/{path}` | Open | — | A plugin's `web/static` assets. Containment-checked; `404` when the plugin is disabled. |
| `GET /api/plugins` | Open | — | Installed plugins with the fields the admin list renders: manifest metadata, permissions, capabilities, handlers, pages, `web_load_error`. |
| `GET /api/plugins/{slug}/purge-preview` | Open | — | What uninstall-with-purge would drop: `{schema: "plugin_<slug>", tables: [{table, rows}]}`. |
| `POST /api/plugins/install` | Admin, fail-closed (core gates) | zip upload or `{"github_url"}` | Proxy to core `POST /v1/plugins/install`, auth forwarded, response verbatim. `503` when the core is down; `413` for a body over 64 MB, refused here rather than buffered (`WEB_PLUGIN_MAX_UPLOAD_BYTES`). |
| `POST /api/plugins/install/{staged_id}/confirm` | Admin, fail-closed (core) | — | Proxy of the confirm phase. |
| `POST /api/plugins/{slug}/enable` | Admin, fail-closed (core) | — | Proxy. |
| `POST /api/plugins/{slug}/disable` | Admin, fail-closed (core) | — | Proxy. |
| `POST /api/plugins/{slug}/uninstall` | Admin, fail-closed (core) | `{"data": "keep"\|"purge"}` | Proxy. |
| `POST /api/plugins/{slug}/upgrade` | Admin, fail-closed (core) | zip or `{"github_url", "force"?}` | Proxy. |

### 3.3 Capabilities

| Method & path | Auth | Response / purpose |
|---|---|---|
| `GET /api/capabilities` | Open | The live capability map (proxied view of the core registry) the sidebar's "+ add" affordances gate on. Also `host_kind` — `linux`, `wsl`, `windows` or `darwin` (`DOMOVOI_HOST_KIND` overrides the detection); `wsl` is a Windows install, where the Satellites page explains that USB adoption and writing a card aren't available. |
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
| `PATCH /api/music/library/{track_id}` | **Device** | `TrackPatch` (title/artist/favorited/...) | Edit track metadata. |
| `DELETE /api/music/library/{track_id}` | **Admin (Bearer)** | `?also_file=false` | Remove a track row (optionally the file too). `204`. `401` without an admin session, `403` for the dashboard cookie alone — the row and the file are left alone either way. |
| `GET /api/music/library/{track_id}/playlists` | Open | — | Playlists containing this track. |
| `POST /api/music/library/upload` | **Device** | multipart audio file(s) and/or `.zip` | Upload straight into the library. The files just written are indexed **in-process** (`library_indexer.index_paths`, bounded to this request's own files) and then the core is asked for an MPD rescan — deliberately *not* the admin-gated library-wide sweep, which a device-tier caller cannot reach. `reindex_triggered` reports whether that index ran. A zip is checked before anything is inflated: `413` when it has more than 5000 members, any member declares more than 1 GiB, or the members declare more than 4 GiB in total. `400` when nothing supported was found. |
| `GET /api/music/library/{track_id}/audio` | Open | `?download=` | Stream the file to the browser player (range requests). `?download=1` serves it as an attachment (save to device) named from the on-disk basename. |
| `GET /api/music/library/{track_id}/cover` | Open | — | Cover art. |
| `DELETE /api/music/acquisitions/{acq_id}` | **Device** | — | Cancel a pending acquisition. `204`. |
| `GET /api/music/now-playing` | Open | — | Per-room now-playing (from the cached core snapshot + MPD), with source attribution. |
| `POST /api/music/now-playing/{room_id}/favorite` | **Device** | — | Heart whatever the room is playing (re-searches by title into the library/queue). |
| `POST /api/music/play` | **Device** | `{room_id, query}` | Proxy → core `/v1/admin/music/play` (full voice pipeline). |
| `POST /api/music/play-track` | **Device** | `{room_id, track_id}` | Proxy → core direct-play (no conversation log, no external fallback). |
| `POST /api/music/play-tracks` | **Device** | `{room_id, track_ids}` | Proxy → core queue cast. |
| `POST /api/music/play-playlist` | **Device** | `{room_id, playlist_id, shuffle?}` | Proxy → core playlist start. |
| `POST /api/music/add-by-query` | **Device** | `{room_id, query, artist?, attach_to_playlist_id?}` | Proxy → core acquisition enqueue. |
| `POST /api/music/add-by-url` | **Outbound-fetch (core decides)** | `{room_id, url, title?, dedup_key?, attach_to_playlist_id?}` | Proxy with credentials + source address forwarded; the core's verdict passes back verbatim. |
| `POST /api/music/{pause\|resume\|stop\|skip}/{room_id}` | Open (FE-3) | — | Transport proxies → core `/v1/admin/music/{action}/{room_id}`. These four stay open by decision: they are the video satellite's kiosk transport row, and that screen renders unattended with nobody to hold a credential. See [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md). |
| `POST /api/music/library/reindex` | **Admin (Bearer)** | — | Proxy → core background reindex. |
| `POST /api/music/library/enrich` | **Admin (Bearer)** | — | Proxy → core background enrichment. |

### 3.5a Music: the room queue (editable) and device names

The room queue is MPD's, shared by every client — as opposed to each client's
own player queue, which never leaves the device. This surface is what makes it
editable, and what answers "who put this on?".

Provenance lives in `room_queue_items`, keyed by `(room_id, MPD songid)`. The
read prefers the device's CURRENT name and falls back to the snapshot taken at
add time, so renaming a device relabels its queue entries while a device that
has since been forgotten still shows the name it used. Entries with no record
— voice commands, casts from before devices had names, anything that reached
MPD from outside Domovoi — return `added_by: null`, and the UI renders nothing
rather than guessing. `GET /api/music/now-playing` carries the same
`song_id` + `added_by` pair for whatever each room is currently playing.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/music/queue/{room_id}` | Open | `?device_id=` | The queue with provenance joined: `{room_id, items:[{song_id, pos, file, title, artist, album, duration_sec, added_by, added_by_device_id, added_at, playing}], current_song_id, editable, blocked_reason}`. Reading is **never** blocked — a blocked device still sees what's on, and `editable:false` + `blocked_reason` say why it can't change anything. Reaps provenance rows whose songid has left the queue. |
| `POST /api/music/queue/{room_id}/add` | **Device** | `{track_ids:[..], device_id}` | Append, then stamp provenance. Proxy → core. `403` when the device is blocked here. |
| `POST /api/music/queue/{room_id}/remove` | **Device** | `{song_ids:[..], device_id}` | Drop entries and their provenance rows. `403` when blocked. |
| `POST /api/music/queue/{room_id}/move` | **Device** | `{song_id, to_position, device_id}` | Reorder. No DB write — provenance is keyed by songid, which is exactly why. `403` when blocked. |
| `POST /api/music/queue/{room_id}/clear` | **Device** | `{device_id}` | Empty the queue and wipe the room's provenance. `403` when blocked. |
| `GET /api/music/queue-blocks` | **Admin (read)** | — | Every block: `[{id, device_id, device_name, room_id, note, created_at}]`. `room_id: null` = every room. |
| `POST /api/music/queue-blocks` | **Admin (mutation)** | `{device_id?, device_name?, room_id?, note?}` | Block a device from editing a queue. Needs at least one of id/name (`400` otherwise); `409` when that device is already blocked at that scope. |
| `DELETE /api/music/queue-blocks/{id}` | **Admin (mutation)** | — | Unblock. `204`; `404` unknown id. |
| `POST /api/devices/register` | **Device** | `{device_id, name?, platform?, user_agent?}` | Upsert this client's row and bump `last_seen_at`. Idempotent — clients call it every boot. `name` seeds the row only when it is NEW, so a client that always sends its platform default can't overwrite a chosen name. `400` malformed id. |
| `PATCH /api/devices/{device_id}` | **Device** | `{name}` | Rename. Whitespace is collapsed so two names can't look identical yet block differently. `404` if the device has never registered. |
| `GET /api/devices` | **Admin (read)** | `?limit=200` | The device roster, most-recently-seen first. Admin-gated: it's an inventory of what's on the network. Feeds the blocklist editor, so an admin picks a device from a list instead of typing an id. |

`device_id` is **required** on every edit and optional only on the read. Not
because it proves anything — it is self-asserted — but because a blocklist
anyone evades by omitting the field is no control at all, and leaving it out is
far easier than claiming someone else's id. A missing `device_id` on an edit is
a `422`.

A block matches on device **id** OR device **name**. Both matter: the id
survives a rename (the obvious way to slip a block), the name survives a
reinstall (new id, same household label). Creating a block from the roster
fills both. A name block can only catch a device whose name the server knows —
an id it has never seen has no name to compare, so it passes.

**This is household policy, not a security boundary.** A `device_id` is
self-asserted by the client over a trusted LAN, exactly like the rest of the
daily tier, so someone determined can claim a different one. It reliably keeps
a known device out of a queue; it is not a defence against an attacker.
Managing blocks is admin-gated precisely so it can't be undone from the device
it was applied to. See [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md).

### 3.6 People

Reads are **Open**. The memory / favorite / preference edits are **Device**
tier — a person's own content, written by whichever household client they are
using. The two deletes that lose identification data — forgetting a person and
dropping a voice profile — are **Admin (Bearer)**: `401` without an admin
session, `403` for the dashboard cookie alone.

Person-centric views over the voice-profile / memory tables.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/people` | — | Everyone Domovoi has voice-identified. |
| `GET /api/people/{person_id}` | — | One person. |
| `DELETE /api/people/{person_id}` | **Admin** | Forget a person (profiles, memories, links). |
| `GET /api/people/{person_id}/sessions` | `?limit=20` | Recent conversation sessions. |
| `GET /api/people/{person_id}/conversations` | `?limit=50` | Recent conversation turns. |
| `GET /api/people/{person_id}/notes` | — | Notes mentioning them. |
| `GET /api/people/{person_id}/profiles` | — | Their voice profiles (embeddings metadata). |
| `DELETE /api/people/{person_id}/profiles/{profile_id}` | **Admin** | Drop one voice profile. |
| `GET /api/people/{person_id}/memories` | `?status=` | Extracted memories. |
| `POST /api/people/{person_id}/memories` | `MemoryCreate` | Add a memory manually. |
| `PATCH /api/people/{person_id}/memories/{memory_id}` | `MemoryPatch` | Edit/confirm/reject a memory. |
| `DELETE /api/people/{person_id}/memories/{memory_id}` | — | Delete a memory. |
| `GET /api/people/{person_id}/favorites` | `?kind=` | Favorites (songs, stations, …). |
| `POST /api/people/{person_id}/favorites` | `FavoriteCreate` | Add a favorite. |
| `DELETE /api/people/{person_id}/favorites/{favorite_id}` | — | Remove a favorite. |
| `GET /api/people/{person_id}/preferences` | — | Per-person preferences. |
| `PATCH /api/people/{person_id}/preferences` | `PreferencesPatch` | Update preferences. |

Related: the voice-identification denylist — `GET /api/denylist` (Open) and
`DELETE /api/denylist/{entry_id}` (**Admin (Bearer)**: removing an opt-out
puts someone back in front of the matcher, so it answers to the operator).

### 3.7 Satellites

Reads are **Open**. The action endpoints carry the tier the core route behind
each one carries, so the two hops agree: room label, timer cancel, announce,
announce-all and volume are **Device**; restart, display and the config push
are **Admin (Bearer)**; code push, pairing reset, adopt and delete are the
**security tier**. Live state comes from the cached core snapshot; actions
proxy to the core admin endpoints with the caller's credentials forwarded.

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/satellites` | Open | — | All known rooms with presence, wifi, volume, active voice, synced code SHA, full-duplex capability. |
| `GET /api/satellites/{room_id}` | Open | — | One room. |
| `GET /api/satellites/{room_id}/sessions` | Open | `?limit=20` | Recent sessions in this room. |
| `GET /api/satellites/{room_id}/conversations` | Open | `?limit=50` | Recent turns in this room. Each carries `utterance_trigger` (`wake_word`/`barge_in`/`followup`/`push_to_talk`; null before V011). |
| `GET /api/satellites/{room_id}/logs` | **Admin (read)** | `?max_bytes=` (1 KB–10 MB, default 1 MB) | Satellite's recent log output, live over its WS. Gated: the satellite logs every transcript, so this returns room conversation content. `404` when the room isn't connected — the buffer lives in the Pi's process. |
| `GET /api/satellites/{room_id}/notes` | Open | — | Notes taken in this room. |
| `GET /api/satellites/{room_id}/recently-played` | Open | `?limit=100` | Play history for the room. |
| `GET /api/satellites/{room_id}/timers` | Open | — | Active timers/reminders. |
| `DELETE /api/satellites/{room_id}/timers/{timer_id}` | **Device** | — | Cancel a timer. |
| `POST /api/satellites/{room_id}/announce` | **Device** | `{message}` | Proxy → core announce (one room). |
| `POST /api/satellites/announce-all` | **Device** | `{message}` | Proxy → core announce (broadcast). |
| `POST /api/satellites/{room_id}/dropin/start` | Proxy — core requires an admin Bearer | `{target_room}` | Proxy → core drop-in start; the caller's credentials are forwarded. |
| `POST /api/satellites/{room_id}/dropin/end` | Open | — | Proxy → core drop-in end. |
| `GET /api/satellites/{room_id}/dropin/phone-info` | **Device (`X-Device-Token` or Bearer)** | — | What a phone client needs to join this room's drop-in (`/v1/dropin/...` URL + capability info). The phone presents the same token again on the upgrade. |
| `GET /v1/satellite-plugins/manifest` (core) | Open | — | `{files: {"<slug>/<rel>": sha256}, meta: {slug: {...}}}` — enabled plugins' `[satellite]` payloads; satellites mirror it like the code channel. |
| `GET /v1/satellite-plugins/manifest.sig` (core) | Open | — | The payload list inside the same signed envelope shape, with `channel: "satellite-plugins"`. These are the files whose `post_install` runs as root on the device, so a pinned satellite refuses an unsigned or wrongly-signed list before it downloads anything. |
| `GET /v1/satellite-plugins/{path}` (core) | Open | — | One payload file by its `<slug>/<rel>` channel path. |
| `GET /api/satellites/media/status` | **Admin (read)** | — | Media-prep card data: boards, cache state, docker availability, per-plugin payload summary, and what this host can do: `host_kind`, `drive_targets` (`false` inside WSL, which sees no removable drives) and `default_setup_transport` (`usb`, or `portal` inside WSL). |
| `GET /api/satellites/media/targets` | **Admin (read)** | — | Removable drives that look like a flashed Pi boot partition. Always empty inside WSL. |
| `POST /api/satellites/media/prepare` | **Admin (Bearer)** | `{board, mic_profile, target: {kind: drive\|zip, token?}, offline?, setup_transport?: usb\|portal, wifi_country?}` | Start (or attach to) a media build; progress rides the `satellites.media` realtime channel. An omitted `setup_transport` is `usb`, or `portal` inside WSL. Inside WSL a `drive` target is refused with `422` (use the zip). |
| `GET /api/satellites/media/jobs` | **Admin (read)** | `?limit` | Recent build jobs (no server paths; `has_artifact` flags downloadables). |
| `POST /api/satellites/media/jobs/{id}/cancel` | **Admin (Bearer)** | — | Mark a build cancelled (best-effort). |
| `GET /api/satellites/media/jobs/{id}/download` | **Admin (read)** | — | The overlay zip for a `kind=zip` build. Since WEB-1 it carries no plaintext passwords: `userconf.txt` holds the console password's hash, and the setup-AP key and console login are shown once in the dashboard (`/jobs/{id}/credentials`, memory only). A card written straight to a **drive** still gets `domovoi/ap.json` + `domovoi/console.json`, which stage 1 needs. |
| `GET /api/satellites/media/jobs/{id}/credentials` | **Admin (read)** | — | `{ap: {ssid, psk} \| null, console: {username, password}}` (`ap` is null for a USB-adoption card) for a finished build, from the web process's memory (`404` once the dashboard restarts). The dashboard's "show setup details" reads it, for drive and zip builds alike; for a zip it is the only place the passwords appear. |
| `POST /api/satellites/media/cache/refresh` | **Admin (Bearer)** | — | Refresh the wheel/deb/model caches (slow on a cold cache). |
| `GET /api/satellites/pending` | Open | — | Unprovisioned satellites presenting a USB adoption volume on the server (empty when adoption is off, and inside WSL, which sees no USB drives). |
| `POST /api/satellites/pending/{pending_id}/adopt` | **Admin, security tier** | `{room_id, room_label?, wifi_ssid, wifi_psk, wifi_country?, wifi_hidden?, device_profile?, initial_volume?, force?}` | Adopt: preseed pairing on the core and write the provision file to the device. `409` room exists / device re-nonced, `410` device unplugged. |
| `DELETE /api/satellites/{room_id}` | **Admin, security tier** | — | Proxy → core delete (remove a `waiting` room). |
| `PATCH /api/satellites/{room_id}` | **Device** | `{room_label}` | Proxy → core room-label update. |
| `POST /api/satellites/{room_id}/volume` | **Device** | `{level}` | Proxy → core set-volume. |
| `POST /api/satellites/{room_id}/display` | **Admin (Bearer)** | `{action}` (`on` \| `off` \| `restart_kiosk`) | Proxy → core satellite display (video satellites only; `409` otherwise). |
| `POST /api/satellites/{room_id}/restart` | **Admin (Bearer)** | — | Proxy → core satellite restart. |
| `POST /api/satellites/{room_id}/upgrade` | **Admin, security tier** | — | Proxy → core satellite code sync + self-restart; the core applies the same gate (credentials forwarded). |
| `POST /api/satellites/{room_id}/pairing/reset` | **Admin, security tier** | — | Proxy → core `DELETE /v1/admin/satellites/{room_id}/pairing`; the core applies the same gate. |
| `GET /api/satellites/{room_id}/config` | Open | — | Proxy → core per-satellite editable config. |
| `PATCH /api/satellites/{room_id}/config` | **Admin (Bearer)** | `{"changes": {...}}` | Proxy → core config push (Pi rewrites `config.toml`, restarts). |

### 3.8 Calendar

Reads are **Open**; every write is **Device** tier.

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
| `PATCH /api/config/editable` | **Admin, security tier** | `{"changes": {...}}` | Proxy → core `POST /v1/admin/config`. Returns `{applied, restart_required, rejected, normalized}`. |
| `GET /api/config/version` | Open | — | Proxy → core `GET /v1/admin/version` (version label, restart mode, the update unit's `last_update` and `bad_sha`). |
| `GET /api/config/server-identity` | Open | — | Proxy → the `identity` block of core `GET /v1/health`: `{algorithm, fingerprint, public_key}`. What Settings → About shows, so a person can compare this server's fingerprint with the one printed on a prepared satellite card. |
| `POST /api/config/version/check` | **Device** | — | Proxy → core upstream check. |
| `POST /api/config/version/pull` | **Admin (Bearer)** | — | Proxy → core `git pull --ff-only`. |
| `GET /api/satellites/approvals` | **Admin read** | — | Proxy → pending approvals. Declared before `/{room_id}` so the path parameter can't shadow it. |
| `POST /api/satellites/approvals/{room_id}/approve` | **Admin (Bearer)** | `{code}` | Proxy → approve. `code` must be digits; the core compares it and counts the attempt. |
| `POST /api/satellites/approvals/{room_id}/reject` | **Admin (Bearer)** | — | Proxy → reject. |
| `POST /api/config/version/restart` | **Admin, security tier** | — | Proxy → core service restart (or update unit start). Gated at both hops. The connection drops moments after the response; clients treat that as success and poll `GET /api/config/version` until `restart_required` clears. In `update` mode they also stop when `last_update` shows a finished run newer than the one before the press: a refused or rolled-back update never clears `restart_required` the way a successful one does. |

### 3.10 Playlists

Reads are **Open**; every write is **Device** tier.

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

Reads are **Open**; every mutation (`POST` / `PATCH` / `DELETE`) is
**Admin (Bearer)** via `require_admin_mutation` — these rows decide what
every satellite says and in whose voice, and a Piper upload puts a model
file on the server. Mutations trigger the core's background clip re-render
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
| `POST /api/voices/piper` | multipart (`.onnx` + config) | Upload a local piper voice. `201`. Both files are streamed to disk under a byte budget — the model at 200 MB, the config at 4 MB — and an upload over either one is answered `413` while it is still arriving, leaving nothing on disk. A request whose declared `Content-Length` is over 210 MB is refused before its body is read at all. |
| `PATCH /api/voices/{voice_id}` | `VoicePatch` | Rename / set default. |
| `DELETE /api/voices/{voice_id}` | — | Remove a voice. `204`. |

### 3.12 Wake words

Reads are **Open**; every mutation (`POST` / `PATCH` / `DELETE`, including
clip selection and deletion and the record / score / push proxies) is
**Admin (Bearer)** via `require_admin_mutation` — this surface decides what
the house listens for. Recording, scoring, and pushing proxy to the core (which owns
the satellite sessions, openWakeWord, and the model files); training is
picked up by the core's background trainer. The default wake word is
`hey_jarvis`; this surface is how you train a custom one (e.g. "Hey Domovoi").

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/wake-words` | — | Registry: slug, status (`recording`/`training`/`ready`/`failed`), clip counts, threshold. |
| `POST /api/wake-words` | `WakeWordCreate` | Create a wake word (starts in `recording`). `201`. `phrase` must match `^[A-Za-z0-9 ,.'-]+$` (letters, digits, spaces and spoken punctuation, ≤ 120 chars) — it becomes an argument to the operator's `WAKE_WORD_TRAIN_COMMAND`, so `422` for anything else. |
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

**Device tier** (`X-Device-Token` or an admin Bearer; the byte serves also
take the dashboard cookie and a `?device_token=` query, because an `<img
src>` can't set a header) with three exceptions that take **admin
(mutation)**:

- `POST /delete` — the one verb that destroys something;
- `GET /download` when the path is a **directory** — the server builds the
  zip in memory and hands back a whole tree in one request;
- any write whose target library is a **removable drive** — writing onto a
  stick somebody plugged into the server is a different risk from saving
  into the household's own libraries.

`core:documents` (§3.14) is **not** one of them: saving into the Documents
library through `/api/files` is device tier, exactly as it is on
`/api/documents`.

Everything else — browsing, downloading a file, uploading, moving,
importing — belongs to the household: a paired phone shouldn't need the
admin password to drop a file into the music folder. The pre-setup grace is
kept throughout.

The device model the room queue uses (§3.6) still rides on top: every write
names the calling `device_id` (**required** — a blocklist anyone evades by
omitting the field is no blocklist), and an admin can take file writes away
from a named device with the **device blocks** below. What the token
changed is what that id means: only a caller already holding the household
credential reaches the block check, so an unpaired device can't write
whatever it calls itself. Within the household the id stays self-asserted,
so the block is household policy rather than a security boundary — same as
the queue's. Reads are never blocked; `browse` reports `writable` /
`blocked_reason` for the calling device so a client can disable its own
controls and say why.

This is the generic surface behind the **Files** tab: one router browses/
downloads/uploads/deletes/imports across every root the dashboard exposes —
the core media dirs (music / audiobooks / podcasts / documents),
enabled-plugin `[[media_libraries]]` roots, and present removable drives. It
is **additive** and does not touch `/api/documents/*` (§3.14), which the
Files page still calls for the Documents library's in-place editing.

The client only ever sends a `library_id` + a **relative** `path`; the absolute
`root_path` of each library is resolved and validated server-side and never
serialized. Containment rejects `..`, drive-absolute (`C:/…`), UNC (`//host/…`)
and symlink escapes; secret-shaped names under `~/.domovoi` are filtered from
every listing/serve/copy.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/files/libraries` | — | The library registry: `{ "libraries": [ … ] }`, ordered core, plugin, removable. Each record carries `id, label, kind (core\|plugin\|removable), icon, kind_icon, owner, editable, importable, doc_editing, reindex_kind, present` — `root_path` is stripped. |
| `GET /api/files/browse` | `?library_id=&path=&device_id=` | One directory level (dirs-first, then name). Returns `{ library_id, path, editable, importable, doc_editing, breadcrumb:[…], entries:[…], writable, blocked_reason }`; each entry is `{ name, rel, is_dir, size, mtime, kind (folder\|audio\|doc-office\|doc-text\|image\|pdf\|other), locked_by }` (`locked_by` non-null only for `core:documents`). `device_id` is optional and only affects `writable` / `blocked_reason` — `editable` is the library's property, `writable` is the calling device's. `400` traversal · `404` missing dir / unknown library · `410` ejected removable. |
| `GET /api/files/download` | `?library_id=&path=` | Serve a file as an attachment (audio via Range/`206`) or a directory as a streamed zip (`{name}.zip`, 5000-member cap). A **directory** additionally needs an **admin session** (`401` without). `404` missing · `413` cap · `400` traversal. |
| `POST /api/files/upload` | multipart: `library_id`, `path`, `device_id`, `files[]` · `X-Requested-With` | Upload into the browsed directory. `200 {saved, skipped, reindex_triggered}`. `401` no device token, or a **removable** target without an admin session (Documents is device tier like any other core library) · `403` non-editable, device blocked, **or the preflight-forcing header missing** · `404` bad dest · `400` none saved · `422` no `device_id`. Each name is sanitized to a bare basename, deduped, and re-containment-checked before write. |
| `POST /api/files/delete` | **Admin (mutation)** · `{ library_id, paths:[…], recursive:false }` | Delete files; folders need `recursive:true` (bounded, symlink-confined). Refuses to delete a library root. `200 {deleted, failed, reindex_triggered}`. `401` no admin session · `403` non-editable. For `core:documents`, releases any editor lock on a deleted path. |
| `POST /api/files/move` | `{ source_library_id, paths:[…], target_library_id, target_path, device_id }` | Move files/folders into another folder — the drag-and-drop verb. Within one library or between two, as long as **both are editable** (a move deletes from the source, so a read-only root can only ever be a destination; removables stay copy-only via `/import`). Per-path outcome: `200 {moved, skipped, failed, reindex_triggered}` — `skipped` holds harmless no-ops (dropped into the folder it was already in) so they don't read as errors. Refuses a library root, a folder into itself or a descendant, a name that already exists at the destination (never overwrites), and secret-shaped names. `403` either side read-only **or device blocked** · `404` missing target dir · `422` no `device_id`. Reindexes **both** sides when either is an indexed library. |
| `POST /api/files/import` | `{ source_library_id, source_path, target_library_id, target_path, device_id }` | Copy a file/dir from a **removable** source into an **importable** library (server-side, member+byte capped). `200 {copied, skipped, reindex_triggered}`. `403` device blocked · `409` source not removable / target not importable · `410` ejected source · `404` missing · `422` no `device_id`. |
| `GET /api/files/device-blocks` | **Admin (read)** | Every files block: `[{id, device_id, device_name, note, created_at}]`. |
| `POST /api/files/device-blocks` | **Admin (mutation)** · `{device_id?, device_name?, note?}` | Block a device from uploading, moving or importing anywhere (it can still browse and download). Matches id **or** name, like the queue blocks. Needs at least one of id/name (`400` otherwise); `409` when that device is already blocked. |
| `DELETE /api/files/device-blocks/{id}` | **Admin (mutation)** | Unblock. `204`; `404` unknown id. |

After a successful write to an indexed library (`reindex_kind == "music"`) the
web process proxies the core `POST /v1/admin/library/reindex` with the caller's
credentials forwarded; `audiobooks` runs the in-process indexer; `podcasts` /
`documents` / removable are no-ops.

### 3.14 Documents (homegrown editors)

**Saving is a household action; deleting is an admin action.** **Reads are
device tier** (`X-Device-Token`, an admin Bearer, the dashboard cookie, or
`?device_token=` for the browser-fetched `/raw` and `/export` URLs).
**Saves — `/create`, `/upload`, `PUT /text`, `PUT /sheet`,
`/drawings/write` — are device tier too**: a valid `X-Device-Token` or an
admin Bearer. The dashboard cookie *alone* is still refused `403` on any of
them (that split is the CSRF backstop, not a claim about who owns the
folder), and a mutation never reads `?device_token=`. **`/delete` and
`/download-zip` are admin tier** (`Authorization: Bearer`): delete is the
verb that destroys something, and `/download-zip` is the one request that
turns "can read the library" into "holds a copy of the library". All three
keep the pre-setup grace.

**A save writes only what its editor edits, and this is one of TWO DOORS
into the same folder.** `documents_dir` defaults to the operator's real
`~/Documents` and one household token is shared by every paired client, so
the tier alone is not the whole rule:

* `PUT /text` writes markdown and text — the kinds of file the text editor
  opens. A target that belongs to another editor (`.xlsx`, `.csv`,
  `.excalidraw`), to no editor (`.pdf`, an image, a legacy office format),
  or that is binary / larger than the editor's read limit is refused `415`
  and **nothing on disk changes**. `PUT /sheet` likewise refuses anything
  outside `.xlsx`/`.csv` with `415` before it creates anything, and
  `/drawings/write` refuses anything outside `.excalidraw`/`.svg` with
  `400`. Without these, a household save would be a general-purpose
  overwrite primitive over the operator's documents — a delete with extra
  steps, and delete is the verb that stayed admin.
* A save may create `documents_dir` itself but never a folder tree inside
  it: a path whose parent folder does not exist is `404`, matching
  `POST /api/files/upload`.
* `/api/documents/*` and `/api/files/*` both write into `core:documents`,
  so **both enforce the same rules**: the per-device write block
  (`files_device_blocks`), the admin-write library list, the library's
  `editable` flag, and the secret-shaped-name filter that skips `.env`,
  `*.key`, `*.pem`, `*.crt`, `*.p12`, `*.pfx`, `pairing_token` and
  `setup-code.txt`. A name one door refuses is refused by the other
  (`400`, or `skipped` on an upload).
* **Who the caller is does not depend on the caller mentioning it.**
  `device_id` in the body is still required on `/api/files` writes and
  optional on `/api/documents` saves — no client sends it there, and
  requiring it would `422` every Save — but it is no longer what decides.
  The server takes the first well-formed id it finds in the body, the
  `X-Device-Id` header, `?device_id=`, or the `domovoi-device-id` cookie
  it sets itself on `POST /api/devices/register`, which every browser
  calls on every dashboard load. A device an admin blocked in Settings is
  therefore refused on both doors from a browser, with no client change.
  Still open, and stated rather than implied: the Android app builds its
  HTTP client with no cookie jar, so it is unidentified on these routes
  until it sends `X-Device-Id`; and within the household an id is
  self-asserted either way, which is why this is household policy rather
  than a security boundary.
* **A path is normalised before it is judged, and refused when it cannot
  be.** Every gate above asks about the name the caller SENT, and the
  filesystem may store the bytes under a different one. `400` for: a
  control character anywhere in the path (a percent-encoded NUL or tab
  — previously an unhandled `500` out of `stat()`); on a Windows host, a
  `:` anywhere (`tax.pdf:stash.md` wrote into an NTFS alternate data
  stream that no listing, zip or export could see, while the extension
  gate read `.md`); and, on a Windows host, a path segment with a trailing
  dot or space (`.env.` and `.env ` are stored as `.env`, which is how
  they slipped the reserved-name filter on all four doors). The
  reserved-name filter itself now judges every name a name could become,
  on every platform.

The former OnlyOffice/Collabora sidecars — and
with them the open/close locks, JWT capability tokens, save callbacks, and
WOPI routes — are retired. Editing is homegrown/in-page: a markdown doc editor
(`/text` + `/export/doc`), a spreadsheet grid (`/sheet` + `/export/sheet`,
.xlsx/.csv round-trip via openpyxl), and Excalidraw for drawings. Every
row's `category` tells the UI how to open it
(`doc | sheet | drawing | newtab | download | text`); legacy office formats
(.docx/.doc/.odt/.rtf/.xls/.ods) list and download but don't edit in-app.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/documents` | **Device** · `?kind=all` | List documents with `category` routing (also `/api/documents/`). |
| `POST /api/documents/create` | **Device** · `CreateRequest` (optional `device_id`) | Create a blank file (`doc` → .md, `sheet` → .xlsx, `drawing` → .excalidraw, `text` → verbatim name). `400` for a secret-shaped name (including one wearing a trailing dot or space), for a stream separator or a control character; `409` if it already exists. |
| `POST /api/documents/upload` | **Device** · multipart (optional `device_id` field) · `X-Requested-With` | Upload documents. `403` without the preflight-forcing header. Secret-shaped names land in `skipped`, exactly as on `POST /api/files/upload`. |
| `POST /api/documents/delete` | **Admin (mutation)** · `DeleteRequest` | Delete documents. |
| `POST /api/documents/download-zip` | **Admin (mutation)** · `ZipRequest` | Zip + download a selection. |
| `GET /api/documents/text/{rel_path}` | **Device** | Read a text/markdown file (415 for binary/too-large). |
| `PUT /api/documents/text/{rel_path}` | **Device** · `TextWriteRequest` (optional `device_id`) | Write a text/markdown file. `415` — with the bytes untouched — for a target another editor owns, for a binary, or for one over the editor's read limit; `400` for a secret-shaped name, for a path the filesystem would store elsewhere (stream separator, trailing dot/space) or one it cannot use at all; `404` when the parent folder does not exist. |
| `GET /api/documents/sheet/{rel_path}` | **Device** | The sheet grid model (`rows[[{v,f}]]`); 415 for non-.xlsx/.csv. |
| `PUT /api/documents/sheet/{rel_path}` | **Device** · `SheetWriteRequest` (optional `device_id`) | Write the grid back (.xlsx keeps formulas as formulas). `415` for anything outside .xlsx/.csv, raised before anything is created. |
| `GET /api/documents/export/doc/{rel_path}` | **Device** · `?fmt=docx` | Export markdown/text as .docx (python-docx). |
| `GET /api/documents/export/sheet/{rel_path}` | **Device** · `?fmt=csv\|xlsx` | Export a sheet as .csv or .xlsx. |
| `GET /api/documents/raw/{rel_path}` | **Device** | Raw file bytes. Inline for the types a browser renders safely; HTML, SVG and XHTML come back as an attachment with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`. |
| `POST /api/documents/drawings/read` | **Device** · `DrawingReadRequest` | Read a drawing document. |
| `POST /api/documents/drawings/write` | **Device** · `DrawingWriteRequest` (optional `device_id`) | Save a drawing. `400` for anything outside .excalidraw/.svg. |

### 3.15 Podcasts and audiobooks

Reads are **Open**; every write is **Device** tier — subscribing, unsubscribing,
polling, saving a resume position and re-walking the audiobooks folder.
Subscribing and polling also make the server fetch a URL, so the feed URL must
pass the outbound-URL rules (§1.1). Feeds are polled by core background
workers; audio is served by this process.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/podcasts/subscriptions` | — | Subscribed feeds. |
| `POST /api/podcasts/subscriptions` | `SubscribeRequest` | **Device.** Subscribe to a feed URL. `400` for a URL the server won't fetch. |
| `DELETE /api/podcasts/subscriptions/{sub_id}` | — | Unsubscribe. |
| `GET /api/podcasts/subscriptions/{sub_id}/episodes` | — | Episodes for one subscription. Each row includes `has_file` and `file_ext` (e.g. `".mp3"`, or `null` when not downloaded) instead of the private server path. |
| `GET /api/podcasts/discover` | `?q=` (required) | Search a podcast directory. Hits whose feed URL would be refused are left out. |
| `POST /api/podcasts/poll` | — | **Device.** Poll feeds now (instead of waiting for the worker). |
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
per device × person, like the podcasts store). All **device tier**: the
file-content endpoints serve the same libraries the Files surface (§3.13)
lets a paired household client browse, so the reads take
`X-Device-Token` / an admin Bearer / the dashboard cookie / a
`?device_token=` query (a `<video src>` can't set a header), and the two
position writes take the header-only form of the same gate. Position saves
fire the `video_positions.changed` WS event.

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
`(library_id, rel_path)` with the same containment as the Files surface.
Both **device tier** — they serve the same libraries a paired household
client can browse in §3.13, and a phone that can list a folder should see
its thumbnails. Because an `<img src>` can't set a header, both also take
the dashboard cookie and a `?device_token=` query. The Files tab's per-row
**Open** action for images uses `/raw`; `/thumb` backs image tiles anywhere
the dashboard needs one.

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
| `GET /api/images/raw` | `?library_id=&path=` | The original, inline (the Files tab's Open target). An **SVG** is a document a browser executes, so it comes back as an attachment with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox` instead of rendering on the dashboard's origin. |

### 3.18 Chat

Claude-desktop-style threaded text chat answered by the local Ollama
(`clients/ollama.chat_stream`). Independent of the voice pipeline's chat
mode (Letta, per-room sessions); `chat_threads.letta_agent_id` is the
parked bridge for backing a thread with a stateful agent later. A message
carrying image uploads is answered by `ollama_vision_model` (the Vision
role slot on the Models page) instead of the Q&A model. Mutations fire the
`chat.changed` WS event.

Reads are **Open**; every write — creating, renaming or deleting a thread,
sending a message, staging an upload — is **Device** tier.

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
| `GET /api/models/active` | — | Which models are active for each role. The `stt` row also carries `device` and `compute_type` (the Whisper pair). |
| `POST /api/models/active` | `SetActiveBody` | Switch the active model for a role. For `stt`, an optional `compute_type` is written as `whisper_compute_type` in the same config write (checked against the device; `400` on any other role). |
| `GET /api/models/hardware` | — | Proxy → core `GET /v1/admin/hardware` (GPU/CPU/RAM/disk fit badges). |
| `DELETE /api/models/{name}` | — | **Admin.** Remove an installed model. |
| `GET /api/models/jobs` | — | Running/finished pull jobs with progress. |
| `POST /api/models/pull` | `PullBody` | **Admin.** Start downloading a model (background job). A reference that names its own registry host (`host/ns/model:tag`) is `400` when that host fails the outbound-URL rules. |
| `POST /api/models/pull/{job_id}/cancel` | — | **Admin.** Cancel a pull. |

### 3.20 News

Reads are **Open**; every write is **Device** tier — following a topic,
dropping a topic or a feed, hearting a story, attaching or re-testing a feed,
and polling now. The ones that make the server fetch something (feed attach,
re-test, poll) also have to pass the outbound-URL rules (§1.1). Scheduled
fetching runs in a core background worker.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/news/categories` | — | Available topic categories. |
| `GET /api/news/people/{person_id}/topics` | — | A person's followed topics. |
| `POST /api/news/people/{person_id}/topics` | `NewsTopicCreate` | Follow a topic. |
| `DELETE /api/news/topics/{topic_id}` | — | Unfollow. |
| `GET /api/news/topics/{topic_id}/feeds` | — | Feeds attached to a topic. |
| `POST /api/news/topics/{topic_id}/feeds` | `NewsFeedCreate` | **Device.** Attach a feed. `400` for a URL the server won't fetch. |
| `DELETE /api/news/topics/{topic_id}/feeds/{feed_id}` | — | Detach a feed. |
| `POST /api/news/feeds/{feed_id}/validate` | — | **Device.** Fetch-test a feed. |
| `GET /api/news/people/{person_id}/items` | `?limit=50` | Fetched items for a person. An item's `url` is either an absolute `http(s)://` link or `null`: the core keeps only web links at ingest (any other scheme is dropped, the story itself is kept), and the dashboard and Android app re-check the scheme before rendering a hyperlink or opening the link. The same rule applies to a now-playing `source_url`. |
| `POST /api/news/items/{item_id}/favorite` | `NewsItemFavorite` | Star an item. |
| `GET /api/news/people/{person_id}/briefing` | — | The assembled spoken-style briefing. |
| `POST /api/news/poll` | `?person_id=` | Fetch now. |

### 3.21 Health

| Method & path | Auth | Response / purpose |
|---|---|---|
| `GET /api/health` | Open | `{status: "ok"\|"degraded", db_reachable, domovoi_reachable}`. Returns `200` even when degraded so the UI can render a partial-degradation banner. |

### 3.22 The static mount, and how a front-end change reaches a browser

Everything under `/` that is not `/api` or `/ws` is the dashboard itself,
served from `web/static` by `web.backend.static_cache.RevalidatingStaticFiles`.
It is worth a section because it is the delivery mechanism for every
front-end change in the product, and getting it wrong is invisible: the
server holds the fix, the browser runs the old file, and nothing says so.

| What is asked for | What comes back |
|---|---|
| `GET /` or any `*.html` | The page, with every same-origin `<script src>` / `<link href>` / `<img src>` rewritten to carry `?v=<that file's token>`, plus `window.__DOMOVOI_BUNDLE__` set to a digest of the bytes being sent. `Cache-Control: no-cache` and an `ETag` computed over the REWRITTEN bytes. A conditional request for a page is answered **here**, against those bytes — see "The 304 that ate the deploy" below. |
| `GET /files.jsx?v=<current token>` | The file, `Cache-Control: public, max-age=31536000, immutable`. No revalidation, no round trip. |
| `GET /files.jsx` (no token, or a stale one) | The file, `Cache-Control: no-cache`. Store it, but ask before reusing it. |

The token is that one file's size and mtime, **per file, not per tree**:
`web/static` is 12 MB, 11 of it vendored bundles that change only when
somebody re-vendors them, and a tree-wide token would re-download all of
it on every release.

Why not simply `Cache-Control: no-cache` on everything? Because it was
measured not to work. `StaticFiles` used to send no `Cache-Control` at
all, so freshness was heuristic (RFC 9111 §4.2.2: about 10% of the age
since `Last-Modified`), and a browser that had opened the dashboard held
non-revalidating copies for days. A browser only learns a new header by
making a request, and the whole problem is that it does not make one:
driven in a real headless Chrome with a warm cache, one ordinary reload
after a deploy served the OLD bundle both with the header fix and without
it. Changing the URL is what makes a deploy arrive, because a URL the
browser has never seen has nothing to serve from cache.

Cost, on the LAN this runs on: one conditional `GET` per page load (the
page, `304`, no body) and a full download of exactly the files that
changed. Not the ~35 revalidations a uniform `no-cache` would have cost.

#### The 304 that ate the deploy

A release almost never changes `index.html` — the point of the rewrite is
that it does not have to — and `git pull --ff-only` rewrites only files
whose content changed, so the page keeps its size and its mtime.
`StaticFiles` derives its `ETag` from exactly those two things and answers
`304` from `file_response()`, before the rewrite is consulted at all. A
warm browser would send the stat `ETag` it stored under the old regime, get
a `304`, and keep the page with no `?v=` on anything — every URL of which
it already holds. **The versioned HTML never arrives because the HTML
carrying it never arrives.**

So the mount drops `If-None-Match` / `If-Modified-Since` / `If-Range` from
a request whose path names a page, and compares against the bytes it is
about to send. Assets keep their `304`s: an asset's stat `ETag` does
describe what it sends, and that is what holds a warm reload to one round
trip instead of thirty-five.

#### How a release actually reaches each browser

| The operator does this | A browser that has fetched the page since 2026-09-25 | A browser whose copy predates it |
|---|---|---|
| Reload (Ctrl-R) | New | **New** — this is the one that works |
| Bookmark / pinned tab / typed address / link | New | Old, until it reloads once or its heuristic window lapses |
| Leaves the tab open | The dashboard says it is behind and offers a reload | Nothing; that page has no such check in it |

The second column is the steady state and it is what this section
promises. The third is a **one-time** cost and it is not fixable from the
server: a copy stored under the old header-less regime has no explicit
freshness, falls to the RFC 9111 §4.2.2 heuristic, and is served straight
out of the browser's own cache with **no request made at all**. Nothing a
server sends can reach a request that is never made. The window is about
10% of the gap between when that browser last fetched the page and when
`index.html` was last written — hours on this box, not days.

**So the release note for 2026-09-25 has to say: reload the dashboard once
(plain Ctrl-R is enough — no hard refresh, no clearing) on each browser and
phone. Every release after this one arrives on an ordinary open.** Measured
on the real dashboard, all four arrivals, in a real headless Chrome:
`functional-testing/plan-20260922/reach-real-20260925/`.

#### `GET /api/bundle` — is this tab running what the box has?

| Method & path | Auth | Response / purpose |
|---|---|---|
| `GET /api/bundle` | Open | `{bundle: "<hex>" \| null}` — a digest of the page the mount would serve right now. |

The one staleness no cache header can reach: headers govern the NEXT
request, and a tab that has finished loading makes none. A dashboard left
open on a tablet runs last week's bundle until somebody closes it, and
nothing says so. The page carries the same digest in
`window.__DOMOVOI_BUNDLE__`; the page polls this route (20 s after load,
on becoming visible, and every 10 minutes) and shows a reload notice when
the two differ. Open, because a dashboard that cannot tell it is stale is
the thing this exists to stop, and the digest describes a page anyone on
the LAN can already `GET`. `null` means there is no static tree, and the
browser-side check treats that as no opinion.

#### Plugin assets are the same door

`GET /plugins/<slug>/static/<path>` (section 4.1) carries
`Cache-Control: no-cache` for the same reason. A plugin upgrade changes
those files WITHOUT changing their names — the manifest a plugin author
writes has no version in it — so they are revalidated rather than
versioned: one conditional `GET` per declared script per page load,
answered `304` with no body until the operator actually upgrades
something. `index.html`'s own loader asks with `cache: 'no-cache'` as
well, because the server header only governs a copy stored WITH it and a
browser warmed under the old regime is not reached by it at all.

`web/static/sw.js` is the other half. A service worker answers before the
HTTP cache is consulted, so it is network-first for the shell and re-fetches
with `cache: 'no-cache'`; its cache is the offline fallback, not the source
of truth. After each successful store it drops the other copies of that
same path, so the shell cache holds one entry per file rather than one per
file per deploy — `activate` only ever deletes whole caches by name, so
without that a phone would carry every copy of every file it ever fetched. Note the asymmetry worth knowing at deploy time: a service worker
only registers on a secure context, so on a plain-HTTP LAN install
(`http://<host>:6369`) there is no worker at all and the static mount is
the only layer there is. A browser still running a PRE-2026-09-25 worker
takes one extra reload on that one upgrade — the code deciding what to
serve on the first one is already in the browser, and no server change can
reach it.

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
  admin session unless the plugin author put it on another tier with a
  decorator on the route function:
  * `@device_endpoint` — the **device tier**: the household `X-Device-Token`
    or an admin Bearer, answered exactly like the core's own device-tier
    routes (`401` with nothing, `403` on the dashboard cookie alone, `429`
    with `Retry-After` once a source keeps presenting wrong tokens). On a GET
    it gates the read like the core's media reads (header, cookie or
    `?device_token=`).
  * `@open_endpoint` — no credential at all.

  Every route on either tier is listed on the install preview's trust screen
  (`device_endpoints` and `open_endpoints`, under separate headings); a route
  carrying both is refused at staging and at load, and so is a route whose
  marker the preview's source scan cannot see (one applied by a call or set
  by hand rather than stacked as a decorator). So is a route the gate cannot
  cover: a plain Starlette `Route` (`router.add_route`), `Mount`, `Host` or
  `WebSocketRoute` on a plugin router would mount without the gate
  dependency, so it fails the load. GETs are open unless the
  plugin adds its own `Depends(admin_required)` or marks the GET
  `@device_endpoint`.
* Pre-setup grace applies: until the admin credential exists, the gate
  allows everything (LAN-trust, matching the core's daily-use surfaces).
* Reserved path: the core itself serves `GET /v1/plugins/{slug}/status`
  (section 2.3), so a plugin cannot mount its own `/status`.

**Web process — `/api/plugins/<slug>/...`** (`web/backend/plugin_host.py`):

* Routers registered via the plugin's web entry point (`register_web(ctx)`,
  `ctx.add_router(...)`) mount behind a slug-enable gate (`404` when
  disabled) **and the same default-deny auth gate as the core** (one body,
  `domovoi.webkit.enforce_route_tier`): every non-GET route requires an
  admin session (`401` with no credential, `403` when only the dashboard
  cookie is present — mutations are Bearer-only) unless its route function
  carries `@domovoi.webkit.device_endpoint` (household token or admin
  Bearer) or `@domovoi.webkit.open_endpoint`. GETs are open unless the
  plugin adds `Depends(domovoi.webkit.admin_required)`. The dashboard
  attaches its Bearer and the stored household token to every plugin call;
  a device-tier refusal opens the "pair this browser" prompt and an admin
  one the sign-in modal, and the refused call is replayed once the
  credential exists. The pre-setup grace applies here too. A plugin web
  route that proxies to its core routes forwards the caller's Bearer,
  cookie and household token (`CoreClient.post_admin`).
* Static assets serve from `GET /plugins/<slug>/static/<path>` (containment-
  checked), with `Cache-Control: no-cache` — an upgrade changes these files
  without changing their names, so they have to be revalidated or the old
  panel keeps running (§3.22). Pages, nav entries, and realtime channels are
  announced through `GET /api/plugins/manifest` so the frontend needs no
  rebuild.
* Plugin web code runs under an import guard: it can never pull core runtime
  modules into the web process.

### 4.2 Live example: the bundled radio plugin

`plugins/radio` (publisher Coders Farm) mounts both surfaces and is the
reference implementation.

Core router → mounted at `/v1/plugins/radio` (the FCC import keeps the
admin default; the simulcast lookup is `@device_endpoint`):

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /v1/plugins/radio/fcc-import` | Admin (default mutation gate) | Start the FCC FM bulk import as a background job (`?state=` optional); returns immediately. A long server-side job, like the core's library sweeps. |
| `GET /v1/plugins/radio/fcc-import` | Open | Import job progress. |
| `POST /v1/plugins/radio/stations/{station_id}/resolve-simulcast` | Device (`@device_endpoint`) | Find an online simulcast stream for an FM station. |
| `GET /v1/plugins/radio/state` | Open | Live tuner state: `{sdr_available, sdr_frequency_mhz, fcc_import}`. (Named `/state` because the core reserves `/status`.) |

Web router → mounted at `/api/plugins/radio` (slug-enable gate; GETs open
for the dashboard's daily-use radio pages; the everyday mutations are
device tier — a paired phone plays and favorites without the admin
password — and the FCC import keeps the admin default; nothing is open):

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/plugins/radio/search` | Open | `?q=&country_code=&tag=&language=&limit=30&offset=0` | Station-directory search; hits already saved locally carry their row id + favorited flag. |
| `GET /api/plugins/radio/stations` | Open | `?favorited_only=&source=online\|fm&q=&frequency_mhz=&limit=200&offset=0` | Saved stations. |
| `GET /api/plugins/radio/stations/{station_id}` | Open | — | One station. |
| `GET /api/plugins/radio/recent` | Open | — | The ten most recently played stations. |
| `POST /api/plugins/radio/play` | Device (`@device_endpoint`) | `{source, external_id?, station_id?, ...}` | Stamp a station as played (resolve-or-create, not a favorite) and return the row to stream. A new `stream_url` passes the outbound-URL check before it becomes a row. |
| `POST /api/plugins/radio/stations` | Device (`@device_endpoint`) | station body | Save (favorite) a station. `201`. Idempotent on `external_id`. `422` for a null `name` / `source` / `tags` / `sample_interval_sec` or a `frequency_mhz` outside 0-9999.9 (the column is `NUMERIC(5,1)`). |
| `PATCH /api/plugins/radio/stations/{station_id}` | Device (`@device_endpoint`) | `{name?, stream_url?, favorited?, sample_interval_sec?, tags?}` | Edit / favorite / unfavorite. A `stream_url` passes the outbound-URL check before it is stored. An explicit `null` clears `stream_url` or `tags`; for `name`, `favorited` or `sample_interval_sec` (NOT NULL columns) it is a `422` naming the field, and nothing is written. |
| `DELETE /api/plugins/radio/stations/{station_id}` | Device (`@device_endpoint`) | — | Forget a station (cascades to its detections). `204`. |
| `POST /api/plugins/radio/stations/{station_id}/resolve-simulcast` | Device (`@device_endpoint`) | — | Simulcast resolution from the dashboard; the caller's household token (or Bearer) is forwarded to the core's own device-tier gate. |
| `POST /api/plugins/radio/fcc-import` | Admin (default mutation gate) | `?state=` | Start the FCC import (Bearer forwarded to the core). |
| `GET /api/plugins/radio/fcc-import` | Open | — | Poll the FCC import. |
| `GET /api/plugins/radio/detections` | Open | filters | Passive song-detection history. |
| `GET /api/plugins/radio/badge` | Open | — | `{favorites: N}` — the sidebar badge count. |
| `GET /api/plugins/radio/stations/{station_id}/stream` | Open | — | Proxy a station's audio through the web backend for the browser player (dodges CORS/mixed-content). FM/SDR stations return `409` — those play only through a satellite room. |

---

*Generated against the v1.0.0 tree; when this document and the code disagree,
the code wins — and please open an issue. See also
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) for what common error responses mean
in practice.*
