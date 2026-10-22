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
| Min / target SDK | 26 / 36 (Android 8.0 to 16; compiled against 36 with AGP 8.13.2; what each version gets: [docs/ANDROID_COMPATIBILITY.md](../docs/ANDROID_COMPATIBILITY.md)) |
| ABIs | arm64-v8a, armeabi-v7a, x86_64, x86 (the on-device assistant on the 64-bit ones only; see *ABIs and APKs*) |
| Networking | OkHttp + kotlinx.serialization (thin JSON client mirroring `web/static/data.js`) |
| Realtime | One OkHttp WebSocket to `/ws/state`, subscribe-all, exponential-backoff reconnect (1s → 15s) — `net/StateBus.kt` |
| Playback | Media3 ExoPlayer behind a `MediaSessionService` (background audio + media notification); one queue for library / radio / podcasts / audiobooks; casting to satellite rooms via the admin music endpoints |
| Images | Coil |
| Alerts | A notification when a timer or reminder goes off anywhere in the house: live over `/ws/state`, plus a local `AlarmManager` mirror for when the socket is down — `alerts/` |
| On-device assistant | LiteRT-LM running Gemma 4 E2B on the phone, no server needed (64-bit installs with 8 GB of memory or more; see *On-device assistant*) |
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

### The release build is shrunk (R8)

`assembleRelease` and `bundleRelease` run R8 (`isMinifyEnabled` and
`isShrinkResources` in `app/build.gradle.kts`): it shrinks, optimises and
renames the code and drops resources nothing uses. A debug build is not
shrunk, so passing on a debug build does not show that the release works.

- **Keep rules** are in `app/proguard-rules.pro`, each with the reason it is
  there. The libraries bring most of their own; the file keeps what nothing
  else does: LiteRT-LM, ONNX Runtime and sherpa-onnx (their native code finds
  Java and Kotlin classes and fields by name), one player listener method
  that is found by name, and the line numbers in stack traces. A rule goes in
  only when a release build shows it is needed (a crash, a failed native
  lookup). After adding a library that uses native code or reflection, run
  the release build before trusting it.
- **The mapping.** Every release build writes
  `app/build/outputs/mapping/release/mapping.txt`, which turns an obfuscated
  stack trace (from Google Play, or a problem report copied from Settings →
  About & updates) back into names with R8's retrace. The App Bundle carries
  it for Play. `release.ps1` / `release.sh` stop if the build wrote none and
  keep a copy beside the signed files as `domovoi-<version>-mapping.txt`;
  `play-bundle.ps1` / `.sh` check that the bundle carries it and keep a copy
  too. It is not attached to the GitHub release.
- **Testing it.** Run the release APK on a phone or the emulator before a
  release. The release scripts' `-NoPublish` (`--no-publish`) run (below)
  builds, signs and checks the APKs without publishing anything.

### ABIs and APKs

Every native library the app ships has builds for all four Android ABIs
except LiteRT-LM, the on-device assistant's engine, which is 64-bit only.
So a 32-bit APK is the whole app without the on-device assistant, and the
app hides the assistant there itself (`assistant/AssistantSupport.kt`:
a 64-bit process whose class loader finds `liblitertlm_jni.so`).

| Library | arm64-v8a | armeabi-v7a | x86_64 | x86 | From |
|---|---|---|---|---|---|
| `liblitertlm_jni.so` (on-device assistant) | yes | — | yes | — | LiteRT-LM 0.18.0 AAR |
| `libonnxruntime.so`, `libonnxruntime4j_jni.so` (phone voice, speech) | yes | yes | yes | yes | onnxruntime-android 1.28.0 AAR |
| `libsherpa-onnx-jni.so` (speech recognition, voice detection) | yes | yes | yes | yes | built here, `native/sherpa-onnx/` |
| `libandroidx.graphics.path.so`, `libdatastore_shared_counter.so` | yes | yes | yes | yes | AndroidX |
| `libimage_processing_util_jni.so`, `libsurface_util_jni.so` (room mode's QR scanner) | yes | yes | yes | yes | CameraX 1.4.2 |

**16 KB pages.** Every 64-bit library is built with 16 KB aligned LOAD
segments and stored uncompressed at a 16 KB offset in the APK, so it maps
on a phone with 16 KB memory pages (Android 15 and later may use them;
Google Play requires it; Android 16 runs an app that isn't in a
compatibility mode, with a warning). `check<Variant>NativeAlignment` reads
every `lib/arm64-v8a` and `lib/x86_64` library in the built APKs and fails
`assemble` when one isn't (the report is in
`app/build/reports/native-alignment/`); it caught CameraX 1.3.4's
`libimage_processing_util_jni.so` (4 KB), which is why CameraX is 1.4.2.
32-bit libraries are exempt: no 32-bit process runs with 16 KB pages.

What a build carries (`app/build.gradle.kts`):

```bash
./gradlew :app:assembleDebug                              # default: ONE APK, arm64-v8a + x86_64 (a phone and the emulator)
./gradlew :app:assembleDebug -Pdomovoi.abis=arm64-v8a     # one APK, only those ABIs (comma list, or "all")
./gradlew :app:assembleDebug -Pdomovoi.apkPerAbi          # one APK PER ABI, all four: app-<abi>-debug.apk
./gradlew :app:bundleRelease                              # an App Bundle with all four ABIs
```

- The **default stays one APK for a phone and the emulator**, so the dev
  loop and the emulator harness install the same file as before. All four
  ABIs in one APK would add about 60 MB of 32-bit libraries (stored
  uncompressed) that a 64-bit phone never loads.
- **One APK per ABI** is what a GitHub release attaches (the two phone
  ABIs, arm64-v8a and armeabi-v7a: *Publishing a release*, below); each
  carries only its own ABI's libraries, and the armeabi-v7a and x86 ones
  carry no LiteRT-LM. They share one `versionCode`, so a phone can move from one to
  another with an ordinary update (same signing key).
- An **App Bundle** (any `bundle*` task) carries all four ABIs unless
  `domovoi.abis` says otherwise, and keeps Play's default split by ABI
  (`bundle { abi { enableSplit = true } }`): Play builds one APK per ABI
  from it and gives each phone only its own. A 32-bit phone gets the
  armeabi-v7a split, with no LiteRT-LM; a 64-bit phone the arm64-v8a one.
  To see what a given phone would get:
  `bundletool build-apks --bundle=app-release.aab --output=x.apks --device-spec=phone.json`
  with e.g. `{"supportedAbis": ["armeabi-v7a"], "sdkVersion": 26, "screenDensity": 320, "supportedLocales": ["en-US"]}`.

## Release-signed builds

The APK the CI workflow publishes (`.github/workflows/android-apk.yml`,
run by hand from the Actions tab only, the `domovoi-debug-apk` artifact)
is **debug-signed** with a key the
runner makes up for that run, and its SHA-256 is in the run summary. It is
a test build: each CI run carries a different key, Android won't install
one over another (uninstalling to upgrade loses the saved servers and the
household token), and nothing ties it to this project. What the build
itself trusts: the workflow's actions are pinned by commit SHA and run with
a read-only token, and `gradle/actions/setup-gradle` validates the wrapper
jar; the Gradle distribution the wrapper then downloads
(`gradle-8.14.3-bin.zip` from services.gradle.org, over HTTPS) is not yet
checked against a digest, because `gradle/wrapper/gradle-wrapper.properties`
has no `distributionSha256Sum` (Gradle publishes the value beside the zip,
as `gradle-8.14.3-bin.zip.sha256`). For a phone that keeps the app, use a
release build signed with a key you keep: the next section.

## Publishing a release (the app every Domovoi server hands out)

