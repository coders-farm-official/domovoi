# Android compatibility

Which phones the Android app runs on, which APK to install, and what each
Android version and processor gets. The app is built and tested on the
Android emulator; see [What was tested](#what-was-tested) for exactly how.

## Requirements

- **Android 8.0 (API 26) or later.** The app targets Android 15 (API 35).
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
  a separate question; the assistant's model needs about 8 GB of RAM.
- **32-bit phones:** the app checks once per start whether the assistant's
  engine library is in this install and this process can load it
  (`assistant/AssistantSupport.kt`). Where it isn't, the benchmark card is
  not shown, the local Settings screen doesn't mention the assistant, and
  the licences list leaves out the models only the assistant downloads.

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
