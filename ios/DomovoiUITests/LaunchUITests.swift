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

    /// TODO(verify-at-mac): `performAccessibilityAudit` is iOS 17+ and throws.
    /// If the first run reports findings in SwiftUI's own chrome rather than
    /// ours, narrow the audit with the `for:` option set rather than deleting
    /// it.
    func testTheFirstScreenPassesApplesAccessibilityAudit() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.staticTexts["Domovoi"].waitForExistence(timeout: 10))
        try app.performAccessibilityAudit()
    }
}