A household gets the app from **its own Domovoi server**: the dashboard's
"add a spare device" QR code opens `<server>:6370/join` on the phone, which
downloads the APK from that server and opens the app as the room
(Settings → Phones & browsers → "get the Domovoi app" is the same page without a
room). The server hands out only what `android/release.json` in its
checkout names: per ABI the APK's file name, size and SHA-256, and the
SHA-256 of the **release key's signing certificate**. Before it keeps or
serves an APK it verifies the file's own v2/v3 signature and requires that
certificate (`domovoi/android_app.py`, `domovoi/apk_signature.py`). It gets
the files from this repo's GitHub release only when an admin presses
**Download the app to this server** (on the spare-device card or Settings →
Phones & browsers; never by itself), or from an admin's upload when internet access
is off. `release` is `null` until the first release, and
every page says "no app release yet".

Publishing is the owner's job, on his own machine: the signing key never
goes into CI, a session or the repo.

**Once: the release key.** RSA (the server checks RSA signatures only),
kept outside the checkout (`.gitignore` refuses `*.jks`, `*.keystore` and
`*.p12` in it anyway), backed up with its password:

```bash
keytool -genkeypair -v -keystore ~/domovoi-release.jks -alias domovoi \
  -keyalg RSA -keysize 4096 -validity 10000
```

Every later release must be signed with this key, or phones refuse the
update (the script refuses too). A phone with a debug build installed
uninstalls it once before the first release build goes on.

**Every release:**

1. Raise `versionCode` (and `versionName`) in `app/build.gradle.kts`,
   commit, and push. `versionCode` is `YYMMDDNN`: the build's date and
   that day's number (`26101501` is the first build of 2026-10-15), one
   sequence for the GitHub releases and Google Play. The script refuses a
   `versionCode` of another shape, one that is not above the last
   release's, and a checkout that is not its branch's tip on GitHub (pull
   or push first: the release is committed on top of it).
2. Run the script from a clean checkout of that branch (it says what it
   does as it goes):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\release.ps1
   ```

   ```bash
   bash android/scripts/release.sh          # Linux, macOS, Git Bash
   ```

   It builds `:app:assembleRelease -Pdomovoi.apkPerAbi -Pdomovoi.abis=arm64-v8a,armeabi-v7a`,
   asks for the keystore file, the alias and the passwords (typed, not
   shown; never written anywhere: apksigner reads them from the script's
   environment for that one call), signs
   `domovoi-<versionName>-arm64-v8a.apk` and `-armeabi-v7a.apk`, and checks
   them: `apksigner verify` (v2 present, one RSA signer, not a debug key,
   the same key as the last release), 16 KB page alignment, and the
   server's own check (`python -m domovoi.android_app check`). Then it
   writes `android/release.json`, commits it (`android: release <v> [skip
   ci]`) and pushes it, and publishes the GitHub release `v<versionName>`
   (the project-wide tag, e.g. `v1.0.0-rc3`) on
   `coders-farm-official/domovoi` with `gh`, **tagged at that commit**: the
   tag holds the manifest naming its own APKs, so a server installed at
   the tag hands them out. The release carries the two APKs and an
   APK-only `SHA256SUMS`, with the certificate's SHA-256 in the notes.
   Every release is a full one, marked Latest, release candidates
   (`1.0.0-rc3`) included: the suffix says what it is. `-Prerelease` / `--prerelease` makes one a pre-release.
   Last it checks the tag is that commit and downloads
   each file back by the URL the manifest names.

   If the run stops after the commit (the push, `gh` or a download
   failed), run it again from the same checkout: it sees the release
   commit at HEAD and carries on from there with the signed files it left
   (nothing is built or signed again).
3. The rest of the release (the Windows Setup, one `SHA256SUMS` for every
   file, the notes, the `stable` branch) is
   `scripts/release/finish-release.ps1`: [docs/CONTRIBUTING.md,
   "Releasing"](../docs/CONTRIBUTING.md#releasing).

`-NoPublish` (`--no-publish`) stops after the checks (nothing is
committed, pushed or published), leaving the signed APKs, `SHA256SUMS` and
the manifest it would commit in `app/build/outputs/apk/release-signed/`;
`-SkipBuild` (`--skip-build`) signs what the last build left. Needs a JDK,
the SDK's build-tools 35.0.0, Python 3.11+, git with push access and `gh
auth login` with permission to publish releases.

**Google Play:** `android/scripts/play-bundle.ps1` (or `.sh`) builds the
App Bundle and signs it with the same key (jarsigner; its prompts and
checks are the release scripts'), and `android/play/PUBLISH-CHECKLIST.md`
walks through the Play Console, Play App Signing with this key (PEPK)
included. It reads the same `versionCode` (`YYMMDDNN`, the same shape
check) and refuses one below the last GitHub release's in
`android/release.json`: the bundle of a build already released on GitHub
keeps that release's `versionCode`.

Keep the key and its password safe and backed up: every later release has
to be signed with the same key, or phones refuse the upgrade.

**Before 2027:** Google's Android developer verification is to make apps
from unregistered developers hard to install on certified phones, sideloaded
ones included, from some date in 2027. Register the publisher and
`com.domovoi.app` with this key's certificate SHA-256 (the `cert_sha256` in
`release.json`) well before then.

### "A newer app is available from your server"

The app tells its person when the server it is connected to hands out a
newer release than the one installed (`update/AppUpdate.kt`). It asks
**that server only**, never the internet: the dashboard's open
`GET /api/devices/android-app` (the core's `GET /v1/android-app` plus
`join_url`), at most every half hour; a room-mode phone asks the core it
proved, after connecting and then every six hours.

- **Newer** means the release's `version_code` is above the installed
  `versionCode`.
- **Never for a Google Play install** (installer `com.android.vending`,
  from `getInstallSourceInfo`): Play updates it. **Never for a debug
  build** (`FLAG_DEBUGGABLE`): Settings → About & updates says "Debug build" and the
  server isn't asked. Never when the release's `cert_sha256` is not one of
  this install's signing certificates (a self-built copy): About says why.
- **Which APK:** the first of the phone's own ABIs (`Build.SUPPORTED_ABIS`,
  most preferred first) that the release carries, never a lesser one, so a
  64-bit phone isn't moved to the 32-bit app.
- **What the person sees:** on Home, one quiet card ("Domovoi 1.0.1 is
  available from your server"), **get the update** and **dismiss**. Get
  the update opens the server's join page in the browser as
  `/join?abi=<abi>&update=1` (`domovoi/join_page/join.js` puts that APK
  first and words the page as an update); the phone's installer does the
  rest. When the server knows the release but holds no checked copy for
  this phone yet, the card says an admin can press **Download the app to
  this server** (Settings → Phones & browsers) and has no button.
- **Dismiss** hides it until a newer release; a "not on the server yet"
  card dismissed comes back once, when the file arrives. Settings → About & updates →
  App updates always shows where things stand.
- **Room mode:** a faint line at the foot of the room screen, never a
  dialog: **update** (after the phone's unlock when the room shows over
  the lock screen) and **hide**.

## First run / offline-local mode

Without a configured server (or with one out of reach) the app runs in
**local mode**: Music, Videos and Files tabs, backed by the device's own
media via MediaStore (granular `READ_MEDIA_AUDIO` / `READ_MEDIA_VIDEO` /
`READ_MEDIA_IMAGES` permissions on API 33+) and the folders added to Files,
plus a **Chat** tab on a 64-bit install, with the chats kept on this
phone (the on-device assistant, below). A `connect` chip opens the discovery screen — the web backend on
your LAN, e.g. `http://192.168.1.20:6369`, health-checked via
`/api/health` before accepting. Change it later under Settings →
Phones & browsers. Traffic is plain HTTP on your LAN (same trust model as the
web dashboard), and plain HTTP is accepted **only** towards the home
network: RFC 1918 addresses, loopback, link-local, the emulator host
`10.0.2.2`, and names under `.local` / `.home.arpa` / `.internal` /
`.lan` / `.home`. Any other address must be `https://`. The platform
half of that rule is `res/xml/network_security_config.xml` (referenced
from the manifest, system trust store only); because Android cannot
express an IP range there, `net/CleartextPolicy.kt` enforces the whole
rule on the app's single `OkHttpClient`, which the API, both WebSockets,
media3 and Coil share; the same rule is applied in Settings → Phones & browsers
and, by hand, to every save-to-device download (`DownloadManager` is its
own HTTP stack). Backups are off (`allowBackup="false"`,
`dataExtractionRules`), so the server address, device id, pinned server
identities and the sealed pairing token never leave the device in a cloud
or `adb` backup or a device-to-device transfer.

