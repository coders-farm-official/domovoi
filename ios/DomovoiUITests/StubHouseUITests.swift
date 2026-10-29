import XCTest

/// Drive the app to a server, so the screens behind one can be looked at.
///
/// Every visual check on this track has been blocked on the same fact: with
/// no server saved the shell is `.local`, so Home, the pairing screens and
/// the token-trouble card are unreachable and a Simulator can only ever
/// photograph the music placeholder. `swift test` cannot see a view, the
/// compiler cannot see a layout, and a review cannot see a cut line — and
/// the *first* screen still failed Apple's accessibility audit on clipped
/// text with 643 tests green.
///
/// The server is `stubhouse.py` on 127.0.0.1:6601 (`design-notes/ios/
/// STUB-HOUSE.md`), which the Simulator reaches as the Mac's own loopback.
/// Nothing here touches a real house.
final class StubHouseUITests: XCTestCase {

    override func setUp() {
        continueAfterFailure = false
    }

    /// The map, not the test: launch, open the connection sheet, and print
    /// what is actually on screen at each step.
    ///
    /// There is not one `accessibilityIdentifier` in the app, so every query
    /// a real test makes has to go through a label a person reads. Guessing
    /// those from the source and iterating on a Simulator is several minutes
    /// a guess; printing the tree once is one run.
    func testPrintTheElementTreeOnTheWayToAServer() throws {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        shot(app, "01-launch")
        print("=== TREE AT LAUNCH ===")
        print(app.debugDescription)

        // The pill in the top bar. In the local shell its label is `connect`.
        let pill = app.buttons["connect"]
        print("=== pill exists: \(pill.exists), hittable: \(pill.exists && pill.isHittable) ===")
        guard pill.waitForExistence(timeout: 10) else {
            print("=== NO `connect` BUTTON. Every button: ===")
            for button in app.buttons.allElementsBoundByIndex {
                print("  button label=\(button.label) id=\(button.identifier)")
            }
            return XCTFail("no connect pill — the tree above says what is there instead")
        }

        pill.tap()
        // The sheet animates in; wait on something inside it rather than sleeping.
        _ = app.textFields.firstMatch.waitForExistence(timeout: 10)
        shot(app, "02-connection-sheet")
        print("=== TREE WITH THE CONNECTION SHEET OPEN ===")
        print(app.debugDescription)

        print("=== every text field ===")
        for field in app.textFields.allElementsBoundByIndex {
            print("  field label=\(field.label) placeholder=\(field.placeholderValue ?? "nil") id=\(field.identifier)")
        }
        print("=== every button ===")
        for button in app.buttons.allElementsBoundByIndex {
            print("  button label=\(button.label) id=\(button.identifier)")
        }
    }

    /// Attach a screenshot that survives the run, so it can be pulled out of
    /// the result bundle afterwards. `.keepAlways` matters: the default
    /// deletes attachments for tests that passed, which is every test whose
    /// picture anyone wants.
    private func shot(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
