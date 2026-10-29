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
/// asks whether ours is in there.
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
    ]

    /// Every literal in every file above, decoded, concatenated per file.
    private static let haystack: String = {
        let base = repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        return kotlinFiles.map { name -> String in
            let url = base.appendingPathComponent(name)
            guard let text = try? String(contentsOf: url, encoding: .utf8) else { return "" }
            // A sentinel between files so a fragment cannot be satisfied by
            // the end of one file plus the start of the next.
            return literals(in: text).joined() + "\u{0}"
        }.joined()
    }()

    /// Pull every double-quoted Kotlin string literal out of `source`, in
    /// order, with its escapes decoded. Line and block comments are skipped;
    /// a `//` inside a literal is not a comment.
    ///
    /// Kotlin's raw strings (`"""…"""`) are read as raw: no escape decoding,
    /// which is what raw means. None of the files above uses one for a
    /// user-facing string, so this is for completeness rather than for a case
    /// that exists.
    private static func literals(in source: String) -> [String] {
        var out: [String] = []
        let chars = Array(source)
        var i = 0
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
                out.append(raw)
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
            out.append(value)
        }
        return out
    }

    // MARK: - the helpers every assertion goes through

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
    func testEveryKotlinFileWasActuallyRead() throws {
        let base = Self.repositoryRoot.appendingPathComponent("android/app/src/main/java/com/domovoi/app")
        for name in Self.kotlinFiles {
            let url = base.appendingPathComponent(name)
            XCTAssertTrue(
                FileManager.default.fileExists(atPath: url.path),
                "\(name) has moved; this test would quietly check nothing"
            )
            let text = try String(contentsOf: url, encoding: .utf8)
            XCTAssertFalse(
                Self.literals(in: text).isEmpty,
                "\(name) contributed no string literals at all"
            )
        }
        XCTAssertGreaterThan(Self.haystack.count, 5_000, "the haystack is far smaller than measured")
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
            assertKotlinSays(s)
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
        assertKotlinSays("/ android", "the Kotlin's word, which ours replaces")
        XCTAssertEqual("/ iphone", ShellStrings.platformCrumb)
        XCTAssertFalse(Self.haystack.contains("/ iphone"))
    }

    // MARK: - §7.2 the picker

    func testThePickerSaysWhatAndroidSays() {
        assertKotlinSays(ShellStrings.notOnWifi)
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
            assertKotlinSays(s)
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
            assertKotlinSays(s)
        }
        // "use " is four characters, so it is checked whole rather than
        // split: the splitter skips pieces too short to mean anything.
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
            assertKotlinSays(choice.reason)
        }
        assertKotlinSays(ServerChoice.noServer.title)
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
    func testTheIdentityChangedBannerSaysWhatAndroidSays() {
        assertKotlinSays("This looks like a different Domovoi, or it was reinstalled. ")
        assertKotlinSays("the identity this phone trusted, so this phone sent it nothing and is showing its own media. ")
        assertKotlinSays("If you reinstalled Domovoi or moved it to a new machine, trust it again. ")
        assertKotlinSays("If you're away from home, don't.")
        assertKotlinSays("If you replaced the server, forget it (the X next to it in the server list, or under ")
        assertKotlinSays("Settings → Phones & browsers → Trusted servers), then trust and pair it again.")
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
            assertKotlinSays(s)
        }
        XCTAssertTrue(trustAgainButton.contains("—"), "em dash")
        assertKotlinSaysAround(trustAgainText(pinned: sentinel, now: sentinel))
        assertKotlinSaysAround(trustingAgainToast(address: sentinel))
        XCTAssertTrue(trustingAgainToast(address: "x").contains("—"), "em dash")
    }

    // MARK: - §7.6 the trust dialog

    func testTheTrustDialogSaysWhatAndroidSays() {
        for s in [ShellStrings.trustTitle, ShellStrings.trustCancel, ShellStrings.trustConfirm] {
            assertKotlinSays(s)
        }
        // The common part, and each tail on its own: the two tails are
        // neighbours in the Kotlin.
        assertKotlinSays("Check that this address is your Domovoi before you continue: the app "
            + "will load the screens this server advertises and send it your "
            + "requests. Nothing is saved until you confirm.")
        assertKotlinSays(" The identity above should match Settings → About & updates on the dashboard; "
            + "trusting pins it, and the household token goes out only after the "
            + "server proves it holds that key.")
        assertKotlinSays(" This server offers no identity (an older web backend), so nothing "
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
            assertKotlinSays(tokenTroubleTitle(trouble))
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
        // sentence about the old browser tab.
        assertKotlinSays("Paste the household token to pair again. An admin finds it on the dashboard under ")
        assertKotlinSays("Settings → Phones & browsers → Household token.")
        assertKotlinSays("Domovoi still wants this phone to wait ")
        assertKotlinSays(" after the wrong token, so the new one is tried when that is over.")
        assertKotlinSays("If the household token changed, paste the new one now: it is tried when the wait is over. ")
        assertKotlinSays(" If a browser on this phone has the dashboard open from before, close that tab or pair it "
            + "again too: it still sends the old token.")
        for buttons in [
            ShellStrings.pairAgain,
            ShellStrings.pairedAgainToast,
            ShellStrings.tryOldTokenAgain,
            ShellStrings.tryingOldTokenToast,
            ShellStrings.useThisPhonesMedia,
        ] {
            assertKotlinSays(buttons)
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
            assertKotlinSays(s)
        }
        XCTAssertTrue(ShellStrings.pairExplanation.contains("—"), "em dash")
        XCTAssertEqual(2, ShellStrings.pairHint.components(separatedBy: "→").count - 1, "two arrows")
    }

    /// The field's own rules and the check messages, which live in
    /// ``TokenPasteField``. Not read off the Kotlin until now either.
    func testTheTokenFieldSaysWhatAndroidSays() {
        // A control character the field refuses, and that `trimmed` does
        // NOT strip (so the hint is about the alphabet, not the length).
        assertKotlinSays(TokenPasteField.hint("abc\u{7}")!)
        assertKotlinSaysAround(
            TokenPasteField.hint("abc")!.replacingOccurrences(of: "12", with: sentinel)
        )
        assertKotlinSays(TokenPasteField.message(.refused)!)
        assertKotlinSays(TokenPasteField.message(.unavailable)!)
        assertKotlinSays(TokenPasteField.message(.throttled(waitMs: 0), waitLeftMs: 0)!)
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
            assertKotlinSays(s)
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
            assertKotlinSays(s)
        }
        assertKotlinSaysAround(
            ShellStrings.phoneBarSub()
                .replacingOccurrences(of: "2", with: sentinel)
                .replacingOccurrences(of: "5", with: sentinel)
        )
        assertKotlinSays("move ")
        assertKotlinSays(" left")
        assertKotlinSays(" right")
        assertKotlinSaysAround(ShellStrings.takeOffTheBar(sentinel))
        assertKotlinSaysAround(ShellStrings.addLabelToTheBar(sentinel))
        assertKotlinSays("phone bar back to ")
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
        assertKotlinSays(" is turned off")
        assertKotlinSays(off.detail)
        let missing = ShellStrings.unavailableNote(label: "Stations", isChat: false, aiEnabled: true)
        XCTAssertEqual("stations isn't available", missing.title)
        assertKotlinSays(" isn't available")
        assertKotlinSays(missing.detail)
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
            assertKotlinSays(group.label)
            assertKotlinSays(group.key)
        }
        for section in SettingsSection.all {
            assertKotlinSays(section.label)
            assertKotlinSays(section.sub)
            assertKotlinSays(section.key)
            if !section.words.isEmpty { assertKotlinSays(section.words) }
            for alias in section.aliases { assertKotlinSays(alias) }
            if let dashboard = section.dashboard { assertKotlinSays(dashboard) }
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
            assertKotlinSays(s)
        }
        assertKotlinSays("pinned ")
        assertKotlinSaysAround(ShellStrings.deviceRenamed(to: sentinel))
        assertKotlinSaysAround(ShellStrings.renameFailed(sentinel))
        assertKotlinSaysAround(ShellStrings.belongsTo(sentinel))
        assertKotlinSaysAround(ShellStrings.listeningAs(sentinel))
        XCTAssertTrue(ShellStrings.forgotActive(address: "x").contains("—"), "em dash")
        XCTAssertTrue(ShellStrings.serverSaved.contains("—"), "em dash")
        XCTAssertEqual(2400, ShellStrings.toastMs)
    }

    /// `ShellStrings.forgot` is a prefix of `forgotActive`, so the shorter one
    /// must not be checked by containment alone; this pins the pair.
    func testForgettingTheActiveServerAlsoAsksForTheList() {
        XCTAssertEqual("forgot box:6369", ShellStrings.forgot(address: "box:6369"))
        XCTAssertEqual("forgot box:6369 — pick a server", ShellStrings.forgotActive(address: "box:6369"))
    }
}
