import XCTest
@testable import DomovoiKit

/// Every user-facing string of the shell, checked **against the Kotlin
/// itself** rather than against a second copy of it.
///
/// The point of this file is the one failure it is for: an em dash typed as a
/// hyphen, an ellipsis typed as three dots, a middle dot typed as a bullet, a
/// curly quote typed straight, a leading space dropped. A test that compared
/// two Swift literals could not see any of that, so this one reads
/// `android/.../*.kt` off disk at test time — the way `LicencesTests` reads
/// `ios/licences/README.md` — pulls every string literal out of them, and
/// asks whether ours is one of them.
///
/// **"One of them", not "in there", since 2026-10-29.** It used to ask
/// whether ours appeared *somewhere* in the Kotlin's strings, which proves
/// only that ours is a subset: a review cut the second sentence off
/// `ShellStrings.localSettingsSub` and the suite stayed green. A plain string
/// now has to match a Kotlin string end to end
/// (``assertKotlinSaysWhole(_:_:file:line:)``), and the concatenated haystack
/// is kept for the fragments that honestly are fragments — the pieces either
/// side of an interpolated value.
///
/// ### How the haystack is built
///
/// Kotlin writes a long sentence as several adjacent literals, joined with
/// `+`, with `append(…)`, or as separate arguments. So the haystack is **every
/// string literal in the file, decoded and concatenated in source order with
/// nothing between them**. Adjacent literals in the source are therefore
/// adjacent in the haystack, whatever joined them, and a sentence spread over
/// four lines is found whole. Comments are skipped, so KDoc prose cannot
/// satisfy an assertion.
///
/// The cost is that two unrelated neighbouring literals could in principle
/// form a match. `testTheHaystackCanFail` is the guard: it asserts that a
/// hyphen where an em dash belongs, and three dots where an ellipsis belongs,
/// are **not** found.
///
/// ### Where it is deliberately coarse
///
/// A string with an interpolation in it is checked in pieces, split on a
/// sentinel put where the value goes. Pieces shorter than 8 characters are
/// skipped: they are the punctuation around a value and would match anything.
/// A string with a branch in it (`identityChangedText`, `trustBody`,
/// `tokenTroubleHint`) is checked clause by clause instead, because the two
/// branches' literals sit side by side in the Kotlin and a whole rendering of
/// one branch runs into the other.
final class ShellStringsTests: XCTestCase {

    // MARK: - reading the Kotlin

    /// This file is at `ios/Packages/DomovoiKit/Tests/DomovoiKitTests/`, so
    /// the repository root is six directories up. The same walk
    /// `LicencesTests` makes.
    static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private static let kotlinFiles = [
        "ui/shell/AppShell.kt",
        "ui/shell/ServerPicker.kt",
        "ui/shell/IdentityChanged.kt",
        "ui/shell/ShellMode.kt",
        "ui/shell/TokenTroubleScreen.kt",
        "ui/shell/ContainerBanner.kt",
        "ui/shell/RouteNotes.kt",
        "ui/shell/ForgetServer.kt",
        "ui/screens/settings/SettingsScreen.kt",
        "ui/screens/settings/PairingScreen.kt",
        "ui/screens/settings/PhoneBarPanel.kt",
        "ui/screens/settings/LocalSettingsScreen.kt",
        "ui/screens/settings/ConnectionPanel.kt",
        "alerts/ContainerIncidents.kt",
        // Home's header (I1-D part 1). The rest of `ui/screens/home/` comes
        // with part 2's sections.
        "ui/screens/home/HomeSections.kt",
        "ui/screens/home/HomeModels.kt",
        // **`HomeScreen.kt` is here for one literal**: `"domovoi"`, the
        // header's fallback name, whose source is `HomeScreen.kt:390`.
        // Without this file the assertion passed on `AppShell.kt:891` and
        // `ConnectionPanel.kt:498` — two unrelated literals that happen to be
        // the same word — so it read as evidence of a match it had not made.
        // Part 2 adds the rest of this file's strings with its sections.
        "ui/screens/home/HomeScreen.kt",
        // Part 2: the health checks and the two formatters a row draws.
        "ui/screens/home/HealthChecks.kt",
        "ui/components/Format.kt",
    ]

    /// Every literal in every file above, decoded, concatenated per file.
    ///
    /// **Only for the checks that are honestly about a fragment**: a piece of
    /// a string with a value interpolated into the middle of it, where the
    /// edges are all there is to compare. A whole string is checked against
    /// ``wholeStrings`` instead — see ``assertKotlinSaysWhole(_:_:file:line:)``.
    private static let haystack: String = {
        let base = repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        return kotlinFiles.map { name -> String in
            let url = base.appendingPathComponent(name)
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { return "" }
            // A sentinel between files so a fragment cannot be satisfied by
            // the end of one file plus the start of the next.
            return literals(in: text).map(\.value).joined() + "\u{0}"
        }.joined()
    }()

    /// Every **complete** string the Kotlin writes down: each literal joined
    /// to its neighbours exactly as the source joins them with `+`, and
    /// nothing else.
    ///
    /// This is the set a whole string is looked up in, and the difference
    /// from ``haystack`` is the whole point of the 2026-10-29 fix. A
    /// containment test on the haystack proves that ours is a **subset** of
    /// what the Kotlin says: cut the second sentence off
    /// `ShellStrings.localSettingsSub` and the suite stayed green, because the
    /// first sentence is still in there. Every one of those assertions had the
    /// hole — a dropped trailing clause, or a sentence shortened at either
    /// edge, was invisible.
    ///
    /// A whole-string lookup has to match a literal from its first character
    /// to its last, so a truncation at either end fails unless it lands
    /// exactly on a `+` boundary in the Kotlin *and* the remainder is itself a
    /// complete group — which is why the groups are built by concatenation
    /// rather than by letting any sub-run count.
    ///
    /// ``wholeLiterals`` is the looser sibling, for the clauses of a string
    /// whose group also contains an interpolation (the token-trouble hints):
    /// there the Kotlin's own literal IS the clause, and the group is the
    /// rendered sentence with a value in the middle of it.
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

    /// Every individual Kotlin literal, whole.
    private static let wholeLiterals: Set<String> = Set(sources.flatMap { $0 }.map(\.value))

