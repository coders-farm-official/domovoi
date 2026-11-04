import XCTest
@testable import DomovoiKit

/// Every plain string the rest of Chat says, checked **against the Kotlin
/// itself** rather than against a second Swift literal.
///
/// The standard `ShellStringsTests` sets, for the failure it exists for: an
/// em dash typed as a hyphen, an ellipsis typed as three dots, a middle dot
/// typed as a bullet, a curly quote typed straight, a dropped clause. Two
/// Swift literals compared with each other cannot see any of it, so this
/// reads `android/.../*.kt` off disk at test time, pulls every string literal
/// out of them, and asks whether ours is one of them **end to end**.
///
/// ### Why the scanner is here and not shared
///
/// `ShellStringsTests`'s own parser is `private` to that file, and tonight's
/// rules put it out of reach (it is another item's file). This is the same
/// algorithm, cut down to what this item needs — one more reason the
/// "shared changes" list in
/// `design-notes/ios/overnight/chatmore/DECISIONS.md` asks for it to be
/// lifted into a helper the whole suite can use.
///
/// ### The four strings that are not in the Kotlin
///
/// ``everyStringIsTheKotlins`` checks the 79 plain strings that are ports.
/// The four that are not are named in ``iOSOnly`` with their reason, and they
/// are exactly the list in
/// `design-notes/ios/overnight/chatmore/IOS-ONLY-STRINGS.md`.
final class ChatMoreStringsTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up — the walk
    /// `ShellStringsTests` and `LicencesTests` make.
    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    /// This item's Kotlin, and only it: a string found in some unrelated
    /// screen is not evidence that this screen says it.
    private static let kotlinFiles = [
        "ui/screens/chat/ChatScreen.kt",
        "ui/screens/chat/ChatDocsUi.kt",
        "ui/screens/chat/ChatPhotoPlan.kt",
        "ui/screens/chat/ChatMessageActions.kt",
        "ui/screens/chat/DraftUi.kt",
        "ui/screens/chat/ServerDrafts.kt",
        "ui/screens/chat/SpeakerMarker.kt",
        "ui/screens/chat/VoiceView.kt",
        "ui/screens/chat/VoiceTurn.kt",
        "ui/screens/assistant/LookupUi.kt",
        "ui/screens/assistant/ReplyReportPanes.kt",
        "ui/screens/assistant/AssistantChat.kt",
        "assistant/ReplyReport.kt",
        "assistant/docs/DocHeadsUp.kt",
        "assistant/lookup/LookupRules.kt",
        "assistant/docs/DocExtract.kt",
        "ui/screens/chat/ChatErrors.kt",
    ]

    /// One Kotlin literal, and whether the source joins it to the one before
    /// it with `+` — Kotlin's only way of making two literals one string, and
    /// how it writes a sentence too long for a line.
    private struct Literal {
        let value: String
        let joinsPrevious: Bool
    }

    /// Every **complete** string the Kotlin writes down: each literal joined
    /// to its neighbours exactly as `+` joins them in the source.
    ///
    /// A whole-string lookup has to match from the first character to the
    /// last, so a clause dropped at either end fails — which is the hole the
    /// shell's own suite closed on 2026-10-29 after a review cut a sentence
    /// in half and the suite stayed green.
    private static let wholeStrings: Set<String> = {
        let base = repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        var out: Set<String> = []
        for name in kotlinFiles {
            let url = base.appendingPathComponent(name)
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { continue }
            let pieces = literals(in: text)
            var group = ""
            for (index, piece) in pieces.enumerated() {
                if index > 0 && !piece.joinsPrevious {
                    out.insert(group)
                    group = ""
                }
                group += piece.value
            }
            if !pieces.isEmpty { out.insert(group) }
        }
        return out
    }()

    /// Pull every double-quoted Kotlin literal out of `source`, in order,
    /// escapes decoded, comments skipped, each carrying whether a bare `+`
    /// joins it to the one before.
    private static func literals(in source: String) -> [Literal] {
        var out: [Literal] = []
        let chars = Array(source)
        var i = 0
        var previousEnd: Int?
        func joins(_ start: Int) -> Bool {
            guard let previousEnd, previousEnd <= start else { return false }
            return String(chars[previousEnd..<start]).trimmingCharacters(in: .whitespacesAndNewlines) == "+"
        }
        while i < chars.count {
            let c = chars[i]
            if c == "/", i + 1 < chars.count, chars[i + 1] == "/" {
                while i < chars.count && chars[i] != "\n" { i += 1 }
                continue
            }
            if c == "/", i + 1 < chars.count, chars[i + 1] == "*" {
                i += 2
                while i + 1 < chars.count && !(chars[i] == "*" && chars[i + 1] == "/") { i += 1 }
                i = min(i + 2, chars.count)
                continue
            }
            guard c == "\"" else { i += 1; continue }
            let start = i
            var value = ""
            if i + 2 < chars.count, chars[i + 1] == "\"", chars[i + 2] == "\"" {
                // A raw string is raw: no escapes to decode.
                i += 3
                while i + 2 < chars.count && !(chars[i] == "\"" && chars[i + 1] == "\"" && chars[i + 2] == "\"") {
                    value.append(chars[i])
                    i += 1
                }
                i = min(i + 3, chars.count)
            } else {
                i += 1
                while i < chars.count, chars[i] != "\"" {
                    if chars[i] == "\\", i + 1 < chars.count {
                        switch chars[i + 1] {
                        case "n": value.append("\n")
                        case "t": value.append("\t")
                        default: value.append(chars[i + 1])
                        }
                        i += 2
                        continue
                    }
                    // An unterminated literal is a scanner mistake, not a
                    // string: stop at the line's end rather than swallowing
                    // the rest of the file.
                    if chars[i] == "\n" { break }
                    value.append(chars[i])
                    i += 1
                }
                i += 1
            }
            out.append(Literal(value: value, joinsPrevious: joins(start)))
            previousEnd = i
        }
        return out
    }

    private func assertKotlinSays(
        _ name: String,
        _ ours: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(
            Self.wholeStrings.contains(ours),
            "ChatMoreStrings.\(name) is not a whole string in this item's Kotlin: \u{201C}\(ours)\u{201D}",
            file: file,
            line: line
        )
    }

    /// The scanner found something at all — without this, every assertion
    /// below would pass vacuously the day the walk up to the repository root
    /// changes depth.
    func testTheKotlinWasActuallyRead() {
        XCTAssertGreaterThan(Self.wholeStrings.count, 500)
        XCTAssertTrue(Self.wholeStrings.contains("hold to talk"))
    }

    /// The guard that this check can fail: the same sentences with a hyphen
    /// where an em dash belongs, three dots where an ellipsis belongs, and a
    /// clause cut off the end.
    func testTheCheckCanFail() {
        XCTAssertFalse(Self.wholeStrings.contains("this summary was interrupted - carry on?"))
        XCTAssertFalse(Self.wholeStrings.contains("reading the draft..."))
        XCTAssertFalse(Self.wholeStrings.contains("a newer version came in meanwhile"))
        XCTAssertFalse(Self.wholeStrings.contains("May I look this up online"))
        XCTAssertFalse(Self.wholeStrings.contains("internet on for this"))
        XCTAssertFalse(Self.wholeStrings.contains("don't warn me about this again."))
    }

    func testEveryPlainStringIsTheKotlinsOwn() {
        let ported: [(String, String)] = [
        ("sources", ChatMoreStrings.sources),
        ("sourceReadNotCited", ChatMoreStrings.sourceReadNotCited),
        ("stop", ChatMoreStrings.stop),
        ("jobInterrupted", ChatMoreStrings.jobInterrupted),
        ("mayILookThisUp", ChatMoreStrings.mayILookThisUp),
        ("mayIReadThisPage", ChatMoreStrings.mayIReadThisPage),
        ("allowOnce", ChatMoreStrings.allowOnce),
        ("allowInThisChat", ChatMoreStrings.allowInThisChat),
        ("notNow", ChatMoreStrings.notNow),
        ("verbLookItUp", ChatMoreStrings.verbLookItUp),
        ("internetOff", ChatMoreStrings.internetOff),
        ("internetAlwaysOn", ChatMoreStrings.internetAlwaysOn),
        ("internetOnForThisChat", ChatMoreStrings.internetOnForThisChat),
        ("verbChangeIt", ChatMoreStrings.verbChangeIt),
        ("headsUp", ChatMoreStrings.headsUp),
        ("neverWarn", ChatMoreStrings.neverWarn),
        ("chooseAnother", ChatMoreStrings.chooseAnother),
        ("verbHeadsUp", ChatMoreStrings.verbHeadsUp),
        ("changes", ChatMoreStrings.changes),
        ("edited", ChatMoreStrings.edited),
        ("firstDraft", ChatMoreStrings.firstDraft),
        ("open", ChatMoreStrings.open),
        ("save", ChatMoreStrings.save),
        ("share", ChatMoreStrings.share),
        ("edit", ChatMoreStrings.edit),
        ("cancel", ChatMoreStrings.cancel),
        ("backToTheChat", ChatMoreStrings.backToTheChat),
        ("readAloud", ChatMoreStrings.readAloud),
        ("saveACopyTo", ChatMoreStrings.saveACopyTo),
        ("more", ChatMoreStrings.more),
        ("newerVersionCameIn", ChatMoreStrings.newerVersionCameIn),
        ("readingTheDraft", ChatMoreStrings.readingTheDraft),
        ("anotherDevice", ChatMoreStrings.anotherDevice),
        ("draft", ChatMoreStrings.draft),
        ("exportFallbackTitle", ChatMoreStrings.exportFallbackTitle),
        ("saveWhere", ChatMoreStrings.saveWhere),
        ("verbSave", ChatMoreStrings.verbSave),
        ("verbShare", ChatMoreStrings.verbShare),
        ("voiceNotRecognized", ChatMoreStrings.voiceNotRecognized),
        ("spokenNotRecognized", ChatMoreStrings.spokenNotRecognized),
        ("someoneItKnows", ChatMoreStrings.someoneItKnows),
        ("talk", ChatMoreStrings.talk),
        ("listening", ChatMoreStrings.listening),
        ("releaseToCancel", ChatMoreStrings.releaseToCancel),
        ("thinking", ChatMoreStrings.thinking),
        ("pressToStop", ChatMoreStrings.pressToStop),
        ("holdToTalk", ChatMoreStrings.holdToTalk),
        ("askedYouSomething", ChatMoreStrings.askedYouSomething),
        ("stillAnsweringTheLastOne", ChatMoreStrings.stillAnsweringTheLastOne),
        ("holdTheButtonWhileYouTalk", ChatMoreStrings.holdTheButtonWhileYouTalk),
        ("didntCatchThat", ChatMoreStrings.didntCatchThat),
        ("microphoneNeeded", ChatMoreStrings.microphoneNeeded),
        ("verbVoice", ChatMoreStrings.verbVoice),
        ("voiceBusyFallback", ChatMoreStrings.voiceBusyFallback),
        ("voiceTooLongFallback", ChatMoreStrings.voiceTooLongFallback),
        ("reportThisReply", ChatMoreStrings.reportThisReply),
        ("reportTitle", ChatMoreStrings.reportTitle),
        ("reportYourQuestion", ChatMoreStrings.reportYourQuestion),
        ("reportYourQuestionSpoken", ChatMoreStrings.reportYourQuestionSpoken),
        ("reportTheReply", ChatMoreStrings.reportTheReply),
        ("reportWritten", ChatMoreStrings.reportWritten),
        ("reportApp", ChatMoreStrings.reportApp),
        ("reportYourNote", ChatMoreStrings.reportYourNote),
        ("reportNotePlaceholder", ChatMoreStrings.reportNotePlaceholder),
        ("reportNoQuestion", ChatMoreStrings.reportNoQuestion),
        ("reportEmptyReply", ChatMoreStrings.reportEmptyReply),
        ("reportUnknownModel", ChatMoreStrings.reportUnknownModel),
        ("reportOnTheHomeServer", ChatMoreStrings.reportOnTheHomeServer),
        ("reportHeading", ChatMoreStrings.reportHeading),
        ("reportSubject", ChatMoreStrings.reportSubject),
        ("reportShareLabel", ChatMoreStrings.reportShareLabel),
        ("reportEmailLabel", ChatMoreStrings.reportEmailLabel),
        ("reportSent", ChatMoreStrings.reportSent),
        ("reportNoApp", ChatMoreStrings.reportNoApp),
        ("reportFailed", ChatMoreStrings.reportFailed),
        ("uploadTooLarge", ChatMoreStrings.uploadTooLarge),
        ("verbUpload", ChatMoreStrings.verbUpload),
        ("verbCarryOn", ChatMoreStrings.verbCarryOn),
        ("verbStop", ChatMoreStrings.verbStop),
        // Written over more than one line in both languages, and matched
        // whole: Kotlin joins its pieces with `+` and so does this, and the
        // scanner above puts a `+`-joined run back together before looking.
        // A clause dropped off either end fails here — which is the hole the
        // shell's suite closed in October.
        ("chipHintOff", ChatMoreStrings.chipHintOff),
        ("chipHintOn", ChatMoreStrings.chipHintOn),
        ("reportExplains", ChatMoreStrings.reportExplains),
        ("notAPicture", ChatMoreStrings.notAPicture),
        ("uploadNotAPicture", ChatMoreStrings.uploadNotAPicture),
        ]
        XCTAssertEqual(ported.count, 84, "a string added to the port is checked here too")
        for (name, ours) in ported {
            assertKotlinSays(name, ours)
        }
    }

    /// ``ChatMoreStrings/chatAccepts`` is the one string the Kotlin does not
    /// write down whole: it composes two literals, the documents constant
    /// `DocExtract.ACCEPTS` (`DocExtract.kt:70`) and a full stop. So all
    /// three pieces are checked, and the sentence is checked to be exactly
    /// their sum — a port by parts rather than a sentence this app made up.
    func testWhatTheChatTakesIsTheKotlinsThreePieces() {
        let documents = "PDF, Word (.docx), OpenDocument (.odt), Excel (.xlsx), CSV, text or markdown"
        let prefix = String(ChatMoreStrings.chatAccepts.dropLast(documents.count + 1))
        XCTAssertTrue(
            Self.wholeStrings.contains(prefix),
            "the pictures half is not the Kotlin's: \u{201C}\(prefix)\u{201D}"
        )
        XCTAssertTrue(
            Self.wholeStrings.contains(documents),
            "the documents clause is not DocExtract.ACCEPTS"
        )
        XCTAssertEqual(ChatMoreStrings.chatAccepts, prefix + documents + ".")
    }

    /// The seven wordings this app says that Android does not, each with the
    /// reason there is nothing to port.
    ///
    /// They are asserted **not** to be the Kotlin's, so that one quietly
    /// becoming a port — or a port quietly landing in this list — shows up as
    /// a failure rather than as a line in a notes file nobody re-reads.
    func testTheIOSOnlyWordingsAreNamedAndAreNotPorts() {
        let iOSOnly: [(String, String, String)] = [
            (
                "chipHintAlways", ChatMoreStrings.chipHintAlways,
                "Android's chip is a chip you tap and has no tooltip. A chip that cannot be tapped has to "
                + "say why, and nothing in the Kotlin says it."
            ),
            (
                "chipHintNever", ChatMoreStrings.chipHintNever,
                "The same, for a household that never goes online."
            ),
            (
                "latest", ChatMoreStrings.latest,
                "A fragment of the Kotlin's own pill (latest v<version>, DraftUi.kt), kept apart because "
                + "the pill with no number is a label of its own here."
            ),
            (
                "reportShareExplains", ChatMoreStrings.reportShareExplains,
                "Android's sentence names Android's share menu; on this phone it is the share sheet, and "
                + "telling a person to look for a menu they have not got is the same defect as a control "
                + "that cannot do what it says."
            ),
            (
                "couldNotShareIt", ChatMoreStrings.couldNotShareIt,
                "Android hands a draft to its own share sheet and has no word for the sheet failing; a "
                + "UIActivityViewController that cannot be presented needs one."
            ),
            (
                "theSentenceTheReplyUsed", ChatMoreStrings.theSentenceTheReplyUsed,
                "Android washes the matched sentence inside the passage and names it nowhere "
                + "(ChatDocsUi.kt). A wash is not a thing VoiceOver can read, so the caption says what "
                + "the colour means."
            ),
            (
                "pictureDidntLoad", ChatMoreStrings.pictureDidntLoad,
                "Coil draws Android's thumbnails and its error state is a glyph, not a sentence "
                + "(ChatScreen.kt). A thumbnail that failed has to say so in words here, because the "
                + "three states of one 84-point square are otherwise indistinguishable."
            ),
        ]
        for (name, ours, why) in iOSOnly {
            XCTAssertFalse(
                Self.wholeStrings.contains(ours),
                "ChatMoreStrings.\(name) is in the Kotlin after all, so it is a port and not iOS-only (\(why))"
            )
            XCTAssertFalse(ours.isEmpty, name)
        }
        XCTAssertEqual(iOSOnly.count, 7, "IOS-ONLY-STRINGS.md lists these seven and no others")
    }
}
