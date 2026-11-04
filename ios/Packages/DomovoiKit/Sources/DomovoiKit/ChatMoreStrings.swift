import Foundation

/// Every word the rest of Chat says — pictures, documents, drafts, jobs,
/// lookups, heads-up cards, voice and report-a-message — transcribed from the
/// Kotlin with the file and line each came from, the shape ``ChatStrings``
/// and ``FileStrings`` have.
///
/// Anything that had to change is marked **iOS-only** and recorded in
/// `design-notes/ios/overnight/chatmore/IOS-ONLY-STRINGS.md` with its reason.
///
/// Words that are **not** here, because the control they belong to is not
/// drawn (`design-notes/ios/overnight/chatmore/DECISIONS.md` says why for
/// each): the paperclip's menu and its cap toasts, the Revise box, "send to
/// home", print, the letters screens, "ask the home server", the phone-chat
/// export, and the talk screen's device-action lines.
public enum ChatMoreStrings {

    // MARK: - documents on a row (`ChatDocsUi.kt`)

    /// `ChatDocsUi.kt:159` — the marker an attachment leaves, which is
    /// "attached " and then the document's summary line.
    public static func attached(_ summary: String) -> String { "attached \(summary)" }

    /// `ChatDocsUi.kt:173` — the label above a reply's source chips.
    public static let sources = "sources"

    /// `ChatDocsUi.kt:188`, a chip's own two screen-reader sentences.
    public static func sourceLabel(_ label: String) -> String { "source \(label)" }
    public static let sourceReadNotCited = ", read but not cited"

    /// `ChatDocsUi.kt:186` — what a tap on a chip does.
    public static func openSource(_ label: String) -> String { "open \(label)" }

    /// The page sheet's caption over the sentence the reply actually used.
    ///
    /// **iOS-only.** Android marks the `match` inside the passage with a
    /// background wash and nothing else (`SourceSheet`, `ChatDocsUi.kt:196`).
    /// A wash inside an `AttributedString` is invisible to VoiceOver — the
    /// element's label is the whole passage either way — so a reader who
    /// cannot see the colour would have no way at all to tell which sentence
    /// was cited. The wash is still drawn; this is the line above it, and it
    /// is drawn only when the match was actually found in the passage
    /// (``ChatMoreSourceRules/matchRange(in:match:)``).
    public static let theSentenceTheReplyUsed = "the sentence the reply used"

    /// A thumbnail's own label.
    ///
    /// **iOS-only.** Android draws a message's pictures as bare `AsyncImage`s
    /// with no `contentDescription` (`ChatScreen.kt:1395-1408`), which is an
    /// image a screen reader cannot name. NIGHT-RULES §3 requires a label on
    /// every icon-only control, and the upload's own file name is the only
    /// thing about the picture this app knows.
    public static func pictureNamed(_ name: String) -> String {
        name.isEmpty ? "a picture" : "picture \(name)"
    }

    /// Said in a thumbnail's place when the fetch failed.
    ///
    /// **iOS-only**, and for the same reason: Android's `AsyncImage` leaves an
    /// empty box, and an empty box is a leaf an audit has to measure and a
    /// person cannot read.
    public static let pictureDidntLoad = "this picture didn't load"

    /// The job bar's spoken value: how much of the document has been read.
    ///
    /// **iOS-only.** Android's bar is a `LinearProgressIndicator` inside a
    /// `liveRegion` whose *text* is the server's line (`DocJobCard`,
    /// `ChatDocsUi.kt:330`), and a SwiftUI `ProgressView(value:total:)` reads
    /// out as a bare percentage unless it is given one — "4 of 11 parts" is
    /// the same fact the bar draws.
    public static func partsRead(read: Int, parts: Int) -> String {
        "\(read) of \(parts) part\(parts == 1 ? "" : "s") read"
    }

    // MARK: - long-document jobs (`ChatDocsUi.kt:301`)

    /// `ChatDocsUi.kt:345`. The server writes the job's own progress line, so
    /// this is the only job sentence the app words itself.
    public static func carryOn(_ left: Int) -> String {
        "carry on (\(left) part\(left == 1 ? "" : "s") left)"
    }

    /// `ChatDocsUi.kt:356` — the button under a live job.
    public static let stop = "stop"

