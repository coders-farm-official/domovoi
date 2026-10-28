# Domovoi for iPhone and iPad

Native Swift and SwiftUI, next to `android/`, under the same rules: permissive
licences only, `[skip ci]` on every commit, and no GitHub Actions. The full
plan, the research and the decisions are in
`design-notes/IOS-APP-PLAN-2026-10-20.md`.

## The state of this track

**Nothing here has been compiled.** This folder was written on a Windows PC
before the Mac existed, so every Swift file in it is unproven: it has not been
through a compiler, a test or a Simulator. The first `swift test` is the next
batch's first job, and it is expected to find things.

What is here:

| | |
|---|---|
| `project.yml` | The whole Xcode project as YAML, for XcodeGen. Targets, capabilities, Info.plist keys, the permission wording. |
| `PrivacyInfo.xcprivacy` | The privacy manifest: no tracking, no collected data, four required-reason APIs. |
| `Domovoi/` | The app. One screen, enough to launch and to show the Local Network prompt. |
| `DomovoiTests/`, `DomovoiUITests/` | The app's own tests: the Info.plist keys pairing depends on, and a launch plus Apple's accessibility audit. |
| `Packages/DomovoiKit/` | The logic the screens sit on. Four ports from the Kotlin so far: `ServerAddress`, `CleartextPolicy`, `TokenScope`, `Ed25519Verify`, each with its Kotlin test mirrored, plus the licences test. |
| `Packages/DomovoiPhonemizer/` | A skeleton. The 1,730-line port is its own milestone. |
| `scripts/` | `remote.sh`, `build-sim.sh`, `test.sh`, `install-on-phone.sh`. |
| `licences/` | The licence rule and the per-package table. **Read it before adding a package.** |

Not here yet, on purpose: `IdentityGate`, `Discovery`, `NetworkWatch`,
`ApiClient`, the Keychain vault, the shell, and `release.sh`. They wait for a
batch that can compile what it writes.

## The layout it is growing into

```
ios/
  project.yml            XcodeGen: targets, capabilities, Info.plist keys
  PrivacyInfo.xcprivacy  the privacy manifest
  Domovoi/               the app (SwiftUI screens, shell, player, alerts)
  DomovoiWidgets/        widgets and Live Activities (later)
  Packages/
    DomovoiKit/          client, pairing, identity, protocol, models
    DomovoiPhonemizer/   the Swift phonemizer; golden test
  scripts/               remote.sh, build-sim.sh, test.sh, install-on-phone.sh
  licences/              the rule and the per-package table
  README.md
```

The tests read the same shared fixtures as Android:
`docs/uml/satellite-fixtures.json`,
`docs/uml/spare-device-enrol-fixture.json`,
`domovoi/phonemizer/golden.json`, and the core-signed identity answers. **The
Android app is the specification.** Where this app and that one could disagree
— the cleartext rule, the token's scope, Ed25519, the satellite protocol — the
Swift test is the Kotlin test, case for case, so the two cannot drift apart
quietly.

## How the loop works

Agents work on this PC, in a worktree, as for everything else. The Mac only
builds and tests.

1. An agent commits on a `wf/ios-*` branch here.
2. The branch is pushed **over SSH to a bare repository in the build user's
   home on the Mac** — never through GitHub.
3. On the Mac, as the build user, through `scripts/remote.sh`:
   - `scripts/test.sh --packages-only` runs `swift test` for the packages. No
     Simulator, no screen, no signing: this is the fast, reliable half.
   - `scripts/test.sh` also builds the app and runs the unit and UI tests on a
     Simulator destination with `CODE_SIGNING_ALLOWED=NO`.
   - `xcrun simctl` boots, installs, launches, grants permissions and takes
     screenshots, which are copied back here.
4. Every screen also gets Apple's accessibility audit
   (`performAccessibilityAudit`), the twin of Android's TalkBack scan.
