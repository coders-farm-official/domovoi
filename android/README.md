# Domovoi Android

Native Android client (`com.domovoi.app`) for the Domovoi dashboard —
feature parity with the web UI (`web/static/`) for everyday use,
talking to the same web backend on `:6369` (REST + `/ws/state`
WebSocket) and, through it, the Domovoi core on `:6370`. Server
administration is the deliberate exception — see *Settings* below.

## Stack

| Layer | Choice |
|---|---|
| Language / UI | Kotlin + Jetpack Compose (Material 3) |
| Min / target SDK | 26 / 35 |
| Networking | OkHttp + kotlinx.serialization (thin JSON client mirroring `web/static/data.js`) |
| Realtime | One OkHttp WebSocket to `/ws/state`, subscribe-all, exponential-backoff reconnect (1s → 15s) — `net/StateBus.kt` |
| Playback | Media3 ExoPlayer behind a `MediaSessionService` (background audio + media notification); one queue for library / radio / podcasts / audiobooks; casting to satellite rooms via the admin music endpoints |
| Images | Coil |
| Alerts | A notification when a timer or reminder goes off anywhere in the house: live over `/ws/state`, plus a local `AlarmManager` mirror for when the socket is down — `alerts/` |
| Settings | Preferences DataStore (server URL, theme, device id, "listening as" person, trusted servers and their pinned identities) — device-local only; server administration hands off to the dashboard. The household token is NOT in it: `data/TokenVault.kt` seals it under an Android Keystore key (see *Security*) |

## Building

Open `android/` in Android Studio (it reads `gradle/wrapper`), or from
the CLI with an Android SDK installed:

```bash
cd android
# point local.properties at your SDK if Android Studio hasn't already:
#   sdk.dir=/path/to/android-sdk
./gradlew :app:assembleDebug
# APK lands in app/build/outputs/apk/debug/
```

## Release-signed builds

The APK the CI workflow publishes (`.github/workflows/android-apk.yml`,
the `domovoi-debug-apk` artifact) is **debug-signed** with a key the
runner makes up for that run, and its SHA-256 is in the run summary. It is
a test build: each CI run carries a different key, Android won't install
one over another (uninstalling to upgrade loses the saved servers and the
household token), and nothing ties it to this project. For a phone that
keeps the app, build a release APK signed with a key you keep:

```bash
# Once: a release key, kept outside the checkout (.gitignore refuses
# *.jks and *.keystore in it anyway).
keytool -genkeypair -v -keystore ~/domovoi-release.jks -alias domovoi \
  -keyalg RSA -keysize 4096 -validity 10000

# Every release:
cd android
./gradlew :app:assembleRelease   # app/build/outputs/apk/release/app-release-unsigned.apk
BT=$ANDROID_HOME/build-tools/35.0.0
"$BT/zipalign" -p -f 4 app/build/outputs/apk/release/app-release-unsigned.apk /tmp/app-release-aligned.apk
"$BT/apksigner" sign --ks ~/domovoi-release.jks --ks-key-alias domovoi \
  --out app-release.apk /tmp/app-release-aligned.apk
"$BT/apksigner" verify --print-certs app-release.apk
sha256sum app-release.apk        # publish this next to the APK
```

Keep the key and its password safe and backed up: every later release has
to be signed with the same key, or phones refuse the upgrade. A phone with
a debug build installed uninstalls it once before the first release build
goes on.

## First run / offline-local mode

Without a configured server the app runs in **local mode**: just a Music
and a Videos tab, backed by the device's own media via MediaStore
(granular `READ_MEDIA_AUDIO` / `READ_MEDIA_VIDEO` permissions on API
33+). A `connect` chip opens the discovery screen — the web backend on
your LAN, e.g. `http://192.168.1.20:6369`, health-checked via
`/api/health` before accepting. Change it later under Settings →
Connection. Traffic is plain HTTP on your LAN (same trust model as the
web dashboard), and plain HTTP is accepted **only** towards the home
network: RFC 1918 addresses, loopback, link-local, the emulator host
`10.0.2.2`, and names under `.local` / `.home.arpa` / `.internal` /
`.lan` / `.home`. Any other address must be `https://`. The platform
half of that rule is `res/xml/network_security_config.xml` (referenced
from the manifest, system trust store only); because Android cannot
express an IP range there, `net/CleartextPolicy.kt` enforces the whole
rule on the app's single `OkHttpClient`, which the API, both WebSockets,
media3 and Coil share; the same rule is applied in Settings → Connection
and, by hand, to every save-to-device download (`DownloadManager` is its
own HTTP stack). Backups are off (`allowBackup="false"`,
`dataExtractionRules`), so the server address, device id, pinned server
identities and the sealed pairing token never leave the device in a cloud
or `adb` backup or a device-to-device transfer.