## Permissions

What `AndroidManifest.xml` asks for, and why. Android grants the normal ones
at install; the sensitive ones (microphone, camera, location, contacts,
calendar, media, and notifications on Android 13 and later) are asked when
the feature is first used.

| Permission | Why |
|---|---|
| `INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE` | Reach the Domovoi server; tell Wi-Fi from mobile data; room mode's Wi-Fi link facts |
| `READ_MEDIA_AUDIO`, `READ_MEDIA_VIDEO`, `READ_MEDIA_IMAGES` (Android 13+), `READ_EXTERNAL_STORAGE` (up to Android 12L), `WRITE_EXTERNAL_STORAGE` (up to Android 9) | The phone's own Music, Videos and Files tabs; saving downloads. Folders in Files come through the system picker, never an all-files permission |
| `POST_NOTIFICATIONS` (Android 13+) | Timer and reminder alerts, downloads, room mode |
| `RECORD_AUDIO`, `MODIFY_AUDIO_SETTINGS` | The chat mic, drop-ins and room mode; asked on first use |
| `CAMERA` (optional hardware) | Scanning a room code; taking a picture for a phone chat; asked on first use |
| `FOREGROUND_SERVICE` with `MEDIA_PLAYBACK`, `DATA_SYNC`, `MICROPHONE` | Music playback; the model download; room mode's microphone |
| `WAKE_LOCK` | Keeps playback, downloads and a room awake |
| `SCHEDULE_EXACT_ALARM`, `RECEIVE_BOOT_COMPLETED` | Timers ring on time with the app closed; re-armed after a restart. The person's own "Alarms & reminders" grant on Android 12 and later |
| `ACCESS_COARSE_LOCATION`, `ACCESS_FINE_LOCATION` | A phone chat's "where am I" (approximate) and the Wi-Fi network's name (precise); optional, asked on first use |
| `READ_CONTACTS`, `READ_CALENDAR` | A phone chat calling or texting by name; reading the phone's own calendar; optional, asked on first use |
| `com.android.alarm.permission.SET_ALARM` | A phone chat's timers and alarms in the Clock app; a normal permission, no prompt |

It does not ask for texts, the call log, an all-files grant, usage stats or
accessibility.

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
  before the first token-bearing request to the saved server on a
  network, the app asks it `GET /api/health?challenge=<nonce>` without a
  token and checks the signed answer against the Ed25519 key it pinned
  for that server. Not the pinned key, or no identity where one is
  pinned, and no token-bearing request leaves — the state socket, the
  background timer sync and a ringing alarm's confirm included — and the
  shell says the server did not prove it is the one this phone paired
  with. The Ed25519 verifier is a plain Kotlin port of the core's vendored
  one, pinned to the RFC 8032 vectors.
  - **What a proof is good for** (`net/NetworkWatch.kt`): a verdict is
    keyed to a fingerprint of every network the phone could reach the
    server over — the default network and every Wi-Fi or Ethernet
    network, registered for separately so a VPN that stays the default
    cannot hide the Wi-Fi changing under it, with each one's transports,
    interface, addresses, gateways, DNS and DHCP server, from all four
    ConnectivityManager callbacks (a twin access point with the home
    SSID that hands out a different lease is a change too). A proof
    stands at most ten minutes on one network, is taken again whenever
    the app comes back to the foreground after a spell away, and a proof
    that straddled a network change is thrown away. The Wi-Fi SSID/BSSID
    join the fingerprint only where the app may read them (the network
    check never asks for location; a phone chat's optional Wi-Fi-name
    permission, below, is a separate question).
  - **The pin is the trust decision:** the picker's dialog shows the key
    the server advertises (compare it with the dashboard's Settings →
    About & updates) and pins it when you say yes, so the first proof has to match
    that key, not whoever answers first on some network; Settings →
    Phones & browsers probes a typed address the same way before saving it. A
    server trusted by a build from before this is pinned the first time it
    proves a key. A server trusted while it offered none is never pinned
    behind your back: the token goes out as it always did for it, the
    topbar says "unverified", and Settings → Phones & browsers shows the key it
    proves now with a "pin this identity" button.
  - **Core down is not an impostor:** a web backend that answers but
    could not ask its core (`domovoi_reachable` false — a restart, an
    update, a busy box) is "not fully up": the token is held, nothing is
    cached, the next request asks again, and the local-media shell says
    so. Only a different key, or a bad or missing proof where one is
    pinned, is "did not prove it is your Domovoi".
  - **Forgetting:** the pin shows under Settings → Phones & browsers next to the
    server, with every other trusted server and a forget button each —
    the server in use included, in Settings and in the server list, after
    a confirmation that shows the pinned key beside the one the server
    proves now. That is the way to re-pair after a reinstalled or replaced
    server; it returns the app to the server list. Switching servers drops
    trust (only the trust) that is attached to no listed server.
  - **Trusting again** (`ui/shell/IdentityChanged.kt`): when the server
    proves a different key, the app goes to the phone's own media at once
    and the banner says "This looks like a different Domovoi, or it was
    reinstalled", with **it's my Domovoi — trust it again**. The yes,
    given with the pinned and the proven fingerprints side by side, pins
    the key the server proved over this phone's challenge and DROPS the
    household token: it belongs to the identity it was paired with and never
    goes to the new key. The pairing screen follows. Nothing is re-trusted
    without that yes.
- **A pasted token is checked first** (`ApiClient.checkDeviceToken`,
  `CheckedPairingField`): the pairing screen, Settings → Phones & browsers
  (**pair** / **replace**) and the refused-token card each make ONE
  device-tier read (`GET /api/satellites`) carrying the candidate alone,
  after the identity gate admits the server and never during the server's
  wait. Taken, it is saved. Refused, it is not saved and the screen says
  "That token doesn't match this server. Copy it from Settings → Phones & browsers →
  Household token on this server's dashboard." A `429` counts the wait down
  with the button off. A `503` or no answer says "Couldn't check it with
  the server — try again" and saves nothing. Nothing retries by itself.
- **A refused token** (`net/TokenHealth.kt`, `ui/shell/TokenTroubleScreen.kt`):
  the server answers "no token" and "wrong token" with the same `401`,
  but the app knows whether it sent one. A device-tier `401`/`403` to a
  request that carried the token marks that token refused for that server
  (kept in the DataStore as the address only, cleared by any new token or
  by forgetting the server). From then on, requests go out without it, as an
  unpaired phone's do, which the server's wrong-token count never sees.
  Instead of the workspace the app shows one card, **Domovoi doesn't
  recognise this phone any more**, with the pairing field (**pair again**),
  **try the old token again** and **use this phone's media**. A device-tier
  `429` (its detail names the header) sets a wait from `Retry-After`. Only
  the core's and the web's own refusal, word for word, marks a token refused
  (`isServerDeviceRefusal`): a plugin's 401 that names the header does not,
  and neither does the `503` the server answers when it cannot check
  credentials (its database did not answer). Until
  it ends nothing carrying the token is sent to that server: the request
  fails in the app with `TokenThrottledException`, and the card counts the
  wait down. A phone holding an old token therefore costs the server's
  ledger one wrong attempt, not one per poll for as long as it runs.
