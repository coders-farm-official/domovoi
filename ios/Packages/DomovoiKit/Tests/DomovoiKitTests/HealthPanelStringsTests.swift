import Foundation
import XCTest
@testable import DomovoiKit

/// Every word Settings → Health says, checked **against the Kotlin itself**
/// rather than against a second copy of it — `ShellStringsTests`' method, for
/// `ui/screens/containers/ContainersScreen.kt` and
/// `alerts/ContainerIncidents.kt`.
///
/// The one failure this file is for: a middle dot typed as a bullet, an
/// apostrophe typed curly, the U+2192 arrow typed as `->`, a leading or
/// trailing space dropped from a fragment that is concatenated with a count.
/// A test comparing two Swift literals could see none of that.
///
/// **It carries its own copy of the reader** (~40 lines) rather than using
/// `ShellStringsTests`'. That file's `literals(in:)`, `wholeStrings` and
/// `haystack` are `private static` members of that class, and widening them
/// would mean editing a test file that twenty parallel branches also touch on
/// the night this was written — which is a merge conflict in the one file
/// every branch needs. DECISIONS asks for the reader to be hoisted into a
/// helper of its own; until it is, this copy is marked as a copy.
final class HealthPanelStringsTests: XCTestCase {

    // MARK: - reading the Kotlin (a copy of ShellStringsTests' reader)

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up.
    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private static let kotlinFiles = [
        "ui/screens/containers/ContainersScreen.kt",
        "alerts/ContainerIncidents.kt",
    ]

    /// One Kotlin string literal, and whether the source joins it to the one
    /// before it with `+` — Kotlin's only way of making two literals one
    /// string, which is how a sentence too long for one line is written.
    private struct Literal {
        let value: String
        let joinsPrevious: Bool
    }

    private static let sources: [[Literal]] = {
        let base = repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        return kotlinFiles.map { name in
            let url = base.appendingPathComponent(name)
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
            return literals(in: text)
        }
    }()

    /// Every **complete** string the Kotlin writes down: each literal joined
    /// to its neighbours exactly as `+` joins them, and nothing else. A whole
    /// string of ours has to match one of these end to end, so a clause
    /// dropped from either end fails.
    private static let wholeStrings: Set<String> = {
        var out: Set<String> = []
        for file in sources {
            var group = ""
            for (index, piece) in file.enumerated() {
                if index > 0 && !piece.joinsPrevious {
                    out.insert(group)
                    group = ""
                }
                group += piece.value
            }
            if !file.isEmpty { out.insert(group) }
        }
        return out
    }()

    /// Every literal, concatenated per file — for the fragments that honestly
    /// are fragments: the piece either side of an interpolated value, which
    /// the Kotlin never writes down whole.
    private static let haystack: String = {
        sources.map { $0.map(\.value).joined() + "\u{0}" }.joined()
    }()

