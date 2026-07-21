# Domovoi Android

Native Android client (`com.domovoi.app`) for the Domovoi dashboard —
feature parity with the web UI (`web/static/`), talking to the same
web backend on `:6369` (REST + `/ws/state` WebSocket) and, through it,
the Domovoi core on `:6370`.

## Stack

| Layer | Choice |
|---|---|
| Language / UI | Kotlin + Jetpack Compose (Material 3) |
| Min / target SDK | 26 / 35 |
| Networking | OkHttp + kotlinx.serialization (thin JSON client mirroring `web/static/data.js`) |
| Realtime | One OkHttp WebSocket to `/ws/state`, subscribe-all, exponential-backoff reconnect (1s → 15s) — `net/StateBus.kt` |
| Playback | Media3 ExoPlayer behind a `MediaSessionService` (background audio + media notification); one queue for library / radio / podcasts / audiobooks; casting to satellite rooms via the admin music endpoints |
| Images | Coil |
| Settings | Preferences DataStore (server URL, theme, device id, "listening as" person) |

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

## First run

The app asks for the dashboard URL — the web backend on your LAN,
e.g. `http://192.168.1.20:6369`. It health-checks `/api/health` before
accepting. Change it later under Settings → Connection. Traffic is
plain HTTP on your LAN (same trust model as the web dashboard);
cleartext is enabled in the manifest for that reason.

## Layout

```
app/src/main/java/com/domovoi/app/
├── AppContainer.kt        # singleton graph: prefs, api, bus, player
├── net/                   # ApiClient (data.js analog), StateBus (/ws/state), rememberApi hooks
├── data/Prefs.kt          # DataStore-backed settings
├── player/                # PlayItem, PlayerController (player.jsx analog), PlaybackService
└── ui/
    ├── theme/             # domovoi design tokens (oklch → sRGB), light/dark
    ├── components/        # Pill, StatusDot, cards, dialogs, Domovoi glyphs, fmt helpers
    ├── shell/             # adaptive nav: bottom bar (phone) / rail (medium) / sidebar (tablet)
    └── screens/           # music, podcasts, audiobooks, news, people, satellites,
                           # calendar, stations, documents, settings, manual
```

Responsive behavior: compact widths get a bottom bar (Music /
Satellites / Calendar / People / More) with list→detail screens
stacked full-screen; medium gets a navigation rail; expanded gets the
web's permanent sidebar with badge counts and side-by-side
list + detail panes. The mini player docks above the navigation on
every size.

Conventions for adding screens: see `CONVENTIONS.md`.

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

- No embedded OnlyOffice/Collabora editors on Documents — office files
  can be created/uploaded/downloaded/deleted; editing hands off to the
  web dashboard. Text files get a native editor; drawings are view/
  download only (no Excalidraw canvas).
- No 10-band EQ / spectrum visualizer in the player (ExoPlayer has no
  Web-Audio-style filter graph without a custom audio processor).
- No offline pin/auto-cache of tracks yet (the web PWA's Cache Storage
  feature) — streaming only.
- Wake-word clip playback uses simple in-place audio playback; the RMS
  envelope sparkline is simplified.
