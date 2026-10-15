# Android compatibility

Which phones the Android app runs on, which APK to install, and what each
Android version and processor gets. The app is built and tested on the
Android emulator; see [What was tested](#what-was-tested) for exactly how.

## Requirements

- **Android 8.0 (API 26) or later.** The app targets Android 16 (API 36),
  as Google Play requires of new apps; see [Android 16](#android-16-api-36).
- **Any of four processor types (ABIs):** 64-bit ARM (`arm64-v8a`, almost
  every phone from about 2017 on), 32-bit ARM (`armeabi-v7a`, older and
  budget phones, Android Go), and the x86 / x86_64 used by emulators and a
  few Chromebooks and tablets.
- **The on-device assistant needs a 64-bit phone** running the 64-bit
  app. Its engine, LiteRT-LM, is published for 64-bit processors only.
  Everything else is the same on every ABI.

## Which APK to install

A release attaches one APK per processor type. Each carries only its own
native libraries.

| APK | For | On-device assistant |
|---|---|---|
| `app-arm64-v8a-*.apk` | 64-bit ARM phones and tablets: pick this one if unsure | yes |
| `app-armeabi-v7a-*.apk` | 32-bit ARM phones (old, budget, Android Go) | no |
| `app-x86_64-*.apk` | x86_64 emulators, Chromebooks and tablets | yes |
| `app-x86-*.apk` | 32-bit x86 (old emulator images, a few old tablets) | no |

If Android answers the arm64-v8a APK with "App not installed" (from a
computer, `adb install` says `INSTALL_FAILED_NO_MATCHING_ABIS`), the phone
is 32-bit: install the armeabi-v7a APK. A 64-bit phone also runs the
32-bit APK, without the assistant; Settings → About then says the 64-bit
app has it.

The app installed from an **App Bundle** (Google Play, or `bundletool`)
gets the right libraries automatically: the bundle carries all four ABIs
and Play hands each phone only its own (see
[android/README.md](../android/README.md#abis-and-apks)).

## What works where

Works = same as on the newest Android. Degraded = works, with the
difference in the note. Hidden = not offered at all (no button that can't
do what it says). "32-bit" is the armeabi-v7a or x86 APK on any Android
version from 8.0.

| Feature | 8.0 (26) – 8.1 (27) | 9 (28) – 10 (29) | 11 (30) | 12 (31) – 12L (32) | 13 (33) + | 32-bit |
|---|---|---|---|---|---|---|
| Install, start, the phone's own Music / Videos / Files | works | works | works | works | works | works |
| Media permission | one prompt, "photos, media, and files" | one prompt, "photos and media" | one prompt | one prompt | separate prompts for music, for photos and videos | as the Android version |
| Connect, pair, Home | works | works | works | works | works | works |
| Music from the server on the phone, media notification | works | works | works | works | works | works |
| Video feed (server and phone): pause on leaving, screen kept on | works | works | works | works | works | works |
| Chat (text) | works | works | works | works | works | works |
| Chat mic (hold to talk) | works; mic prompt Allow / Deny | works; Allow / Deny | works; "While using the app" / "Only this time" | as 11 | as 11 | works |
| Chat mic haptics | degraded: plain key / long-press feedback | degraded: as 8 | works: confirm / reject | works | works | as the Android version |
| Settings → Voice: the phone's own voice, sample clips | works | works | works | works | works | works (ONNX Runtime has 32-bit builds) |
| Timer and reminder alerts | works; exact alarms with no permission | works | works | works; exact alarms need "Alarms & reminders" (on by default; inexact if revoked) | works | works |
| Notifications | on, no prompt | on, no prompt | on, no prompt | on, no prompt | asked at first start | as the Android version |
| Problem reports | degraded: crashes and freezes; no "not responding" closes, and the card says so | degraded: as 8 | works: crashes, freezes, "not responding" closes | works | works | works |
| On-device assistant benchmark (Settings → About) | works; its results lack the thermal status and name the board, not the chip | works; thermal status from 10 | works | works; chip name from 12 | works | **hidden** |
| Room mode (this device as a room) | works; the mic service starts from the room screen | works | works; mic only "while using the app": the service starts from the room screen, and after a restart needs one tap | works; the mic privacy toggle shows as **mic blocked**; the notification's Stop asks for the unlock | works; notifications asked for in the explainer; Android 14+ declares the microphone service type | works (ONNX Runtime only) |
| Documents "download all" (zip) | degraded: lands in the app's own folder (Android/data/…/Download) | degraded on 9: as 8; works on 10 (public Downloads) | works | works | works | as the Android version |

Notes:

- **The keyboard no longer opens on its own on Android 8.** Android 8.x
  gives a window's first text field the focus at start, after a permission
  dialog and when a field lets go, so the keyboard used to pop up over the
  phone's Music filter and Home's announce box on every start. Fixed in
  `MainActivity` (the content frame takes that focus); Android 9 stopped
  doing this.
- **The on-device assistant on old Android versions:** its engine's
  library links against Android 8.0's system libraries (checked symbol by
  symbol against the NDK's API 26 stubs, and loaded on the Android 8.0
  image), and the card loads on every 64-bit emulator image above. Whether a given old phone is fast enough is
  a separate question; the assistant needs a phone sold with 8 GB of
  memory or more (`assistant/DeviceTier.kt`) and says so, with no download
  button, on a smaller one.
- **32-bit phones:** the app checks once per start whether the assistant's
  engine library is in this install and this process can load it
  (`assistant/AssistantSupport.kt`). Where it isn't, the Assistant tab and
  screen are on no launcher (local mode keeps Music, Videos and Files; Home's
  grid, the drawer, the rail and the phone-bar choices leave it out), the
  benchmark card is not shown, the local Settings screen doesn't mention the
  assistant, and the licences list leaves out the models only the assistant
  downloads.

- **Room mode** runs openWakeWord and the Silero voice detector on ONNX
  Runtime, the runtime the phone's voice already ships for all four ABIs,
  and loads nothing of the on-device assistant's (LiteRT-LM,
  sherpa-onnx). The QR scanner (CameraX) is asked for only when "Scan" is
  pressed, and the camera is not required to install. Drop-ins need the
  phone's echo canceller (`AcousticEchoCanceler.isAvailable()`); a phone
  without one is a room without drop-ins (the emulator images have none).

## Android 16 (API 36)

Since 2026-10-15 the app is compiled against and targets API 36 (AGP
8.13.2). What Android 16 changes for an app that targets it, and what the
app does (`TargetApi36Test` holds the code to it):

- **Edge to edge, with no opt-out.** Every activity draws behind the
  system bars and pads its content itself: the main app as before, and
  the room mode screens now call `enableEdgeToEdge` too (dark bars over
  their dark screens) on every Android version. No theme opts out.
- **Predictive back is the default.** Opted into on every version from
  Android 13 (`enableOnBackInvokedCallback`), so back behaves the same
  everywhere; the app handles back only through Compose's `BackHandler`
  (no `onBackPressed`, no `KEYCODE_BACK`). The room screen still keeps
  back from leaving the room: Exit is the way out.
- **Large screens ignore orientation and resize locks** from 600 dp wide:
  the app has none.
- **16 KB memory pages.** Every 64-bit native library is 16 KB aligned and
  the build checks it (`check<Variant>NativeAlignment`), so the app never
  runs in Android 16's page-size compatibility mode. CameraX moved from
  1.3.4 to 1.4.2 for it.
- **Background job quotas.** Android 16 counts jobs that run beside a
  foreground service against the app's job quota. The assistant's model
  download is a WorkManager job promoted to a foreground service, which
  WorkManager keeps running when the scheduler stops the job, and which
  resumes with an HTTP `Range` request if it is ever stopped.
- **Not affected:** `announceForAccessibility` (deprecated; the app uses
  live regions), `elegantTextHeight`, `scheduleAtFixedRate`, intent
  redirection from extras, health permissions.
- **Coming later, not yet enforced:** Android is adding a **local network
  permission** (opt-in on 16, enforced for a later target API). The app
  talks to its Domovoi server on the home network, so the move past API 36
  will need it; nothing changes at 36.

## What was tested

2026-10-12, debug APKs from `wf/android-old-devices`, on the Android
emulator (Google APIs images, x86_64 unless noted), one image at a time,
against a throwaway Domovoi (core + dashboard, a stub language model):

| Image | Android | APK | Result |
|---|---|---|---|
| `android-26;google_apis;x86_64` r16 | 8.0 | default (arm64-v8a + x86_64) | all checks pass after the keyboard fix |
| `android-29;google_apis;x86_64` r13 | 10 | default | all checks pass (see the heap note below) |
| `android-31;google_apis;x86_64` r14 | 12 | default | all checks pass |
| `android-33;google_apis;x86_64` r17 | 13 | default | all checks pass |
| `android-35;google_apis;x86_64` | 15 | default | not rerun here: the emulator every batch's smoke uses |
| `android-30;google_apis;x86` r16 | 11, **32-bit** | x86, and armeabi-v7a through the image's ARM translation | all checks pass; see [32-bit](#32-bit-on-the-emulator) |
| `android-26;google_apis;x86_64` r16 | 8.0, 64-bit | x86 (a 64-bit phone running the 32-bit app) | the phone's own media, Files, Settings; the note in place of the assistant |

Each run: install with no permissions granted, start (keyboard closed),
answer every permission prompt as it comes, play a song from the phone,
the media notification, the phone's video feed (and its pause / resume /
screen-on checks), the Files tab, the local Settings screen, connect and
pair, Home, a server song played on the phone, the server's video feed, a
chat message and its answer, the chat mic view (open, cancel), Settings →
Voice → play a sample, Settings → About, and a timer going off in the
house (its notification).

**Heap note (Android 10 image only):** the `android-29` Google APIs x86_64
image sets no `dalvik.vm.heapgrowthlimit`, so ART capped the app at 16 MB
and it ran out of memory preparing the phone's voice. Real phones set it,
as do the other images (192 MB). With the emulator given the same 192 MB
(`setprop dalvik.vm.heapgrowthlimit 192m` as root, then a runtime restart)
the same steps pass; the app's Java heap peaked at about 48 MB there.

**Room mode, 2026-10-13** (`wf/room-mode`), against a throwaway core and
dashboard (`ft.py up --with-core`, strict pairing on, Whisper tiny, the
openWakeWord models seeded into the run's satellite media cache), the
room's mic fed recorded speech through the debug build's feed (never the
host's microphone). The core was reached as `127.0.0.1` through `adb
reverse`; the security review's fix round (same day) made the room code
refuse loopback, as the shared enrolment contract does, so a rerun dials
the host as `10.0.2.2` (in 10/8) with that name in the throwaway core's
`TRUSTED_HOSTS`:

| Image | APK | Result |
|---|---|---|
| `android-35` x86_64 | default | explainer (Start disabled until scrolled), room code by `domovoi-room:` link, parked with its code, wrong code refused and right code approved through the dashboard API, "check now" to ready; "Hey Jarvis" (score 0.96) → chime → "what time is it?" → early commit → reply played; mute (wake word ignored) and unmute; an announcement played; the normal app redirected to the room screen while active; the service's type is microphone + mediaPlayback; reinstall while active → "tap to resume" → resumed; Stop listening from the notification → Resume; Exit: unlock prompt cancelled (still a room), then the PIN → the normal app |
| `android-30` x86 (32-bit only) | x86 | a second device for an approved room: "Another device is this room" shown; Exit on a phone without a screen lock (the dialog); a new room parked, approved, wake (0.87) → reply; compact layout on 1080x1920; the room's music (ExoPlayer on the room's MPD stream, `music_ready` → MPD resumed) |
| `android-30` x86 | armeabi-v7a, through the image's ARM translation | the same room after the update: wake (0.78–0.96) → reply |
| `android-26` x86_64 (8.0) | default | explainer, parked, approved, wake (0.95) → reply; the normal app redirected to the room screen |

Not tested on the emulator: drop-ins (no image has an echo canceller), a
real microphone, wake accuracy at a distance, days on a charger. Those
need real phones.

**API 36 and 16 KB pages, 2026-10-15** (`wf/assistant-m3`, debug APKs,
no server; the language model copied in after a hash check instead of
downloaded, the speech files downloaded by the app; the voice turn fed a
recorded question through the debug build's feed, never the host's
microphone; every screen also dumped and checked for controls with no
words for a screen reader or under 48 dp):

| Image | APK | Result |
|---|---|---|
| `android-36;google_apis;x86_64` r7, 8 GB | default | edge to edge in the app and on room mode's screens; a typed question answered (3.1 s to first words, 8.3 tokens/s, CPU); long-press → report this reply → note → share as text (the share menu with the report's text; the report saved in `files/assistant-reports/`) → model settings → reports; back through each pane (predictive back); the speech files downloaded with Android 16's job-quota enforcement turned on (`OVERRIDE_QUOTA_ENFORCEMENT_TO_FGS_JOBS`, `…_TOP_STARTED_JOBS`), 10 s; the mic: the app's reason, then Android's dialog; refused twice → "the microphone is off" → open settings opens the app's page; a spoken question transcribed exactly and answered aloud; room mode by `domovoi-room:` link to an address with nothing listening: the room screen (offline), its service foreground with microphone + mediaPlayback, mute and unmute, back stays a room, hold exit → confirm → forget → the normal app; Settings: privacy, all 139 libraries, LiteRT-LM's 2 MB notice opened with no skipped frames |
| `android-36;google_apis_playstore_ps16k;x86_64` r7, **16 KB pages**, 8 GB | default | no page-size compatibility dialog; LiteRT-LM answered a typed question; Moonshine and Silero (sherpa-onnx on ONNX Runtime) heard a spoken question and the reply was spoken; the room code's camera preview (CameraX 1.4.2) ran |
| `android-35;google_apis;x86_64`, 3 GB | default | local shell; the assistant says this phone can't run it (3 GB) with no controls; privacy and licences; room mode's explainer and microphone step |
| `android-26;google_apis;x86_64` r16 (8.0), 3 GB | default | as Android 15, room mode's screens edge to edge; a refused microphone → "open the app's settings" |
| `android-30;google_apis;x86` r16, **32-bit** | x86 | no assistant tab; privacy (without the assistant's points) and licences; room mode's explainer |

What the scan flagged on the first API 36 pass was fixed the same day and
scanned again: the licences' notice links (19 dp tall) and two "allow
mobile data" switches (Settings → Voice, the benchmark card: an
unlabelled switch beside its words). The rescan found nothing else
(only rows cut off at the screen's edge, which measure short).

### 32-bit on the emulator

The `android-30` x86 image is a 32-bit-only Android (`abilist`
`x86,armeabi-v7a,armeabi`, no 64-bit). On it:

- The default APK (arm64-v8a + x86_64) is refused with
  `INSTALL_FAILED_NO_MATCHING_ABIS`: the problem this work fixes.
- The **x86 APK** installs as a 32-bit app and passes every check above:
  the assistant's card is gone, the local Settings screen and the
  licences card don't offer it, and nothing else differs.
- **The phone's own Piper voice runs on 32-bit ONNX Runtime:** with the
  Norman voice in place, "play a sample" spoke through it (the converter's
  dictionary loaded in about 1 s, the voice in about 2 s).
- **The armeabi-v7a APK** also installs there and passes the same checks,
  run through the image's ARM translation (`libndk_translation`), Piper
  voice included (about 5 s to load). That shows the 32-bit ARM build
  works; it says nothing about speed on a real ARM phone.
- Every native library of the x86 APK, and of the x86_64 APK including
  LiteRT-LM's, loads on the Android 8.0 image (`System.load`, which binds
  every symbol at load).

The `android-31` and `android-33` x86_64 images have no 32-bit support at
all (`abilist` `x86_64,arm64-v8a` and `x86_64`), so 32-bit can only be
tried on x86 images (up to Android 11) or on the older x86_64 images that
also run 32-bit apps (the Android 8.0 and 10 ones here do).

**Not tested:** a real 32-bit ARM phone. This PC (x86_64, Windows) can't
emulate an ARM phone at a usable speed; the armeabi-v7a APK ran only
through x86 ARM translation (above) and was checked by its contents (no
LiteRT-LM; ONNX Runtime and sherpa-onnx for armeabi-v7a; every library
links against API 26's system libraries). Real old phones may also differ
in ways an emulator can't show: vendor Android builds, slower storage,
less memory, and how fast the phone's own voice is on an old ARM chip.