    /// The line an interrupted job falls back to when the server sent none
    /// (`ChatScreen.kt:1026`). The server's view normally writes exactly this
    /// sentence itself (`chat_doc_jobs.py:275`).
    public static let jobInterrupted = "this summary was interrupted — carry on?"

    // MARK: - the lookup permission card (`LookupUi.kt:98`)

    /// `LookupUi.kt:109-110` — the card's title, by kind.
    public static let mayILookThisUp = "May I look this up online?"
    public static let mayIReadThisPage = "May I read this page?"

    /// `LookupUi.kt:114` — the one sentence that says where the words go.
    public static func goesTo(_ destination: String) -> String { "goes to \(destination)" }

    /// `LookupUi.kt:117-119` — the three answers.
    public static let allowOnce = "Allow once"
    public static let allowInThisChat = "Allow in this chat"
    public static let notNow = "Not now"

    /// `LookupUi.kt:87` — what an answered card says in place of its buttons.
    public static func cardState(_ state: String) -> String {
        switch state {
        case "allowed_once": return "allowed once"
        case "allowed_chat": return "allowed in this chat"
        case "declined": return "not looked up"
        case "asked_again": return "asked again below"
        default: return state
        }
    }

    /// `ChatScreen.kt:916` — the verb a failed decision's refusal is worded
    /// with.
    public static let verbLookItUp = "look it up"

    // MARK: - the per-thread internet chip (`ChatScreen.kt:1290`)

    public static let internetOff = "internet off"
    public static let internetAlwaysOn = "internet always on"
    public static let internetOnForThisChat = "internet on for this chat"

    /// `ChatScreen.kt:961` — the verb its write is worded with.
    public static let verbChangeIt = "change it"

    /// The chip's screen-reader hints. **iOS-only**: Android's three hints
    /// (`LookupUi.kt:60-62`) are the *phone* chat's and say "Phone chats look
    /// things up…", and its house chip (`ChatScreen.kt:1265`) carries no hint
    /// at all — a chip with no description is a control VoiceOver reads as a
    /// bare label. The wording is Android's with the subject corrected.
    public static let chipHintOff =
        "Domovoi asks before it looks anything up online. Tap to allow it in this chat."
    public static let chipHintOn =
        "This chat looks things up without asking. Tap to have it ask first again."
    public static let chipHintAlways = "This house looks things up without asking (Settings → Models)."
    public static let chipHintNever = "This house never goes online."

    // MARK: - heads-up cards (`ChatDocsUi.kt:366`)

    public static let headsUp = "heads-up"
    public static let neverWarn = "don't warn me about this again"

    /// The option that re-opens the picture picker, which this app does not
    /// draw (``ChatMoreHeadsUp/offerable``).
    public static let chooseAnother = "choose_another"

    /// `ChatDocsUi.kt:368`, with `HeadsUpStore.KIND_WORDS` (`:52`).
    /// **iOS-only in one word**: "on this phone" is Android's, and the pick
    /// is kept per install here too, so it stands.
    public static func silenced(_ kind: String) -> String {
        "no more heads-ups about \(kindWords[kind] ?? "this") on this phone"
    }

    /// `assistant/docs/HeadsUpStore.kt:52`.
    public static let kindWords: [String: String] = [
        "rewrite": "rewriting a whole document",
        "compare": "comparing parts of a long document",
        "calc": "exact sums, averages and dates",
        "long": "long summaries that take a while",
        "scanned": "scanned pages",
        "language": "languages not tested yet",
        "blurry": "blurry photos",
        "handwriting": "handwriting",
    ]

    /// `ChatScreen.kt:838` — the verb a failed pick is worded with.
    public static let verbHeadsUp = "heads-up"

    // MARK: - drafts (`DraftUi.kt`)

    /// `DraftUi.kt:152-154`.
    public static let changes = "changes"
    public static let edited = "edited by you"
    public static let firstDraft = "first draft"

    /// `DraftUi.kt:205` — a draft's kind, in words.
    public static func kindLabel(_ kind: String) -> String {
        switch kind {
        case "resume": return "resume"
        case "cover_letter": return "cover letter"
        default: return "letter"
        }
    }

    /// `DraftUi.kt:241` — the card's own screen-reader sentence.
    public static func draftCard(title: String, version: Int) -> String {
        "draft: \(title), version \(version)"
    }