- **Redirects** (`net/RedirectPolicy.kt`): OkHttp's own follower is off;
  the app follows a `3xx` itself, through the same interceptors, so a
  hop to another host loses the token, a plain-http hop to a public host
  is refused before any connection, an https→http downgrade is never
  followed, and a WebSocket upgrade is never redirected.
- **Save-to-device downloads** (`net/DeviceDownloads.kt`): the system
  `DownloadManager` is a separate HTTP stack that keeps every request
  header in its own database and replays it on retries and resumes. It
  is handed the household token only for the one save on the device tier
  — the video stream — after a fresh identity proof, over unmetered
  networks only, and an unfinished token-bearing download is cancelled
  (partial file and all) when the network changes, or at the next start
  if it was left waiting for a network. Music, podcast and audiobook saves
  are open reads and carry no credential. **Residual:** a finished video
  download's row keeps the header until the download is removed from the
  system's list (removing it from the app would delete the file); the
  database is private to the system and unreadable by other apps, but it
  is outside the vault. Streaming downloads through the app's own client
  (a foreground data-sync service into MediaStore) is the follow-up that
  closes it.
- **At rest** (`data/TokenVault.kt`): tokens are one AES-256-GCM blob in
  `shared_prefs/domovoi-vault.xml`, under a non-exportable key in the
  Android Keystore; a blob the key cannot open is "nothing paired" and the
  app asks to pair again. At every start a plain `device_tokens` value in
  the DataStore (an install from before the vault — or the maintainers'
  test harness, kept outside this repo, which pairs by writing exactly
  that) is swept into the vault and the plain key removed — only once the
  vault's record is committed to disk, so a process killed between the
  two writes leaves the token in one file or the other, never in neither
  — so the harness keeps working and nothing stays in the clear. **Debug-build caveat:** the
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

## On-device assistant

Phone chats: **Domovoi on this phone**. **A preview** in Domovoi 1.0: the
setup card says so beside the download (`PreviewLabel`, `ui/components/Kit.kt`).

Domovoi can answer **on the phone itself**, with no server and, once its
model is downloaded, no internet (a lookup is the one exception, and it
asks first: see *Documents, pictures, lookups and actions*): a language model
(Gemma 4 E2B, run by LiteRT-LM) on the phone's CPU, by text or by
push-to-talk voice. The model sees the system prompt, the conversation, the
phone's clock and a few phone facts, what its read-only tools return, and
what the person attaches to the chat (a document's matching passages, one
picture, a page that was looked up); nothing else.

**What a phone chat knows.** Each place answers from its own world: a phone
chat reads the phone, and never the household's data on the home server (its
calendar, timers, rooms or chats; `NoNetworkTest.aPhoneChatNeverReachesTheServer`:
the classes that read the phone name no server client). The phone facts below
are read on the phone when asked and go nowhere but the model's context and the
saved conversation (app-private, outside backups); "report this reply" shows
exactly what it sends, and the facts and tool results are not in it. The few
times a phone chat does use the network are listed under *What leaves the
phone*.

- **Prompt facts** (`assistant/PhoneFacts.kt`, under about 60 tokens,
  told like the clock: a kept conversation hears a line again only when it
  changed): what's playing (on this phone, or on a room speaker it cast
  to, never named), the playback sleep timer, whether the home server is
  connected, the listener's first name (`data/HouseholdNames.kt`). No room
  names and no house timers: phone chats answer about the phone only;
  asked about rooms or house timers they say those
  are in the home server chats. The persona says to
  use them only when asked, never to mention them otherwise. The position
  is left out on purpose (it changes every second).
