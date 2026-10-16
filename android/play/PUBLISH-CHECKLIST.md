# Publishing Domovoi for Android: the owner's checklist

Everything Play asks for is ready in this folder; what is left is clicks
in the Play Console, and the signing steps that only the key's owner does.
Written 2026-10-15 for `wf/play-prep` (versionCode 26101501, versionName
1.0.0-rc1). Play Console menu names move around; search the console for the
quoted name if a path differs.

| File | What it is |
|---|---|
| `listing/en-US/title.txt`, `short_description.txt`, `full_description.txt` | The store text (30 / 80 / 4000 characters, checked) |
| `listing/details.txt` | Category, contact email, website, privacy policy URL, which image goes where |
| `graphics/icon-512.png`, `graphics/feature-graphic-1024x500.png` | Made by `make_graphics.py` from the repo's own icon and font (`--check` verifies) |
| `screenshots/phone/`, `screenshots/tablet-7/`, `screenshots/tablet-10/` | 4 each, from the API 36 emulator |
| `DECLARATIONS.md` | The policy review: blockers and fixes, the foreground-service form, photo/video permissions, App access, the AI-content answer |
| `DATA_SAFETY.md` | The data safety form, answer by answer |
| `CONTENT_RATING.md` | Rating questionnaire, target audience, and the small App content forms |
| `../scripts/play-bundle.ps1` / `.sh` | Builds and signs the `.aab` |
| Demo videos | Kept outside the repo with the maintainers' Play submission material (4 files, 37-75 s; listed in `DECLARATIONS.md` section 6) |

## What can happen tonight, and what waits on Google

| Tonight | Waits |
|---|---|
| Create the app in the console (once the organisation account exists) | The organisation account itself, if not yet approved: D-U-N-S (free, up to ~30 days), identity and payments checks (up to ~5 days) |
| App signing with your own key (PEPK), before the first upload | |
| Build and upload the `.aab` to **internal testing**: testers can install it within minutes; internal releases may skip review, and the store listing and data safety can still be incomplete | Production review: no fixed time; Google says it can take up to 7 days or longer, and new developer accounts get a closer look |
| Fill the listing and every App content form, send production for review | The production listing going live (after review) |
| Push `wf/play-prep` to main, so the privacy policy URL is live | Re-record the room-mode video on a real phone (optional but stronger, below) |
| | Android developer verification (2027): check the registration once Play shows it |

Organisation accounts are not held to the "12 testers for 14 days" closed
test that new personal accounts need (Google's page names personal
accounts only; organisation exemption *(unverified at a primary source)*).
If the console asks for it anyway, run a closed test with the household's
own phones.

## 0. Before the console: the code and the key

1. **Get this branch onto main and push it** (`wf/play-prep` sits on
   `wf/assistant-m3`). The privacy policy URL
   (`https://github.com/coders-farm-official/domovoi/blob/main/docs/PRIVACY_POLICY.md`)
   is a 404 until `docs/PRIVACY_POLICY.md` is on main, and the app links
   the same address (Settings → About → Privacy). Open the URL in a
   browser before submitting the listing.
2. **The release key.** Decision (2026-10-15): one key for the GitHub APKs,
   the household server's APKs and Play. If it doesn't exist yet, make it
   once, outside the checkout, and back it up in two places (losing it
   means no more updates for phones that installed outside Play):
   ```powershell
   keytool -genkeypair -v -keystore $HOME\domovoi-release.jks -alias domovoi -keyalg RSA -keysize 4096 -validity 10000
   ```
   Its certificate's SHA-256 (you'll compare it with Play's later):
   ```powershell
   keytool -list -v -keystore $HOME\domovoi-release.jks -alias domovoi
   ```
