# Content rating, target audience and the other App content forms: drafted answers

Play Console → Policy and programs → App content. Drafted 2026-10-15 for
versionCode 26101501, re-checked against the manifest on 2026-10-21
(versionCode 26102001). The rating questionnaire is IARC's; its exact wording
changes, so match each answer by meaning. Each answer says what in the app
makes it true.

## Content rating (IARC questionnaire)

- **Email for the rating certificate:** codersfarm@gmail.com
- **Category:** "All other app types" (not a game, not social or
  communication-first; the closest IARC category for a utility /
  productivity app).

| Question (by meaning) | Answer | Why |
|---|---|---|
| Violence, blood, gore | No | The app's own content is UI text; there is no violent content. |
| Sexuality, nudity | No | Same. |
| Profanity or crude humour | No | Same. |
| Drugs, alcohol, tobacco | No | Same. |
| Gambling, simulated gambling | No | Nothing of the kind. |
| Fear, horror | No | |
| Does the app let users **interact or exchange content** with other users? | **Yes** | Household members can talk to each other through the intercom ("drop in"), and room mode is a shared device. Only within the household's own server; no strangers, no public content. |
| Does the app **share the user's location** with other users? | No | Location is never shared with anyone. The app declares approximate and precise location (`AndroidManifest.xml`), used only when someone asks a phone chat "where am I" or for the Wi-Fi network's name: read on the phone, shown in that chat, never sent (`assistant/AndroidPhoneReader.kt`). |
| **Digital purchases** | No | No in-app purchases or billing library. |
| **Unrestricted internet access** (a web browser, search) | No | The app is not a browser. It plays streams the household server lists and opens links in the user's browser. A phone chat can search Wikipedia (or the person's own search) and read the text of one https page the person names, only after an allow card, as words for an answer: no page is displayed, and no cookies or scripts run. If the questionnaire's own wording makes that a Yes, answer Yes; it changes nothing else here (DATA_SAFETY.md, "Lookups in phone chats"). |
| Does the app contain **generated content from AI** / can it produce content not controlled by the developer? *(if asked; whether IARC asks is unverified)* | **Yes** | Chat writes answers with a language model, on the phone (phone chats) or on the household's server. Mitigations: the model's safety tuning, a fixed system prompt, in-app reporting (DECLARATIONS.md §4). |
| User-generated content shared publicly | No | Nothing is published; chats stay on the phone or the household server. |

**Expected result:** Everyone / PEGI 3 / USK 0 class, with the descriptor
"Users Interact". If the AI question is asked and answered Yes, some
authorities may rate it higher (e.g. Teen / PEGI 12); that is fine for
this app *(unverified how each authority treats it)*.

## Target audience and content

- **Target age groups:** **18 and over** only. Recommended: Domovoi is a
  household tool for adults who run a server; choosing any group under 13
  brings the Families policy (and its rules for AI chat) into play.
- **Could the store listing unintentionally appeal to children?** No (no
  cartoon-character marketing aimed at children; the cat is the brand
  mascot). If Play's reviewer disagrees, they will say so; answer that the
  app is a home-server companion for adults.
- **Teacher Approved / Designed for Families:** don't opt in.

## Other App content forms

| Form | Answer | Why |
|---|---|---|
| Ads | **No, my app does not contain ads** | No ad SDK (`OpenSourceLicencesTest`). |
| App access | "Some functionality is restricted", with the instructions in DECLARATIONS.md §7 | A household server is needed for most features. |
| Data safety | DATA_SAFETY.md | |
| Government app | No | |
| Financial features | None | |
| Health | None of the health features | |
| News app | No | Podcasts and radio are the user's own media, not a news publication. |
| COVID-19 contact tracing / status | No | |
| Privacy policy | https://github.com/coders-farm-official/domovoi/blob/main/docs/PRIVACY_POLICY.md | Live once pushed to main. |
| Foreground service permissions | DECLARATIONS.md §3 | |
| Photo and video permissions | DECLARATIONS.md §5 | |
| Exact alarm permission | Not applicable: the app declares `SCHEDULE_EXACT_ALARM`, not `USE_EXACT_ALARM` | DECLARATIONS.md §2. |
| Advertising ID | **No**, the app does not use an advertising ID | No ads or analytics libraries; nothing reads it. If Play warns that a library declares `com.google.android.gms.permission.AD_ID`, check the merged manifest: none did at versionCode 26101501 or 26102001. |
