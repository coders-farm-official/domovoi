# Play policy review: what was found, what was fixed, what the forms ask

For the first Google Play submission of `com.domovoi.app` (versionCode
26101501, `wf/play-prep`). Policy facts were read at Google's own help pages
on 2026-10-15; anything those pages don't say is marked *(unverified)*.
Each answer names the code that makes it true.

**Re-checked 2026-10-28 for 1.0.0 (versionCode 26102801):** the manifest
diffed against `v1.0.0-rc3` adds only what section 1 already lists (location,
contacts, calendar, `SET_ALARM`, the `<queries>` hand-offs, the `FileProvider`)
and the launcher label/task-affinity placeholders (bench builds only). No new
permission, no new foreground service type and no new form to fill; the
three foreground service declarations in section 3 stand.

## 1. Blockers found, and what happened to each

| # | Finding | Would it fail review? | Done |
|---|---|---|---|
| 1 | `USE_EXACT_ALARM` declared from API 33 | Yes: Play allows it only for apps whose **core** function is an alarm clock, a timer or a calendar ([policy](https://support.google.com/googleplay/android-developer/answer/16558241)). Timers are one Domovoi feature among many. | **Fixed in code**: `SCHEDULE_EXACT_ALARM` on every level from 31, never `USE_EXACT_ALARM`; the person's "Alarms & reminders" grant from Home's timers card, inexact alarms without it, re-armed exact when it is given (`alerts/ExactAlarms.kt`, `ExactAlarmsTest`). |
| 2 | "Report this reply" only saved or shared | The AI-generated content policy asks for reporting **to the developer** without leaving the app ([policy](https://support.google.com/googleplay/android-developer/answer/13985936)). | **Fixed in code**, per Kamron's choice: an email draft to info@codersfarm.org (`REPORT_DESTINATION`; codersfarm@gmail.com up to versionCode 26101501). **Residual risk**: the draft opens the email app, i.e. it leaves the app; whether a reviewer accepts that is *(unverified)*. The in-app alternative is built and one line away: `ReportDestination.Https(url)` (`net/ReportUpload.kt`), needing only an HTTPS endpoint at Coders Farm. If review rejects on this point, switch the line, rebuild, resubmit. |
| 3 | `READ_MEDIA_IMAGES` and `READ_MEDIA_VIDEO` | Possibly: since 2025-05-28 Play allows them only when the core function needs broad photo/video access and asks for a declaration ([policy](https://support.google.com/googleplay/android-developer/answer/14115180)); without one, a new app is likely blocked *(unverified)*. | **Declaration drafted** (section 5). Not removed: local mode's Videos tab is a player for every video on the phone, and Files shows the photo library; the system Photo Picker can't list a library. Fallback if Play refuses: drop both and use the Photo Picker / folder picker (a code change, a day). |
| 4 | Foreground services: room mode (`microphone|mediaPlayback`), playback (`mediaPlayback`), WorkManager's download (`dataSync`) | Each type needs the Play Console declaration with a description, the impact if deferred, a video and a use case ([help](https://support.google.com/googleplay/android-developer/answer/13392821)). | **Answers drafted** (section 3); demo videos recorded on the emulator. |
| 5 | Privacy policy | Required, public, linked in the app. | **Done**: `docs/PRIVACY_POLICY.md`, linked from Settings (`PRIVACY_POLICY_URL`). It goes live when pushed to main. |
| 6 | Target API | Must be 36 for new apps since 2026-08-31 ([help](https://support.google.com/googleplay/android-developer/answer/11926878)). | Already 36 (`wf/assistant-m3`). |
| 7 | 16 KB memory pages | Required for apps targeting 35+. | Already enforced by `check<Variant>NativeAlignment`; the bundle carries the same libraries. |

Checked and **not** a problem:

| Item | Finding |
|---|---|
| `QUERY_ALL_PACKAGES` | Not declared (merged release manifest checked). The `<queries>` entries are the text-to-speech service intent and, since 2026-10-21, the phone chat's hand-offs (dial, SMS and mail `SENDTO`, `geo:`, calendar `INSERT`, https `VIEW`, text `SEND`, the contact picker) and the Clock app's `SET_TIMER`, `SET_ALARM`, `SHOW_TIMERS` and `SHOW_ALARMS`: intent-scoped visibility, which needs no declaration. |
| `com.android.alarm.permission.SET_ALARM` | **Since 2026-10-21.** A normal (install-time) permission, which Android requires to send `AlarmClock.ACTION_SET_TIMER` / `ACTION_SET_ALARM` to the Clock app. The Clock app sets the timer or alarm; the app itself schedules nothing new, so it needs no exact-alarm grant and no declaration *(none exists for a normal permission)*. Each timer and alarm is asked first in the chat. |
| `REQUEST_INSTALL_PACKAGES` | Not declared. The app never installs APKs; the household server hands the APK to the browser. |
| Location (fine, coarse, background) | **Fine and coarse since 2026-10-21, foreground only**, asked in a phone chat the first time someone asks "where am I" or the Wi-Fi network's name, after the app's one-line reason; optional. No background location (`ACCESS_BACKGROUND_LOCATION` is not declared), so no location declaration form applies *(unverified that none exists for foreground-only; none was found)*. Room mode still reads Wi-Fi names only where Android allows without it. |
| Contacts, calendar (read) | **Since 2026-10-21, optional**: `READ_CONTACTS` to call or text someone by name from a phone chat (the contact picker works without it), `READ_CALENDAR` to read the phone's own calendar. Neither is a restricted permission with a declaration form *(unverified)*; both are read on the phone only and never sent (Data safety: not collected). Owner, at v1: check the Play Console's "Sensitive app permissions" page lists nothing for them. |
| `SYSTEM_ALERT_WINDOW`, `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`, accessibility, SMS, call log, notification access, usage stats | None (owner rule for the phone assistant: never texts, the call log, notifications, screen-time or accessibility). Room mode only opens the system's battery settings for the person. |
| `CAMERA` | For scanning room mode's room code (asked when "Scan" is pressed) and for taking one photo for a question in a chat on the phone (asked on the first tap of "take a photo", after the app's own one-line reason; in-app CameraX, the photo is held in memory and discarded from memory after the reply, never saved, and never sent except a letter the person chooses to have their own home server read; once Android won't ask again, the tap opens the app's own "the camera is off" note with a way to the settings). `uses-feature` not required. No declaration exists for camera; the Data safety form treats both as processed on the device. The phone chat's "choose a photo" uses the system Photo Picker, which needs no permission. |
| `RECORD_AUDIO` | Asked at the first mic tap with the app's own reason first (`MicPermission.kt`), and by room mode after its explainer. Prominent disclosure: room mode's explainer screen, read to the end before "Start room mode". |
| `RECEIVE_BOOT_COMPLETED` | Re-arms timer alarms, and room mode posts "tap to resume". No microphone or media service is started from boot (Android 15 forbids it; `RoomLockRules`). |
| Cleartext HTTP | `network_security_config.xml` allows it, and `net/CleartextPolicy.kt` enforces it **only** to private addresses and local names (RFC 1918, loopback, link-local, `.local`, `.home.arpa`, `.lan`…): the household server on the LAN has no public certificate. Everything on the internet must be HTTPS, and the person's own search address for phone-chat lookups follows the same rule (plain `http://` only for a home-network address). No Play policy found against this; justification for any reviewer question: "Domovoi is self-hosted; the app talks to the household's own server on the home network, which has no publicly trusted certificate; all internet traffic is HTTPS." |
| `FileProvider` (`${applicationId}.files`) | Not exported; it grants one file at a time, read-only, to an app the person picks when they share or open a saved draft (paths: the app's "Chat documents" folder and a share cache, `res/xml/shared_files.xml`). No declaration exists for it, and it needs no permission. |
| `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE`, `WAKE_LOCK`, `MODIFY_AUDIO_SETTINGS`, `WRITE_EXTERNAL_STORAGE` (Android 9 and older only) | Normal permissions, or one limited to old Android versions; no declaration. |
| Ads, analytics, crash reporting | None; `OpenSourceLicencesTest` fails the build if such a library appears. |
| 32-bit / ABIs | The AAB carries arm64-v8a, armeabi-v7a, x86_64 and x86; Play serves each phone its own split. The 32-bit splits have no on-device assistant engine, and the app hides the assistant there. |
| minSdk 26 | Fine for Play. The assistant needs 8 GB of memory; no device-catalog exclusion is wanted, because the rest of the app (and room mode on old phones) works everywhere. |

## 2. Exact alarms (no form)

Nothing to declare: `SCHEDULE_EXACT_ALARM` has no Play declaration form
*(unverified that none exists; none was found)*. The app works without the
grant (timers may ring late) and asks for it in context.

## 3. Foreground service declarations (Play Console → App content → Foreground service permissions)

Fill one entry per type. Video links: upload the recordings (section 6)
to Google Drive shared as "anyone with the link" (or as an unlisted video
on Google's video site); paste the link.

### microphone (room mode: `roommode/RoomService.kt`)

- **Feature description:** Room mode turns a spare phone or tablet into a
  room speaker for the household's own Domovoi voice assistant. The user
  starts it after reading an explainer and granting the microphone. While
  it runs, the phone listens for the wake word on the device; only speech
  after the wake word is streamed to the user's own Domovoi server on their
  home network, which answers. A persistent notification with "Stop
  listening" shows the whole time.
- **User impact if deferred or interrupted:** The room would stop hearing
  its wake word as soon as the screen turned off or another app came to the
  front, so voice commands, timers set by voice and intercom calls in that
  room would stop working without warning. Listening has to continue with
  the screen off; it cannot wait for a later time.
- **Use case:** Background audio access (voice commands for a virtual
  assistant, audio not saved). If the list differs, pick the closest
  "voice assistant / always-on listening" entry or "Other" with the
  description above.
- **Video:** `domovoi-fgs-demo-2-room-mode.mp4`.

### mediaPlayback (music: `player/PlaybackService.kt`; room mode's replies and music: `roommode/RoomService.kt`)

- **Feature description:** The app plays the user's music, radio stations,
  podcasts and audiobooks (from the phone or from their own Domovoi
  server) through a media session with the standard media notification, so
  playback continues when the user leaves the app or locks the screen. In
  room mode the same service type lets the room speak its answers and play
  music sent to that room while the screen is off.
- **User impact if deferred or interrupted:** Music and spoken answers
  would stop as soon as the user left the app or the screen turned off.
- **Use case:** Media playback (background audio playback).
- **Video:** `domovoi-fgs-demo-3-playback-download.mp4` (first half:
  playback behind the home screen and its notification).

### dataSync (WorkManager's `SystemForegroundService`, used by `assistant/ModelDownloadWork.kt` and `audiobooks/BookDownloads.kt`)

- **Feature description:** When the user taps "download" for the on-device
  assistant's models (about 2.6 GB, once) or a phone voice (about 60 MB),
  the app downloads the files over HTTPS from pinned addresses, resumably,
  with a progress notification that has a pause button. Wi-Fi only unless
  the user allows mobile data. Since 1.0.0-rc3, "Save to phone" on an
  audiobook from the household's own Domovoi server works the same way:
  resumable, with a notification and cancel; if the server goes out of
  reach, the download pauses and finishes when it's back. Play's form for
  this type takes only the task kind ("Network processing → Other") and a
  video, so the existing entry covers it; nothing to change there.
- **User impact if deferred or interrupted:** A 2.6 GB download takes many
  minutes; if it stopped when the user left the app, the assistant could
  not be set up without keeping the screen on for the whole download.
- **Use case:** Network transfer: upload or download (user-initiated).
- **Video:** `domovoi-fgs-demo-4-model-download.mp4` (the voice download
  from Settings, its notification in the shade).
- Note: Android 15+ limits `dataSync` to 6 hours a day; the download
  resumes where it stopped. Google suggests user-initiated data transfer
  jobs for this, which WorkManager does not support yet; WorkManager's
  foreground service is the documented fallback.

## 4. AI-generated content (no separate form)

The on-device assistant (`assistant/`) runs a language model on the phone.
What the policy asks and what the app does:

- **Report offensive output to the developer:** long-press a reply →
  "report this reply" → the sheet shows exactly what the report holds →
  "email draft to coders farm" (`EmailDraftSink`, to info@codersfarm.org),
  plus "save on this phone" and "share as text". See finding 2 for the
  residual risk and the one-line HTTPS switch.
- **Prevent restricted content:** the model's own safety tuning, a fixed
  system prompt (`assistant/Persona.kt`) asking for short spoken answers, a
  4,096-token limit on prompt plus reply (`LiteRtLlmEngine.DEFAULT_MAX_TOKENS`), and
  since 2026-10-21 at most read-only tools the app runs itself, and none for the
  shipped model (Gemma 4 E2B answers phone questions from the app's own
  patterns) (finding a song, video
  or saved book, the timers, the phone's status: `assistant/PhoneTools.kt`);
  the model can't change anything, reach files or contacts, or the network
  (`NoNetworkTest`). A lookup is the app's own client, run only after the
  person's allow card (`assistant/lookup/`; Data safety, "Lookups in phone
  chats"); the model only proposes the words. Calls, texts and the other hand-offs are the app's own
  patterns, shown as a card the person taps, never the model's. The phone's actions (Clock timers and alarms, the player, saved books and
  videos) are also the app's own patterns (`assistant/PhoneActions.kt`),
  never a model tool call for the shipped model; timers, alarms and videos
  are asked first, playback can be undone.

## 5. Photo and video permissions declaration (App content → Sensitive app permissions)

Draft answer (the form's exact questions are not public *(unverified)*):

> Domovoi works as a local media player and file browser when no Domovoi
> server is connected ("local mode"). Its Videos tab lists and plays every
> video on the device, and its Files tab shows the device's photo library
> beside folders the user adds, so the user can browse, play and send their
> own media. This needs broad read access to photos and videos: the Photo
> Picker returns only items the user selects and cannot list a library to
> browse. The app never uploads media unless the user picks "upload" to
> their own server, and never modifies or deletes media. Without the
> permission, the tabs show a "allow access" prompt and the rest of the app
> works. Code: `data/LocalMedia.kt`, `ui/screens/files/PhoneFileSource.kt`.

If Play refuses: remove `READ_MEDIA_IMAGES` and `READ_MEDIA_VIDEO` and
switch those tabs to the Photo Picker and the folder picker
(`OpenDocumentTree`, already used for folders). `READ_MEDIA_AUDIO` is not
covered by this policy.

## 6. Demo videos

Recorded on the API 36 emulator with `adb shell screenrecord` (no audio),
kept outside the repo for size, with the maintainers' Play submission
material:

| File | Shows | Length |
|---|---|---|
| `domovoi-fgs-demo-1-assistant.mp4` | the on-device assistant: new conversation, mic tap, the app's reason, Android's microphone dialog, listening (the debug build's test feed speaks the question), the answer. No foreground service: for reviewers asking about microphone use. | 75 s |
| `domovoi-fgs-demo-2-room-mode.mp4` | room mode: explainer read to the end, Start room mode, the microphone permission, the room code, Become this room, the room screen with its mic chip, the notification shade with "Domovoi Room · Kitchen" and Stop listening, the green mic indicator. | 65 s |
| `domovoi-fgs-demo-3-playback-download.mp4` | a track playing, the home screen, the media notification; then a voice download started in Settings. | 75 s |
| `domovoi-fgs-demo-4-model-download.mp4` | the voice download from Settings with its "Downloading the voice" notification and pause button. | 37 s |

**Limit:** the emulator room in video 2 points at an address with no
server (the shared test server was in use by another job), so it shows
"offline · could not reach …" instead of answering. Google asks to see the
feature that uses the service; a 30-second re-record on a real phone with
the real server (room mode answering "what time is it", then the
notification) is the stronger video. Steps are in PUBLISH-CHECKLIST.md.

## 7. App access (App content → App access)

Choose **"All or some functionality is restricted"** and enter, as
instructions (no credentials exist):

> Domovoi is a self-hosted assistant: most features need a Domovoi server
> running on the user's own computer on their home network, which a
> reviewer cannot reach. Without one the app runs in local mode and
> everything there can be reviewed: open the app, allow media access to
> see Music, Videos and Files from the device, and open the Assistant tab
> (64-bit device with 8 GB of memory or more; it downloads its model, about
> 2.6 GB, on Wi-Fi) to chat with the on-device model, by text or the mic.
> Room mode ("Domovoi Room" icon) can be reviewed up to its room code
> screen; the demo videos linked in the foreground service declaration show
> it running. Server setup: github.com/coders-farm-official/domovoi.

Whether instructions without credentials are accepted is *(unverified)*;
if review asks for access, a public demo server is the only fuller answer.
