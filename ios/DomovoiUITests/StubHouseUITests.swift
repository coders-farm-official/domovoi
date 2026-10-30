import XCTest

/// The screens behind a server, looked at.
///
/// Every visual check on this track was blocked on the same fact: with no
/// server saved the shell is `.local`, so Home, the pairing screens and the
/// token-trouble card are unreachable and a Simulator can only ever
/// photograph the music placeholder. `swift test` cannot see a view, the
/// compiler cannot see a layout, and a review cannot see a cut line — and
/// the *first* screen still failed Apple's accessibility audit on clipped
/// text with 643 tests green and three reviews done.
///
/// The server is `stubhouse.py` on 127.0.0.1:6601
/// (`design-notes/ios/STUB-HOUSE.md`), which the Simulator reaches as the
/// Mac's own loopback. Nothing here touches a real house.
///
/// **The app is launched with the server already saved**, rather than
/// driven through the connection sheet. Opening that sheet starts the LAN
/// discovery sweep, and a Simulator shares the Mac's network — the first
/// run of this file reached `scanning 80/254…` across a real home network
/// before it was stopped. `-domovoi-server` is put through the picker's own
/// normaliser, so the address this file passes is normalised by the code that
/// normalises a typed one — see `DomovoiApp.seededStore()`, which says what
/// happened when that was a comment's promise rather than a call.
///
/// ### Evidence before verdict
///
/// Every test here takes its picture and dumps the tree **before** it
/// asserts anything. The first run of this file asserted first, three cases
/// failed, and `continueAfterFailure = false` meant the screenshots were
/// never taken — so a run whose entire purpose was to produce pictures of a
/// screen nobody had seen produced none of the screen under test. A failing
/// check here is a question about a screen, and the answer is the picture.
final class StubHouseUITests: XCTestCase {

    /// Long enough to be storable: `TokenPasteField` refuses anything under
    /// twelve characters, and a refused token would leave the phone
    /// unpaired, which is a different screen from the one being photographed.
    private static let token = "stubhousetoken0000000000"

    /// ### What Apple's audit has left against these screens
    ///
    /// **Home: nothing.** It had one "Contrast failed" that five rounds of
    /// explanation could not place — the colours were measured off a
    /// screenshot at 6.98:1 and 5.31:1, an "empty button" shape was ruled out
    /// by the local shell passing, the pill's capsule by the same, and a
    /// synthetic `.accessibilityLabel` by removing it and watching the audit
    /// come back byte for byte identical.
    ///
    /// What ended it was reading `issue.element`, which this track had spent
    /// three sessions calling "always nil". It is not nil; `stub_run.sh`
    /// filters `xcodebuild`'s output and the element was in the full log the
    /// whole time. It named the phone bar's `music` caption — one word of
    /// four in the same colour and the same font — and what is true of all
    /// four is that they were drawn on `.bar`, a material whose colour is
    /// whatever is behind it. The bar is an opaque surface now and Home
    /// audits clean.
    ///
    /// **The pairing screen: eight, all the same one.** "Dynamic Type font
    /// sizes are partially unsupported", on elements the audit does *not*
    /// name — the nil elements are real for this finding, where they were
    /// never real for contrast. Everything else that screen had is fixed and
    /// each of them was a defect, not an artefact:
    ///
    ///  - three "Text clipped" — the `Phones & browsers` heading, the
    ///    `back to Rooms & devices` button and the server address field, all
    ///    squeezable flat for want of a `fixedSize`;
    ///  - five "Contrast failed" — the brand *fill* amber at 2.05:1 serving
    ///    as every plain button's label colour through the `AccentColor`
    ///    asset, and four elements the bar was drawn over or that straddled a
    ///    scroll edge, which have no pixels an audit can read;
    ///  - one "Hit area is too small", introduced by the fix for the clipped
    ///    address field and then fixed in turn.
    ///
    /// These are **equality** assertions, so the next change that makes
    /// either screen worse fails, and so does the one that takes the eight to
    /// seven. Nothing in the app target sets a fixed font size — `grep` for
    /// `font(.system(size:` and `UIFont.systemFont` finds none — so whatever
    /// Dynamic Type objects to there, it is not a hard-coded point size.
    private static let knownHomeIssues = 0
    private static let knownPairingIssues = 8

