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

    override func setUp() {
        continueAfterFailure = false
    }

    private func launch(
        mode: String = "large",
        paired: Bool = true,
        port: Int = 6601
    ) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-domovoi-server", "127.0.0.1:\(port)"]
        if paired {
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
            print("""
                AUDIT [\(name)] type=\(issue.auditType) \(issue.compactDescription)
                  detail: \(issue.detailedDescription)
                  element: \(issue.element?.debugDescription ?? "nil")
                """)
            return true
        }
        return found
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
            XCTAssertTrue(
                found.isEmpty,
                "Apple's audit has \(found.count) against Home: "
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
    /// ### It also audits, and that is the point of auditing it
    ///
    /// Home has a "Contrast failed" that nothing names: `issue.element` is
    /// nil, the detail is one sentence, and every text on Home measures
    /// 5.31:1 or better off the screenshot. Two eliminations have already
    /// been wrong about it.
    ///
    /// This screen and Home share the top bar and the phone bar and share
    /// nothing else — no live dot, none of Home's scrolling content. So the
    /// same issue here puts it in the chrome, and its absence here puts it in
    /// Home's own content. That narrows it without guessing, which is more
    /// than the audit itself offers.
    @MainActor
    func testThePairingScreenAndItsButtons() throws {
        let app = launch(paired: false)
        let link = app.buttons[ShellStringsUnderTest.pairLink]
        XCTAssertTrue(
            link.waitForExistence(timeout: 20),
            "no pairing link on Home — the unpaired header did not draw"
        )
        link.tap()
        // Proof that the tap went somewhere, rather than a screenshot of the
        // screen we started on: the link belongs to Home and Home is gone.
        let left = link.waitForNonExistence(timeout: 10)
        evidence(app, "30-pairing")
        if #available(iOS 17.0, *) {
            let found = try auditIssues(app, "pairing")
            XCTAssertTrue(
                found.isEmpty,
                "Apple's audit has \(found.count) against the pairing screen: "
                    + found.joined(separator: " | ")
            )
        }
        XCTAssertTrue(left, "the pairing link did nothing — still on Home")
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
}
