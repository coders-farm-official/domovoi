# Data safety form: drafted answers

Play Console → Policy and programs → App content → **Data safety**. Each
answer below says which code makes it true. Drafted 2026-10-15 for
`wf/play-prep` (versionCode 26101501). Re-check it whenever a feature
starts sending something new; `PRIVACY_POLICY.md` must say the same.

Google's definitions this rests on
([Data safety help](https://support.google.com/googleplay/android-developer/answer/10787469)):
*collected* means transmitted off the device **by your app** to you or a
third party; data processed only on the device is not collected; data the
user chooses to send through another app is that app's transfer.

## The one judgement call: the household's own server

Almost everything the app sends goes to **the user's own Domovoi server**,
at an address the user typed or confirmed, on their home network. Coders
Farm never receives it and has no access to that server; it is not a
third party either (the user runs it). Google's help has no explicit rule
for self-hosted servers *(unverified)*. Its wording cuts both ways:
collection applies "irrespective of whether data is transmitted to you or
a third-party server", but its closest example, a user sending files
directly to their own external drive or cloud storage account, does not
need declaring. The answers below take that example: **not collected and
not shared**, the way a self-hosted file or media client does, with the
privacy policy spelling out what goes there. If Kamron prefers to be
maximally cautious, the alternative is "Option B" at the end.

The email draft is the same kind of call: handing data to another app on
the phone is a transfer the user starts and expects, which Google exempts
from "sharing"; the declaration below is the cautious reading.

## Section 1: Data collection and security

| Question | Answer | Why (code) |
|---|---|---|
| Does your app collect or share any of the required user data types? | **Yes** (only the optional reply report; see below) | The report is the only thing that can reach Coders Farm: `assistant/ReplyReport.kt` `REPORT_DESTINATION` = `Email("info@codersfarm.org")`, an email draft the user sends from their own email app. Declared to be safe, though the app itself transmits nothing (`EmailDraftSink` only opens `ACTION_SENDTO mailto:`). |
| Is all of the user data collected by your app encrypted in transit? | **Yes** | The report travels by the user's email provider (TLS between providers); the HTTPS option (`ReportDestination.Https`) is https-only (`net/ReportUpload.kt`, `ReportSinks.valid`). Model and voice downloads are https-only and pinned (`assistant/ModelCatalog.kt`). |
| Do you provide a way for users to request that their data be deleted? | **Yes** | Email info@codersfarm.org to delete a report we received (privacy policy, "Reports"). Everything on the phone has in-app delete (conversations, reports, models) and goes with an uninstall. |
| Does the app allow users to create an account? | **No** | There is no account with Coders Farm. Pairing with the household server is not an account with us. |

If Kamron decides the email draft is the user's own transfer and declares
**No** data collected, the form ends here with "No data collected"; keep
the report described in the privacy policy either way. Recommended: declare
it (below). Declaring an optional, user-initiated item costs nothing and
can't be found inaccurate.

## Section 2: Data types (only what is declared)

| Data type | Collected | Shared | Optional? | Processed ephemerally? | Purposes | Code |
|---|---|---|---|---|---|---|
| **App activity → Other user-generated content** (the reported question, reply and note) | Yes | No | **Optional** (the user chooses to report and to send) | No | **App functionality**; Fraud prevention, security and compliance (handling reports of offensive output) | `ReplyReport.asText()`, `EmailDraftSink` |
| **Personal info → Email address** (the sender's address on the email the user sends) | Yes | No | **Optional** | No | **Developer communications** (replying about the report) | Only present because the report arrives as an email |
| **App info and performance → Other app performance data** (model name, CPU/GPU, app version inside the report) | Yes | No | **Optional** | No | **App functionality** | `ReplyReport.modelLine()`, `appVersion` |

Not declared, and why:

| Data | Why it is not collected |
|---|---|
| Voice or sound recordings (assistant) | Turned into text **on the phone**, never stored, never sent (`assistant/VoiceSession.kt`, `MicCapture.kt`; `NoNetworkTest` pins that the assistant's code names no network class). |
| Assistant questions and answers | Stay on the phone (`assistant/ConversationStore.kt`, app-private, outside backups: `res/xml/data_extraction_rules.xml`, `AlertsManifestTest`). |
| Voice recordings in room mode, the chat mic, drop-in | Sent only to the user's own Domovoi server (see "the judgement call"). |
| Photos, videos, music, files | Read on the phone in local mode (`data/LocalMedia.kt`, `ui/screens/files/PhoneFileSource.kt`); uploaded only to the user's own server when the user picks "upload". |
| Camera | QR frames decoded on the phone and dropped (`roommode/RoomScanner.kt`). A photo taken for a phone chat's question (N3, `ui/screens/chat/PhonePhotoButtons.kt`) is held in memory, read by the model on the phone, and wiped after the reply; it is never written to storage, the gallery or another app, and never sent (`PhonePicturesControllerTest` checks nothing of it reaches disk). |
| Photos picked for a phone chat | Read through Android's photo picker (no permission), shrunk and read on the phone the same way, never stored or sent; the chat keeps only a "photo" mark (`StoredMessage.photo`). Google's definition counts data sent off the device: none. *(Owner: re-check the form's "Photos" row at v1; conservative reading is "not collected", processed on device only.)* |
| Device or other IDs | The app's own random device id goes only to the user's server (`data/Prefs.kt`). No advertising ID, no Android ID use for tracking. |
| Location | No location permission is requested (`AndroidManifest.xml`); the Wi-Fi network name is read only where Android allows without it. |
| Crash logs, diagnostics | Kept on the phone (`diagnostics/ProblemStore.kt`); the user may copy one. No crash-reporting SDK (`OpenSourceLicencesTest` fails the build if one appears). |
| Model and voice downloads | The request goes to Hugging Face with no user data; the files come down. Google counts data your app *sends*: none. |

## Security practices (shown on the listing)

- Data is encrypted in transit: **Yes** (as above).
- You can request that data be deleted: **Yes**.
- Committed to Play Families Policy: **No** (not a children's app; target
  audience 18+, see CONTENT_RATING.md).
- Independent security review: **No** (leave unchecked).

## When the report destination changes

`REPORT_DESTINATION` is one line. If it becomes `ReportDestination.Https(url)`:
the app itself then transmits the report, so it is definitely
**collected**: keep "Other user-generated content" and "Other app
performance data" (optional, app functionality), **drop "Email address"**
(the JSON carries none: `ReplyReportTest.theJsonHoldsExactlyTheReport`),
and update the privacy policy's "Reports" section. If it becomes `None`,
the form can say "No data collected".

## Option B (maximally cautious, not recommended)

Treat the household server as the developer's: then Audio (voice or sound
recordings), Messages (chat), Photos and videos (uploads), Files and docs,
App activity (app interactions: what is played), Device or other IDs would
each be "collected, optional, app functionality". The listing would then
read as if Coders Farm receives those, which is false and would mislead
users; that is why Option A is the recommendation.
