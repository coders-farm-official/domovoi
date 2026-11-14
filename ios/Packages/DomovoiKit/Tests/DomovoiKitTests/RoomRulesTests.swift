import Foundation
import XCTest
@testable import DomovoiKit

/// The room's small rules R3 owns (Android's `RoomRulesTest` and
/// `RoomLockRulesTest`): who may leave and how, when the room may start,
/// what the ring shows and the words under it. R2 ports the session-rule
/// half (`RoomSessionRules`); the model-digest and sound-path halves are
/// R2's `RoomModelsTest` / `RoomSounds`.
final class RoomRulesTests: XCTestCase {

    // MARK: - RoomLockRules

    func testAHouseholdPhoneNeedsAPasscodeAndARoomOnlyPhoneDoesNot() {
        XCTAssertTrue(RoomLockRules.mayRun(holdsHouseholdToken: false, deviceSecure: false))
        XCTAssertTrue(RoomLockRules.mayRun(holdsHouseholdToken: false, deviceSecure: true))
        XCTAssertTrue(RoomLockRules.mayRun(holdsHouseholdToken: true, deviceSecure: true))
        XCTAssertFalse(RoomLockRules.mayRun(holdsHouseholdToken: true, deviceSecure: false))
    }

    func testLeavingTakesTheUnlockOrSaysWhy() {
        XCTAssertEqual(.deviceCredential, RoomLockRules.exit(holdsHouseholdToken: true, deviceSecure: true))
        XCTAssertEqual(.deviceCredential, RoomLockRules.exit(holdsHouseholdToken: false, deviceSecure: true))
        XCTAssertEqual(.setALockFirst, RoomLockRules.exit(holdsHouseholdToken: true, deviceSecure: false))
        XCTAssertEqual(.confirmNoLock, RoomLockRules.exit(holdsHouseholdToken: false, deviceSecure: false))
    }

    func testTheStartRulesInAndroidsOrder() {
        func start(sticky: Bool = false, active: Bool = true, enrolled: Bool = true, running: Bool = true,
                   mic: Bool = true, holds: Bool = false, secure: Bool = false) -> RoomLockRules.Start {
            RoomLockRules.start(stickyRestart: sticky, active: active, enrolled: enrolled, running: running,
                                micGranted: mic, holdsHouseholdToken: holds, deviceSecure: secure)
        }
        XCTAssertEqual(.run, start())
        XCTAssertEqual(.nothing, start(active: false))
        XCTAssertEqual(.nothing, start(enrolled: false))
        XCTAssertEqual(.askForTap, start(sticky: true))
        XCTAssertEqual(.nothing, start(sticky: true, running: false))
        XCTAssertEqual(.askForTap, start(mic: false))
        XCTAssertEqual(.needsLock, start(holds: true))
        XCTAssertEqual(.run, start(holds: true, secure: true))
        // No microphone outranks the lock, as on Android.
        XCTAssertEqual(.askForTap, start(mic: false, holds: true))
    }

    // MARK: - RoomStatusText

    private func status(
        link: RoomLink = .ready,
        phase: RoomPhase = .listening,
        muted: Bool = false,
        blocked: Bool = false,
        stop: RoomMicStop? = nil
    ) -> RoomStatus {
        RoomStatus(link: link, phase: phase, muted: muted, micBlocked: blocked, wakeReady: true, stoppedByIOS: stop)
    }

    func testTheRingShowsTheOwnersSevenStates() {
        typealias Ring = RoomStatusText.Ring
        XCTAssertEqual(Ring.idle, RoomStatusText.ring(status(), running: true))
        XCTAssertEqual(Ring.listening, RoomStatusText.ring(status(phase: .capturing), running: true))
        XCTAssertEqual(Ring.listening, RoomStatusText.ring(status(phase: .ack), running: true))
        XCTAssertEqual(Ring.thinking, RoomStatusText.ring(status(phase: .thinking), running: true))
        XCTAssertEqual(Ring.speaking, RoomStatusText.ring(status(phase: .speaking), running: true))
        XCTAssertEqual(Ring.call, RoomStatusText.ring(status(phase: .call), running: true))
        XCTAssertEqual(Ring.muted, RoomStatusText.ring(status(muted: true, blocked: true), running: true))
        XCTAssertEqual(Ring.micBlocked, RoomStatusText.ring(status(link: .connecting, blocked: true), running: true))
        XCTAssertEqual(Ring.offline, RoomStatusText.ring(status(link: .offline(reason: "x")), running: true))
        XCTAssertEqual(Ring.offline, RoomStatusText.ring(status(), running: false))
        XCTAssertEqual(Ring.offline, RoomStatusText.ring(nil, running: true))
        XCTAssertEqual(["idle", "listening", "thinking", "speaking", "muted", "offline", "mic blocked", "drop-in"],
                       Ring.allCases.map(RoomStatusText.label))
    }