    override func setUp() {
        continueAfterFailure = false
    }

    /// `server: false` saves nothing, which is the local-media shell — no
    /// workspace, no phone bar, and the same ``ShellTopBar``. It is the only
    /// way from here to audit the chrome without Home's content under it.
    private func launch(
        mode: String = "large",
        paired: Bool = true,
        server: Bool = true,
        port: Int = 6601
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = server ? ["-domovoi-server", "127.0.0.1:\(port)"] : []
        if server, paired {
            app.launchArguments += ["-domovoi-token", Self.token]
        }
        // The type size, set the way the Simulator's own setting would.
        app.launchArguments += ["-UIPreferredContentSizeCategoryName", mode]
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        return app
    }

    /// The house's name in the header, which is the one thing on this screen
    /// only a *completed read* can produce: `HomeConfig.name(of:)` draws
    /// `bot_name` when it is non-blank and the literal `domovoi` otherwise,
    /// so `domovoi` on a server called `stub house` means the config read
    /// did not land — whatever the network did.
    private func houseName(in app: XCUIApplication) -> XCUIElement {
        app.staticTexts["stub house"]
    }

    /// Whether the shell gave up on the server and fell back to local media.
    ///
    /// Worth its own check, and its own message, because it is a *different
    /// failure* from a read that did not land: the workspace is gone, Home is
    /// not on screen at all, and asserting on the header's name reports the
    /// symptom four layers from the cause. The first run of this file said
    /// "the config read did not land" when the truth was that the app had
    /// never sent a request — and it took a request log and five files of
    /// reading to find that out.
    ///
    /// `localBanner(for:canTrustAgain:)`'s `.unreachable` wording, which
    /// appears only in that fallback.
    private func fellBackToLocalMedia(in app: XCUIApplication) -> Bool {
        app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label CONTAINS %@", "Can't reach"))
            .firstMatch
            .exists
    }

    /// Screenshot plus the accessibility tree, kept whatever happens next.
    ///
    /// The tree is the half that answers *why*: the picture of the first run
    /// would have shown a header reading `domovoi`, and only the tree said
    /// the header had also drawn `offline` and `not live` beside a server
    /// address that was plainly reachable.
    private func evidence(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)

        let tree = XCTAttachment(string: app.debugDescription)
        tree.name = "\(name)-tree"
        tree.lifetime = .keepAlways
        add(tree)