    /// `DraftUi.kt:258-262` and `:421` — the card's and the canvas's buttons.
    public static let open = "open"
    public static let save = "save"
    public static let share = "share"
    public static let edit = "edit"
    public static let cancel = "cancel"
    public static let backToTheChat = "back to the chat"
    public static let readAloud = "read aloud"
    public static let saveACopyTo = "save a copy to…"
    public static let more = "more"
    public static let latest = "latest"

    /// `DraftUi.kt:166` — "latest v3" beside a card's own older version.
    public static func latestVersion(_ version: Int) -> String { "latest v\(version)" }

    public static func version(_ version: Int) -> String { "v\(version)" }

    /// `DraftUi.kt:450` / `:456` — the save buttons' labels.
    public static func saveAs(_ format: String) -> String { "save \(formatLabel(format))" }
    public static func formatLabel(_ format: String) -> String { format == "pdf" ? "PDF" : ".docx" }

    /// `DraftUi.kt:491` — the edit's own button, and what a save says.
    public static func saveAsVersion(_ version: Int) -> String { "save as v\(version)" }
    public static func savedAsVersion(_ version: Int) -> String { "saved as v\(version) · \(edited)" }

    /// `DraftUi.kt:484` — the 409 this item exists to not swallow.
    public static let newerVersionCameIn = "a newer version came in meanwhile; your text is still here"

    /// `DraftUi.kt:323` — the canvas while it reads.
    public static let readingTheDraft = "reading the draft…"

    /// `DraftUi.kt:330`. **iOS-only**: Android appends the exception's class
    /// name when it has no message; `failureText(_:_:)` already words every
    /// ``DomovoiError``, so the reason is passed through instead.
    public static func couldNotReadTheDraft(_ why: String) -> String {
        "couldn't read the draft: \(why)"
    }

    /// `DraftUi.kt:157` — which version this is, in words.
    public static func editedOn(_ device: String) -> String { "edited on \(device)" }
    public static func revisionLabel(instruction: String?, basedOn: Int?) -> String {
        let said = instruction?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let quoted = "\u{201C}\(said.isEmpty ? "revised" : said)\u{201D}"
        guard let basedOn else { return quoted }
        return "\(quoted) · based on v\(basedOn)"
    }

    /// `ServerDrafts.kt:75` — a hand edit made on a device the server has no
    /// name for.
    public static let anotherDevice = "another device"

    /// `DraftUi.kt:378` — the canvas's own title before the draft has loaded.
    public static let draft = "draft"

    /// `ServerDrafts.kt:116` — the name an exported file goes by when the
    /// draft has no title.
    public static let exportFallbackTitle = "Draft"

    /// `ServerDrafts.kt:48` — where a save lands, in words.
    public static let saveWhere = "your home server's Documents → From Chat"

    /// `ServerDrafts.kt:62` and `:105` — the save lines the canvas shows.
    public static func savedHere(_ relPath: String) -> String {
        "home server · Documents/\(relPath)"
    }

    public static func savedEntry(relPath: String, check: String) -> String {
        "home server · \(relPath) · \(check)"
    }

    /// `ServerDrafts.kt:153` — the card's save toast.
    public static func savedToast(where place: String, check: String) -> String {
        "saved \(place) · \(check)"
    }

    /// `DraftUi.kt:457` / `ServerDrafts.kt:154` — the verbs.
    public static let verbSave = "save"
    public static let verbShare = "share"

    /// `DraftUi.kt:383`, which is said when nothing on the phone can share a
    /// file. **iOS-only**: the share sheet is `UIActivityViewController` and
    /// always opens, so this is only said when the export itself failed.
    public static let couldNotShareIt = "couldn't share it"

    // MARK: - the speaker marker (`SpeakerMarker.kt:47`)

    public static let voiceNotRecognized = "voice not recognized"
    public static let spokenNotRecognized = "spoken; Domovoi didn't recognize the voice"
    public static let someoneItKnows = "someone it knows"

    public static func maybe(_ who: String) -> String { "maybe \(who)" }
    public static func spokenMaybeBy(_ who: String) -> String {
        "spoken; Domovoi thinks it was \(who), but isn't sure"
    }
    public static func spokenBy(_ who: String) -> String { "spoken by \(who)" }
    public static func spokenByNotOwner(_ who: String) -> String {
        "spoken by \(who), not this phone's owner"
    }