    /// §13.3: a fresh, ready room says "idle", not "listening".
    func testAWaitingRoomIsIdleAndSaysTheWakeWord() {
        XCTAssertEqual("idle", RoomStatusText.label(RoomStatusText.ring(status(), running: true)))
        XCTAssertEqual("Say “Hey Jarvis”", RoomStatusText.detail(status(), running: true, wakeWord: "hey_jarvis"))
        XCTAssertEqual("Hey House Two", RoomStatusText.spoken("hey_house__two"))
    }

    func testTheWordsUnderTheRing() {
        XCTAssertEqual(RoomStatusText.notListening, RoomStatusText.detail(status(), running: false, wakeWord: "x"))
        XCTAssertTrue(RoomStatusText.detail(status(link: .awaitingApproval(code: "305117", removed: false)),
                                            running: true, wakeWord: "x")!.contains("approve"))
        XCTAssertTrue(RoomStatusText.detail(status(link: .awaitingApproval(code: nil, removed: true)),
                                            running: true, wakeWord: "x")!.contains("removed on the dashboard"))
        XCTAssertEqual("Refused: x", RoomStatusText.detail(status(link: .rejected(reason: "Refused: x")), running: true, wakeWord: "x"))
        XCTAssertEqual("Server is down", RoomStatusText.detail(status(link: .offline(reason: "server is down")), running: true, wakeWord: "x"))
        XCTAssertEqual("Loading the wake word…", RoomStatusText.detail(
            RoomStatus(link: .ready, wakeReady: false), running: true, wakeWord: "x"))
        XCTAssertEqual("no models", RoomStatusText.detail(
            RoomStatus(link: .ready, wakeReady: false, wakeProblem: "no models"), running: true, wakeWord: "x"))
        XCTAssertEqual("Drop-in with porch", RoomStatusText.detail(
            RoomStatus(link: .ready, phase: .call, peerRoom: "porch"), running: true, wakeWord: "x"))
        XCTAssertEqual("waiting for approval · code 305117",
                       RoomStatusText.notificationLine(status(link: .awaitingApproval(code: "305117", removed: false))))
        XCTAssertEqual("listening", RoomStatusText.notificationLine(status(phase: .capturing)))
    }

    /// ROOMMODE-MAP §11: the iPhone has no microphone privacy toggle, and
    /// what iOS does to the microphone is said in the iPhone's own words —
    /// after Mute and the blocked mic, before the connection.
    func testTheIPhonesOwnLines() {
        let blocked = RoomStatusText.detail(status(blocked: true), running: true, wakeWord: "x")!
        XCTAssertFalse(blocked.contains("quick settings"), "Android's privacy toggle is not on an iPhone")
        XCTAssertTrue(blocked.contains("turned off for Domovoi in Settings"))
        XCTAssertEqual(RoomStatusText.interrupted,
                       RoomStatusText.detail(status(link: .connecting, stop: .interrupted), running: true, wakeWord: "x"))
        XCTAssertEqual(RoomStatusText.mutedLine,
                       RoomStatusText.detail(status(muted: true, stop: .interrupted), running: true, wakeWord: "x"))
        XCTAssertEqual("Say “Hey Jarvis”",
                       RoomStatusText.detail(status(stop: .leftForeground), running: true, wakeWord: "hey_jarvis"))
        for line in [RoomStatusText.needsLock, RoomStatusText.setALockToExit, RoomStatusText.confirmNoLock] {
            XCTAssertFalse(line.contains("PIN, pattern"), line)
            XCTAssertTrue(line.contains("passcode"), line)
        }
    }

    // MARK: - the explainer (`RoomRulesTest.theExplainerSaysChatsAreLocked`)

    func testTheExplainerSaysChatsAreLocked() throws {
        let stops = try XCTUnwrap(RoomModeStrings.explainer.first { $0.title == "What stops working" })
        XCTAssertTrue(stops.body.contains("no files, browsing or chats, on the home server or on this phone"))
        XCTAssertTrue(RoomModeStrings.explainer.allSatisfy { !$0.title.contains("assistant") && !$0.body.contains("assistant") })
    }

    /// Six points; the iPhone's rules in place of Android's (§11), and
    /// nothing Android-only left in them.
    func testTheExplainerIsTheIPhones() {
        XCTAssertEqual(["Always listening", "A shared device", "Power and heat", "iPhone's rules",
                        "What stops working", "How to leave"], RoomModeStrings.explainer.map(\.title))
        let text = RoomModeStrings.explainer.map(\.body).joined(separator: " ")
        for android in ["Android", "battery optimisation", "PIN, pattern or fingerprint", "green microphone dot"] {
            XCTAssertFalse(text.contains(android), android)
        }
        XCTAssertTrue(text.contains("Optimized Battery Charging"))
        XCTAssertTrue(text.contains("orange dot"))
        XCTAssertTrue(text.contains("open Domovoi again and the room starts again"))
    }
}