    private static func literals(in source: String) -> [Literal] {
        var out: [Literal] = []
        let chars = Array(source)
        var i = 0
        var previousEnd: Int?
        func joins(_ start: Int) -> Bool {
            guard let previousEnd, previousEnd <= start else { return false }
            let gap = String(chars[previousEnd..<start]).trimmingCharacters(in: .whitespacesAndNewlines)
            return gap == "+"
        }
        while i < chars.count {
            let c = chars[i]
            if c == "/" && i + 1 < chars.count && chars[i + 1] == "/" {
                while i < chars.count && chars[i] != "\n" { i += 1 }
                continue
            }
            if c == "/" && i + 1 < chars.count && chars[i + 1] == "*" {
                i += 2
                while i + 1 < chars.count && !(chars[i] == "*" && chars[i + 1] == "/") { i += 1 }
                i = min(i + 2, chars.count)
                continue
            }
            guard c == "\"" else { i += 1; continue }
            let start = i
            i += 1
            var value = ""
            while i < chars.count && chars[i] != "\"" {
                if chars[i] == "\\" && i + 1 < chars.count {
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
            out.append(Literal(value: value, joinsPrevious: joins(start)))
            previousEnd = i
        }
        return out
    }

    // MARK: - the two assertions

    /// A whole string of ours is a whole string of the Kotlin's.
    private func assertWhole(
        _ string: String,
        _ note: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        if Self.wholeStrings.contains(string) { return }
        let shortened = Self.haystack.contains(string)
            ? " — the Kotlin CONTAINS this but does not say exactly this: a clause has been dropped off one end"
            : ""
        XCTFail(
            "not a whole string in the Kotlin: \(debugged(string))\(shortened)"
                + (note.isEmpty ? "" : " — \(note)"),
            file: file,
            line: line
        )
    }

    /// Containment, which is the **weak** one — for a fragment that sits
    /// beside an interpolated value, where nothing stronger exists. Every
    /// call site names the value beside it.
    private func assertFragment(
        _ fragment: String,
        _ note: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(
            Self.haystack.contains(fragment),
            "not in the Kotlin: \(debugged(fragment)) — \(note)",
            file: file,
            line: line
        )
    }

    /// Escapes the characters this file is about, so a failure message says
    /// which one is wrong instead of showing two strings that look alike.
    private func debugged(_ string: String) -> String {
        let shown = string.unicodeScalars.map { scalar -> String in
            switch scalar {
            case "\u{00B7}": return "<U+00B7·>"
            case "\u{2022}": return "<U+2022 BULLET>"
            case "\u{2192}": return "<U+2192→>"
            case "\u{2014}": return "<U+2014—>"
            case "\u{2026}": return "<U+2026…>"
            case "\u{2019}": return "<U+2019 CURLY'>"
            default: return String(scalar)
            }
        }.joined()
        return "\"\(shown)\""
    }

    /// The guard every haystack needs: a wrong character must **not** be
    /// found, or the file proves nothing.
    func testTheReaderCanFail() {
        XCTAssertFalse(
            Self.wholeStrings.contains("last 24 hours • resolved and cleared"),
            "a bullet where a middle dot belongs"
        )
        XCTAssertFalse(
            Self.wholeStrings.contains(
                "an admin acknowledges or clears these on the dashboard's Settings -> Health"
            ),
            "an ASCII arrow where U+2192 belongs"
        )
        XCTAssertFalse(
            Self.wholeStrings.contains("everything checked looks fine."),
            "a full stop the Kotlin does not have"
        )
        XCTAssertFalse(
            Self.wholeStrings.contains("Updates, disk space, rooms waiting for approval"),
            "and the dashboard foot cut short — the 2026-10-29 finding this method exists for"
        )
        XCTAssertFalse(Self.sources.contains(where: \.isEmpty), "both Kotlin files were read")
    }

    // MARK: - the panel's own words

    func testTheSummaryAndNoDetailLines() {
        assertWhole(HealthPanelStrings.allFine)
        assertWhole(HealthPanelStrings.oneThing)
        assertFragment(HealthPanelStrings.thingsSuffix, "after `${problems.size}`")
        assertWhole(HealthPanelStrings.oneThingAdmin)
        assertFragment(HealthPanelStrings.thingsAdminSuffix, "after `${view.count}`")
        assertWhole(HealthPanelStrings.nothingNeedsAttention)
        assertWhole(HealthPanelStrings.adminSeesTheHouse)
        assertWhole(HealthPanelStrings.sharedScreenNote, "the middle dot is U+00B7")
        assertWhole(HealthPanelStrings.adminSeesDetails)
        assertWhole(HealthPanelStrings.pairToSee)
    }

    func testTheSectionsAndTheirCounts() {
        assertWhole(HealthPanelStrings.needAttentionTitle)
        assertFragment(HealthPanelStrings.openSuffix, "after `${open.size}`")
        assertWhole(HealthPanelStrings.pausedTitle)
        assertWhole(HealthPanelStrings.pausedSub)
        assertWhole(HealthPanelStrings.pausesRiskyChanges)
        assertWhole(HealthPanelStrings.resolvedLabel, "the middle dot is U+00B7")
        assertWhole(HealthPanelStrings.adminClearsThese, "the arrow is U+2192")
    }

    func testTheDashboardFootAndItsButton() {
        assertWhole(
            HealthPanelStrings.dashboardFoot,
            "two Kotlin literals joined with `+`, so a dropped second half fails here"
        )
        assertWhole(HealthPanelStrings.openTheDashboard)
        assertWhole(HealthPanelStrings.dashboardHash)
        assertWhole(HealthPanelStrings.noBrowser)
    }

    func testTheFixLinkAndTheRelativeTimeSeparator() {
        assertFragment(HealthPanelStrings.openFixPrefix, "before `${c.fixLabel ?: \"it\"}`")
        assertFragment(HealthPanelStrings.atSeparator, "before `${relTime(it)}`")
    }

    // MARK: - the incident row's words

    func testTheMetaLinesPieces() {
        assertWhole(HealthPanelStrings.tamperingWord)
        assertWhole(HealthPanelStrings.failureWord)
        assertFragment(HealthPanelStrings.openedPrefix, "before `${rel(inc.openedAt)}`")
        assertWhole(HealthPanelStrings.acknowledgedWord)
        assertWhole(HealthPanelStrings.clearedWord)
        assertWhole(HealthPanelStrings.resolvedWord)
        assertWhole(HealthPanelStrings.metaSeparator, "`joinToString(\" · \")`, U+00B7")
    }

    func testEveryKindsWords() {
        for words in [
            HealthPanelStrings.kindMissing,
            HealthPanelStrings.kindStopped,
            HealthPanelStrings.kindRestarting,
            HealthPanelStrings.kindUnhealthy,
            HealthPanelStrings.kindOneshotFailed,
            HealthPanelStrings.kindDaemon,
            HealthPanelStrings.kindImage,
            HealthPanelStrings.kindUnknown,
            HealthPanelStrings.kindConfig,
            HealthPanelStrings.kindOutdated,
            HealthPanelStrings.kindNone,
        ] {
            assertWhole(words)
        }
    }

    func testTheFreezeList() {
        for line in HealthFreezeList.lines { assertWhole(line) }
    }

    func testTheDerivedBannersSentences() {
        assertWhole(HealthPanelStrings.bannerOneContainer)
        assertFragment(HealthPanelStrings.bannerManySuffix, "after `${shown.size}`")
        assertWhole(
            HealthPanelStrings.bannerTamperTail,
            "appended to a finished sentence, so the LEADING space is part of it"
        )
    }

    /// The open tier's sentence is `ContainerIncidents.kt:43`'s
    /// `ADMIN_ATTENTION_TEXT`, and it is **reused** from `ShellStrings`
    /// rather than copied here — this asserts the thing it is reused for.
    func testTheAdminSentenceComesFromTheShellAndIsTheKotlins() {
        assertWhole(ShellStrings.adminAttention)
        XCTAssertEqual("Something needs an admin's attention.", ShellStrings.adminAttention)
        XCTAssertNotEqual(
            HealthStrings.adminAttention, ShellStrings.adminAttention,
            "the check's lowercase `an admin's` and the banner's capitalised one are two strings"
        )
    }

    /// Every one of these is a middle dot, not a bullet, and an ASCII
    /// apostrophe, not a curly one — stated once over the whole set, because
    /// the Kotlin reader proves it string by string and this proves nothing
    /// was typed with the wrong family of punctuation anywhere.
    func testNoCurlyApostropheOrBulletAnywhereInThePanelsWords() {
        let every = [
            HealthPanelStrings.allFine, HealthPanelStrings.oneThing, HealthPanelStrings.thingsSuffix,
            HealthPanelStrings.oneThingAdmin, HealthPanelStrings.thingsAdminSuffix,
            HealthPanelStrings.nothingNeedsAttention, HealthPanelStrings.adminSeesTheHouse,
            HealthPanelStrings.sharedScreenNote, HealthPanelStrings.adminSeesDetails,
            HealthPanelStrings.pairToSee, HealthPanelStrings.needAttentionTitle,
            HealthPanelStrings.openSuffix, HealthPanelStrings.pausedTitle,
            HealthPanelStrings.pausedSub, HealthPanelStrings.resolvedLabel,
            HealthPanelStrings.adminClearsThese, HealthPanelStrings.pausesRiskyChanges,
            HealthPanelStrings.dashboardFoot, HealthPanelStrings.openTheDashboard,
            HealthPanelStrings.noBrowser, HealthPanelStrings.atSeparator,
            HealthPanelStrings.metaSeparator, HealthPanelStrings.bannerOneContainer,
            HealthPanelStrings.bannerManySuffix, HealthPanelStrings.bannerTamperTail,
        ] + HealthFreezeList.lines
        for string in every {
            XCTAssertFalse(string.contains("\u{2019}"), "curly apostrophe in \(debugged(string))")
            XCTAssertFalse(string.contains("\u{2022}"), "bullet in \(debugged(string))")
            XCTAssertFalse(string.contains("\u{2026}"), "ellipsis in \(debugged(string))")
            XCTAssertFalse(string.contains("\u{2014}"), "em dash in \(debugged(string))")
        }
    }
}
