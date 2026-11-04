import Foundation
import XCTest
@testable import DomovoiKit

/// Every string in ``AiSettingsStrings``, and the eight kind words, are the
/// Kotlin's — checked against `android/.../*.kt` read off disk at test time,
/// the way `ShellStringsTests` and `LicencesTests` do it.
///
/// ### Whole literals, not containment
///
/// Each of ours has to **equal** a complete Kotlin string literal, not merely
/// appear inside one. That is 2026-10-29's finding F-4 on this track: a
/// containment check cannot see a clause dropped off an end, so cutting the
/// second sentence off a `sub` left the suite green. The Kotlin's `+`
/// continuations are closed up first (``kotlin(_:)``), so a sentence the
/// source writes as three literals on three lines is compared as the one
/// string a person reads.
///
/// A literal here is any double-quoted run in the file, **comments
/// included** — the KDoc of `ChatSettingsPanel.kt` quotes a sentence of its
/// own. That costs nothing under equality: a comment would have to quote one
/// of these strings exactly, and if it did, the string would still be in the
/// Kotlin.
///
/// `ShellStringsTests` owns the shared version of this machinery and its
/// twenty-odd Kotlin files; it is not extended here, because a shared test
/// file edited by twenty overnight branches collides in a train
/// (NIGHT-RULES §2). The reader below is this item's four files only.
final class AiSettingsStringsTests: XCTestCase {