## Security: what the phone holds and where it sends it

The household token (`X-Device-Token`, docs/SECURITY_PRIVACY.md) is the
one credential the app has. The rules, all unit-tested under
`app/src/test/java/com/domovoi/app/net/`:

- **Scope** (`net/TokenScope.kt`, `DeviceAuthInterceptor`): the token goes
  only to the active server — same scheme, host and port, like a browser
  origin — and the interceptor strips it from anything else, whatever a
  caller put on the request. The one exception is the app's own WebSocket
  upgrades, which may reach another port on the same host (the drop-in
  socket to the core). A radio stream's host, a LAN sweep's 254 probes
  (`net/Discovery.kt` uses a copy of the client with no token interceptor),
  a URL another app pushes at the player: none of them see it.
- **Identity before token** (`net/IdentityGate.kt`, `net/ServerIdentity.kt`):
  the first time in a process — and again after every default-network
  change (`net/NetworkWatch.kt`) — the app asks the saved server
  `GET /api/health?challenge=<nonce>` without a token and checks the
  signed answer against the Ed25519 key it pinned for that server. The
  pin is made the first time a trusted server proves one (trust on first
  use; pair at home). Not the pinned key, or no identity where one is
  pinned, and no token-bearing request leaves — the state socket, the
  background timer sync and a ringing alarm's confirm included — and the
  shell says the server did not prove it is the one this phone paired
  with. A server that offers no identity at all (an older web backend, or
  one whose core is down) is treated as before, with a log line. The pin
  shows under Settings → Connection next to the server, with every other
  trusted server and a forget button each; switching servers forgets trust
  that is attached to no listed server. The Ed25519 verifier is a plain
  Kotlin port of the core's vendored one, pinned to the RFC 8032 vectors.
- **At rest** (`data/TokenVault.kt`): tokens are one AES-256-GCM blob in
  `shared_prefs/domovoi-vault.xml`, under a non-exportable key in the
  Android Keystore; a blob the key cannot open is "nothing paired" and the
  app asks to pair again. At every start a plain `device_tokens` value in
  the DataStore (an install from before the vault — or
  `functional-testing/at.py pair --via datastore`, which writes exactly
  that) is swept into the vault and the plain key removed, so the harness
  keeps working and nothing stays in the clear. **Debug-build caveat:** the
  CI workflow publishes `assembleDebug`, which is `android:debuggable`;
  on a debuggable build `adb shell run-as com.domovoi.app` and an attached
  debugger run as the app's uid and can drive it to decrypt. The Keystore
  protects the file, not a debugger; a release build (not debuggable,
  minified) is what closes that, and at.py's DataStore pairing depends on
  the debug build's `run-as`.
- **The media session** (`player/SessionAccess.kt`, `PlaybackService`):
  the session is exported, as every `MediaSessionService` must be, so any
  app on the phone can bind to it. Its callback admits only this app, the
  service's own notification controller, Android Auto / Automotive and
  controllers the system vouches for (lock screen, Bluetooth, a watch),
  grants none of them `COMMAND_SET_MEDIA_ITEM` / `COMMAND_CHANGE_MEDIA_ITEMS`,
  and fails `onAddMediaItems` / `onSetMediaItems` for everyone. The UI
  drives the ExoPlayer directly, so nothing legitimate needed either.

## Settings: device-local only

The app's Settings screen keeps just the tabs that belong to the phone —
**Connection** (server, theme, this device's name, "listening as") and
**About**. The dashboard's server-administration tabs (Greetings, Voices,
Wake Words, Models, Configuration) are *not* mirrored: they need the
dashboard's admin session, which this app does not have, so they could
never apply a change correctly. In their place a **Server settings** tab
says so in plain words and its **Open the dashboard** button launches the
browser at the connected server's Settings page (`<server>/#settings`);
with no server configured the button is disabled with a hint.

While connected, the save-to-device actions across Music / Videos /
Podcasts / Audiobooks download through the system `DownloadManager` into
`Downloads/Domovoi`, which MediaStore indexes — so anything saved shows
up in local mode automatically.

## Layout

```
app/src/main/java/com/domovoi/app/
├── AppContainer.kt        # singleton graph: prefs, api, bus, player, alerts
├── net/                   # ApiClient (data.js analog), StateBus (/ws/state), rememberApi hooks
├── alerts/                # timer/reminder notifications + the local alarm mirror (process-wide)
├── data/Prefs.kt          # DataStore-backed settings
├── player/                # PlayItem, PlayerController (player.jsx analog), PlaybackService
└── ui/
    ├── theme/             # domovoi design tokens (oklch → sRGB), light/dark
    ├── components/        # Pill, StatusDot, cards, dialogs, Domovoi glyphs, fmt helpers
    ├── shell/             # adaptive nav: bottom bar (phone) / rail (medium) / sidebar (tablet)
    └── screens/           # home, chat, music, podcasts, audiobooks, videos, images,
                           # news, people, satellites, calendar, stations, documents,
                           # local (offline MediaStore music+videos), settings, manual
```

