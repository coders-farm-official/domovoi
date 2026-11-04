import XCTest
@testable import DomovoiKit

/// Every word the alerts say, checked **against the Kotlin itself** rather than
/// against a second copy of it — the shape `ShellStringsTests` has, and for the
/// same one failure: a middle dot typed as a bullet, an apostrophe typed
/// curly, a clause dropped off an end. A test comparing two Swift literals
/// could not see any of that.
///
/// A sentence with no Kotlin counterpart is **iOS-only** and is asserted the
/// other way round: it must *not* be in the Kotlin, and its reason is in
/// `design-notes/ios/overnight/alerts/IOS-ONLY-STRINGS.md`.
///
/// This file reads the Kotlin with its own small scanner rather than
/// `ShellStringsTests`', so that twenty branches adding strings to one shared
/// test file do not collide in a train (NIGHT-RULES §2).
final class AlertsStringsTests: XCTestCase {

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so the
    /// repository root is six directories up.
    private static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private static let kotlinFiles = [
        "alerts/FireModels.kt",
        "alerts/HelpDeliveries.kt",
        "alerts/HelpNotifier.kt",
        "alerts/ContainerIncidents.kt",
        // The hint row's own three words.
        "ui/screens/home/HomeSections.kt",
    ]

    /// Each file's literals, in source order, read once.
    private static let sources: [[String]] = {
        let base = repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        return kotlinFiles.map { name in
            let url = base.appendingPathComponent(name)
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
            return literals(in: text)
        }
    }()

    /// Every complete literal the Kotlin writes down. What a **whole** string
    /// is looked up in: a match has to run from a literal's first character to
    /// its last, so a truncation at either end fails.
    private static let wholeLiterals: Set<String> = Set(sources.flatMap { $0 })

    /// Every literal in a file, concatenated in source order. What a
    /// **fragment** is looked up in: the pieces either side of an interpolated
    /// value (`"Reminder · $room"`) are all there is to compare, and the
    /// concatenation is what lets a sentence written over several literals be
    /// found whole.
    private static let haystack: String = sources.map { $0.joined() + "\u{0}" }.joined()

    /// Pull every double-quoted Kotlin literal out of `source`, in order, with
    /// its escapes decoded. Line and block comments are skipped, so KDoc prose
    /// cannot satisfy an assertion; a raw string (`"""…"""`) is read raw.
    private static func literals(in source: String) -> [String] {
        var out: [String] = []
        var current = ""
        var inString = false
        var inRaw = false
        var inLine = false
        var inBlock = false
        var escaped = false
        let chars = Array(source)
        var index = 0
        while index < chars.count {
            let char = chars[index]
            let next = index + 1 < chars.count ? chars[index + 1] : nil
            if inLine {
                if char == "\n" { inLine = false }
                index += 1
                continue
            }
            if inBlock {
                if char == "*" && next == "/" {
                    inBlock = false
                    index += 2
                    continue
                }
                index += 1
                continue
            }
            if inRaw {
                if char == "\"", next == "\"", index + 2 < chars.count, chars[index + 2] == "\"" {
                    out.append(current)
                    current = ""
                    inRaw = false
                    index += 3
                    continue
                }
                current.append(char)
                index += 1
                continue
            }
            if inString {
                if escaped {
                    switch char {
                    case "n": current.append("\n")
                    case "t": current.append("\t")
                    case "r": current.append("\r")
                    case "u":
                        // `\uXXXX`, which is how Kotlin writes a character it
                        // does not want in the source.
                        let hex = String(chars[(index + 1)..<min(index + 5, chars.count)])
                        if let value = UInt32(hex, radix: 16), let scalar = Unicode.Scalar(value) {
                            current.append(Character(scalar))
                            index += 5
                            escaped = false
                            continue
                        }
                    default: current.append(char)
                    }
                    escaped = false
                    index += 1
                    continue
                }
                if char == "\\" {
                    escaped = true
                    index += 1
                    continue
                }
                if char == "\"" {
                    out.append(current)
                    current = ""
                    inString = false
                    index += 1
                    continue
                }
                current.append(char)
                index += 1
                continue
            }
            if char == "/" && next == "/" {
                inLine = true
                index += 2
                continue
            }
            if char == "/" && next == "*" {
                inBlock = true
                index += 2
                continue
            }
            if char == "\"" {
                if next == "\"", index + 2 < chars.count, chars[index + 2] == "\"" {
                    inRaw = true
                    index += 3
                } else {
                    inString = true
                    index += 1
                }
                continue
            }
            index += 1
        }
        return out
    }

    private func assertKotlinSaysWhole(_ ours: String, _ what: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(
            Self.wholeLiterals.contains(ours),
            "\(what): the Kotlin has no literal that is exactly \"\(ours)\"",
            file: file, line: line
        )
    }

    private func assertKotlinSaysPiece(_ ours: String, _ what: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertTrue(
            Self.haystack.contains(ours),
            "\(what): the Kotlin's strings do not contain \"\(ours)\"",
            file: file, line: line
        )
    }

    // MARK: - the scanner, and the guard that it can fail

    func testTheKotlinWasActuallyRead() {
        XCTAssertFalse(Self.wholeLiterals.isEmpty, "no literals: the path to the Kotlin is wrong")
        XCTAssertEqual(
            Self.kotlinFiles.count, Self.sources.filter { !$0.isEmpty }.count,
            "every file in the list has to have been read"
        )
    }

