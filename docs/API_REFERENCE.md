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
a restart after that writes a fresh one); the core prints it only when its
stdout is a terminal, and its log carries the file's path, never the code;
`POST /api/auth/setup` exchanges that code + a chosen password for the admin
credential (argon2id hash) and a session token. Tokens are 256-bit bearer
values — only their sha256 is stored — with a 30-day sliding expiry under a
90-day absolute cap; changing the password revokes every other session. Both
processes validate against the same `admin_sessions` table, so a token
minted by the dashboard works on the core too. See `domovoi/admin_auth.py`.
The dashboard keeps the token in page memory only; for
`ADMIN_RELOAD_WINDOW_MIN` (default 60) minutes after the password sign-in a
reloaded page takes the same token back through the HttpOnly session cookie
(`POST /api/auth/resume`, same origin only; §3.1). The cookie is a secret of
its own, never the token: the session row keeps its sha256 and the token
wrapped under it.

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
Android app keeps it sealed under an Android Keystore key (AES-256-GCM,
`data/TokenVault.kt`) and sends it on every request to the server it was
issued by — and to no other scheme, host or port, on any redirect hop — on
the `/ws/state` socket and on the drop-in call socket, after that server
has proved its identity against the key pinned when it was trusted, on the
current network, within the last ten minutes and since the app last came
to the foreground (`GET /api/health?challenge=`). Browser
WebSockets cannot set headers, so the dashboard's first `/ws/state` frame
carries `{"subscribe": [...], "device_token": "..."}` instead; the Android
socket uses the header.

Every endpoint below is labeled with one of these tiers:

| Tier | Meaning |
|---|---|
| **Open** | No auth. Daily-use surface, LAN trust. |
| **Device (`X-Device-Token` or Bearer)** | `require_device`: a valid `X-Device-Token` header **or** an admin Bearer. `401` with neither or with a stale token; `403` with only the dashboard cookie. Keeps the pre-setup grace so a fresh install works. This is the tier for ordinary household actions on BOTH hops: a turn, an announcement, playback and the room queue, and on the dashboard also the calendar, playlists, chat, news, podcasts, audiobooks, a person's memories and favorites, device registration, and the satellite verbs the core puts on the same tier (label, timers, announce, volume). |
| **Device read** | `require_device_read`, the READ half of the device tier: everything **Device** accepts, plus the dashboard's session cookie and the household token as a `?device_token=` query (for what a browser fetches by URL — an `<img>`, a `<video>`, an `<audio>`, `window.open`). `401` with none of them, with a stale token or a wrong query token; nothing that writes reads the query. Same pre-setup grace. The tier for the Documents / Files / Images / Videos reads and — because household speech and personal content are for paired devices only (2026-09-26) — for reading back conversations, voice notes, chat, wake-word recordings and a person's memories, favorites and preferences; and, since 2026-10-08, for who is home and the calendar (the people roster, the rooms and their session lists, the pending-adoption list, the calendar reads: WEB-15) and for when a room is in use (a room's play history, its satellite's reported config, the speech latency summary: REV-11). |
| **Device or guest (playing room)** | `require_device_or_active_room_guest` (core) / `require_device_or_guest` (web), and the read half `require_device_read_or_active_room_guest` (owner decisions 2026-10-10). A request carrying ANY credential is judged by exactly the **Device** (or **Device read**) gate, answers included. A request carrying none, on a box past setup, is a **guest**: the core lets it act only while that room is playing (MPD `play` or `pause` with a current song; `403` otherwise, the same sentence for a room that doesn't exist), only with `X-Requested-With` or `X-Domovoi-Device-Id` on the request (`403` without — a cross-site "simple" request carries neither), throttled to 30 actions a minute per address and its queue reads to 120 (`429`), and not once an admin has banned it (`403`; `503` if the ban can't be checked). A guest's add takes at most 50 tracks and never takes the queue past 500 entries (`400` / `409`); a guest's remove takes one song id (`400`). The web hop cannot see MPD, so it passes a guest through with nothing but what it sent, after the switch, the queue blocks, the ban and its own throttle. `GUEST_QUEUE_EDITING=false` makes a guest the **Device** tier's `401` again at both hops. Worn by the room queue's read, add, remove and move and by the transport row except `stop` (§3.5, §3.5a; [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#guests-on-a-playing-room)). |
| **Chat callback** | `require_chat_callback`: the per-boot secret the chat agent's generated proxy tools carry in `X-Chat-Callback`. One endpoint (`POST /v1/admin/chat-tool`) wears it, because Letta's sandbox holds no admin session. A core restart mints a new secret, so the tools must be regenerated (`POST /v1/admin/chat/resync`). |
| **Admin (Bearer)** | `require_admin_mutation`: requires `Authorization: Bearer <token>`. The dashboard cookie is *never* enough for a mutation (CSRF stance). Before first-run setup completes, these endpoints allow requests (pre-setup grace) so a fresh install works. |
| **Admin read (Bearer or cookie)** | `require_admin_read`: a GET that carries secrets. Either a Bearer token or the `domovoi_admin` cookie (set at login, `HttpOnly`, `SameSite=Strict`; a random secret of its own since 2026-10-14, never the token — presented as a Bearer it is nothing) renders it. Same pre-setup grace. |
| **Admin, security tier** | `require_admin_security` (mutations) / `require_admin_security_read` (the device-token read): Bearer-only for mutations, cookie may render the read. **No pre-setup grace — 501 until admin setup completes**, and `--reset-admin` closes them again. Config write, service restart, satellite code push, pairing preseed / reset, approving or rejecting a parked satellite, satellite delete, device-token rotation, opening a drop-in from HTTP, pulling new code (`/v1/admin/version/pull`) and clearing a container-guard tamper incident, which lifts the freeze (delta review N5, 2026-10-16), and every route of the opt-in command recordings (§3.12a) — reads included, which take `require_admin_security_read`. |
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
with a reason; it also holds every GET that answers with household speech or
personal content to **Device read**, and pins the reads beside them that
were deliberately left **Open**.

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
* **The container guard's freeze.** While a tamper incident is open
  (docs/CONTAINER_GUARD.md), the operations it freezes answer `423` with
  the typed envelope
  `{"detail": {"error": {"code": "container_guard_frozen", "message": "Plugin installs are paused: the container guard found signs of tampering (incident #12: ...). An admin must review and clear it on Settings → Health first.", "details": {"operation", "incidents": [{id, summary, opened_at}], "opened_at"}}}}`
  — after the route's own credential check, so a caller without one still
  gets its `401`. The satellite code and plugin payload channels, which take
  no credential, answer `423` with only
  `"Paused by Domovoi's container guard until an admin reviews a problem."`
  and empty `details`. Frozen: `POST /v1/plugins/install`,
  `/v1/plugins/install/{id}/confirm`, `/v1/plugins/{slug}/upgrade`,
  `/v1/plugins/{slug}/enable`, `/v1/admin/satellite/upgrade`, every
  `GET /v1/satellite-code/*` and `/v1/satellite-plugins/*`,
  `/v1/admin/version/pull`, `/v1/admin/version/restart` when it would load
  new code, and the web's `POST /api/satellites/media/prepare` and
  `/cache/refresh`. The web proxies pass the core's `423` through.
* Web endpoints that proxy the core pass its status and body through
  unchanged. Plugin-management proxies return `503` when the core process
  itself is unreachable.
* **Internet turned off.** When the server is answered `never` to the
  internet question (`INTERNET_ACCESS`, see §1.6), a route that would reach
  the internet answers `409` with exactly
  `{"detail": "internet access is turned off for this box (Settings → Internet)"}` and the header `X-Domovoi-Refusal: internet-off`,
  before doing anything (no job row, no fetch, no subprocess, no proxy to
  the core). The plugin installer keeps its own envelope:
  `422 {"detail": {"error": {"code": "internet_off", "message": "internet access is turned off for this box (Settings → Internet)", "details": {}}}}`.

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
| `WS /ws/state` | web :6369 | Dashboard state push. **The handshake needs a household credential** — an `X-Device-Token` header, an admin `Bearer`, the dashboard's session cookie, or (for a browser, which can set no header here) the `domovoi.device-token-b64.<base64url(token)>` subprotocol, which the server echoes back **exactly as offered** or the browser drops the socket. The token is base64url-encoded (padding stripped) because RFC 6455 requires each subprotocol element to be an RFC 9110 *token*, which a chosen household token need not be; the legacy `domovoi.device-token.<token>` spelling is still accepted for clients on a cached bundle. Every offered element is scanned for the b64 prefix before any is read as the legacy form. Without one the upgrade is answered **403** and nothing is ever pushed to that socket. The pre-setup grace applies, so a fresh install's dashboard connects. Client optionally sends `{"subscribe": ["music.now_playing", "satellites.presence", ...], "device_token": "..."}` (the `device_token` field is accepted and ignored — the credential is settled in the handshake); no frame (or an empty list) means all channels. Server pushes `{"type": "<channel>.changed", "data": <full new snapshot>}` events, driven by a 1.5 s poll loop accelerated by Postgres LISTEN/NOTIFY. Core channels: `music.now_playing`, `acquisitions`, `satellites.presence`, `satellites.wifi`, `satellites.health` (a per-room digest from the admin snapshot — when the latest health sample and the last outage report arrived, and the sample's `seq`, never their content — so it fires about once a minute per satellite; the page refetches the room's device-read `/health`), `people.last_seen`, `calendar.events`, `library.indexer`, `wake_words`, `timers` (every running timer and reminder, the rows `GET /api/timers` lists without its `server_now`; fires when one is set, cancelled by voice or from a dashboard, or goes off), `timer_fires` (the timers and reminders that went off in the last hour, newest first, at most 20 — the Fire objects `GET /api/timers/fires` serves, unmasked and whole like `timers`: the handshake admits exactly the tier that reads the whole fire history over HTTP, rule F1; fires on every write to the fire history (V018): one going off, each room's announcement being claimed, spoken or given up on, an acknowledgement, the fire settling; `[]` without V018), `lyrics` (a digest of the lyrics table — counts, the newest change and the lyrics workers' states, never a title or a lyric; §3.5c), `help_deliveries` (help steps sent by voice to a person's phones in the last hour, `[{id, person_id, thread_id, title, created_at}]` newest first, at most 20, V035; fires when one is sent; a phone notifies only for rows whose `person_id` is its owner, §3.18a; `[]` without V035), `containers` (the container guard's household view — the body `GET /api/containers/incidents` gives a household caller, without the admin-only `expected`/`found`; fires on every incident write: one opening, changing, resolving, being acknowledged or cleared; §3.9a), `files` (`{library_id: count}`: how often each filesystem library — Videos, Pictures, Documents, any Files library, none of them indexed in the DB — changed as far as the web knows: a write through `/api/files` or `/api/documents`, or a change on disk under a listing someone viewed in the last two minutes, which the poll tick checks by folder mtime; the Videos and Files pages, web and app, re-read the list on screen when it moves). Enabled plugins add their own via manifest `[[realtime]]` entries. |
| `WS /v1/stream/{room_id}` | core :6370 | The satellite voice stream: bidirectional audio + control frames (hello, wake, audio chunks, transcripts, TTS, music start/stop handshake, drop-in, config/volume/voice status, wake-word recording). The full frame contract is documented in [uml/satellite-protocol.md](uml/satellite-protocol.md); the implementation is `domovoi/streaming.py`. The client's first frame must be `hello`; the server provisions nothing, lists nothing, and sends no `ready` until that `hello` has passed the pairing check (a socket that stays silent for `SATELLITE_HELLO_TIMEOUT_SEC`, default 5 s, or sends any other frame first is closed with nothing created). One session per `room_id` — a second connect with the same id evicts the first for broadcasts (and closes the socket it replaced), but only once its own `hello` is accepted. `Origin`, when present, must be a LAN origin; satellites send none, which passes. `ready.features` lists the message types this core understands beyond protocol 0.1 (`speech_pause`, `end_capture`, `music_failed`, and since 2026-10-09 `health` — the satellite's minute-by-minute health sample — and `log_push` — its log lines for the retained per-room log; these two are listed only to a session whose `hello` proved its room with the pairing token, and are ignored from any other); a satellite sends a new type only when it is listed, because an older core answers an unknown type with `error` and the satellite takes that as the end of the turn — see [Adding to the protocol](uml/satellite-protocol.md#adding-to-the-protocol). The core sends `end_capture {utt}` (stop listening, it is answering — [Early commit](uml/satellite-protocol.md#early-commit)) only to a satellite whose `hello` declared `capture_control`. |
| `WS /v1/dropin/{room_id}` | core :6370 | Phone drop-in only: joins the intercom bridge as a call peer *without* registering as a satellite. Query param `phone_id` identifies the caller (auto-prefixed `phone-` so it can never collide with a room). **Device tier, checked on the upgrade:** send the household token as the `X-Device-Token` header, or as `?token=` when the client is a browser and cannot set headers, or an admin `Authorization: Bearer`. Anything else gets `{"type":"error","code":"unauthorized"}` and a `1008` close before any session exists, so the target room is never disturbed and `active_dropins` gains no entry. **No pre-setup grace:** until first-run admin setup completes (and again after `--reset-admin`) every upgrade gets `{"type":"error","code":"setup_required"}` and a `1008` close, whatever it presents — the socket form of the security tier's `501`. With `DROPIN_ACCEPT_MODE=ring` the call does not open on connect: the server sends `{"type":"dropin_ringing"}`, rings the room, and only bridges when someone there says yes (`dropin_end` with `reason: "no_answer"` after `DROPIN_RING_TIMEOUT_SEC`). `Origin`, when present, must be a LAN origin. See `domovoi/phone_dropin.py`. |

### 1.6 The internet answer and its refusals

`INTERNET_ACCESS` is `always`, `sometimes`, `never`, or unset (`""`, the
behaviour before the setting existed). Both processes read it from their
environment first, then `domovoi/.env` (`domovoi/egress.py`); it is set
with `POST /v1/admin/config` / `PATCH /api/config/editable`
(`{"changes": {"internet_access": "<answer>"}}`) and described in
[INTERNET.md](INTERNET.md). Under `never` these routes answer the `409`
of §1.4:

| Process | Routes |
|---|---|
| core | `POST /v1/admin/library/enrich`; `POST /v1/admin/config/acoustid-test`; the radio plugin's `POST /v1/plugins/radio/fcc-import` and `POST /v1/plugins/radio/stations/{id}/resolve-simulcast` |
| web | `GET /api/podcasts/discover`; `POST /api/podcasts/subscriptions` with a `query` (subscribing by feed URL is allowed: it only stores the URL); `POST /api/podcasts/poll`; `POST /api/news/topics/{id}/feeds`; `POST /api/news/feeds/{id}/validate`; `POST /api/news/poll`; `POST /api/models/pull` (unless the model names a registry on the house network); `POST /api/satellites/media/cache/refresh`; the radio plugin's `GET /api/plugins/radio/search`, `POST …/stations/{id}/resolve-simulcast`, `POST …/fcc-import`, `POST …/play` for an internet station, and `GET …/stations/{id}/stream` for one |

Others report it in their own shape: `POST /v1/admin/version/check` →
`{"behind": 0, "ahead": 0, "upstream": false, "upstream_sha": null, "error": "internet access is turned off for this box (Settings → Internet)"}`
and `POST /v1/admin/version/pull` → `{"pulled": false, "new_sha": null, "error": "internet access is turned off for this box (Settings → Internet)", "prev_sha": null}`,
neither running git; `POST /v1/plugins/install` with a `github_url` →
the installer's `422` with code `internet_off` (a zip still installs when
its Python packages are already present; otherwise the same code, with
"needs the internet to download its Python packages" and
`details.missing`). Voice replies say "I'm set to stay off the internet,
so …".

### 1.7 Paging (the files-like lists)

Every list behind the Music, Videos, Files, Audiobooks and Podcasts pages
pages the same way (`web/backend/api/paging.py`), so no client ever has to
take a whole list at once and every sort, filter and search covers the
whole list on the server:

| Request | Meaning |
|---|---|
| `limit` | Page size. Default **50**, max **200** (`422` above it or below 1). |
| `cursor` | Opaque, from the previous answer's `next_cursor`. A keyset position (the last row's sort key), so a row added or removed between two pages neither repeats a row nor skips one. A cursor cut for a different `sort` is `400` ("start again from page one"); a garbled one is `400`. |
| `offset` | Also accepted (rows to skip). Stable only while the list doesn't change; use `cursor`. |

The answer adds `total` (rows matching the active filters, not the page),
`limit`, `has_more`, `next_cursor` (`null` on the last page) and, for an
offset request, `offset`. Clients start again from page one whenever a
filter, search or sort changes.

**Old clients.** A request that sends none of `limit` / `cursor` /
`offset` is treated as a client built before paging (the Android app
installed before 2026-10-11): `GET /api/videos/list` and
`GET /api/files/browse` answer with every entry as before (Videos at
most 2,000 per library, the bound such a client was built against) plus
`total` and `has_more: false`;
`GET /api/audiobooks`, `GET /api/podcasts/subscriptions` and
`GET /api/podcasts/subscriptions/{id}/episodes` answer with the bare JSON
array they always did. `GET /api/music/library` was always paged (default
50) and still takes `offset`.

**Filesystem lists are cached.** Videos and Files have no index: the
server keeps each library walk / folder listing between requests
(`listing_cache.py`) and checks, on every request, the mtime of each
folder it read (~0.5 ms for 100 folders). A file added, removed or renamed
anywhere it read, a write through this server into the library (upload,
delete, move, import, a document save), `refresh=true`, or five minutes'
age re-reads it (a listing that read more than 2,000 folders re-checks
them at most every 2 s). Memory: a budget of 256 MB estimated at ~600 B a
row plus ~200 B a row per sorted view; past it the least recently used
idle listings go, and a listing read in the last two minutes is never
evicted for room — one library bigger than the whole budget stays cached
while it is being scrolled instead of being re-walked per page.

**No count caps.** A library is walked whole (owner decision 2026-10-12).
The one remaining limit is time: a walk still running after 2 minutes
stops, keeps what it found, and the answer says `truncated: true`,
`truncated_reason: "time"`. Measured on synthetic trees (Windows dev box,
other agents' test runs also loading it): 10,000 videos ~0.1 s cold (was
~2 s), a 10,000-entry folder ~0.13 s (was ~6 s); 50,000 videos in 500
folders ~1.0–1.5 s for the cold first page and ~50 ms for each later page
or a search's page (end to end through the app, ~28 MB cached), a
50,000-entry folder ~1.0–1.2 s cold and ~45 ms later (~38 MB). The walk,
the sort and a search's scan run in worker threads, never on the event
loop.

---

## 2. Core API (`:6370`)