- **Fast paths** (`assistant/PhoneQuestions.kt`, instant, no model, like
  the clock's): what's playing; battery and charging; free space; Wi-Fi or
  mobile data; the next alarm (`AlarmManager.getNextAlarmClock`); Do Not
  Disturb (and the ringer, for "on silent");
  which phone and Android; which model is answering; the connected
  Bluetooth headphones (Android's audio outputs, no permission); and, with
  permission, the phone's own calendar, where am I (the last fix Android
  has, as coordinates: no address lookup) and the Wi-Fi network's name.
  The weather says plainly that a phone chat doesn't use the internet.
- **Library fast paths** (`LibraryQuestions`): "do I have songs by…",
  "do I have a video called…", "where did I leave off in…", "which
  chapter am I on", "how much memory does this phone have".
- **Read tools** for the model (`assistant/PhoneTools.kt`), in LiteRT-LM's
  manual mode (the app runs each call; constrained decoding; on the CPU
  only): `find_music`, `find_video`, `find_book`,
  `phone_status`. They only read. **Offered only to a model whose catalog
  entry sets `readTools`, which Gemma 4 E2B doesn't**: on a foldable phone in
  testing, E2B called a tool for 26.7% of near-miss phrases ("why
  does my battery drain so fast?") and the descriptions cost +1.6 s to a
  fresh conversation's first word, so on E2B the facts come from the fast
  paths only. Meant for E4B once it is in the catalog and measured.
- **Hand-offs** (`assistant/HandOffs.kt`, `HandOffLauncher.kt`): call or
  text (the dialer, `ACTION_DIAL`, or Messages with the number filled in;
  the person comes from the contacts with that permission, else the
  contact picker), an email draft, directions in the maps app, a calendar
  event in the calendar app's own editor (`ACTION_INSERT`), a web link
  (https only), the share sheet with the last reply. Each is a card first
  ("Text Sam?") and launches only on a tap; one no app takes is never
  offered (`resolveActivity`, with the manifest's `<queries>`). Timers and
  alarms through the Clock app work the same way (see *Actions*, below).
- **Permissions, asked on first use, always optional**: approximate
  location (where am I), precise location (only for the Wi-Fi name, with
  its own reason), contacts (calling or texting by name), the
  calendar (reading it), the camera (taking a picture for a chat; the room-code
  scanner asks for it too). The chat's card gives the one-line reason, then
  Android's dialog; "not now" or a refusal is kept
  (`AssistantSettings.declinedPermissions`) and the feature says it can't,
  pointing to Android's settings, without asking again. Setting a timer or
  alarm uses the Clock app's own intents and the install-time `SET_ALARM`
  permission (a normal permission: no prompt, and the app sets nothing
  itself). Never: texts, the call log, notifications, usage stats,
  accessibility.
- **The gate** (debug builds): `N1GateReceiver` runs the 20 scripted and
  30 general questions through a phone chat with pinned facts and writes
  `files/n1-gate.txt` (its comment has the adb lines).

**Documents, pictures, lookups and actions** (a phone chat's extras; each
is something the person asks for):

- **Documents** (`assistant/docs/`). The paperclip attaches a PDF, Word
  (.docx), OpenDocument (.odt), Excel (.xlsx), CSV, text or markdown file
  (up to 100 MB), and Files' "Ask about this document" starts a chat on
  one. The text is read on the phone; the file is copied to
  `filesDir/chat-docs/attachments/<conversation>/` (app-private, outside
  backups, deleted with the conversation). A question is answered from the
  few passages that match it, the sources are listed under the reply, and
  what the document doesn't say gets "I couldn't find that in the
  document." A long document's summary is read in parts by a job that
  survives leaving the screen. **Reading a PDF's text needs Android 15, or
  Android 12 to 14 with Google's PDF update** (`PhonePdfText`); on another
  phone Chat says so and offers **Ask the home server**, which sends that
  one PDF to the home server, takes the text back and keeps nothing there.
  A long summary can likewise be handed to a private job on the home
  server (**Use home server**): the upload is deleted once read, and the
  text and summary stay only in the server's memory until the phone takes
  the summary (24 hours at most, 10 minutes once done with no phone
  watching).
- **Pictures** (`assistant/PhoneVision.kt`). A phone chat can be asked about
  one photo, from the camera or the gallery. The photo buttons show only on
  a phone whose GPU runs the model's photo reader, which the app finds out
  once by trying it and remembers (on the CPU it is too slow and heavy). The
  photo is shrunk to 1,024 pixels on its long side and made into a JPEG with
  no metadata, all in memory: it is never written to storage, not even the
  cache, and the conversation keeps a "photo" marker, not the picture.
  **Letters** (`assistant/PhoneLetters.kt`): asked what a letter says, the
  chat reads the photo's text and explains it, and keeps that text beside
  the answer (deleted with the chat) so follow-up questions can use it; the
  photo itself is still never kept. On a phone that can't read photos
  itself, the "+" offers **a letter for the home server** when a paired
  server can read letters: that one photo goes to the home server
  (`POST /api/chat/letters/read`), which reads it in memory, keeps nothing
  and sends back only the text; the explanation is written on the phone.
- **Drafts** (`assistant/drafts/`). A phone chat can write a letter or
  other document as a draft, kept with the conversation
  (`filesDir/chat-docs/drafts/<conversation>/`, every version, deleted with
  it). **Save** writes a Word or PDF file into the app's "Chat documents"
  folder (shown in Files); **Share** or **Save a copy to…** hands one file
  to the app or place the person picks, through a `FileProvider` that is
  not exported; **send to home**, offered when a home server is paired,
  uploads that one file into the server's Documents → From Chat. The PDFs
  embed the bundled Liberation fonts.
- **Looking things up** (`assistant/lookup/`). The model may propose a
  lookup; nothing goes online until the person answers a card that shows
  the exact words and where they would go: **Allow once**, **Allow in this
  chat** or **Not now**. The chat's "internet" chip shows and changes that
  chat's setting, and the chat's **model settings** hold **Always allow
  internet** and **Use your own search**. At home the lookup goes to the
  home server's own search (its manual and offline library first, then
  Wikipedia and Wikidata, then the web), and the server's internet answer
  "never" is respected. Away from home (or when the home server doesn't
  answer) it goes to Wikipedia directly and, when the person set their own
  search, to their SearXNG address or Brave Search key too (kept sealed in
  the Android keystore; Wikipedia first, then theirs, and Wikipedia alone
  if theirs fails). The own SearXNG address must be `https://`, except a
  home-network address (RFC 1918, link-local, a `.local` name, an IPv6
  unique-local or link-local address), which may be `http://`. A page the
  person names is fetched over https only, with no cookies and no scripts,
  2 MB at most. Nothing carries the household token except the request to
  the home server, and the answer lists its sources. Wikipedia is told the
  app's name and version and the project's GitHub address, as its rules ask.
- **Actions** (`assistant/PhoneActions.kt`, `ActionHost.kt`). Narrow phrases
  the app itself recognises, not the model (in testing the model made false
  actions): a timer or alarm goes to the Clock app after a card asks first;
  the phone's own player (play saved music, pause, resume, next, previous,
  stop, a sleep timer, a saved book) acts at once with an **Undo**; a saved
  video asks first. They are the phone's own: a house timer is a home server
  chat's.

**What leaves the phone.** Only these, each on the person's own action: the
downloads of models and voices (from Hugging Face; Wi-Fi only by default); an
allowed lookup (the search words or page address, to the place the card
names; at home a search is first checked against the home server's own
sources, which stay in the house); a PDF or long document, a letter photo
or a draft the person hands to the home server; and a reply report or a
draft the person shares or emails. The conversation as a whole and the
audio are never sent, and no picture is sent except a letter the person
chooses to have the home server read.

**Where it is: in Chat** (One Chat, 2026-10-21; there is no separate
Assistant tab any more). Chat has one history list and one conversation
view for two places a chat can run (`ui/screens/chat/ChatPlace.kt`): **on
this phone** (this assistant) and **on home server** (the server's chat,
shared with every paired device; its chip says "shared with the household"
until this phone has used it once). Every chat shows its place as a chip
in the list and in its header. Each place answers from its own world and
they never share a conversation or data.

- **A new chat** starts in the place this phone used last; the very first
  time, on the home server when it is in reach and has a chat model
  installed, else on this phone. Until something is said, a picker on the
  "Message Domovoi…" box moves it to the other place; it lists only the
  places that work right now.
- **Away from home** (local mode: no server in reach) Chat is a tab beside
  Music, Videos and Files and lists the phone's chats only, with the line
  "home server chats show when you're home" (the phone keeps no copy of
  them). Phone chats work with no server at all and in airplane mode.
- **The model download** is shown inside Chat the first time a phone chat
  is started without the model; home-server chat works without it.
- **The home server's AI off** (its capability manifest says
  `features.ai: false`): Chat offers no home-server chats, and keeps the
  phone's. A 32-bit install has no phone place; with neither, Chat is on
  no launcher.
- A shared screen (the kitchen tablet) leaves Chat off its launchers, and
  never lists the phone's chats there.
- A saved phone bar that had the old Assistant tab has Chat in its place,
  moved once on the first start after the update (`PhoneBar.migrate`).

**Who gets it.** A 64-bit install (`assistant/AssistantSupport.kt`; the
32-bit APKs carry no engine, and nothing offers the assistant there) on a
phone sold with **8 GB of memory or more** (`assistant/DeviceTier.kt`).
Android reports a little less than the chips hold (an 8 GB phone 7.2-7.8
GiB), so the floor is read as the size the phone is sold in; a phone below
it, or one Android calls low-RAM, gets a plain "this phone can't run the
assistant" and no controls.

**The model download** (`assistant/ModelCatalog.kt`, `ModelDownloadWork.kt`,
`ModelDownloader.kt`), the assistant's only large download (its other
network uses are under *What leaves the phone*):

- Three models, 2.45 GiB in all, each file from its upstream Hugging Face
  repo pinned to one commit (`resolve/<commit>/`, never `main`) with its
  SHA-256 compiled in: the language model (`gemma-4-E2B-it.litertlm`,
  2,588,147,712 bytes, Apache-2.0) and what it listens with, Moonshine tiny
  (speech to text, 42.2 MiB, MIT) and Silero VAD (end of speech, 1.7 MiB,
  MIT). The setup screen lists each with its size and licence, and the
  free space needed (the files plus 200 MB), before anything downloads;
  with too little room it says how much to free and offers no download
  button. A phone set up for text before voice arrived has the language
  model only: the mic offers the speech files (43.9 MiB) on its first tap.
- A WorkManager foreground job (`assistant-models`, a progress
  notification) that carries on when the app leaves the screen and after
  a restart. **Wi-Fi only by default**; an "allow mobile data" switch lets
  it use any network (a running job restarts under the new rule).
- Resumable: the file is written to `<file>.part` and a dropped connection
  or a pause resumes with an HTTP `Range` request (a host that answers 200
  restarts the file). It is hashed while it is written and renamed into
  place only when the SHA-256 matches; a mismatch is deleted and fetched
  once more, then reported. Pause keeps the partial file; cancel deletes
  it.
- Over the app's own HTTP client without the household token (the same
  redirect and cleartext rules; an https→http redirect is refused).