    /// The guard `ShellStringsTests` learnt to keep: a containment test that
    /// cannot fail proves nothing. A hyphen where the middle dot belongs, and
    /// three dots where an ellipsis belongs, must not be found.
    func testTheHaystackCanFail() {
        XCTAssertFalse(Self.haystack.contains("Timer done - "), "a hyphen is not a middle dot")
        XCTAssertFalse(Self.haystack.contains("Timer alerts are off on this telephone"))
        XCTAssertFalse(Self.wholeLiterals.contains("Timer alerts are off"), "and a truncation is not a match")
    }

    // MARK: - the fires

    func testTheTitlesAndTheNounsAreTheKotlins() {
        // `"Reminder · $room"` and `"Timer done · $room"`: the room is
        // interpolated, so the piece before it is what there is to compare.
        assertKotlinSaysPiece(AlertsStrings.reminderTitlePrefix, "reminderTitlePrefix")
        assertKotlinSaysPiece(AlertsStrings.timerTitlePrefix, "timerTitlePrefix")
        assertKotlinSaysWhole(AlertsStrings.noRoom, "noRoom")
        assertKotlinSaysWhole(AlertsStrings.timerNoun, "timerNoun")
        assertKotlinSaysWhole(AlertsStrings.reminderNoun, "reminderNoun")
        // `"${total.roundToLong()}s timer"` and `"${…} min timer"`.
        assertKotlinSaysPiece(AlertsStrings.secondsTimerSuffix, "secondsTimerSuffix")
        assertKotlinSaysPiece(AlertsStrings.minutesTimerSuffix, "minutesTimerSuffix")
    }

    // MARK: - help

    func testTheHelpWordsAreTheKotlins() {
        assertKotlinSaysWhole(AlertsStrings.helpUntitled, "helpUntitled")
        assertKotlinSaysWhole(AlertsStrings.helpBody, "helpBody")
        // `"Help: $t"`.
        assertKotlinSaysPiece(AlertsStrings.helpTitlePrefix, "helpTitlePrefix")
    }

    // MARK: - incidents

    func testTheIncidentWordsAreTheKotlins() {
        assertKotlinSaysWhole(AlertsStrings.incidentTamperTitle, "incidentTamperTitle")
        assertKotlinSaysWhole(AlertsStrings.incidentOutdatedTitle, "incidentOutdatedTitle")
        assertKotlinSaysWhole(AlertsStrings.incidentFailedTitle, "incidentFailedTitle")
        assertKotlinSaysWhole(AlertsStrings.incidentSharedTitle, "incidentSharedTitle")
        assertKotlinSaysWhole(AlertsStrings.incidentBodySeparator, "incidentBodySeparator")
    }

    /// Every one of `kindWords`' eleven answers, which is the kind of list a
    /// port loses one entry of (`ContainerIncidents.kt:315`).
    func testEveryKindWordIsTheKotlins() {
        for kind in [
            "missing", "stopped", "restarting", "unhealthy", "oneshot_failed",
            "daemon", "image", "unknown", "config", "outdated",
        ] {
            assertKotlinSaysWhole(AlertsStrings.incidentKindWords(kind), "kindWords(\(kind))")
        }
        assertKotlinSaysWhole(AlertsStrings.incidentKindWords(nil), "kindWords(null)")
    }

    // MARK: - the hint row

    func testTheRowsThreeWordsAreTheKotlins() {
        assertKotlinSaysWhole(AlertsStrings.notificationsOff, "notificationsOff")
        assertKotlinSaysWhole(AlertsStrings.turnOn, "turnOn")
        assertKotlinSaysWhole(AlertsStrings.notNow, "notNow")
    }

    // MARK: - what is iOS-only, and what is deliberately absent

    /// The four sentences with no Android counterpart. Asserted the other way
    /// round: if one of these turns up in the Kotlin, either Android grew it
    /// and this port should use the Kotlin's wording, or the string was copied
    /// from somewhere it did not belong.
    func testTheIOSOnlySentencesAreNotInTheKotlin() {
        for ours in [
            AlertsStrings.askTitle,
            AlertsStrings.askBody,
            AlertsStrings.askConfirm,
            AlertsStrings.openSettings,
        ] {
            XCTAssertFalse(
                Self.haystack.contains(ours),
                "\"\(ours)\" is recorded as iOS-only, and the Kotlin says it: IOS-ONLY-STRINGS.md is wrong"
            )
        }
    }

    /// The strings this port leaves out exist in the Kotlin — so the omissions
    /// are omissions, and not a map that was reading a file that had moved.
    ///
    /// Each is left out because nothing on an iPhone could make it true:
    /// `MAY_RING_LATE` has no exact-alarm grant to describe, the two confirm
    /// lines are written by code that runs as an alarm rings, and the public
    /// help title has no lock-screen surface (MAP §5.1, DECISIONS §3, §6).
    func testTheStringsThisPortLeavesOutAreRealKotlinStrings() {
        for absent in [
            "Timers may ring a few minutes late on this phone",
            "couldn't reach Domovoi to confirm",
            "going off now",
            "Domovoi sent you help steps",
        ] {
            XCTAssertTrue(
                Self.haystack.contains(absent),
                "\"\(absent)\" is recorded as left out, and the Kotlin does not say it"
            )
        }
    }
}
