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
| Camera | QR frames decoded on the phone and dropped (`roommode/RoomScanner.kt`). |
| Device or other IDs | The app's own random device id goes only to the user's server (`data/Prefs.kt`). No advertising ID, no Android ID use for tracking. |
| Location | Since N1 (2026-10-21) the app may ask for **approximate** location only when a phone chat is asked "where am I", and for **precise** location only when it is asked the Wi-Fi network's name (which Android shows only with it), each with its reason first, always optional. The last fix Android already has is read on the phone and shown as coordinates; it is never looked up online, never read in the background, never sent anywhere (`assistant/AndroidPhoneReader.kt`; `NoNetworkTest` pins that the phone chat's code names no network or server client). Processed on the device only, so not collected. |
| Contacts | Asked for (optional) only to call or text someone by name from a phone chat; the name and number are read on the phone to fill in the dialer or Messages, which the person then uses themselves. Without it the system contact picker hands over only the row picked. Never sent anywhere: not collected. |
| Calendar events | Asked for (optional) only when a phone chat is asked what's on the calendar; read on the phone into that conversation (app-private, outside backups), never sent anywhere: not collected. New events go through the calendar app's own editor (`ACTION_INSERT`), which the person saves themselves. |
| Phone facts in a phone chat (battery, storage, network type, next alarm, Do Not Disturb, now playing, device model, connected headphones, the phone's music/video/book titles) | Read on the phone when asked and put only in the conversation on the phone (`assistant/PhoneQuestions.kt`, `PhoneTools.kt`). Not collected. **Such replies can't be reported** ("report this reply" is not offered on them, and `ReplyReport.draft` refuses them), so none of this, the location, contacts or calendar included, can leave the phone in a report; the model's own prompt facts never include location, contacts or calendar. |
| Crash logs, diagnostics | Kept on the phone (`diagnostics/ProblemStore.kt`); the user may copy one. No crash-reporting SDK (`OpenSourceLicencesTest` fails the build if one appears). |
| Model and voice downloads | The request goes to Hugging Face with no user data; the files come down. Google counts data your app *sends*: none. |

## Lookups in phone chats (W1, 2026-10-21): declare before v1

A phone chat can look things up after the person allows it on the chat's
card (or turns on "Always allow internet", off by default):
`assistant/lookup/LookupClients.kt`. Away from home the app itself sends
the search words to **Wikipedia** (en.wikipedia.org) or to the person's
own search (their SearXNG address, or **Brave Search** with their own
key), and fetches a page the person names. At home it sends them to the
household's own server, which falls under "the judgement call" above.

Declared conservatively (owner: update the Play Console form at v1, and
`PRIVACY_POLICY.md` with it):

| Data type | Collected | Shared | Optional? | Processed ephemerally? | Purposes | Code |
|---|---|---|---|---|---|---|
| **App activity → In-app search history** (the words of a lookup) | **Yes** (the app sends them to Wikipedia, or the person's own search) | **No** (a transfer the user starts and expects) | **Optional** (asked each time unless the person allows more) | **Unclear: declare No** (the receiving service may log it) | **App functionality** | `WikipediaLookup.search`, `OwnSearchLookup.search` |
| **Web browsing → Web browsing history** (the address of a page the person names) | **Yes** | **No** | **Optional** | **Declare No** | **App functionality** | `PageReader.read` |

* Encrypted in transit: Wikipedia, Brave and named pages are **HTTPS
  only** (`PageReader` refuses `http://`; redirects are followed only to
  https). A person's own SearXNG may be plain HTTP on their LAN, and a home
  server on plain HTTP can't be answered "encrypted" for that path either:
  keep the form's encryption answer about what the app sends to third
  parties, and say so in the privacy policy.
* Nothing goes to Coders Farm. No account. The person's own key is kept
  sealed in the Android keystore (`OwnSearchStore.kt`).
* The no-network test (`NoNetworkTest`) allows only this package's
  clients; `PhoneLookupsTest` proves nothing is sent before the allow.

## Owner note for v1 (N1, 2026-10-21)

The manifest now declares `ACCESS_COARSE_LOCATION`, `ACCESS_FINE_LOCATION`,
`READ_CONTACTS` and `READ_CALENDAR`. Under Google's definitions nothing
above changes (all processed on the phone, nothing transmitted), so the
form's answers stay as drafted; the cautious reading is the same. **At
v1, re-open the Data safety form in the Play Console** and check that it
still says what this file says, since Play compares the form with the
permissions in the uploaded bundle. `PRIVACY_POLICY.md` has a section for
these ("What the on-device assistant reads on the phone").

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
