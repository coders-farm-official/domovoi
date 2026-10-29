import XCTest

/// The Simulator half of the I0 loop: does the app launch, and does a screen
/// come up that a person could read? This is the test `ios/scripts/test.sh`
/// runs over SSH, and the one plan 5.1 expects to be the awkward part —
/// Apple's own advice is that screen-based code from an SSH session wants the
/// build user logged in at the Mac's screen, with a LaunchAgent as the
/// fallback.
///
/// Also the accessibility audit, which is the twin of Android's TalkBack scan
/// (plan 5.1, step 4). It is one line here and stays one line per screen as
/// the app grows.
///
/// Not run yet: there was no Mac when it was written.
final class LaunchUITests: XCTestCase {

    override func setUp() {
        continueAfterFailure = false
    }

    func testTheAppLaunchesAndSaysWhatItIs() {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.staticTexts["Domovoi"].waitForExistence(timeout: 10))
    }

    /// First run, 2026-10-28: it failed, with "Contrast nearly passed" — and
    /// it was right. The finding was in our own text, not SwiftUI's chrome:
    /// the two labels used `.secondary`, which over white is 3.44:1, under
    /// the 4.5:1 body text needs. Fixed in the colour (`Color.fgMuted`), not
    /// by narrowing the audit with `for:`. The audit stays unnarrowed so the
    /// next screen gets the same scrutiny.
    ///
    /// `@MainActor` because `performAccessibilityAudit` is main-actor
    /// isolated; without it Swift 6 warns here and will eventually refuse.
    @MainActor
    func testTheFirstScreenPassesApplesAccessibilityAudit() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.staticTexts["Domovoi"].waitForExistence(timeout: 10))
        try app.performAccessibilityAudit()
    }
}
