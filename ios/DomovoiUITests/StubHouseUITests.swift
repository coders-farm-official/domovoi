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
/// before it was stopped. `-domovoi-server` writes exactly what the picker
/// would have written. See `DomovoiApp.seededStore()`.
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

    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// Home, drawn against a server, at the ordinary type size.
    ///
    /// Asserts something only a working read can produce before taking the
    /// picture, so a screenshot of a blank screen cannot pass for a
    /// screenshot of Home. `stub house` is the stub's `bot_name`, which the
    /// header draws from `/api/config`.
    func testHomeDrawsAgainstAServer() throws {
        let app = launch()
        XCTAssertTrue(
            app.staticTexts["stub house"].waitForExistence(timeout: 20),
            "the header never drew the house's name — Home did not reach the server"
        )
        shot(app, "10-home-large")
        print("=== TREE: HOME ===")
        print(app.debugDescription)
    }

    /// The same screen at the largest accessibility type size.
    ///
    /// This is the size that failed the audit on the first screen in I1-C,
    /// and the one the attention card's wrapped rows were argued about in
    /// I1-D part 2's divergence list.
    func testHomeAtTheLargestTypeSize() throws {
        let app = launch(mode: "UICTContentSizeCategoryAccessibilityXXXL")
        XCTAssertTrue(
            app.staticTexts["stub house"].waitForExistence(timeout: 20),
            "the header never drew the house's name"
        )
        shot(app, "11-home-ax5")
    }

    /// Apple's own audit, on Home rather than on the launch screen.
    func testHomePassesApplesAccessibilityAudit() throws {
        let app = launch()
        XCTAssertTrue(app.staticTexts["stub house"].waitForExistence(timeout: 20))
        if #available(iOS 17.0, *) {
            try app.performAccessibilityAudit { issue in
                // Print every issue rather than only failing on the first:
                // the result bundle names none of them, and finding out
                // which element is at fault took a throwaway test last time.
                print("AUDIT detail=\(issue.detail ?? "nil") element=\(issue.element?.debugDescription ?? "nil")")
                return false
            }
        }
        shot(app, "12-home-audited")
    }

    /// An unpaired phone: the device reads 401 and the pairing screen is
    /// what a person is supposed to see.
    ///
    /// Run against `--mode unpaired`, which also refuses the socket upgrade
    /// with a 403 — so this is the picture that settles whether a socket's
    /// own refusal can raise the pairing screen (map §11.6).
    func testAnUnpairedPhoneIsToldToPair() throws {
        let app = launch(paired: false)
        // Whatever it shows, photograph it: the point of this one is the
        // words, which no test has ever read on a screen.
        _ = app.staticTexts.firstMatch.waitForExistence(timeout: 20)
        shot(app, "20-unpaired")
        print("=== TREE: UNPAIRED ===")
        print(app.debugDescription)
    }
}
