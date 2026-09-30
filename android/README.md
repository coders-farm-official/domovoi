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
| Settings | Preferences DataStore (server URL, theme, device id, "listening as" person) — device-local only; server administration hands off to the dashboard |

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
media3 and Coil share. Backups are off (`allowBackup="false"`), so the
server address, device id and — once it lands — the pairing token never
leave the device in a cloud or `adb` backup.

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
leaves the home network):

- **Live:** `timer_fires.changed` on the `/ws/state` socket posts every
  new fire; each (re)connect catches up from
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
- The two paths post one timer once (a small dedupe book keyed by
  server and timer id), into the same notification.
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
  makers' swipe-away) loses every alarm it had set; the next start re-arms
  the ones still ahead before it syncs.
- **Limit (no foreground service, by design):** Android freezes a
  backgrounded app and, from Android 15, blocks its network a few
  seconds after it leaves the screen (`blocked=APP_BACKGROUND` in
  `dumpsys netpolicy`). So the live path only runs while the app is
  open, and the alarm mirror only knows the timers that existed the last
  time the app was open or resumed. A timer set by voice while the app
  sits in the background reaches the phone only when the app is next
  opened (the catch-up posts it if it fired under 30 minutes ago). A
  ringing alarm is allowed the network for its confirm step.

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
