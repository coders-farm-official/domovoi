# Domovoi for iPhone and iPad

The native Swift and SwiftUI app, next to `android/` and under the same
rules: permissive licences only (`licences/README.md`, read it before adding a
package). It has its own version (`MARKETING_VERSION` in `project.yml`,
`1.0.0` for its first App Store release) and ships on the App Store, not with
the server's GitHub releases. CI runs on every push (`.github/workflows/ci.yml`'s
`ios` job builds the app and runs the package tests).

**The Android app is the specification.** Where the two could disagree (the
cleartext rule, the token's scope, Ed25519, the satellite protocol) the Swift
test mirrors the Kotlin test case for case, and both read the same shared
fixtures: `docs/uml/satellite-fixtures.json`,
`docs/uml/spare-device-enrol-fixture.json`, `domovoi/phonemizer/golden.json`
and the core-signed identity answers.

## What is here

| | |
|---|---|
| `project.yml` | The whole Xcode project as YAML, for XcodeGen: targets, capabilities, Info.plist keys, the permission wording. |
| `Domovoi/` | The app: one folder per area (Home, Music, Videos, Chat, Files, Documents, Lists, Calendar, Rooms, Room mode, Settings and the rest), its icon and its privacy manifest. |
| `Domovoi/PrivacyInfo.xcprivacy` | No tracking, no collected data, and the required-reason APIs the code calls. `domovoi/tests/test_ios_store.py` checks it against the code. |
| `DomovoiTests/`, `DomovoiUITests/` | The app's own tests: the Info.plist keys pairing depends on, a launch with Apple's accessibility audit, and the Simulator UI tests that run against a throwaway "stub" server. |
| `Packages/DomovoiKit/` | The logic the screens sit on (client, pairing, identity, protocol, models, room mode), tested with `swift test`. |
| `Packages/DomovoiPhonemizer/` | Empty on purpose: the Swift port of the phonemizer has not started (its README says what it will be). |
| `scripts/` | `pkgtest.sh`, `remote.sh`, `build-sim.sh`, `test.sh`, `install-on-phone.sh`, and the owner's `release.sh` (below). |
| `store/` | The App Store listing text, review notes and questionnaire answers. |
| `licences/` | The licence rule and the per-package table. |

## Decisions the app is built on

- **iOS 18 and up**: iPhone XS / XR and later, and iPad. iPad layouts are
  tested in the Simulator only. Features that need iOS 26 hide themselves
  below it.
- **`com.domovoi.app`**, the same as Android, but only in the Release
  configuration. Debug builds carry `org.codersfarm.domovoi.dev`, so a
  personal-team build can never register the real ID.
- **Version: three integers**, because Apple takes nothing else; TestFlight
  builds are the release candidates. Build numbers are the **YYMMDDNN**
  sequence shared with Android, and only ever go up.
- **Plain HTTP to the home network only.** ATS is asked for
  `NSAllowsLocalNetworking` plus exceptions for `.home.arpa`, `.lan`,
  `.internal` and `.home`, and the app enforces its own `CleartextPolicy` on
  top, because `NSAllowsLocalNetworking` admits public IP addresses too.
- **Chat is answered by the home server only.** The phone does not offer
  itself as a place to chat in this version, and Apple's on-device model is
  not used (owner answer Q11, 2026-11-14: `AppleAssistantEngine` is compiled
  but unreachable in a shipping build; `AssistantEngineHost`). The phone's own
  chat comes back with a model file of our own.
- **No speech-recognition permission.** Apple's speech recognition is not
  used, so that prompt never appears (`DomovoiAppTests` checks the key is
  absent).
- **ONNX Runtime** (Microsoft's Swift package, pinned exactly) is the one
  external dependency, for room mode's wake word.
- **Background audio only.** Room mode is a screen-on kiosk: iOS cannot
  restart a microphone from the background.
- **No increased-memory-limit entitlement**, and no entitlements file at all.

## Friend houses

"Message and share lists with friends’ houses, house to house, with no company server in between", as Android
has it (`android/README.md`, "Friend houses"): the house inbox, a
conversation and its composer, the friends and the shared lists, in Tools
beside Lists with the inbox's unread count as its badge
(`Domovoi/FriendHouses/`, `FriendHouses*.swift` in DomovoiKit).

- **Shown only when the server says it is on:** `GET /api/friend-houses`
  answers 2xx with `status` on, starting, stopped or error. Off,
  unavailable, a garbled answer, 401, 403, 404 and 501 hide it on every
  launcher; no answer, a 429 or a 5xx keep the last verdict; this read never
  raises the pairing screen. Read at start, on a server or token change, on
  reconnect, on coming back to the app and every minute while it is open
  (`FriendHousesGate`).
- **Admin-only things are words, never controls.** A 403 from any call says
  "Only an admin can do that, on the dashboard."; a 400 or 404 shows the
  server's `detail` as written.
- **Notifications** follow each friend's alert level (`inbox`: the badge
  only; `sender`: "A message from <name>"; `read`: the name and the text,
  cut to about 200 characters, control characters out). **Only while the app
  is open:** the iPhone app has no background check (its one background mode
  is `audio`, and there is no push and nothing hosted), so the check runs at
  each of the gate's reads, and the Friends tab says so. The first look at a
  server is a baseline, not a notification.
- **Hostile text:** every name, body, title and `last_error` comes from
  another house and is drawn with `Text(verbatim:)` only (no Markdown, no
  links, no previews, line breaks kept), never logged, and never in a URL: a
  path carries only the checked 64-hex house key and numbers.
  `FriendHousesPlainTextTests` holds the folder to it.

## Building and testing

The maintainers' Mac only builds and tests; the code is written and committed
elsewhere, as for the rest of the repo.

- **CI** (the `ios` job in `.github/workflows/ci.yml`, on every push that
  touches `ios/`): runs DomovoiKit's package tests on macOS, builds
  DomovoiPhonemizer, and compiles the app and its UI-test target for the
  Simulator, unsigned. Nothing there signs anything or holds a secret.
- **`scripts/pkgtest.sh`** runs the same package tests on the Mac in about a
  minute, with no Simulator. It pushes your current commit to a bare
  repository on the Mac over SSH (never through GitHub) and tests HEAD, so
  commit first. It reads the Mac's address from `DOMOVOI_MAC_HOST`, the same
  variables `remote.sh` uses, and the git remote `mac` by default.

  ```
  ios/scripts/pkgtest.sh                 # every package
  ios/scripts/pkgtest.sh DomovoiKit      # one package
  ios/scripts/pkgtest.sh --lines 400     # more of the run's output
  ```

- **`scripts/test.sh`** (through `scripts/remote.sh`, as the Mac's build
  user) also builds the app and runs the unit and UI tests on a Simulator with
  `CODE_SIGNING_ALLOWED=NO`; `--packages-only` runs only `swift test`. Every
  screen gets Apple's accessibility audit (`performAccessibilityAudit`), the
  twin of Android's TalkBack scan. The stub server the UI tests use is part of
  the maintainers' test tooling, which lives outside this repository.
- A Simulator test that needs a throwaway server reaches it through
  `remote.sh --forward PORT`, which allows ports 6600–6699 only and refuses
  6369, 6370, 6432 and 11434 outright, so a live Domovoi is never reachable
  from the build user.
- Builds and `swift test` work over SSH with nobody logged in, but the
  **Simulator and UI tests need the build user logged in at the Mac's
  screen** (fast user switching keeps it so). Everything runs under
  `caffeinate`, `nice` and `taskpolicy -b`, and the Simulator is shut down at
  the end, so the Mac's other users aren't slowed down.

## Who does what on the Mac

| | Build user, over SSH | The owner, in his own Mac account |
|---|---|---|
| `swift test` for the packages | yes | — |
| Simulator builds and tests, screenshots | yes | — |
| Apple ID, certificates, provisioning profiles | **never** | yes |
| A build on a real iPhone | **never** | `scripts/install-on-phone.sh` |
| Archives, App Store Connect, TestFlight, releases | **never** | yes |

Signing, and every decision that reaches Apple, is the owner's. No agent holds
a certificate, a profile, an Apple ID or an App Store Connect key, and nothing
here signs over SSH (it fails on a locked keychain anyway,
`errSecInternalComponent`). The build user is a Standard (non-admin) macOS
account with **no Apple ID**, reachable only by key, with "allow full disk
access for remote users" off.

## Releasing

Releases are the owner's, on the Mac, in his own account, with his own
keychain. No agent runs `release.sh`.

```
cd ~/domovoi && ios/scripts/release.sh            # archive, export an .ipa
cd ~/domovoi && ios/scripts/release.sh --upload   # archive, send to App Store Connect
```

- **The team** comes from `DOMOVOI_TEAM_ID`, or from a line
  `DOMOVOI_TEAM_ID=...` in `ios/release.local`, which is in `.gitignore`.
- **It refuses** a dirty tree, a `MARKETING_VERSION` that is not three
  integers, and a build number that is not a YYMMDDNN above every one used so
  far (`android/release.json`, `android/app/build.gradle.kts`,
  `ios/project.yml` and the Mac's own last release). Without `--build` it
  takes today and the next counter.
- **The version is the iPhone's own**; it does not follow Android's
  versionName or the server's version.
- It runs `xcodegen generate`, archives Release (`com.domovoi.app`), checks the
  archive's version, build, bundle id and privacy manifest, and exports with
  `scripts/ExportOptions.plist` (App Store Connect). Then it writes the build
  number into `project.yml` and prints the steps left by hand: upload, commit
  the build number, TestFlight, and the store page.
- **The store page** is `store/`: `details.txt` says which file goes in which
  App Store Connect field; `REVIEW-NOTES.txt` is for App Review;
  `APP-PRIVACY-ANSWERS.md` holds the App Privacy, export-compliance and
  age-rating answers. `domovoi/tests/test_ios_store.py` keeps every text
  inside its limit.
- **The icon** is `Domovoi/Assets.xcassets/AppIcon.appiconset`, made by
  `scripts/make_app_icon.py` from `docs/assets/domovoi-icon.png`
  (`--check` says whether the PNGs have drifted).
