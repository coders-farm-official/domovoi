import XCTest
@testable import DomovoiMacKit

/// Train 1 S2-4 and S2-5: Restart's result when notifications are off, and
/// the rate-limit alert's wording.
final class RestartResultWordsTests: XCTestCase {
    // S2-4: the result goes where the person sees it.
    func testTheResultIsANotificationOnlyWhenTheyShow() {
        XCTAssertEqual(ResultRoute.decide(.shown, offeredSettingsBefore: false), .notification)
        XCTAssertEqual(ResultRoute.decide(.shown, offeredSettingsBefore: true), .notification)
        XCTAssertEqual(ResultRoute.decide(.hidden, offeredSettingsBefore: false), .alert(offerSettings: true), "off: an alert, offering the settings")
        XCTAssertEqual(ResultRoute.decide(.hidden, offeredSettingsBefore: true), .alert(offerSettings: false), "the offer is made once")
        XCTAssertEqual(ResultRoute.decide(.notAsked, offeredSettingsBefore: false), .alert(offerSettings: false),
                       "never answered: an alert, and nothing to switch on yet")
        XCTAssertTrue(ResultRoute.settingsNote.contains("System Settings > Notifications"))
    }

    func testTheMenuSaysRestartsResultThroughTheRoute() throws {
        let menu = try String(contentsOf: macDir.appendingPathComponent("Sources/Domovoi/MenuBar.swift"), encoding: .utf8)
        XCTAssertFalse(menu.contains("self.notify(RestartResult.backPrefix"), "a back result never goes only to a notification")
        XCTAssertEqual(menu.components(separatedBy: "self.sayResult(RestartResult.backPrefix").count - 1, 2, "both restart paths")
        XCTAssertTrue(menu.contains("ResultRoute.decide("))
    }

    // S2-5: "Domovoi was restarted less than two minutes ago. Wait 111
    // seconds, then try again." Not "Domovoi didn't restart: Domovoi was
    // restarted ...: wait ...".
    func testTheRateLimitIsTwoSentencesOfItsOwn() throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let s = RestartService.State(phase: "done", seenAt: HostFiles.iso(now), lastActedAt: HostFiles.iso(now.addingTimeInterval(-9)), said: "x")
        let why = try XCTUnwrap(RestartService.tooSoon(s, now: now))
        XCTAssertEqual(why, "Domovoi was restarted less than two minutes ago. Wait 111 seconds, then try again")
        XCTAssertEqual(RestartResult.alertText(why), "Domovoi was restarted less than two minutes ago. Wait 111 seconds, then try again.")
        let one = try XCTUnwrap(RestartService.tooSoon(s, now: now.addingTimeInterval(110)))
        XCTAssertTrue(one.hasSuffix("Wait 1 second, then try again"), one)
        // Other refusals keep their prefix.
        XCTAssertEqual(RestartResult.alertText(RestartPlan.underWay), "Domovoi didn't restart: \(RestartPlan.underWay).")
        XCTAssertEqual(RestartResult.alertText("Domovoi is still not answering."), "Domovoi is still not answering.")
    }

    // Review: Restart downloads nothing; its words said "pull".
    func testRestartsWordsDontSayPull() {
        XCTAssertFalse(RestartStep.startUpdateUnit.words.contains("pull"))
    }
}