    // MARK: - reading the Kotlin

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up — the same walk
    /// `ShellStringsTests.repositoryRoot` makes.
    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private static func source(_ name: String) -> String {
        let url = repositoryRoot
            .appendingPathComponent("android/app/src/main/java/com/domovoi/app")
            .appendingPathComponent(name)
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return "" }
        return text
    }

    /// The panel, the signpost, the store and the kinds — the four files this
    /// item was ported from. The three whose *strings* are compared are kept
    /// as literal sets, read and joined once each: the reader walks a file
    /// character by character, and `SettingsScreen.kt` is 25 KB.
    private static let chatPanel = literals(of: source("ui/screens/settings/ChatSettingsPanel.kt"))
    private static let settingsScreen = literals(of: source("ui/screens/settings/SettingsScreen.kt"))
    private static let headsUpStoreText = source("assistant/docs/HeadsUpStore.kt")
    private static let headsUpStore = literals(of: headsUpStoreText)
    /// Kept as text, not literals: the eight keys are asserted as the
    /// Kotlin's two `listOf(...)` calls, which is source and not a string.
    private static let docHeadsUpText = source("assistant/docs/DocHeadsUp.kt")

    /// Every complete literal of one file, with the `+` continuations closed
    /// up so a string split across lines reads as one.
    private static func literals(of source: String) -> Set<String> {
        var text = ""
        // `" +` whitespace `"` is the Kotlin's own join and nothing else can
        // be: the pattern needs a quote on both sides, so `"x" + name` — a
        // literal joined to a *value* — is left alone and stays two pieces.
        var index = source.startIndex
        while index < source.endIndex {
            let rest = source[index...]
            if rest.first == "\"",
               let plus = rest.dropFirst().firstIndex(where: { !$0.isWhitespace }),
               rest[plus] == "+",
               let open = rest[rest.index(after: plus)...].firstIndex(where: { !$0.isWhitespace }),
               rest[open] == "\"" {
                index = rest.index(after: open)
                continue
            }
            text.append(source[index])
            index = source.index(after: index)
        }
        return Set(unjoinedLiterals(in: text))
    }

    /// The quoted runs of a source whose `+` joins are already closed up.
    /// Escapes are decoded far enough for the strings this item ports: none
    /// of them contains one, and a `\"` inside a literal must not end it.
    private static func unjoinedLiterals(in source: String) -> [String] {
        var out: [String] = []
        var current: String?
        var escaped = false
        for character in source {
            guard current != nil else {
                if character == "\"" { current = "" }
                continue
            }
            if escaped {
                switch character {
                case "n": current?.append("\n")
                case "t": current?.append("\t")
                default: current?.append(character)
                }
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "\"" {
                out.append(current ?? "")
                current = nil
            } else {
                current?.append(character)
            }
        }
        return out
    }

    private func assertKotlinSaysWhole(
        _ string: String,
        in whole: Set<String>,
        _ note: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        if whole.contains(string) { return }
        // Say which of the two failures it is, because they want different
        // fixes: a wrong character means ours is wrong; a string the Kotlin
        // only *contains* means a clause has been dropped off one end.
        let contained = whole.contains { $0.contains(string) }
            ? " — the Kotlin CONTAINS this but does not say exactly this: a clause has been dropped off an end"
            : ""
        XCTFail(
            "not a whole string in the Kotlin: \(string)\(contained)"
                + (note.isEmpty ? "" : " — \(note)"),
            file: file,
            line: line
        )
    }

    // MARK: - the reader itself

    /// The reader is the thing every assertion below leans on, so it is
    /// checked against the four files before it is trusted: they are on disk,
    /// they are the right files, and the join actually joins.
    func testTheKotlinIsOnDiskAndTheJoinWorks() {
        XCTAssertFalse(Self.chatPanel.isEmpty, "ChatSettingsPanel.kt was not read")
        XCTAssertFalse(Self.settingsScreen.isEmpty, "SettingsScreen.kt was not read")
        XCTAssertFalse(Self.headsUpStore.isEmpty, "HeadsUpStore.kt was not read")
        XCTAssertFalse(Self.docHeadsUpText.isEmpty, "DocHeadsUp.kt was not read")

        // A split string is one literal after the join, and the two halves
        // are no longer literals of their own — which is what makes the
        // whole-string assertions honest rather than accidental.
        let joined = Self.chatPanel
        XCTAssertTrue(joined.contains(AiSettingsStrings.headsUpSub))
        XCTAssertFalse(
            joined.contains("Domovoi says first when a task is one it is measured to be weak at "),
            "the half-literal survived the join, so a dropped clause would pass"
        )
        // And a literal joined to a *value* is left as it is: the panel's own
        // `Text(if (prefs.off) "off" else "on", …)` keeps two one-word
        // literals, which the switch's assertions need.
        XCTAssertTrue(joined.contains("off"))
        XCTAssertTrue(joined.contains("on"))
    }

    // MARK: - Chat & AI → Chat

    func testTheChatPanelSaysWhatTheKotlinSays() {
        for string in [
            AiSettingsStrings.headsUpTitle,
            AiSettingsStrings.headsUpSub,
            AiSettingsStrings.switchOn,
            AiSettingsStrings.switchOff,
            AiSettingsStrings.offNote,
            AiSettingsStrings.notWarnedHeading,
            AiSettingsStrings.warnAgain,
            AiSettingsStrings.manualLink,
        ] {
            assertKotlinSaysWhole(string, in: Self.chatPanel)
        }
    }

    /// The eight kind words, as pairs — so a word matched against the wrong
    /// key would fail even though both halves are in the file. The Kotlin
    /// writes each pair `"key" to "word"` on one line
    /// (`HeadsUpStore.kt:52-57`).
    func testEveryKindWordIsTheKotlinsAndIsPairedWithItsOwnKey() {
        XCTAssertEqual(8, AiSettingsHeadsUpKinds.words.count)
        for (key, word) in AiSettingsHeadsUpKinds.words {
            assertKotlinSaysWhole(word, in: Self.headsUpStore, "the word for \(key)")
            XCTAssertTrue(
                Self.headsUpStoreText.contains("\"\(key)\" to \"\(word)\""),
                "KIND_WORDS does not pair \(key) with \(word)"
            )
        }
        // And the fallback, which is Android's `?: kind`
        // (`ChatSettingsPanel.kt:69`): a kind this build has no word for is
        // listed by its key rather than dropped.
        XCTAssertEqual("scanned pages", AiSettingsHeadsUpKinds.word(for: "scanned"))
        XCTAssertEqual("sparkly", AiSettingsHeadsUpKinds.word(for: "sparkly"))
        XCTAssertEqual("", AiSettingsHeadsUpKinds.word(for: ""))
    }

    /// The eight keys, in the Kotlin's own two lists and in its order
    /// (`DocHeadsUp.kt:42-43`). Asserted as the source text, so a key added
    /// to or reordered in either list fails here — the set a stored `skip` is
    /// filtered against has to be exactly the server's.
    func testTheEightKindsAreTheKotlinsTwoLists() {
        XCTAssertEqual(8, AiSettingsHeadsUpKinds.kinds.count)
        XCTAssertEqual(Set(AiSettingsHeadsUpKinds.kinds), Set(AiSettingsHeadsUpKinds.words.keys))
        let documents = AiSettingsHeadsUpKinds.kinds.prefix(6)
        let pictures = AiSettingsHeadsUpKinds.kinds.suffix(2)
        XCTAssertTrue(
            Self.docHeadsUpText.contains(Self.listOf(documents)),
            "DOC_KINDS is not \(Array(documents))"
        )
        XCTAssertTrue(
            Self.docHeadsUpText.contains(Self.listOf(pictures)),
            "PICTURE_KINDS is not \(Array(pictures))"
        )
    }

    private static func listOf(_ keys: some Sequence<String>) -> String {
        "listOf(" + keys.map { "\"\($0)\"" }.joined(separator: ", ") + ")"
    }

    // MARK: - the dashboard signpost

    func testTheSignpostSaysWhatTheKotlinSays() {
        for string in [
            AiSettingsStrings.dashboardTitle,
            AiSettingsStrings.dashboardSub,
            AiSettingsStrings.openOnTheDashboard,
            AiSettingsStrings.noServer,
        ] {
            assertKotlinSaysWhole(string, in: Self.settingsScreen)
        }
    }

    /// The paragraph, with the Kotlin's own interpolation put back where the
    /// section's label goes: `${section.label}` is what the source writes
    /// (`SettingsScreen.kt:414`), so rendering ours with that as the label
    /// reproduces the Kotlin literal exactly — a whole-string match on a
    /// string that has a value in the middle of it.
    func testTheParagraphIsTheKotlinsWithTheSectionWhereTheKotlinPutsIt() {
        assertKotlinSaysWhole(
            AiSettingsStrings.dashboardParagraph(section: "${section.label}"),
            in: Self.settingsScreen
        )
        // And what it reads as for this item's two sections, which is what
        // the two cards draw.
        XCTAssertTrue(
            AiSettingsStrings.dashboardParagraph(section: "Models").hasPrefix("Models is changed on"),
            AiSettingsStrings.dashboardParagraph(section: "Models")
        )
        XCTAssertTrue(
            AiSettingsStrings.dashboardParagraph(section: "Internet").hasPrefix("Internet is changed on")
        )
    }

    /// The sentence this port does **not** say, and the reason it does not:
    /// Android toasts `no browser found to open the dashboard` when
    /// `startActivity` finds nothing (`SettingsScreen.kt:425`), a state an
    /// iPhone with Safari and an `http` address cannot be in (`DECISIONS.md`
    /// §3). Asserted both ways — the Kotlin says it, and nothing of ours
    /// does — so that if somebody ports it later, this case is where the
    /// decision is written down.
    func testTheToastAndroidNeedsIsNotSaidHere() {
        let toast = "no browser found to open the dashboard"
        XCTAssertTrue(Self.settingsScreen.contains(toast), "the Kotlin's own wording moved")
        for string in [
            AiSettingsStrings.dashboardTitle,
            AiSettingsStrings.dashboardSub,
            AiSettingsStrings.openOnTheDashboard,
            AiSettingsStrings.noServer,
            AiSettingsStrings.dashboardParagraph(section: "Models"),
        ] {
            XCTAssertFalse(string.contains("browser found"), string)
        }
    }
}