    // MARK: - the talk screen (`VoiceView.kt:195`)

    /// `ChatScreen.kt:1161` — the mic button's label.
    public static let talk = "talk"

    public static let listening = "listening — release to send, slide away to cancel"
    public static let releaseToCancel = "release to cancel"
    public static let thinking = "thinking…"
    public static let pressToStop = "press to stop"
    public static let holdToTalk = "hold to talk"
    public static let askedYouSomething = "Domovoi asked you something — hold to answer"

    /// `VoiceView.kt:258` — a press while the last turn is still being
    /// answered.
    public static let stillAnsweringTheLastOne = "still answering the last one"

    /// `VoiceView.kt:293` — a tap, not speech.
    public static let holdTheButtonWhileYouTalk = "hold the button while you talk"

    /// `VoiceView.kt:371` — the turn went out and the core heard nothing.
    public static let didntCatchThat = "didn't catch that — hold the button while you talk"

    /// `VoiceView.kt:283`. **iOS-only**: Android appends the `IOException`'s
    /// message; here it is the `AVAudioSession`/`AVAudioEngine` error's.
    public static func micDidntOpen(_ why: String) -> String {
        "the microphone didn't open: \(why)"
    }

    /// `ChatScreen.kt:481`.
    public static let microphoneNeeded = "the microphone permission is needed to talk to the domovoi"

    /// `ChatScreen.kt:383` — the verb a failed turn is worded with.
    public static let verbVoice = "voice"

    /// `VoiceTurn.kt:109-111` — the fallbacks, used **only** when the server
    /// sent no `detail` of its own (``ChatMoreVoiceRefusal``).
    public static let voiceBusyFallback = "Domovoi is still answering the last one."
    public static let voiceTooLongFallback = "That was too long — a voice message is at most 30 seconds."
    public static func voiceRefusedFallback(_ status: Int) -> String {
        "Domovoi couldn't take that (\(status))."
    }

    // MARK: - report a message (`ReplyReportPanes.kt:69`)

    /// `AssistantChat.kt:660` — the menu item.
    public static let reportThisReply = "report this reply"

    /// `ReplyReportPanes.kt:79` — the pane's title.
    public static let reportTitle = "Report this reply"

    /// `ReplyReportPanes.kt:81`.
    public static let reportExplains =
        "If this answer is offensive, harmful or wrong, report it. The report holds exactly what is below "
        + "and nothing else; nothing leaves the phone by itself."

    /// `ReplyReportPanes.kt:87-92` — the fields, in the order the sheet shows
    /// them.
    public static let reportYourQuestion = "your question"
    public static let reportYourQuestionSpoken = "your question (spoken)"
    public static let reportTheReply = "the reply"
    public static let reportWritten = "written"
    public static let reportApp = "app"
    public static let reportYourNote = "your note"

    /// `ReplyReportPanes.kt:48` — the note field's placeholder.
    public static let reportNotePlaceholder = "what is wrong with it? (optional)"

    /// `ReplyReportPanes.kt:58`.
    public static let reportNoQuestion = "(none: the reply was the first message)"

    /// `ReplyReport.kt:66` / `:84`.
    public static let reportEmptyReply = "(empty reply)"

    /// `ReplyReport.kt:103`.
    public static let reportUnknownModel = "unknown"

    /// `ReplyReport.kt:102` — a house reply's model line always ends with it.
    public static let reportOnTheHomeServer = "on the home server"

    /// `ReplyReport.kt:74` — the report's first line, house branch.
    public static let reportHeading = "Domovoi on the home server: a reported reply"

    /// `ShareAsTextSink.kt` companion (`ReplyReport.kt:352`).
    public static let reportSubject = "Domovoi: a reported reply"

    /// `ReplyReport.kt:341` and `:345`, `:369` and `:371` — the two sinks
    /// this app offers and the sentence each one says before it is tapped.
    public static let reportShareLabel = "share as text"
    /// **iOS-only in two words**: Android's sentence names "Android's share
    /// menu" (`ReplyReport.kt:341`), which on this phone is the share sheet.
    /// A sentence that tells a person to look for a menu they have not got is
    /// the same defect as a control that cannot do what it says.
    public static let reportShareExplains =
        "Share as text opens the share sheet with the report as plain text, so you can send it with an "
        + "email or messaging app to whoever you choose. Nothing leaves the phone until you do."
    public static let reportEmailLabel = "email draft to coders farm"
    public static func reportEmailExplains(_ address: String) -> String {
        "Email draft opens your email app with the report addressed to Coders Farm, who make Domovoi "
        + "(\(address)). It is sent only when you press send there."
    }