### 2.1 Health and introspection

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/alive` | Open | — | Since 2026-10-15. `{alive: true, pid}`, answered by the event loop itself, with no database and nothing else: liveness only. A core whose database is down still answers it (and `/v1/health` gives its `503`); a frozen core doesn't. What the dashboard's **Restart the core** asks (`/api/config/core/restart` below). |
| `GET /v1/health` | Open | `challenge` (optional) | `{"status":"ok","bot_name","use_stubs","stt","identity"}`; `503` when the DB is unreachable or no handlers registered. Liveness probe. `stt` is the speech-recognition state — `ok`, `fallback` (the configured Whisper didn't load and `whisper_cpu_fallback_model` is transcribing on cpu/int8), `unavailable` (nothing loaded; the core runs without STT and a spoken turn gets a "can't understand speech" reply), `stub` or `not_loaded`. It never makes health fail; the details are on `GET /v1/admin/hardware`. `identity` is `{algorithm:"ed25519", fingerprint:"SHA256:…", public_key}` — this install's server identity. Pass `?challenge=<nonce>` (≤128 URL-safe chars, `400` beyond or on whitespace) and the block also carries that `challenge` back plus a `signature` over it, which is how a satellite tells this household's core from anything else listening on 6370. Add `&addr=<host:port>` — the address the caller dialed — and the block carries `addr` back with the signature covering it, but only when that address is one of this server's own (an interface address, loopback, or a name or address listed in `TRUSTED_HOSTS`; names are never resolved to decide); otherwise the block carries `addr_refused` and no signature, and the core logs the refusal with the `TRUSTED_HOSTS` advice. That binding is what stops a LAN host that relays this request from passing the answer off as its own. A `challenge` without `addr` is still answered the old way for satellites on earlier code (logged once per start). The pre-identity fields are unchanged. |
| `GET /v1/time` | Open | — | `{tz, epoch, iso, utc_offset_sec}` — this host's IANA zone name (`null` if the host cannot name it) and its clock. Satellites copy both on every connect, and stage 2 of a prepared card calls it before anything else, through the root helper `domovoi-sync-time`; a Pi has no battery clock and Pi OS boots in Europe/London. The helper takes the address from its root-owned pin (`/etc/domovoi/server.url`) rather than from the unprivileged caller, and where a fingerprint is pinned it makes the server sign a nonce and the address it dialed first (`/v1/health?challenge=&addr=`). |
| `GET /v1/stats/latency` | **Device read** | `since` (ISO 8601; no offset = UTC; default the last 7 days), `room` (≤120 chars) | Where recent voice turns spent their time, from `intents_log.timings` (V015). `{since, room, limit, turns, stages, paths, whisper_seen, whisper, speculative, early_commit, stt_window, capture_timing, fastlane?}`. `stages` has one entry per stage — `capture_audio_ms`, `endpoint_silence_ms`, `stt_ms`, `stt_wait_ms`, `identify_ms`, `route_ms`, `tts_first_ms`, `total_ms`, `speech_to_reply_ms` — each `{count, p50, p95, max}` in whole milliseconds (linear-interpolation percentiles; `count: 0` and nulls for a stage nobody recorded), over the most recent `limit` (1000) timed turns at or after `since`. `turns` counts those rows; `paths` is how they routed (`{"fast": 12, "qa": 3}`); `whisper_seen` lists the Whisper settings they ran on, `[{model, device, compute_type, cpu_threads, turns}]`, most turns first — more than one entry means the window spans a settings change; `whisper` is what is transcribing now, `{state, model, device, compute_type, cpu_threads, short_window}` (`cpu_threads` null off the cpu; `short_window` true when captures of up to 9 s decode on the 10 s window — `whisper_short_window_enabled` on and the loaded CTranslate2 able to — false when not, null with no real model loaded); `speculative` is how the transcripts started at a pause fared, `{turns, reused, decodes}` — turns that had one, how many of those used it, speculative decodes started; `early_commit` counts the captures the core ended early, `{turns, A, B, cut_in, watched}` — by tier, how many of them the person was still talking at (`cut_in`), and how many had a satellite that listened on after the capture to find out (`watched`, satellites from 2026-09-30 on); for the others `cut_in` is a lower bound, since it only sees the frames already on their way when the capture ended (30-90 ms). `stt_window` is `{short, full, rechecked}`: turns whose transcript was decoded on the 10 s window, and on faster-whisper's 30 s one (a longer capture, the setting off, a short decode that came back blank or unsure and fell back, or a second hearing), and how many turns were heard a second time (`whisper_short_window_recheck`: a short-window transcript bound for the tool router, decoded again on the 30 s path — counted under `full` when that text was used, under `short` when the second hearing heard nothing usable). `capture_timing` is `{count, p50, p95, max}` per capture-clock key — when a turn's frames, its pause and its speculative decode happened against the pace the audio was spoken at: `sat_wake_ms`, `sat_start_backlog_ms`, `sat_pause_ms`, `sat_pause_backlog_ms`, `sat_end_backlog_ms` (the satellite's own clock; only from satellites that send it), `frame_lag_first_ms`, `frame_lag_max_ms`, `pause_rx_lag_ms`, `end_rx_lag_ms`, `pause_net_ms` (signed), `pause_to_decode_ms`, `decode_wait_ms`, `decode_start_ms`, `stt_decode_wait_ms` ([the capture clock](uml/satellite-protocol.md#the-capture-clock); reading them: [CPU_HOST.md](CPU_HOST.md#when-the-early-transcript-starts-late)). `fastlane`, present only while `fastlane_mode` is on or the window holds fast-lane shadow records, counts them: `{mode, state, model, observed, would_commit, agree, disagree, missed, preempted, speech_after_commit, commit_ms, lead_ms, cpu_ms}` — `observed` turns the lane followed, `would_commit` the ones it would have acted on, of which `agree` / `disagree` matched Whisper's command or not, `missed` turns Whisper heard as a committable command the lane didn't catch, `preempted` such turns the core's early commit ended before the lane's hold ran out (not counted as missed), `speech_after_commit` commits the person talked on past; `commit_ms` (last voiced frame to the lane's decision), `lead_ms` (how long before `utterance_end` it came) and `cpu_ms` (the lane's decode time per turn) are `{count, p50, p95, max}` ([CPU_HOST.md](CPU_HOST.md#the-streaming-fast-lane)). Stage meanings are in [CPU_HOST.md](CPU_HOST.md#measuring-turn-latency). Numbers only — no transcript, person or session — but a paired device's read since 2026-10-08: a turn count for one room over a short window says somebody just spoke there ([SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#daily-tier-lan-trust)). `401` with no credential; `422` for a malformed `since`; `503` when the database is unreachable or V015 is missing. |
| `GET /v1/connectivity` | Open | — | `{online, last_checked_at, last_online_at, target, policy, reason}` — the internet-connectivity probe the offline-first router consults. `policy` is the internet answer (`""` \| `always` \| `sometimes` \| `never`); `reason` is `connected`, `offline` (the probe's dial failed) or `turned_off` (the answer is `never`: the probe doesn't dial, and `online` is `false`). |
| `GET /v1/handlers` | Open | — | List of `HandlerInfo`: `{name, requires_network, tool_schema, fast_path_count, priority_band, origin, display, example_phrases}`. `origin` is `"core"` or a plugin slug. `example_phrases` is what a person can say to use the handler: its own `example_phrases` (`Handler.example_phrases`, kept out of the tool description the routing model reads), else the quoted phrases of a trailing `Example: '…'` in a plugin's tool description. Every core handler lists some since 2026-10-17 (the list was empty: the descriptions they were read from had lost them); `chat_mode` lists its phrase only while chat mode is on. Powers the dashboard's manual page, Home's first-run hint and the spoken "what can you do". |
| `GET /v1/voice/activity` | **Device (`X-Device-Token` or Bearer)** | — | Whether a voice turn is being answered now (`domovoi/voice_activity.py`): `{busy}` and nothing else. A turn counts from speech to text to the end of its answer, for a satellite's utterance, the app's chat mic and `POST /v1/intent`; one open longer than 300 s no longer counts. The web process's document jobs ask before every model call and wait while `busy`, so a voice turn waits at most for the one call running (about a minute per part on a CPU-only box). |
| `POST /v1/intent` | **Device (`X-Device-Token` or Bearer)** | `{transcript, room_id?, session_id?, synthesize?}` | Routes a text utterance through the full intent pipeline. Returns the `Response` JSON (`{text, session_id, matched_handler, matched_path, online, data, music_action, music_stream_url, ...}`); with `synthesize: true` returns `audio/wav` bytes instead, with the text and metadata in `X-Response-Text`, `X-Session-Id`, `X-Matched-Handler`, `X-Matched-Path`, `X-Online` headers. |
| `POST /v1/phone/lookup` | **Device (`X-Device-Token` or Bearer), no pre-setup grace** | `{"query": "...", "internet": true, "limit": 3}` or `{"url": "https://..."}` (one of the two; `query` at most 300 characters, `limit` 1-3) | Since W1 (2026-10-21). A lookup for a chat (the phone app at home through `POST /api/chat/lookup`, and the dashboard's chat): the sources in the core's order, this server's own first (its manual, an offline library: class `local`), then Wikipedia and Wikidata through the search helper (`api`), then the open web or the household's own search (`web`), asking no further once it has `limit` results. `internet: false` asks only the `local` sources (no permission needed, works under `never`). `url` reads that page: HTTPS only, no cookies, no JavaScript, 2 MB at most, its main text. Answers `{kind, query, results: [{title, url, site, snippet, extract, source, source_name, source_class, licence, section}], internet_used, notice, destination}`; `notice` is a plain sentence when the household's own search failed and Wikipedia answered instead. `409` internet-off (§1.6) under `never` for anything that would leave the house (nothing sent); `422` `{detail, code}` for a page that isn't HTTPS, isn't public, is over 2 MB or isn't HTML or text (codes `not_https`, `bad_address`, `not_public`, `too_large`, `not_a_page`, `empty`, `unreachable`, `http_error`), or for neither or both of `query` / `url`; `429` (`too_many`) past 30 lookups a minute for everyone together, or 10 for one credential (the household token, normalised, is one credential: phones and browsers sharing it share its 10; a local-only search isn't counted); `422` `too_slow` when a page's text took longer than 5 s to read (its reader process is killed); `501` before first-run admin setup. The AI switch doesn't apply. See `domovoi/lookup_web.py`. |
| `GET /v1/lookup/sources` | **Device read** | — | The lookup sources in the order they are asked: `{sources: [{slug, registered_as, name, lookup_class, needs_permission, core}], order, always_allow, own_search, destination, internet}`. Names no key and no address. |
| `POST /v1/phone/voice` | **Device (`X-Device-Token` or Bearer), no pre-setup grace** | multipart: `audio` (16 kHz mono PCM16, raw or a WAV of that format, at most 30 s), `phone_id` (the app's device id), `thread_id`, `player` (optional JSON: the phone player's `{title, artist, kind, playing}`), `speak` (optional, default `true`; `false` when the phone speaks the reply itself in its own voice pick: no text-to-speech here and `audio` is `null`) | One push-to-talk turn from the app's chat mic, answered like a satellite turn: speech-to-text, voice identification, the router satellites use, text-to-speech. A leading wake phrase ("Hey Domovoi, …", however speech-to-text spells the name) is taken off before routing, as a satellite's transcript never holds one; `transcript` and the thread keep what was said. Never registers a satellite session (no room's Pi is evicted); logged under `phone-<phone_id>`. Writes both turns into chat thread `thread_id` (`source: "voice"`, the speaker on the user row) and fires `chat_changed`. Answers `{heard, reason, transcript, reply, audio: {mime: "audio/wav", b64} \| null, speaker: {person_id, name, confidence, confident} \| null, expect_followup, actions, matched_handler, matched_path, session_id, room_id, messages: [user row, assistant row], error}`. `heard: false` (`reason` `too_short` under 0.3 s, `blank` when nothing was said) writes nothing. `actions` is what the phone itself should do — `play` (with `items`), `pause`, `resume`, `stop`, `next`, `previous`, `volume` — because a phone is its own device: media plays on it, timers and reminders set from it are house-wide, a satellite's settings (Wi-Fi, its voice) are refused aloud, and it is never handed a room's music player. The routing session is kept per phone and thread, so a question Domovoi asks is answered by the next press (`expect_followup: true`; "yes" resumes it). Voice identification matches each person's phone samples when they have any, else their satellite samples, and never learns from a phone turn; "my name is X" for an X who exists asks "Add this phone's voice to X?", and a "yes" as the very next press (within 60 s; any other turn drops the question) adds the clip to X's phone samples (at most `VOICE_PROFILE_PHONE_SAMPLE_CAP`, default 3, oldest dropped) instead of enrolling a second X; with two people of that name it refuses rather than guess; a new name enrols into the phone set (V026). An enrolment question is answered only where it was asked: a "yes" from anywhere but the same phone's chat mic (say, `/v1/intent` with the phone's `session_id`) is refused. A failed turn is still written, with `error: {detail, code: "turn_failed"}`. `404` unknown thread, `409` the phone's previous turn is still running, `400` an unreadable WAV, `413` over 30 s, `415` a WAV of another format, `422` a malformed `phone_id` or a `thread_id` outside 1..2^63-1, `500` (`save_failed`) when the answered turn could not be written, `503` speech recognition not running, `501` before first-run admin setup (as the drop-in socket). The app reaches it through `POST /api/chat/threads/{id}/voice`. See `domovoi/phone_voice.py`. |

### 2.2 File-sync channels (pulled by satellites)

While the container guard's freeze is on (§1.4, docs/CONTAINER_GUARD.md),
every `/v1/satellite-code/*` and `/v1/satellite-plugins/*` route answers
`423` with no detail, so a satellite upgrade gets nothing to install; the
sounds and wake-model channels keep working.

Three parallel manifest+file channels. A satellite hash-compares the manifest
against its local cache and downloads only changed files, verifying each
body's sha256 against the manifest.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/sounds/manifest` | Open | `?voice=<name>` (optional; defaults to the registry default voice) | `{relative_path: sha256}` for every rendered MP3 in that voice's subtree (greetings, `network_issues.mp3`, `sample.mp3`). |
| `GET /v1/sounds/manifest.sig` | Open | `?voice=` optional | The same clip list inside the signed envelope described under `/v1/satellite-code/manifest.sig`, with `channel: "satellite-sounds"`. A pinned satellite asks for this and refuses a list its own server did not sign — these are the clips the room says — and checks every body against the list before writing it. |
| `GET /v1/sounds/{path}` | Open | `?voice=` optional | Serves one rendered clip (`audio/mpeg`). Locked to `.mp3` files strictly inside the voice subtree (no traversal). |
| `GET /v1/satellite-code/manifest` | Open | — | `{relative_path: sha256}` for allowlisted files under `satellite/` (`.py .toml .txt .md .service .sh .ps1 .json .example`; never `__pycache__`, `.pyc`, `.bak`, `.env*`). Basis for in-field satellite upgrades. |
| `GET /v1/satellite-code/manifest.sig` | Open | — | The same list inside a signed envelope: `{algorithm, fingerprint, public_key, channel:"satellite-code", manifest, issued_at, serial, signature, signature_v2}`. `signature` is over the channel and the list (what satellites on earlier code verify); `signature_v2` is over the channel, the list, `issued_at` (epoch seconds) and `serial` — a per-channel number that only ever grows when the list changes. A satellite prepared with this server's fingerprint asks for this instead, installs nothing if `signature_v2` does not verify, and refuses an envelope whose `serial` is behind the last one it accepted, so a recording of a genuine older list cannot roll it back. The manifest travels INSIDE the envelope so the list that was signed and the list that is used are the same object. The unsigned `manifest` above keeps serving devices prepared before server identities. |
| `GET /v1/satellite-code/bundle.tar.gz` | Open; `423` while the container guard's freeze is on | — | The same files as `/v1/satellite-code/manifest`, under `satellite/`, as one deterministic tar.gz (sorted, times and owners zeroed, no gzip time): what a spare computer's install command downloads (Settings → Rooms → add a spare device, 2026-10-13). The command runs it only when its SHA-256 matches the one the dashboard quoted. `X-Content-SHA256` carries the digest. Declared before the catch-all. |
| `GET /v1/satellite-code/bundle.sha256` | Open; `423` while frozen | — | `{sha256, size, files, path}` of that bundle: what the dashboard writes into the install command. |
| `GET /v1/satellite-code/{path}` | Open | — | Serves one allowlisted satellite source file (`application/octet-stream`). Traversal-guarded. |
| `GET /v1/satellite-windows/files/{name}` | Open; `423` while frozen | — | One file of the Windows satellite's runtime (2026-10-13): the embeddable Python or a wheel, served only when `satellite/windows-runtime.json` lists `name` and the cached bytes still match its SHA-256 and size; `404` otherwise (the installer then fetches the same pinned file upstream). |
| `GET /v1/admin/satellite-windows` | **Admin read** | — | `{ready, python, files, missing, bytes_total, bytes_cached, fetch}`: whether this server holds every file of the Windows runtime, and the last fetch. |
| `POST /v1/admin/satellite-windows/fetch` | **Admin** | — | Starts fetching the missing files from python.org / PyPI through the outbound gate, each held to its pin; answers at once with `started` and the status. Refused (the status says why) under `INTERNET_ACCESS=never`. |
| `POST /v1/admin/satellite-setup/codes` | **Admin** | `{payload}` | A setup code for a room's enrolment payload (2026-10-21, the Windows satellite Setup): `{code, code_display, expires_at, server, file_name, download_path, short_path}`. Live 24 hours in the core's memory; the same payload while it lives gets the same code, renewed. `422` for a payload the devices would refuse. Not a credential (docs/SECURITY_PRIVACY.md). |
| `GET /v1/satellite-setup/codes/{code}?challenge=` | Open; `423` while frozen | — | What the Setup gets for a live code (case, dashes, O/I/L ignored; the code is 60 bits of the payload's SHA-256): `{format: 1, code, payload, core, room, room_id, fingerprint, bundle_sha256, bundle_size, bundle_path, expires_at}`, the payload and the bundle digest the PowerShell command carries. With `challenge` (32 lower-case hex digits) also `{challenge, algorithm: "ed25519", public_key, signature}`: the identity key's signature over `domovoi-satellite-setup-v1\n<challenge>\n<code>\n<payload>\n<bundle_sha256>`, which the Setup verifies. `404` for an unknown or expired code, `422` for a malformed challenge. |
| `GET /v1/satellite-setup/download/{code}` | Open; `423` while frozen | — | `DomovoiSatelliteSetup.exe`, named `DomovoiSatelliteSetup_<code>_<host>_<port>.exe` (the code only for an https or IPv6 server) so the Setup reads them back. `404` with a plain sentence for an expired code or when this server doesn't hold the Setup. `GET /win/{code}` is the same, at an address short enough to type. |
| `GET /v1/admin/satellite-setup` | **Admin read** | — | `{ready, file, size, sha256, release, bundled, fetched_at, windows_install, fetchable, internet_off, fetch}`: whether this server holds the Setup and where it came from (`bundled`: the Windows server's own Setup brought it); `fetchable` the release a fetch would take it from (a Linux server, a release on GitHub that has it, looked up at most every six hours, never under `INTERNET_ACCESS=never`), else `null`. |
| `POST /v1/admin/satellite-setup/fetch` | **Admin** | — | Starts fetching the Setup from the GitHub release `android/release.json` names (else the latest) through the outbound gate, kept only if that release's `SHA256SUMS` lists its SHA-256; answers at once with `started` and the status. Refused under `INTERNET_ACCESS=never` and on a Windows install (whose own Setup brings it); a failure is said in plain words, the details go to the log. |
| `GET /join` | Open | — | The page a phone's camera opens from the spare-device QR code, and the "get the Domovoi app" page for household phones (2026-10-15, `domovoi/join_page/`): static HTML whose script (`/join/join.js`, `/join/join.css`; nothing inline, its own `Content-Security-Policy`, `Referrer-Policy: no-referrer`) reads the room code from the URL **fragment** (never sent to the server), offers the APK from this server, then opens the app as the room (an Android `intent:` link to `domovoi-room:`, package `com.domovoi.app`, falling back to `/join?app=missing`). The app's update notice opens it as `/join?abi=arm64-v8a|armeabi-v7a&update=1` (2026-10-16): that APK first when the server holds it, worded as an update. Not in the OpenAPI spec. |
| `GET /v1/admin/usb-stick` | **Admin read** | — | Old computers' room sticks (2026-10-16, `domovoi/usb_stick/`): `{debian: {version, name, size, cached, shim_signatures}, refusal: {reason, message} \| null, internet_off, disk_free, need_bytes, job, builds}`. `refusal` is why **Prepare the stick** can't start now (`busy`, `no_xorriso`, `internet_off` while Debian's image isn't cached, `disk`); `job` is `{running, stage (download\|check\|build), room, room_id, started_at, finished_at, ok, message, reason, bytes_done, bytes_todo, build}` (the last failure stays until the next try); `builds` the finished sticks, newest first, each `{id, room, room_id, made, size, sha256, debian, wifi, spoken_code, image_url, os_list_url, manifest_url}`. |
| `POST /v1/admin/usb-stick/build` | **Admin**; `423` while frozen | `{payload}` | Starts preparing a stick for the room code `payload` (the spare-device enrolment payload): Debian's netinst from its pinned URLs the first time (cdimage.debian.org, through the outbound gate, kept only at the pinned size and SHA-256), then the room's image (`xorriso`). The code must carry this server's own fingerprint (`422 {reason: not_this_server}`); `422` for one the room couldn't use; `409 {reason, message}` for a refusal above. Answers at once with `started` and the status. |
| `DELETE /v1/admin/usb-stick/builds/{build_id}` | **Admin** | — | Removes a finished stick's image (about 800 MB); the status. `404` for an unknown id. |
| `GET /v1/usb-stick/{build_id}/domovoi-room-stick.img` | Open; `423` while frozen | — | One stick's image (an attachment), by its random 32-character id; `404` for an unknown id or an image whose size changed. Open because Raspberry Pi Imager and a browser download can't send a credential, and nothing on a stick is secret (SECURITY_PRIVACY.md, "Old computers: the room stick"). |
| `GET /v1/usb-stick/{build_id}/os-list.json` | Open; `423` while frozen | — | Raspberry Pi Imager's custom list for that stick: one device (`A computer`), one image (`Domovoi room stick`) with its `url`, `extract_size`, `extract_sha256` (Imager checks it while writing); no `init_format`. |
| `GET /v1/usb-stick/{build_id}/domovoi-room-stick.rpi-imager-manifest` | Open; `423` while frozen | — | The same list as a file Imager opens on a double-click (`application/vnd.raspberrypi.imager-manifest+json`, an attachment). |
| `GET /v1/android-app` | Open | — | The app release this server hands out: `{release: {version_name, version_code, cert_sha256} \| null, apks: [{abi, file, size, ready, path}], ready}`; `release` is null before the first release (`android/release.json`), `ready` per APK when this server holds a copy that passes every check. What the join page reads. |
| `GET /v1/android-app/files/{name}` | Open; `423` while the container guard's freeze is on | — | One release APK (`application/vnd.android.package-archive`, as an attachment), served only when `android/release.json` names `name` and the cached copy passes `domovoi/android_app.py` `check_apk`: its size and SHA-256, its own v2 (and v3) signature verified, by the release key whose certificate SHA-256 the manifest pins, with libraries for that ABI only. `404` otherwise. |
| `GET /v1/admin/android-app` | **Admin read** | — | The public view plus `{missing, bytes_total, bytes_cached, upload_max, internet_off, fetch}`; `fetch` is `{running, started_at, finished_at, ok, message, bytes_done, bytes_todo}` (the cards' progress, and the last failure until the next try). |
| `POST /v1/admin/android-app/fetch` | **Admin**; `423` while frozen | — | Starts fetching the missing APKs from the release's GitHub URLs (only `https://github.com/coders-farm-official/domovoi/releases/download/<tag>/<file>`, through the outbound gate), each kept only if it passes `check_apk`; answers at once with `started` and the status. `409` before the first release; refused (the status says why) under `INTERNET_ACCESS=never`. |
| `POST /v1/admin/android-app/upload` | **Admin**; `423` while frozen | multipart `file` | An admin hands the server one of the release's APKs (a house with internet access off): kept only if its size and SHA-256 are one the manifest names and it passes `check_apk`; answers `{accepted, abi, …status}`. `422 {reason, message}` otherwise (`unknown`, `signature`, `cert`, `abi`); `413` when bigger than any of the release's files; `409` before the first release; `415` unless multipart. The body is read only after the admin gate (a refused caller's upload is never read). |
| `GET /v1/satellite-linux/files/{name}` | Open; `423` while frozen | — | The Linux satellite's own Python (2026-10-15): python-build-standalone's CPython for one machine, served only when `satellite/locks/linux-python.txt` lists `name` and the cached bytes still match its SHA-256 and size; `404` otherwise (the installer then fetches the same pinned file from GitHub). Used by `install-pc.sh` on a computer whose own `python3` won't do. |
| `GET /v1/admin/satellite-linux` | **Admin read** | — | `{ready, pythons: [{machine, version, size}], files, missing, bytes_total, bytes_cached, fetch}`: whether this server holds the Linux satellite's Python for every machine the lock names, and the last fetch. `503` when the lock is unreadable. |
| `POST /v1/admin/satellite-linux/fetch` | **Admin** | — | Starts fetching the missing files from python-build-standalone's GitHub releases through the outbound gate, each held to its pin; answers at once with `started` and the status. Refused (the status says why) under `INTERNET_ACCESS=never`. |
| `GET /v1/wake-models/manifest` | Open | — | `{relative_path: sha256}` for trained wake-word models (`.onnx` / `.onnx.json`) under the server's wake-models dir. |
| `GET /v1/wake-models/manifest.sig` | Open | — | The same model list inside the signed envelope, with `channel: "satellite-wake-models"`. A pinned satellite takes only a list its own server signed: the model is what the room listens for, and its body is parsed in the client. |
| `GET /v1/wake-models/{path}` | Open | — | Serves one wake-model file. Extension-allowlisted and traversal-guarded. |
| `GET /v1/wake-base/manifest.sig` | Open | — | For a phone in room mode: `{name: sha256}` of the openWakeWord base models (melspectrogram, embedding, the stock wake words, Silero VAD) inside the signed envelope, `channel: "satellite-wake-base"`. Lists only pinned `.onnx` files (`OWW_MODEL_FILES`) the satellite media cache holds with their pinned size and digest; empty until Settings → Rooms → prepare satellite media → Refresh caches has run. Signed only (domovoi/wake_base_models.py). |
| `GET /v1/wake-base/{name}` | Open | — | One model the list names; `404` for anything else. |

### 2.3 Capabilities, plugins, and observability

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/capabilities` | Open | — | `{"capabilities": {<slug>: [provider, ...]}}` — the live capability registry (what the dashboard "+ add" affordances and the Android app gate on). |
| `GET /v1/plugins` | Open | — | `{"plugins": [{slug, name, version, publisher, enabled, bundled, install_source, status, last_error, installed_at, updated_at, pending_restart}]}` — the plugin registry rows. `pending_restart` is `{slug, from_version, to_version, since, where}` for an upgrade staged for the next restart (see confirm below), else `null`. `worker_notes` (since 2026-10-15) is what the plugin's running workers say about themselves, `[{worker, note}]` (a note at most 300 characters; the radio fingerprinter's pause after failures in a row, say), `[]` when they say nothing. |
| `GET /v1/plugins/{slug}/status` | Open | — | Per-plugin live status: registry row + handlers registered under the slug + live worker and startup-hook state (`workers`, `startup_hooks`) from the shared worker registry, plus `pending_restart` as in `GET /v1/plugins` (handlers and workers are empty while an upgrade waits for its restart). Each worker carries `note` (since 2026-10-15): its own word on its state, or `null`. `404` for an unknown slug. This is also where **worker observability** lives — there is no separate `/v1/admin/workers` endpoint. |

Plugin lifecycle (from `domovoi/plugins_runtime/installer.py`; all **Admin,
fail-closed** — `501` until first-run setup, Bearer-only after). Install and
upgrade are two-phase: stage → preview → confirm. Install (stage and
confirm), upgrade (stage and confirm) and enable answer `423` while the
container guard's freeze is on (§1.4); disable and uninstall don't. The
preview carries `containers` (the `[[containers]]` the plugin declares:
`{id, name, image, pinned, required, label, allow}`) and
`containers_unwatched` (it needs Docker and declares none).

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `POST /v1/plugins/install` | Admin, fail-closed | multipart zip in field `file`, **or** JSON `{"github_url": "..."}` | Phase A: stage + validate. Returns `{staged_id, preview}` — the trust screen: `{slug, name, version, publisher, license, description, permissions, requirements: {direct, transitive: [{name, version, hashed, origin, origin_ok}]}, handlers, migration_count, capabilities, open_endpoints: [{method, path, module, function, process}], device_endpoints: [same shape], satellite: {apt_packages, post_install, pip_requirements, files_count, payload_mb} \| null, trust_statement}`. `422` with `detail.error.code` on rejection: `endpoint_tier_conflict` (a route decorated both `@open_endpoint` and `@device_endpoint`), `lockfile_option` (a global pip option in the lockfile), `lockfile_requirement` (a line that is not an exact `name==version` pin — direct URLs, local paths, ranges), `internet_off` (a GitHub install, or a dependency that would have to be downloaded, while the server is answered `never` — §1.6), plus the zip / manifest / layout / migration-lint codes. |
| `POST /v1/plugins/install/{staged_id}/confirm` | Admin, fail-closed | — | Phase B: run pip + plugin migrations + hot-load. Returns `{installed, loaded, slug, version, status, restart_required}` (`loaded: false` with `status: "load_error"` and `error` when the contract checks refuse the load). Also confirms staged *upgrades* (adds `upgraded_from`): an upgrade of a plugin the core has already imported is **staged for restart** — migrations applied, files swapped, row written, not hot-loaded (Python would hand back the old module) — and answers `loaded: false, restart_required: true`; the upgrade is then listed in `GET /v1/admin/version` → `plugins_pending_restart` until the next restart loads it. A fresh install of a plugin whose code the core imported earlier in the same process (uninstalled, then installed again) is staged the same way, at any version. An upgrade keeps the plugin's enabled state: a disabled plugin stays disabled and unloaded (`enabled: false, loaded: false`; `restart_required` only when the core still holds its old code, which a later enable would otherwise re-register). A migration problem rolls the install back with `422` and `detail.error.code` `migration_drift` (an applied migration file changed on disk), `migration_lint` or `migration_failed`; the message names the file. When an *upgrade* fails after some of its migrations ran, those stay applied (migrations never run backwards) and the previous version is restored to run against them; the error's `detail.error.details.migrations_kept` names them per database, `{"<database>": ["V002__x.sql", ...]}`, and the Plugins page shows the list. One plugin step runs at a time: a confirm, an upgrade, an uninstall and an install preview's dependency check wait for each other (two pip runs against the core's environment at once corrupt each other's bookkeeping), and a second confirm of a staged id whose confirm is still running answers `409` with `detail.error.code` `confirm_in_progress` instead of running it twice. |
| `POST /v1/plugins/{slug}/enable` | Admin, fail-closed | — | Enable and hot-load a disabled plugin. Runs the migration catch-up first; a refusal or failure there is a `422` with the same `migration_*` codes as confirm, and the plugin stays disabled. While an upgrade of the plugin waits for its restart, enable marks it enabled without loading and answers `{enabled: true, slug, restart_required: true}`. |
| `POST /v1/plugins/{slug}/disable` | Admin, fail-closed | — | Disable: unload handlers/workers; the plugin's HTTP routes start returning `404`. |
| `POST /v1/plugins/{slug}/uninstall` | Admin, fail-closed | `{"data": "keep" \| "purge"}` (default `keep`) | Remove the plugin; `purge` also drops its Postgres schema. |
| `POST /v1/plugins/{slug}/upgrade` | Admin, fail-closed | zip or `{"github_url", "force"?}` | Stage an upgrade (returns `{staged_id, preview}`; confirm via the shared confirm endpoint). Dev-mode installs refuse upgrade. A bundled plugin refuses with `422` `bundled_plugin` ("bundled plugins update with the core"): it lives in the Domovoi checkout and updates with a pull and restart, and an upgrade would move its folder out of the checkout. Install refuses a zip whose slug is a bundled plugin the same way. |

### 2.4 Acquisitions (generic media queue)

The acquisition queue exists even with no media-provider plugin installed —
rows wait `pending` and responses carry graceful-absence copy; installing a
fulfiller later drains the backlog.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/acquisitions` | Open | `?status=&limit=50` (max 200) | `{"acquisitions": [...], "fulfillers": [...], "can_fulfill_query", "can_fulfill_url"}` — rows from `media_acquisitions`, newest first. |
| `POST /v1/admin/music/add-by-query` | **Device (`X-Device-Token` or Bearer)** | `{room_id, query, artist?, attach_to_playlist_id?}` | Enqueue a free-text acquisition. Returns `{queued, outcome, message, already_in_library, already_downloading, acquisition_id, fulfiller_available, title}`. |
| `POST /v1/admin/music/add-by-url` | **Outbound-fetch** | `{room_id, url, title?, dedup_key?, attach_to_playlist_id?}` | Enqueue an acquisition for an exact external URL (skips fuzzy library dedup; honors `dedup_key`). Gated because it triggers provider code against a caller-chosen URL. Whoever asks — an admin session included — the URL also has to pass the outbound-URL check in store mode (§1.1): `400` for a non-http(s) scheme, `localhost`, or a host that is or resolves to a loopback / private / link-local address. `409` with `X-Domovoi-Refusal: internet-off` under the **No** internet answer (§1.6), before anything is queued. |

### 2.5 Admin: live state and satellite actions

Used primarily by the web backend (a separate process that can't read the
core's `app.state`). Unlabeled endpoints here are **Open** — the v1 LAN-trust
posture; the specifically dangerous ones carry the Bearer gate.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/admin/snapshot` | Open | — | Process-state snapshot for the dashboard's poll loop: `{active_rooms, resumable_music, wifi_status, now_playing, current_playlist, active_dropins, satellite_full_duplex, satellite_sat_type, satellite_mic_enabled, satellite_supports, satellite_board, satellite_display, satellite_voice, satellite_volume, satellite_synced_sha, satellite_upgrades, satellite_health, domovoi_version, home_problems_visibility, music_alias_fetch, lyrics, lyrics_index}`. `satellite_health` (2026-10-09) is a per-room DIGEST — `{received_at, seq, outage_reported_at}`, when the latest health sample and the last outage report arrived — never the sample or the report themselves, which are device read (`GET /v1/admin/satellite/{room_id}/health`) because they say whether a turn or call is open, the SSID and the gateway. `satellite_supports` / `satellite_board` (2026-10-12) list, per connected room, the abilities its `hello` declared and the board it named — only for a device that sent them, so a Pi is absent from both. `satellite_upgrades` (2026-10-13) is, per room, a satellite code upgrade under way or finished within the last 10 minutes — `{state, expected_sha, from_sha, back_sha, started_at, deadline_at, finished_at}`, `state` one of `running` \| `done` \| `rolled_back` \| `no_news` (domovoi/satellite_upgrades.py); in memory only. `home_problems_visibility` is the LIVE value of that one setting, which the web's `GET /api/config` reads from here (a config save changes only the core's copy). `music_alias_fetch` is the opt-in MusicBrainz "also called" lookup's STATUS only — `{enabled, state, last_tick_at, last_request_at, last_error, rate_limited_until, artists_total, artists_due}` (`state`: `off` \| `offline` \| `rate_limited` \| `running` \| `idle` \| `done` \| `error`; `last_error` an error type or a short code) — never an artist name or an alias; `GET /api/music/aliases/status` adds the database counts. `lyrics` is the lyrics workers' STATUS only (§3.5c) — `{scan: {state, last_tick_at, last_pass_at, tracks, unscanned, read_last_pass, last_error}, fetch: {enabled, write_lrc, state, last_tick_at, last_request_at, due, rate_limited_until, paused_until, last_error}}` (`fetch.state`: `off` \| `internet_off` \| `offline` \| `rate_limited` \| `paused` \| `running` \| `idle` \| `done` \| `error`), and `lyrics_index` the lyric-search index's — `{state, pending, indexed, last_tick_at, last_error, search_enabled}` (`search_enabled` the live "Find songs by their words" setting); never a title or a lyric. `GET /api/music/lyrics/status` adds the database counts. |
| `POST /v1/admin/announce` | **Device (`X-Device-Token` or Bearer)** | `{room_id?, message}` (1–500 chars) | Speak `message` on one satellite, or all when `room_id` is null. `{"announced_to": [rooms]}`; `503` if nothing is connected, `404` for an unknown room. |
| `POST /v1/admin/dropin/start` | **Admin, security tier** | `{initiator_room, target_room}` | Open a two-way drop-in between two connected, AEC-capable rooms — a live microphone bridge nobody in either room was asked about, so it takes an admin Bearer, and answers `501` before setup (no pre-setup grace for a live microphone). `400` same room, `404` room offline, `409` disabled / no AEC / already in a call. Under `DROPIN_ACCEPT_MODE=ring` it returns `{"status": "ringing"}` and opens nothing until the target room answers. |
| `POST /v1/admin/dropin/end` | **Device (`X-Device-Token` or Bearer)** | `{room_id}` | Hang up whatever call the room is in. `404` when not in a call. |
| `POST /v1/admin/satellite/restart` | **Admin (Bearer)** | `{room_id}` | Ask a connected satellite to restart its own service. `503`/`404`/`502` as above. `409` when the device's `hello` declared `supports` without `restart` (spare-device satellites, see docs/uml/satellite-protocol.md "Declared abilities"); a Pi never gets it. Writes an `intents_log` audit row. |
| `POST /v1/admin/satellite/set-volume` | **Device (`X-Device-Token` or Bearer)** | `{room_id, level}` (0–100) | Set the satellite's master hardware output volume (scales both TTS and music). |
| `POST /v1/admin/satellite/display` | **Admin (Bearer)** | `{room_id, action}` (`on` \| `off` \| `restart_kiosk`) | Drive a **video** satellite's screen (panel power via its configured mechanism, or a kiosk-browser restart). `409` when the room isn't a video satellite, or when the device's `hello` declared `supports` without `display` (spare-device satellites, see docs/uml/satellite-protocol.md "Declared abilities"); a Pi never gets it. `503`/`404`/`502` as above. Writes an `intents_log` audit row. |
| `POST /v1/admin/satellite/upgrade` | **Admin, security tier**; `423` while the container guard's freeze is on | `{room_id}` | Tell a satellite to mirror `/v1/satellite-code`, verify sha256s, self-restart, and roll back if it doesn't reconnect in time. Returns `{requested, room_id, expected_sha, upgrade}`, `upgrade` being the room's new record (the snapshot's `satellite_upgrades` entry). One at a time per room: from the request until the satellite says `hello` again (`done` on `expected_sha`, `rolled_back` on another) or its window ends (`no_news`, twice `SATELLITE_UPGRADE_RECONNECT_TIMEOUT_SEC` plus two minutes), a second request is `409` ("already upgrading"), since a second `upgrade` frame mid-sync would start a second sync over the first; a frame that never went out (`502`) leaves no record. `409` also when the device's `hello` declared `supports` without `code_sync` (spare-device satellites, see docs/uml/satellite-protocol.md "Declared abilities"); a Pi never gets it. A device that declared `code_sync` but not `plugin_payloads` gets `"plugin_payloads": false` in its `upgrade` frame. |
| `POST /v1/admin/satellites/{room_id}/pairing/preseed` | **Admin, security tier** | `{sat_type?, room_label?, hardware?, board?, mac?, force?}` | USB adoption: mint the room's pairing token (sha256 stored; RAW token returned once, never logged) + upsert the inventory row. `409` already paired unless `force` (rotates). |
| `DELETE /v1/admin/satellites/{room_id}/pairing` | **Admin, security tier** | — | Delete the room's pairing row so the NEXT `hello` for that room re-pairs. Returns `{room_id, reset}` (`reset: false` when there was nothing to clear). |
| `DELETE /v1/admin/satellites/{room_id}` | **Admin, security tier** | — | Remove a never-connected satellite (inventory + preseeded pairing). `409` when the room is provisioned (has an MPD instance). |
| `POST /v1/admin/satellites/{room_id}/label` | **Device (`X-Device-Token` or Bearer)** | `{room_label}` (null clears) | Set the satellite's display room label (grouping tag; cosmetic, daily-tier). |
| `GET /v1/admin/satellite/{room_id}/config` | **Device read** | — | Editable satellite config: the schema joined with the values the Pi reported, `{room_id, reported, fields:[{name, label, group, section, tier, type, min, max, choices, choice_labels, unit, help, value}]}`. `choice_labels` (null unless set) maps each choice to the words the dashboard shows for it (`wake.ack_mode`: `greeting` → "Spoken greeting, then listen"); the value saved is still the choice. `value` is null for a setting the Pi did not report (a satellite older than the setting). `supported`: whether this satellite has the setting at all — a satellite reports every setting it has (null when unset), so `false` is one missing from its report (its code predates it; the dashboard shows "needs a satellite upgrade" instead of an input), null until it has reported. `404` when the room isn't connected. `409` when the device's `hello` declared `supports` without `config` (spare-device satellites, see docs/uml/satellite-protocol.md "Declared abilities"); a Pi never gets it. A device that declared a list without `wifi`, `mic_gain`, `leds`, `display` or `sounds` is not shown that section's settings. |
| `POST /v1/admin/satellite/{room_id}/config` | **Admin (Bearer)** | `{"changes": {field: value}}` | Validate and push config edits; the Pi rewrites its `config.toml` and restarts. Returns `{sent, rejected, restarting}`. A setting the satellite's report doesn't have (`supported: false` above) is rejected ("upgrade it first") rather than sent: an older Pi would write it, restart, and ignore it. `409` when the device's `hello` declared `supports` without `config` (spare-device satellites, see docs/uml/satellite-protocol.md "Declared abilities"); a Pi never gets it. A setting in a section the device didn't declare is rejected ("it didn't declare 'leds'"). |
| `POST /v1/admin/satellite/{room_id}/voice` | **Admin (Bearer)** | `{"voice": "<name>" \| null}` | The room drawer's voice picker: what "switch to <name>'s voice" said in the room does (`domovoi/room_voice.py`). A connected satellite that keeps its own voice (a Pi, a PC) is sent the change as an announcement in the new voice ("Okay, I'm Norman now.") whose `response_end` carries `pi_action: set_voice`; it stores the name and reports it back in `voice_status`. A room whose device keeps none (a phone in room mode: `supports` without `voice`, or offline, board `android_room`) has it kept in `satellites.voice` (V031), offline too, then says the confirmation if connected; `null` is the server's own voice there. A shipped voice whose files are on disk but has no registry row is registered first. Returns `{room_id, voice, kept_on: "device"\|"server", said}`. `404` an unknown voice or room, `409` a voice not downloaded (or one that can't speak here), an offline Pi, or a busy room (nothing changed), `400` `null` for a Pi. |
| `GET /v1/admin/satellite/{room_id}/logs` | **Admin read (Bearer or cookie)** | `?max_bytes=` (1 KB–10 MB, default 10 MB) | The satellite's logs, two of them since 2026-10-09. `text`/`bytes`/`buffered_bytes`/`truncated`/`chunks` are the tail of the satellite's in-RAM log ring, pulled live over its WS (`get_logs` → chunked `logs_chunk`), exactly as before; `live` repeats them as one object (`null` while the room is offline); `retained` is the tail (up to `max_bytes`) of the log the core keeps on disk for the room — the lines the satellite pushed (`log_push`), including what it spooled while disconnected, each prefixed with the server's receive time and `[offline]` for spooled lines — `{text, bytes, file_bytes, rotated_bytes, max_bytes, updated_at, truncated}`, or `null` when nothing is retained (a satellite on older code); `last_outage` is the room's last `prev_health` report (below) or `null`; `connected` says which. Gated like a config read: both logs hold what the room said (the satellite logs each transcript). An **offline** room answers `200` with `live: null` and whatever is retained — that is what retaining is for — and `404` only when the core holds nothing for it at all; `503` disconnected mid-transfer, `504` stopped answering, `502` when the answer comes to more than `max_bytes` allows for (the reassembly buffer is capped per request, so a session cannot answer one pull with frames for as long as the timeout permits). A connected device whose `hello` declared `supports` without `logs` is answered like an offline room, plus `live_supported: false`: no `get_logs` goes to it. |
| `GET /v1/admin/satellite/{room_id}/health` | **Device read** | — | The room's health (2026-10-09): `{room_id, connected, latest, last_outage, outages, history}`. `latest` is the newest `health` sample the satellite sent (`{received_at, sample}` — memory, fds, CPU, load, free memory, SoC temperature and throttle flags, uptime and boot id, mic frames/s, the wake loop's heartbeat age, queue depths, the WebSocket state and reconnect count, the Wi-Fi link and NetworkManager state, gateway and core reachability; numbers and short strings, never speech), kept by the core across the room disconnecting and, with V022, read back from the database after a core restart; `last_outage` is the record the satellite's previous process life left behind and sent in its `hello` (`{reported_at, prev_boot_id, report}` — the report holds its last sample, `offline_diag` (one entry per failed connect while it was disconnected: the failure class, whether the gateway and the core answered, the link), the self-check `actions` it took and `last_exit_reason`); `outages` the last five such reports; `history` a 24 h summary (`samples`, `from`/`to`, min/max/last of `rss_kb`, `cpu_pct`, `soc_temp_c`, `mic_fps`, `fds`, `mem_avail_kb`, `throttled_any`, `reconnects`), `null` without V022. `200` with nulls for a room nothing is known about. Device read because every field is about the hardware and the link, like the reported config (REV-11). |

### 2.6 Admin: version, config, chat, hardware

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/admin/version` | **Device read** (`X-Device-Token`, Bearer or the dashboard cookie) | — | `401` with no credential (CORE-21: the answer carries the update unit's last run, its error text included, and what this host can restart). `{sha, running_sha, checkout_sha, version, checkout_version, channel, channels, channel_pin, restart_required, code_restart_required, plugins_pending_restart, restart_capable, restart_hint, restart_command, restart_mode, restart_in_progress, started_at, uptime_sec, last_update, last_update_problem, bad_sha}`. `version` is the release label of the running code, captured at boot from `git describe --tags --match "v[0-9]*"`: `1.0.0` on a release, `1.0.0 + 14 (abc1234)` past one, the pyproject version and the short SHA with no release tag in its history; `checkout_version` is the same for the tree now. `channel` is the branch of origin the check and the pull follow (`stable`, `main`, another, or `null`), read from the checkout's config; `channels` the ones the dashboard offers (`["stable", "main"]`); `channel_pin` the branches root's update unit allows (`DOMOVOI_UPSTREAM_BRANCH`), `null` when it pins none. `sha`/`running_sha` are captured at boot (the code actually loaded); `checkout_sha` is read live from the tree. `restart_required` is true when either the checkout moved past the running code (`code_restart_required`) or a plugin upgrade is staged for restart: `plugins_pending_restart` lists those as `[{slug, from_version, to_version, since, where: ["core"]}]`. `restart_mode` is `update` when the restart starts `domovoi-update.service`, else `restart`. `restart_in_progress` is true while a restart or update is under way: the restart this process accepted, for the moment before systemd has the job (and, without the unit, until the bounce ends this process), or `domovoi-update.service` running, however it was started (systemd's `ActiveState`, read without sudo; the unit's own `running` record, at most 30 minutes old, when systemctl can't say). While it is, the restart and the pull below are refused, and the dashboard offers neither and follows the run to its end, after a reload too. `restart_capable` says whether this host can do the restart unattended (the sudoers grant for its mode); when it can't, `restart_hint` says why and `restart_command` is the command that does it by hand (`sudo systemctl start domovoi-update.service`, or `sudo systemctl restart domovoi-core domovoi-web`), `null` on a host without systemd (Windows, a development box). `started_at` is the core process's boot time (Unix seconds): a restart moves it. `last_update` is that unit's last run (`null` on a host without it): `{status, mode, from_sha, to_sha, prev_source, bad_sha, started_at, finished_at, duration_sec, deps_changed, mpd_changed, migrations_before, migrations_after, db_restored, error, steps: [{name, status, duration_sec}]}`, where `status` is `ok`, `running`, `rolled_back`, `rollback_failed`, `failed`, `aborted` or `refused`. A step's `status` is `ok`, `failed`, `refused` or `warn` (it went ahead, but not as configured); a `warn` step of `sync-deps`, `rollback-deps` or `env-secrets` also carries `detail`, one line the script wrote itself ("lock not applied: <why>; ..." when `DOMOVOI_USE_LOCK=1` could not be honoured, what `env-secrets` added to `domovoi/.env`), at most 300 characters; every other step's detail, raw tool output included, stays on the box. The run's `duration_sec` is `null` while it goes on, and any `duration_sec`, the run's or a step's, is `null` when it can't be right: negative, longer than the run by `started_at`/`finished_at`, or over a day (what an `apply-update.sh` from before 2026-09-30 recorded on Ubuntu 26.04, whose uutils `date` broke its clock). `last_update_problem` says why `last_update` is `null` when it shouldn't be: `missing` (the unit is installed but has no result), `unreadable`, `too_large`, `invalid` (not JSON) or `not_a_result`; `null` when there is a result or no unit. `bad_sha` is the commit the unit rolled back; don't offer it again. `signature` is the checkout's commit-signature verdict, `{status, signer, key, enforced, allowed_signers, detail}`, where `status` is `verified`, `unsigned`, `unverified` (signed, but not by a key in the allowed-signers file, or no file to check against) or `unknown`, and `enforced` says whether that file exists; `last_update` carries the unit's own `signature` object too. `last_watchdog_restart` (since 2026-10-15) is the last time the core was ended for freezing on this box, `{at, reason, pid, log}` from `~/.domovoi/logs/core-watchdog-last.json` (its loop watchdog, faulthandler's backstop, systemd's watchdog, or the dashboard's **Restart the core**; `log` is where the stacks are), or `null`; a `warn` step of `core-restart` or `journal` (the update unit's drop-ins) carries its `detail` too. See [LINUX_HOST.md](LINUX_HOST.md#updates-from-the-dashboard), [Signed updates](LINUX_HOST.md#signed-updates) and [The loop watchdog](LINUX_HOST.md#the-loop-watchdog). |
| `POST /v1/admin/version/check` | **Device (`X-Device-Token` or Bearer)** | — | `git fetch --no-tags`, then the tags on their own (never forced), then report `{behind, ahead, upstream, upstream_sha, error, channel, upstream_version, latest_release, tag_warning}`. `tag_warning` is set when the tags could not be fetched, a published tag that moved upstream included (git keeps this server's copy): the labels may be off, the counts and the pull are not affected. `upstream_sha` is the full SHA a pull would move to, for comparing with `bad_sha`; `upstream_version` its release label (on `stable`, the release that is available); `latest_release` the label of `origin/stable`, `null` before the first release. Read-only, best-effort. Under `never` no git runs: `upstream: false` with the turned-off reason as `error` (§1.6). |
| `POST /v1/admin/version/pull` | **Admin, security tier** (`501` before setup); `423` while the container guard's freeze is on | — | `git fetch --no-tags` and the tags on their own (a tag that could not be fetched is `tag_warning` in a successful answer, never a refusal), then the fetched tip's signature is checked, then `git merge --ff-only` to that exact SHA; a dirty/diverged tree returns `pulled: false` + stderr. The answer carries the same `signature` object as `GET /v1/admin/version`; while the allowed-signers file exists, a tip that does not verify answers `pulled: false` with the reason in `error` and the tree does not move (without the file the pull goes ahead and the core logs a warning). A checkout with no upstream branch answers `pulled: false` saying so. Admin-tier: it changes the code on disk that this process and `/v1/satellite-code` serve from. Never restarts the process — that's the separate restart below. A successful pull also returns `prev_sha` and records it in `UPDATE_STATE_DIR/prev_sha`: the SHA the update unit rolls back to before it has a record of its own (the running SHA while an earlier pull is still waiting for its restart, else the pre-pull HEAD). Under `never`: `pulled: false` with the turned-off reason, and no git runs. While a restart or update is under way (`restart_in_progress`): `pulled: false`, "a restart or update is under way; pull again once it has finished", and no git runs (the update unit applies the HEAD it read when it started). |
| `POST /v1/admin/version/channel` | **Admin, security tier**; `423` while the container guard's freeze is on | `{"channel": "stable"\|"main"}` | Follow that channel from now on: the local branch of that name is made at HEAD and set to track `origin/<channel>`. HEAD never moves and no file changes; the next check says what is new there, and the pull stays its own action. `{ok, changed, channel, error}`; `changed: false` when it already follows that channel. Refused with `ok: false`, nothing changed: another channel name; under `never` (it fetches); while a restart or update is under way; when `DOMOVOI_UPSTREAM_BRANCH` in `/etc/default/domovoi-update` pins other branches (the dashboard can't edit that file); when origin has no such branch (`stable` before the first release); when HEAD is not on that branch's line (a pull could never fast-forward there); when a local branch of that name holds commits nothing else has. `501` before first-run setup. |
| `GET /v1/admin/satellites/approvals` | **Admin read** | — | Satellites waiting for a human (portal onboarding). A request is parked by the satellite's hello: a code it brings must be six ASCII digits, or the four a satellite set up before 2026-09-22 still carries (`error{reason:"approval_code_invalid"}` otherwise, nothing parked); a different device asking for the same room is `approval_conflict` for three minutes after the first one parked, and then replaces it with its own token and code. Returns `room_id`, `has_code`, `mac`, `board`, `sat_type`, `attempts`, timestamps. Neither the pairing token hash nor the code is returned: the operator reads the code off the device. |
| `POST /v1/admin/satellites/approvals/{room_id}/approve` | **Admin, security tier** | `{code}` | `501` before first-run setup (2026-10-08): approving binds a room to a device for good, so a LAN host cannot park its own satellite on an unclaimed core and approve it. Promote a pending satellite into a real pairing, copying the token hash it presented — approval binds a device, not just a room name. `code` is the six digits the satellite is showing and saying; it is compared in constant time. `400` without a numeric code, `422` when it does not match (the request stays parked), `409` when nothing is pending or the pending row carries no code, `429` after five attempts for that room in five minutes. A wrong code is never `401`/`403`: those two mean the CALLER is not admitted, and the dashboard turns them into the admin-password prompt, so a mistyped digit answered with `403` cost the operator a re-login (F-050). |
| `POST /v1/admin/satellites/approvals/{room_id}/reject` | **Admin, security tier** | — | `501` before first-run setup, like approve. Drop a pending request. Not a ban: the device keeps retrying until approved or powered off. |
| `POST /v1/admin/version/restart` | **Admin, security tier**; `423` while the container guard's freeze is on and the restart would load new code (pulled code or a staged plugin upgrade) | — | Load pulled code, or just restart (the dashboard's **Restart Domovoi**, so a hand edit of `domovoi/.env` takes effect). With `domovoi-update.service` installed it starts that unit (back up, sync dependencies, rebuild MPD, migrate, restart, health-check, roll back on failure; with nothing new since the commit it last applied, only stop, start and health-check, with no backup and no migrations); otherwise it bounces `domovoi-core` + `domovoi-web`. Returns `{ok, mode, units, delay_sec, error, in_progress}` **before** the restart fires, so the client can tell "restarting" from "the server broke". One at a time: while a restart or update is already under way (`restart_in_progress` above) it schedules nothing and answers `ok: false, in_progress: true` with the reason in `error`; a client follows the run under way instead of reporting a failure. Needs the sudoers grant for the current `mode` in [LINUX_HOST.md](LINUX_HOST.md); without it returns `ok: false` and the reason rather than prompting, and never falls back from `update` to a plain bounce. |
| `GET /v1/admin/config` | **Admin read (Bearer or cookie)** | `?section=common\|advanced` (optional) | The editable-config registry joined with live values (`{fields, plugin_fields, advanced_available}`). What comes back depends on how the caller authenticated: a **Bearer** reads everything; a **cookie-only** caller gets the `common` section with every credential value replaced by a mask (`masked: true` on the field) and no `advanced` section at all — `?section=advanced` answers `401` for it. Masked settings: `database_url`, `acoustid_api_key`, `letta_token` (`config_schema.SECRET_SETTING_NAMES`); plugin secrets arrive pre-masked as before. **Write-only** settings (`config_schema.WRITE_ONLY_SETTING_NAMES`: `acoustid_api_key`, since 2026-10-17) are never read back, not even to a Bearer: an admin (Bearer or cookie) gets the mask with `masked: true`, `write_only: true` and `is_set` (bool); any other caller (the pre-setup grace) gets no row for them. Every row carries `write_only`, and `is_set` (`null` unless write-only). Each field row also carries `choice_labels` (dict or `null`), `needs_internet` (the dashboard greys it under `never`), `needs_internet_choices` (choice values greyed under `never`; none of the core's fields use it now that the Microsoft Edge voices are gone, but a plugin's field may), `internet_profile` (the field follows the internet answer), `follows_internet` (it does right now: the answer is set and nobody set the field by hand) and `set_in_environment` (its key is in the process environment). The first row is `internet_access` (group `Internet`, choices `always` / `sometimes` / `never`); an installer can detect the feature by finding it. |
| `POST /v1/admin/config/search-test` | **Admin, security tier** | `{"kind": "brave" \| "searxng", "key"?, "url"?}` | Since W1. Settings → Chat & AI → Search → "Use your own search" → **test**: one search of a fixed word with the typed Brave key or SearXNG address, else the saved one. `{result: works\|refused\|unreachable\|error, detail}`. `409` internet-off under `never` (nothing sent); `422` with nothing to test or a key that isn't one. The key is never echoed, logged or stored here. |
| `POST /v1/admin/config/acoustid-test` | **Admin, security tier** | `{"key": "<typed key>"?}` (at most 256 characters; absent or empty tests the saved `ACOUSTID_API_KEY`) | Since 2026-10-17. Settings → Advanced's **test the key**: one AcoustID lookup of a fixed fingerprint with that key, through pyacoustid like the library enricher. `{result, detail, fingerprints}`: `result` is `works`, `refused` (AcoustID refused the key, e.g. a user API key), `unreachable` (no answer about the key: the line, the service, another AcoustID error, in `detail`) or `unavailable` (pyacoustid isn't installed); `fingerprints` says whether this server can fingerprint songs (Chromaprint's `fpcalc`). `409` internet-off (§1.6) under `never`, with nothing sent; `422` when there is no key to test or the typed one isn't a key. The key is never echoed or logged. |
| `POST /v1/admin/config` | **Admin, security tier** | `{"changes": {...}, "plugin": "<slug>"?}` | Validate, persist to `.env` (or the plugin's `~/.domovoi/plugins/<slug>.env`), live-apply `hot`/`reapply` tiers, and report `{applied, restart_required, rejected, normalized}`. Saving `acoustid_api_key` (write-only: send the key, or `""` to clear it; surrounding whitespace is dropped; anything but letters, digits, `.`, `_`, `-` is `rejected`) applies at once, and lists `library_enricher_enabled` in `restart_required` when song recognition's next boot changes because of it (it follows the internet answer and turns on only with a key or the Shazam add-on; never under `never` or when set by hand). `whisper_device` and `whisper_compute_type` are checked as a pair as the next boot will read them: a write that names a compute type the device can't run (`float16` on `cpu`) is `rejected` together with any device in the same write; a write that only changes the device resets a saved compute type that can't run there to `auto` and says so in `normalized` (`{field: note}`, `{}` when nothing was adjusted). **The internet answer:** `changes.internet_access` takes `always`, `sometimes` or `never`; it is `rejected` ("set in the server's environment (INTERNET_ACCESS); change it there") when `INTERNET_ACCESS` is in the process environment. Saving it re-derives the fields that follow the answer — live ones land in `applied`, restart-tier ones in `restart_required` (plus `internet_access` itself when the change crosses `never`, for `HF_HUB_OFFLINE`) — and starts (`always` / `sometimes`) or stops (`never`) the SearXNG container in the background. **`follow_internet`** (`["<field>", ...]`, at most 16) hands fields back to the answer: each one's `.env` line is commented out (never deleted) and the answer's value applied; the response gains `followed`, and a field outside the profile or pinned in the environment lands in `rejected`. |
| `POST /v1/admin/voice-history/delete-all` | **Admin, security tier** (Bearer; the cookie alone is `403`; `501` before setup) | — | Delete all voice history (`domovoi/voice_history.py`): writes a `delete_all` job to `voice_history_jobs` (V030) with everything said until now as its cutoff, starts it in the background and answers `{job}` (`{id, kind, cutoff, requested_at, started_at, finished_at, deleted, error, state}`) at once. It deletes every `conversation_log` and `intents_log` row from before the cutoff, every session idle since before it (and the older exchanges in one still in use), and the satellites' logs kept on the server, in batches like the daily cleanup. A second call while a job waits (not started, or failed and waiting for its retry) moves that job's cutoff up to now and answers it; while one is running it answers a follow-up job (one at most) that runs after it. A restart in the middle finishes it at the next boot (the `voice_history_pruner` worker); a failure keeps its `error` and is retried within the hour. Writes one `[ui] delete all voice history` intents_log row (no speech). |
| `GET /v1/admin/internet` | **Admin read (Bearer or cookie)** | — | The Settings → Internet page: `{answer, answer_locked, answer_source, choices: [{value, label, summary, detail}], privacy_note, connectivity: {online, reason, target, last_checked_at, last_online_at}, hf_hub_offline, features: [{name, label, value, next_boot_value, answer_values: {always, sometimes, never}, follows, set_by, applies, condition, condition_met}], restart_required, search_helper: {state, detail, at, managed}, network_plugins: [{slug, name, bundled}], warnings: [str]}`. `search_helper.state` is the last start/stop of the search helper (SearXNG) in this process: `unknown` (nothing since the core started), `starting`, `stopping`, `running`, `stopped`, `failed` (with `detail`), `left` (unanswered) or `unmanaged` (`DOMOVOI_MANAGE_SEARXNG=0`); `network_plugins` are the loaded plugins whose manifest sets `permissions.network` (`bundled` ones follow the answer); `warnings` is filled only under `never`: an `OLLAMA_URL` host outside the house, or an Ollama cloud model. `answer_source` is `unset`, `env_file` or `environment` (then `answer_locked` is true); `set_by` is `default`, `answer`, `env_file` or `environment`; `applies` is `live` or `restart`; `condition` names a requirement (song recognition: "needs an AcoustID key or the Shazam add-on") and `condition_met` says whether it holds — the key itself is never sent. |
| `POST /v1/admin/internet/search-helper` | **Admin** | — | Runs the search helper's start/stop for the CURRENT answer in the background (what saving the answer does), for a first start that failed or timed out: `{scheduled, search_helper: {state, detail, at, managed}}`, `scheduled` false while the answer is unset or `DOMOVOI_MANAGE_SEARXNG=0`. Nothing is saved. |
| `GET /v1/admin/device-token` | **Admin, security tier** (read: Bearer only; the cookie alone is `403`) | — | `{token, header}` — the household device token (`header` is `X-Device-Token`). Opaque: a generated one is an eight-word hyphenated phrase, a chosen one is any printable ASCII. `501` before setup. |
| `POST /v1/admin/device-token` | **Admin, security tier** | `{"token": "..."}` | Replace the household token with one an admin chose: printable ASCII 0x20–0x7E, 12 to 128 characters, stored verbatim with the outer whitespace trimmed. Returns `{token, header, rotated: true}` — setting IS a rotation. `400` with a message a person can act on (`at least 12 characters`, `at most 128 characters`, `letters, digits, punctuation and spaces only — …`) and the row unchanged; the rejected value is never echoed back. `501` before setup. **Restart both processes before setting one:** core and web share this row, and a process still running a build from before the two-form match canonicalises the presented candidate first, so it refuses every device-tier call until it is restarted. A generated phrase is canonical and is accepted by either build, which is why rotating is the way back. |
| `POST /v1/admin/device-token/rotate` | **Admin, security tier** | — | Mint a replacement household token in the eight-word phrase format: `{token, header, rotated: true}`. The previous token is refused from now on; `~/.domovoi/device-token.txt` is rewritten. Every household client has to be re-enrolled. This is also the way back from a chosen token. |
| `POST /v1/admin/chat-tool` | **Chat callback** | `{tool, args}` + `X-Chat-Callback` | Execute a chat-mode tool call on behalf of the chat agent's sandboxed proxy tools. The header must carry this boot's callback secret, which the generated tool source embeds — `401` otherwise. Degrades to an apology string rather than 500ing; returns `{"text": "..."}`. Regenerate the tools after a core restart (`POST /v1/admin/chat/resync`). |
| `POST /v1/admin/chat/resync` | **Admin (Bearer)** | — | Rebuild the chat tool surface and re-attach it to every chat agent. The install/enable/disable pipeline runs this automatically; this is the manual trigger. |
| `GET /v1/admin/hardware` | Open | — | Host hardware snapshot for the Models page: `{gpus, cpu, ram, disk, stt}`; each field degrades to empty/null independently. `stt` is what speech recognition loaded at boot: `{state, configured: {model, device, compute_type, compute_type_resolved}, loaded: {model, device, compute_type} \| null, fallback_reason, error}` — `state` as in `/v1/health`; the Models page shows a banner for `fallback` and `unavailable`. |

### 2.6a Admin: the container guard

The guard runs in the core and is the only writer of `container_incidents`
(V024); docs/CONTAINER_GUARD.md says what it watches and why. The web
dashboard reads the incidents itself and proxies these.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /v1/admin/containers` | **Admin read** (Bearer or cookie) | — | `{guard, incidents_readable, freeze_operations, mpd_rebuild, incidents, banner, frozen, attention}`. `mpd_rebuild`: `{available, why}`, whether the music player image can be rebuilt from the dashboard (not with internet access turned off) and why not. `guard` is the last check: `{state: ok\|holding\|docker_unreachable\|docker_missing\|disabled\|starting, checked_at, interval_sec, hold, holds, database_ok, docker: {ok, error, cli_missing}, compose_project, watched: [{name, owner, role, label, state, required, image, image_id, pin: digest\|id\|repository\|tag\|none, allow, hold, observed: {status, health, image, image_id, restart_count}}], held, unrecognized, unwatched_plugins, failing_resolvers: [{slug, container, for_sec}], mpd_image, pending: [{key, class, kind, container, seen, for_sec}], findings, last_opened, last_resolved}`. `failing_resolvers` are plugin containers whose resolver has kept raising past 30 minutes (not watched until it answers); `mpd_image` says how this start placed the music player image (adopted after the installer's or the update unit's rebuild, adopted unverified, or reported), `null` when nothing changed. `incidents` are the open ones and those resolved in the last 24 h, open first, at most 50 rows, in full (§3.9a's admin view). |
| `POST /v1/admin/containers/check` | **Admin (Bearer)** | — | Run a check now: `{guard}`. The debounce still applies — one check never opens an incident by itself. |
| `POST /v1/admin/containers/mpd-image/rebuild` | **Admin, security tier** (`501` before setup) | — | Rebuild the music player image now, exactly as the core builds it when it is missing; it becomes the image the guard expects (baseline source `core-build`), and every room's player on another image is removed and created again on it with its data volume (what it plays stops; playlists and queues stay). Waits for the build (seconds with Docker's build cache, a minute or so without). `{image_id, baseline, rooms: [{room, recreated, error}], guard}`. `409` while one is running or an update or restart is under way, `503` when it can't run (internet access turned off, no `MPD_IMAGE_TAG`) or the build failed. Every room is held for the guard from before the build until the last one is done. A tamper incident stays open until it is cleared. |
| `POST /v1/admin/containers/incidents/{id}/acknowledge` | **Admin (Bearer)** | — | Acknowledge an open **failure**: the banner hides it until something new happens; it still resolves by itself. `{incident}`. `409` for a tamper incident ("review it, then clear it") or a resolved one, `404` for none. Recorded as `admin via <source>`. |
| `POST /v1/admin/containers/incidents/{id}/clear` | **Admin, security tier** (`501` before setup) | `{"note": "..."}` (optional, at most 500 characters) | Clear an open **tamper** incident after review: `{incident, frozen}` — `frozen` is false once no tamper incident is open. Recorded (who, when, the note). `409` for a failure (it resolves by itself) or one already cleared, `404` for none. |

### 2.7 Admin: voices, sounds, wake words

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `POST /v1/admin/sounds/regenerate` | **Admin (Bearer)** | — | Kick a background re-render of all voices' greeting/canned clips, then notify satellites to re-sync. Returns `{"started": true}` immediately. |
| `POST /v1/admin/sounds/setup-clips` | **Admin (Bearer)** | — | Render the satellite's setup phrase set in the household's default voice, synchronously (sixteen short lines, seconds), for media prep to copy onto a prepared card. The web process cannot import the TTS clients, so its prepare job calls this with the operator's credentials forwarded. Returns `{written, problems}`. |
| `POST /v1/admin/voices/sample` | **Device (`X-Device-Token` or Bearer)** | `{"name": "<voice>"}` | Live-synthesize a sample line in a registered voice; returns `audio/wav` with the text in `X-Sample-Text`. `404` for an unknown voice; `409` when the voice's own engine gave nothing and another would answer, the detail saying why in words (`"Eric can't speak right now: Microsoft's voice service answered HTTP 403"`). |
| `POST /v1/admin/pronunciations/reload` | **Admin (Bearer)** | — | Load the "say it like" list from the database (V027, over the shipped defaults) into the text-to-phoneme converter, then re-render in the background the clips whose words it changes. `{"reloaded": true, "left_out": [<entries it could not use>]}`. The web backend calls it after an edit. |
| `POST /v1/admin/voice/say-like` | **Device (`X-Device-Token` or Bearer)** | `{"word", "say_like", "voice"?}` | Say `word` as `say_like` asks, unsaved, in `voice` (one of the three) or the server's voice: `audio/wav`, the phonemes in `X-Phonemes` (URL-encoded, sentences joined by ` \| `). `400` with the reason for an entry the converter can't use or an unknown voice; `502` when the voice can't speak. |
| `POST /v1/admin/wake/record/start` | **Admin (Bearer)** | `{room_id, wake_word_id}` | Tell a connected satellite to record positive training clips (fresh take: clip dir + count reset). `404` room/word unknown, `409` mid drop-in or wrong status, or (before anything is reset) a device that declared `supports` without `wake_clips`, `502` send failure. |
| `POST /v1/admin/wake/record/stop` | **Admin (Bearer)** | `{room_id}` | Stop an in-progress recording; the Pi resumes its wake loop. |
| `POST /v1/admin/wake/train/stop` | **Admin (Bearer)** | `{wake_word_id}` | Stop a word's training. The one running now: its run is cancelled (the container gets `docker stop`, so it removes its copies of the recordings, then `rm -f`) and, when the run has ended, the word goes back to `recording` with its clips, error "Training stopped from the dashboard."; `{stopping: true}`. A queued one goes back at once; `{stopping: false, stopped}`. Not queued again either way. `404` no such word, `409` not training. |
| `POST /v1/admin/wake/forget` | **Admin (Bearer)** | `{slug}` (`^[a-z0-9_]+$`) | A deleted word's training files (synthetic clips, features, any copies of its recordings) leave the trainer's cache volume, in the background (a training of it is stopped first); `{scheduled: true}`. The dashboard's delete calls it; the worker's first pass after a start removes the files of any word that no longer exists. |
| `POST /v1/admin/wake/push` | **Admin (Bearer)** | `{room_id, wake_word_id}` | Push a trained model: the Pi writes the slug to its wake sidecar, syncs from `/v1/wake-models`, and restarts. `409` when not trained yet, or for a device that declared `supports` without `wake_models`. |
| `POST /v1/admin/wake/score` | **Admin (Bearer)** | `{wake_word_id}` | Offline-score recorded clips against the trained model (per-clip max score + silence baseline). `409` no model, `501` when openWakeWord isn't installed. |
| `GET /v1/admin/wake/trainer` | **Admin read (Bearer or cookie)** | — | Settings → Wake words' training card (`domovoi/wake_trainer_setup.py`): `{enabled, job, gpu_name, training_active, timeout_sec, timeout_auto, requirements}`. `enabled`: training is on (the setting, and a command). `job` is the "Turn on wake-word training" job, kept in `~/.domovoi/wake_trainer/setup.json`: `{state: idle\|running\|failed\|done, stage: image\|gpu\|data\|settings\|null, percent, message, error, log_tail: [str], needs_cpu_confirm, started_at, finished_at}`; a job the core stopped in the middle of reads `failed` with "Interrupted: …". `gpu_name`: the card the job's GPU check found. `training_active`: a word is training now. `timeout_sec`: the time limit a training gets now, `timeout_auto` when that is sized to the server (`wake_word_train_timeout_sec` = 0). `requirements` (cached for about 10 s, and for as long as a setup job or a training runs): `{docker: {ok, version, os, runtimes, error}, gpus: [{name, mem_used_mb, mem_total_mb}], gpu, estimate, disk: {free_gb, need_gb, headroom_gb, path, total_first_gb, confirm_gb}, memory: {total_gb, need_gb, pc_need_gb}, wsl, download_gb, image: {name, present, current}, data_ready, refusals: [str], notes: [str]}`, where `estimate` is `{device: "gpu", minutes_per_word, first_minutes, gpu_names}` or `{device: "cpu", minutes_per_word, first_minutes, cpus, physical_cores, measured}`, and a non-empty `refusals` is what the enable below would answer `409` with. On the Windows install's distro (`wsl: true`) Domovoi can't see the Windows drive: `disk.free_gb` is `null` (never the distro's virtual disk), `disk.confirm_gb` (40) is what the admin confirms C: has free, and a memory refusal is worded in the PC's terms ("this PC has about 24 GB", by the installer's rule: the distro gets the PC's memory minus 8 GB; `memory.pc_need_gb` 32, the PC that is enough). `memory.total_gb` is always what Domovoi itself has; no PC size is shown when nothing is refused (a distro without the installer's cap would make it a guess). Elsewhere `confirm_gb` and `pc_need_gb` are `null`. |
| `POST /v1/admin/wake/trainer/enable` | **Admin, security tier** | `{"cpu_ok": bool, "disk_ok": bool}` (both default `false`) | Turn wake-word training on: one background job, as the service user, builds the trainer image (labelled with the git tree id of `scripts/wake_word/docker`), checks the GPU with `--gpus all` when the host has an NVIDIA card, downloads the training data (`--warm`), then writes `WAKE_WORD_TRAINER_ENABLED=true` and `WAKE_WORD_TRAIN_COMMAND` to `domovoi/.env`, applies them and starts the trainer worker. Returns the status above at once; poll the `GET`. `cpu_ok: true` is the admin's yes to training on the CPU; `disk_ok: true` the admin's word that C: has `disk.confirm_gb` free (required on the Windows install only). `409` with the reason in `detail` when it can't start: a refusal (internet turned off, too little disk or memory, no Docker), the Windows install without `disk_ok`, a server that would train on the CPU without `cpu_ok`, a job already running or starting (one at a time: two overlapping calls start one job), or a word training right now. Where the host has an NVIDIA card Docker can't use, it also leaves `~/.domovoi/wake_trainer/gpu-runtime-retry`, so the next update or Restart sets the GPU runtime up at once. Under internet **never** a running job stops (its build or download) and fails saying so. `501` before setup. A GPU Docker can't reach is not a `409`: the job stops at `stage: "gpu"` with `needs_cpu_confirm: true`, and the same call with `cpu_ok: true` goes on without it. |
| `GET /v1/admin/wake/trainer/footprint` | **Admin read (Bearer or cookie)** | — | What turning training off would free, as Docker sizes it: `{image, image_bytes, image_present, volume, volume_bytes, volume_present, total_bytes}`; a size Docker can't give is `null`, never a guess. Up to about two minutes (`docker system df -v`). |
| `POST /v1/admin/wake/trainer/disable` | **Admin, security tier** | — | Turn wake-word training off and free the space: `WAKE_WORD_TRAINER_ENABLED=false` and an empty `WAKE_WORD_TRAIN_COMMAND` in `domovoi/.env` and live, then the trainer image, Docker's build cache and the cache volume (`domovoi_oww_cache`: the training data and every word's training files) removed. Trained models and recorded clips stay; queued words go back to `recording`. `{freed: <the footprint before>, errors: [str]}`. `409` while a setup job runs or a word trains. `501` before setup. |

### 2.8 Admin: music and library

Voice-equivalent calls (`play`, the transport actions) re-enter the regular
routing pipeline so they land in `intents_log`/`conversation_log` exactly like
a spoken turn. Direct-play endpoints bypass the router, write only an
`intents_log` row (transcript prefixed `[ui]`), and never fall through to an
external streaming provider.

The `queue/*` endpoints are the exception to "music actions replace the
queue": they edit the room's existing MPD queue in place. Entries are
always addressed by MPD **songid**, never by position — a songid is stable
for the life of a queue entry, so a concurrent reorder can't turn "remove
entry 3" into "remove the wrong song". They know nothing about devices;
the "added by" provenance and the device blocklist live in the web
process (§3.5a).

Everything in this table is on the **Device** tier (`X-Device-Token` or an admin Bearer) — ordinary household playback — except the queue read, which is **Device read** (the same, or the dashboard cookie), and the two library sweeps at the end, which are **Admin (Bearer)**.

None of them creates a room. The routes that name a room (`play-track`, `play-tracks`, `play-playlist`, the queue routes) start that room's music player if it is down, and answer **`404`** for a room the house does not have — no `mpd_rooms`, pairing or inventory row — before a port is allocated or a container started. A room appears when its satellite's hello is accepted (or it is adopted), never from a URL.

| Method & path | Request | Response / purpose |
|---|---|---|
| `POST /v1/admin/music/play` | `{room_id, query}` | Route `"play <query>"` through the full pipeline; dispatches the music-start frame to the room's Pi. Returns `{text, matched_handler, matched_path, music_action, online}`. |
| `POST /v1/admin/music/play-track` | `{room_id, track_id}` | Play one `library_tracks` row directly via MPD (tag → filename → basename lookup). `404` unknown/unfindable track, `502` MPD error. |
| `POST /v1/admin/music/play-tracks` | `{room_id, track_ids: [..], start_sec?, start_paused?}` (≤500) | Load an ordered queue of library tracks into the room's MPD and start playback (the browser and phone players' "cast to room"). The room starts on the first id, `start_sec` into it when given (dropped if that id or its file is missing); clients send their CURRENT track first. `start_paused: true` (a cast from a paused phone or browser): the room takes the queue and waits paused there — a person's pause, which no handshake or auto-resume undoes — until `resume`. Returns `{played, queued, requested, latency_ms, paused}`. A cast that fails names the part that failed beside the words: `502 {"failed": "music_player", "detail": …}` the room's music player on the server didn't answer; `503 {"failed": "satellite", …}` no satellite has ever connected, so no room has a player. A room whose satellite is offline is not a failure: its player takes the queue and the satellite joins when it connects. `404` when none of the ids (or their files) can be found. |
| `POST /v1/admin/music/play-playlist` | `{room_id, playlist_id, shuffle?}` | Start a playlist (`playlist_id` 0 = the virtual Favorites). Ordered mode resumes from the saved position; stamps in-room playlist state so "next" stays in-playlist. |
| `POST /v1/admin/music/{action}/{room_id}` (**Device or guest**) | — | Transport controls; `action` ∈ `pause`, `resume`, `stop`, `skip`, `next`, `previous` (routed as the spoken equivalents). A guest may use every one but `stop` (`401`), on a playing room only. `skip`/`next` in a room whose queue holds two songs or more (a cast, queue adds) goes to the queue's next song, and after its last song the queue ends; a one-song queue keeps the smart skip (provider search, in-playlist advance, another library track). `previous` is MPD's (the queue's first song starts again). A control that did not happen is never a 200: `502` the room's player didn't answer, `409` nothing is playing, `503` no room has a player yet; the `502` and `503` bodies also name the part that failed (`failed`: `music_player` — the room's music player on the server — or `satellite`), beside `detail` in words. A 200 carries `ok: true` with `{text, matched_handler, matched_path, music_action, online}`. `400` for an unknown action. |
| `GET /v1/admin/music/queue/{room_id}` (**Device read or guest**) | — | The room's live MPD queue in order: `{room_id, items:[{song_id, pos, file, title, artist, album, duration_sec}], current_song_id, guest}` — `guest: true` when the answer was given to a guest (the web hop shows the guest view on it). `401` with a bad credential (or none, with guests off), `404` for a room the house does not have, `502` MPD error. A guest reads a playing room only (`403` otherwise), and nothing is started for it. |
| `POST /v1/admin/music/queue/{room_id}/add` (**Device or guest**) | `{track_ids: [..]}` (≤500) | **Append** library tracks without clearing — the one music path that doesn't replace the queue. Starts playback when the room was idle with an empty queue — never for a guest. A guest adds at most 50 tracks a call (`400`) and never past 500 entries in the queue (`409`). `{queued:[{song_id, file}], requested, started}`. `404` when MPD can't resolve any of them. |
| `POST /v1/admin/music/queue/{room_id}/remove` (**Device or guest**) | `{song_ids: [..]}` | Drop entries by songid. `{removed:[..], skipped:[..]}` — an id MPD no longer has is reported, not an error. A guest removes one id per call (`400` for more) and may not drop the entry playing now (`403`). |
| `POST /v1/admin/music/queue/{room_id}/move` (**Device or guest**) | `{song_id, to_position}` | Reorder (`to_position` is where it lands once taken out). `404` when the id isn't in the queue any more. |
| `POST /v1/admin/music/queue/{room_id}/clear` (**Device**) | — | Empty the queue and stop the room (dispatches the music-stop frame). Never a guest's. |
| `POST /v1/admin/music/mpd-rescan` | — | Tell every provisioned room's MPD to rescan the music dir — the playability half of a reindex, without the library sweep. `{rooms, updated}`. Device tier on purpose: no caller input, no database, and the web calls it after an upload has already written the files. |
| `POST /v1/admin/library/reindex` (**Admin**) | — | Background: sweep the music dir into `library_tracks`, then make every per-room MPD rescan. Returns `{"queued": true}` immediately. |
| `POST /v1/admin/library/enrich` (**Admin**) | — | Background: metadata enrichment pass (rate-limited; can take minutes). `{"queued": true}`. When nothing can run: `{"queued": false, "reason": "disabled" \| "offline" \| "no_provider"}` (`no_provider`: no AcoustID key and no Shazam add-on). `409` under `never` (§1.6). |

### 2.9 Printers, printing and scanning

Network printers only (IPP Everywhere, AirPrint, Mopria) and eSCL
(AirScan) scanners: no USB, no Windows spooler (`domovoi/printing/`).
**Device read** (household token, admin Bearer or the dashboard cookie):
the lists, status and the job log. **Device** (`X-Device-Token` or an admin
Bearer): look, add, defaults, test page, preview, print, cancel, scan.
**Admin (Bearer)**: rename, remove, trust a changed certificate, change the
limits, clear the job log. A guest gets `401` on all of them. The web
proxies each one (§3.23); the two print routes take a Files reference
there. Every address passes the LAN-only check
(`domovoi/printing/lan_target.py`; docs/SECURITY_PRIVACY.md, "Printers and
scanners").

| Method & path | Request | Response / purpose |
|---|---|---|
| `GET /v1/printers?refresh=` (**Device read**) | — | `{printers:[PrinterRow], has_printer, has_scanner, default_printer_id, rooms:[{id,label}], printable:{exts, pdf_first_exts, pdf_first_message}, limits, discovery}`. `refresh=true` re-reads the status of rows older than ~50 s (Settings sends it every minute while open; a worker otherwise polls every 10 minutes). A PrinterRow: `id, name, make_and_model, location, address, uri, added_via, can_print, can_scan, color, duplex, is_default, rooms, two_sided_default, color_default, defaults{sides,color,media}, choices{sides,color,media[{id,label}],copies_max}, status{state, tone, words, message, checked_at}, supplies[{name, level, color, kind, low}], supplies_reported, admin_url, needs_attention, identify, scan{sources,color,resolutions}, active_job, moved` (`moved`: "The office printer moved to …" for a week after a verified move, also in `status.words`). Printer-supplied strings are data (capped, control characters stripped). |
| `POST /v1/printers/discover` (**Device**) | `{look_again: bool}` | One mDNS look (group mode, then legacy unicast, 3 s). `look_again: true` also sweeps the home /24 for IPP on 631 (and eSCL on 80/443 of what answered), at most once a minute, never in the background, and only when mDNS found nothing new. `{found:[{key, make_and_model, location, address, prints, scans, color, duplex, via, already_added}], diagnostics:{ran_at, found, by_multicast, by_unicast, by_sweep, typed, sweep: "ran"\|"rate_limited"\|"not_needed"\|"skipped", sweep_retry_sec, errors}}`. A `key` is good for ten minutes. At most 6 looks a minute per address (`429`). A saved printer announced at a new address moves only when the new address answers with its UUID (and pinned certificate) and the old one no longer does. |
| `POST /v1/printers` (**Device**) | `{key}` or `{address}`, optional `name`, `room_id` | Add one: a `key` from the last look, or a typed address (an IP, `printer.local`, or an `ipp://` / `ipps://` / `http://…/eSCL` URL). Saved only after the device answers like one (an IPP `printer-uuid`, or eSCL capabilities XML); a printer that also scans is one row. `201` PrinterRow. `400` not on the home network, or not its usual port (an admin's typed full URL may use any port, and only an admin's typed address may use `OUTBOUND_ALLOW_HOSTS`); `409` nothing answered like a printer, or already added. |
| `PATCH /v1/printers/{id}` (**Device**) | `{is_default?, rooms?, two_sided_default?, color_default?}` | Household defaults (`null` follows the printer). PrinterRow. |
| `POST /v1/printers/{id}/rename` (**Admin**) | `{name}` | PrinterRow. |
| `DELETE /v1/printers/{id}` (**Admin**) | — | `{removed}`. Its jobs stay in the log. |
| `POST /v1/printers/{id}/trust-certificate` (**Admin**) | — | Pin the certificate it now presents (IPPS, or eSCL over https). `409` when it hasn't changed. |
| `POST /v1/printers/{id}/refresh` (**Device**) | — | Re-read the status now. PrinterRow. |
| `POST /v1/printers/{id}/identify` (**Device**) | — | Identify-Printer (flash or sound, as the printer supports). `{ok}`. |
| `POST /v1/printers/{id}/test-page` (**Device**) | `{who?, source?}` | Print Domovoi's test page. JobRow. |
| `POST /v1/print/preview` (**Device**) | multipart `file` + `meta` (JSON: `filename, title, printer_id?, options{pages, copies, sides, color, media}`) | `{printer_id, printer_name, title, kind, pages_total, pages, copies, sheets, options, summary, thumbs:[{page, src}], notes, adjustments, over_limit, choices}`. `415` a Word/ODT file (`detail` says to save it as PDF) or anything that doesn't print; `409` no printer; `422` a document that can't be drawn (or a page past the size limits); `400` a paper size the printer doesn't take or past 13.5 × 30 in; `429` past 30 previews a minute per address. |
| `POST /v1/print/jobs` (**Device**) | as preview, plus `who?, source?, library_id?, path?` in `meta` | `202` JobRow `{id, printer_id, printer_name, title, pages, copies, sheets, options, state: queued\|printing\|done\|failed\|canceled, pages_done, progress, error, who, source, created_at, finished_at, can_cancel}`. `413` over a per-job limit, `429` over the hourly or daily limit (each with the sentence to show). |
| `GET /v1/print/jobs?printer_id=&limit=` (**Device read**) | — | `{jobs:[JobRow], scans:[{id, scanner_name, state, pages, name, path, incomplete, error, who, source, created_at, finished_at}]}`, newest first; kept 90 days. |
| `GET /v1/print/jobs/{id}` (**Device read**) | — | JobRow, refreshed from the printer while the job is in flight. |
| `POST /v1/print/jobs/{id}/cancel` (**Device**) | — | Cancel-Job at the printer. JobRow. |
| `DELETE /v1/print/jobs` (**Admin**) | — | Clear the print and scan log. `{cleared}`. |
| `GET /v1/print/limits` (**Device read**), `PUT` (**Admin**) | `{max_pages_per_job, max_copies, max_sheets_per_job, max_jobs_per_hour_per_device, max_sheets_per_day, max_scan_pages}` (any subset) | The limits (defaults 100, 20, 200, 20, 500, 50) plus `history_days` (90, fixed). |
| `POST /v1/scan/jobs` (**Device**) | `{printer_id?, source: auto\|platen\|adf\|adf_duplex, color: color\|grey, resolution: 300, who?, origin?}` | Scan the glass (one page) or the whole feeder (`auto`: the feeder when it's loaded), then wait. `201` ScanRow `{id, printer_id, scanner_name, state: scanning\|waiting\|saving\|done\|failed\|canceled, source_used, pages, thumbs, can_scan_more, name, library_id, path, incomplete, error, readable}`. `409` no scanner, busy, the feeder jammed or empty, or a page that isn't a readable JPEG of a sane size (the scan ends, never stuck). `403` when Settings, Devices blocks the device the request names from changing files (also on `next` and `finish`). One scan at a time per scanner. |
| `GET /v1/scan/jobs/{id}` (**Device read**) | — | ScanRow. |
| `POST /v1/scan/jobs/{id}/next`, `/finish`, `/cancel` (**Device**) | — | Another glass page; save all pages as one PDF in `Documents/Scans/` ("Scan 2026-10-21 15.40.pdf"); or drop them. A session left waiting is saved after 10 minutes. `readable` is the seam for "what does this letter say?": false until the documents work registers a reader. |

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
stay Open unless the row says otherwise. The exception is anything that reads
back what the household SAID or what the house keeps about a person — a room's
or a person's conversations and voice notes, a person's memories, favorites
and preferences, the chat threads, messages and images, a wake-word clip's
audio — which is **Device read** (§1.1): paired devices only. So, since
2026-10-08, is who is home and the calendar: the people roster and a
person's row, a person's or a room's session list, every room's row and the
pending-adoption list, and the calendar reads (WEB-15). Each of those is
the web process's own read, not a proxy, so there is no core hop behind it.

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
| `GET /api/auth/status` | Open | — | `{setup_complete, authenticated, reload_window_min, resume_until, resume_in_sec}` — the dashboard's setup-vs-login probe. `authenticated` reflects cookie or Bearer. `reload_window_min` is the live `ADMIN_RELOAD_WINDOW_MIN` (0 = off). `resume_until` (ISO 8601, the server's clock) and `resume_in_sec` (whole seconds left, rounded up — what a client should count from) are set while the session the `domovoi_admin` cookie names is inside its reload window, `null` otherwise. Read-only: it slides nothing. |
| `POST /api/auth/setup` | Open (requires setup code) | `{setup_code, password}` (password ≥10 chars) | First-run claim of the admin tier. `403` wrong/missing/expired code (the code is valid for 24 h from when it was written), `409` already set up. Returns `{ok, token, reload_window_min, resume_until, resume_in_sec}` and sets the session cookie (its own secret, not the token). Deletes the code file and rotates the household device token. |
| `POST /api/auth/login` | Open (backoff-throttled) | `{password, label?}` | Verify the password (per-source exponential backoff; `429` + `Retry-After` while throttled, `401` wrong password), mint a token, set the cookie (its own secret, not the token). Returns `{ok, token, reload_window_min, resume_until, resume_in_sec}` — the reload window starts now. |
| `POST /api/auth/resume` | The session cookie, same origin, `X-Requested-With` | — (an empty JSON object) | A reloaded dashboard page takes its admin sign-in back (2026-10-14). Returns `{ok, token, reload_window_min, resume_until, resume_in_sec}`, where `token` is the Bearer of the session the cookie names, unwrapped with the cookie (nothing is minted), with `Cache-Control: no-store`; slides that session's expiry like any use. Only while `0 ≤ now − signed_in_at < ADMIN_RELOAD_WINDOW_MIN` (absolute: counted from the password sign-in, a first-run setup or a password change made with that session, never from use). `403` without `X-Requested-With`, or when the request is not same-origin: `Origin` must be exactly this request's scheme, host and port (`Host`), or, with no `Origin`, `Sec-Fetch-Site` must be `same-origin`; neither header is a `403` too. `401` with no cookie, or a cookie whose session is signed out, revoked, expired, past the 90-day cap, from before V028, or outside the window (and always with the window at 0, which is also what the dashboard uses while the core's live value is unknown since it started). A refusal changes nothing. A Bearer alone resumes nothing. |
| `POST /api/auth/logout` | Bearer only | — | Revoke the *calling* session and clear the cookie. `401` without a Bearer (a cross-site POST with just the cookie can't log you out). |
| `GET /api/auth/sessions` | Bearer or cookie | — | `{"sessions": [{token_hash, label, created_at, expires_at, last_used_at, current}]}` for the revoke UI. |
| `DELETE /api/auth/sessions/{token_hash}` | Bearer only | — | Revoke a session by hash. `404` unknown hash. |
| `POST /api/auth/password` | Bearer only | `{old_password, new_password}` | Change the admin password (old one re-verified). Every other session is revoked (and with it any reload window it had); the calling session's own reload window starts again. Returns `{ok, revoked_sessions}`. |
| `GET /api/auth/device-token` | **Admin, security tier** (read: Bearer only; the cookie alone is `403`) | — | `{token, header}` — the household device token, from the same table the core reads. `401` unauthenticated, `501` before setup. The dashboard calls it right after a login to pair the browser, and Settings → Phones & browsers renders it for an admin. |
| `POST /api/auth/device-token` | **Admin, security tier** | `{"token": "..."}` | Set the household token to one an admin chose — the web mirror of the core's `POST /v1/admin/device-token`, same table, same rule, same 400s. Settings → Phones & browsers → Household token → `set…` calls this. It writes the shared row directly, so the same restart-both warning applies: a web on the new build can set a chosen token that a core still on the old build will refuse. |
| `POST /api/auth/device-token/rotate` | **Admin, security tier** | — | Rotate the household token (`{token, header, rotated}`); the core sees the new one immediately and the file mirror is rewritten. |

### 3.2 Plugins (management proxies + host)

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/plugins/manifest` | Open | — | Everything the frontend shell needs to render plugin pages without a rebuild (pages, nav entries, realtime channels, load errors), and for the plugin page frame each plugin's `publisher` and `accent`: `{source: "plugin"|"default", light: {...}, dark: {...}}`, each theme's `brand`, `brand_hover`, `brand_press`, `brand_fg`, `brand_soft`, `brand_halo`, `brand_ring`, `brand_ink` as `#rrggbb` / `#rrggbbaa`, already contrast-checked (the neutral plugin slate when the plugin declares no `[plugin].accent`). Served by the plugin host router, ahead of the `/{slug}` matchers. |
| `GET /plugins/{slug}/static/{path}` | Open | — | A plugin's `web/static` assets. Containment-checked; `404` when the plugin is disabled. |
| `GET /api/plugins` | Open | — | Installed plugins with the fields the admin list renders: manifest metadata, permissions, capabilities, handlers, pages, `web_load_error`, and `worker_notes` (since 2026-10-15: the core's `GET /v1/plugins` `worker_notes`, asked with a 1.5 s limit; `[]` when the core doesn't answer). |
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
| `GET /api/capabilities` | Open | The live capability map (proxied view of the core registry) the sidebar's "+ add" affordances gate on. Each plugin entry carries `name`, `publisher` and the same `accent` object as `/api/plugins/manifest` (the Android app's plugin screen frame). Also `host_kind` — `linux`, `wsl`, `windows` or `darwin` (`DOMOVOI_HOST_KIND` overrides the detection); `wsl` is a Windows install, where Settings → Rooms explains that USB adoption and writing a card aren't available. `features` holds `chat` (voice chat mode), `office` and `ai`: the household's AI switch (`AI_FEATURES`, Settings → Models), read live from `.env`; `false` hides Chat in the dashboard and the app, and a server without the key has AI. |
| `GET /api/capabilities/manual` | Open | Handler metadata + example phrases for the "What can I say?" manual page (from core `GET /v1/handlers`). |

### 3.4 Acquisitions

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/acquisitions` | Open | `?status=&limit=100` | The generic acquisition queue for the Downloads-style page (reads the DB directly; fulfiller availability included). |

### 3.5 Music: library, player, transport

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/music/library` | Open | `?q=&source=&favorited=&sort=added_desc&limit=50&cursor=` (or `&offset=`) | Paged library listing (§1.7): `{total, items, limit, has_more, next_cursor, offset?}`; sorts `added_desc` (default), `added_asc`, `title`, `artist`, `duration` (NULL titles / artists / durations last). Each track's `file_path` is relative to the music library (`Artist/Album/01 Song.mp3`, `uploads/song.mp3`; a row outside it shows its file name alone), never the absolute server path (WEB-16) — the same in every read that answers with a track, playlists included. `q` matches title, artist, album and that same relative path, so nothing above the music library is searchable either. |
| `GET /api/music/library/stats` | Open | — | Library totals for the Stats card. |
| `GET /api/music/library/{track_id}` | Open | — | One track. |
| `PATCH /api/music/library/{track_id}` | **Device** | `TrackPatch` (title/artist/favorited/...) | Edit track metadata. |
| `DELETE /api/music/library/{track_id}` | **Admin (Bearer)** | `?also_file=false` | Remove a track row (optionally the file too). `204`. `401` without an admin session, `403` for the dashboard cookie alone — the row and the file are left alone either way. |
| `GET /api/music/library/{track_id}/playlists` | Open | — | Playlists containing this track. |
| `POST /api/music/library/upload` | **Device** | multipart audio file(s) and/or `.zip`, optional `device_id` field | Upload straight into the library. `403` when the calling device is under a Files block (§3.13), checked before anything is written; the caller is identified as on the Documents door — the `device_id` field, `X-Device-Id`, `?device_id=` or the `domovoi-device-id` cookie. The files just written are indexed **in-process** (`library_indexer.index_paths`, bounded to this request's own files) and then the core is asked for an MPD rescan — deliberately *not* the admin-gated library-wide sweep, which a device-tier caller cannot reach. `reindex_triggered` reports whether that index ran. A zip is checked before anything is inflated: `413` when it has more than 5000 members, any member declares more than 1 GiB, or the members declare more than 4 GiB in total. Each name is judged as it will be stored, as on the Files and Documents doors: a control character anywhere, and on a Windows host a `:` (an NTFS stream) or a trailing dot or space, lands in `skipped` with the reason. `400` when nothing supported was found. |
| `GET /api/music/library/{track_id}/audio` | Open | `?download=` | Stream the file to the browser player (range requests). `?download=1` serves it as an attachment (save to device) named from the on-disk basename. `404` "file missing on disk" and the `400` for a row outside `MUSIC_DIR` name the track id, never its path. |
| `GET /api/music/library/{track_id}/cover` | Open | `If-None-Match` | Cover art, read from the file on every request the client's cache doesn't answer — nothing is stored. The picture built into the file (ID3 `APIC`, front cover preferred; MP4 `covr`; FLAC pictures; Vorbis/Opus `METADATA_BLOCK_PICTURE` / `COVERART`; WMA `WM/Picture`), else the first image in the track's own folder named `cover` / `folder` / `front` / `album` (`.jpg` `.jpeg` `.png` `.webp`, any case). Served as-is with the content type its bytes show, a strong `ETag` (the audio file's path, mtime and size, plus the folder image's) and `Cache-Control: public, max-age=86400`; `304` when `If-None-Match` matches; `404` when there is none or it is over 8 MiB (cacheable for 5 minutes); `400` for a row outside `MUSIC_DIR`, as `/audio`. Open because `<img>` tags and the phone's lock screen fetch it by URL. The answer for each version of a file is kept in memory (32 MiB of pictures at most) and a file is read once however many ask; tags are read two files at a time, each read within the tag reader's budget. |
| `DELETE /api/music/acquisitions/{acq_id}` | **Device** | — | Cancel a pending acquisition. `204`. |
| `GET /api/music/now-playing` | Open | — | Per-room now-playing (from the cached core snapshot + MPD), with source attribution. `added_by` (the device that queued the song) is named only to a household credential — the household token, an admin session or the dashboard cookie, or the pre-setup grace; for anyone else it is `null`. |
| `POST /api/music/now-playing/{room_id}/favorite` | **Device** | — | Heart whatever the room is playing (re-searches by title into the library/queue). |
| `POST /api/music/play` | **Device** | `{room_id, query, device_id?}` | Proxy → core `/v1/admin/music/play` (full voice pipeline). `403` when the calling device is blocked from this room's queue (§3.5a). |
| `POST /api/music/play-track` | **Device** | `{room_id, track_id, device_id?}` | Proxy → core direct-play (no conversation log, no external fallback). `403` when the calling device is blocked from this room's queue (§3.5a). |
| `POST /api/music/play-tracks` | **Device** | `{room_id, track_ids, start_sec?, start_paused?, device_id?}` | Proxy → core queue cast (`start_sec` forwarded only when > 0, `start_paused` only when true). `403` when the calling device is blocked from this room's queue (§3.5a). |
| `POST /api/music/play-playlist` | **Device** | `{room_id, playlist_id, shuffle?, device_id?}` | Proxy → core playlist start. `403` when the calling device is blocked from this room's queue (§3.5a). |
| `POST /api/music/add-by-query` | **Device** | `{room_id, query, artist?, attach_to_playlist_id?}` | Proxy → core acquisition enqueue. |
| `POST /api/music/add-by-url` | **Outbound-fetch (core decides)** | `{room_id, url, title?, dedup_key?, attach_to_playlist_id?}` | Proxy with credentials + source address forwarded; the core's verdict passes back verbatim. |
| `POST /api/music/{pause\|resume\|stop\|skip}/{room_id}` | Open at this hop (FE-3); the core route is **Device or guest** | — | Transport proxies → core `/v1/admin/music/{action}/{room_id}`; its status passes through (a control that didn't happen is `502`/`409`/`503`, not 200). This hop asks for nothing (they are the video satellite's kiosk transport row), but the core route behind them takes the device tier — or a guest while the room is playing, for pause, resume and skip (never stop) — so the kiosk has to be paired for all its buttons to work (§3.7, [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md)). Not covered by queue blocks (§3.5a); a guest ban covers a guest's presses. |
| `POST /api/music/previous/{room_id}` | **Device or guest** | — | Proxy → core `/v1/admin/music/previous/{room_id}`: back one song in the room's queue (the app's and the dashboard's previous while casting). Not a kiosk verb, so not open; a guest's press is passed through (the core decides) unless guests are off or the guest is banned. |
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
`song_id` + `added_by` pair for whatever each room is currently playing
(`added_by` named only to a household credential, `null` otherwise).

**Guests** (owner decisions 2026-10-10): the read, add, remove and move take
a caller with no credential while the room is playing — see the **Device or
guest** tier above. For a guest the read sets `guest: true` — when this hop sees no
credential, and also whenever the core says its answer went to a guest (a
`?device_token=` here is not forwarded, so it buys nothing) — shows no
household device names, ids or times (an entry a guest queued reads
`added_by: "a guest"`, never the name of a device whose id the guest
claimed), gives a blocked guest a generic `blocked_reason` rather than the
block's own message, and
answers a room that isn't playing with `200`, no items, `editable: false`
and `blocked_reason: "Pair this phone to change a room that isn't
playing"`, so the dashboard opens no pairing prompt for a look. A banned
guest reads `editable: false` too. Every dashboard request carries the
browser's self-asserted `X-Domovoi-Device-Id`, and the web hop forwards it,
the caller's `X-Requested-With` and its user agent (`X-Forwarded-User-Agent`)
to the core, which matches guest bans on the id, refuses a guest request
with neither custom header, and lists the guests it saw. For an **admin**
reader the read sets `admin: true` and gives each guest-added entry
`guest_device_id` and `guest_source` — what a ban takes.

| Method & path | Auth | Request | Response / purpose |
|---|---|---|---|
| `GET /api/music/queue/{room_id}` | **Device read or guest (core decides)** | `?device_id=` | The queue with provenance joined: `{room_id, items:[{song_id, pos, file, title, artist, album, duration_sec, added_by, added_by_device_id, added_at, guest_device_id, guest_source, playing}], current_song_id, editable, blocked_reason, guest, admin}`. Reading is **never** blocked — a blocked device still sees what's on, and `editable:false` + `blocked_reason` say why it can't change anything. Reaps provenance rows whose songid has left the queue. |
| `POST /api/music/queue/{room_id}/add` | **Device or guest** | `{track_ids:[..], device_id}` | Append, then stamp provenance (a guest's: `added_by` "a guest", with its id and address kept for an admin). Proxy → core. `403` when the device is blocked here or the guest banned. |
| `POST /api/music/queue/{room_id}/remove` | **Device or guest** | `{song_ids:[..], device_id}` | Drop entries and their provenance rows. `403` when blocked or banned, or (core) for a guest naming the entry playing now. |
| `POST /api/music/queue/{room_id}/move` | **Device or guest** | `{song_id, to_position, device_id}` | Reorder. No DB write — provenance is keyed by songid, which is exactly why. `403` when blocked or banned. |
| `POST /api/music/queue/{room_id}/clear` | **Device** | `{device_id}` | Empty the queue and wipe the room's provenance. `403` when blocked. Never a guest's (`401`). |
| `GET /api/music/queue-blocks` | **Admin (read)** | — | Every block: `[{id, device_id, device_name, room_id, note, created_at}]`. `room_id: null` = every room. |
| `POST /api/music/queue-blocks` | **Admin (mutation)** | `{device_id?, device_name?, room_id?, note?}` | Block a device from editing a queue. Needs at least one of id/name (`400` otherwise); `409` when that device is already blocked at that scope. |
| `DELETE /api/music/queue-blocks/{id}` | **Admin (mutation)** | — | Unblock. `204`; `404` unknown id. |
| `GET /api/music/guests` | **Admin (read)** | — | The guests that changed a room's music in the last 7 days, newest first (≤100): `[{device_id, source, user_agent, room_id, last_action, first_seen, last_seen, ban_id}]` — `ban_id` is the ban that covers that guest now, by id or by address. Recorded by the core (V023 `guests_seen`: at most 10 rows per address, deleted 30 days after last seen by the hourly `guests_seen_pruner`). |
| `GET /api/music/guest-bans` | **Admin (read)** | — | Every guest ban: `[{id, device_id, source, note, created_at}]`. |
| `POST /api/music/guest-bans` | **Admin (mutation)** | `{device_id?, source?, note?}` | Ban a guest from every room's queue edits and transport, matching its device id OR its address. Needs one of the two (`400`); `409` for a duplicate. Never affects a household credential. Household policy, not a security boundary. |
| `DELETE /api/music/guest-bans/{id}` | **Admin (mutation)** | — | Unban. `204`; `404` unknown id. |
| `POST /api/devices/register` | **Device** | `{device_id, name?, platform?, user_agent?, owner_person_id?}` | Upsert this client's row and bump `last_seen_at`. `owner_person_id` (V035) is the app's "This phone belongs to" for this server: a `people.id`, or `null` to clear it; left out, the stored value stays (a browser never sends it); an id no person has is stored as nobody. Voice help sends steps to the phones marked as the asker's (§3.18a). Idempotent — clients call it every boot (the dashboard only once it can pass this tier, again whenever a credential arrives, when the tab comes back, and every 2 minutes while it is visible; a refusal never prompts). `name` seeds the row only when it is NEW, so a client that always sends its platform default can't overwrite a chosen name. Answers the device's own row, `{device_id, name, platform, user_agent, first_seen_at, last_seen_at, shared_screen}` — `shared_screen` is how the dashboard (and the Android app, which registers once it is paired or its live connection is up, again when either changes, and every 2 minutes while it is on screen) learns an admin marked it a shared screen. `400` malformed id. |
| `PATCH /api/devices/{device_id}` | **Device** | `{name}` | Rename. Whitespace is collapsed so two names can't look identical yet block differently. `404` if the device has never registered. |
| `PATCH /api/devices/{device_id}/shared-screen` | **Admin (mutation)** | `{shared_screen: bool}` | Mark a device as a shared screen (the kitchen tablet), or back to a personal one; answers the device row. Admin tier on purpose — the device's own household token is refused (`401`/`403`), so a device cannot un-share itself through this route. Presentational only: what the dashboard's Home hides on a shared screen (calendar titles and locations, a reminder's text, problem rows) is readable by the tablet itself, which is paired — and the problem rows (`/api/health`) are readable with no credential at all — an unpaired browser (a private window on the tablet included) is never masked, and the flag belongs to a self-asserted device id kept in the browser's storage — clearing it, or registering under a new id, gives an unmarked device. The device learns a change on its next register (at most about two minutes while the page is open). Leaves `last_seen_at` alone. `404` unregistered, `400` malformed id, `503` on a database without V014. |
| `GET /api/devices` | **Admin (read)** | `?limit=200` | The device roster, most-recently-seen first, each row with its `shared_screen` flag. Admin-gated: it's an inventory of what's on the network. Feeds the blocklist editor and the shared-screen toggle, so an admin picks a device from a list instead of typing an id. |
| `GET /api/devices/android-app` | Open | — | Settings → Phones & browsers → **get the Domovoi app** (2026-10-15): `{join_url, join_problem, release, apks, ready}` — the server's join page without a room code (`<core>/join`, or `null` with `join_problem` when this server's address is not on the home network) and the core's public `GET /v1/android-app` (`unreachable: true` when the core doesn't answer). Open, like the page it points at. The Android app's "a newer app is available from your server" notice reads it too (2026-10-16, `android/…/update/AppUpdate.kt`; a room-mode phone reads the core's `GET /v1/android-app` instead). |

`device_id` is **required** on every edit and optional only on the read. Not
because it proves anything — it is self-asserted — but because a blocklist
anyone evades by omitting the field is no control at all, and leaving it out is
far easier than claiming someone else's id. A missing `device_id` on an edit is
a `422`.

**The block also covers the routes that replace a queue** (WEB-11):
`POST /api/music/play`, `/play-track`, `/play-tracks` (the browser player's
cast) and `/play-playlist` answer `403` with the block's message for a blocked
device, before the core is asked. There `device_id` is optional — no client
sends it on those routes yet — and the caller is identified as the Files and
Documents doors identify it: the body's `device_id`, then `X-Device-Id`,
`?device_id=`, then the `domovoi-device-id` cookie every browser carries from
`POST /api/devices/register`. A caller that names itself nowhere (today's
Android app, which keeps no cookie jar and sends no `X-Device-Id`) is not
identified, so no block matches it. **Not covered, by decision:** the transport
verbs — pause, resume, stop, skip, previous. They move the playhead through
what is queued rather than changing it, they are the kiosk's buttons, and a
block on them would hold only for a caller that volunteered its id.

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

### 3.5b Music: "also called" names

Other names the household says for a library artist, album or song — a
nickname, how a styled name is actually said, a name speech-to-text keeps
hearing — so "play <name>" finds the right thing (table `library_aliases`,
migration V019). The track drawer shows them as an editable list per target
(the song, each of its artists, its album), and the voice can teach them too:
"when I say X I mean Y" / "forget that name" / "what else is Y called"
(handler `music_alias`, band 295).

* **One row per name → target.** A target can have any number of names; a
  NAME means exactly one thing in the household. Names are compared by their
  spoken key (`spoken_names.alias_key`: case, punctuation and spelling-out are
  ignored, so "Gramps", "gramps!" and "GRAMPS" are one name).
* **A name already meaning something else is a question, never a second
  meaning:** `409` with `{"detail": {"code": "alias_taken", "existing":
  LibraryAlias, "can_replace": bool, "message": str}}`; the dashboard asks
  "Replace it?" and, on yes, POSTs again with `replace: true` (which moves the
  existing row to the new target). The same name for the same target is a
  `200` no-op.
* **A name MAY take over another library name** (a song title taught to mean
  an album): the answer's `shadows` lists what it now plays instead of, and
  the resolver prefers a household name on an exact tie. MusicBrainz names
  (the opt-in lookup) never do, and never displace a household name.
* **Who may remove or replace what:** an admin anything; anyone a MusicBrainz
  name (removing one hides it — `suppressed` — so the next lookup does not add
  it back); a paired device the names IT added, by the device id its request
  carries (the dashboard's `domovoi-device-id` cookie or `X-Device-Id`); a
  voice speaker the names the same recognized person added (or, unrecognized,
  ones added by voice from the same room). Everything else is `403` "Only an
  admin can remove a name someone else added." A device id is self-asserted,
  exactly as for the queue blocks above: **household policy, not a security
  boundary.** A client that sends no id (the Android app today) adds names
  only an admin can remove.

`LibraryAlias` = `{id, alias, alias_key, target_type: artist|album|track,
target_key, target_name, target_artist_key, target_track_id, source:
manual|voice|musicbrainz, created_by_kind: admin|device|voice|system, added_by,
created_at, suppressed, can_remove}`. `added_by` is "admin", the adding
device's name, "voice in <room>" or "MusicBrainz" — never another device's raw
id, and the device's NAME only for a caller on the device tier (a device token,
an admin Bearer, the dashboard cookie or the pre-setup grace): an open read
with no credential gets "a device" instead, since `GET /api/devices`, the
inventory those names come from, is admin-only. `can_remove` is for the caller
of THIS request.

| Route | Tier | Body / query | Notes |
|---|---|---|---|
| `GET /api/music/aliases` | Open | `?target_type=&target_key=&track_id=&source=&include_suppressed=false&limit=200&offset=0` (`limit` ≤ 500) | `{items:[LibraryAlias], total}`, household names first. |
| `GET /api/music/aliases/for-track/{track_id}` | Open | — | The drawer in one call: `{track:{id, title, aliases}, artists:[{name, key, credit, aliases}], album:{name, key, artist_key, artist_name, aliases}\|null}`. One `artists` row per performer in the credit; a credit that reads like one band's name ("Earth, Wind & Fire" — only `,` `&` `;` between at most four parts) also gets a row for the whole credit, first, with `credit: true`. `404` unknown track. |
| `POST /api/music/aliases` | **Device** | `{alias, target_type: artist\|album\|track, target_name?, target_artist_name?, target_track_id?, replace=false}` | `201 {status: "added"\|"replaced", alias, shadows:[{type, name}]}`; `200 {status: "exists", ...}`; `409 alias_taken` (above); `403` replacing someone else's; `404` the target is not in the library (an album with `target_artist_name` must have a track whose primary artist is that artist); `422 {"detail": {"code": "empty"\|"too_long"\|"nothing_to_say"\|"already_its_name", "message"}}` — a name is 1–120 characters after trimming (control and format characters — NUL, a right-to-left override, zero-width ones — are dropped first), says at most 240 characters' worth of spoken key (a long run of digits reads as many words: `too_long` too), and the target's own library name is not another name for it. |
| `DELETE /api/music/aliases/{alias_id}` | **Device** | — | `204`. A MusicBrainz name is hidden, a household one deleted. `403` someone else's (admin only); `404` unknown or already hidden. |
| `GET /api/music/aliases/status` | Open | — | The Stats card: `{household, musicbrainz, suppressed, fetch:{enabled, state, artists_total, checked, matched, no_match, ambiguous, errors, aliases_added, last_lookup_at, last_error}}`. Counts come from the database; `enabled` / `state` / `last_error` from the core's snapshot key `music_alias_fetch` (`enabled: null, state: "unknown"` until the core reports one). |

Every write is a plain database write: the core's spoken-name resolver sees it
on its next request through a cheap library/aliases fingerprint (a voice add
drops the fingerprint in-process, so "play <name>" works on the very next
turn).

### 3.5c Music: lyrics

Lyrics for the library's songs (migration V021: table `track_lyrics`, view
`track_lyrics_shown`), which the dashboard's and the app's players show for
what is playing — line by line, the current line highlighted, when they are
timed; as plain text otherwise. The core fills the table: its local scan reads
a `.lrc` next to each song (same base name, any case) and the lyrics inside the
file (ID3 `USLT` / `SYLT`, Vorbis `LYRICS` / `UNSYNCEDLYRICS`, the MP4 lyrics
atom, `WM/Lyrics`, APE `Lyrics`; LRC-formatted text counts as timed), and, only
when switched on, its LRCLIB lookup adds timed lyrics for songs that have none
of their own (and may save them as a Domovoi-marked `.lrc` beside the song).
What is SHOWN is decided by the view, never by the client: the owner's own
`.lrc` first, whatever it holds; then timed lyrics beat plain ones, and within
each kind the song's own tags beat LRCLIB.

**Household tier only — Device read** (the household token in `X-Device-Token`
or `?device_token=`, an admin Bearer, the dashboard cookie, or the pre-setup
grace); a bare request is `401`. Unlike `/api/music/library`, `/now-playing`
and `/cover`, which stay open and carry no lyrics, nothing here answers an
open page — the kiosk display (`display.html`) never shows lyrics. Lyrics are
the household's own copy of copyrighted text for its own songs. Every answer,
the refusals included, carries `Cache-Control: no-store`. On a database that
has not taken V021 every route answers `503 {"detail": "lyrics are not set up
on this server yet"}`. Nothing here writes, and no error body or log line
carries a lyric.

| Route | Tier | Notes |
|---|---|---|
| `GET /api/music/library/{track_id}/lyrics` | **Device read** | One song's `LyricsDoc` (below). `404 {"detail": "track N not found"}` when the track is not in the library; `422` for an id that is not a positive 32-bit integer. |
| `GET /api/music/now-playing/{room_id}/lyrics` | **Device read** | What the room plays and where, with its lyrics, in one read — the room's MPD read (the same as `GET /api/music/now-playing`) plus the doc: `{room_id, state: play\|pause\|stop, track_id, elapsed_sec, duration_sec, read_at, line_index, lyrics: LyricsDoc\|null}`. `track_id` and `lyrics` are null when nothing plays or the song is not a library track (a stream). `line_index` (only for timed lyrics, else null) is the last line whose `t` is at most `elapsed_sec` × 1000, `-1` before the first. `read_at` is the server's clock right after the MPD read — informational: a client carries the position forward from when IT received the answer, since the two clocks need not agree. `404` for a room that is not provisioned. |
| `GET /api/music/lyrics/status` | **Device read** | The Music page's Jobs card: counts from the database merged with the core snapshot's `lyrics` and `lyrics_index` keys (§2.5): `{tracks, scanned, with_lyrics, synced, plain, instrumental, by_source: {sidecar, embedded, lrclib}, lrclib: {enabled, state, asked, found, not_found, instrumental, skipped, errors, due, next_retry_at, rate_limited_until, paused_until, last_error}, lrc_files: {enabled, written, exists, edited, deleted, failed, last_error}, scan: {state, unscanned, last_pass_at}, index: {state, pending, indexed}, search_enabled}`. `next_retry_at` is the earliest LRCLIB retry of a "not found"; `lrc_files.enabled` is the LRCLIB lookup on AND "Save lyrics as .lrc files" on; `lrc_files.last_error` the most frequent reason a `.lrc` could not be saved (`permission`, `outside_music_dir`, `io`, `name`); `index.pending` the songs whose words are not searchable yet. States, `due`, the timers and the switches come from the core; until it has reported, the states read `"unknown"` and the switches `null`. Counts and short codes only — never a title or a lyric. |

`LyricsDoc` = `{track_id, status: synced|plain|instrumental|none, checking,
source: sidecar|embedded|lrclib|null, source_label, lines: [{t, text}]|null,
text|null, updated_at}`:

* `status` — `synced` (timed `lines`, and the same words as plain `text`),
  `plain` (`text` only), `instrumental` (LRCLIB says the song has no words) or
  `none`;
* `lines` — `t` in milliseconds from the start of the song, the file's
  `[offset:]` already applied, in time order; `text: ""` marks an
  instrumental gap;
* `checking` — Domovoi is still looking: the song has no lyrics row yet, the
  local scan has not read its files yet, or nothing is shown while the LRCLIB
  lookup is on, has not asked about the song, and the file is there;
* `source_label` — where the shown words came from, in words: `from <the .lrc's
  file name>`, `from the song file` or `from LRCLIB`.

**Realtime.** `WS /ws/state` (household tier, §1.5) pushes `lyrics.changed`
with a digest of the table — `{count, with_lyrics, synced, asked, lrc_written,
index_pending, max_updated, fetch_state, scan_state, index_state}` (`{}`
without V021) — whenever it moves (the core's lyrics workers bump
`updated_at` on every change they make). Counts and states only, never a
title and never a lyric: it tells the Jobs card, and a player that is still
"looking for lyrics…", to read again.

### 3.6 People

What a person said and what the house keeps about them — their conversation
turns, the notes that name them, their memories, favorites and preferences —
is read on the **Device read** tier (§1.1): a paired device only, `401` for
anything else on the LAN. So are the roster, one person's row and their
session list (2026-10-08, WEB-15): `last_seen_at` and "when and in which
room they spoke" are presence, which `/ws/state` already withheld from an
unpaired client. The voice-profile rows stay **Open** (enrolment metadata,
no words anybody said; left open pending an owner decision). The memory /
favorite / preference edits are **Device**
tier — a person's own content, written by whichever household client they are
using. The two deletes that lose identification data — forgetting a person and
dropping a voice profile — are **Admin (Bearer)**: `401` without an admin
session, `403` for the dashboard cookie alone.

Person-centric views over the voice-profile / memory tables.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/people` | **Device read** | Everyone Domovoi has voice-identified, with `last_seen_at`, and `namesakes`: how many OTHER people have the same name (case and surrounding space aside). Voice enrolment makes a second person with a name only on purpose (a "no" to a satellite's "I already know someone called X. Is that you?"); a non-zero `namesakes` is such a pair (or one from before that rule), which voice ID won't guess between when the name is said, so the People page flags it. |
| `GET /api/people/{person_id}` | **Device read** | One person (with `namesakes`). |
| `PATCH /api/people/{person_id}` | **Admin** · `{name}` (1-60 characters; runs of whitespace become one space) | Rename a person: what voice ID calls them and the name "my name is …" finds them by. A name someone else already has is `409` (one name, one person); changing only the case of a person's own name is fine. `404` unknown person, `422` an empty name or one with control characters. Answers the updated person. |
| `DELETE /api/people/{person_id}` | **Admin** | Forget a person: what they said (every `conversation_log` and `intents_log` row voice identification attributed to them as the speaker, and the short-term conversation of the sessions those turns were in), then the person with their voice samples, memories, favorites and preferences, in one transaction (2026-10-17). A turn in which someone else said their name is that speaker's and stays. `404` unknown person, nothing deleted. |
| `GET /api/people/{person_id}/sessions` | **Device read** · `?limit=20` | Recent conversation sessions. |
| `GET /api/people/{person_id}/conversations` | **Device read** · `?limit=50` | Recent conversation turns. |
| `GET /api/people/{person_id}/notes` | **Device read** | Notes mentioning them. |
| `GET /api/people/{person_id}/profiles` | — | Their voice samples (metadata only, never an embedding), newest first: `id, person_id, model, enrolled_at, room_id, sample_seconds, source`. `source` is `satellite` (heard by a room's satellite: what satellites match) or `phone` (said into the app's chat mic, filed under `phone-<device id>`: what a phone turn matches first; satellites never use them), V026. Also `claimed` and `claimed_at` (V029): the sample is not the person's own voice — it was added because somebody said their name and "yes", or a room learned it from a voice recognized only through such a sample; "forget me" from a voice that doesn't match the person's own removes only these. For an admin caller (Bearer, the dashboard cookie, or before setup) also `claim_room` (where the yes was said), `claimed_by_person_id` / `claimed_by_name` (who that voice was recognized as; null: an unknown voice) and `claim_of` (the claim a learned sample stands on). Open, but for anyone else a phone sample's `room_id` is null (it names the phone) and the claim details are null (delta review N4). The dashboard's People page lists the two sets apart and marks the claims. |
| `DELETE /api/people/{person_id}/profiles/{profile_id}` | **Admin** | Drop one voice sample, either set; a claimed one takes what rooms learned through it (V029). |
| `GET /api/people/{person_id}/memories` | **Device read** · `?status=` | Extracted memories (pending ones included). |
| `POST /api/people/{person_id}/memories` | `MemoryCreate` | Add a memory manually. |
| `PATCH /api/people/{person_id}/memories/{memory_id}` | `MemoryPatch` | Edit/confirm/reject a memory. |
| `DELETE /api/people/{person_id}/memories/{memory_id}` | — | Delete a memory. |
| `GET /api/people/{person_id}/favorites` | **Device read** · `?kind=` | Favorites (songs, stations, …). |
| `POST /api/people/{person_id}/favorites` | `FavoriteCreate` | Add a favorite. |
| `DELETE /api/people/{person_id}/favorites/{favorite_id}` | — | Remove a favorite. |
| `GET /api/people/{person_id}/preferences` | **Device read** | Per-person preferences. |
| `PATCH /api/people/{person_id}/preferences` | `PreferencesPatch` | Update preferences. |

Related: the voice-identification denylist — `GET /api/denylist` (Open) and
`DELETE /api/denylist/{entry_id}` (**Admin (Bearer)**: removing an opt-out
puts someone back in front of the matcher, so it answers to the operator).

**Voice history** (People → Voice history, 2026-10-17;
`domovoi/voice_history.py`). "Keep voice history" is the setting
`voice_history_keep` (`30`, `90` (the default), `365` or `forever`), written
like any other through `PATCH /api/config/editable` (**Admin, security
tier**); a save deletes what is now older at once, and the core's daily
cleanup does it from then on.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/voice-history` | **Device read** | `{keep, keep_label, keep_days, choices: [{value, label}], turns, oldest_at, delete_all, last_cleanup}`: the setting (`keep_days` is `null` for forever), how many conversation turns are kept and the oldest one's time, and the latest "Delete all voice history" and daily cleanup, each a job `{id, kind, cutoff, requested_at, started_at, finished_at, deleted, error, state}` (`state`: `queued`, `running`, `failed`, `done`; `deleted`: counts per store — `conversation_log`, `intents_log`, `sessions_deleted`, `sessions_trimmed`, `satellite_log_lines`) or `null`. Counts and times only, never what was said. The setting is the core's live value (its snapshot), or this process's copy while the core hasn't answered. |
| `POST /api/voice-history/delete-all` | **Admin, security tier** (`501` before setup) | Delete all voice history: every turn said until now, everywhere, with Domovoi's answers, the routing log, every room's short-term conversation, and the satellites' logs kept on the server. Forwarded to the core's `POST /v1/admin/voice-history/delete-all` with the caller's credentials; answers `{job}` at once and runs in the background (read its progress from `GET /api/voice-history`). `502` when the core is unreachable. |

### 3.7 Satellites

Reads are **Open**, except a room's conversations and voice notes — what was
said there — which are **Device read** (paired devices only), and the log pull,
which carries the same speech raw and is an **Admin read** at both hops. The
room list, one room's row, a room's session list and the pending-adoption
list are **Device read** too since 2026-10-08 (WEB-15): presence, Wi-Fi SSIDs,
hardware, synced code, call partners, who spoke when, and the MAC of a device
being adopted are household state the `/ws/state` handshake already gated. The
kiosk display reads its own room's row, so it is paired by its URL
(`/display.html?room=<room_id>&device_token=<household token>`); unpaired, it
falls back to the room id and the clock. The
action endpoints carry the tier the core route behind
each one carries, so the two hops agree: room label, timer cancel, announce,
announce-all and volume are **Device**; restart, display and the config push
are **Admin (Bearer)**; code push, pairing reset, adopt and delete are the
**security tier**. Live state comes from the cached core snapshot; actions
proxy to the core admin endpoints with the caller's credentials forwarded.
The per-room "Only reminders for this device" setting is written straight to
the shared database on the **Device** tier.

**Timers and rule M1.** A reminder's words are household speech. Every open
timer read — `GET /api/timers` (and its `fires`), `GET /api/satellites/{room_id}/timers`
and `GET /api/timers/fires` — answers a caller that holds no household
credential (the device token, an admin Bearer, the dashboard cookie, or the
pre-setup grace) with every reminder's `message` and `label` null and
`masked: true`, whatever room it was set in. The row itself stays: countdown,
room, kind and, for a fire, where it was heard — so Home keeps working on an
unpaired kitchen tablet. A plain timer's label ("pasta") is not masked.

**Rule F1 — the fire history.** The same credentials read the whole fire
history (V018, kept 7 days): every room's outcome and reason code, who
stopped a timer and when. Anyone else — no credential, a stale token, a
throttled source — reads **the last 10 minutes only**, and each fire cut down
to what Home's done line and the alert card draw: `deliveries` is `[]`,
`acked_at`, `acked_by` and `settled_at` are null, and `summary` loses its
` · stopped in <room>`; the ids, kind, origin room, times, `heard_in`, the
rest of `summary` and a plain timer's label stay. `GET /api/timers/fires`
says which view it answered with `window_sec` (`null` = the whole history,
`600` = the last 10 minutes); an empty windowed answer says nothing about
older fires. The caller is classified only when the answer depends on it —
a reminder in it (M1), a fire in it (F1) — so an ordinary read charges no
token backoff; `GET /api/timers/fires` always classifies, since how far back
it reaches depends on the caller.

**Fire** (the object `fires` / `GET /api/timers/fires` / the `timer_fires`
channel carry): `{id, timer_id, kind: "timer"|"reminder", is_reminder, label,
message, masked, room_id, created_at, due_at, fired_at, settled_at, acked_at,
acked_by, heard_in: [room], summary, deliveries: [{room_id, is_origin,
outcome, detail, finished_at}]}`. `room_id` is the room it was SET in (null =
set with no room). `deliveries` lists the origin room first, then the rest
A→Z; `outcome` is `pending`, `sending`, `spoken`, `interrupted`, `failed`,
`offline`, `busy_timeout` or `cancelled`; `detail` is a reason code
(`capturing`, `forced_over:followup`, `acknowledged:kitchen`, …), never
speech. `heard_in` is the rooms where it was `spoken` or `interrupted`.
`summary` is computed by the server and shown verbatim: `heard in garage,
kitchen` (+ ` · still announcing`), `announcing…`, `not announced in any
room` (no room was going to: none online, or every online room announces
only its own), `not heard in any room` (+ ` (garage offline)`), each + ` · stopped
in kitchen` once acknowledged (the household tier only, rule F1). The words the
core spoke are never served. Without a household credential a fire comes cut
down (rule F1): `deliveries: []`, `acked_at` / `acked_by` / `settled_at` null.

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/satellites` | **Device read** | — | All known rooms with presence, wifi, volume, active voice (`voice`; with `voice_on_server`: true for a room whose voice the server keeps, a phone in room mode, whose `voice` is then the kept one while it is offline too), synced code SHA, full-duplex capability, and `capture_commands` / `capture_since`: whether the room is recording commands (§3.12a) — an admin opted it in and an admin credential exists, exactly what the core acts on — and since when. Every paired device can see that a room records; what it recorded is admin-only. Each row also carries `timers_own_only`: the room's "Only reminders for this device" setting (`false` without V018); `board`, the hardware kind (`linux_pc`, `android_room`, or the board USB adoption recorded for a Pi; kept for offline rooms); and `supports`, what the connected device declared it can do (`null` for a device that sent no list — every Pi — and for an offline room); and `upgrade`, a code upgrade under way or just finished (the core snapshot's `satellite_upgrades` entry, else `null`): the dashboard and the app keep the room's Upgrade button locked while its `state` is `running`. `last_seen_at` (since 2026-10-15) is when the room was last heard from: the newest of its health samples (the 24 h prune keeps each room's newest one, so this stays right for a room silent longer), its last paired hello and its music player row's time; `last_connected_at` is only that last one (set when the room's MPD row was allocated, not at each connect), kept for older clients. While the core doesn't answer every room reads `offline` (presence comes from the core): ask `GET /api/health`'s `domovoi_reachable` before calling a room offline. |
| `GET /api/satellites/{room_id}` | **Device read** | — | One room (the kiosk's label and idle mode). |
| `GET /api/satellites/{room_id}/sessions` | **Device read** | `?limit=20` | Recent sessions in this room, each with the `person_id` who spoke. |
| `GET /api/satellites/{room_id}/conversations` | **Device read** | `?limit=50` | Recent turns in this room. Each carries `utterance_trigger` (`wake_word`/`barge_in`/`followup`/`push_to_talk`; null before V011). |
| `GET /api/satellites/{room_id}/logs` | **Admin (read)** | `?max_bytes=` (1 KB–10 MB, default 1 MB) | Satellite's logs: the live ring over its WS and, since 2026-10-09, the log the core retains for it on disk, with its last outage — the core route's shape (§2.5) passes through. Gated: both hold every transcript the room heard. An offline room answers with `live: null` and what is retained; `404` only when nothing is held for it at all. |
| `GET /api/satellites/{room_id}/health` | **Device read** | — | Proxy → core `GET /v1/admin/satellite/{room_id}/health` (§2.5): the latest health sample, the last outage report and a 24 h summary. The drawer's Health card and Last outage block. |
| `GET /api/satellites/{room_id}/notes` | **Device read** | — | Notes taken in this room. |
| `GET /api/satellites/{room_id}/recently-played` | **Device read** | `?limit=100` | Play history for the room (each play's `started_at` says when the room was in use, so a paired device's read since 2026-10-08). |
| `GET /api/satellites/{room_id}/timers` | Open, M1 | — | Active timers/reminders in this room: `[{id, expires_at, created_at, label, message, room_id, is_reminder, masked}]`, soonest first. `is_reminder` is true when the stored row has a message; a reminder set with no task ("set a reminder for 10 minutes") stores `message: ""` and the label `"10 minute reminder"`, and fires as "Here's your 10 minute reminder." Without a household credential every reminder reads `message`/`label` null, `masked: true` (rule M1 above). |
| `DELETE /api/satellites/{room_id}/timers/{timer_id}` | **Device** | — | Cancel a timer. `204`; `404` when it isn't in that room. Fires `timers_changed`. |
| `GET /api/satellites/{room_id}/timer-announcements` | Open | — | The room's "Only reminders for this device" setting: `{room_id, own_only, since}`. `own_only: false` (the default) = this satellite announces every room's timers and reminders, each named by the room it was set in; `true` = only the ones set on it. The room a timer was set in always announces it. `since` = when it was turned on. `404` for a room this server has never known (neither connected nor adopted). Reads off (`{own_only: false, since: null}`) without V018. |
| `PUT /api/satellites/{room_id}/timer-announcements` | **Device** | `{own_only: bool}` (nothing else) | Turn the setting on or off. Idempotent: on again keeps the original `since`. Answers the same shape as the GET. Works for an offline room. `404` unknown room; `422` bad room id or body; `503` without V018. Device tier like volume and the room label: it only makes a room say less, and the phone app (household token only) carries the same switch. |
| `GET /api/timers` | Open, M1, F1 | — | Every running timer and reminder in the house, soonest first — including the ones set with no room, which no per-room route lists — plus what went off in the last 10 minutes: `{server_now, timers: [{id, expires_at, created_at, label, message, room_id, is_reminder, masked}], fires: [Fire] \| null}`. `server_now` is the database clock (the one timers fire against), so a page counts down against the server rather than the phone's clock; `created_at` + `expires_at` give each timer's full length. `fires` is newest first, at most 20 — Home's "done · kitchen" lines come from it, never from a row vanishing (which is what a cancel looks like too); `null` = this server keeps no fire history (V018 missing), `[]` = nothing went off lately. Every reminder, timer row or fire, is masked for a caller without a household credential (rule M1 above), and each fire cut down for it (rule F1 above). Changes push on the `timers` and `timer_fires` channels. |
| `GET /api/timers/fires` | Open, M1, F1 | `?since_id=` (0 to 2^63−1), `?room_id=` (the room it was SET in, ≤ 120), `?timer_id=` (0 to 2^31−1), `?limit=` (1–200, default 50); out of range is `422` | The fire history (kept `timer_fire_retention_days`, default 7): `{server_now, fires: [Fire], window_sec}`. With `since_id`, only `id > since_id`, OLDEST first — how a client that was away catches up; without it, newest first. **Tiered inside (rule F1 above):** a household credential (the Android app, a paired shared screen, a signed-in dashboard, an admin Bearer) reads the whole history, `window_sec: null`; anyone else only fires from the last 10 minutes, each cut down, `window_sec: 600` — the filters apply within that window. The caller is always classified here (a wrong token pays the backoff). **`503`** `{"detail": "timer fire history needs database migration V018 — run Flyway"}` when the server keeps no history — not an empty list, so a client can tell "unknown" from "none". |
| `DELETE /api/timers/{timer_id}` | **Device** | — | Cancel any timer or reminder, with or without a room. `204`; `404` unknown id. Fires `timers_changed`. |
| `POST /api/satellites/{room_id}/announce` | **Device** | `{message}` | Proxy → core announce (one room). |
| `POST /api/satellites/announce-all` | **Device** | `{message}` | Proxy → core announce (broadcast). |
| `POST /api/satellites/{room_id}/dropin/start` | Proxy — core requires an admin Bearer (security tier: `501` before setup) | `{target_room}` | Proxy → core drop-in start; the caller's credentials are forwarded. |
| `POST /api/satellites/{room_id}/dropin/end` | Open | — | Proxy → core drop-in end. |
| `GET /api/satellites/{room_id}/dropin/phone-info` | **Device (`X-Device-Token` or Bearer)** | — | What a phone client needs to join this room's drop-in (`/v1/dropin/...` URL + capability info). The phone presents the same token again on the upgrade. |
| `GET /v1/satellite-plugins/manifest` (core) | Open | — | `{files: {"<slug>/<rel>": sha256}, meta: {slug: {...}}}` — enabled plugins' `[satellite]` payloads; satellites mirror it like the code channel. Under `INTERNET_ACCESS=never` each `meta` entry carries `"offline": true`, and the satellite's root helper installs that plugin's apt packages from its local caches only. |
| `GET /v1/satellite-plugins/manifest.sig` (core) | Open | — | The payload list inside the same signed envelope shape (`issued_at`, `serial`, `signature`, `signature_v2`), with `channel: "satellite-plugins"`. These are the files whose `post_install` runs as root on the device, so a pinned satellite refuses an unsigned, wrongly-signed or older-than-last-accepted list before it downloads anything, saves the verified envelope beside its mirror, and the device's root helper (`domovoi-apply-payload`) verifies that envelope again with its own root-owned verifier against the root-owned pin before it stages or runs anything — taking each slug's packages and script from the signed `meta`, staging only files the signed list names with the hashes it gives. |
| `GET /v1/satellite-plugins/{path}` (core) | Open | — | One payload file by its `<slug>/<rel>` channel path. |
| `GET /api/satellites/media/status` | **Admin (read)** | — | Media-prep card data: boards, cache state, docker availability, per-plugin payload summary, and what this host can do: `host_kind`, `drive_targets` (`false` inside WSL, which sees no removable drives) and `default_setup_transport` (`usb`, or `portal` inside WSL). |
| `GET /api/satellites/media/targets` | **Admin (read)** | — | Removable drives that look like a flashed Pi boot partition. Always empty inside WSL. |
| `POST /api/satellites/media/prepare` | **Admin (Bearer)**; `423` while the container guard's freeze is on | `{board, mic_profile, target: {kind: drive\|zip, token?}, offline?, setup_transport?: usb\|portal, wifi_country?}` | Start (or attach to) a media build; progress rides the `satellites.media` realtime channel. An omitted `setup_transport` is `usb`, or `portal` inside WSL. Inside WSL a `drive` target is refused with `422` (use the zip). |
| `GET /api/satellites/media/jobs` | **Admin (read)** | `?limit` | Recent build jobs (no server paths; `has_artifact` flags downloadables). |
| `POST /api/satellites/media/jobs/{id}/cancel` | **Admin (Bearer)** | — | Mark a build cancelled (best-effort). |
| `GET /api/satellites/media/jobs/{id}/download` | **Admin (read)** | — | The overlay zip for a `kind=zip` build. Since WEB-1 it carries no plaintext passwords: `userconf.txt` holds the console password's hash, and the setup-AP key and console login are shown once in the dashboard (`/jobs/{id}/credentials`, memory only). A Wi-Fi setup (`portal`) zip also carries `domovoi/claim.json`, `{schema: 1, job_id, secret, expires_at}`: a one-time pass, not a password, that the tray's card helper trades over loopback for the setup key (`POST /claim`, below) and never copies onto the card. A card written straight to a **drive** still gets `domovoi/ap.json` + `domovoi/console.json`, which stage 1 needs. |
| `GET /api/satellites/media/jobs/{id}/credentials` | **Admin (read)** | — | `{ap: {ssid, psk} \| null, console: {username, password}}` (`ap` is null for a USB-adoption card) for a finished build, from the web process's memory (`404` once the dashboard restarts). The dashboard's "show setup details" reads it, for drive and zip builds alike; for a zip it is the only place the passwords appear. After a re-made setup key (below) `console` is null and `reissued` is true. |
| `POST /api/satellites/media/jobs/{id}/setup-key` | **Admin (Bearer)** | `{ssid}` (the setup network's name, from the zip's `domovoi/device-info.json`) | The setup network's key for a finished `kind=zip` build, for the tray's **Sign in as admin to fetch it**: `{ap: {ssid, psk}, reissued}`. While the web process still remembers the build's key it is answered unchanged (`409` for a different network name); after a restart a NEW key is made for the same network (`reissued: true`) and remembered like the first (memory only), so "show setup details" shows it. That is sound because the key lives only in the card's `domovoi/ap.json`, which the tray writes. `404` when there is no finished zip build with that id (prepare the card again); `422` for a name that isn't `Domovoi-Setup-XXXX`. |
| `POST /api/satellites/media/claim` | **None: loopback only** (the zip's one-time pass is the credential) | `{job_id, secret}` (from the zip's `domovoi/claim.json`) | The Windows tray's card helper trades a Wi-Fi setup zip's one-time pass for `{ap: {ssid, psk}}`, so nobody types the key; `Cache-Control: no-store`. Answered only when the TCP peer is loopback and the request carries no `X-Forwarded-For`, `Forwarded`, `X-Real-IP` or `X-Forwarded-Host` (a loopback peer's `X-Forwarded-For` is believed on WSL, round 4's WIN-7, so a forwarded request is never taken as local). Refusals are `{detail, reason}`: `403 not_local`; `410 forgotten` (the web process restarted and no longer has the key, checked before the secret); `403 invalid` (not this build's secret); `410 expired` (24 h after the build); `409 used` (good once). The server keeps only the secret's SHA-256, in memory, beside the credentials it unlocks. Still needs `X-Requested-With`, like every write. |

| `POST /api/satellites/media/cache/refresh` | **Admin (Bearer)**; `423` while the container guard's freeze is on | — | Refresh the wheel/deb/model caches (slow on a cold cache). Returns `{bucket: {ok, message}}` per bucket. `409` under `never` (§1.6); preparing a card still works from what is cached. |
| `GET /api/satellites/pending` | **Device read** | — | Unprovisioned satellites presenting a USB adoption volume on the server, with MAC, board and model (empty when adoption is off, and inside WSL, which sees no USB drives). |
| `POST /api/satellites/pending/{pending_id}/adopt` | **Admin, security tier** | `{room_id, room_label?, wifi_ssid, wifi_psk, wifi_country?, wifi_hidden?, device_profile?, initial_volume?, force?}` | Adopt: preseed pairing on the core and write the provision file to the device. `409` room exists / device re-nonced, `410` device unplugged. |
| `DELETE /api/satellites/{room_id}` | **Admin, security tier** | — | Proxy → core delete (remove a `waiting` room). |
| `PATCH /api/satellites/{room_id}` | **Device** | `{room_label}` | Proxy → core room-label update. |
| `POST /api/satellites/{room_id}/volume` | **Device** | `{level}` | Proxy → core set-volume. |
| `POST /api/satellites/{room_id}/display` | **Admin (Bearer)** | `{action}` (`on` \| `off` \| `restart_kiosk`) | Proxy → core satellite display (video satellites only; `409` otherwise). |
| `POST /api/satellites/{room_id}/restart` | **Admin (Bearer)** | — | Proxy → core satellite restart. |
| `POST /api/satellites/{room_id}/upgrade` | **Admin, security tier** | — | Proxy → core satellite code sync + self-restart; the core applies the same gate (credentials forwarded). `409` while an upgrade of the room is already running (the row's `upgrade.state` says `running`). |
| `POST /api/satellites/{room_id}/pairing/reset` | **Admin, security tier** | — | Proxy → core `DELETE /v1/admin/satellites/{room_id}/pairing`; the core applies the same gate. |
| `GET /api/satellites/{room_id}/config` | **Device read** | — | Proxy → core per-satellite editable config (the same tier at both hops; the caller's credential is forwarded). |
| `PATCH /api/satellites/{room_id}/config` | **Admin (Bearer)** | `{"changes": {...}}` | Proxy → core config push (Pi rewrites `config.toml`, restarts). |
| `POST /api/satellites/{room_id}/voice` | **Admin (Bearer)** | `{"voice": "<name>" \| null}` | Proxy → core room voice (`POST /v1/admin/satellite/{room_id}/voice`): the drawer's voice picker. The room rows' `voice_on_server` says which kind of room it is (true: a phone in room mode, whose voice the server keeps; its `voice` is then the kept one, offline too). |

### 3.8 Calendar

Reads are **Device read** (since 2026-10-08, WEB-15: titles, places and
descriptions of the household's appointments, which `/ws/state` already
withheld from an unpaired client); every write is **Device** tier.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/calendar/events` | **Device read** · `?start=&end=&limit=500` | Events in a window (ISO datetimes). |
| `POST /api/calendar/events` | `CalendarEventCreate` | Create an event. |
| `GET /api/calendar/events/{event_id}` | **Device read** | One event. |
| `PATCH /api/calendar/events/{event_id}` | `CalendarEventPatch` | Edit an event. |
| `DELETE /api/calendar/events/{event_id}` | — | Delete an event. |

### 3.9 Config and version

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/config` | Open | — | Static UI bootstrap: `{bot_name, tts_voice, rooms, web_version, wake_word_min_clips, home_problems_visibility, internet_access}`. `internet_access` is the internet answer (`""` while unanswered, `always`, `sometimes`, `never`), read from this process's environment and `domovoi/.env`: the dashboard greys online controls under `never` and shows the Home row while it is `""`. `home_problems_visibility` (`everyone` \| `summary` \| `admins`, the admin-set `HOME_PROBLEMS_VISIBILITY`) decides who sees the Home page's problem rows; it is the one household setting this response carries, read live from the core's snapshot. |
| `GET /api/config/editable` | **Admin read (Bearer or cookie)** | — | Proxy → core `GET /v1/admin/config` (live values; plugin secrets pre-masked). |
| `PATCH /api/config/editable` | **Admin, security tier** | `{"changes": {...}, "follow_internet": [...]?}` | Proxy → core `POST /v1/admin/config`. Returns `{applied, restart_required, rejected, normalized}` (plus `followed` when `follow_internet` was sent). |
| `POST /api/config/search-test` | **Admin, security tier** | `{"kind", "key"?, "url"?}` | Since W1. Proxy → core `POST /v1/admin/config/search-test`; gated at both hops. |
| `POST /api/config/acoustid-test` | **Admin, security tier** | `{"key": "..."?}` | Since 2026-10-17. Proxy → core `POST /v1/admin/config/acoustid-test` (auth forwarded); gated at both hops. |
| `GET /api/config/internet` | **Admin read (Bearer or cookie)** | — | Proxy → core `GET /v1/admin/internet` (auth forwarded): what Settings → Internet shows. |
| `POST /api/config/internet/search-helper` | **Admin** | `{}` | Proxy → core `POST /v1/admin/internet/search-helper`: Settings → Internet's "start it again". |
| `GET /api/config/version` | **Device read (core decides)** | — | Proxy → core `GET /v1/admin/version`; the caller's token and cookie are forwarded, and the core's `401` comes back as it is (version label, restart mode, the update unit's `last_update` and `bad_sha`). This process adds to `plugins_pending_restart` every plugin whose web module it imported at an older version than the registry row now holds, or that was uninstalled and installed again since it imported it (`where` gains `"web"`; a plugin only this process holds old code for is its own entry), and sets `restart_required` when anything is listed. |
| `GET /api/config/server-identity` | Open | — | Proxy → the `identity` block of core `GET /v1/health`: `{algorithm, fingerprint, public_key}`. What Settings → About shows, so a person can compare this server's fingerprint with the one printed on a prepared satellite card. |
| `POST /api/config/version/check` | **Device** | — | Proxy → core upstream check. |
| `POST /api/config/version/pull` | **Admin, security tier** (`501` before setup) | — | Proxy → core pull (fetch, signature check, fast-forward to the checked SHA); the core's answer, `signature` included, comes back as it is. |
| `POST /api/config/version/channel` | **Admin, security tier** | `{"channel": "stable"\|"main"}` | Proxy → core channel switch (Settings → About & updates's **updates**). Gated at both hops. |
| `POST /api/satellites/enrolment` | **Admin** | `{room, core?}` | Settings → Rooms → add a spare device (2026-10-13): `{payload, join_url, room, room_id, core, fingerprint, bundle_sha256, bundle_size, command, command_windows, windows_runtime, linux_runtime, android_app, usb_stick, conflict}`; `join_url` is what the QR code carries since 2026-10-15, `<core>/join#<payload>` (the server's join page, which hands the phone the app; the app's scanner reads the payload out of it), `android_app` the core's `GET /v1/admin/android-app` answer or `null` (read only: making a code never downloads the app); `command_windows` is the one-line Windows PowerShell command (the same digest check, then `satellite/scripts/install-windows.ps1`), `windows_runtime` the core's `GET /v1/admin/satellite-windows` answer or `null`, `linux_runtime` the core's `GET /v1/admin/satellite-linux` answer or `null` — the enrolment payload (`domovoi-room:1?…`, docs/uml/satellite-protocol.md "Enrolling a spare device"), the room id it connects as, the core it pins (`core` overrides the address this server finds for itself), the one-line Linux install command, and why the room is taken when it is. Writes nothing. `422 {reason, message}` for a room name or address the payload can't carry (`not_home_network` included); `503` when the core gives no identity; the core's `423` passes through while frozen. |
| `GET /api/satellites/usb-stick` | **Admin read** | — | Proxy → core `GET /v1/admin/usb-stick` (the old-computer card). The enrolment answer carries the same status as `usb_stick`. |
| `POST /api/satellites/usb-stick` | **Admin** | `{room, core?}` | **Prepare the stick**: the enrolment payload for `room` (as `POST /api/satellites/enrolment` makes it) handed to core `POST /v1/admin/usb-stick/build`; its answer plus `{room, room_id, core, conflict}`, or the core's `409 {reason, message}` verbatim. `422` for a room the code can't carry, before the core is asked. |
| `DELETE /api/satellites/usb-stick/{build_id}` | **Admin** | — | Proxy → core `DELETE /v1/admin/usb-stick/builds/{build_id}` (**remove it from this server**); `404` for an id that isn't one. |
| `GET /api/satellites/windows-runtime` | **Admin read** | — | Proxy → core `GET /v1/admin/satellite-windows`. |
| `POST /api/satellites/windows-runtime/fetch` | **Admin** | — | Proxy → core `POST /v1/admin/satellite-windows/fetch` (the card's **get the Windows files**). |
| `GET /api/satellites/windows-setup` | **Admin read** | — | Proxy → core `GET /v1/admin/satellite-setup`. |
| `POST /api/satellites/windows-setup/fetch` | **Admin** | — | Proxy → core `POST /v1/admin/satellite-setup/fetch` (the card's **get the Windows Setup**). |
| `GET /api/satellites/android-app` | **Admin read** | — | Proxy → core `GET /v1/admin/android-app`. |
| `POST /api/satellites/android-app/fetch` | **Admin** | — | Proxy → core `POST /v1/admin/android-app/fetch`: the cards' **Download the app to this server**, the only thing that starts a download. |
| `POST /api/satellites/android-app/upload` | **Admin** | multipart `file` | Streamed through to core `POST /v1/admin/android-app/upload` without being held here (the card's **upload an APK**); the core's answer verbatim. `415` for anything but a multipart upload. |
| `GET /api/satellites/linux-runtime` | **Admin read** | — | Proxy → core `GET /v1/admin/satellite-linux`. |
| `POST /api/satellites/linux-runtime/fetch` | **Admin** | — | Proxy → core `POST /v1/admin/satellite-linux/fetch` (the card's **get the Linux Python**). |
| `GET /api/satellites/approvals` | **Admin read** | — | Proxy → pending approvals. Declared before `/{room_id}` so the path parameter can't shadow it. |
| `POST /api/satellites/approvals/{room_id}/approve` | **Admin, security tier** | `{code}` | Proxy → approve (`501` before setup at both hops). `code` must be digits; the core compares it and counts the attempt. |
| `POST /api/satellites/approvals/{room_id}/reject` | **Admin, security tier** | — | Proxy → reject (`501` before setup at both hops). |
| `GET /api/config/core/restart` | **Device read** | — | Since 2026-10-15. Whether the core's event loop answers (any answer on `GET /v1/alive` within 3 s), and when it doesn't, what `POST` below would do: `{answering, db_down, health, capable, how, why, starting, manual_command, silent_sec, silence_needed_sec}`. While it answers, `health` is the status `/v1/health` gave (`null`: none in 3 s) and `db_down` is true for its database's `503`; nothing is offered. `silent_sec` is how long the core has given no answer, as this process has watched it (each read counts; more than 12 s between two silent probes starts the count again), and `silence_needed_sec` how long a press needs (`LOOP_WATCHDOG_STALL_SEC` + 15 s, at least 45). `how` is `signal` (the web ends the core's main process; systemd's `Restart=` starts it again) or `sudo` (the core's own restart grant, where this process can use it); `why` says why not, `starting` is true while the core is starting (systemd says so, or, under a unit that isn't `Type=notify`, it hasn't left its ready note, one naming its PID written after it started, and started less than `LOOP_WATCHDOG_STARTUP_SEC` ago) or was just restarted, and must be left to it, `manual_command` is the command for a person (`null` without systemd). |
| `POST /api/config/core/restart` | **Admin, security tier, checked by the web** | — | Since 2026-10-15. Restart a core that doesn't answer: the web checks the Bearer against the shared `admin_sessions` table itself (the core can't), refuses while the core's loop answers at all (`ok: false, answering: true`, with `db_down` when its database is down: a restart can't help; otherwise use the restart below) or is starting, waits until it has given no answer for `silence_needed_sec` (asking every second; an answer meanwhile ends the wait with `answering: true`, a new main PID with `starting: true`), reads every guard again for the same process, then sends the core SIGUSR1 (faulthandler writes every stack to `~/.domovoi/logs/core-faulthandler.log`) and SIGKILL, and notes it for `last_watchdog_restart`. One press at a time: another meanwhile gets `ok: false` and *a restart is already under way*. `{ok, how, error, answering, db_down, starting, manual_command}`; clients then poll `GET /api/health` until `domovoi_reachable` is true. No privilege: the shipped web unit keeps `NoNewPrivileges`. See [LINUX_HOST.md](LINUX_HOST.md#the-loop-watchdog). |
| `POST /api/config/version/restart` | **Admin, security tier** | — | Proxy → core service restart (or update unit start). Gated at both hops. The connection drops moments after the response; clients treat that as success and poll `GET /api/config/version` until the server is back on a fresh start with `restart_required` clear: `started_at` differs from a read taken at the press (the old process answers for a moment after the response, and a restart with nothing waiting never set `restart_required`). In `update` mode they stop instead when `last_update` shows a finished run newer than the one before the press: a refused or rolled-back update never clears `restart_required` the way a successful one does. Either way the wait also outlasts `restart_in_progress`. An answer of `ok: false, in_progress: true` (or a read at the press that already shows `restart_in_progress`) means another restart got there first: clients poll until `restart_in_progress` clears and report that run's `last_update`, and so does a dashboard loaded while one is under way. |

### 3.9a The container guard

Reads straight from `container_incidents`; actions proxy to the core (§2.6a)
with the caller's credential forwarded and the same gate at both hops.

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/containers/summary` | Open | — | `{attention, tone: "err"\|"warn"\|null, text}` — for a browser with no household credential: whether something needs an admin's attention (`text` is "Something needs an admin's attention.") and the banner's colour. Never which container or what failed. |
| `GET /api/containers/incidents` | **Device read** | — | `{incidents, banner, frozen, attention}`. `incidents`: the open incidents and those resolved in the last 24 h, open first then newest, **at most 50 rows**: `{id, class: failure\|tamper, kind: missing\|stopped\|restarting\|unhealthy\|oneshot_failed\|daemon\|outdated\|image\|unknown\|config, severity: error\|warn, label, summary, owner, container, opened_at, last_seen_at, resolved_at, resolution: recovered\|no_longer_expected\|cleared\|null, acknowledged, acknowledged_at, cleared_at, open, freezes, can_acknowledge, can_clear}`; an admin caller (Bearer or cookie) also gets `expected`, `found`, `acknowledged_by`, `cleared_by`, `clear_note`. `banner`: `{show, tone: tamper\|error\|warn\|null, text, count, incident_ids, frozen}` — shown while a tamper incident is open or a failure is open and unacknowledged. `outdated` is a failure (amber): a compose or plugin container on another registry image of its own repository than its pin (recreate it). A failure back within 15 minutes of resolving reopens its row (same `id`, no new notification). `[]` / no banner when V024 is missing, and nothing at all while the database is down (on a compose install that includes Docker being down: Home's `db` row is what shows then). Live on the `containers` channel (§1.5). |
| `GET /api/containers/status` | **Admin read** | — | Proxy → core `GET /v1/admin/containers`. |
| `POST /api/containers/check` | **Admin (Bearer)** | — | Proxy → core check. |
| `POST /api/containers/mpd-image/rebuild` | **Admin, security tier** (`501` before setup) | — | Proxy → core music player image rebuild (waits up to 12 minutes). |
| `POST /api/containers/incidents/{id}/acknowledge` | **Admin (Bearer)** | — | Proxy → core acknowledge. |
| `POST /api/containers/incidents/{id}/clear` | **Admin, security tier** (`501` before setup) | `{"note"?}` | Proxy → core clear. |

`GET /api/plugins` rows carry `containers` (as the install preview, §2.3),
`containers_watched` and `containers_unwatched`.

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
| `GET /api/voices` | — | The voice registry (engine, model ref, default flag), and per row `sample`: what its play button plays (`"voice"` live, `"clip"` a shipped voice's built-in clip before it is downloaded, `null` nothing: no button) and `problem`: why a Piper voice can't speak here, in words (a voice trained on espeak phonemes other than English `"en"` / `"en-us"`; rooms set to it speak with the system voice), else `null`. |
| `GET /api/voices/{voice_id}/sample` | — | WAV sample — proxies the core's live TTS (`/v1/admin/voices/sample`); the web process has no TTS of its own. A shipped voice not downloaded yet answers its built-in clip (`audio/ogg`, Opus). `409` for a leftover Microsoft Edge row (the core removes them at boot), for a Piper voice that can't speak here (with the reason), and from the core when another voice would have answered. Every refusal from the core passes through with its status and its own `detail` (a `4xx`; anything else is a `502` whose detail names it): the page shows that sentence, never a bare "502 Bad Gateway". A catalogue voice registered but not downloaded yet (an install's kept Cori) is fetched on its first sample, waiting up to 120 s. |
| `POST /api/voices/piper` | multipart (`.onnx` + config) | Upload a local piper voice: trained on espeak's English phonemes, American (`"en-us"`) or British (`"en"`), as its config's `espeak.voice` says. `201` with the row (its `problem` says why another phoneme set can't speak here; it is kept). Both files are streamed to disk under a byte budget — the model at 200 MB, the config at 4 MB — and an upload over either one is answered `413` while it is still arriving, leaving nothing on disk. A request whose declared `Content-Length` is over 210 MB is refused before its body is read at all. |
| `PATCH /api/voices/{voice_id}` | `VoicePatch` | Rename / set default. |
| `DELETE /api/voices/{voice_id}` | — | Remove a voice. `204`. |

### 3.11a Domovoi's voice and the "say it like" list

Domovoi speaks with one of three public-domain Piper voices, fed by its own
text-to-phoneme converter (`domovoi/phonemizer`), and an owner can tell the
converter how to say a word. The phone runs the same converter and voices
and reads the server's choice and list here (`web/backend/api/voice.py`;
the data model in `domovoi/voice_settings.py`; the "say it like" format in
`domovoi/phonemizer/README.md`). Reads are **Device** (`X-Device-Token` or
an admin Bearer); changes are **Admin (Bearer)**. Every write answers with
the `GET /api/voice` body, then asks the core to reload the list
(`/v1/admin/pronunciations/reload`) or re-render the clips
(`/v1/admin/sounds/regenerate`), best-effort.

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/voice` | Device | — | `{"voices": [{"id", "name", "sex", "sample_rate", "size_bytes", "sha256"}], "server_voice": "<id>", "pronunciations": [{"word", "say_like"}], "updated_at": "<UTC, ISO 8601, Z>"}`. `voices`: the three (`en_US-norman-medium` Norman, `en_US-kristin-medium` Kristin, `en_US-ljspeech-medium` Linda), each `.onnx` with its size and SHA-256 at rhasspy/piper-voices revision `c10ece1a…`. `server_voice`: the voice registry's default when it is one of the three, else `TTS_PIPER_VOICE`. `pronunciations`: the effective list (the shipped entries with the owner's changes and removals applied), in order. `updated_at` moves whenever the list or the server's voice changes. |
| `GET /api/voice/samples` | Device | — | `{"samples": {"<id>": "voice" \| "clip" \| null}}`: what each of the three voices' play button plays: synthesized live (the voice is on this server), its built-in clip (not downloaded yet), or nothing (no button). |
| `GET /api/voice/sample/{voice_id}` | Device | — | A sample in one of the three voices: a sentence synthesized live, `audio/wav` (proxies `/v1/admin/voices/sample`), when the voice is on this server; else its built-in clip, `audio/ogg` (Opus, `domovoi/voice_clips/`, "Hi, I'm Norman. This is how Domovoi sounds."). `X-Sample-Source` says which. `404` for another id. |
| `PUT /api/voice/server` | Admin | `{"voice": "<id>"}` | Make one of the three the server's voice: the registry's default (rooms without a voice of their own speak in it); clips re-render. `400` for another id. |
| `POST /api/voice/pronunciations` | Admin | `{"word", "say_like"}` | Add a word, or change how a listed one is said (matched without regard to case or accents). `400` with the reason when the converter can't use it; `409` past 500 words of the owner's own (the shipped words don't count). |
| `DELETE /api/voice/pronunciations/{word}` | Admin | — | Take a word off the list: the owner's own word is deleted; a shipped one stays off until added again or the defaults are put back. `404` when it isn't listed. |
| `POST /api/voice/pronunciations/defaults` | Admin | — | Put the shipped entries back: the owner's changes to them are deleted, so the defaults of the release the box runs apply (later releases' too); the owner's own words stay. |
| `POST /api/voice/pronunciations/preview` | Device | `{"word", "say_like", "voice"?}` | Hear an entry before saving it: `audio/wav` from `/v1/admin/voice/say-like`, phonemes in `X-Phonemes`. `400` for an unusable entry or unknown voice; a refusal from the core passes through with its own `detail`, as for the samples. |

### 3.12 Wake words

Reads are **Open**, except a clip's audio — a recording of somebody in the
house saying the phrase — which is **Device read** (paired devices only; the
dashboard's `<audio>` carries the token as `?device_token=`), and the
training card's status and footprint, **Admin reads**. Every mutation
(`POST` / `PATCH` / `DELETE`, including
clip selection and deletion and the record / score / push proxies) is
**Admin (Bearer)** via `require_admin_mutation` — this surface decides what
the house listens for — except turning training on or off, which is the
**Admin, security tier**. Recording, scoring, pushing, stopping a training and
the training switch proxy to the core (which owns the satellite sessions, openWakeWord, the model files
and the trainer); training is picked up by the core's background trainer.
The default wake word is `hey_jarvis`; this surface is how you train a
custom one (e.g. "Hey Domovoi").

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/wake-words` | — | Registry: slug, status (`recording`/`training`/`ready`/`failed`), clip counts, threshold. |
| `POST /api/wake-words` | `WakeWordCreate` | Create a wake word (starts in `recording`). `201`. `phrase` must match `^[A-Za-z0-9 ,.'-]+$` (letters, digits, spaces and spoken punctuation, ≤ 120 chars) — it becomes an argument to the training command (`WAKE_WORD_TRAIN_COMMAND`), so `422` for anything else. |
| `POST /api/wake-words/{id}/record/start` | `{room_id}` | Proxy → core: satellite starts capturing positive clips. |
| `POST /api/wake-words/{id}/record/stop` | `{room_id}` | Proxy → core: stop capturing. |
| `POST /api/wake-words/{id}/train` | — | Queue training (the background trainer picks it up). Needs the word in `recording`, or `failed` (it trains again on the clips it has), with at least `wake_word_min_clips` (15) selected clips, else `409`. The route doesn't check that training is on: the dashboard greys **Train** until it is, and a word queued while it is off waits in `training`. |
| `GET /api/wake-words/trainer` | **Admin read** | Proxy → `GET /v1/admin/wake/trainer` (§2.7) with the caller's credentials: whether training is on, the setup job, and what training needs against this server. |
| `POST /api/wake-words/trainer/enable` | **Admin, security tier** · `{cpu_ok, disk_ok}` | Proxy → `POST /v1/admin/wake/trainer/enable` (§2.7): `200` with the status (the job runs in the background), `409` with the reason it can't start, `501` before setup. |
| `GET /api/wake-words/trainer/footprint` | **Admin read** | Proxy → `GET /v1/admin/wake/trainer/footprint` (§2.7): what **Turn off and free the space** removes, with Docker's sizes. |
| `POST /api/wake-words/trainer/disable` | **Admin, security tier** | Proxy → `POST /v1/admin/wake/trainer/disable` (§2.7): training off, the trainer image, its build cache and the cache volume removed. `409` while it is busy. |
| `POST /api/wake-words/{id}/train/stop` | — | Proxy → `POST /v1/admin/wake/train/stop` (§2.7): stop this word's training; it goes back to `recording` with its clips. |
| `GET /api/wake-words/{id}/clips` | — | Recorded clips with quality analysis + selection state. |
| `GET /api/wake-words/{id}/clips/{name}/audio` | **Device read** · `?variant=raw\|trimmed` | Listen to one clip. |
| `PATCH /api/wake-words/{id}/clips/{name}` | `{selected}` | Include/exclude one clip from training. |
| `POST /api/wake-words/{id}/clips/selection` | `ClipSelectionBody` | Bulk select/deselect. |
| `POST /api/wake-words/{id}/clips/reanalyze` | — | Re-run clip quality analysis. |
| `DELETE /api/wake-words/{id}/clips/{name}` | — | Delete a clip. `204`. |
| `POST /api/wake-words/{id}/score` | — | Proxy → core offline scoring (`501` when openWakeWord isn't installed). |
| `POST /api/wake-words/{id}/push` | `{room_id}` | Proxy → core: deploy the trained model to a satellite. |
| `PATCH /api/wake-words/{id}` | `WakeWordPatch` | Edit threshold / metadata. |
| `DELETE /api/wake-words/{id}` | — | Delete the wake word (+ artifacts: its model, its recorded clips, and, through `POST /v1/admin/wake/forget`, its training files in the trainer's cache volume). `204`, also when the core can't be reached (the worker's sweep removes those files after its next start). |

### 3.12a Command recordings (opt-in per room)

Rooms an admin opts in keep each wake-word or follow-up command that reached
the router — the capture as a 16 kHz mono WAV plus a JSON sidecar — for 14
days, to tune when Domovoi stops listening (early-endpointing design,
2026-09-28). Off for every room by default; the opt-in is a row in
`command_capture_rooms` (V016). What is and is never kept, where, and for how
long: SECURITY_PRIVACY.md §"Command recordings for tuning".

Every route is the **security tier**: reads take `require_admin_security_read`
and writes `require_admin_security`, both Bearer-only (since 2026-10-14 the
dashboard cookie alone is `403` on the reads too), and both answer **501
before first-run setup**. The household
token — in the header or as `?device_token=` — is `401` here, although it
reads every other kind of household speech. The dashboard plays audio by
fetching it with the admin's credentials, never through a URL token.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/captures` | `?room_id=` (optional) | `{rooms: [{room_id, enabled, enabled_at, count, bytes}], captures: [sidecar + bytes], count, bytes, cap_bytes, retention_days, labels, problem}`, newest first. `rooms` lists every opted-in room and any room that still has recordings. A sidecar is `{version, id, room_id, captured_at, trigger, duration_ms, sample_rate, end_reason, capture: {frames, voiced_frames, trailing_silent_frames, silence_limit_frames} \| null, listen: {silence_timeout, …}, transcript, matched_handler, matched_path, interrupted, timings, label, labeled_at}` — no person, session or embedding. `problem` is why nothing can be recorded (an unsafe `COMMAND_CAPTURES_DIR`), else null. Recordings past the retention are left out. |
| `GET /api/captures/clips/{room_id}/{capture_id}/audio` | — | The WAV, `Cache-Control: no-store`. `404` for an id that is not a capture id, a recording of another room, or one past the retention. |
| `PATCH /api/captures/clips/{room_id}/{capture_id}` | `{label: "cut_off" \| "fine" \| "waited_too_long" \| null}` | Record whether the capture ended at the right moment ("cut off too early" / "fine" / "waited too long"); null clears it. Returns the updated sidecar. `422` any other label, `404` unknown recording. |
| `DELETE /api/captures/clips/{room_id}/{capture_id}` | — | Delete one recording (audio and sidecar). `204`; `404` unknown. |
| `PUT /api/captures/rooms/{room_id}` | — | Opt the room in. Idempotent (keeps the first `enabled_at`). `404` a room that has neither connected nor been adopted; `409` when `COMMAND_CAPTURES_DIR` sits in or around a directory something serves; `503` before V016 is applied. Returns `{room_id, enabled: true, enabled_at, deleted: 0}`. |
| `DELETE /api/captures/rooms/{room_id}` | — | Opt the room out **and delete every recording it kept**, in the same request (row first, files second). Returns `{room_id, enabled: false, enabled_at: null, deleted}`. |

Retiring a room (`DELETE /api/satellites/{room_id}`, and the core route
behind it) also opts it out and deletes its recordings.

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
the core media dirs (`core:music` / `core:audiobooks` / `core:podcasts` /
`core:documents` / `core:pictures` / `core:videos`, from `MUSIC_DIR`,
`AUDIOBOOKS_DIR`, `PODCASTS_DIR`, `DOCUMENTS_DIR`, `PICTURES_DIR` and
`VIDEOS_DIR`), enabled-plugin `[[media_libraries]]` roots, and present
removable drives. It
is **additive** and does not touch `/api/documents/*` (§3.14), which the
Files page still calls for the Documents library's in-place editing.

The client only ever sends a `library_id` + a **relative** `path`; the absolute
`root_path` of each library is resolved and validated server-side and never
serialized. Containment rejects `..`, drive-absolute (`C:/…`), UNC (`//host/…`)
and symlink escapes; secret-shaped names under `~/.domovoi` are filtered from
every listing/serve/copy. Whatever sits inside `~/.domovoi` (bar its media
subdirectories) or inside `COMMAND_CAPTURES_DIR` (§3.12a) is refused by
location as well: `404` for a typed path, and left out of a listing, a
directory zip and an import copy, even from a library rooted above it.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/files/libraries` | — | The library registry: `{ "libraries": [ … ] }`, ordered core, plugin, removable. Each record carries `id, label, kind (core\|plugin\|removable), icon, kind_icon, owner, editable, importable, doc_editing, reindex_kind, present` — `root_path` is stripped. |
| `GET /api/files/browse` | `?library_id=&path=&device_id=&q=&kind=&sort=&limit=&cursor=&refresh=` | One directory level, folders first under every `sort` (`name` default · `newest` · `oldest` · `largest`), paged (§1.7). `q` is a case-insensitive substring of the name and `kind` keeps one entry kind (`folder` = folders only), both over the whole folder. Returns `{ library_id, path, editable, importable, doc_editing, breadcrumb:[…], entries:[…], writable, blocked_reason, total, folder_total, limit, has_more, next_cursor }` (`total` counts the matches, `folder_total` the folder; no paging params → every entry, as before); the listing is cached between pages and re-read when the folder changes (§1.7); each entry is `{ name, rel, is_dir, size, mtime, kind (folder\|audio\|doc-office\|doc-text\|image\|pdf\|other), locked_by }` (`locked_by` non-null only for `core:documents`). `device_id` is optional and only affects `writable` / `blocked_reason` — `editable` is the library's property, `writable` is the calling device's. `400` traversal · `404` missing dir / unknown library · `410` ejected removable. |
| `GET /api/files/download` | `?library_id=&path=` | Serve a file as an attachment (audio via Range/`206`) or a directory as a streamed zip (`{name}.zip`, 5000-member cap). A **directory** additionally needs an **admin session** (`401` without). `404` missing · `413` cap · `400` traversal. |
| `POST /api/files/upload` | multipart: `library_id`, `path`, `device_id`, `files[]`, optional `create_dir` · `X-Requested-With` | Upload into the browsed directory. `200 {saved, skipped, reindex_triggered, created_dir}`. With `create_dir=true` the destination `path` is made first — exactly ONE new level under a folder that exists (`404` when its parent doesn't); the new name is refused (`400`) when it holds a separator, is `.` / `..`, has a control character, a leading/trailing space, a trailing dot, any of `< > : " \| ? *`, is a Windows device name (`CON`, `NUL`, `COM1`, `LPT9`, … with any extension) or a secret-shaped name; it is containment-checked like any destination (a link may point inside the library, never out). A folder already there is fine (`created_dir: false`); one made for an upload that saved nothing is removed again. Same tier as the upload. How the app adds a per-chapter folder to `core:audiobooks` as one book: the folder and its files, then the usual reindex. `401` no device token, or a **removable** target without an admin session (Documents is device tier like any other core library) · `403` non-editable, device blocked, **or the preflight-forcing header missing** · `404` bad dest · `400` none saved · `422` no `device_id`. Each name is sanitized to a bare basename, deduped, and re-containment-checked before write. |
| `POST /api/files/upload-stream` | query: `library_id`, `rel_path`, `device_id`, optional `path` (the folder on screen), `on_conflict` (`keep_both` default · `replace` · `skip`), `reindex` (default `true`) · body: the file's raw bytes · `X-Requested-With` | One file of a folder upload (the dashboard's Upload → Folder… and a dropped folder). `rel_path` is the file's place inside what was picked (`Book Title/Disc 1/01.mp3`); every segment is refused (`400`) when it is empty, `.`/`..`, holds a backslash or a control character, starts or ends with a space or ends with a dot, is over 255 bytes, is a Windows device name, is secret-shaped, or (on a Windows server only) holds `< > : " \| ? *`; an absolute or drive path is refused; at most 32 levels. Missing folders are made inside the target one level at a time, each re-checked inside the library (a link may point inside, never out; a file where a folder should go is `409`). The body streams to a hidden part-file beside its destination and is renamed into place when complete, so memory stays flat whatever the size and a cancelled upload leaves nothing. `200 {saved, skipped, replaced, bytes, reindex_triggered}` (`saved` is the library-relative path, `""` when skipped). `413` over 8 GiB for one file · `401`/`403`/`404` as `/upload`. |
| `POST /api/files/upload-check` | JSON `{library_id, path, rel_paths[≤5000], device_id}` | Before a batch: `200 {existing: [rel], refused: {rel: why}}` — which paths are already there (so the page asks once: keep both, replace, skip) and which the server would refuse. Writes nothing; same gates as the upload. |
| `POST /api/files/upload-done` | JSON `{library_id, device_id}` | The end of a batch sent with `reindex=false`: the listing is re-read everywhere and an indexed library (Music, Audiobooks) reindexes once. `200 {reindex_triggered, reindex_kind}`; same gates as the upload. |
| `POST /api/files/delete` | **Admin (mutation)** · `{ library_id, paths:[…], recursive:false }` | Delete files; folders need `recursive:true` (bounded, symlink-confined). Refuses to delete a library root. `200 {deleted, failed, reindex_triggered}`. `401` no admin session · `403` non-editable. For `core:documents`, releases any editor lock on a deleted path. |
| `POST /api/files/move` | `{ source_library_id, paths:[…], target_library_id, target_path, device_id }` | Move files/folders into another folder — the drag-and-drop verb. Within one library or between two, as long as **both are editable** (a move deletes from the source, so a read-only root can only ever be a destination; removables stay copy-only via `/import`). Per-path outcome: `200 {moved, skipped, failed, reindex_triggered}` — `skipped` holds harmless no-ops (dropped into the folder it was already in) so they don't read as errors. Refuses a library root, a folder into itself or a descendant, a name that already exists at the destination (never overwrites), and secret-shaped names. `403` either side read-only **or device blocked** · `404` missing target dir · `422` no `device_id`. Reindexes **both** sides when either is an indexed library. |
| `POST /api/files/import` | `{ source_library_id, source_path, target_library_id, target_path, device_id }` | Copy a file/dir from a **removable** source into an **importable** library (server-side, member+byte capped). `200 {copied, skipped, reindex_triggered}`. `403` device blocked · `409` source not removable / target not importable · `410` ejected source · `404` missing · `422` no `device_id`. |
| `GET /api/files/device-blocks` | **Admin (read)** | Every files block: `[{id, device_id, device_name, note, created_at}]`. |
| `POST /api/files/device-blocks` | **Admin (mutation)** · `{device_id?, device_name?, note?}` | Block a device from uploading, moving or importing anywhere (it can still browse and download) — every door into a library: `/api/files`, the `/api/documents` saves and `POST /api/music/library/upload`. Matches id **or** name, like the queue blocks. Needs at least one of id/name (`400` otherwise); `409` when that device is already blocked. |
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
  `editable` flag, and the secret-shaped-name filter that skips `.env`
  and every `.env.*`, `*.key`, `*.pem`, `*.crt`, `*.p12`, `*.pfx`,
  `*.kdbx`, `*.gpg`, `*.ovpn`, SSH keys under their default names
  (`id_rsa*`, `id_dsa*`, `id_ecdsa*`, `id_ed25519*`), `.netrc`,
  `.git-credentials`, `.pgpass`, `credentials.json`, `secrets.json`,
  `pairing_token`, `setup-code.txt` and the folders `.ssh`, `.gnupg`,
  `.aws`, `.kube`, `tls` and `.domovoi`. A name one door refuses is refused by the other
  (`400`, or `skipped` on an upload). The filter covers **reads** too
  (WEB-14): such a file already in `~/Documents` is left out of
  `GET /api/documents`, out of `/download-zip`, and every read route
  (`/raw`, `/text`, `/sheet`, both `/export`s, `/drawings/read`) answers
  `404` for it — and for anything under a folder with such a name (`tls`,
  `.ssh`), or reached through a link that resolves onto one — exactly as
  `/api/files` does (its `/browse` and `/download` ask about every segment
  of the path too).
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
| `GET /api/documents` | **Device** · `?kind=all` | List documents with `category` routing (also `/api/documents/`). Secret-shaped names are not listed. |
| `POST /api/documents/create` | **Device** · `CreateRequest` (optional `device_id`) | Create a blank file (`doc` → .md, `sheet` → .xlsx, `drawing` → .excalidraw, `text` → verbatim name). `400` for a secret-shaped name (including one wearing a trailing dot or space), for a stream separator or a control character; `409` if it already exists. |
| `POST /api/documents/upload` | **Device** · multipart (optional `device_id` field, optional `folder`) · `X-Requested-With` | Upload documents. `403` without the preflight-forcing header. Secret-shaped names land in `skipped`, exactly as on `POST /api/files/upload`. `folder` may be `From Chat` (the one folder a client may name; made when missing): the app's "Send to home" for a phone chat's draft. `saved` then lists `From Chat/<name>`. |
| `POST /api/documents/delete` | **Admin (mutation)** · `DeleteRequest` | Delete documents. |
| `POST /api/documents/download-zip` | **Admin (mutation)** · `ZipRequest` | Zip + download a selection. |
| `GET /api/documents/text/{rel_path}` | **Device** | Read a text/markdown file (415 for binary/too-large). |
| `PUT /api/documents/text/{rel_path}` | **Device** · `TextWriteRequest` (optional `device_id`) | Write a text/markdown file. `415` — with the bytes untouched — for a target another editor owns, for a binary, or for one over the editor's read limit; `400` for a secret-shaped name, for a path the filesystem would store elsewhere (stream separator, trailing dot/space) or one it cannot use at all; `404` when the parent folder does not exist. |
| `GET /api/documents/sheet/{rel_path}` | **Device** | The sheet grid model (`rows[[{v,f}]]`); 415 for non-.xlsx/.csv. |
| `PUT /api/documents/sheet/{rel_path}` | **Device** · `SheetWriteRequest` (optional `device_id`) | Write the grid back (.xlsx keeps formulas as formulas). `415` for anything outside .xlsx/.csv, raised before anything is created. `413` before anything is read when the existing sheet is too large to save into (an .xlsx that unpacks to more than 16 MiB, a .csv over 8 MiB). Saves run one at a time, off the event loop. |
| `GET /api/documents/export/doc/{rel_path}` | **Device** · `?fmt=docx` | Export markdown/text as .docx (python-docx). |
| `GET /api/documents/export/sheet/{rel_path}` | **Device** · `?fmt=csv\|xlsx` | Export a sheet as .csv or .xlsx. |
| `GET /api/documents/raw/{rel_path}` | **Device** | Raw file bytes. Inline only for the allowlist a browser renders inertly — raster images, `audio/*`, `video/*`, PDF, plain text; everything else (HTML, SVG, XHTML, every `+xml` type such as `.rss` / `.xsl` / `.xaml`, Markdown, JSON, an unknown type) comes back as an attachment with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox` (`web/backend/api/inline_serve.py`). |
| `POST /api/documents/drawings/read` | **Device** · `DrawingReadRequest` | Read a drawing document. |
| `POST /api/documents/drawings/write` | **Device** · `DrawingWriteRequest` (optional `device_id`) | Save a drawing. `400` for anything outside .excalidraw/.svg. |

### 3.15 Podcasts and audiobooks

Reads are **Open**, except the directory search and an audiobook's resume
places (`/position`, `/positions`: **Device**); every write is **Device**
tier — subscribing, unsubscribing, polling, saving a resume position and
re-walking the audiobooks folder. Subscribing and polling also make the server
fetch a URL, so the feed URL must pass the outbound-URL rules (§1.1). So does
the directory search (`GET /api/podcasts/discover`: the server queries Apple
with the caller's term), which is why it is **Device** too (WEB-18); the
artwork a search result points at stays open, for the `<img>`. Feeds are polled by core background
workers; audio is served by this process.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/podcasts/subscriptions` | `?q=&sort=title\|newest&limit=&cursor=` | Subscribed feeds, paged (§1.7: `{items, total, …}`; the bare array when no paging param is sent). `q` matches title, author and feed URL. Each row's `artwork` is a path on this server (`/api/podcasts/subscriptions/{id}/artwork?v=…`) or `null`, never the publisher's URL: the server downloads each show's artwork itself, so clients load nothing from outside the house. |
| `POST /api/podcasts/subscriptions` | `SubscribeRequest` (`feed_url` or `query`, `keep_n` 1–50, default 5) | **Device.** Subscribe to a feed URL, or by `query` (a directory search). `keep_n` is how many of the newest episodes stay downloaded; outside 1–50 is `422` (each episode is a download of up to 512 MB). `400` for a URL the server won't fetch. Under `never` subscribing by `query` is `409`; a feed URL is stored and fetched once the internet is allowed. |
| `GET /api/podcasts/subscriptions/{sub_id}/artwork` | — | Open, like episode audio (an `<img>` can't send headers). The show's artwork as the server stored it (JPEG, PNG, WebP or GIF, at most 5 MB), `Cache-Control: max-age=86400`; `404` when it isn't downloaded. |
| `GET /api/podcasts/discover/artwork/{key}` | — | Open. Artwork for a Discover result, by a key the server minted for that result; a key it didn't mint is `404` without any fetch, so a client can never make the server fetch a URL of its choosing. |
| `DELETE /api/podcasts/subscriptions/{sub_id}` | — | Unsubscribe. |
| `GET /api/podcasts/subscriptions/{sub_id}/episodes` | `?q=&downloaded=&sort=newest\|oldest&limit=&cursor=` | Episodes for one subscription, paged (§1.7; the bare array when no paging param is sent), undated episodes last. `q` matches the title; `downloaded=true` keeps the ones with a file here. Each row includes `has_file` and `file_ext` (e.g. `".mp3"`, or `null` when not downloaded) instead of the private server path. |
| `GET /api/podcasts/discover` | `?q=` (required) | **Device.** Search a podcast directory (the server fetches Apple's search, so a caller without the household token or an admin Bearer is `401` before anything leaves the house). Hits whose feed URL would be refused are left out. A hit's `artwork` is a server path (`/api/podcasts/discover/artwork/{key}`) or `null`. `409` under `never`. |
| `POST /api/podcasts/poll` | — | **Device.** Poll feeds now (instead of waiting for the worker). `409` under `never`. |
| `GET /api/podcasts/episodes/{episode_id}/audio` | `?download=` | Stream a downloaded episode. `?download=1` serves it as an attachment named from the episode title plus its on-disk extension. |
| `GET /api/podcasts/positions/{episode_id}` | `?device_id=&person_id=` | Resume position. |
| `POST /api/podcasts/positions/{episode_id}` | `PositionSave` | Save position. |
| `GET /api/audiobooks` | `?q=&sort=title\|author\|newest&limit=&cursor=` | Indexed books (also `/api/audiobooks/`), paged (§1.7: `{items, total, …}`; the bare array when no paging param is sent). `q` matches title, author and narrator. Each row includes, instead of the private server path: `is_folder`; `file_ext` (single-file books; `null` for folder books, which download as a zip); `library_path`, the book's path inside the `core:audiobooks` library in POSIX form (`"Dune"` for a folder book, `"Dune.m4b"` for a file: what `POST /api/files/delete` takes, with `recursive: is_folder`); `size_bytes`, the whole book on disk (`null` until the indexer has measured it); `cover_url`, `/api/audiobooks/{id}/cover` when the indexer found a cover, else `null` (`artwork` carries the same value, for clients built before it); and `files`, what the book is made of in play order, `[{file, title, start_sec, duration_sec, size_bytes}]` — one entry per chapter file for a folder book (`file` is what `/audio?file=` takes), one entry for a single-file book. A book whose file or folder is gone is pruned at the next reindex (with its resume positions); an AUDIOBOOKS_DIR that walks empty prunes only what a Files delete just removed, so a share that isn't mounted doesn't empty the list. |
| `GET /api/audiobooks/{book_id}` | — | One book, with the fields above. |
| `POST /api/audiobooks/reindex` | — | Re-walk the audiobooks dir; when a book was added, ask the core for an MPD rescan (`POST /v1/admin/music/mpd-rescan`, the caller's credentials forwarded) so every room can play it. |
| `GET /api/audiobooks/{book_id}/audio` | `?file=&download=` | Stream a book file, with Range (`206`). A folder book plays file by file: `?file=` is one of its `files[].file` (a bare name, resolved inside the book's folder; `400` without it, `404` for a name that isn't an audio file). Single-file books ignore it. `?download=1` serves it as an attachment — a single chapter for folder books, the whole file for single-file books. |
| `GET /api/audiobooks/{book_id}/cover` | `If-None-Match` | Open (an `<img>` and a lock screen can't send a credential). The book's cover read from its own files on request: the picture built into it (a single-file book, or a folder book's first chapter file: MP4 `covr`, ID3 APIC, FLAC / Vorbis / Opus pictures), else an image beside it — in a folder book's folder `cover` / `folder` / `front` / `album` `.jpg` / `.jpeg` / `.png` / `.webp` (any case), beside a single-file book an image of the same name (`Dune.jpg`; never a `cover.jpg` loose in AUDIOBOOKS_DIR). No online lookup and nothing stored: a strong `ETag` (the files' paths, sizes and times), `Cache-Control: max-age=86400`, `304` while it matches, `404` (cacheable 5 min) when there is none. An image linked out of AUDIOBOOKS_DIR is not followed. |
| `GET /api/audiobooks/{book_id}/download` | — | Save a whole book to the device: single-file books come back as the file itself (attachment); folder books are zipped (one `<title>/<chapter>` entry per chapter) into a temp file removed after the response is sent. |
| `GET /api/audiobooks/{book_id}/position` | **Device** · `?device_id=&person_id=` | Resume position: the place in the BOOK (for a folder book, the file's `start_sec` plus the place in the file). Exactly that person's slot on that device; without `person_id`, the unidentified listener's slot only. Device tier since 2026-10-20 (it was open, and names any person). |
| `POST /api/audiobooks/{book_id}/position` | `PositionSave` | Save position, as above. |
| `GET /api/audiobooks/{book_id}/positions` | **Device** · `?device_id=` (required) `&person_id=` | This listener's places in the book on OTHER devices and rooms, newest first: `[{device_label, room, position_sec, updated_at}]` — what the app's "Continue from the kitchen (2 h 13 m)" offers. `room` is the room id for a place a room saved (`device_label` the same), `null` for a phone or a browser (`device_label` its name in the device list, else `phone` / `browser`; for the unidentified listener always `phone` / `browser`, never a device's name, since that place could be anybody's). Only rows for exactly this `person_id` (without one, the unidentified listener's), never another person's; the asking `device_id`'s own row, places at 0, and turns that named no room are left out. A room playing a book has its place saved every 30 s (`room_book_positions_interval_sec`), into the slot of whoever's voice command started or resumed it there (the unidentified listener's when nobody was recognised) and no one else's. |

### 3.16 Videos

Videos are discovered live from the same media-library registry the Files
tab uses — any video file (`.mp4` `.m4v` `.mov` `.webm` `.mkv`) inside any
core / plugin / removable library appears, keyed by `(library_id, rel_path)`.
The core library meant for them is `core:videos` (`VIDEOS_DIR`, default
`~/Videos`, created at first boot like the other core media dirs), but a
video in Music, Pictures or a plugin library shows up just the same.
Nothing is indexed into the DB except resume positions (`video_positions`,
per device × person, like the podcasts store). All **device tier**: the
file-content endpoints serve the same libraries the Files surface (§3.13)
lets a paired household client browse, so the reads take
`X-Device-Token` / an admin Bearer / the dashboard cookie / a
`?device_token=` query (a `<video src>` can't set a header), and the two
position writes take the header-only form of the same gate. Position saves
fire the `video_positions.changed` WS event; a change to the videos themselves
(an upload from a phone, a delete, a file another process wrote) fires
`files.changed`, and `GET /api/videos/list?refresh=true` (the pages' Rescan
and pull-to-refresh) re-walks the folders.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/videos/list` | `?q=&library_id=&sort=&limit=&cursor=&refresh=` | Videos across every present library (each walked whole — no count cap; hidden dirs skipped), paged (§1.7) as `{videos, total, limit, has_more, next_cursor, libraries, truncated, truncated_reason}`. `sort`: `library` (default: library, then path) · `name` · `newest` · `oldest` · `largest`. `q` is a case-insensitive substring of the name or path; `library_id` keeps one library. `libraries` is `[{library_id, library_label, total}]` — the matches per library with `q` applied — for section headers; `truncated` says the list is short of the disk: `truncated_reason` `"time"` (a walk ran past its 2-minute budget) or `"unpaged"` (no paging params: every match, as before, at most 2,000 per library — the bound clients from before paging were built against). The walk is cached between pages (§1.7). |
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
| `GET /api/images/raw` | `?library_id=&path=` | The original, inline (the Files tab's Open target) when it is a raster picture. An **SVG** is a document a browser executes, so it comes back as an attachment with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox` instead of rendering on the dashboard's origin — as does anything else off the inline allowlist (§3.14 raw). |

### 3.18 Chat

Claude-desktop-style threaded text chat answered by the local Ollama
(`clients/ollama.chat_stream`). Independent of the voice pipeline's chat
mode (Letta, per-room sessions); `chat_threads.letta_agent_id` is the
parked bridge for backing a thread with a stateful agent later. A message
carrying image uploads is answered by `ollama_vision_model` (the Vision
role slot on the Models page) instead of the Q&A model. When that model is
not installed, or none is set, such a message is answered at once with the
`vision_model_missing` refusal and Ollama is never asked; when Ollama can't
be asked what is installed, the send goes ahead. A text turn sends the
thread's earlier pictures to no one: they ride along only to the vision
model, because Ollama refuses a request that hands a text-only model an
image. Each picture goes to the model upright (EXIF orientation applied)
and at most 1280 px on its long edge, from a copy kept beside the upload
(`<token>-1280.jpg` or `.png`; a picture already that small and upright
goes as it is); the upload itself, which `GET /api/chat/uploads/{token}`
serves, is unchanged. Only those five kinds are decoded, at most 40
megapixels, three at a time and off the event loop; a bigger picture is
left out of what the model gets. A call that carries pictures also sends
`think: false` (a refusal of the flag is retried once without it) and
`ollama_vision_num_ctx` as its context window. Every call opens with one
system message (`ollama.chat_system_prompt`): the model answers as Domovoi
(the `bot_name` setting), this household's assistant, with the profile of
the thread's person (`chat_threads.person_id`) when the thread has one. A
call that may carry pictures adds how to read them (describe, read any text,
a picture of the Domovoi name or cat logo is a picture of itself) and keeps
the vision model's own Modelfile `SYSTEM` (from `/api/show`), and its
history is cut to fit the vision window, less the system message and room
for the reply: the message being answered always goes, older
messages lose their pictures first (about 1,200 tokens each) and then
drop out, oldest first. Every call also asks for at most
`ollama_chat_num_predict` tokens (2048; 0 = no cap; with a cap, that much of
the vision window is what is kept for the reply). A reply stopped there
(`done_reason` `length`) ends with `…` and a short italic note, in the
stream and in the kept row alike; it is not an error. Mutations fire the
`chat.changed` WS event.

Titles (V034): a thread's first message, typed or spoken, titles it with its
first five words ("…" when there were more). Once a reply has finished, the
chat model (`ollama_model`) writes a title of at most five words in the
background, once per thread; the reply never waits for it, and the next
message to the thread stops it (it runs again after that reply). It never
writes over a name a person set (`PATCH` with a `title`: the thread's
`title_by_user`), and doesn't run while the household's AI features are off;
a title the model can't give (an error, nothing usable) leaves the first
words. Threads from before V034 keep the titles they had. Title jobs run one
at a time, from one queue; any message, try again or app voice turn stops the
one running (it is written after that reply). A thread whose title was given
on purpose is never retitled: `title_generated` already true (whoever makes a
thread with a deliberate title sets it, as the help desk does for its "Help: …"
threads), or a title starting `Help:`. Titles lose control and format
characters (bidi overrides, zero-width marks), as do names given with `PATCH`.
Limit: a satellite's voice turn goes to the core directly, so it doesn't stop
a title job; at most one short title (16 tokens) can share the model with it.

Reads are **Device read** — the thread list (titles and snippets are message
text), a thread's transcript and the images sent into it are the household's
conversation, for paired devices only; an image's `<img src>` carries the
token as `?device_token=`. The model picker (`/models`) stays **Open**. Every
write — creating, renaming or deleting a thread, sending a message, staging an
upload — is **Device** tier.

**Drafts** (documents plan D4, `web/backend/api/chat_drafts.py`, V039). A
message asking for a resume, a cover letter or a letter ("write a cover
letter for the warehouse job at Acme") is a draft turn: the model writes
markdown in the kind's skeleton (`domovoi/drafts`), it streams as `delta`
events after a `status` event `{state: "drafting", action}`, and the `done`
row is the draft card: `content` is one line ("First draft of your cover
letter: 245 words.", "Version 2: cut 120 words (380 → 260)."), and `draft`
is `{id, version, kind, title, template, words, action, change, parts,
based_on, newer}` (`action` `create`, `revise` or `print`; `parts` when a
long version was revised section by section; `based_on` the version a
revision was written from, and `newer` the version that came in while it
was written, when one did: both are kept, and the reply line says so). In
a thread with a draft, a message is a revision only when it clearly means
the draft: it names it ("make the letter shorter"), edits "it"/"this"
("make it more formal", "rewrite it"), or, right after the reply that
wrote or revised it, is an edit-only instruction ("shorter"); anything
else is an ordinary message. `SendBody.revise_draft` (a draft id in this
thread, `404` otherwise; the card's Revise box) makes the message a
revision of that draft whatever it says. A revision makes the next version
from the latest one alone (the only text the model sees), and "print it" answers with the
print card (`action: print`) and no model asked. Every version is kept in
`chat_drafts` / `chat_draft_versions`; only a saved version becomes a file,
in Documents → From Chat. The drafts go with their thread. A draft row is
not `retryable`. A phone chat said into the chat mic ("print it", "print the
letter") prints the thread's latest version after the usual spoken
confirmation.

**Documents** (documents plan D1, `web/backend/api/chat_documents.py`, V036).
A PDF, Word (.docx), OpenDocument (.odt), Excel (.xlsx), CSV, text or
markdown file staged with `POST /api/chat/documents` is kept under
`~/.domovoi/chat_uploads/documents/<token><ext>`, read once in a short-lived
worker process (`domovoi/doc_extract`: a 120 s time limit, a memory and CPU
cap, zip-bomb and size checks, an XML parser that refuses DOCTYPEs and
entities, no network), and its text cached beside it as
`<token>-parts.json`: an ordered list of parts, each with the label an
answer cites (`p. 7`, `Section: Deposit`, `Section: Deposit (para 3)`,
`para 14`, `Sheet Budget, rows 2-41`, `Sheet Budget, column totals`,
`page header`, `footnotes`). A user row that attached documents carries
`documents: [{token, name, kind, pages, words, parts, scanned_pages,
garbled_pages, summary, notes, size}]`; the text itself is never in the
transcript. A thread with documents is answered from them (find, then
answer; `domovoi/doc_qa`), unless the message carries pictures: an
attachment with no words gets `I've read lease.pdf (30 pages). Ask me
anything about it.`; a question gets the best passages (BM25, at most 4
and about 1,200 tokens, the previous question's words added for a
follow-up) in a marked block of data after the rules, and the reply is
checked before it is kept — citations of a passage the app didn't give are
taken out, quotes must match their passage, and a reply saying the
passages don't answer becomes exactly `I couldn't find that in the
document.`; when no passage matches, that sentence is the reply and the
model is never asked. "Summarize" (and "overview", "key points", "tl;dr")
reads a document of up to 10,000 characters whole; a longer one is read in
parts by a **job** (below). The reply
row carries `doc: {mode: "question" | "summary" | "attached", found,
sources: [{token, name, label, page, text, match, cited}], notes: [{code,
text}]}` (`sources` are the passages given, best first; `cited` the ones
the checked reply names) and `stats.model_role` `documents`. A document turn streams no model text: an `event: status` `{state: "checking"}` comes first, then the checked reply as one `delta`. The passages sit between per-prompt random markers and document text can't write a label or a marker; the check is by label only (a fact cited to a passage that was given but doesn't hold it is a known limit). A document keeps at most 4,000,000 characters of text (a note says what was read), a sheet at most 256 columns and 200,000 cells, and a staged document no message took is removed after an hour. Deleting the
thread deletes its documents' files when no other thread refers to them.
While the household's AI features are off (the AI-optional switch, where
the server has it) the routes that feed a chat refuse with `409`; reading
text for a phone (`/documents/extract`) asks no model and stays open.

**Long documents** (`web/backend/api/chat_doc_jobs.py`, V038). A summary
of documents longer than one pass is a map/reduce **job**: the documents
are cut into parts of about 3,500 tokens (14,000 characters, never across
documents; a bigger part is sliced), each part gets "the main points of this
part, at most 8 bullets, each with its label" (its notes keep only labels of
passages in that part, and a line whose every label was made up goes), and
the notes are combined into the summary (in groups first when they don't
fit one pass), whose labels are checked against every part. A summary of a
document that fits one part is one call. The send's SSE stream then
carries `event: job` (the job's view), `event: progress` whenever the view
changes (and the time left at least every 2 s), and, when the job ends,
the checked reply as one `delta`, `error` if it failed, and `done` with the
persisted row. The job does not depend on the stream: a reader who leaves
loses nothing, and the job writes the reply itself. A job's **view** is
`{id, state, waiting_for_voice, part, parts, read, eta_s, estimate_s, line,
private, thread_id, question, covers, elapsed_s, result?, error?}`:
`state` `running`, `reducing` (writing the summary), `stopped`, `failed`,
`done`, or `interrupted` (a restart took its runner: `line` "this summary
was interrupted — carry on?", or `state` `stopped` with `line` "stopped (AI
is off)" while AI features are off; nothing restarts it but **resume**); `line` the progress in words (`reading part 3 of 8 · about 1
min left`, `waiting for a voice question to finish · part 4 of 8 next`,
`writing the summary · …`); `estimate_s` the whole job's time when it
started and `eta_s` the time left (null until something was measured);
`covers` what the parts read cover (`pages 1-9`, `the first 3 of 8
parts`); `result` `{content, doc, message?}` once it ended (a failed
private job carries `error` and no `result`). A part's notes keep only lines
citing a passage of that part; the summary keeps only lines citing a page
some part's notes cited (a line with no label, a page no note cited and a
made-up page all go); a part with nothing cited adds nothing and the note
`nothing_cited` names it; nothing cited anywhere gives a sentence saying
so. The reply's
`doc` adds `job: {id, state, parts, read, estimate_s, wall_s}` and the note
`read_in_parts` ("Read in 8 parts. Points that span parts may be
missed."); its `sources` are only the cited parts. Each part's notes are
saved as it finishes (`chat_doc_jobs.notes`); **stop** keeps them, and the
reply is "Summary of pages 1-9 (stopped)" over the notes so far (note
`stopped`; no model call), and **resume** ("carry on") reads only the rest
and writes the whole summary as a new reply. A job the web process lost (a
restart) reads as `interrupted` and carries on only when resumed, with the
model its message named. One live job per thread: another summary request
there follows it (the same stream); at most 2 jobs read at once in the house
(`MAX_RUNNING`, phones' private jobs included): a home-server chat gets a
reply saying so, a phone's job `429 busy`. Before every model call (each
part and each combine step) a job asks the core `GET /v1/voice/activity`
and waits while a voice turn is being answered (`waiting_for_voice`; a core
that can't be asked counts as not busy, and it waits at most 180 s), so a
voice turn waits at most for the call already running: seconds on a GPU,
about a minute per part on a CPU-only box; parts of different jobs take
turns. AI features turned off
stop a job at its next part (note `ai_off`, the notes kept) and refuse a new
one or a carry on with `409`. **Time estimates** come from measured speeds:
the QA model's last 50 replies' `prompt_tokens`/`prompt_ms` (prompts of 400
tokens or more preferred) and `output_tokens`/`generate_ms` in `stats`,
with `prompt_chars` where a job recorded it (characters per token); during
a job the time left comes from its own parts. A finished job's row is
deleted (the reply has it); a deleted thread stops its jobs.

**A phone's private job** ("Use home server" from a phone chat, owner
decision Q6): the phone sends the file and the request; the file is read in
the capped worker and deleted as soon as it has been read, the text lives
only in the web process's memory (the text goes when the job ends), nothing
is written to the database, and the job and its summary are forgotten when
the phone deletes it, 24 hours after it began, 10 minutes after a phone last
watched it, or at a restart; a sweep runs every 2 minutes, and a GET of an
expired job is `404`. A leftover upload folder, a read's temporary folder
and a half-written parts file are removed by the sweep and at boot.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/chat/threads` | `?archived=`, `?q=` | Thread list, newest first, with message counts, last snippet, `archived` and `title_by_user`. Without `q` it lists the threads that aren't archived (`archived=true`: the archived ones). `q` (at most 200 characters) searches: the threads whose title or any message contains it, case ignored and literally (`%` and `_` are characters), archived ones too unless `archived` says which, at most 100, each with `match_snippet` (the words around the first match in a message, or `null` when only the title matched). |
| `POST /api/chat/threads` | `ThreadCreate` | New thread. A title given here counts as the person's (`title_by_user`); without one, the first message titles it. |
| `PATCH /api/chat/threads/{id}` | `ThreadPatch` | Rename (`title`, which becomes the person's: the model's title job never writes over it; a blank one is `400`) and/or archive (`archived`). |
| `DELETE /api/chat/threads/{id}` | — | Delete thread + messages; unreferenced upload files (and the model's copies of them) are removed. |
| `GET /api/chat/threads/{id}/messages` | — | Full transcript, and `jobs`: the thread's long-document jobs (running, stopped, failed) as views. Each message: `id, role, content, images, model, error, created_at`, plus `stats` (replies) and `device_id` / `device_name` (the sender's install, name looked up at read time), and `source` (`typed`, or `voice` for both rows of a turn spoken into the app's chat mic). A voice user row also carries `speaker_person_id`, `speaker_name` (looked up at read time; null once the person is forgotten), `speaker_confidence` (the voice match score) and `speaker_confident` (the score clears the bar voice identification learns from; the app draws a softer marker below it). Every row carries `retryable`: whether "try again" may replace it (only ever true for an assistant row). |
| `POST /api/chat/threads/{id}/messages` | `SendBody` | Persist the user turn and stream the reply as **SSE** (`delta` events per chunk, one final `done` with the persisted row, `error` `{detail, code, model}` when there is no reply or it broke off — see the codes below). `SendBody.device_id` (optional) records the sending install; self-asserted, and kept only when it is a well-formed device id (anything else is stored as none). A message with pictures that asks what a letter says or what to do about it ("what does this letter say?", "how much do I owe?", "¿qué dice esta carta?"; documents plan D6) is a **letter turn**: `status` events `reading_letter` (`{photo, photos}`) and `checking_letter`; the vision model says per photo whether it is printed or handwritten (a handwritten one gets the `handwriting` heads-up card first, once a thread), transcribes each photo on its own, then the chat model explains it in words only (a sentence of its that names a figure is left out, note `letter_dropped`) and picks, by number, which of the letter's date and amount sentences matter; the reply quotes every one of those sentences verbatim, figure in bold, under its photo, and references only as the letter's own tokens; a line addressed to an AI assistant is left out of the quotes and named (`letter_injection`). The reply's `doc` is `{mode: "letter", sources: [{label: "photo 1", text: <the transcript>}], notes, letter: {dates: [{photo, sentence, figures, label}], amounts: [...], references: [[photo, label, number]], dropped, shaped, injection, kind, photos}}`; `stats.model_role: "letter"` (not retryable). |
| `POST /api/chat/threads/{id}/retry` | — | "Try again": a new reply to the thread's last question, streamed as **SSE** like a send, in place of the reply it got. The model doesn't see the reply being replaced. Once the new reply is complete, the old row is deleted and the new one written in one transaction, and the `done` event says so with `replaces` (the old row's id); if the old row is already gone (another try again replaced it), nothing is written and the stream ends with an `error` event, code `already_retried`. A retry that fails writes nothing: the stream ends with its `error` event and a `done` carrying the kept reply (`kept: true`), and it can be tried again. Only the chat model's reply to a typed message can be tried again (a message row's `retryable`; `stats.model_role` `chat`, `vision` or `override`, or none on a typed row from before V017). `409` with a `code`: `nothing_to_retry` (the thread doesn't end with a reply to a question), `not_retryable` (a spoken turn's reply, which may have set a timer, or another writer's, such as the help desk's `model_role: "help"`), `already_retried` (a try again on it is already streaming); `404` unknown thread. |
| `POST /api/chat/threads/{id}/lookups/{message_id}` | `{"decision": "once" \| "chat" \| "no", "device_id"?}` | Since W1. The person's answer to a permission card: an assistant row whose `stats.lookup_card` is `{kind: search\|page, query, destination, state: pending}` (a send writes one, with an `event: lookup_permission` before `done`, when a lookup would leave the house and neither the chat's `internet_allowed` nor the household's `LOOKUP_ALWAYS_ALLOW` lets it go). `once` and `chat` look it up through the core's `POST /v1/phone/lookup` and stream the answer like a send (`delta`, `replace` when the reply became the honest line, `done`, `error`); `chat` also sets the thread's `internet_allowed`; `no` answers without looking (the model is told it didn't). The answer's row carries `stats.lookup` `{kind, query, internet_used, sources: [{title, url, site, source_name, source_class, licence}], notice, covered, model_asked}`, and its text ends with the sources line written by the server (Wikipedia's with "CC BY-SA 4.0 · summarised"); the thread's `used_internet` is set when anything left the house. Under `never` the answer says so and nothing is sent. `404` no such card, `409` a card already answered, `409` ai-off. |
| `POST /api/chat/lookup` | `{"query"?, "url"?, "internet"?, "limit"?}` | Since W1. The app's phone chats at home: forwarded with the caller's credentials to the core's `POST /v1/phone/lookup` (§2.1), its answer and refusal (with `X-Domovoi-Refusal`) as given. Device tier; stores nothing. `502` / `504` when the core can't be reached or took too long. |
| `GET /api/chat/internet` | — | Since W1. The conversation header's chip: `{internet: "always"\|"sometimes"\|"never"\|"", always_allow, lookups}`. Device read. |
| `GET /api/chat/lookup-sources` | — | Since W1. Proxy of the core's `GET /v1/lookup/sources` (Settings → Chat & AI → Search). Device read. |
| `POST /api/chat/threads/{id}/heads-up` | `{message_id, option, heads_up?}` | The pick on a heads-up card (documents plan D3; Device, AI features on). A reply that is a card has `doc.mode: "heads_up"` and `doc.heads_up: {kind, text, options: [{id, label}], never_label, note, op, chosen}`, with no model asked (`stats.model_role: "heads_up"`, not retryable). The pick is kept on the card (`chosen`) and the question before it answered that way, streamed as **SSE** like a send: `try_anyway` / `go_ahead` / `send_anyway` as it would have been (with a note saying what it means), `calculate` the model only lists the numbers with their pages and the app reads each strictly and works it out (`doc.calc`, notes `calc_worked`, `calc_only_found`, `calc_repeated`), or refuses the whole calculation naming the number it can't read or find on its page (note `calc_unsure`); `choose_another` answers nothing (`{chosen, message_id}`). `409` with a `code`: `not_a_card`, `already_answered`, `stale` (a newer message followed the card), `bad_option`; `404` unknown thread or message. Cards come from the measured weak spots (`domovoi/doc_qa/chat-weak-spots.json`), never the model: a whole-document rewrite, an exact sum over the document, a long summary that will take a while here (its measured estimate), a document in an untested language, a blurry picture asked to be read. Each kind shows once per thread. A send's `heads_up: {off, skip: [kind]}` is the device's own switch and "don't warn me" kinds; silenced, an exact sum and scanned pages become a one-line note (`heads_up_calc`, `heads_up_scanned`). |
| `POST /api/chat/threads/{id}/voice` | multipart `audio`, `phone_id`, `player?`, `speak?` | A push-to-talk turn from the app's chat mic, answered into this thread: forwarded with the caller's credentials to the core's `POST /v1/phone/voice` (§2.1), whose answer or refusal comes back as it was given. `413` over 30 s of audio, `502` (`core_unreachable`) when the core isn't answering, `504` (`core_timeout`) after 180 s. |
| `POST /api/chat/uploads` | multipart `file` | Stage an image (20 MB cap) → `{token, name}`. It must really be a JPEG, PNG, GIF, WebP or BMP (`415` otherwise, whatever its name) of at most 40 megapixels (`413`), read from its header. A refusal is `{detail, code, ...}`: `detail` is a plain sentence to show, `code` a stable one to word it by — `415 unsupported_format` with `format` naming what the bytes are when they show a camera format (`"HEIC"`, `"HEIF"`, `"AVIF"`, `"JPEG XL"`, `"TIFF"`; read from the magic bytes, nothing decoded) or `null`; `413 too_many_pixels` with `max_megapixels`; `413 file_too_large` with `max_mb`. (A body past the transport cap gets the body guard's bare `413` with no `code`.) The Android app converts what the server can't read (HEIC and the like, or a photo over the limits) to a JPEG of at most 2560 px on its long edge before it uploads, and the dashboard words the refusal (a HEIC photo: send it from the app, or save it as a JPEG). |
| `GET /api/chat/uploads/{token}` | — | Serve a chat image inline. |
| `POST /api/chat/documents` | multipart `file` | Stage a document for a message (Device, AI features on; 100 MB) → `{token, name, kind, pages, words, parts, scanned_pages, garbled_pages, summary, notes, size}`. `kind` is `pdf`, `docx`, `odt`, `xlsx`, `csv`, `txt` or `md`; `pages` is a PDF's (else null); `summary` the one-line marker (`lease.pdf · 30 pages · about 9,800 words`); `notes` `[{code, text}]` with `code` `scanned_pages` (pages that are pictures of text, not searched), `garbled_pages` (text the file stores scrambled), `formulas_without_value`, `long_document`. A refusal is `{detail, code}`, `detail` the sentence to show: `415 old_format` (.doc, .rtf, .xls, .ods, .pages: what to save it as) or `415 unsupported_format`; `413 file_too_large` with `max_mb`; `422 unreadable` (damaged, a zip bomb, XML entities, encrypted, a password-protected PDF, a scan with no text, a file that isn't what its name says, a read that took too long); `409` with AI features off. Then send `documents: [{token}]` (at most 4) in `SendBody`; `content` may be empty when documents go with it. |
| `POST /api/chat/documents/extract` | multipart `file` | Read a document's text for a phone that can't read a PDF itself (Device; works with AI features off; nothing is kept: the file is read in a temporary folder that is removed before the answer) → `{kind, pages, parts: [{label, page, text, head}], scanned_pages, garbled_pages, words, chars, notes}`. Refusals as above. |
| `GET /api/chat/documents/{token}/parts` | — | Device read. `{meta, parts: [{label, page, text, head}]}`: the text a citation points at (the app's page sheet). `404` unknown token. |
| `GET /api/chat/documents/{token}/file` | — | Device read (`?device_token=` allowed). The original file: a PDF inline (`application/pdf`; the dashboard opens it at `#page=N`), any other kind as a download; `nosniff`, and a sandbox CSP on anything not shown inline. |
| `GET /api/chat/threads/{id}/documents` | — | Device read. `{documents: [...]}`: the thread's documents in the order attached. `404` unknown thread. |
| `POST /api/chat/threads/from-document` | `{rel_path}` | "Ask about this document" (Device, AI features on): a new thread `About <name>` about a file in the Documents library (`rel_path` relative to `documents_dir`, read through the Documents routes' containment and secret-name checks; `400` outside it, `404` missing or secret-shaped), copied into the chat's uploads and read, with the attachment and its reply already in it → `{thread, document}`. Refusals as for `/documents`. |
| `GET /api/chat/threads/{id}/drafts` | — | Device read. `{drafts: [{id, thread_id, kind, template, title, format, kind_label, version, words, first_lines, source, created_at, updated_at}]}`: the thread's drafts, each with its latest version. `404` unknown thread. |
| `GET /api/chat/drafts` | `?limit=` (≤ 200) | Device read. Every chat's drafts, newest first, each with its thread's `thread_title` (the Chat page lists them while AI features are off, so a draft can still be saved). |
| `GET /api/chat/drafts/{id}` | — | Device read. `{draft, versions: [{version, body, display, instruction, author, source, model, saved, words, changes, based_on, device_id, device_name, person_id, created_at}], templates: [{id, label}], formats}`, oldest version first. `body` is the markdown; `display` the plain text the changes view shows; `changes` `[[op, text], …]` against the version before (`=` kept, `+` added, `-` removed; `null` for v1); `source` `first`, `revision` (with its `instruction`) or `edited` (`author: person`); `device_id` / `device_name` the install that made it (a client says "edited by you" only when it is its own); `saved` `[{format, rel_path, check: {ok, line, read, expected}, template, at}]`. `draft.format` is the kind's default save format (`docx` for a resume or cover letter, `pdf` for a letter). `404` unknown draft. |
| `POST /api/chat/drafts/{id}/versions` | `{text, base_version, device_id?}` | A hand edit (characters XML can't hold are dropped), saved as the next version "edited by you" (Device; works with AI features off) → `{version: {…, change}}` (`change` the one line of what changed), or `{version, unchanged: true}` when the text is the latest version's. `409 {detail, code: "stale_version", latest}` when a newer version was written after `base_version` (two edits at once: one is saved, the other gets the `409`); `422` for a text with no words. |
| `POST /api/chat/drafts/{id}/save` | `{version?, format?, template?, device_id?}` | Save a version (default the latest) as `docx` or `pdf` (default the kind's) into Documents → **From Chat** (made when missing), through the Documents write policy (the per-device block, `403`) and the secret-name filter; a taken name gets ` (1)`. Then the ATS self-check reads the saved file back (a PDF's text layer, a .docx's paragraph XML) and compares it word for word with the draft → `{rel_path, name, format, version, check: {ok, line, read, expected}, library_id: "core:documents"}`; `line` is `text readable · single column` when it matches, else says where it differs. Device; works with AI features off. `template` another template of the same kind (`422` otherwise). |
| `GET /api/chat/drafts/{id}/export` | `?version=&format=&template=` | Device read (`?device_token=` allowed). The version as a file to download (`Content-Disposition: attachment`, `nosniff`); nothing is kept on the server. |
| `POST /api/chat/drafts/{id}/print/preview` | `{version?, template?, printer_id?, options}` | The print preview of a version's PDF, the same answer as `POST /api/print/preview` (Device). |
| `POST /api/chat/drafts/{id}/print/jobs` | `{version?, template?, printer_id?, options}` | Print a version's PDF on the home's printer, the same answer as `POST /api/print/jobs` (Device, `202`). The dashboard and the app call it from their Print dialog, after its preview; a chat's "print it" only offers that dialog. |
| `GET /api/chat/doc-jobs/speed` | `?chars=` | Device read. This server's measured speed for documents and, with `chars`, the time a summary of that many characters would take here → `{ai, model, speed: {prefill_tps, decode_tps, chars_per_token, samples} \| null, chunk_tokens, parts, estimate_s}` (`null` until measured). What a phone compares with its own time before it offers "Use home server"; `ai: false` when the household turned AI features off. |
| `POST /api/chat/doc-jobs` | multipart `file`, `question` | A phone's private summary job (Device, AI features on): **SSE** `job`, `progress`, `done` (the view, with `result: {content, doc}`), `error` first if it failed. Refusals as for `/documents`, and `429 busy` with 2 jobs reading in the house. |
| `GET /api/chat/doc-jobs/{id}` | — | Device read. A job's view (a private one's, with its `result`, until it is deleted). `404` unknown. |
| `GET /api/chat/doc-jobs/{id}/events` | — | Device read. The job's progress as SSE from now on (`job` first, then as above): coming back to a job. |
| `POST /api/chat/doc-jobs/{id}/stop` | — | Device. Stop: the part being read is abandoned, the notes kept; the reply is the summary of what was read. Answers the view. |
| `POST /api/chat/doc-jobs/{id}/resume` | — | Device, AI features on. Carry on a stopped, failed or interrupted thread job (`429 busy` with 2 reading): only the parts not read, then the whole summary as a new reply. `409 private` for a phone's job (its document is gone), `409 not_stopped`. |
| `DELETE /api/chat/doc-jobs/{id}` | — | Device. A phone took its private job's summary: the job and its text go. `404` for anything else. |
| `POST /api/chat/letters/read` | multipart `file` (at most **8 MB**), `stop_if_handwritten` (default `true`) | A phone chat's letter photo, read by the home server for a phone whose GPU can't run the photo model (documents plan D6; Device, AI features on, `409` ai-off otherwise) → `{blur, kind, text, stopped, seconds}`: `blur` the variance of the Laplacian of the grey picture at 384 px (low is blurry; the phone shows the blurry card itself), `kind` `"printed"`, `"handwritten"` or `null` (the vision model's one word), `text` the transcript. With `stop_if_handwritten` a handwritten photo answers `stopped: true` with no text, so the phone can show the handwriting card first and ask again with `false`. The caller is checked before the body is read; the body is read into memory, never spooled to disk, and **nothing is kept**: the photo is never written, and the explanation is written on the phone. One read per phone at a time (keyed on the phone's `X-Device-Id` header) and two in the house (`429 busy`, a plain `detail`); a voice turn in a room goes first. `413 file_too_large` (`{detail, code, max_mb: 8}`, also from the transport guard) / `too_many_pixels`, `415 unsupported_format`, `503 vision_model_missing` / `read_failed`. |
| `GET /api/chat/letters/ready` | — | Device read. Whether this server can read a phone's letter photo now: `{ai, vision, max_mb}` (`vision`: its picture model is installed; `null` when Ollama couldn't be asked; with AI features off, `ai` and `vision` are both false and Ollama isn't asked). The phone offers "a letter for the home server" only when `ai` and `vision` are both true. |
| `GET /api/chat/models` | Open | `{installed, default_model, vision_model, vision_installed}`: the installed Ollama models and the configured defaults. `vision_installed` is `false` when a picture would be refused (the vision model isn't installed, or none is set), `null` when Ollama couldn't be asked; the clients explain the first before a picture is attached. |

A reply's `stats` (V017) is an object whose keys are all optional:
`prompt_tokens`, `output_tokens`, `tokens_per_sec`; `total_ms`, `load_ms`,
`prompt_ms`, `generate_ms` (Ollama's own timings — `load_ms` well above zero
means the model had to be loaded first); `first_token_ms`, `wall_ms`
(measured by the server); `done_reason` (`stop`, or `length` when the reply
was cut off); `context_sent`, `context_in_thread`, `context_limit` (messages
sent to the model against those in the thread — fewer sent means older ones
fell out of what it saw); `num_ctx` (the context window the call asked
for: `ollama_vision_num_ctx` when it carried pictures); `model_role`
(`chat`, `vision` when images were attached, `override` when the request
named a model, `voice` for a spoken turn's reply); and
`error_code` when the reply has an `error`; `window_from_id` when the model
didn't see the whole thread: the id of the oldest message it was sent, where
the app draws its quiet "earlier messages are beyond what Domovoi can keep in
mind" divider. A spoken turn's reply keeps
`matched_handler`, `matched_path` and its stage times instead of Ollama's:
`audio_ms` (the recording), `stt_ms`, `stt_window_s`, `identify_ms`,
`route_ms`, `tts_ms`, `wall_ms`; its one error code is `turn_failed` (the
turn itself failed; the reason is in the core's log).

A failed reply never carries the exception's own text (it named internal
hosts, ports and library help URLs); that goes to the web process's log.
`error` is a plain sentence, and `code` / `stats.error_code` says which:

| Code | When |
|---|---|
| `vision_model_missing` | Pictures, and the vision model isn't installed or none is set (answered up front) — or Ollama said it is not found. |
| `model_missing` | The model for a text turn isn't installed (Ollama `404`). |
| `vision_unsupported` | The vision model can't take pictures (it is a text-only model). |
| `model_memory` | Not enough free memory to load the model. |
| `model_unreachable` | Ollama could not be reached, or the connection broke mid-reply. |
| `model_timeout` | Ollama took too long to take the request. |
| `model_error` | Anything else Ollama reported. |
| `empty_reply` | The model answered with nothing. |

The first four are fixed in Settings → Models; both clients word
`vision_model_missing` themselves and link an admin there. A row written
before the codes existed is read out the same way (the code worked out from
its text); the row itself is not changed.

**Help turns** (part 9, §3.18a). In a thread titled `Help: …` (the
dashboard's "ask Domovoi" and voice help start these), or for a message in
any thread that asks about Domovoi itself and whose answer Domovoi's help
holds, the model is given the help desk's instruction and the passages
found (the manual, the FAQ, the troubleshooting guide, the error codes'
entries, installed plugins' help files), the live facts and what the
system's own records show, instead of the chat persona. It answers only
from those. The reply ends with a `*From:*` line of the sections given,
written by the server (links the model made up lose their target), and the
row's `stats.help` is `{covered, sources: [{title, section, link, kind}],
model_asked}`. A help thread whose question the help doesn't cover is
answered "The manual doesn't cover that." and Ollama is not asked. A
picture in a help turn is first read by the vision model (which page, what
error), and that reading is searched too; the picture stays on the server.

### 3.18a Help (Domovoi's own help desk)

| Route | Tier | Body / query | Notes |
|---|---|---|---|
| `GET /api/help/search` | Open | `?q=&limit=5` (1-10) | Domovoi's help for a question, best first: `{query, covered, results: [{id, title, section, snippet, link, kind, confident}], not_covered}`. `kind` is `manual`, `faq`, `troubleshooting`, `error` or `plugin`; `link` is a dashboard route (`#manual/wake-word`) or the doc on GitHub. `covered: false` (with `not_covered: "The manual doesn't cover that."`) when nothing holds most of the question's words. No model: the "?" panel's search, and all of help while AI features are off. Open, like the manual it searches. |
| `GET /api/help/facts` | Device read | — | Live facts, exact: `{version, internet, internet_words, ai, rooms: [{name, status}], plugins: [{slug, name, version, enabled, status}]}`. |
| `GET /api/help/checks` | Device read | — | What the system's own records show right now: `{findings: [{key, tone, text, link, scope, details, room}]}` — Home's attention checks, each room's health sample and last outage report (offline, couldn't reach the server or the router, weak signal, heat, under-voltage, the mic privacy switch, a pending reboot, its self-check actions), plugins that failed or are degraded, the container guard, the last update. Redacted: no addresses, tokens, codes, network or host names. The update's details and the internet question are an admin's (`scope: "admin"`, present only for an admin caller). |
| `POST /api/help/report` | Device | `{what_happened?, include_room_names?}` | "Report a problem": `{text, filename}`, a Markdown summary for the person to read, then copy or save and post themselves. Version, host kind, AI and internet answers, plugins, rooms (as `room 1`, `room 2`… unless `include_room_names`), the checks' findings and the person's own words, all redacted. Built on request, returned, never stored or sent. |
| `GET /api/help/deliveries` | Device read | `?person_id=&since_id=&limit=20` | Help steps sent by voice (V035): `{deliveries: [{id, person_id, thread_id, title, created_at}]}`, newest first, or oldest first after `since_id`. The app's catch-up read beside the `help_deliveries` socket channel. `thread_id` is the `Help: …` chat holding the steps. |

Voice "how do I…" (the core's `help_desk` handler, band 106) answers with
the best page's first sentence or two and asks "Want me to send the steps
to your phone?". Yes writes the `Help: …` chat and a `help_deliveries` row
for the asker only: the recognised speaker, else the person named after
"Who should I send it to?". No phone marked as theirs: nothing is written.

### 3.19 Models (LLM management)

The reads of the catalog, the installed models, the hardware and the pull
jobs are **Open**. The role slots go through the core's config:
`GET /api/models/active` is an **Admin read** there and `POST
/api/models/active` a config write on the **security tier** (`501` before
setup), the core deciding both on the credentials this process forwards.
Pulling, cancelling a pull and deleting a model are **Admin (Bearer)** on
this process. Talks to the local Ollama instance; hardware facts proxy to the
core (which owns the CUDA context). Domovoi runs **two** models — the
conversational one and the tool-routing one — switchable independently.

| Method & path | Request | Purpose |
|---|---|---|
| `GET /api/models/catalog` | — | The curated model catalog with size/VRAM hints. Each `whisper` row also carries `cached` (true / false / null): whether that model is already in this host's Hugging Face cache, so the page can grey a size that can't load under `INTERNET_ACCESS=never`. |
| `GET /api/models/installed` | — | Models present in Ollama. |
| `GET /api/models/active` | — | Which models are active for each role. The `stt` row also carries `device` and `compute_type` (the Whisper pair). Every row says `installed` (in Ollama's list for an Ollama role, in the model cache for `stt`; null when that can't be told) and `model_source`: `config` (the live config), `startup` (a cookie-only read leaves the Whisper fields out of the config, so the `stt` row takes what the core loaded at boot from `/v1/admin/hardware`) or null (unknown). The `qa` and `vision` rows add `chat_model` and `chat_restart_required`: the dashboard's chat reads those models when the dashboard starts, so a model saved since then reaches the chat after a restart. |
| `POST /api/models/active` | `SetActiveBody` | Switch the active model for a role. For `stt`, an optional `compute_type` is written as `whisper_compute_type` in the same config write (checked against the device; `400` on any other role). |
| `GET /api/models/hardware` | — | Proxy → core `GET /v1/admin/hardware` (GPU/CPU/RAM/disk fit badges). |
| `DELETE /api/models/{name}` | — | **Admin (Bearer).** Remove an installed model. |
| `GET /api/models/jobs` | — | Running/finished pull jobs with progress. |
| `POST /api/models/pull` | `PullBody` | **Admin (Bearer).** Start downloading a model (background job). A reference that names its own registry host (`host/ns/model:tag`) is `400` when that host fails the outbound-URL rules. Under `never` it is `409` before any `model_jobs` row is written and before Ollama is asked, unless the reference names a registry on the house network. |
| `POST /api/models/pull/{job_id}/cancel` | — | **Admin (Bearer).** Cancel a pull. |
| `GET /api/models/ai` | — | The household's AI switch for Settings → Models: `{enabled, locked, ollama_reachable, internet_off, models: [{role, model, installed, download_gb, duplicate}], missing: [model], missing_gb}`. `models` are the `qa`, `tool` and `vision` role models; `installed` is null when Ollama can't be asked; `download_gb` is the size the repo records for the installers' models (null for another); `missing_gb` is what turning AI on would download (null when part of it is unknown). `locked`: `AI_FEATURES` is set in the server's environment, so the dashboard can't change it. |
| `POST /api/models/ai` | `{enabled: bool, install?: bool}` | **Admin (Bearer).** Turn AI chat and questions on or off: writes `ai_features` through `POST /v1/admin/config` (answers as that does, plus `pulls` and `pull_errors`). With `enabled` and `install`, then starts a pull (as `POST /api/models/pull`) for each role model Ollama doesn't have; one that can't start is named in `pull_errors` with why. While AI is off, `POST /api/chat/threads/{id}/messages` and `POST /api/chat/uploads` answer `409` with `X-Domovoi-Refusal: ai-off`; reading threads stays open. |

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
| `POST /api/news/topics/{topic_id}/feeds` | `NewsFeedCreate` | **Device.** Attach a feed. `400` for a URL the server won't fetch. `409` under `never`, before the feed is fetched or stored. |
| `DELETE /api/news/topics/{topic_id}/feeds/{feed_id}` | — | Detach a feed. |
| `POST /api/news/feeds/{feed_id}/validate` | — | **Device.** Fetch-test a feed. `409` under `never`; the feed's `valid` flag is left as it was. |
| `GET /api/news/people/{person_id}/items` | `?limit=50` | Fetched items for a person. An item's `url` is either an absolute `http(s)://` link or `null`: the core keeps only web links at ingest (any other scheme is dropped, the story itself is kept), and the dashboard and Android app re-check the scheme before rendering a hyperlink or opening the link. The same rule applies to a now-playing `source_url`. |
| `POST /api/news/items/{item_id}/favorite` | `NewsItemFavorite` | Star an item. |
| `GET /api/news/people/{person_id}/briefing` | — | The assembled spoken-style briefing. |
| `POST /api/news/poll` | `?person_id=` | Fetch now. `409` under `never`. |

### 3.21 Health and statistics

| Method & path | Auth | Response / purpose |
|---|---|---|
| `GET /api/health` | Open | `{status: "ok"\|"degraded", db_reachable, domovoi_reachable, stt, identity, core_alive}`. `core_alive` (since 2026-10-15): `true` when the core answers, or when `/v1/health` doesn't but its event loop answers `GET /v1/alive` (running but can't serve, most likely its database); `false` when neither answers (stopped or frozen); `null` from an older web. Returns `200` even when degraded so the UI can render a partial-degradation banner. `stt` passes the core's `/v1/health` speech-recognition state through (`ok` / `fallback` / `unavailable` / `stub` / `not_loaded`; `null` when the core did not answer with one); it never makes `status` degraded. `identity` passes the core's identity block through from the same ping (`algorithm`, `public_key`, `fingerprint` — public by construction, the string Settings → About shows), and `?challenge=<nonce>` (1–128 characters from `A-Z a-z 0-9 . _ ~ -`, the alphabet the core signs; anything else is `400` here, before the hop) is forwarded to the core so the block also carries `challenge` and the core's `signature` over `domovoi-health-v1\n<nonce>`. `null` when the core did not answer or predates identity. The Android app pins the key the first time it talks to a server and demands this proof again after every network change before it sends the household token (A6-03). |
| `GET /api/stats/latency` | **Device read** | Proxy → core `GET /v1/stats/latency` (§2.1), same `since` / `room` query and the same answer. `since` is parsed here, so a malformed one is this process's `422`; `502` when the core doesn't answer. The same tier at both hops; like every web→core hop it forwards the caller's headers. The Models page shows it as the "recent speech timings" line under speech-to-text. |

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
on the real dashboard, all four arrivals, in a real headless Chrome (with
the maintainers' functional test harness, kept outside this repo).

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

### 3.23 Printers, printing and scanning

`web/backend/api/printers.py` proxies the core's §2.9 routes under
`/api/printers`, `/api/print` and `/api/scan`, forwarding the caller's
credentials, with the same tiers checked here first. Every write needs
`X-Requested-With`. The differences:

| Method & path | Request | Response / purpose |
|---|---|---|
| `POST /api/print/preview` (**Device**) | `{library_id, path, printer_id?, options}` | The file is named the way the Files tab and Documents know it (`core:documents` and its path for a document). The web resolves it with the Files containment (`safe_join`) and the secret-name filter (`404` for `.env`, `.ssh/…`), refuses files over 100 MB (`413`), and sends the bytes to `POST /v1/print/preview`. |
| `POST /api/print/jobs` (**Device**) | as preview | The same, to `POST /v1/print/jobs`, labelled with the calling device's name (Settings → Phones & browsers) and `source` `app` or `dashboard`. `202` JobRow. |
| `POST /api/scan/jobs`, `/api/scan/jobs/{id}/next`, `/finish` (**Device**) | as §2.9 | A scan writes into Documents, so the Documents write policy applies first (the per-device block, `403`). `finish` drops the Documents listing cache, so the new PDF shows at once. Scan ids are 32 hex characters (`404` otherwise). |
| everything else | | One to one with §2.9: `GET /api/printers`, `POST /api/printers/discover`, `POST /api/printers`, `PATCH /api/printers/{id}`, `POST /api/printers/{id}/rename` (**Admin**), `DELETE /api/printers/{id}` (**Admin**), `POST /api/printers/{id}/trust-certificate` (**Admin**), `/refresh`, `/identify`, `/test-page`, `GET /api/print/jobs` and `/api/print/jobs/{id}`, `POST /api/print/jobs/{id}/cancel`, `DELETE /api/print/jobs` (**Admin**), `GET` and `PUT /api/print/limits`, `GET /api/scan/jobs/{id}`, `POST /api/scan/jobs/{id}/cancel`. |

---

## 4. Plugin-mounted routes

Plugins ship their own HTTP surfaces, mounted by each process at a
slug-namespaced prefix. See [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md)
for how to declare them.

### 4.1 Conventions

**Core process — `/v1/plugins/<slug>/...`** (`domovoi/plugin_http.py`):

* **Enable gate**: a disabled plugin's routes return `404` (a per-slug flag
  guards them). Each enable mounts the routers that load registered in
  place of the previous load's, so a re-enabled plugin answers from its
  live state and a plugin re-enabled with a different route set serves
  exactly that set; the route table does not grow.
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
  cover: a plain Starlette `Route` (`router.add_route`), `Mount` or `Host`
  on a plugin router would mount without the gate dependency, so it fails
  the load. Plugin websocket routes are not supported yet: the gate is
  HTTP-only, so a `@router.websocket` route (or a Starlette
  `WebSocketRoute`) fails the load in both processes with "plugin
  websocket routes are not supported yet". GETs are open unless the
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
| `POST /v1/plugins/radio/fcc-import` | Admin (default mutation gate) | Start the FCC FM bulk import as a background job (`?state=` optional); returns immediately. A long server-side job, like the core's library sweeps. `409` under `never`. |
| `GET /v1/plugins/radio/fcc-import` | Open | Import job progress. |
| `POST /v1/plugins/radio/stations/{station_id}/resolve-simulcast` | Device (`@device_endpoint`) | Find an online simulcast stream for an FM station. `409` under `never`. |
| `GET /v1/plugins/radio/state` | Open | Live tuner state: `{sdr_available, sdr_frequency_mhz, fcc_import}`. (Named `/state` because the core reserves `/status`.) |

Web router → mounted at `/api/plugins/radio` (slug-enable gate; GETs open
for the dashboard's daily-use radio pages; the everyday mutations are
device tier — a paired phone plays and favorites without the admin
password — and the FCC import keeps the admin default; nothing is open):

| Method & path | Auth | Request | Purpose |
|---|---|---|---|
| `GET /api/plugins/radio/search` | Open | `?q=&country_code=&tag=&language=&limit=30&offset=0` | Station-directory search; hits already saved locally carry their row id + favorited flag. `409` under `never`. |
| `GET /api/plugins/radio/stations` | Open | `?favorited_only=&source=online\|fm&q=&frequency_mhz=&limit=200&offset=0` | Saved stations. |
| `GET /api/plugins/radio/stations/{station_id}` | Open | — | One station. |
| `GET /api/plugins/radio/recent` | Open | — | The ten most recently played stations. |
| `POST /api/plugins/radio/play` | Device (`@device_endpoint`) | `{source, external_id?, station_id?, ...}` | Stamp a station as played (resolve-or-create, not a favorite) and return the row to stream. A new `stream_url` passes the outbound-URL check before it becomes a row. Under `never` an internet station (`source: "online"` with a stream outside the house network) is `409` before anything is written; FM plays. |
| `POST /api/plugins/radio/stations` | Device (`@device_endpoint`) | station body | Save (favorite) a station. `201`. Idempotent on `external_id`. `422` for a null `name` / `source` / `tags` / `sample_interval_sec` or a `frequency_mhz` outside 0-9999.9 (the column is `NUMERIC(5,1)`). |
| `PATCH /api/plugins/radio/stations/{station_id}` | Device (`@device_endpoint`) | `{name?, stream_url?, favorited?, sample_interval_sec?, tags?}` | Edit / favorite / unfavorite. A `stream_url` passes the outbound-URL check before it is stored. An explicit `null` clears `stream_url` or `tags`; for `name`, `favorited` or `sample_interval_sec` (NOT NULL columns) it is a `422` naming the field, and nothing is written. |
| `DELETE /api/plugins/radio/stations/{station_id}` | Device (`@device_endpoint`) | — | Forget a station (cascades to its detections). `204`. |
| `POST /api/plugins/radio/stations/{station_id}/resolve-simulcast` | Device (`@device_endpoint`) | — | Simulcast resolution from the dashboard; the caller's household token (or Bearer) is forwarded to the core's own device-tier gate. `409` under `never`, before the core is asked. |
| `POST /api/plugins/radio/fcc-import` | Admin (default mutation gate) | `?state=` | Start the FCC import (Bearer forwarded to the core). `409` under `never`. |
| `GET /api/plugins/radio/fcc-import` | Open | — | Poll the FCC import. |
| `GET /api/plugins/radio/detections` | Open | filters | Passive song-detection history. |
| `GET /api/plugins/radio/badge` | Open | — | `{favorites: N}` — the sidebar badge count. |
| `GET /api/plugins/radio/stations/{station_id}/stream` | Open | — | Proxy a station's audio through the web backend for the browser player (dodges CORS/mixed-content). FM/SDR stations return `409` — those play only through a satellite room. Under `never` an internet stream is `409` with the turned-off reason (§1.4) before any lookup. |

---

*Generated against the v1.0.0 tree; when this document and the code disagree,
the code wins — and please open an issue. See also
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) for what common error responses mean
in practice.*