        print("=== TREE: \(name) ===")
        print(app.debugDescription)
    }

    /// Home, drawn against a server, at the ordinary type size.
    func testHomeDrawsAgainstAServer() throws {
        let app = launch()
        // Give the reads a chance to land before the picture is taken, but
        // photograph either way: `waitForExistence`'s return value is the
        // verdict, not the evidence.
        let named = houseName(in: app).waitForExistence(timeout: 20)
        evidence(app, "10-home-large")
        // The shell first, because a workspace that is gone explains a header
        // that never drew and not the other way round.
        XCTAssertFalse(
            fellBackToLocalMedia(in: app),
            "the shell gave up on the server and showed local media — the "
                + "saved address or the server, not Home"
        )
        XCTAssertTrue(
            named,
            "the header never drew the house's name — the config read did not land"
        )
    }

    /// The same screen at the largest accessibility type size.
    ///
    /// This is the size that failed the audit on the first screen in I1-C,
    /// and the one the attention card's wrapped rows were argued about in
    /// I1-D part 2's divergence list.
    func testHomeAtTheLargestTypeSize() throws {
        let app = launch(mode: "UICTContentSizeCategoryAccessibilityXXXL")
        let named = houseName(in: app).waitForExistence(timeout: 20)
        evidence(app, "11-home-ax5")
        XCTAssertTrue(named, "the header never drew the house's name")

        // ### The bar must not eat the screen
        //
        // The first version of this screen passed the clipping audit and was
        // worse than the thing it fixed. The tree measured the server pill at
        // **62.7 points wide and 314.3 tall** — its address wrapped to about a
        // character a line — because the pill, `unverified` and the status
        // word were dividing 361 points between them. Home's content began at
        // **y=444 of 852**: more than half the screen was chrome.
        //
        // These are the two numbers that say so, and they are deliberately
        // generous. A pill under 160 points is one that still wraps its
        // address over two or three lines at this type size, which is fine; a
        // pill over it is one that has gone back to wrapping per character.
        let pill = app.buttons[ShellStringsUnderTest.pillLabel]
        XCTAssertTrue(pill.exists, "no server pill in the bar")
        XCTAssertLessThan(
            pill.frame.height,
            160,
            "the server pill is \(Int(pill.frame.height)) points tall — its "
                + "address is wrapping a character at a time, which is what "
                + "sharing a row with `unverified` and the status word does"
        )
        XCTAssertLessThan(
            houseName(in: app).frame.minY,
            360,
            "Home's content starts at y=\(Int(houseName(in: app).frame.minY)) "
                + "of 852 — the bar is taking most of the screen"
        )
    }

    /// Apple's audit on a screen, with every issue it has — not the first.
    ///
    /// Two things this gets right that the inline version did not.
    ///
    /// `continueAfterFailure` is **true for the duration**. The handler
    /// returning `true` is documented to suppress an issue, and for the two
    /// "Text clipped" ones it did; a "Contrast failed" with a nil element was
    /// recorded by XCTest anyway, and `setUp`'s `continueAfterFailure = false`
    /// then ended the test inside `performAccessibilityAudit` — before the
    /// assertion that lists everything, and before any later screen was
    /// looked at. Whatever the suppression rule really is, the list is
    /// complete this way.
    ///
    /// It returns the issues rather than asserting, so a caller can audit
    /// **two screens and compare**. That is the only tool here that narrows a
    /// contrast issue: Apple names no element, `issue.element` is nil, and
    /// two screens that share chrome but not content say which half it is in.
    @MainActor
    @available(iOS 17.0, *)
    private func auditIssues(_ app: XCUIApplication, _ name: String) throws -> [String] {
        let wasContinuing = continueAfterFailure
        continueAfterFailure = true
        defer { continueAfterFailure = wasContinuing }
        var found: [String] = []
        try app.performAccessibilityAudit { issue in
            found.append("\(issue.compactDescription): \(issue.detailedDescription)")
            // **One `print` per line, deliberately.** This was a single
            // `print` of a multi-line string, and `xcodebuild`'s output kept
            // only its first line: three runs' worth of logs showed the
            // headline and silently dropped the detail and the element, which
            // is why "`issue.element` is always nil" was a belief rather than
            // an observation on this track. A one-line call per field cannot
            // be half-swallowed.
            print("AUDIT [\(name)] \(issue.compactDescription) | type=\(issue.auditType)")
            print("AUDIT [\(name)]   detail: \(issue.detailedDescription)")
            print("AUDIT [\(name)]   element: \(Self.oneLine(issue.element))")
            return true
        }
        return found
    }

    /// An audit issue's element as one line, or `nil` as the word.
    ///
    /// `XCUIElement.debugDescription` is a paragraph with its own query chain
    /// and newlines, which is what got swallowed. Type, identifier, label and
    /// frame are enough to find the view in the tree dumps beside it.
    @MainActor
    private static func oneLine(_ element: XCUIElement?) -> String {
        guard let element else { return "nil" }
        let id = element.identifier
        return "type=\(element.elementType.rawValue)"
            + (id.isEmpty ? "" : " id='\(id)'")
            + " label='\(element.label)'"
            + " frame=\(element.frame)"
    }

    /// The local-media shell, audited — the screen that split the chrome.
    ///
    /// Home has one "Contrast failed" that nothing names, and the pairing
    /// screen has three. What those two share is the top bar **and** the
    /// phone bar, so comparing them could only say "chrome", not which half.
    ///
    /// With nothing saved there is no workspace: the same ``ShellTopBar`` over
    /// the local-media placeholder, and **no phone bar**. It passes — zero
    /// issues — which is what put the failure in the phone bar and cleared
    /// the top bar with it, the pill's `.quaternary` capsule included
    /// (`#DDDDDE` on white is 1.36:1, and it is on this screen too).
    ///
    /// So this is not only the I1-C result turned into a standing test,
    /// though it is that as well — the chrome has been rebuilt twice since
    /// that run, and "the local shell passed" was a thing remembered rather
    /// than a thing checked. It is also the control for
    /// ``knownHomeIssues``: a day when this one starts failing is a day
    /// those two numbers mean something different.
    @MainActor
    func testTheLocalShellPassesTheAudit() throws {
        let app = launch(server: false)
        _ = app.staticTexts.firstMatch.waitForExistence(timeout: 20)
        evidence(app, "40-local-shell")
        if #available(iOS 17.0, *) {
            let found = try auditIssues(app, "local-shell")
            XCTAssertTrue(
                found.isEmpty,
                "Apple's audit has \(found.count) against the local shell: "
                    + found.joined(separator: " | ")
            )
        }
    }

    /// Apple's own audit, on Home rather than on the launch screen.
    ///
    /// `@MainActor` because `performAccessibilityAudit` is main-actor
    /// isolated and calling it from a `nonisolated` test warns under Swift 6.
    @MainActor
    func testHomePassesApplesAccessibilityAudit() throws {
        let app = launch()
        let named = houseName(in: app).waitForExistence(timeout: 20)
        evidence(app, "12-home-audited")
        // The audit runs on whatever is on screen. It is worth running even
        // when the read did not land — a header with a fallback name in it
        // is still a header, and contrast and touch targets do not depend on
        // the server answering.
        if #available(iOS 17.0, *) {
            let found = try auditIssues(app, "home")
            XCTAssertEqual(
                found.count,
                Self.knownHomeIssues,
                "Apple's audit has \(found.count) against Home, not the "
                    + "\(Self.knownHomeIssues) known: "
                    + found.joined(separator: " | ")
            )
        }
        XCTAssertTrue(named, "the header never drew the house's name")
    }

    /// An unpaired phone still sees its house, and is told to pair.
    ///
    /// Both halves matter and they come from different tiers of the same
    /// server: `/api/config` is open, so the header draws `stub house`,
    /// while `/api/satellites`, `/api/calendar/events` and `/api/timers`
    /// answer 401 — and that is what raises the pairing link. A phone
    /// showing neither has lost the server; one showing the name and no link
    /// is silently missing its rooms.
    ///
    /// `--mode unpaired` is **not** what produces those 401s, whatever this
    /// comment claimed when it was written. The stub refuses a device-tier
    /// route when `MODE == "unpaired" or not self._paired()`, and a launch
    /// with no token fails the second half on its own; the mode changes that
    /// line only for a phone that *does* send a token.
    ///
    /// The **socket** is the other way round. Its refusal is
    /// `MODE == "unpaired" and not self._paired()`, so under `--mode normal`
    /// an unpaired phone is handed `/ws/state` anyway and the header reads
    /// `live` with no token saved. That cannot happen against a real house,
    /// so this case has to be run a **second** time under `--mode unpaired`,
    /// where the socket behaves like the server it stands in for. Nothing in
    /// this file can pin that: `stub_run.sh` gives one mode to the whole
    /// class. The assertions below hold under either, which is the point —
    /// the mode decides what the *picture* shows, and whether a socket's own
    /// 403 can raise the pairing screen (map §11.6) is only answerable
    /// there.
    ///
    /// It asserted nothing until it was looked at: `waitForExistence` on
    /// `staticTexts.firstMatch` is true of every screen the app can draw,
    /// the local-media placeholder included, so this case could not fail.
    func testAnUnpairedPhoneIsToldToPair() throws {
        let app = launch(paired: false)
        // Whatever it shows, photograph it: the point of this one is the
        // words, which no test has ever read on a screen.
        let link = app.buttons[ShellStringsUnderTest.pairLink]
        let told = link.waitForExistence(timeout: 20)
        let named = houseName(in: app).waitForExistence(timeout: 10)
        evidence(app, "20-unpaired")
        XCTAssertFalse(
            fellBackToLocalMedia(in: app),
            "the shell gave up on the server instead of drawing an unpaired Home"
        )
        XCTAssertTrue(
            named,
            "the open config read did not land — an unpaired phone still gets it"
        )
        XCTAssertTrue(told, "nothing on this screen told the phone to pair")
    }

    /// The screen behind the pairing link — and the app's prominent buttons,
    /// which nothing has ever photographed.
    ///
    /// Worth its own case because of what the brand change rests on:
    /// `.borderedProminent` draws a **white** label, and white on the brand
    /// amber is 2.05:1, so those buttons were given `Color.brandFg`
    /// explicitly. Whether a button style lets a caller override its label
    /// colour is not something the compiler or `swift test` can answer, and
    /// if it does not, the fix is to tint the button with `brandInk` instead.
    /// Either way the picture decides it.
    ///
    /// Reached by **tapping**, not by opening the connection sheet: that
    /// sheet starts the LAN discovery sweep across whatever network the Mac
    /// is on. This route is `onPair` → `openSettingsTab("phones-and-browsers")`
    /// and touches nothing but the stub.
    ///
    /// ### It also audits, and this screen is the worse of the two
    ///
    /// Fourteen issues, against Home's one. Six of them are the phone bar's,
    /// which ``knownPairingIssues`` says what is known about; the other
    /// **eight are this screen's own** — four "Dynamic Type font sizes are
    /// partially unsupported", three "Text clipped" and one contrast — and
    /// they are I1-D work rather than an accepted limit.
    ///
    /// Sharing the chrome with Home and nothing else is what made it useful
    /// before that split: no live dot, none of Home's scrolling content. The
    /// live dot was cleared here, and `issue.element` is nil for every one of
    /// the fourteen, so the eight will be found the same way the one was —
    /// by taking things off the screen, not by reading the audit's sentence.
    @MainActor
    func testThePairingScreenAndItsButtons() throws {
        let app = launch(paired: false)
        let link = app.buttons[ShellStringsUnderTest.pairLink]
        if !link.waitForExistence(timeout: 20) {
            // **Print the screen, not a sentence about it.** This assertion
            // failed for two runs while four other cases did the identical
            // three lines and passed, and a message naming the thing that is
            // missing cannot say what is there instead. `showsPairLink` is
            // `!paired`, so the tree answers in one line whether this is an
            // unpaired Home without its link or a paired one that should
            // never have been.
            evidence(app, "30-pairing-MISSING")
            print("PAIRLINK MISSING — tree follows")
            print(app.debugDescription)
            XCTFail("no pairing link on Home — the unpaired header did not draw")
            return
        }
        link.tap()
        // Proof that the tap went somewhere, rather than a screenshot of the
        // screen we started on: the link belongs to Home and Home is gone.
        let left = link.waitForNonExistence(timeout: 10)
        evidence(app, "30-pairing")
        if #available(iOS 17.0, *) {
            let found = try auditIssues(app, "pairing")
            XCTAssertEqual(
                found.count,
                Self.knownPairingIssues,
                "Apple's audit has \(found.count) against the pairing screen, "
                    + "not the \(Self.knownPairingIssues) known: "
                    + found.joined(separator: " | ")
            )
        }
        XCTAssertTrue(left, "the pairing link did nothing — still on Home")
    }

    /// **Where do the eight live?** A bisect that changes nothing in the app.
    ///
    /// The eight are "Dynamic Type font sizes are partially unsupported" and
    /// the audit does not name their elements, so they have to be found by
    /// difference. The obvious way — a launch argument that hides one card at
    /// a time — means diagnostic `if` statements in shipping view code, and
    /// the obvious second way, auditing a sub-element rather than the whole
    /// app, rests on an API this track has not confirmed exists.
    ///
    /// Neither is needed. ``SettingsPlaceholder`` already has three states
    /// and the app already has buttons that walk between them, so one run can
    /// audit four screens that differ in known ways:
    ///
    /// | Screen | Draws | Panel? |
    /// |---|---|---|
    /// | `A-phones` | the pairing screen — the known eight | **yes** |
    /// | `B-group` | title, sub, a headline, four section buttons, `all settings` | no |
    /// | `C-no-panel` | `Printers & scanners`: title, sub, headline, sub, `not built yet`, a back button | no |
    /// | `D-index` | title, sub, five group buttons | no |
    ///
    /// `C` is the one that matters. It is the **same `SettingsPlaceholder`
    /// state** as the pairing screen — `.section`, so the same headline, the
    /// same `sub`, the same back button — with `PhonesAndBrowsersPanel`
    /// swapped for the words `not built yet`. So:
    ///
    ///  - `C` near eight ⇒ the findings are `SettingsPlaceholder`'s own, and
    ///    the no-scroll hypothesis in `STUB-HOUSE-FINDINGS-01.md` is the
    ///    live one: that `VStack` has no `ScrollView` in any state.
    ///  - `C` near zero ⇒ they are the panel's, and the nested `ScrollView`
    ///    is where to look rather than the missing one.
    ///  - `B` and `D` then say whether what is left is the title and `sub`
    ///    every state shares, or the buttons.
    ///
    /// **Deliberately not an equality assertion.** Three of these four counts
    /// are unknown — that is the point of running it — so this asserts only
    /// the baseline, which ``knownPairingIssues`` already pins, and otherwise
    /// prints. A diagnostic that fails on an unknown it was written to
    /// discover tells you nothing you did not put there.
    @MainActor
    func testWhereTheEightDynamicTypeFindingsLive() throws {
        let app = launch(paired: false)
        let link = app.buttons[ShellStringsUnderTest.pairLink]
        XCTAssertTrue(
            link.waitForExistence(timeout: 20),
            "no pairing link on Home — the unpaired header did not draw"
        )
        // No `#available` check: `IPHONEOS_DEPLOYMENT_TARGET` is 18.0
        // (`project.yml:40`), so ``auditIssues``' `@available(iOS 17.0, *)` is
        // already satisfied. The `if #available` blocks elsewhere in this file
        // are vestigial and this one would be too.
        var counts: [(String, Int)] = []

        /// Audit whatever is on screen now, photograph it, and record the
        /// count. The screenshot and the tree are what attribute a count to a
        /// screen afterwards — a number on its own would leave the next
        /// session guessing which screen it came from, which is the mistake
        /// this file's header is about.
        func step(_ name: String) throws {
            evidence(app, "50-bisect-\(name)")
            let found = try auditIssues(app, "bisect-\(name)")
            counts.append((name, found.count))
            print("BISECT [\(name)] \(found.count) issue(s)")
            for line in found { print("BISECT [\(name)]   \(line)") }
        }

        // **Home, still unpaired, before the tap.** The first run of this
        // bisect returned 8 for all four Settings states — an index with a
        // title, a sub and five group buttons scored the same as a section
        // with a headline, a sub, `not built yet` and a back button. A count
        // that does not move when the entire content of the screen changes is
        // not a count of that content, and the uncontrolled difference was
        // sitting in `launch(paired: false)`: every state this test visits is
        // in the **unpaired** app, where Home's clean audit is a paired one.
        //
        // So this step is the control that should have been here first. Eight
        // here and the findings are the unpaired chrome's, and Settings never
        // had anything to do with them.
        try step("0-home-unpaired")

        link.tap()
        XCTAssertTrue(link.waitForNonExistence(timeout: 10), "the pairing link did nothing")

        try step("A-phones")

        // `.section` → `.group`, via the button the audit itself flagged for
        // clipping. `place.up` is what it calls, so this is the app's own
        // navigation and not a relaunch into a different state.
        let backToRooms = app.buttons[ShellStringsUnderTest.backToRooms]
        XCTAssertTrue(backToRooms.waitForExistence(timeout: 10), "no back button on the pairing screen")
        backToRooms.tap()
        let printers = app.buttons[ShellStringsUnderTest.printersSection]
        XCTAssertTrue(printers.waitForExistence(timeout: 10), "tapping back did not reach the group list")
        try step("B-group")

        // The same `.section` shape as the pairing screen, with `not built
        // yet` where the panel was.
        printers.tap()
        XCTAssertTrue(
            app.staticTexts[ShellStringsUnderTest.notBuilt].waitForExistence(timeout: 10),
            "Printers & scanners did not draw its placeholder"
        )
        try step("C-no-panel")

        // Back up one and out to the index.
        let backAgain = app.buttons[ShellStringsUnderTest.backToRooms]
        XCTAssertTrue(backAgain.waitForExistence(timeout: 10), "no back button on the printers section")
        backAgain.tap()
        let allSettings = app.buttons[ShellStringsUnderTest.allSettings]
        XCTAssertTrue(allSettings.waitForExistence(timeout: 10), "no all-settings button on the group list")
        allSettings.tap()
        XCTAssertTrue(
            app.buttons[ShellStringsUnderTest.roomsGroup].waitForExistence(timeout: 10),
            "all settings did not reach the index"
        )
        try step("D-index")

        print("BISECT SUMMARY " + counts.map { "\($0.0)=\($0.1)" }.joined(separator: " "))

        // The one count that is already known. **By name**, not by position:
        // this used to read `counts.first`, and adding a step before the
        // pairing screen would have silently re-pointed the only assertion in
        // the test at a different screen.
        let baseline = counts.first { $0.0 == "A-phones" }?.1
        XCTAssertEqual(
            baseline,
            Self.knownPairingIssues,
            "the pairing screen audited \(baseline.map(String.init) ?? "nothing"), not the "
                + "known \(Self.knownPairingIssues) — every other count in this run is "
                + "measured against a baseline that has changed"
        )
    }
    /// **Are the eight elements, or are they sizes?** One per larger category.
    ///
    /// Everything about the eight says they are not a count of broken views.
    /// They do not move when the screen's whole content changes — an index of
    /// seven elements and a panel of forty both score 8 — every one carries
    /// the identical sentence, and `element` is nil for all of them where it
    /// is populated for every contrast and hit-area finding this track has
    /// seen.
    ///
    /// There are twelve content size categories. From `large`, the ones
    /// **above** it are XL, XXL, XXXL and AX1-AX5 — exactly **eight**. So the
    /// hypothesis is that Apple reports this finding once per larger size the
    /// screen fails at, and the screen is one thing failing eight times
    /// rather than eight things failing once.
    ///
    /// That is falsifiable, which is the point of it:
    ///
    /// | Launch size | Larger categories | Predicts |
    /// |---|---|---|
    /// | `S` | M, L, XL, XXL, XXXL, AX1-AX5 | **10** |
    /// | `large` | XL, XXL, XXXL, AX1-AX5 | **8** |
    /// | AX5 | none | **0** |
    ///
    /// If the counts come back 10/8/0 the eight were never eight defects and
    /// `knownPairingIssues` is a count of *sizes*, which changes what fixing
    /// them means. If they come back 8/8/8 the hypothesis is dead and the
    /// number is a cap.
    @MainActor
    func testWhetherTheEightAreSizesRatherThanElements() throws {
        var counts: [(String, Int)] = []
        for (label, mode) in [
            ("S", "UICTContentSizeCategoryS"),
            ("large", "large"),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let app = launch(mode: mode, paired: false)
            let link = app.buttons[ShellStringsUnderTest.pairLink]
            XCTAssertTrue(
                link.waitForExistence(timeout: 20),
                "no pairing link on Home at \(label) — the unpaired header did not draw"
            )
            link.tap()
            XCTAssertTrue(
                link.waitForNonExistence(timeout: 10),
                "the pairing link did nothing at \(label)"
            )
            evidence(app, "60-sizes-\(label)")
            let found = try auditIssues(app, "sizes-\(label)")
            let dynamic = found.filter { $0.hasPrefix("Dynamic Type") }
            counts.append((label, dynamic.count))
            print("SIZES [\(label)] dynamic=\(dynamic.count) total=\(found.count)")
            for line in found { print("SIZES [\(label)]   \(line)") }
            app.terminate()
        }
        print("SIZES SUMMARY " + counts.map { "\($0.0)=\($0.1)" }.joined(separator: " "))

        // Printed, not asserted. All three numbers are the unknown here, and
        // the prediction is the thing under test — an assertion would only
        // restate it.
        XCTAssertEqual(counts.count, 3, "a launch did not get far enough to audit")
    }


    /// **Is it the screen, or is it being pushed?** The last difference left.
    ///
    /// What is known after two bisects: paired Home audits 0, unpaired Home
    /// audits 0 now the pair link is 44 points, the local shell audits 0, and
    /// **every** `SettingsPlaceholder` state audits 8 — the index, the group
    /// list, a section with no panel, and the pairing screen alike. The eight
    /// do not move when the content changes completely, so they are not the
    /// content. They are not one-per-larger-type-size either: launching at S,
    /// large and AX5 gave 8, 8 and 7 against a prediction of 10, 8 and 0.
    ///
    /// One difference remains. Home and the local shell are **roots** of the
    /// `NavigationStack`; every screen that audits 8 is a screen that was
    /// **pushed** onto it. A pushed destination gets the stack's own
    /// navigation bar, which is where ``WorkspaceView/stack`` already found
    /// one layout bug — the back button and our breadcrumb drawn on top of
    /// each other, at ordinary type size.
    ///
    /// This pushes a route with no Settings in it at all. `music` is the
    /// phone bar's second tab and ``ShellModel/navigate(_:)`` appends it to
    /// `path`, so it is pushed exactly as Settings is, while sharing none of
    /// its views.
    ///
    ///  - `music` near 8 ⇒ the eight belong to the **push**, and Settings was
    ///    never involved. `knownPairingIssues` is then a property of every
    ///    pushed screen in the app, which is a much larger finding than a
    ///    card that needs a `fixedSize`.
    ///  - `music` at 0 ⇒ it is Settings' own chrome after all, and what is
    ///    left is the two lines every state draws, `settingsTitle` and
    ///    `settingsSub`.
    @MainActor
    func testWhetherAPushedScreenIsWhatCarriesTheEight() throws {
        let app = launch()
        XCTAssertTrue(
            houseName(in: app).waitForExistence(timeout: 20),
            "Home did not draw against the stub house"
        )
        evidence(app, "70-push-root")
        let root = try auditIssues(app, "push-root")
        print("PUSH [root] \(root.count)")

        // **`Music`, not `music`.** The tab's accessibility label is
        // ``Route/label`` — the raw value, capitalised — while the caption
        // drawn under the icon is the lower-cased one. The tree says so:
        // `Button … label: 'Music'` holding `StaticText … label: 'music'`.
        // The first run of this test asked for the caption and found no
        // button.
        let tab = app.buttons[ShellStringsUnderTest.musicTab]
        XCTAssertTrue(tab.waitForExistence(timeout: 10), "no Music tab in the phone bar")
        tab.tap()
        // The breadcrumb, **not** Home's absence. Home is the stack's root and
        // stays in the hierarchy under a pushed screen, so
        // `waitForNonExistence` on its content is not a test of having left
        // it. The top bar's leading text is rebuilt from the current route.
        XCTAssertTrue(
            app.staticTexts[ShellStringsUnderTest.musicCrumb].waitForExistence(timeout: 10),
            "the Music tab did not push — the breadcrumb still shows Home"
        )
        evidence(app, "70-push-music")
        let pushed = try auditIssues(app, "push-music")
        print("PUSH [music] \(pushed.count)")
        for line in pushed { print("PUSH [music]   \(line)") }
        print("PUSH SUMMARY root=\(root.count) music=\(pushed.count)")

        // Home as a root is the one number already pinned, and it is the
        // control for the comparison rather than the thing under test.
        XCTAssertEqual(
            root.count,
            Self.knownHomeIssues,
            "Home audited \(root.count), not the known \(Self.knownHomeIssues) — "
                + "the pushed count is being read against a baseline that moved"
        )
    }

}