- Stored in `filesDir/models/<id>/` (app-private, outside backups, removed
  on uninstall). **Delete model** (the assistant's model settings) frees
  the space; the download can be run again.

**Memory.** The model loads when the assistant shows (about 2.4 s on the
CPU on the owner's phone) and is freed five minutes after it leaves the
screen, or at once when Android asks the app for memory, so playback and
the rest of the app are not starved. **CPU by default**; the GPU is an
opt-in in the model settings (faster replies on many phones, slower to
load, about 1.5 GB more memory, and not every phone's driver works; a
failed GPU load offers the CPU). A long conversation is given to the model
from its newest turns, as much as fits the model's context.

**Voice** (push to talk; `assistant/VoiceSession.kt`). Tap the mic (the
composer's button while nothing is typed): the microphone opens only now,
the voice detector (Silero) hears where the question ends (half a second of
silence), Moonshine turns it into text on the phone, and the transcript
becomes the question, marked "spoken". The reply streams as a typed one does
and is read aloud sentence by sentence, the first while the model is still
writing the rest (`SentenceSplitter` strips Markdown first), in the phone's
own voice: Domovoi's Piper voice picked in Settings → Voices once it is
downloaded (the chat mic's replies use the same pick; Kristin unless
another is chosen), Android's own text-to-speech voice until then. The one
button follows the turn: while it listens and has heard you it sends what
you said (or just stop talking); otherwise it stops everything at once,
the voice silenced and the model stopped, what was written kept.

- **Replies to typed questions stay silent** unless "read typed replies
  aloud too" is on in the model settings.
- **Audio is never written anywhere**: it is heard in memory and dropped;
  only the transcript is kept, as the question.
- **One mic owner at a time** (`audio/MicArbiter.kt`): the assistant asks
  before it opens the mic and says who has it when it is refused; the mic
  closes at the end of speech, on stop, or when the assistant leaves the
  screen. The microphone permission is asked on the first tap.
- **Audio focus**: the phone's own music pauses while the mic listens and
  ducks under the reply.
- With developer options on (below), the long-press on a spoken turn's
  reply shows the voice numbers: end of speech to the first spoken word
  (the target is 3 s or less; the detector's half-second silence
  wait is shown beside it), and how long speech to text took. The model
  settings show the last stop's tap to silence.
- **Debug builds only** (`app/src/debug`): a test can queue WAV files in the
  app's private `files/debug-mic-feed/` (reachable only with `run-as`) and
  the next voice turn hears the first of them instead of the microphone, at
  the microphone's own pace. The release build has no such feed.

**Conversations** stay on the phone: one JSON file per conversation in
`filesDir/assistant/` (app-private, outside backups and device-to-device
transfer), never sent to a server (a lookup sends only its search words or
page address, and a document, a letter photo or a draft goes to the home
server only when the person asks it to). A conversation keeps a lookup's
sources and the text read from a letter photo; its attached documents are
kept beside it in `filesDir/chat-docs/attachments/` and its drafts in
`filesDir/chat-docs/drafts/`, and they go when it does. The history lists
them; each can be deleted, and **clear all conversations** deletes every one. With developer
options on, a reply's long-press also shows how it went (time to first
words, tokens a second, CPU or GPU).

**The benchmark** (Settings → About & updates → developer options) stays as a
measuring tool: load time, time to first words, decode speed, voice turns,
memory, network use and the 10-minute heat loop, with results to copy. It
shares the model files with the assistant, and each frees the other's copy
of the model before loading its own.

**Developer options** (`data/DeveloperOptions.kt`,
`ui/screens/settings/DeveloperOptionsSection.kt`, since 2026-10-16) keep
the measuring tools out of a household's way: the benchmark, a reply's
timings in its long-press menu and the last stop's timing in the model
settings show only while they are on. Tap **app version** (Settings →
About & updates, or the no-server settings' **About this app**) seven times, as on
Android's own build number: from the third tap a toast counts down ("You
are now 3 steps away from developer options"), the seventh says
"Developer options on", and a **Developer options** section appears at the
end of the page with the benchmark and a **use developer options** switch
that turns them off again. The choice is kept on the phone (the
`developer_options` key in the `domovoi` DataStore); a phone that never
chose starts with them on in a debug build (the version line says "debug
build") and off in a release build. An install without the assistant (a
32-bit phone or APK) has nothing to put under them, so there the version
line is plain text and they never come on.

**Report this reply** (Google Play's AI-generated content policy: a
person can report an answer to the developer). Long-press a finished
reply, **report this reply**: the sheet shows exactly what the report
holds (the question before it, marked if spoken; the reply; the model and
CPU or GPU; when; the app's version) and takes an optional note. **Save on
this phone** keeps it in `filesDir/assistant-reports/` (one JSON file each,
app-private, outside backups, apart from the conversations so clearing
those keeps a report); the model settings' **Reports** card lists them, to
send on or delete one or all. **Email draft to coders farm** opens the
person's email app with the report addressed to info@codersfarm.org
(the project's address); it is sent only when they press send there.
**Share as text** opens Android's share menu with the report as plain
text. Where reports go is one line, `REPORT_DESTINATION` in
`assistant/ReplyReport.kt`, behind `ReportSink`: `ReportDestination.Email`,
`ReportDestination.Https(url)` (the report POSTed as JSON by
`net/ReportUpload.kt`: https only, no household token, no redirect,
"sent" only on a 2xx; the in-app path Play's policy describes, once
Coders Farm has the endpoint) or `ReportDestination.None` (the share menu
only). A change is declared in Play's data safety form
(`android/play/DATA_SAFETY.md`).

**The microphone permission** is asked on the first tap of the mic, after
the app's own one-line reason ("Domovoi needs the microphone to hear your
question…", `ui/screens/assistant/MicPermission.kt`); after a refusal
Android won't ask about again, the tap shows the way to the app's settings
instead of doing nothing.

**Privacy.** Settings → About & updates (and the local shell's settings) has a
**Privacy** card: what the app keeps and where it goes, and the privacy
policy's link from `PRIVACY_POLICY_URL`
(`ui/screens/settings/PrivacyCard.kt`): `docs/PRIVACY_POLICY.md` as
GitHub shows it on main (live once pushed; the owner may replace it with
a Coders Farm page). Blank shows no link; it must be https. The card's
points say what holds on every install: storage stays private, a phone
chat goes online only for a lookup you allow and hands a document, letter
or draft to the home server only when you choose, the audio is never kept,
and there is no analytics, advertising or crash reporting (`PrivacyCardTest`
holds the card to that).

**TalkBack.** Titles are headings; the voice turn's state (listening,
thinking, speaking) and the engine's loading or failing are live regions,
read out as they change; a message's copy and report are screen-reader
actions too, and it says who wrote it; switch rows toggle as one labelled
control; every icon button and link says what it does
(`AssistantAccessibilityTest` composes every state and finds no unlabelled
control).

**Open-source parts** of the assistant (the licences card in Settings →
About & updates lists them with their notices; nothing GPL; fonts under the
SIL Open Font License):

| Part | Licence | How it gets here |
|---|---|---|
| LiteRT-LM 0.18.0 (the engine) | Apache-2.0 | in the 64-bit APKs |
| Gemma 4 E2B, instruction-tuned (`.litertlm`) | Apache-2.0 | downloaded by the assistant |
| AndroidX WorkManager 2.11.2 (the download job) | Apache-2.0 | in every APK |
| sherpa-onnx 1.13.8, built without text to speech (speech recognition, voice detection); compiled in: Eigen (MPL-2.0, unmodified), nlohmann/json (MIT), kaldi-decoder, kaldi-native-fbank, kaldifst, OpenFst, simple-sentencepiece (Apache-2.0), KISS FFT (BSD-3-Clause) | Apache-2.0 | in every APK; the assistant's listening and the benchmark |
| ONNX Runtime 1.28.0 | MIT | in every APK |
| Moonshine tiny, English (speech to text) | MIT | downloaded by the assistant |
| Moonshine base, English (speech to text, larger) | MIT | downloaded by the benchmark (developer options) |
| Silero VAD v4 (voice detection) | MIT | downloaded by the assistant |
| Piper voices Norman, Kristin, Linda | public domain | downloaded from Settings → Voices; speak the assistant's replies |
| Liberation Sans and Liberation Serif 2.1.5 | OFL-1.1 | in every APK; embedded in the PDFs a phone chat's drafts are saved as, the same files as the server's |

Under those parts the licences card lists **every library in the build**,
from the variant's own dependency graph (`app/build.gradle.kts`
`OpenSourceTask` writes `licences/maven.json` into the assets, with each
library's POM licences and the licence and notice files it carries, and
copies `android/licences/`: the Apache-2.0 text, ONNX Runtime's licence and
third-party notices, libyuv's licence). Which licence each library is
under is decided by hand in `ui/screens/settings/OpenSourceLicences.kt`
(`RULES`), and `OpenSourceLicencesTest` holds the build to it: a library
with no rule, a non-permissive rule, a POM that disagrees with its rule, a
stale rule, or an analytics, advertising or crash-reporting library fails
the unit tests. **Adding a dependency** therefore means checking its
licence at its source and adding (or confirming) its rule.

## Settings: the dashboard's groups, device-local panels

The app's Settings has the dashboard's five groups and plain names
(2026-10-21, `SettingsScreen.kt` `SettingsGroup` / `SettingsSection`): a
search box and the groups, then a group's sections, then the section; Back
steps out one level. What belongs to the phone has its panel here:
**Rooms** (the satellites), **Phones & browsers** (which server, theme,
this device's name, "listening as"), **Printers & scanners** (the printers
and scanners your home server prints and scans with: status, ink, defaults;
renaming and removing are admin-only, so it says where), **App layout** (the
bottom bar),
**Voices** (how this phone speaks), **Chat** (Chat & AI: a heads-up
before shaky results, on this phone), **About & updates** (with the app's
version, and the developer options it turns on: "On-device assistant",
above) and **Health** (the checks Home's problem rows come from, and the
container guard's details). The sections only an admin changes on the
server (Wake words, Greetings, Models, Internet, Voice history, Advanced)
need the dashboard's admin session, which this app does not have: each
says so in plain words, and **Open on the dashboard** launches the browser
at that section of the connected server (`<server>/#settings/<section>`,
in the words every dashboard version knows); with no server configured
the button is disabled with a hint. Links from before the regroup
(`satellites`, `phonebar`, `connection`, `voice`, `server`) still land.

While connected, the save-to-device actions across Music / Videos /
Podcasts download through the system `DownloadManager` into
`Downloads/Domovoi`, which MediaStore indexes — so anything saved shows
up in local mode automatically.

Audiobooks save into the app's own storage instead (`audiobooks/`,
2026-10-20): `filesDir/audiobooks/b<id>-<random>/` holds the audio, the
cover and `book.json` (title, chapters, files, the server it came from),
so a saved book plays with its chapters and no server, needs no storage
permission on any Android version, and never shows up as songs. Each save
is a WorkManager job (`BookDownloads`): any network, then a check that the
book's server answers; out of reach it retries with a linear back-off from
a minute (and at once when the phone joins a network), resuming each
file's `.part` with a Range request over the app's own client, so no
header is kept in a system database. The book page shows the size before
saving; the notification and the page cancel. One place per book
(`BookPositions`): streaming and the saved copy share it, it is kept on
the phone and sent to the book's server's slot for this phone when
connected; when both moved, the newer wins. "Continue from the kitchen"
reads `/api/audiobooks/{id}/positions`. A folder book plays as one item
(`player/BookMediaSources.kt` joins its files). Audiobooks → **your
device** lists the saved books and the space they use; the local shell
has an Audiobooks tab once one is saved. **Add to server** uploads files
(a book each) or a folder (one book, `create_dir=true`) into
`core:audiobooks`; **remove from server** asks for the admin password,
sends it only to a server that just proved the identity the phone pinned
for it (`IdentityAdmission`: a verified pin, paired or not; a legacy
server without a key is refused), signs in for that one delete
(`/api/files/delete`) and signs out (residual: a kill in between leaves the
session to its 30-day expiry; docs/SECURITY_PRIVACY.md).

## Layout

```
app/src/main/java/com/domovoi/app/
├── AppContainer.kt        # singleton graph: prefs, api, bus, player, alerts, assistant
├── assistant/             # on-device assistant: model catalog, store, download job, engine, conversations,
│   │                      #   phone facts and questions, actions, hand-offs, pictures, reply reports
│   ├── docs/              # documents in phone chats: text extraction, passages, long-document jobs
│   ├── drafts/            # drafts in phone chats: store, Word and PDF writers, the "Chat documents" folder
│   └── lookup/            # looking things up: the rules, and the clients (the only network code here)
├── audiobooks/            # books saved to the phone: store, resumable download job, one place per book
├── net/                   # ApiClient (data.js analog), StateBus (/ws/state), rememberApi hooks
├── alerts/                # timer/reminder notifications + the local alarm mirror, container-guard and help notifications
├── audio/MicArbiter.kt    # one microphone owner: room mode, drop-in, chat mic, assistant
├── diagnostics/           # the phone's own crash and freeze record (Settings → About & updates)
├── dropin/                # the phone side of a drop-in call
├── phonemizer/            # text to phonemes for the phone's own Piper voice
├── roommode/              # this device as a room satellite (see "Room mode")
├── update/                # "a newer app is available from your server"
├── voice/                 # the chat mic and the phone's own voice
├── data/Prefs.kt          # DataStore-backed settings
├── player/                # PlayItem, PlayerController (player.jsx analog), PlaybackService
└── ui/
    ├── theme/             # domovoi design tokens (oklch → sRGB), light/dark
    ├── components/        # Pill, StatusDot, cards, dialogs, Domovoi glyphs, fmt helpers
    ├── shell/             # adaptive nav: bottom bar (phone) / rail (medium) / sidebar (tablet)
    └── screens/           # home, chat (One Chat: both places, history, documents, pictures), music,
                           # podcasts, audiobooks, videos, images, news, people, satellites (a Settings
                           # tab), calendar, stations, files, documents, printing (print and scan
                           # sheets, Printers & scanners), containers (Settings → Health), local
                           # (offline MediaStore music+videos), assistant (the phone chat's screens),
                           # settings, manual
```

The app opens on **Home**, as the dashboard opens on `#home`: the house
at a glance (rooms and what they are playing, every timer counting down,
problems worth knowing about, today's events, an announce box), built
from the same open reads as the web page. Home is also where the app
falls back when a screen's plugin goes away, and the top-left brand
leads back to it: the "domovoi" crumb in the topbar, the rail's glyph,
the drawer's brand row.

Responsive behavior: compact widths get the web phone strip as a bottom
bar — the phone bar, Home / Music / Videos / Chat until someone picks their
own two to five tabs for that phone in Settings → App layout (Home is always
first and counts as one; kept in the app's Prefs, so each phone has its own; a shared screen
never shows its personal screens there). Every other screen, Settings and
the manual included, is a tile on Home's "everything" grid (whose "edit
the bar" link opens that editor), and the home tab stays lit while one of
them is open. Satellites is a tab of Settings, as on the dashboard
(Settings → Rooms); Home's rooms and problem rows open it there.
List→detail screens stack full-screen. Medium gets a navigation rail
(Home first);
expanded gets the web's permanent sidebar with badge counts and
side-by-side list + detail panes, and Home spreads into the web
desktop's two columns once its pane is wide enough. The mini player
docks above the navigation on every size.

A **shared screen** (an admin marks the device in the dashboard's
Settings → Phones & browsers; the kitchen tablet) is learned from this install's
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
  Exact alarms: always on API 26–30; from 31 with the person's
  `SCHEDULE_EXACT_ALARM` grant ("Alarms & reminders", off by default
  for a new install on Android 14+), inexact without it. Never
  `USE_EXACT_ALARM`: Google Play keeps it for alarm-clock, timer and
  calendar apps. Without the grant Home's timers card says "Timers may
  ring a few minutes late on this phone" once, with **allow alarms**
  (the system's page) and **not now**; when the grant comes,
  everything is re-armed exact (`alerts/ExactAlarms.kt`). Re-armed after
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
- Two other kinds of notification ride the same live socket while the app is
  running, each on its own channel: the container guard's incidents
  (`alerts/IncidentAlerts.kt`, for an admin) and voice help's deliveries
  (`alerts/HelpAlerts.kt`). Neither is part of the timer machinery above.
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

## Room mode

**A preview** in Domovoi 1.0, labelled on Settings → Rooms' "Use this
device as a room" card and at the top of the explainer.

A spare phone or tablet as a room satellite (the person's guide:
[docs/SPARE_DEVICE_SATELLITES.md](../docs/SPARE_DEVICE_SATELLITES.md#phones-and-tablets-room-mode)).
It speaks the satellite protocol on the core's `/v1/stream/{room}`
(docs/uml/satellite-protocol.md) in Kotlin; it does not run the Pi's
Python. Package `roommode/` (not `satellite/`, which would sit beside
`ui/screens/satellites/`), plus `audio/MicArbiter.kt`.

| File | What |
|---|---|
| `RoomSetupActivity.kt` | The way in: the "Domovoi Room" launcher alias, Settings → Rooms → "Use this device as a room", a `domovoi-room:` link. The explainer (scrolled to its end), the mic and notification permissions, the room code (scanned with `RoomScanner.kt`, CameraX + ZXing, or pasted) |
| `RoomEnrolment.kt` | The room code: `domovoi-room:1?core=…&fp=…&room=…`, percent-encoded UTF-8; the room name to a room id (`^[a-z][a-z0-9_]{0,31}$`) |
| `RoomStore.kt` | What a room phone keeps; the room key sealed under its own Keystore key; `active` (the normal app is locked) and `running` (resume after a reboot) |
| `RoomIdentity.kt` | `verify_server`'s port (the address-bound `/v1/health` proof, before every connect) and signed file lists (`signature_v2`, freshness) |
| `RoomProtocol.kt`, `CaptureMachine.kt`, `HealthSample.kt`, `RoomSessionRules.kt` | Frames, the capture loop frame by frame (pause hints, `end_capture`, follow-ups), the health sample, what the refusals mean. Pure |
| `WakeDetector.kt`, `OrtModels.kt` | openWakeWord's streaming pipeline (melspectrogram → embedding → wake model) and Silero VAD, on ONNX Runtime: the runtime the phone voice already ships for all four ABIs |
| `RoomModels.kt`, `RoomSounds.kt` | The models (`/v1/wake-base`, pinned like the core's `OWW_MODEL_FILES`; `/v1/wake-models`) and the clips (`/v1/sounds`), each from a signed list |
| `RoomEngine.kt` | The room: the connect loop, the listening thread (wake, acknowledgement, capture, reply, follow-ups, drop-in relay), the core's frames, health |
| `RoomMic.kt`, `RoomAudio.kt`, `RoomNetwork.kt`, `RoomDevice.kt` | The mic through `assistant/MicCapture.kt`'s `AndroidMic` (and "mic blocked": two seconds of exact zeros), the speaker / clips / music / volume, the Wi-Fi-bound client with no token interceptor, battery and Wi-Fi facts |
| `RoomService.kt` | The foreground service (microphone + mediaPlayback, Wi-Fi lock, partial wake lock), its notification, the after-reboot "tap to resume" |
| `RoomModeActivity.kt`, `RoomStatusText.kt` | The room display: own task, dark, drifting; ring, mic chip, Mute, Exit behind `KeyguardManager` |

Rules it keeps:

- **Room key only.** No household token: room mode's client has no token
  interceptor and follows no redirect; while room mode is on the app's
  client puts the token on nothing (`net/HouseholdLock.kt`, checked by
  `DeviceAuthInterceptor` and `tokenForDownload`), the state socket is
  closed, the timer sync and mirrored alarms are stopped, token-bearing
  downloads are cancelled, and `MainActivity` sends everyone to the room
  screen (and leaves Recents).
- **Exit behind the unlock.** A phone holding a household token needs a
  screen lock to be a room (`RoomLockRules.kt`); leaving offers keep or
  forget.
- **Old phones.** Works in the armeabi-v7a APK and from Android 8: it uses
  ONNX Runtime only, never LiteRT-LM or sherpa-onnx (the assistant's).
- **One mic owner** (`audio/MicArbiter.kt`): room mode, the phone drop-in
  call, the chat mic and the assistant; room mode excludes the others for
  as long as it is on.
- **`supports` says exactly what it handles** (`RoomProtocol.supports`):
  `speech_pause`, `capture_control`, `announce_after_session_end`,
  `wake_models`, `sounds`, and `full_duplex` only where
  `AcousticEchoCanceler.isAvailable()`. Never `chat_mode`.

Tests: `app/src/test/java/com/domovoi/app/roommode/`,
`audio/MicOwnershipTest.kt` and `net/HouseholdLockTest.kt` (JVM).
`RoomGuardsTest` pins the guards in Android entry points by reading the
compiled classes (mute, the sticky restart, the drop-in's echo canceller,
the normal app's redirect, the lock rule); `RoomNetworkTest` that no
redirect is followed; `RoomModelsTest` the freshness, pin and body checks.
`RoomProtocolTest` reads
`docs/uml/satellite-fixtures.json` (the core's and the Pi's tests read it
too); `RoomEnrolmentTest` reads `docs/uml/spare-device-enrol-fixture.json`
(`satellite/enrolment.py` and the dashboard's tests read it too) and must
match every case; `RoomIdentityTest`
checks a health answer and an envelope the core's own code signed;
`RoomRulesTest` checks the app's model pins against
`domovoi/satellite_media/fetchers.py`. The wake-word parity test runs the
real models (not in the repo: their licence) against openWakeWord's own
scores when pointed at them:

```bash
./gradlew :app:testDebugUnitTest -Pdomovoi.oww.dir=<openwakeword>/resources/models \
  -Pdomovoi.room.clips=<folder with hey_jarvis.wav, what_time.wav and their .oww.json scores>
```

**Debug builds** carry a test feed for the emulator (`src/debug/`,
`DebugFeedReceiver`): `adb shell am broadcast -a com.domovoi.app.debug.ROOM_FEED
-n com.domovoi.app/.roommode.DebugFeedReceiver --ez on true` makes the
room's mic play queued raw PCM (16 kHz mono) from the app's external
`files/feed/` (`--es play <file>`) instead of the microphone. Only a sender
holding `DUMP` (adb's shell) reaches it; a release APK does not contain it.

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
- No server administration (greetings, wake words, models, the internet
  answer, voice history, Advanced) — by design; each of those Settings
  sections opens the dashboard instead.
- Home shows the problem rows a household member may see and never the
  admin-only ones (satellites waiting for approval or adoption, updates,
  disk) or the "this box isn't claimed yet" row: the app has no admin
  session to read them with. A plugin problem opens the dashboard's
  plugin list in the browser. The "everything" grid lists the app's own
  screens; plugin web pages stay on the dashboard.
