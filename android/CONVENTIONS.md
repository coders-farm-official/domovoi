# Domovoi Android — code conventions

The app mirrors the web dashboard (`web/static/*.jsx`) feature-for-feature.
Read these core files before writing a screen:

- `app/src/main/java/com/domovoi/app/AppContainer.kt` — `LocalApp` (api, bus, prefs, player), `LocalToast`
- `app/src/main/java/com/domovoi/app/net/ApiClient.kt` — `api.get/post/patch/put/delete(path, body)` return `JsonElement`; `api.upload(path, MultipartBody)`; `api.bytes(path)`; `api.absolute(path)` for media URLs; `DomovoiJson`
- `app/src/main/java/com/domovoi/app/net/ApiHooks.kt` — `rememberApi(keys..., eventTypes) { app -> ... }` (the useApiList/useApiObject analog), `OnStateEvents`
- `app/src/main/java/com/domovoi/app/net/StateBus.kt` — WS events (`WsEvent(type, payload)`)
- `app/src/main/java/com/domovoi/app/ui/components/*.kt` — `DomovoiCard`, `Pill`, `Tone`, `StatusDot`, `RoomChip`, `AvatarBubble`, `PageHeader`, `SectionLabel`, `Stat`, `EmptyState`, `LoadingState`, `ErrorState`, `ConfirmDialog`, `PromptDialog`, `relTime`, `fmtDur`, `fmtBigDur`, `fmtBytes`, `fmtRemaining`, `isLive`, `DomovoiGlyph`, `SleepingDomovoi`
- `app/src/main/java/com/domovoi/app/ui/theme/Theme.kt` — `Domovoi.colors.*` (brand/fg/fgMuted/border/card/ok/warn/err/idle + soft variants)
- `app/src/main/java/com/domovoi/app/player/PlayItem.kt` + `PlayerController.kt` — local playback + casting

## Rules

1. **Screens live in `ui/screens/<domain>/`** and only write files there. The
   entry composable is `<Domain>Screen()` — keep the existing signature from
   the stub. Split big screens into multiple files in the same package.
2. **Models**: define `@Serializable` data classes for your domain inside your
   package (e.g. `ui/screens/music/MusicModels.kt`). Decode with
   `DomovoiJson.decodeFromJsonElement(serializer(), element)` or the
   `JsonElement.decode<T>()` helper. Always tolerate missing fields
   (nullable + defaults) — the backend evolves.
3. **Fetching**: use `rememberApi` with the same WS `eventTypes` the web page
   subscribes to. Loading → `LoadingState()`, error → `ErrorState(msg,
   refresh)`, empty list → `EmptyState(...)` with web-equivalent copy.
4. **Mutations**: `val toast = LocalToast.current; val scope =
   rememberCoroutineScope()` then `scope.launch { runCatching {
   app.api.post(...) }.onSuccess { toast("..."); refresh() }.onFailure {
   toast("failed: ${it.message}") } }`. Every mutation toasts, like the web.
5. **Destructive actions** get a `ConfirmDialog` (web `window.confirm`).
6. **Design rules** (from the domovoi-design skill): one amber accent
   (`Domovoi.colors.brand`) — never introduce a second accent. No emoji. Live
   things pulse (`StatusDot(tone, live=true)` / `Pill(live=true)`).
   Lowercase chrome labels ("now playing", "online"); sentence case content.
7. **Responsive**: screens must work from a phone in portrait to a tablet.
   Use `LazyColumn`-first layouts; where the web shows a two-pane layout
   (list + detail), collapse to navigation-on-tap on narrow widths — check
   `androidx.compose.material3.adaptive.currentWindowAdaptiveInfo()
   .windowSizeClass.windowWidthSizeClass == WindowWidthSizeClass.COMPACT`
   (from `androidx.window.core.layout`). Detail panes on compact width are
   full-screen overlays/sheets with a back affordance (`BackHandler`).
8. **JSON building**: `buildJsonObject { put("room_id", room) }` from
   `kotlinx.serialization.json`.
9. **Timestamps** arrive as ISO strings — use `relTime`/`parseInstant`.
10. Keep helpers `private` to your file/package to avoid cross-package clashes.
11. Compose Material3 + material-icons-extended are available. Coil
    (`coil.compose.AsyncImage`) for artwork via `api.absolute(path)`.
12. **Plugin-backed screens are capability-gated.** If a screen's endpoints
    come from a plugin router (`/api/plugins/<slug>/...`), declare its
    capability in `Route.requiredCapability()` (`ui/shell/Routes.kt`) and it
    will only render when `GET /api/capabilities` lists that capability
    (`net/Capabilities.kt`, provided via `LocalCapabilities`). Never hardcode
    provider names, provider tabs, or provider tone maps — labels/tones come
    from the manifest's `handler_display`; unknown values render neutral.