    /// Each file's literals, in source order, read once.
    private static let sources: [[Literal]] = {
        let base = repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        return kotlinFiles.map { name in
            let url = base.appendingPathComponent(name)
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
            return literals(in: text)
        }
    }()

    /// One Kotlin string literal, and whether the source joins it to the
    /// literal before it with `+` — which is how Kotlin writes a sentence too
    /// long for one line, and the only way two literals become one string.
    struct Literal {
        let value: String
        let joinsPrevious: Bool
    }

    /// Pull every double-quoted Kotlin string literal out of `source`, in
    /// order, with its escapes decoded, and with each one carrying whether the
    /// source joins it to the one before it. Line and block comments are
    /// skipped; a `//` inside a literal is not a comment.
    ///
    /// **`joinsPrevious`** is true when everything between the previous
    /// literal's closing quote and this one's opening quote is whitespace and
    /// a single `+`. That is Kotlin's only way of making two literals one
    /// string, and it is what lets a sentence written over four lines be
    /// matched whole. Anything else between them — a comma, a `)`, an
    /// `append(`, an identifier, a `+` with a comment after it — means two
    /// different strings, and they are kept apart.
    ///
    /// Kotlin's raw strings (`"""…"""`) are read as raw: no escape decoding,
    /// which is what raw means. None of the files above uses one for a
    /// user-facing string, so this is for completeness rather than for a case
    /// that exists.
    private static func literals(in source: String) -> [Literal] {
        var out: [Literal] = []
        let chars = Array(source)
        var i = 0
        /// Where the previous literal ended; nil before the first one.
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
            // A raw string?
            if i + 2 < chars.count && chars[i + 1] == "\"" && chars[i + 2] == "\"" {
                i += 3
                var raw = ""
                while i + 2 < chars.count
                    && !(chars[i] == "\"" && chars[i + 1] == "\"" && chars[i + 2] == "\"") {
                    raw.append(chars[i])
                    i += 1
                }
                i = min(i + 3, chars.count)
                out.append(Literal(value: raw, joinsPrevious: joins(start)))
                previousEnd = i
                continue
            }
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

    // MARK: - the helpers every assertion goes through

    /// **A whole string of ours is a whole string of the Kotlin's.** The
    /// helper every plain string goes through: no interpolation, no branch,
    /// nothing to split — so there is no reason to accept a substring, and
    /// every reason not to (see ``wholeStrings``).
    private func assertKotlinSaysWhole(
        _ string: String,
        _ note: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        if Self.wholeStrings.contains(string) { return }
        // Say which of the two failures it is, because they want different
        // fixes: a wrong character means ours is wrong, and a string the
        // Kotlin only contains means ours has been shortened (or the
        // Kotlin's has grown).
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

    /// A whole Kotlin **literal**, for the clause of a string whose Kotlin
    /// group also holds an interpolation: there the literal is the clause, so
    /// this is still an end-to-end match and not a containment.
    private func assertKotlinSaysWholeLiteral(
        _ string: String,
        _ note: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(
            Self.wholeLiterals.contains(string),
            "not a whole literal in the Kotlin: \(debugged(string))\(note.isEmpty ? "" : " — \(note)")",
            file: file,
            line: line
        )
    }

    /// Containment, which is the **weak** one.
    ///
    /// Left for the only subjects it is honest about: a piece of a string with
    /// a value interpolated into it, where the piece genuinely is a fragment
    /// of a Kotlin literal and nothing stronger is available. Every call site
    /// that uses it says which value sits beside the fragment. A plain string
    /// must use ``assertKotlinSaysWhole(_:_:file:line:)``: a containment check
    /// on a plain string cannot see a dropped trailing clause, which is
    /// 2026-10-29's finding F-4.
    private func assertKotlinSays(
        _ fragment: String,
        _ note: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        XCTAssertTrue(
            Self.haystack.contains(fragment),
            "not in the Kotlin: \(debugged(fragment))\(note.isEmpty ? "" : " — \(note)")",
            file: file,
            line: line
        )
    }

    /// Check a rendered string that has values interpolated into it, by
    /// splitting on the sentinel those values were.
    ///
    /// Containment is right here and stays: each piece **is** a fragment of a
    /// Kotlin literal, because the value that follows it is not in the source
    /// at all. What it cannot catch is a clause dropped from an *end* of such
    /// a string, and there is no way for it to: the Kotlin never writes that
    /// string down whole either. The plain strings, which it does write down
    /// whole, go through ``assertKotlinSaysWhole(_:_:file:line:)``.
    private func assertKotlinSaysAround(
        _ rendered: String,
        sentinel: String = "\u{1}",
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let pieces = rendered.components(separatedBy: sentinel).filter { $0.count >= 8 }
        XCTAssertFalse(
            pieces.isEmpty,
            "nothing long enough to check in \(debugged(rendered))",
            file: file,
            line: line
        )
        for piece in pieces {
            assertKotlinSays(piece, file: file, line: line)
        }
    }

    /// Show the characters this file exists to protect, so a failure says
    /// which one is wrong.
    private func debugged(_ text: String) -> String {
        "\"" + text
            .replacingOccurrences(of: "\n", with: "\\n")
            .replacingOccurrences(of: "—", with: "<EMDASH>")
            .replacingOccurrences(of: "→", with: "<ARROW>")
            .replacingOccurrences(of: "…", with: "<ELLIPSIS>")
            .replacingOccurrences(of: "·", with: "<MIDDOT>")
            .replacingOccurrences(of: "“", with: "<LQUOTE>")
            .replacingOccurrences(of: "”", with: "<RQUOTE>")
            .replacingOccurrences(of: "’", with: "<RSQUOTE>") + "\""
    }

    private let sentinel = "\u{1}"

    // MARK: - the test that proves this test can fail

    /// **Every** file in the list must exist and must have contributed
    /// literals.
    ///
    /// A per-file check and not a size threshold on the whole haystack: one
    /// renamed file out of fourteen would barely move a total (the measured
    /// total on 2026-10-29 is 13,787 characters, and the smallest file
    /// contributes a few dozen), so a total would let a file go missing and
    /// quietly stop checking its strings. `literals(in:)` returns `[]` for a
    /// file it could not read, which is exactly the silent case.
    func testEveryKotlinFileWasActuallyRead() {
        let base = Self.repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        for name in Self.kotlinFiles {
            let url = base.appendingPathComponent(name)
            // `try?` and not `try`: a throwing read stops the loop at the
            // first missing file, so a run that renamed three of them would
            // only name one. Every file is reported.
            let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
            XCTAssertFalse(
                Self.literals(in: text).isEmpty,
                "\(name) could not be read, or contributed no string literals at all"
            )
        }
        XCTAssertGreaterThan(Self.haystack.count, 5_000, "the haystack is far smaller than measured")
    }

    /// **The guard on the fix for F-4**: a string shortened at either end is
    /// rejected, where the old containment check accepted it.
    ///
    /// The three subjects are the three shapes the hole had. The first is the
    /// review's own sabotage, so it stays in the suite rather than in a
    /// report: one Kotlin literal with its second sentence cut off. The second
    /// is a `+`-joined sentence with its last clause dropped — the shape a
    /// tidy-up produces. The third is a sentence shortened at the front.
    ///
    /// Each is asserted twice over: the real string passes, the cut one fails.
    /// A test that only checked the cut ones would pass if the lookup were
    /// broken outright.
    func testAShortenedStringIsNotAWholeString() {
        let cases: [(whole: String, cut: String, shape: String)] = [
            (
                ShellStrings.localSettingsSub,
                "This phone's voice and its problem reports.",
                "a single literal with its second sentence cut off (the review's sabotage)"
            ),
            (
                ShellStrings.pairExplanation,
                "This Domovoi asks devices to prove they belong to the household before they can change anything.",
                "a +-joined sentence with its last clause dropped"
            ),
            (
                ShellStrings.settingsSub,
                "in groups. Search to jump straight to a setting.",
                "shortened at the front"
            ),
        ]
        for (whole, cut, shape) in cases {
            XCTAssertTrue(Self.wholeStrings.contains(whole), "the real string: \(debugged(whole))")
            XCTAssertFalse(
                Self.wholeStrings.contains(cut),
                "accepted a shortened string (\(shape)): \(debugged(cut))"
            )
            // And the proof that this is a real tightening: the old check
            // accepted every one of them.
            XCTAssertTrue(
                Self.haystack.contains(cut),
                "this case is not testing anything: containment rejects it too (\(shape))"
            )
        }
    }

    func testTheHaystackCanFail() {
        // An em dash typed as a hyphen.
        XCTAssertFalse(Self.haystack.contains("not on wifi - join your home network"))
        // An ellipsis typed as three dots.
        XCTAssertFalse(Self.haystack.contains("checking..."))
        // A middle dot typed as a bullet.
        XCTAssertFalse(Self.haystack.contains("no server yet • pick one below"))
        // Straight quotes where the Kotlin uses curly ones.
        XCTAssertFalse(Self.haystack.contains("nothing matches \""))
        // A dropped leading space.
        XCTAssertFalse(Self.haystack.contains("|domovoi / local media"))
        // And something that is simply not in the app.
        XCTAssertFalse(Self.haystack.contains("Welcome to Domovoi! Let's get you set up."))
    }

    // MARK: - §7.1 chrome

    func testTheChromeSaysWhatAndroidSays() {
        for s in [
            ShellStrings.localTopbar,
            ShellStrings.connectPill,
            ShellStrings.switchServer,
            ShellStrings.statusAvailable,
            ShellStrings.statusOffline,
            ShellStrings.statusLive,
            ShellStrings.homeCrumb,
            ShellStrings.unverified,
            ShellStrings.drawerWorkspace,
            ShellStrings.drawerSystem,
            ShellStrings.themeLabel,
            ShellStrings.homeLabel,
            ShellStrings.bannerDetails,
            ShellStrings.bannerDetailsLabel,
            ShellStrings.adminAttention,
            ShellStrings.badgeOverflow,
        ] {
            assertKotlinSaysWhole(s)
        }
    }

    /// The two leading spaces are the chrome's spacing, and they are the
    /// easiest thing in this file to lose to a tidy-up.
    func testTheStringsWithTwoLeadingSpacesKeepThem() {
        for s in [
            ShellStrings.localTopbar,
            ShellStrings.statusAvailable,
            ShellStrings.statusOffline,
            ShellStrings.statusLive,
            ShellStrings.defaultBar,
        ] {
            XCTAssertTrue(s.hasPrefix("  "), debugged(s))
            XCTAssertFalse(s.hasPrefix("   "), debugged(s))
        }
    }

    /// **The one deliberate difference in the whole file.** Android's drawer
    /// says `/ android`; ours says `/ iphone`. Asserted both ways so neither
    /// can drift into the other by accident — and flagged in `I1C1-REPORT.md`
    /// as a choice for Kamron rather than a transcription.
    func testThePlatformIsTheOneWordThatDiffers() {
        assertKotlinSaysWhole("/ android", "the Kotlin's word, which ours replaces")
        XCTAssertEqual("/ iphone", ShellStrings.platformCrumb)
        XCTAssertFalse(Self.haystack.contains("/ iphone"))
    }

    // MARK: - §7.2 the picker

    func testThePickerSaysWhatAndroidSays() {
        assertKotlinSaysWhole(ShellStrings.notOnWifi)
        assertKotlinSaysAround(ShellStrings.scanning(done: 137, total: 254).replacingOccurrences(of: "137/254", with: sentinel))
        for s in [
            ShellStrings.noDomovoisFound,
            ShellStrings.domovois,
            ShellStrings.rescan,
            ShellStrings.addManually,
            ShellStrings.manualPlaceholder,
            ShellStrings.add,
            ShellStrings.checking,
            ShellStrings.connected,
            ShellStrings.selected,
            ShellStrings.use,
            ShellStrings.forget,
            ShellStrings.unnamedServer,
        ] {
            assertKotlinSaysWhole(s)
        }
        assertKotlinSaysAround(
            ShellStrings.nothingAnswered(port: 6369).replacingOccurrences(of: "6369", with: sentinel)
        )
        assertKotlinSaysAround(ShellStrings.couldNotReachDashboard(url: sentinel))
    }

    /// `scanning …` ends in the ellipsis **character**, and the port comes
    /// from the same constant on both phones.
    func testThePickersTwoInterpolations() {
        XCTAssertEqual("scanning 137/254…", ShellStrings.scanning(done: 137, total: 254))
        XCTAssertEqual(6369, Discovery.defaultPort)
        XCTAssertTrue(ShellStrings.nothingAnswered().contains("port 6369"))
    }

    // MARK: - §7.3 the connection dialog

    func testTheConnectionDialogSaysWhatAndroidSays() {
        for s in [
            ShellStrings.connectionTitle,
            ShellStrings.thisPhone,
            ShellStrings.thisPhoneSub,
            ShellStrings.inUse,
            ShellStrings.close,
        ] {
            assertKotlinSaysWhole(s)
        }
        // `use $title`: the Kotlin literal is "use " with the title
        // interpolated after it, so this fragment is all there is to compare
        // and containment is the honest check. Four characters, so the
        // splitter would skip it — hence it is asserted by hand.
        assertKotlinSays("use ")
    }

    /// `ServerChoice.reason` and `.title`, which `ShellModeTest` pins on the
    /// Kotlin side too. The middle dot is in two of them.
    func testEveryServerChoiceReasonAndTitleIsTheKotlins() {
        for choice: ServerChoice in [
            .noServer,
            .unreachable(label: "x"),
            .notOurs(label: "x"),
            .coreDown(label: "x"),
            .available(label: "x"),
            .available(label: "x", needsPairing: true),
        ] {
            assertKotlinSaysWhole(choice.reason)
        }
        assertKotlinSaysWhole(ServerChoice.noServer.title)
        XCTAssertTrue(ServerChoice.noServer.reason.contains("·"), "middle dot")
        XCTAssertTrue(
            ServerChoice.available(label: "x", needsPairing: true).reason.contains("·"),
            "middle dot"
        )
    }

    // MARK: - §7.4 the local shell's banners

    func testTheBannersSayWhatAndroidSays() {
        assertKotlinSaysAround(localBanner(for: .unreachable(label: sentinel), canTrustAgain: false)!)
        assertKotlinSaysAround(localBanner(for: .coreDown(label: sentinel), canTrustAgain: false)!)
    }

    // MARK: - §7.5 identity changed

    /// Checked clause by clause: the two branches' literals are neighbours in
    /// `IdentityChanged.kt`, so a whole rendering of one runs into the other.
    ///
    /// Five of the six clauses are whole `append(…)` literals, so they are
    /// matched end to end. The first is the only containment check in the
    /// group, and it has to be: its Kotlin literal is
    /// `"…reinstalled. $address answered, but not with "`, so the clause is a
    /// genuine fragment of it. The assertion below that the rendered text
    /// contains `"…reinstalled. A answered, but not with "` is what pins the
    /// other end of it.
    func testTheIdentityChangedBannerSaysWhatAndroidSays() {
        assertKotlinSays("This looks like a different Domovoi, or it was reinstalled. ")
        assertKotlinSaysWhole("the identity this phone trusted, so this phone sent it nothing and is showing its own media. ")
        assertKotlinSaysWhole("If you reinstalled Domovoi or moved it to a new machine, trust it again. ")
        assertKotlinSaysWhole("If you're away from home, don't.")
        assertKotlinSaysWhole("If you replaced the server, forget it (the X next to it in the server list, or under ")
        assertKotlinSaysWhole("Settings → Phones & browsers → Trusted servers), then trust and pair it again.")
        // And every clause is actually in what we render, so the clauses
        // above cannot drift away from the function.
        let yes = identityChangedText(address: "A", canTrustAgain: true)
        let no = identityChangedText(address: "A", canTrustAgain: false)
        XCTAssertTrue(yes.contains("If you're away from home, don't."))
        XCTAssertTrue(no.contains("Settings → Phones & browsers → Trusted servers), then trust and pair it again."))
        for text in [yes, no] {
            XCTAssertTrue(text.contains("This looks like a different Domovoi, or it was reinstalled. A answered, but not with "))
        }
    }

    func testTheTrustAgainDialogSaysWhatAndroidSays() {
        for s in [trustAgainButton, trustAgainTitle, trustAgainNo, trustAgainYes] {
            assertKotlinSaysWhole(s)
        }
        XCTAssertTrue(trustAgainButton.contains("—"), "em dash")
        assertKotlinSaysAround(trustAgainText(pinned: sentinel, now: sentinel))
        assertKotlinSaysAround(trustingAgainToast(address: sentinel))
        XCTAssertTrue(trustingAgainToast(address: "x").contains("—"), "em dash")
    }

    // MARK: - §7.6 the trust dialog

    func testTheTrustDialogSaysWhatAndroidSays() {
        for s in [ShellStrings.trustTitle, ShellStrings.trustCancel, ShellStrings.trustConfirm] {
            assertKotlinSaysWhole(s)
        }
        // The common part, and each tail on its own: the two tails are
        // neighbours in the Kotlin.
        assertKotlinSaysWhole("Check that this address is your Domovoi before you continue: the app "
            + "will load the screens this server advertises and send it your "
            + "requests. Nothing is saved until you confirm.")
        assertKotlinSaysWhole(" The identity above should match Settings → About & updates on the dashboard; "
            + "trusting pins it, and the household token goes out only after the "
            + "server proves it holds that key.")
        assertKotlinSaysWhole(" This server offers no identity (an older web backend), so nothing "
            + "can be pinned: the app will not be able to tell it from another "
            + "server at the same address.")
        assertKotlinSaysAround("It calls itself \"\(sentinel)\". ")

        // Each tail begins with a leading space, because the parts are
        // concatenated; without it the sentence runs into the previous one.
        let pinned = ShellStrings.trustBody(name: nil, offersIdentity: true)
        let unpinnable = ShellStrings.trustBody(name: nil, offersIdentity: false)
        XCTAssertTrue(pinned.contains("confirm. The identity above"), debugged(pinned))
        XCTAssertTrue(unpinnable.contains("confirm. This server offers no identity"), debugged(unpinnable))
        // The name is in ASCII double quotes, not curly ones.
        let named = ShellStrings.trustBody(name: "kitchen-box", offersIdentity: true)
        XCTAssertTrue(named.hasPrefix("It calls itself \"kitchen-box\". "), debugged(named))
        XCTAssertFalse(named.contains("“"))
        // A blank name is treated as none (Kotlin's `takeIf { it.isNotBlank() }`).
        XCTAssertEqual(pinned, ShellStrings.trustBody(name: "   ", offersIdentity: true))
    }

    // MARK: - §7.7 the token-trouble card (I1-B's strings, never read off the Kotlin until now)

    func testTheTokenTroubleCardSaysWhatAndroidSays() {
        for trouble: TokenTrouble in [.notRecognised(waitMs: 0), .waiting(waitMs: 125_000)] {
            assertKotlinSaysWhole(tokenTroubleTitle(trouble))
        }
        assertKotlinSaysAround(
            tokenTroubleText(.notRecognised(waitMs: 0), address: sentinel)
        )
        assertKotlinSaysAround(
            tokenTroubleText(.waiting(waitMs: 125_000), address: sentinel)
                .replacingOccurrences(of: "2:05", with: sentinel)
        )
        assertKotlinSaysAround(
            tokenTroubleWaitLine(.notRecognised(waitMs: 38_000))!
                .replacingOccurrences(of: "0:38", with: sentinel)
        )
        assertKotlinSaysAround(
            tokenTroubleWaitLine(.waiting(waitMs: 38_000))!
                .replacingOccurrences(of: "0:38", with: sentinel)
        )
        // The hints branch three ways, and all three end with the same
        // sentence about the old browser tab. Each clause here is a whole
        // Kotlin literal — the group it belongs to is the rendered sentence
        // with a wait clock interpolated into it, which is why these are
        // literal matches rather than group matches.
        assertKotlinSaysWholeLiteral("Paste the household token to pair again. An admin finds it on the dashboard under ")
        assertKotlinSaysWholeLiteral("Settings → Phones & browsers → Household token.")
        // These two sit either side of the interpolated wait clock
        // (`TokenTroubleScreen.kt:83-84`): the first is the tail of one
        // literal, the second the head of the next, so each is a fragment and
        // containment is all there is.
        assertKotlinSays("Domovoi still wants this phone to wait ")
        assertKotlinSays(" after the wrong token, so the new one is tried when that is over.")
        assertKotlinSaysWholeLiteral("If the household token changed, paste the new one now: it is tried when the wait is over. ")
        assertKotlinSaysWhole(" If a browser on this phone has the dashboard open from before, close that tab or pair it "
            + "again too: it still sends the old token.")
        for buttons in [
            ShellStrings.pairAgain,
            ShellStrings.pairedAgainToast,
            ShellStrings.tryOldTokenAgain,
            ShellStrings.tryingOldTokenToast,
            ShellStrings.useThisPhonesMedia,
        ] {
            assertKotlinSaysWhole(buttons)
        }
    }

    /// `TokenTroubleTest.theCardSpeaksHouseholdNotDashboard`, ported: the card
    /// never says `429`, never names a header, and never gives the
    /// dashboard's advice.
    func testTheCardSpeaksHouseholdNotDashboard() {
        var copy: [String] = []
        for trouble: TokenTrouble in [
            .notRecognised(waitMs: 0), .notRecognised(waitMs: 125_000),
            .waiting(waitMs: 1), .waiting(waitMs: 53_000),
        ] {
            copy.append(tokenTroubleTitle(trouble))
            copy.append(tokenTroubleText(trouble, address: "192.168.1.30:6369"))
            copy.append(tokenTroubleHint(trouble))
            if let line = tokenTroubleWaitLine(trouble) { copy.append(line) }
        }
        for text in copy {
            XCTAssertFalse(text.contains("429"), "no raw status: \(text)")
            XCTAssertFalse(text.contains("X-Device-Token"), "no header names: \(text)")
            XCTAssertFalse(text.contains("sign in as an admin"), "not the dashboard's advice: \(text)")
        }
    }

    // MARK: - §7.8 the pairing screen

    func testThePairingScreenSaysWhatAndroidSays() {
        for s in [
            ShellStrings.pairTitle,
            ShellStrings.pairExplanation,
            ShellStrings.pairHint,
            ShellStrings.notNow,
            ShellStrings.pairedToast,
            ShellStrings.householdTokenField,
            ShellStrings.pair,
            ShellStrings.replace,
        ] {
            assertKotlinSaysWhole(s)
        }
        XCTAssertTrue(ShellStrings.pairExplanation.contains("—"), "em dash")
        XCTAssertEqual(2, ShellStrings.pairHint.components(separatedBy: "→").count - 1, "two arrows")
    }

    /// The field's own rules and the check messages, which live in
    /// ``TokenPasteField``. Not read off the Kotlin until now either.
    func testTheTokenFieldSaysWhatAndroidSays() {
        // A control character the field refuses, and that `trimmed` does
        // NOT strip (so the hint is about the alphabet, not the length).
        assertKotlinSaysWhole(TokenPasteField.hint("abc\u{7}")!)
        assertKotlinSaysAround(
            TokenPasteField.hint("abc")!.replacingOccurrences(of: "12", with: sentinel)
        )
        assertKotlinSaysWhole(TokenPasteField.message(.refused)!)
        assertKotlinSaysWhole(TokenPasteField.message(.unavailable)!)
        assertKotlinSaysWhole(TokenPasteField.message(.throttled(waitMs: 0), waitLeftMs: 0)!)
        assertKotlinSaysAround(
            TokenPasteField.message(.throttled(waitMs: 42_000), waitLeftMs: 42_000)!
                .replacingOccurrences(of: "0:42", with: sentinel)
        )
        XCTAssertTrue(TokenPasteField.message(.refused)!.contains("→"))
        XCTAssertTrue(TokenPasteField.message(.unavailable)!.contains("—"), "em dash")
        XCTAssertTrue(
            TokenPasteField.message(.throttled(waitMs: 1), waitLeftMs: 1)!.contains("—"),
            "em dash"
        )
    }

    // MARK: - §7.9 Settings

    func testTheSettingsIndexSaysWhatAndroidSays() {
        for s in [
            ShellStrings.settingsTitle,
            ShellStrings.settingsSub,
            ShellStrings.settingsSearchPlaceholder,
            ShellStrings.allSettings,
            ShellStrings.onTheDashboard,
            ShellStrings.onTheDashboardSub,
            ShellStrings.openOnTheDashboard,
            ShellStrings.noServerForDashboard,
            ShellStrings.noBrowserForDashboard,
            ShellStrings.localSettingsSub,
            ShellStrings.back,
        ] {
            assertKotlinSaysWhole(s)
        }
        assertKotlinSaysAround(ShellStrings.backTo(sentinel))
        assertKotlinSaysAround(ShellStrings.changedOnTheDashboard(section: sentinel))
        XCTAssertTrue(ShellStrings.settingsSearchPlaceholder.contains("·"), "middle dot")
        XCTAssertTrue(ShellStrings.settingsSearchPlaceholder.contains("…"), "ellipsis")
        XCTAssertTrue(ShellStrings.noServerForDashboard.contains("—"), "em dash")
    }

    /// The no-match line uses **curly** quotes, and trims the query the way
    /// the Kotlin does.
    func testTheNoMatchLineUsesCurlyQuotes() {
        XCTAssertEqual("nothing matches “foo”", ShellStrings.nothingMatches("  foo  "))
        // The prefix of `"nothing matches “${query.trim()}”"`
        // (`SettingsScreen.kt:340`): a fragment, so containment.
        assertKotlinSays("nothing matches “")
    }

    func testThePhoneBarPanelSaysWhatAndroidSays() {
        for s in [
            ShellStrings.phoneBarTitle,
            ShellStrings.alwaysFirst,
            ShellStrings.defaultBar,
            ShellStrings.barIsFull,
            ShellStrings.addToTheBar,
            ShellStrings.fiveTabsAtMost,
            ShellStrings.sharedScreenNote,
        ] {
            assertKotlinSaysWhole(s)
        }
        assertKotlinSaysAround(
            ShellStrings.phoneBarSub()
                .replacingOccurrences(of: "2", with: sentinel)
                .replacingOccurrences(of: "5", with: sentinel)
        )
        // Fragments either side of the route label in
        // `"move ${route.label} left"` (`PhoneBarPanel.kt:182-183`).
        assertKotlinSays("move ")
        assertKotlinSays(" left")
        assertKotlinSays(" right")
        assertKotlinSaysAround(ShellStrings.takeOffTheBar(sentinel))
        assertKotlinSaysAround(ShellStrings.addLabelToTheBar(sentinel))
        assertKotlinSaysWhole("phone bar back to ")
        // The toast's list is joined with the middle dot, and `"everything"`
        // is in ASCII quotes.
        XCTAssertEqual(
            "phone bar back to home · music · videos · chat",
            ShellStrings.phoneBarBackTo(["Home", "Music", "Videos", "Chat"])
        )
        XCTAssertTrue(ShellStrings.phoneBarSub().contains("\"everything\""))
        XCTAssertFalse(ShellStrings.phoneBarSub().contains("“"))
        XCTAssertEqual(2, ShellStrings.phoneBarMin)
        XCTAssertEqual(5, ShellStrings.phoneBarMax)
    }

    func testTheHiddenRoutePlaceholdersSayWhatAndroidSays() {
        let off = ShellStrings.unavailableNote(label: "Chat", isChat: true, aiEnabled: false)
        XCTAssertEqual("chat is turned off", off.title)
        XCTAssertEqual("this server has its AI features turned off", off.detail)
        // The tail of `"${label.lowercase()} is turned off"`
        // (`RouteNotes.kt:11`); the detail beside it is a whole string.
        assertKotlinSays(" is turned off")
        assertKotlinSaysWhole(off.detail)
        let missing = ShellStrings.unavailableNote(label: "Stations", isChat: false, aiEnabled: true)
        XCTAssertEqual("stations isn't available", missing.title)
        // The tail of `"${label.lowercase()} isn't available"`
        // (`RouteNotes.kt:13`).
        assertKotlinSays(" isn't available")
        assertKotlinSaysWhole(missing.detail)
        // Chat with the AI on is "isn't available", not "is turned off".
        XCTAssertEqual(
            "chat isn't available",
            ShellStrings.unavailableNote(label: "Chat", isChat: true, aiEnabled: true).title
        )
    }

    /// Every section's label and `sub`, and the five group labels, read from
    /// the table they were ported from.
    func testEverySettingsSectionMatchesTheKotlin() {
        XCTAssertEqual(14, SettingsSection.all.count)
        XCTAssertEqual(5, SettingsGroup.allCases.count)
        for group in SettingsGroup.allCases {
            assertKotlinSaysWhole(group.label)
            assertKotlinSaysWhole(group.key)
        }
        for section in SettingsSection.all {
            assertKotlinSaysWhole(section.label)
            assertKotlinSaysWhole(section.sub)
            assertKotlinSaysWhole(section.key)
            if !section.words.isEmpty { assertKotlinSaysWhole(section.words) }
            for alias in section.aliases { assertKotlinSaysWhole(alias) }
            if let dashboard = section.dashboard { assertKotlinSaysWhole(dashboard) }
        }
        // The keys are unique, and every alias resolves.
        XCTAssertEqual(14, Set(SettingsSection.all.map(\.key)).count)
        XCTAssertEqual("rooms", SettingsSection.named("satellites")?.key)
        XCTAssertEqual("phones-and-browsers", SettingsSection.named("connection")?.key)
        XCTAssertEqual("advanced", SettingsSection.named("configuration")?.key)
        XCTAssertNil(SettingsSection.named("nope"))
        XCTAssertNil(SettingsSection.named(nil))
        // Two of the subs carry a character worth protecting.
        XCTAssertTrue(SettingsSection.named("voice-history")!.sub.contains("—"), "em dash")
        XCTAssertTrue(SettingsSection.named("advanced")!.sub.contains("—"), "em dash")
    }

    // MARK: - §7.10 toasts

    func testTheToastsSayWhatAndroidSays() {
        assertKotlinSaysAround(ShellStrings.forgotActive(address: sentinel))
        // The prefix of both `"forgot $address"` toasts
        // (`ForgetServer.kt:74`, `ConnectionPanel.kt:247`); the whole of
        // either is checked by `assertKotlinSaysAround` above and by
        // `testForgettingTheActiveServerAlsoAsksForTheList`.
        assertKotlinSays("forgot ")
        for s in [
            ShellStrings.noLongerPaired,
            ShellStrings.serverSaved,
            ShellStrings.couldNotSwitch,
            ShellStrings.nameCannotBeBlank,
            ShellStrings.belongsToNobody,
            ShellStrings.listeningAsThisDevice,
            ShellStrings.pairedToast,
        ] {
            assertKotlinSaysWhole(s)
        }
        // The prefix of `"pinned ${offered.fingerprint}"`
        // (`ConnectionPanel.kt:188`).
        assertKotlinSays("pinned ")
        assertKotlinSaysAround(ShellStrings.deviceRenamed(to: sentinel))
        assertKotlinSaysAround(ShellStrings.renameFailed(sentinel))
        assertKotlinSaysAround(ShellStrings.belongsTo(sentinel))
        assertKotlinSaysAround(ShellStrings.listeningAs(sentinel))
        XCTAssertTrue(ShellStrings.forgotActive(address: "x").contains("—"), "em dash")
        XCTAssertTrue(ShellStrings.serverSaved.contains("—"), "em dash")
        XCTAssertEqual(2400, ShellStrings.toastMs)
    }

    /// §6: Settings → Phones & browsers' three cards, and the forget
    /// confirmation, against `ConnectionPanel.kt` and `ForgetServer.kt` —
    /// both of which this file already reads.
    func testPhonesAndBrowsersSaysWhatAndroidSays() {
        for s in [
            ShellStrings.serverCard,
            ShellStrings.householdTokenCard,
            ShellStrings.trustedServersCard,
            ShellStrings.serverCardSub,
            ShellStrings.householdTokenCardSub,
            ShellStrings.trustedServersCardSub,
            ShellStrings.notConnected,
            ShellStrings.serverURLPlaceholder,
            ShellStrings.saveAndReconnect,
            ShellStrings.paired,
            ShellStrings.notPaired,
            ShellStrings.pairedExplanation,
            ShellStrings.forgetTheToken,
            ShellStrings.identityLabel,
            ShellStrings.pinThisIdentity,
            ShellStrings.identityNotPinnedYet,
            ShellStrings.noTrustedServersYet,
            forgetServerTitle,
            forgetServerCancel,
            forgetServerConfirm,
        ] {
            assertKotlinSaysWhole(s)
        }
        XCTAssertTrue(ShellStrings.serverCardSub.contains("—"), "em dash")
        XCTAssertTrue(ShellStrings.trustedServersCardSub.contains("—"), "em dash")
        XCTAssertTrue(ShellStrings.householdTokenCardSub.contains("→"), "arrow")
    }

    /// The four paragraphs of the identity section (§4.4), each of which has
    /// a branch in the Kotlin's `when` and sits beside its neighbours, so
    /// each is checked as the whole string the Kotlin builds.
    func testTheIdentitySectionSaysWhatAndroidSays() {
        for display: IdentityDisplay in [
            .pinned(fingerprint: "SHA256:a"),
            .offered(ServerIdentity.Pin(publicKey: "k", fingerprint: "SHA256:a")),
            .noIdentityOffered,
            .notPinnedYet,
        ] {
            assertKotlinSaysWhole(display.detail, "the identity section's \(display) paragraph")
        }
    }

    /// The forget confirmation's opening and its three identity lines. The
    /// opening carries the address in the middle of it, so it is checked
    /// around the sentinel; the three lines are whole literals.
    func testTheForgetConfirmationSaysWhatAndroidSays() {
        assertKotlinSaysAround(forgetActiveServerText(address: sentinel, pinned: nil, proven: nil))
        assertKotlinSaysWholeLiteral("\nnot proved on this network yet")
        assertKotlinSaysWholeLiteral("\nit still proves that key — forgetting is not needed for identity's sake")
        XCTAssertTrue(
            forgetActiveServerText(address: "a", pinned: "p", proven: "q").contains("\nit now proves: q")
        )
        XCTAssertTrue(
            forgetActiveServerText(address: "a", pinned: "p", proven: "p").contains("—"),
            "em dash"
        )
    }

    /// The sentences Android has no words for are **not** in the Kotlin, and
    /// that is the point: this is the test that fails if someone "ports" one
    /// of them into `ShellStrings` as though it were Android's.
    func testTheTwoIOSOnlySentencesAreNotInTheKotlin() {
        XCTAssertFalse(Self.haystack.contains(IOSOnlyStrings.localNetworkDenied))
        XCTAssertFalse(Self.haystack.contains(IOSOnlyStrings.noAddressYet))
        XCTAssertFalse(Self.haystack.contains(IOSOnlyStrings.attentionStale))
        // The one Android does say about an empty sweep is checked against
        // the Kotlin by `testThePickersTwoInterpolations`, which splits it on
        // the sentinel where the port goes; it is not repeated here.
    }

    /// `ShellStrings.forgot` is a prefix of `forgotActive`, so the shorter one
    /// must not be checked by containment alone; this pins the pair.
    func testForgettingTheActiveServerAlsoAsksForTheList() {
        XCTAssertEqual("forgot box:6369", ShellStrings.forgot(address: "box:6369"))
        XCTAssertEqual("forgot box:6369 — pick a server", ShellStrings.forgotActive(address: "box:6369"))
    }

    // MARK: - Home's header (map §6.1)

    /// The header's seven strings, each against the Kotlin whole.
    ///
    /// The map says of all of `ui/screens/home/`: "The apostrophe is ASCII
    /// U+0027 in every single Home string … there is **no** curly apostrophe
    /// anywhere". `HomeHeaderStrings.explainUnpaired` has two of them, so a
    /// curly one typed here is what this catches — along with the U+00B7
    /// middle dots and the separator's four spaces.
    func testTheHomeHeaderSaysWhatTheKotlinSays() {
        assertKotlinSaysWhole(HomeHeaderStrings.live)
        assertKotlinSaysWhole(HomeHeaderStrings.notLive, "U+00B7, one space each side")
        assertKotlinSaysWhole(HomeHeaderStrings.explainLive)
        assertKotlinSaysWhole(HomeHeaderStrings.explainUnpaired, "both apostrophes ASCII")
        assertKotlinSaysWhole(HomeHeaderStrings.explainDown)
        assertKotlinSaysWhole(HomeHeaderStrings.pairLink)
        assertKotlinSaysWhole(HomeHeaderStrings.countsSeparator, "two spaces, U+00B7, two spaces")
        assertKotlinSaysWhole(HomeHeaderStrings.defaultName)
    }

    /// Spelt out rather than left to the whole-string check, because these
    /// are the characters a careless copy loses and the map flagged this
    /// exact file for it.
    func testTheHomeHeaderKeepsItsMiddleDotsAndAsciiApostrophes() {
        for string in [
            HomeHeaderStrings.notLive,
            HomeHeaderStrings.explainLive,
            HomeHeaderStrings.explainUnpaired,
            HomeHeaderStrings.explainDown,
            HomeHeaderStrings.countsSeparator,
        ] {
            XCTAssertTrue(string.contains("\u{00B7}"), "U+00B7 middle dot: \(debugged(string))")
            XCTAssertFalse(string.contains("\u{2022}"), "a bullet is not a middle dot")
        }
        XCTAssertTrue(HomeHeaderStrings.explainUnpaired.contains("isn't"))
        XCTAssertTrue(HomeHeaderStrings.explainUnpaired.contains("doesn't"))
        XCTAssertFalse(HomeHeaderStrings.explainUnpaired.contains("\u{2019}"), "no curly apostrophe")
        XCTAssertEqual("  \u{00B7}  ", HomeHeaderStrings.countsSeparator)
    }

    // MARK: - the health checks (map §6.2) and the card (§6.3)

    /// The twenty health-check strings that are whole literals in
    /// `HealthChecks.kt`, each against the Kotlin whole.
    ///
    /// Whole and not fragments: a fragment check passes on a substring, so
    /// `"none"` would be satisfied by `"none installed"` and `"ready"` by
    /// `"already"` — and twenty-four assertions that cannot fail are worse
    /// than none, because they read as cover.
    func testTheHealthChecksSayWhatTheKotlinSays() {
        assertKotlinSaysWhole(HealthStrings.waiting)
        assertKotlinSaysWhole(HealthStrings.unread)
        assertKotlinSaysWhole(HealthStrings.answering)
        assertKotlinSaysWhole(HealthStrings.coreDown, "U+00B7, ASCII apostrophe in isn't")
        assertKotlinSaysWhole(HealthStrings.dbDown)
        assertKotlinSaysWhole(HealthStrings.sttReady)
        assertKotlinSaysWhole(HealthStrings.sttOff, "can't, ASCII")
        assertKotlinSaysWhole(HealthStrings.sttFallback)
        assertKotlinSaysWhole(HealthStrings.sttUnreported)
        assertKotlinSaysWhole(HealthStrings.noRoomsYet)
        assertKotlinSaysWhole(HealthStrings.none)
        assertKotlinSaysWhole(HealthStrings.everyScreenIsShowing)
        assertKotlinSaysWhole(HealthStrings.noneInstalled)
        assertKotlinSaysWhole(HealthStrings.noneStuck)
        assertKotlinSaysWhole(HealthStrings.acqCoreAway)
        assertKotlinSaysWhole(HealthStrings.oneRequestWaiting)
        assertKotlinSaysWhole(HealthStrings.containersFine)
        assertKotlinSaysWhole(HealthStrings.adminAttention)
        assertKotlinSaysWhole(HealthStrings.tampering)
        assertKotlinSaysWhole(HealthStrings.containerNeedsAttention)
        // The one plural, which is built rather than stored
        // (`"${ids.size} Domovoi containers need attention"`,
        // `HealthChecks.kt`). Guarded by `> 1` on both sides, so there is no
        // "1 Domovoi containers" to catch — unlike `stop $count rooms?`,
        // which is defect #9 and is not this batch's code.
        assertKotlinSays(" Domovoi containers need attention")
        XCTAssertEqual(
            "2 Domovoi containers need attention",
            guardCheck(ContainerGuardState(
                loaded: true, tier: .household, bannerShows: true,
                bannerTone: .error, incidentIDs: [1, 2]
            )).text
        )
    }

    /// The four that **cannot** be whole literals, and why.
    ///
    /// Each one is the tail of a Kotlin string with a value interpolated into
    /// its head — `"$name is offline · it can't hear anyone right now"` — so
    /// there is no literal in the file equal to it and
    /// ``assertKotlinSaysWhole(_:_:file:line:)`` would have to fail. The
    /// honest check is containment, and the leading space is part of what is
    /// checked: dropping it is how `kitchenis offline` ships.
    func testTheFourInterpolatedTailsAreTheKotlinsTails() {
        assertKotlinSays(HealthStrings.isOfflineSuffix, "leading space included")
        assertKotlinSays(HealthStrings.wasSetUpSuffix, "leading space included")
        assertKotlinSays(HealthStrings.allWorkingSuffix, "space, U+00B7, space")
        assertKotlinSays(HealthStrings.manyRequestsSuffix)
        for suffix in [
            HealthStrings.isOfflineSuffix,
            HealthStrings.wasSetUpSuffix,
            HealthStrings.allWorkingSuffix,
            HealthStrings.manyRequestsSuffix,
        ] {
            XCTAssertTrue(suffix.hasPrefix(" "), "a tail begins with its space: \(debugged(suffix))")
        }
    }

    /// The card's three ported strings. The fourth — the stale mark — is
    /// **ours**, and `IOSOnlyStrings` is where that is recorded; a
    /// `assertKotlinSaysWhole` on it would have to fail.
    func testTheAttentionCardSaysWhatTheKotlinSays() {
        assertKotlinSaysWhole(HomeAttentionStrings.title)
        assertKotlinSaysWhole(HomeAttentionStrings.summaryLine, "the admin's, ASCII apostrophe")
        assertKotlinSaysWhole(HomeAttentionStrings.checking, "U+2026 ellipsis, not three dots")
        XCTAssertFalse(
            Self.wholeStrings.contains(HomeAttentionStrings.stale),
            "if Android ever grows this string, port it and drop it from IOSOnlyStrings"
        )
    }

    /// `+N more`, which is built rather than stored.
    ///
    /// `HomeSections.kt:303` is `"+${rows.size - 3} more"`, so only the tail
    /// is a literal; the `+` and the count are the composition, and that is
    /// what the second assertion pins.
    func testTheMoreLabelIsTheKotlinsMoreLabel() {
        assertKotlinSays(" more", "the tail of HomeSections.kt:303's interpolation")
        let five = HomeAttentionState(
            view: .rows((0..<5).map {
                AttentionRow(key: "k\($0)", tone: .warn, text: "x", target: .settings("health"))
            }),
            compact: true
        )
        XCTAssertEqual("+2 more", five.moreLabel)
    }

    /// `relTime`'s words, from `ui/components/Format.kt`.
    func testTheRelativeTimesSayWhatTheKotlinSays() {
        assertKotlinSaysWhole("just now")
        assertKotlinSays("m ago")
        assertKotlinSays("h ago")
        assertKotlinSays("d ago")
        assertKotlinSays("MMM d, yyyy", "the pattern itself, so a locale cannot drift it")
        assertKotlinSaysWhole("\u{2014}", "an em dash for no time, not a hyphen")
    }

}