3. **Build the bundle** from the pushed, clean checkout:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\play-bundle.ps1
   ```
   It asks for the keystore, alias and passwords (never stored or
   logged), builds `bundleRelease`, signs it with jarsigner, checks it, and
   prints the `.aab` path (`android\app\build\outputs\bundle\release-signed\domovoi-1.0.0-rc1-26101501.aab`),
   its SHA-256 and the certificate's SHA-256. Bash: `bash android/scripts/play-bundle.sh`.

## 1. Create the app

Play Console → **Home → Create app**:
- App name: `Domovoi: self-hosted assistant` (or just `Domovoi`)
- Default language: English (United States)
- App or game: **App**; Free or paid: **Free**
- Tick the Developer Program Policies and US export laws declarations.

The package name is fixed by the first upload: `com.domovoi.app`.

## 2. App signing: use your own key (do this before the first upload)

Play App Signing keeps the key Play signs installs with. Choosing "use my
own key" means Play signs with **the same key** as the GitHub APKs, so a
phone can move between Play and a household-server install with an
ordinary update. Decide it before the first upload: if you upload first
and accept Google's generated key, Play installs and GitHub installs are
signed differently and can never update each other.

1. Console → the app → **Test and release → App integrity → Play app
   signing** (or, on the first "Create new release" page, **Choose signing
   key / Change app signing key**).
2. Choose **Use a different app signing key** → **Export and upload a key
   from Java keystore**.
3. Download the **PEPK tool** (`pepk.jar`) and the **encryption public key**
   the page offers, into a folder outside the checkout.
4. Run the command **exactly as the page shows it** (it carries the right
   flags for the current tool); it looks like:
   ```powershell
   java -jar pepk.jar --keystore=$HOME\domovoi-release.jks --alias=domovoi --output=domovoi-play-signing.zip --include-cert --rsa-aes-encryption --encryption-key-path=encryption_public_key.pem
   ```
   PEPK asks for the keystore and key passwords itself. The `.zip` is the
   private key encrypted for Google; upload it on the same page, then
   delete it.
5. **Upload key**: the page asks for the certificate of the key you will
   sign uploads with. Per the one-key decision that is the same key:
   ```powershell
   keytool -export -rfc -keystore $HOME\domovoi-release.jks -alias domovoi -file upload_certificate.pem
   ```
   Upload `upload_certificate.pem`. (Google recommends a separate upload
   key; it is optional. With one, `play-bundle` would sign with it and its
   check against `android/release.json`'s certificate would need to allow
   it. Not needed tonight.)
6. Afterwards the page shows the **App signing key certificate** SHA-256:
   it must equal the one `keytool -list` printed in step 0.2 and the one
   `play-bundle` printed. If they differ, stop: something else was uploaded.

## 3. Internal testing (tonight)

1. **Test and release → Testing → Internal testing → Testers**: create an
   email list (up to 100 people), add the household's Google accounts, save.
2. **Create new release** → upload `domovoi-1.0.0-rc1-26101501.aab`.
   Release name: `1.0.0-rc1 (26101501)`. Release notes (en-US):
   > First Play release: the on-device assistant (64-bit phones with 8 GB
   > or more), local music, videos and files, room mode, and the companion
   > for your Domovoi server.
3. **Next → Save → Start rollout to Internal testing**.
4. Copy the **opt-in link** from the Testers tab; each tester opens it,
   accepts, and installs from Play. A phone with a debug or GitHub build
   signed by another key must uninstall it first (same key: it updates).

Play's pre-launch report runs automatically on internal releases; read it
the next day (crashes, accessibility, security warnings).

## 4. Store listing

**Grow users → Store presence → Main store listing** (text and graphics
from this folder, see `listing/details.txt`):
- App name, short description, full description: paste the three files.
- App icon: `graphics/icon-512.png`. Feature graphic:
  `graphics/feature-graphic-1024x500.png`.
- Phone screenshots: `screenshots/phone/1-…4-…` in order. 7-inch and
  10-inch tablet screenshots: the `tablet-7` and `tablet-10` folders.

**Store settings**: category **Productivity**; contact email
**info@codersfarm.org** (matches the developer profile); website
`https://github.com/coders-farm-official/domovoi`.

## 5. App content (Policy and programs → App content)

Work down the list; each has its drafted answer:

