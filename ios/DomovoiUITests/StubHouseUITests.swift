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
    /// **Eight false positives, measured as such.** The platform floor every
    /// pushed screen in this app sits on.
    ///
    /// This was `knownPairingIssues`, and the name was wrong for three
    /// sessions: the Settings index, the group list, a section with no panel,
    /// the token card and a pushed `music` all audit 8, so it was never about
    /// pairing. ``testWhichPieceOfAPushedScreenCarriesTheEight`` found where
    /// they live — four in ``ShellTopBar`` and four in ``PhoneBarView``, one
    /// per text leaf — and ``testTheBarsTextGrowsOnAPushedScreen`` found that
    /// every one of them scales:
    ///
    /// ```
    /// SCALE GREW Chat 13.3->48.0 | Home 13.3->48.0 | Music 13.3->48.0
    ///            Videos 13.3->48.0 | crumb 15.7->52.7
    /// SCALE STUCK
    /// ```
    ///
    /// A 3.6x growth between `large` and AX5, on the very leaves Apple says
    /// "User will not be able to change the font size of". The detail line is
    /// **false** about these elements, and `element: nil` on all eight is the
    /// audit reporting what it could not measure as what it found broken. The
    /// same eight leaves on Home, as the stack's root, audit 0.
    ///
    /// ### Why this is a floor and not a suppression
    ///
    /// It is asserted by **signature**, not by total (``floorAndRest(_:)``):
    /// a ninth of these fails the suite, and so does one finding of any other
    /// kind, or one of these that arrives *with* an element — which is what a
    /// real Dynamic Type defect would look like. Pinning the total alone would
    /// have let a new contrast finding in unnoticed the moment one of the
    /// eight went away, which is exactly what `knownPairingIssues` was doing.
    private static let knownPushedBarFloor = 8

    /// Seconds the seeded push waits for the root to draw
    /// (``AuditStrip/pushDelay``).
    private static let pushDelay: Double = 12

    /// **The companion houses, one per failure mode.** `stub_run.sh` starts
    /// these beside the primary house on 6601; a test picks the state it
    /// wants by port rather than by a mode it cannot set from here. All
    /// inside 6600-6699, which is the range the stub itself enforces and the
    /// only range this track may bind.
    private static let throttledPort = 6602
    private static let roomsDownPort = 6603
    private static let silentSocketPort = 6604
    private static let unpairedPort = 6605

    override func setUp() {
        continueAfterFailure = false
    }

    /// `server: false` saves nothing, which is the local-media shell — no
    /// workspace, no phone bar, and the same ``ShellTopBar``. It is the only
    /// way from here to audit the chrome without Home's content under it.
    @MainActor
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

    /// The headlines the audit is known to report with **no element**, which
    /// is the audit saying it had no measurement rather than naming a view.
    ///
    /// Two wordings, and they are the same finding at two type sizes. At
    /// `large` a pushed screen reports eight "partially unsupported"; at AX5
    /// the same screen reports seven "unsupported" and no "partially" at all
    /// (measured 2026-10-30 across seven states). Both carry
    /// `element: nil` and the detail "User will not be able to change the
    /// font size of this element", which
    /// ``testTheBarsTextGrowsOnAPushedScreen()`` measured to be false of
    /// every leaf in those bars — 13.3 to 48.0 points, a 3.6x growth.
    ///
    /// A headline **not** in this set, even with no element, lands in `rest`
    /// and fails its caller. That is deliberate: the one anonymous
    /// `Contrast failed` this file has seen outside a modal is unattributed
    /// and unexplained, and a classifier that swallowed "anything the audit
    /// could not attribute" would have hidden it.
    private static let knownAnonymousHeadlines: Set<String> = [
        "Dynamic Type font sizes are partially unsupported",
        "Dynamic Type font sizes are unsupported",
    ]

    /// Split an audit's findings into the measured floor and everything else.
    ///
    /// The floor is a finding with no element **and** one of the two headlines
    /// in ``knownAnonymousHeadlines``. Everything else lands in `rest`, which
    /// every caller asserts is empty: a finding Apple can attribute to an
    /// element is a defect to fix — that is how the modal bug was found, by
    /// two `Contrast failed` that named the views behind a dialog — and an
    /// anonymous finding of an unrecognised kind is an unknown, not a floor.
    private static func floorAndRest(_ found: [String]) -> (floor: Int, rest: [String]) {
        func isFloor(_ line: String) -> Bool {
            guard line.hasSuffix("| element: nil") else { return false }
            return Self.knownAnonymousHeadlines.contains { line.hasPrefix($0) }
        }
        return (found.filter(isFloor).count, found.filter { !isFloor($0) })
    }

    /// The house's name in the header, which is the one thing on this screen
    /// only a *completed read* can produce: `HomeConfig.name(of:)` draws
    /// `bot_name` when it is non-blank and the literal `domovoi` otherwise,
    /// so `domovoi` on a server called `stub house` means the config read
    /// did not land — whatever the network did.
    @MainActor
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
    /// ``knownPushedBarFloor`` has always counted, which the bisect's own step
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
    @MainActor
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
    @MainActor
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
    @MainActor
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
    @MainActor
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
    /// **two screens and compare**, which is how every finding on this track
    /// that Apple would not attribute was eventually placed.
    ///
    /// **`issue.element` is populated, and this comment used to say it was
    /// not.** It claimed "Apple names no element, `issue.element` is nil" as a
    /// general fact; it is nil for exactly one class of finding — the eight of
    /// ``knownPushedBarFloor``, where the audit had no measurement to report —
    /// and is named for every contrast and hit-area finding this file has
    /// seen. The belief came from a log filter that kept the first line of a
    /// multi-line `print` and dropped the rest, and it survived long enough to
    /// become a method. ``floorAndRest(_:)`` now depends on the difference
    /// being real, which is why the element is in the returned string and not
    /// only in the log.
    @MainActor
    @available(iOS 17.0, *)
    private func auditIssues(_ app: XCUIApplication, _ name: String) throws -> [String] {
        let wasContinuing = continueAfterFailure
        continueAfterFailure = true
        defer { continueAfterFailure = wasContinuing }
        var found: [String] = []
        try app.performAccessibilityAudit { issue in
            // **The element is part of the record, not just the log.** The
            // eight are told apart from a real Dynamic Type defect by having
            // no element (``knownPushedBarFloor``), and a classifier that
            // reads only the two descriptions cannot see that difference.
            found.append(
                "\(issue.compactDescription): \(issue.detailedDescription)"
                    + " | element: \(Self.oneLine(issue.element))"
            )
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

    /// The element frame a finding carries, as ``oneLine(_:)`` wrote it.
    ///
    /// `CGRect.description` is `(x, y, w, h)`, so this is a parse of this
    /// file's own output rather than of Apple's. It exists for one
    /// classification, in ``testTheVideosGridAudits``: a `Contrast failed`
    /// against an element the **scroll view's own bottom edge** is cutting in
    /// half is not a colour defect, and the only way to tell that from a real
    /// one is where the element is.
    private static func frame(in line: String) -> CGRect? {
        guard let opened = line.range(of: "frame=(") else { return nil }
        let rest = line[opened.upperBound...]
        guard let closed = rest.firstIndex(of: ")") else { return nil }
        let numbers = rest[..<closed]
            .split(separator: ",")
            .compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
        guard numbers.count == 4 else { return nil }
        return CGRect(x: numbers[0], y: numbers[1], width: numbers[2], height: numbers[3])
    }

    /// Clear the stub house's recently-played list, from the test process.
    ///
    /// The stub is one process for a whole run and the app is relaunched per
    /// case, so **watched state crosses tests**: whichever case plays a video
    /// leaves a strip on the screen of every case that runs after it, in
    /// alphabetical order. That is how `testWatchingSomethingPutsItInThe…`
    /// came to fail on its first assertion — the strip was already there,
    /// put there by `testAVideoPlaysInTheFeed` a minute earlier.
    ///
    /// The Simulator shares the Mac's loopback, which is how the app reaches
    /// the stub, so the test process can reach it too.
    @MainActor
    private func clearWatched(port: Int = 6601) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/reset-watched")!)
        request.httpMethod = "POST"
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("{}".utf8)
        let answered = expectation(description: "the stub cleared its watched list")
        var code = -1
        URLSession.shared.dataTask(with: request) { _, response, _ in
            code = (response as? HTTPURLResponse)?.statusCode ?? -1
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        XCTAssertEqual(200, code, "the stub did not clear its watched list")
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
    /// where the socket behaves like the server it stands in for. `stub_run.sh` now starts a house per
    /// mode, each on its own port, so this *is* pinnable from here — see
    /// ``testTheStatesTheAuditHadNeverSeen()``, which audits against the
    /// `unpaired` house on 6605. The assertions below hold under either, which is the point —
    /// the mode decides what the *picture* shows, and whether a socket's own
    /// 403 can raise the pairing screen (map §11.6) is only answerable
    /// there.
    ///
    /// It asserted nothing until it was looked at: `waitForExistence` on
    /// `staticTexts.firstMatch` is true of every screen the app can draw,
    /// the local-media placeholder included, so this case could not fail.
    @MainActor
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
    /// which ``knownPushedBarFloor`` says what is known about; the other
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
            let split = Self.floorAndRest(found)
            XCTAssertTrue(
                split.rest.isEmpty,
                "Apple's audit has findings against the pairing screen beyond the "
                    + "measured floor of \(Self.knownPushedBarFloor): "
                    + split.rest.joined(separator: " / ")
            )
            XCTAssertEqual(
                split.floor,
                Self.knownPushedBarFloor,
                "Apple's audit has \(found.count) against the pairing screen, "
                    + "not the \(Self.knownPushedBarFloor) known: "
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
    /// the baseline, which ``knownPushedBarFloor`` already pins, and otherwise
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
            Self.knownPushedBarFloor,
            "the pairing screen audited \(baseline.map(String.init) ?? "nothing"), not the "
                + "known \(Self.knownPushedBarFloor) — every other count in this run is "
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
    /// `knownPushedBarFloor` is a count of *sizes*, which changes what fixing
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
    ///    never involved. `knownPushedBarFloor` is then a property of every
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
            // `none` is kept as the documented anomaly, not as a control: a
            // **seeded** push measures 0 with the destination fully on screen,
            // while the identical push by tap measures 8. Nothing explains
            // that yet, so every variant below arrives by a tap instead.
            "none", "tap", "tapAfterHomeAudit",
            "topBar", "phoneBar", "navBar", "plainRoot", "content", "lazyGrid",
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
            // **Everything but `none` arrives by tap**, because that is the
            // only arrival that has ever reported the eight. Which element
            // gets tapped depends on what the variant took away:
            //
            //  - the phone bar's `Music` tab, normally;
            //  - one of Home's everything tiles when the **bar itself** is
            //    the piece removed (the tiles are
            //    ``ShellModel/everything``, which is unaffected by the bar
            //    not being drawn);
            //  - the plain root's own button when **Home** is the piece
            //    removed, which is why that root has one.
            let arrivesByTap = variant != "none"
            let app = arrivesByTap
                ? launch(strip: ["tap", "tapAfterHomeAudit"].contains(variant) ? nil : variant)
                : launch(strip: nil, push: "Music", pushDelay: Self.pushDelay)
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
                let opener: XCUIElement
                switch variant {
                case "phoneBar": opener = app.buttons[ShellStringsUnderTest.filesTile]
                case "plainRoot": opener = app.buttons[ShellStringsUnderTest.plainRootPush]
                default: opener = app.buttons[ShellStringsUnderTest.musicTab]
                }
                guard opener.waitForExistence(timeout: 20) else {
                    // The tree, not a sentence about what is missing — this
                    // file has paid six runs for that lesson once already.
                    print("STRIP [\(variant)] NO OPENER — tree follows")
                    print(app.debugDescription)
                    app.terminate()
                    continue
                }
                opener.tap()
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
        // **`tap`, not `none`.** The seeded arrival reports 0 for reasons
        // nothing has explained, so it cannot be the baseline the others are
        // read against.
        let control = counts.first { $0.0 == "tap" }?.1
        XCTAssertEqual(
            control,
            Self.knownPushedBarFloor,
            "a pushed screen with the whole shell around it audited "
                + "\(control.map(String.init) ?? "nothing"), not the known "
                + "\(Self.knownPushedBarFloor) — every other count in this run, "
                + "`lazyGrid` above all, is a difference from a baseline that "
                + "has moved"
        )
    }

    /// Any element with this exact label, whatever kind it is.
    ///
    /// The markers below are drawn variously as a `Text`, a `Label` and a
    /// button, and which of those a query has to ask for is an implementation
    /// detail of the view rather than anything a person experiences. Asking
    /// `descendants(matching: .any)` means a marker that moves from a `Text`
    /// to a `Label` does not read as a screen that never drew.
    @MainActor
    private func anything(_ app: XCUIApplication, labelled label: String) -> XCUIElement {
        app.descendants(matching: .any)
            .matching(NSPredicate(format: "label == %@", label))
            .firstMatch
    }

    /// **Every state in this app the audit had never been pointed at.**
    ///
    /// The findings this file is built on all came from the two screens it
    /// happened to audit. An inventory turned up seven states that no audit
    /// had ever rendered, for three different reasons:
    ///
    /// | State | Why it had never been audited |
    /// |---|---|
    /// | Home at AX5 | ``testHomeAtTheLargestTypeSize()`` measures the pill's height and Home's `minY` and **never calls the audit** |
    /// | the auto-raised `pair this phone` screen | every test taps `not now` or the pair link straight past it |
    /// | the hoisted ``ForgetServerCard`` | reachable only by tapping `forget` in the panel, which nothing did |
    /// | Home with rooms down | `stub_run.sh` started **one** house, so a mode other than the run's was unreachable |
    /// | the `.waiting` token card | same — it needs a 429, which only the `throttled` house serves |
    /// | the `.notRecognised` token card | same — it needs a 401 *to a phone holding a token* |
    /// | Home on a silent socket | same |
    ///
    /// The last four are what the companion houses in `stub_run.sh` are for.
    ///
    /// Everything is audited at **AX5**, not `large`: `large` is the size the
    /// audited screens were already green at, and every real finding on this
    /// track so far — the 62.7×314.3 pill, the attention card's wrapped rows
    /// — only exists at the accessibility sizes.
    ///
    /// **Asserts arrival, prints findings.** Whether each state draws at all
    /// is a claim worth failing on: a `rooms-down` house that produces no
    /// `couldn't load rooms` card is a bug whatever the audit says. The
    /// finding counts are unknown — that is the point of the run — and a
    /// diagnostic that fails on an unknown it was written to discover tells
    /// you nothing you did not put there. They are pinned in a second pass,
    /// once each one has been read and either fixed or explained.
    @MainActor
    func testTheStatesTheAuditHadNeverSeen() throws {
        var unreached: [String] = []
        var findings: [String: [String]] = [:]
        var floors: [String: Int] = [:]

        /// Audit whatever is on screen, under a name, having first waited for
        /// the one element that says this state is really the one on screen.
        @MainActor
        func sweep(
            _ name: String,
            _ app: XCUIApplication,
            marker: XCUIElement,
            timeout: TimeInterval = 25
        ) throws {
            let arrived = marker.waitForExistence(timeout: timeout)
            evidence(app, "40-\(name)")
            if !arrived {
                unreached.append(name)
                print("SWEEP [\(name)] NEVER ARRIVED — audited anyway, tree above")
            }
            guard #available(iOS 17.0, *) else { return }
            let split = Self.floorAndRest(try auditIssues(app, name))
            floors[name] = split.floor
            findings[name] = split.rest
            print("SWEEP [\(name)] floor=\(split.floor) other=\(split.rest.count)")
            for line in split.rest { print("SWEEP [\(name)]   \(line)") }
        }

        let ax5 = "UICTContentSizeCategoryAccessibilityXXXL"

        // 1. Home, at the size nothing had audited it at.
        let home = launch(mode: ax5)
        try sweep("home-ax5", home, marker: houseName(in: home))
        home.terminate()

        // 2. An unpaired phone with no token, at AX5.
        //
        //    **This was `pairing-raised`, and the expectation was wrong.** It
        //    waited for ``ShellStringsUnderTest/pairTitle`` on the theory that
        //    a phone with no token has the pairing screen raised over it. It
        //    does not: the tree shows Home, drawn from the open `/api/config`,
        //    with `couldn't load rooms` where the device tier was refused and
        //    the `pair this phone for live updates and controls` link in it.
        //    That is the right behaviour — a phone that has never paired can
        //    still see its house — and the pairing screen belongs to a
        //    *refused* token, which is `token-not-recognised` below.
        let unpairedHome = launch(mode: ax5, paired: false)
        try sweep(
            "unpaired-home",
            unpairedHome,
            marker: anything(unpairedHome, labelled: ShellStringsUnderTest.pairLink)
        )
        unpairedHome.terminate()

        // 3. The forget dialog, hoisted to the Settings screen's root. Its
        //    own doc comment argues it is reachable at every type size
        //    because `DialogCard` is a `ScrollView`; this is the first time
        //    anything has checked that at AX5.
        let forgetting = launch(mode: ax5, paired: false)
        XCTAssertTrue(
            reachPhonesAndBrowsers(forgetting),
            "never reached Settings → Phones & browsers, so the forget dialog "
                + "could not be opened"
        )
        let forgetControl = forgetting.buttons[ShellStringsUnderTest.forget]
        if forgetControl.waitForExistence(timeout: 10) {
            // Once. The dialog's destructive button carries the same label.
            forgetControl.tap()
        } else {
            print("SWEEP no `forget` control in the panel — tree follows")
            print(forgetting.debugDescription)
        }
        try sweep(
            "forget-card",
            forgetting,
            marker: anything(forgetting, labelled: ShellStringsUnderTest.forgetServerTitle)
        )
        forgetting.terminate()

        // 4-7. The houses `stub_run.sh` now starts beside the primary one.
        let waiting = launch(mode: ax5, port: Self.throttledPort)
        try sweep(
            "token-waiting",
            waiting,
            marker: anything(waiting, labelled: ShellStringsUnderTest.tokenWaitingTitle)
        )
        waiting.terminate()

        // ### Twice, and the second pass is an experiment
        //
        // This state is the only one of seven to report a `Contrast failed`,
        // on every pass, and two explanations have already died:
        //
        //  1. *the timers' room chip, dimmed because the rooms read failed* —
        //     `home-ax5` draws a genuinely offline room's chip dimmed and
        //     reports nothing, and the dim is gone now anyway;
        //  2. *something unique to this state* — a tree diff says there is
        //     nothing in it the clean states do not also draw.
        //
        // What killed both was looking at the picture. The element the audit
        // finally named is `pasta`, a timer's **title** — no dim, no colour of
        // its own — and in the screenshot it is **straddling the bottom edge
        // of the scroll viewport**, half of it under the phone bar. It is
        // flagged here and not on `home-ax5` because there the rooms list sits
        // above the timers and pushes that row right off the screen instead of
        // half on it.
        //
        // So the hypothesis is now about the **edge**, not the element: a text
        // that is partly rendered gets a contrast reading of a partly rendered
        // thing. The second pass swipes the card fully into view before
        // auditing. If the finding disappears, or lands on whatever text is at
        // the edge *then*, it belongs to the viewport and not to this app.
        //
        // That one of the two passes named `pasta` and the other named nothing
        // at all, in the same run, is the same instability pointing the same
        // way.
        for pass in ["rooms-down", "rooms-down-scrolled"] {
            let roomsDown = launch(mode: ax5, port: Self.roomsDownPort)
            if pass.hasSuffix("scrolled") {
                _ = anything(roomsDown, labelled: ShellStringsUnderTest.roomsFailed)
                    .waitForExistence(timeout: 25)
                roomsDown.swipeUp()
                usleep(2_000_000)
            }
            try sweep(
                pass,
                roomsDown,
                marker: anything(roomsDown, labelled: ShellStringsUnderTest.roomsFailed)
            )
            roomsDown.terminate()
        }

        let silent = launch(mode: ax5, port: Self.silentSocketPort)
        try sweep("silent-socket", silent, marker: houseName(in: silent))
        silent.terminate()

        // A token the house refuses: `paired: true` against the `unpaired`
        // house, which is the only combination that produces a 401 to a
        // phone that is holding a token.
        let refused = launch(mode: ax5, port: Self.unpairedPort)
        try sweep(
            "token-not-recognised",
            refused,
            marker: anything(refused, labelled: ShellStringsUnderTest.tokenNotRecognisedTitle)
        )
        refused.terminate()

        let order = [
            "home-ax5", "unpaired-home", "forget-card", "token-waiting",
            "rooms-down", "rooms-down-scrolled", "silent-socket",
            "token-not-recognised",
        ]
        // `floor+other` per state, in one grep-able line. Nested `$0`s in a
        // string interpolation are how a summary line becomes a compile error,
        // so this is a plain loop.
        var summary: [String] = []
        for name in order {
            let floor = floors[name].map(\.description) ?? "-"
            summary.append("\(name)=\(floor)+\(findings[name]?.count ?? 0)")
        }
        print("SWEEP SUMMARY " + summary.joined(separator: " "))
        XCTAssertTrue(
            unreached.isEmpty,
            "these states never drew the element that identifies them, so the "
                + "audit above is of something else: "
                + unreached.joined(separator: ", ")
        )
        // Everything Apple could attribute, and every anonymous finding of a
        // kind this file has not measured. Empty is the claim everywhere but
        // the two `rooms-down` passes, where **one** `Contrast failed` is
        // allowed while the viewport-edge question above is open.
        //
        // Allowed by **state and by kind and by count**, not by a total: a
        // second finding there, a finding of any other kind there, or one of
        // these anywhere else all fail. Pinning the total is what let a real
        // finding hide behind `knownPairingIssues` for three sessions.
        var rest: [String] = []
        for name in order {
            let found = findings[name] ?? []
            guard name.hasPrefix("rooms-down") else {
                rest += found.map { "\(name): \($0)" }
                continue
            }
            let contrast = found.filter { $0.hasPrefix("Contrast failed") }
            rest += found.filter { !$0.hasPrefix("Contrast failed") }.map { "\(name): \($0)" }
            if contrast.count > 1 {
                rest += contrast.dropFirst().map { "\(name) (a second one): \($0)" }
            }
            print("SWEEP [\(name)] contrast findings: \(contrast.count)")
        }
        XCTAssertTrue(
            rest.isEmpty,
            "Apple's audit has findings outside the measured floor: "
                + rest.joined(separator: "  //  ")
        )
    }

    /// **Is the AX5 wording the same findings as the `large` wording?**
    ///
    /// At `large` a pushed screen reports eight "Dynamic Type font sizes are
    /// **partially** unsupported", and
    /// ``testWhichPieceOfAPushedScreenCarriesTheEight()`` placed them: four
    /// in ``ShellTopBar``, four in ``PhoneBarView``, one per text leaf, by
    /// stripping each bar and watching the count fall to four.
    ///
    /// At AX5 the same screen reports **seven**, and the word `partially` is
    /// gone. That is the headline Apple uses for an element with no scaling
    /// at all, so taking the two for the same finding is an assumption — and
    /// this file has already written up one conclusion from an unproven
    /// assumption about these findings and had to correct it twice.
    ///
    /// So it is measured the same way the eight were. The claim is the
    /// **algebra**: stripping one bar leaves the other bar's findings, so
    ///
    /// ```
    /// topBar + phoneBar == none
    /// ```
    ///
    /// which held exactly at `large` (4 + 4 = 8). If it holds at AX5 the
    /// seven live in the same two bars and ``knownAnonymousHeadlines`` is
    /// right to hold both wordings. If it does not, AX5 has findings of its
    /// own somewhere else and the floor is hiding them.
    ///
    /// Arrival is by **tap**, never by the seeded push: a tap waits for
    /// quiescence, and the seeded push measures 0 on a destination that is
    /// provably on screen for reasons still unexplained (see
    /// ``testWhichPieceOfAPushedScreenCarriesTheEight()``).
    @MainActor
    func testTheAX5WordingIsTheSameBarFindings() throws {
        let ax5 = "UICTContentSizeCategoryAccessibilityXXXL"
        let variants = ["none", "topBar", "phoneBar"]
        var counts: [String: Int] = [:]

        for variant in variants {
            let app = launch(mode: ax5, strip: variant == "none" ? nil : variant)
            // With the phone bar gone there is no `Music` tab to tap, so the
            // push comes from one of Home's everything tiles instead — the
            // same substitution the `large` bisect makes.
            let opener = variant == "phoneBar"
                ? app.buttons[ShellStringsUnderTest.filesTile]
                : app.buttons[ShellStringsUnderTest.musicTab]
            guard opener.waitForExistence(timeout: 25) else {
                print("AX5STRIP [\(variant)] NO OPENER — tree follows")
                print(app.debugDescription)
                app.terminate()
                continue
            }
            opener.tap()

            // Only the destination can satisfy this: the `not built yet`
            // placeholder or the navigation bar's own Back button. The
            // breadcrumb cannot — it is mounted outside the `NavigationStack`
            // and flips before the destination lays out, which is the third
            // timing confound this file paid for.
            let placeholder = app.descendants(matching: .staticText)
                .matching(NSPredicate(format: "label CONTAINS %@", ShellStringsUnderTest.notBuilt))
                .firstMatch
            let back = app.navigationBars.buttons.firstMatch
            let deadline = Date().addingTimeInterval(25)
            var arrived = false
            while Date() < deadline {
                if placeholder.exists || back.exists { arrived = true; break }
                usleep(300_000)
            }
            usleep(3_000_000)  // the transition itself, which neither marker waits out
            print("AX5STRIP [\(variant)] arrived: \(arrived)")
            evidence(app, "41-ax5-\(variant)")

            if #available(iOS 17.0, *) {
                let split = Self.floorAndRest(try auditIssues(app, "ax5-\(variant)"))
                counts[variant] = split.floor
                XCTAssertTrue(
                    split.rest.isEmpty,
                    "a pushed screen at AX5 with `\(variant)` stripped has findings "
                        + "outside the floor: " + split.rest.joined(separator: " / ")
                )
            }
            app.terminate()
        }

        var summary: [String] = []
        for variant in variants {
            summary.append("\(variant)=\(counts[variant].map(\.description) ?? "-")")
        }
        print("AX5STRIP SUMMARY " + summary.joined(separator: " "))

        guard let whole = counts["none"],
              let withoutTop = counts["topBar"],
              let withoutPhone = counts["phoneBar"]
        else {
            XCTFail("a variant never arrived, so the algebra cannot be checked")
            return
        }
        XCTAssertEqual(
            withoutTop + withoutPhone,
            whole,
            "stripping the top bar leaves \(withoutTop) and stripping the phone "
                + "bar leaves \(withoutPhone), which do not add up to the "
                + "\(whole) a whole pushed screen reports at AX5. The AX5 wording "
                + "is then NOT the same findings as the `large` wording, and "
                + "`knownAnonymousHeadlines` is holding one that belongs somewhere "
                + "else"
        )
        XCTAssertLessThan(withoutTop, whole, "stripping the top bar changed nothing")
        XCTAssertLessThan(withoutPhone, whole, "stripping the phone bar changed nothing")
    }

    /// **Do the eight flagged leaves actually scale?** The question the audit
    /// will not answer, measured.
    ///
    /// ``testWhichPieceOfAPushedScreenCarriesTheEight`` settled where they
    /// live. With every variant arriving by a tap:
    ///
    /// ```
    /// STRIP SUMMARY tap=8 topBar=4 phoneBar=4 navBar=8 plainRoot=8 content=8 lazyGrid=8
    /// ```
    ///
    /// Four in ``ShellTopBar`` and four in ``PhoneBarView``, and the tree of a
    /// pushed screen has exactly four text leaves in each — the breadcrumb,
    /// the pill's address, `unverified` and `live` above; `home`, `music`,
    /// `videos` and `chat` below. **One finding per text leaf**, by
    /// arithmetic rather than by guess.
    ///
    /// `plainRoot` at 8 is what kills the covered-root reading that three
    /// write-ups of this had as its leading hypothesis: a root of three
    /// `Text`s and a button carries all eight, so Home was never underneath
    /// it in any sense that mattered. `content` and `navBar` at 8 rule out the
    /// destination and the navigation bar, as before.
    ///
    /// ### What is left is a contradiction, and this test is its referee
    ///
    /// The identical eight leaves are on **Home**, where the audit reports 0 —
    /// `tapAfterHomeAudit` measures that in the same process that then reports
    /// 8. Same views, same text styles, same frames; 0 as a root and 8 when a
    /// destination is pushed onto the `NavigationStack` beside them. Nothing
    /// in either bar uses a font Dynamic Type cannot change: one grep across
    /// the app for `Font.system(size:)`, `UIFont.systemFont` and
    /// `Font.custom` finds nothing in them, and there is no
    /// `.dynamicTypeSize` cap anywhere.
    ///
    /// So either the detail line is true and these leaves cannot be resized,
    /// or it is false and the audit is reporting what it could not *verify*
    /// as what it found to be broken — which would also explain `element: nil`
    /// on all eight, where every contrast and hit-area finding on this track
    /// names its element.
    ///
    /// That is not a matter of opinion. The text either gets bigger or it
    /// does not, and the accessibility tree reports the frames. This launches
    /// a pushed screen at `large` and again at AX5 and compares the height of
    /// each flagged leaf it can address by name.
    ///
    /// **If the heights grow, the eight are a false positive** and the right
    /// answer is a documented floor rather than a fix — and this test, not a
    /// comment, is what keeps that claim honest.
    @MainActor
    func testTheBarsTextGrowsOnAPushedScreen() throws {
        /// The flagged leaves this test can name. Five of the eight: the
        /// breadcrumb and the four tab captions.
        ///
        /// The pill's `127.0.0.1:6601`, `unverified` and `live` are left out
        /// deliberately — the first two are state the stub decides and the
        /// third's label carries leading whitespace, so a query on them is a
        /// query on the fixture rather than on the font. Five leaves settle
        /// the question as well as eight would.
        func heights(_ app: XCUIApplication) -> [String: CGFloat] {
            var out: [String: CGFloat] = [:]
            let crumb = app.staticTexts[ShellStringsUnderTest.musicCrumb]
            if crumb.exists { out["crumb"] = crumb.frame.height }
            for tab in ["Home", "Music", "Videos", "Chat"] {
                // Scoped **through the tab's button**, because `music` is also
                // the placeholder's own heading on this very screen and an
                // app-wide query would measure whichever it found first.
                let caption = app.buttons[tab].staticTexts.firstMatch
                if caption.exists { out[tab] = caption.frame.height }
            }
            return out
        }

        var measured: [String: [String: CGFloat]] = [:]
        for (label, mode) in [
            ("large", "large"),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let app = launch(mode: mode)
            XCTAssertTrue(
                houseName(in: app).waitForExistence(timeout: 20),
                "Home did not draw against the stub house at \(label)"
            )
            let tab = app.buttons[ShellStringsUnderTest.musicTab]
            XCTAssertTrue(tab.waitForExistence(timeout: 10), "no Music tab at \(label)")
            tab.tap()
            XCTAssertTrue(
                app.navigationBars.buttons.firstMatch.waitForExistence(timeout: 10),
                "the Music tab did not push at \(label)"
            )
            usleep(3_000_000)
            evidence(app, "90-scale-\(label)")
            measured[label] = heights(app)
            for (leaf, h) in heights(app).sorted(by: { $0.key < $1.key }) {
                print("SCALE [\(label)] \(leaf) height=\(h)")
            }
            app.terminate()
        }

        guard let small = measured["large"], let big = measured["AX5"] else {
            return XCTFail("a launch did not reach a pushed screen")
        }
        var grew: [String] = []
        var stuck: [String] = []
        for (leaf, h) in small.sorted(by: { $0.key < $1.key }) {
            guard let tall = big[leaf] else {
                print("SCALE [compare] \(leaf) MISSING at AX5")
                continue
            }
            if tall > h {
                grew.append("\(leaf) \(h)→\(tall)")
            } else {
                stuck.append("\(leaf) \(h)→\(tall)")
            }
        }
        print("SCALE GREW " + grew.joined(separator: " | "))
        print("SCALE STUCK " + stuck.joined(separator: " | "))

        XCTAssertFalse(small.isEmpty, "no flagged leaf could be addressed at large")
        XCTAssertTrue(
            stuck.isEmpty,
            "Apple's audit says these leaves cannot be resized, and these did not grow "
                + "between large and AX5: \(stuck.joined(separator: ", ")) — which makes the "
                + "eight a real defect in \(ShellStringsUnderTest.musicCrumb)'s bars rather "
                + "than a floor, and `knownPushedBarFloor` is hiding it"
        )
    }

    // MARK: - Videos

    /// Open Videos and wait for its grid.
    ///
    /// The grid is a paged read and the strip is another, so the count line
    /// is the thing to wait on: it appears only once the list has answered
    /// (``VideosModel/countText`` is nil before that), which makes it the one
    /// signal that means "the page landed" rather than "the screen exists".
    ///
    /// ### Why the predicate is `BEGINSWITH "showing "` and not `videos`
    ///
    /// The first version of this waited on any text **containing** "videos",
    /// and it came back satisfied in five seconds — seven seconds before the
    /// push was due. What it had found was the phone bar's own `videos` tab
    /// label, which is on screen from the first frame of Home. So `landed`
    /// said yes about a screen the test had never reached, the screenshot was
    /// of Home, and the only thing that failed was the count assertion
    /// underneath, which read as "the numbers are wrong" rather than "this is
    /// the wrong screen". A landing check has to match something that cannot
    /// exist until the thing has landed; "showing " is that, because
    /// ``Pager/countText(shown:total:noun:)`` is the only place in the app
    /// that writes it and ``VideosModel/countText`` is nil until the list has
    /// answered.
    @MainActor
    private func openVideos(
        _ mode: String = "large",
        delay: Double? = nil
    ) -> (app: XCUIApplication, landed: Bool) {
        let app = launch(mode: mode, push: "Videos", pushDelay: delay ?? Self.pushDelay)
        let count = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", "showing "))
            .firstMatch
        return (app, count.waitForExistence(timeout: 40))
    }

    /// The grid: a heading per library with the **server's** count, poster
    /// tiles, and the count line.
    ///
    /// The stub serves fifty-four videos in `Movies` and six on a `USB
    /// stick`, so page one holds fifty of the sixty and the two headings must
    /// read 54 and 6 — not 44 and 6, which is what the rows that loaded would
    /// say. That difference is the whole reason the answer is decoded twice
    /// (``VideosModel`` and ``VideoLibraryTotals``), and it is invisible to
    /// anything but a real paged read.
    @MainActor
    func testVideosDrawsItsGridWithTheServersCounts() throws {
        let (app, landed) = openVideos()
        evidence(app, "40-videos-grid")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")
        XCTAssertTrue(landed, "the videos list never answered — no count line drew")

        XCTAssertTrue(
            app.staticTexts["showing 50 of 60 videos"].exists,
            "the count line is not the server's numbers"
        )
        XCTAssertTrue(app.staticTexts["Movies · 54"].exists, "the Movies heading is not the server's total")
        // **No USB heading yet**, and that is the other half of the same
        // property: page one is fifty rows and all fifty are in `Movies`, so
        // the second library has nothing loaded — and a library with no rows
        // gets no heading (``VideoGrid/entries(_:)``). A heading reading
        // `USB stick · 6` over an empty grid is what the totals decode would
        // produce if it were applied to libraries rather than to rows, which
        // is the mistake worth pinning here.
        XCTAssertFalse(
            app.staticTexts["USB stick · 6"].exists,
            "a library with nothing loaded was given a heading"
        )
        // A tile is a button labelled with what the video is called, which is
        // also how everything below taps one.
        XCTAssertTrue(
            app.buttons["a long one.mp4"].exists,
            "no tile for the one video the stub can actually play"
        )
    }

    /// The same grid at the largest accessibility type size.
    ///
    /// The size that found the clipped text on the first screen of this
    /// track. Videos has three places text can be squeezed flat: the
    /// heading's `label · count`, the resume line under a strip row, and a
    /// tile's caption, all of which have `fixedSize` for that reason.
    @MainActor
    func testVideosAtTheLargestTypeSize() throws {
        let (app, landed) = openVideos("UICTContentSizeCategoryAccessibilityXXXL")
        evidence(app, "41-videos-ax5")
        XCTAssertTrue(landed, "the videos list never answered at AX5")
    }

    /// Apple's audit over the grid.
    @MainActor
    @available(iOS 17.0, *)
    func testTheVideosGridAudits() throws {
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")
        let found = try auditIssues(app, "videos-grid")
        evidence(app, "42-videos-grid-audited")
        let (floor, rest) = Self.floorAndRest(found)
        // The outer scroll view, which is the tallest one on screen — the
        // other is the strip's, lying on its side.
        let page = app.scrollViews.allElementsBoundByIndex
            .max(by: { $0.frame.height < $1.frame.height })
        let bottom = page?.frame.maxY ?? 0
        XCTAssertGreaterThan(bottom, 0, "no scroll view to take a bottom edge from")
        let unexplained = rest.filter { line in
            // A Dynamic Type headline **with** an element, which this file
            // believed for three sessions was the signature of a real
            // defect. It is not: ``testTheVideosGridTextGrowsAtAX5`` measures
            // these very leaves and they grow 3.6x and more. The belief came
            // from never having seen one — the eight on a pushed screen all
            // arrive anonymous — and the measurement is what settles it, in
            // both directions. The grown-by check is the test; this is the
            // classification that makes room for it.
            if Self.knownAnonymousHeadlines.contains(where: { line.hasPrefix($0) }) {
                return false
            }
            // A contrast finding against an element the scroll view's own
            // bottom edge cuts in half. Half an element has no pixels to
            // read, which is what the audit is reporting, and every list on
            // this phone has a row straddling that edge — including the one
            // in the picture beside this run, where `02 holiday 2026.mp4`
            // spans 768.5 to 782.8 against a content area ending at 770.
            // Scrolling one row makes it a different row's turn.
            if line.hasPrefix("Contrast failed"), let box = Self.frame(in: line),
               box.minY < bottom, box.maxY > bottom {
                return false
            }
            return true
        }
        XCTAssertEqual(
            [],
            unexplained,
            "the grid has findings neither measurement nor the viewport edge explains"
        )
        // **Zero, not eight.** ``knownPushedBarFloor`` is the Settings and
        // Music figure and this screen does not produce it: every finding
        // here arrives with an element attached. Asserting 8 would be
        // asserting another screen's shape, and asserting nothing would let
        // an anonymous finding of an unrecognised kind through — the thing
        // the floor exists to catch. So this pins what this screen does:
        // nothing the audit cannot attribute.
        XCTAssertEqual(0, floor, "an anonymous finding appeared on the grid")
    }

    /// **The measurement the audit's Dynamic Type finding claims to be
    /// about.**
    ///
    /// Apple reported five of the grid's own leaves as "User will not be able
    /// to change the font size of this element", each with the element
    /// attached. This launches the same screen at `large` and at AX5 and
    /// measures them. Anything that really is stuck fails here, and nothing
    /// is excluded from the audit on the strength of a belief.
    ///
    /// A leaf that is not on screen at AX5 is skipped rather than failed: at
    /// that size the count line alone is 105 points tall and the grid's first
    /// tile is most of the way down the window.
    @MainActor
    func testTheVideosGridTextGrowsAtAX5() throws {
        func heights(_ app: XCUIApplication) -> [String: CGFloat] {
            var out: [String: CGFloat] = [:]
            for (leaf, label) in [
                ("count", "showing 50 of 60 videos"),
                ("heading", "Movies · 54"),
                ("strip-heading", "recently played"),
                ("caption", "a long one.mp4"),
                ("resume", "0:10 / 0:40"),
            ] {
                let text = app.staticTexts[label].firstMatch
                if text.exists { out[leaf] = text.frame.height }
            }
            return out
        }

        var measured: [String: [String: CGFloat]] = [:]
        for (label, mode) in [
            ("large", "large"),
            ("AX5", "UICTContentSizeCategoryAccessibilityXXXL"),
        ] {
            let (app, landed) = openVideos(mode)
            XCTAssertTrue(landed, "the videos list never answered at \(label)")
            usleep(2_000_000)
            evidence(app, "4A-videos-scale-\(label)")
            measured[label] = heights(app)
            for (leaf, height) in heights(app).sorted(by: { $0.key < $1.key }) {
                print("SCALE [\(label)] \(leaf) height=\(height)")
            }
            app.terminate()
        }

        let small = measured["large"] ?? [:]
        let big = measured["AX5"] ?? [:]
        var compared = 0
        var stuck: [String] = []
        for (leaf, height) in small.sorted(by: { $0.key < $1.key }) {
            guard let grown = big[leaf] else { continue }
            compared += 1
            if grown < height * 2 { stuck.append("\(leaf) \(height)->\(grown)") }
        }
        XCTAssertEqual([], stuck, "a leaf Apple flagged really does not scale")
        XCTAssertGreaterThanOrEqual(
            compared,
            2,
            "nothing could be compared, so this proved nothing: \(small) / \(big)"
        )
    }

    /// **A real video, decoded by a real player.**
    ///
    /// This is the part no package test can reach: `FeedEngine` talks to
    /// AVFoundation and nothing else in this batch does. The stream URL is
    /// built here for the first time against something that checks it — the
    /// household token rides in the **query** (``VideoStream``), and the stub
    /// refuses the videos routes without a token in either place, so a
    /// picture of a playing video is also proof the token arrived.
    ///
    /// Two screenshots three seconds apart. The generated video draws a white
    /// column at "how far through this is", so the pair of pictures shows
    /// whether it is actually playing rather than parked on frame one — which
    /// a single screenshot of a video cannot tell you.
    @MainActor
    func testAVideoPlaysInTheFeed() throws {
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")

        app.buttons["a long one.mp4"].firstMatch.tap()
        let counter = app.staticTexts["1 / 50"]
        let opened = counter.waitForExistence(timeout: 20)
        evidence(app, "43-feed-opened")
        XCTAssertTrue(opened, "the feed did not open on the video that was tapped")
        XCTAssertTrue(app.buttons["close"].exists, "the feed has no way out")

        Thread.sleep(forTimeInterval: 3)
        evidence(app, "44-feed-three-seconds-later")
        // Still the same page: three seconds of playing must not advance the
        // pager, which would mean the scroll view had decided to settle
        // somewhere else on its own.
        XCTAssertTrue(counter.exists, "the feed moved off the page by itself")
    }

    /// The details sheet, filled in by the player rather than the library.
    ///
    /// `480 × 270` is not in any JSON the stub serves: the resolution, the
    /// bitrate, the frame rate and the codec are read off the decoded track
    /// (`FeedEngine.takeFacts`), which is `await`ed since iOS 16. So this
    /// case is the only thing on either side of the project that can tell
    /// whether that load lands — and whether it lands on the **right video**,
    /// which is why `takeFacts` compares the item after the await.
    @MainActor
    func testTheDetailsSheetSaysWhatThePlayerMeasured() throws {
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")
        app.buttons["a long one.mp4"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["1 / 50"].waitForExistence(timeout: 20), "the feed did not open")

        // A long press on the **page**, by coordinate.
        //
        // `app.staticTexts["1 / 50"].press(...)` was the first attempt and it
        // opened nothing: the counter is in the chrome drawn over the pager,
        // so the press never reached the view carrying
        // `onLongPressGesture`. A normalized coordinate presses the pixels,
        // which is what a finger does.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4))
            .press(forDuration: 1.0)
        let details = app.buttons["details"]
        let opened = details.waitForExistence(timeout: 10)
        evidence(app, "45-feed-options")
        XCTAssertTrue(opened, "the long press opened no options sheet")
        details.tap()

        let resolution = app.staticTexts["480 × 270"]
        let measured = resolution.waitForExistence(timeout: 20)
        evidence(app, "45-feed-details")
        XCTAssertTrue(
            measured,
            "the details sheet never showed the stream's real resolution — "
                + "the awaited track load did not land, or landed on another video"
        )
        XCTAssertTrue(app.staticTexts["duration"].exists, "no duration row")
        XCTAssertTrue(app.staticTexts["size"].exists, "no size row")
    }

    /// The session queue, which is a playlist and **not** a list of files to
    /// delete.
    @MainActor
    func testThePlaylistSheetListsTheQueue() throws {
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")
        app.buttons["a long one.mp4"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["1 / 50"].waitForExistence(timeout: 20), "the feed did not open")

        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4))
            .press(forDuration: 1.0)
        let playlist = app.buttons["playlist"]
        let opened = playlist.waitForExistence(timeout: 10)
        evidence(app, "46-feed-options")
        XCTAssertTrue(opened, "no options sheet")
        playlist.tap()

        let heading = app.staticTexts["playlist · 50 videos"]
        let listed = heading.waitForExistence(timeout: 10)
        evidence(app, "46-feed-playlist")
        XCTAssertTrue(listed, "the playlist sheet does not say how many are queued")
        XCTAssertTrue(
            app.buttons["remove from playlist"].firstMatch.exists,
            "no way to drop a video from the queue"
        )
    }

    /// A video the server will not stream says so, and says to swipe on.
    ///
    /// Most of the stub's library has no file behind it, on purpose: a 404
    /// from `/stream` is a state a real house will not produce on demand, and
    /// it is the difference between the feed's two failure messages — this
    /// one, and the refusal a phone with no proof gets (``VideoStream``),
    /// which must **not** say "swipe for the next one" because the next one
    /// will not play either.
    @MainActor
    func testAVideoWithNoFileSaysItCannotPlay() throws {
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")

        app.buttons["01 she said \"no\".mp4"].firstMatch.tap()
        // **Which page the feed is on, before anything about the message.**
        // This is the second tile, so the feed must open on page two — and
        // the first run of this case is how the pager's seeded position bug
        // was found (``VideoFeedView/init``): the chrome said `2 / 50`, the
        // player held this video, and the page on screen was the first
        // video's poster, one viewport above. Everything below asked about a
        // message on a page nobody could see.
        let counter = app.staticTexts["2 / 50"]
        let onThePage = counter.waitForExistence(timeout: 20)
        let message = app.staticTexts["can't play this video"]
        let said = message.waitForExistence(timeout: 30)
        evidence(app, "47-feed-cannot-play")
        XCTAssertTrue(onThePage, "the feed did not open on the video that was tapped")
        XCTAssertTrue(said, "a stream that 404s drew no message")
        XCTAssertTrue(
            app.staticTexts["swipe for the next one"].exists,
            "it did not say what to do about it"
        )
    }

    /// **The resume round trip, end to end.**
    ///
    /// Watch a few seconds, close the feed, and the video is in "recently
    /// played" with where it got to. That path crosses everything this batch
    /// built: the five-second tick and the save on close
    /// (``VideoFeedModel/tick(positionSec:durationSec:)``), the POST body
    /// (``VideoPath/saveBody``), the strip's own read, and
    /// ``VideosModel/feedClosed(on:)`` re-reading it. The stub remembers what
    /// it was sent, so this is the real thing and not a canned answer.
    @MainActor
    func testWatchingSomethingPutsItInTheRecentlyPlayedStrip() throws {
        // The stub outlives this case and `testAVideoPlaysInTheFeed` runs
        // before it (alphabetically), so without this the strip is already
        // there and the assertion below fails about the wrong thing.
        clearWatched()
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")
        XCTAssertFalse(
            app.staticTexts["recently played"].exists,
            "the strip is there before anything has been watched"
        )

        app.buttons["a long one.mp4"].firstMatch.tap()
        XCTAssertTrue(app.staticTexts["1 / 50"].waitForExistence(timeout: 20), "the feed did not open")
        // Past the five-second floor, so the saved position is one the app
        // will actually resume from (``FeedRules/resumeFloorSec``).
        Thread.sleep(forTimeInterval: 8)
        app.buttons["close"].tap()

        let strip = app.staticTexts["recently played"]
        let appeared = strip.waitForExistence(timeout: 20)
        evidence(app, "48-videos-recently-played")
        XCTAssertTrue(
            appeared,
            "watching a video and closing the feed did not put it in the strip"
        )
    }

    /// Filtering, and the strip that is not part of a search result.
    @MainActor
    func testFilteringNarrowsTheGrid() throws {
        let (app, landed) = openVideos()
        XCTAssertTrue(landed, "the videos list never answered")

        let field = app.textFields["filter…"]
        XCTAssertTrue(field.waitForExistence(timeout: 10), "no filter field")
        field.tap()
        field.typeText("portrait")

        let narrowed = app.staticTexts["showing 1 of 1 videos"]
        let answered = narrowed.waitForExistence(timeout: 20)
        evidence(app, "49-videos-filtered")
        XCTAssertTrue(answered, "the filter did not narrow the grid to the one match")
        XCTAssertTrue(app.staticTexts["USB stick · 1"].exists, "the heading kept a stale count")
        XCTAssertFalse(
            app.staticTexts["recently played"].exists,
            "the strip is drawn over a search result"
        )
    }
    // MARK: - Files

    /// Open Files and wait for its first page.
    ///
    /// The count line again, for ``openVideos``' reason: `showing 50 of 56
    /// items` cannot exist until the folder has answered, where the word
    /// `Files` is on screen from the first frame as a phone-bar tab.
    @MainActor
    private func openFiles(
        _ mode: String = "large",
        delay: Double? = nil
    ) -> (app: XCUIApplication, landed: Bool) {
        let app = launch(mode: mode, push: "Files", pushDelay: delay ?? Self.pushDelay)
        let count = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label BEGINSWITH %@", "showing "))
            .firstMatch
        return (app, count.waitForExistence(timeout: 40))
    }

    /// Pick a library from the header's menu.
    @MainActor
    private func pickLibrary(_ app: XCUIApplication, _ label: String) -> Bool {
        let picker = app.buttons["choose library"]
        guard picker.waitForExistence(timeout: 20) else { return false }
        picker.tap()
        let item = app.buttons[label].firstMatch
        guard item.waitForExistence(timeout: 10) else { return false }
        item.tap()
        return true
    }

    /// Tap a row's `•••` and return whether its menu opened.
    ///
    /// By its own label and **not** by taking the second button called
    /// `Albums`: the first tree from this screen had two buttons with that
    /// one label on a single row, which is ambiguous to a reader and to a
    /// query. The menu says whose it is now.
    @MainActor
    private func openRowMenu(_ app: XCUIApplication, _ name: String) -> Bool {
        let menu = app.buttons["more for \"\(name)\""].firstMatch
        guard menu.waitForExistence(timeout: 10) else { return false }
        menu.tap()
        return true
    }

    /// The first page of the first library, with the server's own count.
    ///
    /// The stub's `Music` root holds fifty-six things — two folders and
    /// fifty-four tracks — so page one is fifty of fifty-six and the folders
    /// come first because the server put them there.
    @MainActor
    func testFilesDrawsTheFirstLibraryAndItsFirstPage() throws {
        let (app, landed) = openFiles()
        evidence(app, "50-files-list")
        XCTAssertFalse(fellBackToLocalMedia(in: app), "the shell gave up on the server")
        XCTAssertTrue(landed, "the folder never answered — no count line drew")

        XCTAssertTrue(
            app.staticTexts["showing 50 of 56 items"].exists,
            "the count line is not the server's numbers"
        )
        XCTAssertTrue(app.buttons["Albums"].exists, "no row for the first folder")
        XCTAssertTrue(app.buttons["track 00.mp3"].exists, "no row for the first file")
        // **No Delete control anywhere**, which is the point:
        // `/api/files/delete` needs an admin session and this app has none.
        XCTAssertFalse(app.buttons["delete"].exists, "a phone cannot delete and must not offer to")
        XCTAssertFalse(app.buttons["Delete…"].exists, "Android's own label for it is not here either")
    }

    /// Where deleting is done, said at the foot of the list.
    ///
    /// Asserted here rather than in the test above, and with a scroll: at the
    /// foot of a fifty-row lazy list the line is not merely off screen, it is
    /// not built, so a query for it fails whatever the code says. That is the
    /// honest cost of this placement — a line above every folder would be
    /// clutter for everyone to answer a question few people ask, and the row
    /// menus cannot hold prose (`whereDeletingLives` says why).
    @MainActor
    func testTheListSaysWhereDeletingIsDone() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")
        XCTAssertTrue(app.buttons["notes.md"].waitForExistence(timeout: 20), "Documents never listed")

        let line = app.staticTexts["deleting is on the dashboard — a phone can't sign in as an admin"]
        for _ in 0..<6 where !line.exists {
            app.swipeUp()
        }
        evidence(app, "66-files-delete-line")
        XCTAssertTrue(line.exists, "the list never says where deleting is done")
    }

    /// The same screen at the largest accessibility type size.
    @MainActor
    func testFilesAtTheLargestTypeSize() throws {
        let (app, landed) = openFiles("UICTContentSizeCategoryAccessibilityXXXL")
        evidence(app, "51-files-ax5")
        XCTAssertTrue(landed, "the folder never answered at AX5")
    }

    /// Walking into a folder and back up by its crumb.
    @MainActor
    func testWalkingIntoAFolderAndBackUpByItsCrumb() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")

        app.buttons["Albums"].tap()
        let song = app.buttons["a song.mp3"]
        XCTAssertTrue(song.waitForExistence(timeout: 20), "Albums never listed")
        evidence(app, "52-files-in-albums")
        XCTAssertTrue(app.buttons["Albums"].exists, "no crumb for the folder we are in")

        // The library's own name is the root crumb.
        app.buttons["library root"].tap()
        XCTAssertTrue(
            app.buttons["track 00.mp3"].waitForExistence(timeout: 20),
            "the root crumb did not go back to the root"
        )
    }

    /// A document opens in the reader, which says on its face that it cannot
    /// write.
    @MainActor
    func testADocumentReadsAndTheReaderSaysItIsReadOnly() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")

        let notes = app.buttons["notes.md"]
        XCTAssertTrue(notes.waitForExistence(timeout: 20), "Documents never listed")
        notes.tap()
        let readOnly = app.staticTexts["read-only — editing is on the dashboard"]
        XCTAssertTrue(readOnly.waitForExistence(timeout: 20), "the reader never opened")
        evidence(app, "53-files-document")
        XCTAssertTrue(
            app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "the shopping list"))
                .firstMatch.exists,
            "the document's own text is not on screen"
        )
    }

    /// A picture opens in the viewer; an SVG says what it is instead of
    /// drawing nothing.
    @MainActor
    func testAPictureIsShownAndAnSvgSaysItCannotBe() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")

        let photo = app.buttons["photo.jpg"]
        XCTAssertTrue(photo.waitForExistence(timeout: 20), "Documents never listed")
        photo.tap()
        let picture = app.images["photo.jpg"]
        XCTAssertTrue(picture.waitForExistence(timeout: 20), "the picture never drew")
        evidence(app, "54-files-picture")
        app.buttons["close"].firstMatch.tap()

        let svg = app.buttons["logo.svg"]
        XCTAssertTrue(svg.waitForExistence(timeout: 20), "back to the list")
        svg.tap()
        let refusal = app.staticTexts["not a picture this phone can show"]
        XCTAssertTrue(refusal.waitForExistence(timeout: 20), "an undecodable image drew nothing at all")
        evidence(app, "55-files-not-a-picture")
        // The way out that does work is offered right there.
        XCTAssertTrue(app.buttons["save to device"].exists, "no save offered for a file that cannot be shown")
    }

    /// A file over the save cap gets a line saying how big it is and where to
    /// get it — and no control that would refuse.
    @MainActor
    func testAFileTooBigToSaveSaysSoRatherThanOfferingOne() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "Documents"), "the library picker never opened")
        XCTAssertTrue(app.buttons["notes.md"].waitForExistence(timeout: 20), "Documents never listed")
        evidence(app, "56-files-too-big")

        XCTAssertTrue(
            app.staticTexts["too big to save from a phone (4.00 GB) — on the dashboard"].exists,
            "the four-gigabyte row does not say why it cannot be saved here"
        )
    }

    /// A read-only library offers no move, and a drive offers an import.
    @MainActor
    func testWhatEachLibraryOffersOnARow() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")

        XCTAssertTrue(pickLibrary(app, "Comics"), "the library picker never opened")
        XCTAssertTrue(app.buttons["issue 1.cbz"].waitForExistence(timeout: 20), "Comics never listed")
        XCTAssertTrue(openRowMenu(app, "issue 1.cbz"), "the row has no menu")
        evidence(app, "57-files-readonly-menu")
        XCTAssertFalse(app.buttons["move"].exists, "a read-only library offered a move")
        XCTAssertFalse(app.buttons["import into a library"].exists, "a plugin library offered an import")
        app.buttons["save to device"].firstMatch.tap()

        XCTAssertTrue(pickLibrary(app, "USB stick"), "the picker never opened for the drive")
        XCTAssertTrue(app.buttons["holiday.mp4"].waitForExistence(timeout: 20), "the drive never listed")
        XCTAssertTrue(openRowMenu(app, "holiday.mp4"), "the drive's row has no menu")
        evidence(app, "58-files-drive-menu")
        XCTAssertTrue(app.buttons["import into a library"].exists, "a drive did not offer an import")
    }

    /// Moving a file: the destination picker, and what the toast says.
    @MainActor
    func testMovingAFileSaysWhatHappened() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")

        XCTAssertTrue(openRowMenu(app, "track 00.mp3"), "the row has no menu")
        app.buttons["move"].firstMatch.tap()
        let here = app.buttons["move here"]
        XCTAssertTrue(here.waitForExistence(timeout: 20), "the move sheet never opened")
        evidence(app, "59-files-move-sheet")
        // Into a subfolder of the library the file is already in, which is
        // where the picker starts.
        app.buttons["Albums"].firstMatch.tap()
        XCTAssertTrue(here.waitForExistence(timeout: 10), "the picker did not walk into Albums")
        here.tap()

        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label CONTAINS %@", "moved"))
            .firstMatch
        XCTAssertTrue(toast.waitForExistence(timeout: 20), "nothing was said about the move")
        evidence(app, "60-files-moved")
    }

    /// Importing off the drive, which copies on the server and leaves the
    /// drive's folder on screen.
    @MainActor
    func testImportingOffTheDriveSaysWhereItWent() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        XCTAssertTrue(pickLibrary(app, "USB stick"), "the library picker never opened")
        XCTAssertTrue(app.buttons["holiday.mp4"].waitForExistence(timeout: 20), "the drive never listed")

        XCTAssertTrue(openRowMenu(app, "holiday.mp4"), "the drive's row has no menu")
        app.buttons["import into a library"].firstMatch.tap()
        let music = app.buttons["Music"].firstMatch
        XCTAssertTrue(music.waitForExistence(timeout: 20), "the import sheet never opened")
        evidence(app, "61-files-import-sheet")
        music.tap()

        let toast = app.descendants(matching: .staticText)
            .matching(NSPredicate(format: "label CONTAINS %@", "copied"))
            .firstMatch
        XCTAssertTrue(toast.waitForExistence(timeout: 20), "nothing was said about the import")
        evidence(app, "62-files-imported")
        // The drive's own folder is still the one on screen: an import writes
        // into another library and nothing here changed.
        XCTAssertTrue(app.buttons["holiday.mp4"].exists, "the screen left the drive after an import")
    }

    /// A device an admin has blocked: the card above the list, and **no write
    /// controls at all**.
    @MainActor
    func testABlockedDeviceCanLookButIsOfferedNothingToTouch() throws {
        setFilesBlocked(true)
        defer { setFilesBlocked(false) }

        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        evidence(app, "63-files-blocked")
        XCTAssertTrue(
            app.descendants(matching: .staticText)
                .matching(NSPredicate(format: "label CONTAINS %@", "is blocked from changing files"))
                .firstMatch.exists,
            "the blocked card is not on screen"
        )
        XCTAssertTrue(openRowMenu(app, "track 00.mp3"), "the row has no menu")
        evidence(app, "64-files-blocked-menu")
        XCTAssertFalse(app.buttons["move"].exists, "a blocked device was offered a move")
    }

    /// Apple's audit over the Files list.
    @MainActor
    @available(iOS 17.0, *)
    func testTheFilesListAudits() throws {
        let (app, landed) = openFiles()
        XCTAssertTrue(landed, "the folder never answered")
        let found = try auditIssues(app, "files-list")
        evidence(app, "65-files-audited")
        let (floor, rest) = Self.floorAndRest(found)
        print("FILES AUDIT floor=\(floor) rest=\(rest.count)")
        for line in rest { print("FILES AUDIT REST :: \(line)") }
        // The same pin the grid has: a finding Apple can attribute is a
        // defect, and an anonymous one of an unrecognised kind is an unknown
        // rather than a floor.
        XCTAssertEqual([], rest, "the Files list has an audit finding of its own")
        XCTAssertEqual(0, floor, "an anonymous finding appeared on the Files list")
    }

    /// Flip the stub house's files block, from the test process.
    ///
    /// A control route rather than a sixth stub house on a sixth port: it is
    /// one field of one answer (``clearWatched`` says why the test process
    /// can reach the stub at all).
    @MainActor
    private func setFilesBlocked(_ blocked: Bool, port: Int = 6601) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/_test/files-blocked")!)
        request.httpMethod = "POST"
        request.setValue(Self.token, forHTTPHeaderField: "X-Device-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("{\"blocked\":\(blocked)}".utf8)
        let answered = expectation(description: "the stub set its files block")
        var code = -1
        URLSession.shared.dataTask(with: request) { _, response, _ in
            code = (response as? HTTPURLResponse)?.statusCode ?? -1
            answered.fulfill()
        }.resume()
        wait(for: [answered], timeout: 15)
        XCTAssertEqual(200, code, "the stub did not set its files block")
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

    /// An everything-grid tile, which is how the no-phone-bar variant leaves
    /// Home. ``Route/label`` again, so capitalised.
    static let filesTile = "Files"

    /// ``AuditStrip/plainRootPush`` — the one button on the plain root.
    /// Spelled out rather than imported, like every other string here.
    static let plainRootPush = "push music from a plain root"
    /// ``HomeRoomsStrings/couldNotLoadRooms`` — the card Home draws when
    /// `/api/satellites` fails for a reason that is not the token, which is
    /// what the `rooms-down` house serves (503).
    static let roomsFailed = "couldn't load rooms"
    /// ``tokenTroubleTitle(_:)`` for `.waiting`, raised by the `throttled`
    /// house's 429 with a `Retry-After`.
    static let tokenWaitingTitle = "Domovoi asked this phone to wait"
    /// ``tokenTroubleTitle(_:)`` for `.notRecognised`, raised by the
    /// `unpaired` house answering 401 to a phone that *did* send a token.
    static let tokenNotRecognisedTitle = "Domovoi doesn't recognise this phone any more"
    /// ``forgetServerTitle`` — the hoisted ``ForgetServerCard``'s heading.
    static let forgetServerTitle = "forget this server?"
    /// ``ShellStrings/forget`` — the panel's forget control.
    ///
    /// **Also the destructive button inside the dialog it opens**
    /// (``forgetServerConfirm``). Two controls, one label, one of them
    /// irreversible: a query for this is unambiguous only while the dialog is
    /// shut, which is why the sweep taps it once and never again.
    static let forget = "forget"

}