5. A Simulator test that needs a throwaway server reaches it through
   `remote.sh --forward PORT`, which allows ports 6600–6699 only and refuses
   6369, 6370, 6432 and 11434 outright. Kamron's live Domovoi is never
   reachable from the Mac's build user.
6. Everything on the Mac runs under `caffeinate`, `nice` and `taskpolicy -b`,
   and the Simulator is shut down at the end, so Kamron's own work on that Mac
   stays smooth.

**One thing known in advance** (plan 5.1, checked 2026-10-21): builds and
`swift test` work over SSH with nobody logged in, but the **Simulator and UI
tests need the build user logged in at the Mac's screen**. Apple's own staff
call running screen-based code from SSH "always a bad idea". So the build user
stays logged in through fast user switching, and a LaunchAgent in that session
is the fallback if Simulator runs over SSH prove flaky.

## What runs where

| | Build user on the Mac, over SSH | Kamron, in his own Mac account |
|---|---|---|
| `swift test` for the packages | yes | — |
| Simulator builds and tests, screenshots | yes | — |
| Apple ID, certificates, provisioning profiles | **never** | yes |
| A build on a real iPhone | **never** | `scripts/install-on-phone.sh` |
| Archives, App Store Connect, TestFlight, releases | **never** | yes |

Signing is Kamron's, and so is every decision that reaches Apple. No agent
holds a certificate, a profile, an Apple ID or an App Store Connect key, and
nothing here signs over SSH — it fails on a locked keychain anyway
(`errSecInternalComponent`), which is the second reason to keep it his.

The build user is a Standard (non-admin) macOS account with **no Apple ID**,
reachable only by key, with "allow full disk access for remote users" off.

## Decisions this folder is built on

- **iOS 18 and up** (owner answer Q1): iPhone XS / XR and later. The iOS 26-only
  features — AlarmKit, SpeechAnalyzer — are extras that hide themselves below 26.
- **iPhone and iPad** (Q4). Kamron has no iPad, so iPad layouts are tested in
  the Simulator only, and the review notes will say so. Not on Apple-silicon
  Macs until someone has used it there.
- **`com.domovoi.app`** (Q2), the same as Android — but only in the Release
  configuration. Debug builds carry `org.codersfarm.domovoi.dev.kamron`, so a
  personal-team build can never register the real ID before the Coders Farm
  organisation account does.
- **Version `1.0.0`**, three integers, because Apple takes nothing else;
  TestFlight builds are the release candidates. Build numbers are the shared
  **YYMMDDNN** sequence with Android, and only ever go up.
- **Plain HTTP to the home network only.** ATS is asked for
  `NSAllowsLocalNetworking` plus exceptions for `.home.arpa`, `.lan`,
  `.internal` and `.home` (Q5) — and the app still enforces its own
  `CleartextPolicy` on top, because `NSAllowsLocalNetworking` admits public IP
  addresses too.
- **Our own models.** Gemma 4 E2B for the assistant, not Apple's Foundation
  Models (Q11), and **our own Moonshine for speech to text, not Apple's
  speech recognition** (Q12). So there is no Foundation Models adapter, no
  related entitlement, and **no speech-recognition permission key** — that
  prompt never appears.
- **Background audio only** (Q9, and App Review 2.5.4). Room mode is a
  screen-on kiosk in v1: iOS cannot restart a microphone from the background.
- **No increased-memory-limit entitlement yet.** Spike A0 decides it on
  Kamron's iPhone 14, with numbers.

## Where to look next

- `design-notes/IOS-APP-PLAN-2026-10-20.md` — the plan, and the owner answers
  at the end, which override its recommendations.
- `design-notes/IOS-SESSION-START.md` — Kamron's Mac setup (part A) and how
  this track is run (part B).
- `design-notes/TESTING-PLAYBOOK.md` — the test tiers every batch here works to.
- `licences/README.md` — before adding any package.