1. **Privacy policy**: the GitHub URL above (live after step 0.1).
2. **Ads**: no ads.
3. **App access**: "some functionality is restricted", instructions from
   `DECLARATIONS.md` §7.
4. **Content rating**: questionnaire per `CONTENT_RATING.md`.
5. **Target audience**: 18 and over (`CONTENT_RATING.md`).
6. **Data safety**: `DATA_SAFETY.md`.
7. **Government apps, financial features, health, news**: no / none.
8. **Advertising ID**: does not use it.
9. **Foreground service permissions**: three types (microphone,
   mediaPlayback, dataSync), text from `DECLARATIONS.md` §3. Each needs a
   video link: upload the videos to Google Drive shared as "anyone with
   the link" (or unlisted on Google's video site) and paste the links.
10. **Photo and video permissions** (Sensitive app permissions): the
    declaration from `DECLARATIONS.md` §5.

## 6. Production

1. **Test and release → Production → Countries/regions**: add them.
2. **Create new release** → **Add from library** → the internal release's
   bundle (no new upload) → release notes → Save → Review.
3. **Publishing overview → Send changes for review**. Turn on **Managed
   publishing** first if you want to choose the moment it goes live after
   approval.
4. Review: days. Policy emails go to the developer account's email; the
   likeliest questions are the email-draft report (`DECLARATIONS.md`
   finding 2: switch to the HTTPS sink if asked), the photo/video
   permissions (finding 3), and the foreground-service videos.

## 7. Better room-mode video (optional, recommended)

The emulator's room had no server to answer. On a real spare phone paired
with the Beelink, record with Android's own screen recorder (quick
settings → Screen record, no audio needed), about 30-60 s: open **Domovoi
Room** → read the explainer → Start room mode → allow the microphone →
scan the dashboard's room code → Become this room → (approve on the
dashboard) → say the wake word and "what time is it" → the answer → pull
down the notification shade to show "Domovoi Room · …" with Stop
listening and the green mic dot. Upload it the same way; replace the microphone
and mediaPlayback links in the declaration.

## 8. GitHub release (the household server's APKs)

On the branch that carries `android/scripts/release.ps1`
(`wf/app-from-server`, under review), once merged: from a clean, pushed
checkout,
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\release.ps1
```
It builds the arm64-v8a and armeabi-v7a APKs, signs them with **the same
key**, checks them as a Domovoi server will, commits and pushes
`android/release.json` (`[skip ci]`), and publishes the GitHub release
`v<versionName>` with `gh`, tagged at that commit (a version with a suffix,
`1.0.0-rc2`, is a pre-release; a plain one is Latest);
`scripts/release/finish-release.ps1` then adds the Windows Setup, the
checksums and the notes. `-NoPublish` stops before git and GitHub. The
APKs and the Play bundle must share one `versionCode` sequence: a later
build raises `versionCode` (`YYMMDDNN`) in `android/app/build.gradle.kts`
before either script runs; both refuse to go backwards.

## 9. Android developer verification (2027)

From 2027 certified Android phones install only apps whose package and
signing certificate are registered to a verified developer, including
APKs from outside Play. Play registers apps created in the console
automatically; check it once the app exists: **Play Console → Android
developer verification** (or the app's **App integrity** page) should
list `com.domovoi.app` with the release key's certificate SHA-256. With
Play App Signing on your own key that is the same certificate the GitHub
APKs carry; if it is missing, add it there (**Add key**: the console has
you sign a small proof APK with the key). Do this well before 2027.

## Organisation account requirements (for reference)

- A **D-U-N-S number** for Coders Farm (free from Dun & Bradstreet, up to
  ~30 days); the legal name and address in the payments profile must match
  the D&B record.
- A one-time **US$25** registration fee.
- A **website** for the organisation (the GitHub repository will do if
  the form accepts it; a codersfarm page is better).
- Developer **email and phone**, verified by one-time codes; legal name,
  address, developer email and phone are shown on Play.
- Identity / payments-profile verification: up to a few days.