    /// `ChatScreen.kt:1352-1355` — what the person is told afterwards.
    public static let reportSent = "report sent to coders farm"
    public static let reportNoApp = "no app on this phone can take the report"
    public static let reportFailed = "couldn't send the report; try again"

    // MARK: - pictures (`ChatPhotoPlan.kt`, `ChatErrors.kt`)

    /// `ChatErrors.kt:59`, word for word — including the clause about HEIC
    /// and AVIF being turned into a JPEG first, which is as true of this
    /// phone as of Android's (``ChatMorePicture/plan(kind:width:height:byteCount:)``).
    ///
    /// The Kotlin composes it: two literals, then
    /// `DocExtract.ACCEPTS` (`DocExtract.kt:70`) and a full stop. The
    /// documents clause here is that constant's own words, and
    /// `ChatMoreStringsTests` checks all three pieces separately for that
    /// reason.
    ///
    /// Nothing in this app draws it yet — there is no paperclip
    /// (DECISIONS.md §2) — and it is ported anyway because the refusal it
    /// belongs to (`ChatMorePicture/uploadRefusalText`) is live.
    public static let chatAccepts =
        "Chat takes text, pictures and documents, up to 4 of each a message. Pictures: PNG, JPEG, GIF, WebP "
        + "or BMP, and phone photos in HEIC or AVIF, which this phone turns into a JPEG first. Documents: "
        + "PDF, Word (.docx), OpenDocument (.odt), Excel (.xlsx), CSV, text or markdown."

    /// `ChatErrors.kt:65`.
    public static func visionMissing(_ model: String?) -> String {
        let name = model?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if name.isEmpty {
            return "Pictures need a vision model, and none is set on this Domovoi. "
                + "An admin can pick one in Settings → Models."
        }
        return "Pictures need the vision model \(name), which isn't installed on this Domovoi. "
            + "An admin can install it in Settings → Models, or pick another vision model there."
    }

    /// `ChatPhotoPlan.kt:189-191`.
    public static let notAPicture =
        "That file isn't a picture this phone can open, so it can't be sent. "
        + "The chat takes JPEG, PNG, GIF, WebP and BMP pictures, and converts other phone photos."

    /// `ChatPhotoPlan.kt:193`.
    public static func pictureDamaged(_ label: String) -> String {
        "This \(label) picture couldn't be opened on this phone; it may be damaged."
    }

    /// `ChatPhotoPlan.kt:196-197`.
    public static func photoDamaged(_ label: String) -> String {
        "This \(label) photo couldn't be opened on this phone; it may be damaged. "
        + "Save it as a JPEG and send it again."
    }

    /// `ChatPhotoPlan.kt:199-200`. On iOS this is JPEG XL and TIFF only:
    /// `ImageIO` decodes HEIC, HEIF and AVIF on every supported version, so
    /// Android's "that needs Android 9 or newer" branch
    /// (`ChatPhotoPlan.kt:185-188`) has no iOS subject and is not ported.
    public static func cannotConvert(_ label: String) -> String {
        "This photo is in \(label) format, which this phone can't convert. "
        + "Save it as a JPEG and send it again."
    }

    /// `ChatPhotoPlan.kt:243-244` — the words for an older server's bare
    /// 415/413, when it sent no coded refusal of its own.
    public static let uploadNotAPicture =
        "That file isn't a picture the chat can read. Send a JPEG, PNG, GIF, WebP or BMP."
    public static let uploadTooLarge = "That picture is too large for the chat."

    /// `ChatPhotoPlan.kt:218` — the verb an upload failure is worded with.
    public static let verbUpload = "upload"

    // MARK: - what is said when a read fails

    /// `ChatScreen.kt:1017` — the verb a failed carry-on is worded with.
    public static let verbCarryOn = "carry on"

    /// `ChatScreen.kt:1040` — and a failed stop.
    public static let verbStop = "stop"
}
