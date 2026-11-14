import XCTest

/// The release unit's cases (batch 2026-11-14).
///
/// Owner answer Q11, 2026-11-14: "Hide it for 1.0". The phone's own chat is
/// not offered, so a launch on the REAL engine path (the only one a shipping
/// build has) must draw Chat exactly as a phone without a model does: the
/// house's threads, one place, no picker and no phone chip. The fake-engine
/// cases in `StubHouseUITests+Assistant.swift` keep testing the place itself.
extension StubHouseUITests {

    @MainActor
    func testRelease_NoPhonePlaceOnTheRealEngine() throws {
        control("reset-fixtures", "{}", port: 6601, what: "the fixtures before the real-engine case")
        let app = assistantLaunch(model: "real")
        let house = app.buttons[StubChat.fridge].firstMatch
        XCTAssertTrue(
            house.waitForExistence(timeout: 40),
            "Chat itself did not draw the house's threads\n\(app.debugDescription)"
        )
        evidence(app, "release-real-engine-no-phone-place")
        XCTAssertFalse(
            assistantChip(app, AssistantUnderTest.chipPhone).exists,
            "a phone chip on the real engine: Apple's model is not offered in 1.0\n\(app.debugDescription)"
        )
        XCTAssertFalse(
            startingWith(app, AssistantUnderTest.runs).exists,
            "the place picker drew on the real engine\n\(app.debugDescription)"
        )
        XCTAssertFalse(
            startingWith(app, AssistantUnderTest.subtitleBoth).exists,
            "the two-place subtitle on the real engine\n\(app.debugDescription)"
        )
        XCTAssertTrue(
            startingWith(app, AssistantUnderTest.houseSubtitle).exists,
            "Chat is not drawing its own one-place subtitle\n\(app.debugDescription)"
        )
    }
}
