# App Store Connect answers: App Privacy, export compliance, age rating

Drafted 2026-11-14 for version 1.0.0 (the iPhone's own version; the build
number comes from `ios/scripts/release.sh`). The counterpart of
`android/play/DATA_SAFETY.md` and `android/play/CONTENT_RATING.md`. Apple
rewords its questionnaires, so match each answer by meaning. Each answer says
what in the app makes it true.

## App Privacy (App Store Connect → App Privacy)

**Data collection: "No, we do not collect data from this app."** The label
then reads **Data Not Collected**.

Why (plan 4.3). Apple counts data as collected when it leaves the device in a
way that lets the developer or its partners get at it beyond serving the
request in real time. Nothing here does:

- **Everything the app sends goes to the household's own Domovoi server**, on
  its home network, which the household runs and Coders Farm never sees.
  That is the person's own server, not ours.
- **The microphone** is used for the talk button, for room mode (a spare
  iPhone set up as a room, listening only while the app is on screen) and for
  drop-ins (the intercom). The audio goes only to the household's own server,
  never to Coders Farm or anyone else, and is not kept by the app. So "Data
  Not Collected" still holds with room mode.
- **Chat** is answered only by the household's own server. This version has
  no on-device chat (owner answer Q11: Apple's model is not offered) and
  sends nothing to an AI company.
- **Problem reports and reply reports** go only if the person sends them, by
  their own email app, to info@codersfarm.org, after seeing the whole report.
  The app never receives or sends it itself (it opens a `mailto:` draft), and
  it meets Apple's "optional disclosure" criteria anyway: not used for
  tracking or advertising, infrequent, optional, and sent only when the
  person chooses to, with their name and address shown as theirs.
- **No analytics, advertising, tracking or crash-reporting SDK**, and no
  third-party package at all today (`ios/licences/README.md`).
- `ios/Domovoi/PrivacyInfo.xcprivacy` says the same: tracking false, no
  tracking domains, no collected data types.

**Tracking: No.** No data is linked with other companies' data or shared with
data brokers; no IDFA, so no App Tracking Transparency prompt.

**Change this the same day** the app sends anything to a service of ours or a
third party (a Wikipedia lookup, an HTTPS report destination, any service of
ours): declare it conservatively (search history or user content, not linked,
app functionality), update the privacy manifest in the same batch and the
privacy policy the same day.

**Privacy policy URL:**
https://github.com/coders-farm-official/domovoi/blob/main/docs/PRIVACY_POLICY.md
(it covers the iPhone app as well as Android).

## Export compliance

**Answer: the app uses encryption, and it is exempt.** The build already
says so: `ITSAppUsesNonExemptEncryption` is `false` in `ios/project.yml`, so
App Store Connect does not ask per build. No documentation upload, no French
declaration.

- If asked "Does your app use encryption?": **Yes** (HTTPS).
- "Does it qualify for any of the exemptions?": **Yes**: it uses only the
  encryption built into Apple's operating system (HTTPS/TLS through
  `URLSession`) and authentication, none of it proprietary or non-standard.

What was checked (2026-11-14, grep of `ios/Domovoi` and `ios/Packages`):

- **Networking:** `URLSession` (HTTP, HTTPS and WebSocket). TLS is Apple's;
  the app sets no custom TLS (no `NWProtocolTLS`, no `sec_protocol_options`,
  no `SecTrust` override).
- **CryptoKit:** `SHA256` (a fingerprint of the server's identity key, a
  cache key in `ManualStore`/`AppContainer`) and `Curve25519.Signing` public-key
  **verification** of signed server answers (`Ed25519Verify.swift`). Hashing
  and signature checks are authentication, not encryption of content.
- **Security:** the Keychain (`TokenVault.swift`) holds the household token
  with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`; the OS encrypts it.
- **No custom crypto:** no CommonCrypto, no `AES`/`ChaChaPoly`/`SymmetricKey`,
  no OpenSSL, libsodium or any crypto package. (The one "AES" in the code is
  a comment about Android.)

Re-check if a package is added: an ONNX Runtime or LiteRT-LM build brings no
encryption of its own, but read its notes.

## Age rating (App Store Connect → App Information → Age Rating)

Apple's questionnaire (updated 2025: ages 4+, 9+, 13+, 16+, 18+).

| Question (by meaning) | Answer | Why |
|---|---|---|
| Violence (cartoon, realistic, prolonged, graphic) | None | The app's own content is UI text. |
| Sexual content or nudity | None | Same. |
| Profanity or crude humour | None | Same. |
| Alcohol, tobacco or drugs | None | Same. |
| Mature or suggestive themes | None | |
| Horror or fear themes | None | |
| Medical or treatment information | None | The Health page is the server's own health (disks, updates), not medical. |
| Simulated gambling / gambling / contests | None / No / No | |
| Loot boxes, in-app purchases | No | Free, no purchases (the nonprofit fee waiver needs that to stay so). |
| **Unrestricted web access** | **No** | The app is not a browser. Links (the dashboard) open in Safari. |
| **User-generated content** | **No** | Nothing is published; files and chats stay on the phone or the household's own server. |
| **Messaging and chat** | **Yes** | Household members can talk to each other's rooms through drop-in (the intercom). Only inside the household's own server; no strangers. |
| Advertising | No | No ad SDK. |
| Parental controls / age assurance | No | |
| **AI-generated content, an assistant or chatbot** (if asked) | **Yes** | Chat answers with a language model running on the household's own server. Mitigations: the model's safety tuning, a fixed system prompt, and reporting from each reply's menu. |

**Expected rating: 13+** (plan 4.3), because of open-ended AI chat and
messaging. If Apple's form gives a lower rating, accept it; don't raise it by
hand. "Made for Kids": **No**.