/// Strings this file matches on, kept together and named so that a change in
/// the app that breaks a query is a one-line fix here rather than a hunt.
///
/// Deliberately **not** `@testable import DomovoiKit`: a UI test drives the
/// app through the same surface a person does, and a query that silently
/// followed a renamed string would stop testing what it claims to.
private enum ShellStringsUnderTest {
    static let pairLink = "pair this phone for live updates and controls"

    /// ``ShellPill/accessibilityLabel`` for a saved server, which is what the
    /// pill button answers to in the tree.
    static let pillLabel = "switch server"

    static let allSettings = "all settings"

    /// ``ShellStrings/backTo(_:)`` for the group every section the bisect
    /// visits belongs to.
    static let backToRooms = "back to Rooms & devices"

    static let roomsGroup = "Rooms & devices"

    /// A section in the same group as `Phones & browsers` with **no screen**,
    /// which is what makes it the control.
    static let printersSection = "Printers & scanners"

    static let notBuilt = "not built yet"

    /// The phone bar tab's accessibility label (``Route/label``), which is
    /// capitalised. The lower-cased `music` in the tree is the caption drawn
    /// under the icon, and querying that finds no button.
    static let musicTab = "Music"

    /// ``ShellTopBar``'s leading text once the Music route is pushed.
    static let musicCrumb = "domovoi / music"
}
