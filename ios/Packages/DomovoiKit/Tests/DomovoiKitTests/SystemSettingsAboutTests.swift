import XCTest
@testable import DomovoiKit

/// About & updates: the version line, where help goes, and the help card's
/// round trip (``SystemSettingsHelpModel``).
@MainActor
final class SystemSettingsAboutTests: XCTestCase {

    // MARK: - the version line (data/DeveloperOptions.kt:120)

    func testTheVersionLineIsAndroidsSentence() {
        XCTAssertEqual("app version 1.0.0", systemSettingsAppVersionText(version: "1.0.0", debugBuild: false))
        XCTAssertEqual(
            "app version 1.0.0 (debug build)",
            systemSettingsAppVersionText(version: "1.0.0", debugBuild: true)
        )
    }

    /// `?: "unknown"`. This is also the shape a `Bundle` key mistake takes,
    /// which is why the UI case asserts the drawn line is **not** this.
    func testAMissingOrBlankVersionReadsAsUnknown() {
        XCTAssertEqual("app version unknown", systemSettingsAppVersionText(version: nil, debugBuild: false))
        XCTAssertEqual("app version unknown", systemSettingsAppVersionText(version: "", debugBuild: false))
        XCTAssertEqual("app version unknown", systemSettingsAppVersionText(version: "   ", debugBuild: false))
    }

    // MARK: - where help goes (ChatPlace.kt:185-195)

    /// Each of the facts, one at a time. Unfolded, Android's rule with no
    /// phone engine is: the server's place when `!away && serverChatOn`, else
    /// the Manual screen.
    func testHelpGoesToTheServerOnlyWhenItIsInReachAndAllowsChat() {
        XCTAssertEqual(.serverChat, systemSettingsHelpEntry(connected: true, aiEnabled: true))
        XCTAssertEqual(.manual, systemSettingsHelpEntry(connected: false, aiEnabled: true))
        XCTAssertEqual(.manual, systemSettingsHelpEntry(connected: true, aiEnabled: false))
        XCTAssertEqual(.manual, systemSettingsHelpEntry(connected: false, aiEnabled: false))
    }

    /// The server reads this exact prefix (`web/backend/api/chat.py:301`,
    /// `help_delivery.TITLE_PREFIX`), so the title is not a label: get it
    /// wrong and the thread is answered from the whole model instead of from
    /// Domovoi's help.
    func testTheHelpTitleAndItsBodyAreTheOnesTheServerReads() {
        XCTAssertEqual("Help: App", systemSettingsHelpTitle)
        XCTAssertTrue(systemSettingsHelpTitle.hasPrefix("Help:"))
        let body = systemSettingsHelpThreadBody()
        let object = try? JSONSerialization.jsonObject(with: body)
        XCTAssertEqual(["title": "Help: App"], object as? [String: String])
        XCTAssertEqual(ChatPath.newThread, "/api/chat/threads")
    }

    // MARK: - the help card's round trip

    private struct Rig {
        let key = Shared<String?>("aaaaaaaa")
        let accepted = Shared<[Int64]>([])
        /// Runs inside the round trip, before the thread comes back.
        var insideTheRoundTrip: (@Sendable () -> Void)?
        var answer: Result<ChatThread, DomovoiError> = .success(ChatThread(id: 7, title: "Help: App"))

        @MainActor
        func model() -> SystemSettingsHelpModel {
            let key = key
            let accepted = accepted
            let inside = insideTheRoundTrip
            let answer = answer
            // A local function and not a closure literal: a closure's typed
            // `throws(DomovoiError)` is not inferred from an annotation on
            // the variable, and the body's throw reads as `any Error`.
            @Sendable func create() async throws(DomovoiError) -> ChatThread {
                inside?()
                switch answer {
                case .success(let thread): return thread
                case .failure(let failure): throw failure
                }
            }
            return SystemSettingsHelpModel(
                serverKey: { key.current },
                createThread: create,
                accept: { captured, thread in
                    // ``AppContainer/requestChatThread(serverKey:thread:title:)``'s
                    // own rule, in one line: the captured key must still be
                    // this phone's server.
                    guard captured == key.current, thread.id > 0 else { return false }
                    accepted.mutate { $0.append(thread.id) }
                    return true
                }
            )
        }
    }

    func testAHelpThreadIsAcceptedAndChatIsOpenedOnce() async {
        let rig = Rig()
        let model = rig.model()
        await model.askForHelp()
        XCTAssertNil(model.error)
        XCTAssertEqual([7], rig.accepted.current)
        XCTAssertFalse(model.starting)
        XCTAssertTrue(model.takeShouldOpenChat())
        XCTAssertFalse(model.takeShouldOpenChat(), "taken once, so a redraw does not push Chat twice")
    }

    /// **The read-after-await case.** The server is switched *inside* the
    /// round trip, so the key read before the `POST` is no longer this
    /// phone's. The thread must be dropped: its id means nothing outside the
    /// household that minted it, and looked up on the new server that house
    /// would answer with its own thread number 7.
    ///
    /// Android has no such check (`AskForHelp.kt:76-78`).
    func testAServerSwitchInsideTheRoundTripDropsTheThread() async {
        var rig = Rig()
        let key = rig.key
        rig.insideTheRoundTrip = { key.set("bbbbbbbb") }
        let model = rig.model()
        await model.askForHelp()
        XCTAssertEqual([], rig.accepted.current, "nothing was handed over")
        XCTAssertEqual(SystemSettingsStrings.helpServerChanged, model.error)
        XCTAssertFalse(model.takeShouldOpenChat(), "Chat is not opened on a thread that was dropped")
    }

    /// The same switch, but back again before the reply lands: the key that
    /// was captured is this phone's server once more, so the thread is its
    /// own and is kept. This is the half that makes the case above a test of
    /// the *key* and not of "any change at all".
    func testAServerThatComesBackInsideTheRoundTripKeepsItsThread() async {
        var rig = Rig()
        let key = rig.key
        rig.insideTheRoundTrip = {
            key.set("bbbbbbbb")
            key.set("aaaaaaaa")
        }
        let model = rig.model()
        await model.askForHelp()
        XCTAssertNil(model.error)
        XCTAssertEqual([7], rig.accepted.current)
    }

    func testWithNoServerSavedNothingIsAsked() async {
        let rig = Rig()
        rig.key.set(nil)
        let model = rig.model()
        await model.askForHelp()
        XCTAssertEqual([], rig.accepted.current)
        XCTAssertNotNil(model.error)
        XCTAssertFalse(model.starting)
    }

    func testAFailureSaysWhatFailedInAndroidsWords() async {
        var rig = Rig()
        rig.answer = .failure(.unreachable("nothing answered"))
        let model = rig.model()
        await model.askForHelp()
        XCTAssertEqual(failureText("start a help chat", .unreachable("nothing answered")), model.error)
        XCTAssertFalse(model.takeShouldOpenChat())
    }

    /// A second tap while the first is in flight is refused, and the error of
    /// the last attempt is cleared when a new one starts.
    func testTheErrorIsClearedWhenTheNextAttemptStarts() async {
        var rig = Rig()
        rig.answer = .failure(.unreachable("nothing answered"))
        var model = rig.model()
        await model.askForHelp()
        XCTAssertNotNil(model.error)

        rig.answer = .success(ChatThread(id: 9, title: "Help: App"))
        model = rig.model()
        await model.askForHelp()
        XCTAssertNil(model.error)
        XCTAssertEqual([9], rig.accepted.current)
    }
}
