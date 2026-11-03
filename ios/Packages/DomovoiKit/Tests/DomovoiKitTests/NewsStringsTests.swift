import Foundation
import XCTest
@testable import DomovoiKit

/// Every word ``NewsStrings`` says, checked **against the Kotlin itself**
/// rather than against a second copy of it.
///
/// The shape `ShellStringsTests` established, in miniature and with its own
/// reader so that two branches touching one shared test file cannot collide:
/// `NewsScreen.kt` and `NewsDetail.kt` are read off disk at test time, every
/// double-quoted literal is pulled out of them, and ours has to be **one of
/// them end to end**. That is the one failure this file is for — an em dash
/// typed as a hyphen, an ellipsis typed as three dots, a middle dot typed as
/// a bullet, a clause dropped off the end of a sentence. A test comparing two
/// Swift literals could see none of it.
///
/// ### Where it is deliberately coarse
///
/// A Kotlin string with a `${…}` in it cannot be read as one literal by a
/// quote scanner, because the interpolation contains quotes of its own
/// (`"${person.name ?: "?"}'s news"` is three runs to a scanner and one
/// string to Kotlin). Those six are checked as **fragments of the file's own
/// source text**, which is a containment and is said to be one, and the
/// *rendered* sentences are checked for their boundaries in
/// `NewsModelsTests` instead (the plurals, the counts).
final class NewsStringsTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up — the walk
    /// `ShellStringsTests` and `LicencesTests` both make.
    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private static let kotlinFiles = [
        "ui/screens/news/NewsScreen.kt",
        "ui/screens/news/NewsDetail.kt",
        // `failureText`'s own wording, which the verbs below are handed to.
        "net/ApiClient.kt",
        // `webLinkOrNull`, ported as ``newsWebLink(_:)``.
        "ui/components/LinkScheme.kt",
    ]

    private static let source: String = {
        let base = repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        return kotlinFiles
            .compactMap { try? String(contentsOf: base.appendingPathComponent($0), encoding: .utf8) }
            .joined(separator: "\n")
    }()

    /// Every double-quoted literal in those files, escapes decoded, comments
    /// skipped — so KDoc prose cannot satisfy an assertion.
    private static let literals: Set<String> = Set(wholeLiterals(in: source))

    private static func wholeLiterals(in text: String) -> [String] {
        var out: [String] = []
        let chars = Array(text)
        var i = 0
        while i < chars.count {
            if chars[i] == "/", i + 1 < chars.count, chars[i + 1] == "/" {
                while i < chars.count && chars[i] != "\n" { i += 1 }
                continue
            }
            if chars[i] == "/", i + 1 < chars.count, chars[i + 1] == "*" {
                i += 2
                while i + 1 < chars.count && !(chars[i] == "*" && chars[i + 1] == "/") { i += 1 }
                i = min(i + 2, chars.count)
                continue
            }
            guard chars[i] == "\"" else { i += 1; continue }
            i += 1
            var value = ""
            while i < chars.count && chars[i] != "\"" {
                if chars[i] == "\\", i + 1 < chars.count {
                    switch chars[i + 1] {
                    case "n": value.append("\n")
                    case "t": value.append("\t")
                    case "r": value.append("\r")
                    default: value.append(chars[i + 1])
                    }
                    i += 2
                } else {
                    value.append(chars[i])
                    i += 1
                }
            }
            i += 1
            out.append(value)
        }
        return out
    }

    // MARK: - the two helpers every assertion goes through

    private func assertKotlinSays(
        _ string: String,
        _ note: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(
            Self.literals.contains(string),
            "not a whole literal in the Kotlin: \(debugged(string))"
                + (note.isEmpty ? "" : " — \(note)"),
            file: file,
            line: line
        )
    }

    /// For the six interpolated strings only — a containment of the file's
    /// own source text, which is why it is a different helper with a
    /// different name.
    private func assertKotlinSourceHas(
        _ fragment: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(
            Self.source.contains(fragment),
            "the Kotlin source does not contain \(debugged(fragment))",
            file: file,
            line: line
        )
    }

    /// Characters a failure message has to show, since the whole point is
    /// telling an em dash from a hyphen.
    private func debugged(_ string: String) -> String {
        let codes = string.unicodeScalars
            .filter { $0.value > 127 }
            .map { "U+\(String($0.value, radix: 16, uppercase: true))" }
        return codes.isEmpty ? "\"\(string)\"" : "\"\(string)\" [\(codes.joined(separator: " "))]"
    }

    // MARK: - the rig can fail

    /// The guard every one of these files needs: a hyphen where an em dash
    /// belongs, and three dots where an ellipsis belongs, are **not** found.
    /// Without this, a reader cannot tell a passing assertion from a reader
    /// that matches anything.
    func testTheReaderCanFail() {
        XCTAssertFalse(Self.literals.isEmpty, "the Kotlin was not read at all")
        XCTAssertFalse(
            Self.literals.contains("topic added - discovering feeds..."),
            "a hyphen and three dots must not pass as an em dash and an ellipsis"
        )
        XCTAssertFalse(
            Self.literals.contains("No feeds yet - discovery found none; add one below."),
            "the em dash in the free-form empty line"
        )
        XCTAssertFalse(
            Self.literals.contains("Categories give broad coverage"),
            "half a sentence is not a whole literal"
        )
    }

    // MARK: - the page

    func testThePageHeaderSaysWhatAndroidSays() {
        assertKotlinSays(NewsStrings.title)
        assertKotlinSays(NewsStrings.subtitle, "NewsScreen.kt:97 — saved stories, not their saved stories")
        assertKotlinSays(NewsStrings.noPeople)
        assertKotlinSays(NewsStrings.noPeopleSub)
        assertKotlinSays(NewsStrings.pickAPerson)
        assertKotlinSays(NewsStrings.pickAPersonSub)
        assertKotlinSays(NewsStrings.requestFailed)
        assertKotlinSays(NewsStrings.unknownPerson)
    }

    /// The page header and the person's sub-line **differ by one word** in
    /// the Kotlin, and the difference is easy to lose in a transcription.
    func testTheTwoSubtitlesAreNotTheSameSentence() {
        XCTAssertNotEqual(NewsStrings.subtitle, NewsStrings.personSub)
        assertKotlinSays(NewsStrings.personSub, "NewsDetail.kt:126 — their saved stories")
        XCTAssertTrue(NewsStrings.personSub.contains("their saved stories"))
        XCTAssertFalse(NewsStrings.subtitle.contains("their"))
    }

    // MARK: - one person

    func testThePollButtonSaysWhatAndroidSays() {
        assertKotlinSays(NewsStrings.pollNow)
        assertKotlinSays(NewsStrings.polling, "NewsDetail.kt:132 — the ellipsis is U+2026")
        XCTAssertTrue(NewsStrings.polling.unicodeScalars.contains(UnicodeScalar(0x2026)!))
    }

    func testTheHeadingAndTheToastAreTheKotlinsInterpolations() {
        assertKotlinSourceHas("'s news")
        assertKotlinSourceHas("polled — ")
        assertKotlinSourceHas(" new item")
        XCTAssertEqual("Kamron's news", NewsStrings.personsNews("Kamron"))
        XCTAssertTrue(
            NewsStrings.polled(3).unicodeScalars.contains(UnicodeScalar(0x2014)!),
            "polled — N uses an em dash"
        )
    }

    func testEveryFailureVerbIsTheKotlinsVerb() {
        assertKotlinSays(NewsStrings.verbPoll)
        assertKotlinSays(NewsStrings.verbUpdateCategory)
        assertKotlinSays(NewsStrings.verbAddTopic)
        assertKotlinSays(NewsStrings.verbRemoveTopic)
        assertKotlinSays(NewsStrings.verbAddFeed)
        assertKotlinSays(NewsStrings.verbValidate)
        assertKotlinSays(NewsStrings.verbRemoveFeed)
        assertKotlinSays(NewsStrings.verbUpdateFavorite)
    }

    /// The sentence a failed write reads, end to end, on both phones —
    /// `failureText` is shared, so this is the whole toast and not a verb.
    func testAFailedWriteReadsTheSameSentenceAsAndroids() {
        XCTAssertEqual(
            "add feed failed: 409: the internet is off",
            failureText(
                NewsStrings.verbAddFeed,
                .refused(ApiFailure(status: 409, message: "409: the internet is off"))
            )
        )
        XCTAssertEqual(
            "poll failed (offline?)",
            failureText(NewsStrings.verbPoll, .unreachable("nothing answered"))
        )
    }

    // MARK: - the briefing card

    func testTheBriefingCardSaysWhatAndroidSays() {
        assertKotlinSays(NewsStrings.briefingLabel)
        assertKotlinSays(NewsStrings.briefingSub)
        assertKotlinSays(NewsStrings.noBriefing)
        assertKotlinSourceHas("generated ")
    }

    // MARK: - the topic manager

    func testTheTopicCardSaysWhatAndroidSays() {
        assertKotlinSays(NewsStrings.topicsLabel)
        assertKotlinSays(NewsStrings.topicsSub)
        assertKotlinSays(NewsStrings.freeformPlaceholder, "the ellipsis is U+2026")
        assertKotlinSays(NewsStrings.addTopic)
        assertKotlinSays(NewsStrings.topicAdded, "an em dash and an ellipsis")
        assertKotlinSays(NewsStrings.noTopics)
        assertKotlinSays(NewsStrings.topicKindFallback)
        assertKotlinSays(NewsStrings.expand)
        assertKotlinSays(NewsStrings.collapse)
        assertKotlinSays(NewsStrings.removeTopic)
        assertKotlinSays(NewsStrings.remove)
    }

    /// The confirm's body **names the topic**, which is how a dialog that
    /// confirmed the wrong row would be visible (`NewsDetail.kt:291`).
    func testTheRemoveConfirmNamesTheTopicInAsciiQuotes() {
        assertKotlinSourceHas("and its feeds?")
        XCTAssertEqual(
            "Remove \"Formula 1\" and its feeds?",
            NewsStrings.removeTopicBody("Formula 1")
        )
        XCTAssertFalse(
            NewsStrings.removeTopicBody("x").contains("\u{201C}"),
            "the Kotlin's \\\" is an ASCII quote, not a curly one"
        )
    }

    // MARK: - the feeds

    func testTheFeedListSaysWhatAndroidSays() {
        assertKotlinSays(NewsStrings.loadingFeeds, "the ellipsis is U+2026")
        assertKotlinSays(NewsStrings.noFeedsFreeform, "the dash is an em dash")
        assertKotlinSays(NewsStrings.noFeeds)
        assertKotlinSays(NewsStrings.feedURLPlaceholder, "the ellipsis is U+2026")
        assertKotlinSays(NewsStrings.addFeed)
        assertKotlinSays(NewsStrings.feedAdded)
        assertKotlinSays(NewsStrings.feedFallbackTitle)
        assertKotlinSays(NewsStrings.recheckValidity)
        assertKotlinSays(NewsStrings.removeFeed)
        assertKotlinSourceHas(" feed${")
    }

    /// The two empty lines are different sentences, and which one is shown
    /// depends on the topic's kind. One of them ending up as the other is a
    /// sentence about discovery told over a category.
    func testTheTwoEmptyFeedLinesAreDifferentSentences() {
        XCTAssertNotEqual(NewsStrings.noFeeds, NewsStrings.noFeedsFreeform)
        XCTAssertTrue(NewsStrings.noFeedsFreeform.contains("discovery found none"))
        XCTAssertFalse(NewsStrings.noFeeds.contains("discovery"))
    }

    // MARK: - the saved feed

    func testTheSavedFeedSaysWhatAndroidSays() {
        assertKotlinSays(NewsStrings.savedFeedLabel)
        assertKotlinSays(NewsStrings.savedFeedSub)
        assertKotlinSays(NewsStrings.noStories)
        assertKotlinSays(NewsStrings.noStoriesSub)
        assertKotlinSays(NewsStrings.untitledStory)
        assertKotlinSays(NewsStrings.favorite)
        assertKotlinSays(NewsStrings.unfavorite)
    }

    // MARK: - the chip row

    /// `heard <relTime>` is the one fact the expanded rail carried that this
    /// port keeps, so the words are still Android's even though the layout
    /// they came from is not.
    func testTheHeardLineIsTheRailsOwnWording() {
        assertKotlinSourceHas("heard ")
        XCTAssertEqual("heard 2h ago", NewsStrings.heard("2h ago"))
    }

    // MARK: - the iOS-only sentences

    /// The three sentences this port adds — and the assertion that they are
    /// **not** in the Kotlin, so that one of them quietly becoming a
    /// transcription of an Android string would fail here rather than pass
    /// everywhere.
    func testTheIOSOnlySentencesAreNotInTheKotlin() {
        for ours in [NewsStrings.feedsUnread, NewsStrings.verbLoadFeeds] {
            XCTAssertFalse(
                Self.source.contains(ours),
                "\(debugged(ours)) is in the Kotlin after all — it is not iOS-only"
            )
        }
        // `personChip` is "<name>'s news", which the Kotlin does say as the
        // heading; what is iOS-only is using it as a chip's accessibility
        // label, not the words. Asserted as the same sentence on purpose: a
        // reader hearing one thing and seeing another is the bug.
        XCTAssertEqual(NewsStrings.personChip("Chevy"), NewsStrings.personsNews("Chevy"))
    }
}
