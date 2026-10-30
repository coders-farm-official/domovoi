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
    /// **Zero, and the name was wrong for three sessions.**
    ///
    /// This was `knownPairingIssues = 8`, and it was never about pairing: the
    /// Settings index, the group list, a section with no panel, the token
    /// card and a pushed `music` all audited 8, so it was a property of being
    /// **pushed**. It is 0 now. What took the eight away is in
    /// ``testWhichPieceOfAPushedScreenCarriesTheEight``.
    private static let knownPushedIssues = 8

    /// Seconds the seeded push waits for the root to draw
    /// (``AuditStrip/pushDelay``).
    private static let pushDelay: Double = 12

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
        port: Int = 6601,
        strip: String? = nil,
        push: String? = nil,
        pushDelay: Double? = nil
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = server ? ["-domovoi-server", "127.0.0.1:\(port)"] : []
        if server, paired {
            app.launchArguments += ["-domovoi-token", Self.token]
        }
        // ``AuditStrip``. Both are DEBUG-only on the app's side and read once
        // at launch, so a variant cannot leak into the next case.
        if let strip { app.launchArguments += ["-domovoi-audit-strip", strip] }
        if let push { app.launchArguments += ["-domovoi-audit-push", push] }
        if let pushDelay {
            app.launchArguments += ["-domovoi-audit-push-delay", "\(pushDelay)"]
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

    /// Get to **Settings → Phones & browsers**, where an unpaired phone pastes
    /// its household token, from an unpaired launch.
    ///
    /// ### What this file has been calling "the pairing screen"
    ///
    /// Not the screen titled `pair this phone`. ``HomeHeaderStrings/pairLink``
    /// opens the token card in Settings — `domovoi / settings`,
    /// `Household token`, `Save & reconnect` — and that is what
    /// ``knownPushedIssues`` has always counted, which the bisect's own step
    /// name `A-phones` says out loud. The screen titled `pair this phone`,
    /// with `not now` on it, is a **different** one: an unpaired phone's
    /// device-tier reads come back 403 and the shell raises it by itself
    /// (`ShellModel.swift:503`, map §11.6).
    ///
    /// ### Why a loop and not three lines
    ///
    /// Three cases asserted the link exists, tapped it and carried on. That
    /// only ever passed when the link was reached **before** the auto-raise,
    /// which is a race — and one the everything grid tipped by ceasing to be
    /// a `LazyVGrid`: thirteen tiles drawn eagerly make Home's first frame
    /// dearer. Nothing about the app got worse.
    ///
    /// So when the auto-raise wins, take its own way out and tap `not now`,
    /// which puts the phone back on Home with the link still there. Both
    /// paths end on one screen, so the audit count means the same thing on
    /// every run.
    ///
    /// Six runs were spent before the screen was simply printed, because the
    /// failure said "the unpaired header did not draw" — a claim about
    /// something absent, which cannot report what is present. Four of the
    /// five eliminations were theories about why the link was missing, and it
    /// was never missing.
    @MainActor
    private func reachPhonesAndBrowsers(_ app: XCUIApplication) -> Bool {
        let link = app.buttons[ShellStringsUnderTest.pairLink]
        let notNow = app.buttons[ShellStringsUnderTest.notNow]
        let marker = app.staticTexts[ShellStringsUnderTest.householdToken]
        let deadline = Date().addingTimeInterval(30)
        while Date() < deadline {
            if marker.exists { return true }
            if link.exists {
                link.tap()
            } else if notNow.exists {
                notNow.tap()
            }
            usleep(300_000)
        }
        print("PHONES & BROWSERS NOT REACHED — tree follows")
        print(app.debugDescription)
        return false
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
    /// which ``knownPushedIssues`` says what is known about; the other
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
        // The assertion that replaced "the link did nothing — still on Home",
        // and a stronger one: that we are **on** the screen, by the card
        // that only it draws, however we arrived.
        XCTAssertTrue(
            reachPhonesAndBrowsers(app),
            "never reached Settings → Phones & browsers from an unpaired launch"
        )
        evidence(app, "30-pairing")
        if #available(iOS 17.0, *) {
            let found = try auditIssues(app, "pairing")
            XCTAssertEqual(
                found.count,
                Self.knownPushedIssues,
                "Apple's audit has \(found.count) against the pairing screen, "
                    + "not the \(Self.knownPushedIssues) known: "
                    + found.joined(separator: " | ")
            )
        }
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
    /// the baseline, which ``knownPushedIssues`` already pins, and otherwise
    /// prints. A diagnostic that fails on an unknown it was written to
    /// discover tells you nothing you did not put there.
    @MainActor
    func testWhereTheEightDynamicTypeFindingsLive() throws {
        let app = launch(paired: false)
        let link = app.buttons[ShellStringsUnderTest.pairLink]
        let homeWasAuditable = link.waitForExistence(timeout: 8)
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
        // **Skipped, not failed, when the app got to pairing first.** An
        // unpaired phone's device-tier reads 403 and the shell raises the
        // pairing screen itself, so Home is not always auditable here — and
        // a control step that cannot run is worth a printed line, not a red
        // suite. ``reachPhonesAndBrowsers(_:)`` carries the whole story.
        if homeWasAuditable {
            try step("0-home-unpaired")
            link.tap()
        } else {
            print("BISECT [0-home-unpaired] skipped — the app raised pairing before Home could be audited")
        }
        XCTAssertTrue(reachPhonesAndBrowsers(app), "never reached Phones & browsers")

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
            Self.knownPushedIssues,
            "the pairing screen audited \(baseline.map(String.init) ?? "nothing"), not the "
                + "known \(Self.knownPushedIssues) — every other count in this run is "
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
    /// `knownPushedIssues` is a count of *sizes*, which changes what fixing
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
            XCTAssertTrue(
                reachPhonesAndBrowsers(app),
                "never reached Phones & browsers at \(label)"
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
    ///    never involved. `knownPushedIssues` is then a property of every
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

    /// **Which piece of a pushed screen carries the eight?** The removal
    /// method, mechanised.
    ///
    /// Six theories have died on this. What is known: every **pushed** screen
    /// audits 8 `Dynamic Type font sizes are partially unsupported` and every
    /// **root** audits 0; the count does not move when the content changes
    /// completely (`A-phones`, `B-group`, `C-no-panel`, `D-index` and `music`
    /// are all 8); it is not one-per-larger-type-size (8/8/7 against a
    /// prediction of 10/8/0); and `element` is nil on all eight, so the audit
    /// will not say which view it means.
    ///
    /// Guessing has had its six turns. This takes the pieces away instead,
    /// one per launch, in one build — the way Home's six were finally
    /// explained, by removing the `LazyVGrid` rather than reasoning about it.
    ///
    /// Every variant arrives the same way: ``AuditStrip/seededRoute`` puts
    /// `Music` in ``ShellModel/path`` once the workspace appears, which is the
    /// same push a tab tap makes. It has to be seeded rather than tapped
    /// because one variant removes the bar the tab is in.
    ///
    /// ### The first run measured an empty screen, and said so nowhere
    ///
    /// The prediction was `root` at 0 and every other variant at 8. What came
    /// back was:
    ///
    /// ```
    /// STRIP SUMMARY none=0 topBar=0 phoneBar=0 navBar=0 root=0 content=0 lazyGrid=0
    /// ```
    ///
    /// Zero everywhere, control included — and it meant nothing. The **same
    /// run** of the suite that printed those zeroes also printed
    /// `PUSH SUMMARY root=0 music=8`, `BISECT … A-phones=8 … D-index=8` and
    /// `SIZES S=8 large=8 AX5=7`. One build, one Simulator, the eight plainly
    /// still there, and this harness alone could not see them.
    ///
    /// The difference was **when the push happened**. Every other test waits
    /// for the house name — Home fully drawn against the stub — and then taps
    /// a tab. This one seeded `path` from a `.task` on the first frame and
    /// audited as soon as the breadcrumb appeared, perhaps two seconds in,
    /// with Home's seven sections still empty behind it. A variant harness
    /// whose every cell reads 0 because the screen under test had not drawn
    /// is the viewport confound again, in a new costume: a number that moves
    /// for a reason that is not the one being varied.
    ///
    /// ### What the accident proved anyway
    ///
    /// It is the best evidence on the record for where the eight live. A
    /// pushed screen with Home **not yet drawn** audits 0; the identical
    /// pushed screen with Home drawn behind it audits 8. Nothing about the
    /// pushed screen changed between those two measurements.
    ///
    /// So the eight are Home's own views, audited while **covered** by a push
    /// — mounted, not unloaded. That also explains the detail that made them
    /// unguessable for three sessions: `element: nil` on all eight, where
    /// every contrast and hit-area finding on this track names its element.
    /// A view behind a pushed screen is out of the accessibility tree and has
    /// no element to name.
    ///
    /// ### What is still open, and what each variant now decides
    ///
    /// Home audits **0** as the visible root. The same views audit 8 covered.
    /// So either covering changes something about our views, or SwiftUI cannot
    /// prove Dynamic Type support for anything behind a pushed screen at all —
    /// a platform floor, not a defect. ``AuditStrip/plainRoot`` is the variant
    /// that decides it: a root of three `Text`s and nothing else.
    ///
    /// `pushDelay` is what makes any of these numbers mean something, and each
    /// variant now also prints whether the root drew before the push, because
    /// the confound was invisible in its own log.
    ///
    /// **Printed, not asserted**, apart from the control — which is 8 again,
    /// and is the one number that is known.
    @MainActor
    func testWhichPieceOfAPushedScreenCarriesTheEight() throws {
        var counts: [(String, Int)] = []
        for variant in [
            // The three arrivals, measured against each other before any
            // piece is removed. See the note on `tap` below.
            "none", "tap", "tapAfterHomeAudit",
            "topBar", "phoneBar", "navBar", "root", "plainRoot",
            "content", "lazyGrid",
        ] {
            // **Three ways to arrive, because the delay was not the
            // difference either.** With the push held 12 seconds and
            // `root drew first: true` on every variant that has a Home, this
            // harness still read 0 across the board while
            // ``testWhetherAPushedScreenIsWhatCarriesTheEight`` read
            // `music=8` from the same build. Two differences remain between
            // the two tests, and both are in this test rather than in the
            // app:
            //
            //  - **how the push happens** — a seeded `path` versus a tap on
            //    the phone bar's tab;
            //  - **how many times the app is audited** — this test audits
            //    once, and that one audits Home *first* and the pushed screen
            //    second.
            //
            // The second is the live suspicion. `performAccessibilityAudit`
            // drives the content size category through its range and puts it
            // back; an audit of Home that leaves anything behind would be
            // found by the next audit in the same process, and `music=8` has
            // only ever been measured as a *second* audit. If that is it, the
            // eight on `music` are an artefact of this test file and the
            // question goes back to `SettingsPlaceholder`, which has only
            // ever been measured as a first audit.
            //
            //  - `tap` is `none` arriving the other way, audited once.
            //  - `tapAfterHomeAudit` is the push test exactly: Home audited,
            //    then the tab tapped, then the pushed screen audited.
            let arrivesByTap = variant.hasPrefix("tap")
            let app = arrivesByTap
                ? launch()
                : launch(
                    strip: variant == "none" ? nil : variant,
                    push: "Music",
                    // Long enough for Home's seven sections to land against
                    // the stub, because a push that beats them measures an
                    // empty root and reads 0 for the wrong reason.
                    pushDelay: Self.pushDelay
                )
            // **Recorded, not assumed.** The confound that wasted the first
            // run was invisible in its own log: every count was 0 and nothing
            // said the root had been blank. This prints whether the house name
            // was ever on screen before the push, per variant, so a zero can
            // be read.
            let house = houseName(in: app)
            let drew = house.waitForExistence(timeout: Self.pushDelay + 10)
            print("STRIP [\(variant)] root drew first: \(drew)")

            if variant == "tapAfterHomeAudit" {
                let before = try auditIssues(app, "strip-\(variant)-home")
                print("STRIP [\(variant)] home audited first: \(before.count)")
            }
            if arrivesByTap {
                let tab = app.buttons[ShellStringsUnderTest.musicTab]
                XCTAssertTrue(
                    tab.waitForExistence(timeout: 10),
                    "no Music tab in the phone bar for \(variant)"
                )
                tab.tap()
            }
            // ### The arrival condition, and the third timing confound
            //
            // This waited on the **breadcrumb**, and the breadcrumb is a lie
            // about arrival: ``ShellTopBar`` is mounted *outside* the
            // `NavigationStack` (``WorkspaceView/stack``), so it is rebuilt
            // from `model.navigation.route` the instant `path` changes —
            // before the destination has been laid out, let alone finished
            // animating in. The audit then ran on a screen mid-push and
            // reported 0, and every removal variant inherited that 0.
            //
            // A tap did not have the problem because `XCUIElement.tap()`
            // waits for the app to go quiescent, so the push had settled
            // before the next query ran. That is the whole of the difference
            // between `none=0` and `tap=8` — same destination, same Home
            // behind it, same single audit.
            //
            // So the condition is now something only the **destination**
            // can satisfy:
            //
            //  - the `not built yet` placeholder, which every variant draws
            //    except ``AuditStrip/content``;
            //  - or the navigation bar's `Back` button, which exists in every
            //    variant except ``AuditStrip/navBar``.
            //
            // No variant removes both, so one of the two is always a true
            // statement that the pushed screen is on screen. The settle after
            // it is for the transition itself, which neither marker waits
            // out.
            let placeholder = app.descendants(matching: .staticText)
                .matching(NSPredicate(format: "label CONTAINS %@", ShellStringsUnderTest.notBuilt))
                .firstMatch
            let back = app.navigationBars.buttons.firstMatch
            let deadline = Date().addingTimeInterval(Self.pushDelay + 25)
            var arrived = false
            while Date() < deadline {
                if placeholder.exists || back.exists { arrived = true; break }
                usleep(300_000)
            }
            if arrived {
                // Crude on purpose, and printed so it can be argued with. A
                // push animates for a third of a second; three is a margin
                // over a Simulator under load, and the cost of being wrong
                // the other way is another run of meaningless zeroes.
                usleep(3_000_000)
                print(
                    "STRIP [\(variant)] settled — placeholder: \(placeholder.exists) "
                        + "back: \(back.exists)"
                )
            }
            guard arrived else {
                print("STRIP [\(variant)] NEVER PUSHED — tree follows")
                print(app.debugDescription)
                app.terminate()
                continue
            }
            // Worth saying out loud rather than silently auditing it: with the
            // root stripped, Home's reads never start, and a shell that gives
            // up on the server swaps the whole screen for local media. A count
            // taken from that screen is a count of a different app.
            if fellBackToLocalMedia(in: app) {
                print("STRIP [\(variant)] WARNING — fell back to local media")
            }
            evidence(app, "80-strip-\(variant)")
            let found = try auditIssues(app, "strip-\(variant)")
            let dynamic = found.filter { $0.hasPrefix("Dynamic Type") }
            counts.append((variant, dynamic.count))
            print("STRIP [\(variant)] dynamic=\(dynamic.count) total=\(found.count)")
            for line in found { print("STRIP [\(variant)]   \(line)") }
            app.terminate()
        }
        print("STRIP SUMMARY " + counts.map { "\($0.0)=\($0.1)" }.joined(separator: " "))

        // The control, and only the control. If the whole shell stops
        // auditing 8 on a pushed screen then the five numbers beside it are
        // differences from a baseline that moved, and the run says nothing.
        let control = counts.first { $0.0 == "none" }?.1
        XCTAssertEqual(
            control,
            Self.knownPushedIssues,
            "a pushed screen with the whole shell around it audited "
                + "\(control.map(String.init) ?? "nothing"), not the known "
                + "\(Self.knownPushedIssues) — every other count in this run, "
                + "`lazyGrid` above all, is a difference from a baseline that "
                + "has moved"
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
    /// ``ShellStrings/pairTitle`` — the pairing screen's own heading, which is
    /// how ``reachPhonesAndBrowsers(_:)`` knows it is there without tapping
    /// anything. Deliberately **not** a prefix of ``pairLink``'s matcher:
    /// `XCUIElementQuery`'s subscript is an exact match, so the two cannot be
    /// confused for one another.
    /// ``ShellStrings/pairTitle`` — the **auto-raised** pairing screen's
    /// heading, which is a different screen from where ``pairLink`` goes.
    static let pairTitle = "pair this phone"
    /// ``ShellStrings/notNow`` — the one way off the auto-raised pairing
    /// screen (`ShellModel.swift:503`).
    static let notNow = "not now"
    /// ``ShellStrings/householdTokenCard`` — the card that marks
    /// Settings → Phones & browsers, which is where ``pairLink`` actually
    /// goes and what this file has been calling "the pairing screen".
    static let householdToken = "Household token"

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