The app opens on **Home**, as the dashboard opens on `#home`: the house
at a glance (rooms and what they are playing, every timer counting down,
problems worth knowing about, today's events, an announce box), built
from the same open reads as the web page. Home is also where the app
falls back when a screen's plugin goes away, and the top-left brand
leads back to it: the "domovoi" crumb in the topbar, the rail's glyph,
the drawer's brand row.

Responsive behavior: compact widths get the web phone strip as a bottom
bar (Home / Music / Satellites / Calendar / Chat); every other screen,
Settings and the manual included, is a tile on Home's "everything"
grid, and the home tab stays lit while one of them is open. List→detail
screens stack full-screen. Medium gets a navigation rail (Home first);
expanded gets the web's permanent sidebar with badge counts and
side-by-side list + detail panes, and Home spreads into the web
desktop's two columns once its pane is wide enough. The mini player
docks above the navigation on every size.

A **shared screen** (an admin marks the device in the dashboard's
Settings → Devices; the kitchen tablet) is learned from this install's
own device registration and remembered per server. While it is on, Home
shows calendar times as "busy", a reminder as its room, and problems as
one neutral line, and People, Chat, Files and News drop off every
launcher. Presentational, exactly as on the web: the tablet still holds
the household token (see docs/SECURITY_PRIVACY.md).

Conventions for adding screens: see `CONVENTIONS.md`.

## Timer and reminder alerts

When a timer or reminder goes off in any room, the phone posts a
notification on the **Timers and reminders** channel (`alerts/`,
process-wide — not a screen, no foreground service, no FCM; nothing
leaves the home network). What the phone does, exactly:

| App | New timer set anywhere | Timer goes off |
|---|---|---|
| open (on screen) | known at once (`timers.changed`), local alarm armed | notified at once (`timer_fires.changed`) |
| in the background or closed | known at the next background check, about every 15 min | its local alarm rings on time if the phone knew of it; otherwise notified at the next check if under 30 min old |

- **Live (app open):** `timer_fires.changed` on the `/ws/state` socket
  posts every new fire; each (re)connect catches up from
  `GET /api/timers/fires?since_id=…` (fires under 30 minutes old; a
  server's first catch-up only the last 2 minutes).
- **Alarm mirror:** every running timer and reminder on the active
  server gets a local alarm at its due time (on the server's clock), so
  the phone rings with the app closed or off the home network. When it
  rings it asks the server what happened (within 2 s): a recorded fire
  posts with where it was heard; a timer cancelled meanwhile stays
  quiet; no answer posts anyway, "couldn't reach Domovoi to confirm".
  Exact alarms: always on API 26–30, with `SCHEDULE_EXACT_ALARM` on
  31–32 (inexact without it), `USE_EXACT_ALARM` from 33. Re-armed after
  a reboot or an app update.
- **Background sync (app off screen):** a self-rescheduling alarm
  (`alerts/TimerSync.kt`) wakes the app about every 15 minutes for a few
  seconds to run the same catch-up (`GET /api/timers/fires?since_id=…`,
  the 30-minute rule) and mirror sync (`GET /api/timers`: arm new timers,
  disarm cancelled ones), with the household token. Each tick asks for
  the next one before it touches the network, gives up after 8 s (Doze
  lifts its network block for an exact allow-while-idle alarm's app for
  10 s), and skips the network when the start's own sync reached the
  server under a minute earlier. No server set: no ticks. Notifications
  off: a tick only disarms. Restarted by `TimerBootReceiver` after a
  reboot or an app update (first tick within ~2 min) and by every start
  of the app (the only way back after a force-stop).
  - **Why an exact alarm chain, not WorkManager or an inexact alarm:**
    only an exact allow-while-idle alarm gets the network in Doze. On the
    API 35 emulator in forced Doze the exact tick's receiver read
    `allowed=POWER_SAVE_ALLOWLIST|NOT_IN_BACKGROUND effective=NONE` in
    `dumpsys netpolicy` and synced; an inexact `setAndAllowWhileIdle` tick
    fired on time but read `allowed=NOT_IN_BACKGROUND effective=DOZE`, and
    its requests timed out. A WorkManager job (JobScheduler) waits for a
    Doze maintenance window (an hour after the phone settles, then two,
    four, six apart), and WorkManager would add a dependency, a database
    and a start-up initializer; the app already re-arms after a reboot.
    The exact-alarm permissions are the ones the timers already hold.
  - **How it is armed, per API level**, so it never holds back a timer's
    own alarm: 31+ with exact alarms allowed: `setExactAndAllowWhileIdle`
    every 15 min; the timers' exact alarms share Doze's budget of 72 an
    hour with it (`allow_while_idle_quota`). 26–30: every allow-while-idle
    alarm of an app shares one slot per ~9 minutes while dozing or in
    Battery Saver, so the exact tick (no permission needed below 31) is
    kept out of the 9 minutes either side of every mirrored timer alarm.
    31–32 with "Alarms & reminders" revoked: no exact alarms at all, so a
    plain inexact alarm (10 min, delivered up to 7.5 min late) that runs
    while the phone is awake and waits for Doze's maintenance windows.
  - **What stops it:** a user-set **Restricted** battery usage stops the
    ticks and the timer alarms alike while the app is in the background;
    a force-stop stops both until the next start (Android 15 also sends
    the app `BOOT_COMPLETED` as it leaves the stopped state, seen on the
    emulator). Not measured: how far app standby buckets space the ticks
    for an app Android files as seldom used (`am set-standby-bucket` would
    not take the app below "working set" on the emulator, where the
    tick's `app_standby` policy did not defer it).
- The two paths post one timer once (a small dedupe book keyed by
  server and timer id), into the same notification; a background check
  that finds a fire already posted by either posts nothing new.
- **Privacy:** a shared screen never shows the words, locked or not. On
  any other phone the lock screen shows only the kind and the room
  ("Reminder · garage") **when the phone is set to hide sensitive
  notification content** (Settings > Notifications > notifications on
  lock screen, or "Sensitive notifications" off on a Pixel); Android's
  default shows the whole notification there, a reminder's words
  included, and an app cannot force otherwise. The mirror keeps a
  reminder's words only in app-private storage, excluded from cloud
  backup and from device-to-device transfer, and its alarms carry ids
  only.
- With notifications off for the app, nothing posts and nothing is
  armed; Home's timers card says "Timer alerts are off on this phone"
  with a way to turn them on.
- Tapping an alert opens Home. The satellite detail's **Only reminders
  for this device** switch sets which rooms speak other rooms' timers.
- A phone that force-stops the app (Settings > Force stop, some
  makers' swipe-away) loses every alarm it had set, the background
  sync's included; the next start re-arms the ones still ahead and
  restarts the sync.
- **Limit (no foreground service, by design):** Android freezes a
  backgrounded app and, from Android 15, blocks its network a few
  seconds after it leaves the screen (`blocked=APP_BACKGROUND` in
  `dumpsys netpolicy`). So the live path only runs while the app is
  open. In the background the phone knows what its last check (at most
  about 15 minutes ago, unless something above stopped the checks) found: a timer
  set by voice and due before the next check reaches the phone only as a
  late notification at that check. A broadcast receiver is exempt from
  that block while it runs, which is what the ticks and the ringing
  alarms' confirm step rely on. Checked 2026-09-30 on the API 35
  emulator against a fake server: with the app in the background
  (`blocked=APP_BACKGROUND … effective=APP_BACKGROUND`) the tick's
  requests went through (`allowed=NOT_IN_BACKGROUND effective=NONE`
  while it ran), posted the fire it had missed and armed the new timer;
  in forced Doze the exact tick did the same (see above).

## Capability gating (plugins)

The app fetches `GET /api/capabilities` from the web backend at connect
(and again whenever the state WebSocket reconnects). Screens backed by a
plugin are compiled in but *hidden* unless the manifest lists their
capability — e.g. the Stations screen renders only when an installed
plugin declares `"stations"` (the bundled radio plugin). Sidebar badges
for gated routes are skipped when the capability is absent, and history
pill tones come from the manifest's `handler_display` entries (unknown
names render neutral). If the endpoint is missing or unreachable, all
gated screens stay hidden. Provider-specific search/download UIs are
web-dashboard plugin pages only; this app stays provider-agnostic.

## Known gaps vs the web app

- Documents editing is simpler than the web's: markdown gets the native
  text editor with a formatting toolbar (no preview pane), spreadsheets
  get a plain value grid (formulas save as `=...` strings but aren't
  evaluated on-device), legacy office files download only, and drawings
  are view/download only (no Excalidraw canvas).
- No 10-band EQ / spectrum visualizer in the player (ExoPlayer has no
  Web-Audio-style filter graph without a custom audio processor).
- No offline pin/auto-cache of tracks yet (the web PWA's Cache Storage
  feature) — streaming only.
- No server administration (greetings, voices, wake words, models,
  configuration) — by design; Settings → Server settings opens the
  dashboard instead.
- Home shows the problem rows a household member may see and never the
  admin-only ones (satellites waiting for approval or adoption, updates,
  disk) or the "this box isn't claimed yet" row: the app has no admin
  session to read them with. A plugin problem opens the dashboard's
  plugin list in the browser. The "everything" grid lists the app's own
  screens; plugin web pages stay on the dashboard.
