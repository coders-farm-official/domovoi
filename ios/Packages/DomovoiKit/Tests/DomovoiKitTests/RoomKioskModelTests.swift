import Foundation
import XCTest
@testable import DomovoiKit

/// A room that does what it is told: R2's engine stands behind
/// ``RoomEngineControl`` in the app; the kiosk is tested against this.
final class FakeRoomEngine: RoomEngineControl, @unchecked Sendable {
    private let state = RoomLock<(status: RoomStatus, calls: [String], continuations: [AsyncStream<RoomStatus>.Continuation])>(
        (RoomStatus(link: .connecting), [], [])
    )
    let startsOK: Bool

    init(startsOK: Bool = true) { self.startsOK = startsOK }

    var calls: [String] { state.withLock { $0.calls } }

    var status: RoomStatus { state.withLock { $0.status } }

    func statusUpdates() -> AsyncStream<RoomStatus> {
        AsyncStream { continuation in
            let now = state.withLock { s -> RoomStatus in
                s.continuations.append(continuation)
                return s.status
            }
            continuation.yield(now)
        }
    }

    func send(_ s: RoomStatus) {
        let all = state.withLock { st -> [AsyncStream<RoomStatus>.Continuation] in
            st.status = s
            return st.continuations
        }
        for c in all { c.yield(s) }
    }

    @discardableResult func start() -> Bool {
        state.withLock { $0.calls.append("start") }
        return startsOK
    }

    func stop() {
        let all = state.withLock { st -> [AsyncStream<RoomStatus>.Continuation] in
            st.calls.append("stop")
            defer { st.continuations = [] }
            return st.continuations
        }
        for c in all { c.finish() }
    }

    func setMuted(_ on: Bool) {
        state.withLock {
            $0.calls.append("mute \(on)")
            $0.status.muted = on
        }
    }

    func retryNow() { state.withLock { $0.calls.append("retry") } }
}

@MainActor
final class RecordingRoomScreen: RoomScreenControl {
    var awake = false
    var dimmed = false
    var log: [String] = []
    func keepAwake(_ on: Bool) {
        if awake != on { log.append("awake \(on)") }
        awake = on
    }
    func dim(_ on: Bool) {
        log.append("dim \(on)")
        dimmed = on
    }
}

@MainActor
final class RecordingRoomNotices: RoomNoticePosting {
    var posted: [(title: String, body: String)] = []
    var cancels = 0
    func post(title: String, body: String) { posted.append((title, body)) }
    func cancel() { cancels += 1 }
}

/// The room screen (`RoomModeActivity.kt` + `RoomService.kt`'s start
/// rules, ROOMMODE-MAP §3): foreground-only listening, the resume notice,
/// the lock rules at every return, Mute, Exit, the idle timer, dimming,
/// drift and heat.
/// Everything a kiosk test drives and reads, built inside the test (a
/// `@MainActor` test class's stored properties would be initialised off it).
@MainActor
final class KioskHarness {
    static let kitchen = RoomEnrolment(
        coreURL: "http://192.168.1.20:6370",
        fingerprint: "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA",
        roomID: "kitchen",
        roomLabel: "Kitchen"
    )

    var clock: TimeInterval = 1_000
    var engines: [FakeRoomEngine] = []
    var left: [Bool] = []
    let store = RoomStore.inMemory()
    let screen = RecordingRoomScreen()
    let notices = RecordingRoomNotices()

    func model(
        lock: FixedRoomLockFacts = FixedRoomLockFacts(),
        mic: Bool = true,
        startsOK: Bool = true
    ) -> RoomKioskModel {
        store.enrol(Self.kitchen)
        store.setActive(true)
        store.setRunning(true)
        return RoomKioskModel(
            store: store,
            makeEngine: { [unowned self] in
                let e = FakeRoomEngine(startsOK: startsOK)
                self.engines.append(e)
                return e
            },
            lock: lock,
            screen: screen,
            notices: notices,
            micGranted: { mic },
            now: { [unowned self] in self.clock },
            onLeave: { [unowned self] forgot in self.left.append(forgot) }
        )
    }

    /// The engine the model built, or a failure (never an index).
    func engine() throws -> FakeRoomEngine {
        try XCTUnwrap(engines.first, "the model built no engine")
    }
}

/// The room screen (`RoomModeActivity.kt` + `RoomService.kt`'s start
/// rules, ROOMMODE-MAP §3): foreground-only listening, the resume notice,
/// the lock rules at every return, Mute, Exit, the idle timer, dimming,
/// drift and heat.
@MainActor
final class RoomKioskModelTests: XCTestCase {

    /// Lets the status stream's task deliver.
    private func settle() async {
        for _ in 0..<5 { await Task.yield() }
    }

    // MARK: - the scene (§3.1, §3.2)

    func testTheRoomStartsOnScreenAndStopsWhenDomovoiLeavesIt() async {
        let h = KioskHarness()
        let m = h.model()
        XCTAssertTrue(h.engines.isEmpty, "nothing starts before the screen is in the foreground")
        m.enteredForeground()
        XCTAssertEqual(1, h.engines.count)
        XCTAssertEqual(["start"], h.engines.first?.calls)
        XCTAssertTrue(m.engineUp)
        XCTAssertTrue(h.screen.awake, "the screen stays on while the room runs on screen")

        m.enteredBackground()
        XCTAssertEqual(["start", "stop"], h.engines.first?.calls)
        XCTAssertFalse(m.engineUp)
        XCTAssertFalse(h.screen.awake)
        XCTAssertEqual(1, h.notices.posted.count)
        XCTAssertEqual("Domovoi Room · Kitchen is not listening", h.notices.posted.first?.title)
        XCTAssertEqual("Tap to resume room mode", h.notices.posted.first?.body)
        XCTAssertTrue(h.store.running, "the person did not stop it: it comes back by itself")

        m.enteredForeground()
        XCTAssertEqual(2, h.engines.count, "back on screen, the room resumes by itself")
        XCTAssertEqual(2, h.notices.cancels, "the notice goes when the room listens again")
    }

    func testANotRunningRoomPostsNothingAndOffersResume() {
        let h = KioskHarness()
        let m = h.model()
        h.store.setRunning(false)
        m.enteredForeground()
        XCTAssertTrue(h.engines.isEmpty)
        XCTAssertEqual(.resume, m.action)
        XCTAssertEqual(RoomStatusText.notListening, m.detail)
        m.enteredBackground()
        XCTAssertTrue(h.notices.posted.isEmpty)
        m.enteredForeground()
        m.perform(.resume)
        XCTAssertEqual(1, h.engines.count)
        XCTAssertTrue(h.store.running)
        XCTAssertEqual(.mute, m.action)
    }

    func testAHouseholdPhoneWithoutAPasscodeStopsWithNeedsLock() {
        let h = KioskHarness()
        let m = h.model(lock: FixedRoomLockFacts(holdsHouseholdToken: true, deviceSecure: false))
        m.enteredForeground()
        XCTAssertTrue(h.engines.isEmpty)
        XCTAssertTrue(m.needsLock)
        XCTAssertEqual(RoomStatusText.needsLock, m.detail)
        XCTAssertEqual(.none, m.action, "no button: iOS has no link to the passcode setting")
    }

    func testNoMicrophonePermissionSaysWhereToTurnItOn() {
        let h = KioskHarness()
        let m = h.model(mic: false)
        m.enteredForeground()
        XCTAssertTrue(h.engines.isEmpty)
        XCTAssertTrue(m.micPermissionOff)
        XCTAssertEqual(RoomStatusText.micPermissionOff, m.detail)
        XCTAssertEqual(.openSettings, m.action)
    }

    // MARK: - what the screen shows

    func testTheApprovalCodeAndCheckNow() async throws {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        let e = try h.engine()
        e.send(RoomStatus(link: .awaitingApproval(code: "482913", removed: false)))
        await settle()
        XCTAssertEqual("482913", m.approvalCode)
        XCTAssertEqual("482 913", RoomModeStrings.approvalCodeShown("482913"))
        XCTAssertEqual("approval code 4 8 2 9 1 3", RoomModeStrings.approvalCodeDescription("482913"))
        XCTAssertEqual(RoomStatusText.awaitingLine, m.detail)
        m.checkNow()
        XCTAssertEqual(["start", "retry"], e.calls)
        e.send(RoomStatus(link: .ready, wakeReady: true))
        await settle()
        XCTAssertNil(m.approvalCode)
        XCTAssertEqual("Say “Hey Jarvis”", m.detail)
        XCTAssertEqual(.idle, m.ring)
    }

    func testMuteGoesToTheEngineAndTheStoreAndTheChipFollowsTheRecorder() async throws {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        let e = try h.engine()
        e.send(RoomStatus(link: .ready, micOpen: true, wakeReady: true))
        await settle()
        XCTAssertTrue(m.micOpen)
        m.perform(.mute)
        XCTAssertTrue(h.store.muted)
        XCTAssertEqual(["start", "mute true"], e.calls)
        // The button follows the engine's status; the chip, the recorder.
        e.send(RoomStatus(link: .ready, muted: true, micOpen: true, wakeReady: true))
        await settle()
        XCTAssertEqual(.unmute, m.action)
        XCTAssertTrue(m.micOpen, "the chip follows micOpen, never the button")
        e.send(RoomStatus(link: .ready, muted: true, micOpen: false, wakeReady: true))
        await settle()
        XCTAssertFalse(m.micOpen)
        XCTAssertEqual(.muted, m.ring)
    }

    // MARK: - leaving (§3.6)

    func testExitWithAPasscodeTakesTheUnlockThenKeepOrForget() async {
        let h = KioskHarness()
        let m = h.model(lock: FixedRoomLockFacts(deviceSecure: true, unlockSucceeds: true))
        m.enteredForeground()
        m.exitHeld()
        await settle()
        XCTAssertEqual(.keepOrForget, m.dialog)
        m.leave(forget: false)
        XCTAssertEqual([false], h.left)
        XCTAssertFalse(h.store.active)
        XCTAssertNotNil(h.store.enrolment, "kept for later")
        XCTAssertNotNil(h.store.pairingToken())
        XCTAssertEqual(["start", "stop"], h.engines.first?.calls)
        XCTAssertFalse(h.screen.awake)
    }

    func testAFailedUnlockLeavesTheRoomARoom() async {
        let h = KioskHarness()
        let m = h.model(lock: FixedRoomLockFacts(deviceSecure: true, unlockSucceeds: false))
        m.enteredForeground()
        m.exitHeld()
        await settle()
        XCTAssertNil(m.dialog)
        XCTAssertTrue(h.store.active)
    }

    func testNoPasscodeAsksAndAHouseholdPhoneMustSetOne() {
        let h = KioskHarness()
        let m = h.model()
        m.exitHeld()
        XCTAssertEqual(.confirmNoLock, m.dialog)
        m.confirmExit()
        XCTAssertEqual(.keepOrForget, m.dialog)
        m.leave(forget: true)
        XCTAssertEqual([true], h.left)
        XCTAssertNil(h.store.enrolment)
        XCTAssertNil(h.store.pairingToken())

        let other = KioskHarness()
        let household = other.model(lock: FixedRoomLockFacts(holdsHouseholdToken: true, deviceSecure: false))
        household.exitHeld()
        XCTAssertEqual(.setALock, household.dialog)
    }

    // MARK: - the display (§3.3, §3.4, D9)

    func testItDimsAfterAMinuteIdleAndATouchOrATurnBringsItBack() async throws {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        let e = try h.engine()
        e.send(RoomStatus(link: .ready, wakeReady: true))
        await settle()
        h.clock += 59
        m.tick()
        XCTAssertFalse(m.dimmed)
        h.clock += 2
        m.tick()
        XCTAssertTrue(m.dimmed)
        XCTAssertTrue(h.screen.dimmed)
        m.touched()
        XCTAssertFalse(m.dimmed)
        XCTAssertFalse(h.screen.dimmed)
        h.clock += 61
        m.tick()
        XCTAssertTrue(m.dimmed)
        e.send(RoomStatus(link: .ready, phase: .capturing, wakeReady: true))
        await settle()
        XCTAssertFalse(m.dimmed, "a turn puts the person's level back")
        XCTAssertTrue(m.animates)
        h.clock += 120
        m.tick()
        XCTAssertFalse(m.dimmed, "never dims during a turn")
    }

    func testNothingAnimatesWhileIdleAndTheContentDriftsOnceAMinute() async throws {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        let e = try h.engine()
        e.send(RoomStatus(link: .ready, wakeReady: true))
        await settle()
        XCTAssertFalse(m.animates)
        XCTAssertEqual(.zero, m.drift)
        for _ in 0..<20 {
            h.clock += 61
            m.tick()
            XCTAssertLessThanOrEqual(abs(m.drift.x), Double(RoomDrift.reach))
            XCTAssertLessThanOrEqual(abs(m.drift.y), Double(RoomDrift.reach))
        }
    }

    func testLeavingTheScreenPutsTheBrightnessBack() {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        h.clock += 61
        m.tick()
        XCTAssertTrue(h.screen.dimmed)
        m.enteredBackground()
        XCTAssertFalse(h.screen.dimmed)
        XCTAssertFalse(m.dimmed)
    }

    // MARK: - heat (D8, §3.8)

    func testSeriousHeatDimsAndStopsAnimationButKeepsListening() async throws {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        let e = try h.engine()
        e.send(RoomStatus(link: .ready, phase: .thinking, wakeReady: true))
        await settle()
        XCTAssertTrue(m.animates)
        m.thermalChanged(.serious)
        XCTAssertTrue(m.dimmed)
        XCTAssertFalse(m.animates)
        XCTAssertTrue(m.engineUp, "the wake chain is the cheap part: it keeps listening")
        m.touched()
        XCTAssertTrue(m.dimmed, "stays at the floor while hot")
    }

    func testCriticalHeatStopsAndRestartsAfterAMinuteCool() {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        m.thermalChanged(.critical)
        XCTAssertTrue(m.tooHot)
        XCTAssertFalse(m.engineUp)
        XCTAssertEqual(RoomStatusText.tooHot, m.detail)
        XCTAssertEqual(.none, m.action)
        XCTAssertTrue(h.notices.posted.isEmpty, "the screen says it; nobody needs a notice")
        m.enteredForeground()
        XCTAssertEqual(1, h.engines.count, "no restart while hot")
        m.thermalChanged(.serious)
        h.clock += 120
        m.tick()
        XCTAssertTrue(m.tooHot, "serious is not cool")
        m.thermalChanged(.fair)
        h.clock += 59
        m.tick()
        XCTAssertTrue(m.tooHot)
        h.clock += 2
        m.tick()
        XCTAssertFalse(m.tooHot)
        XCTAssertEqual(2, h.engines.count, "starts again by itself")
    }

    func testCriticalHeatInTheBackgroundPostsItsOwnSentence() {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        m.enteredBackground()
        m.thermalChanged(.critical)
        XCTAssertEqual(2, h.notices.posted.count)
        XCTAssertEqual(RoomStatusText.tooHot, h.notices.posted.last?.body)
    }

    // MARK: - an interruption that does not end (§3.2)

    func testAnInterruptionPastTenSecondsPostsTheNoticeOnce() async throws {
        let h = KioskHarness()
        let m = h.model()
        m.enteredForeground()
        let e = try h.engine()
        e.send(RoomStatus(link: .ready, wakeReady: true, stoppedByIOS: .interrupted))
        await settle()
        XCTAssertEqual(RoomStatusText.interrupted, m.detail)
        h.clock += 9
        m.tick()
        XCTAssertTrue(h.notices.posted.isEmpty)
        h.clock += 2
        m.tick()
        XCTAssertEqual(1, h.notices.posted.count)
        h.clock += 30
        m.tick()
        XCTAssertEqual(1, h.notices.posted.count, "once per interruption")
    }

    // MARK: - Guided Access (§3.5)

    func testTheGuidedAccessTipHidesWhileItIsOnAndOnceDismissed() {
        let h = KioskHarness()
        let m = h.model()
        XCTAssertTrue(m.showsGuidedAccessTip)
        m.guidedAccessChanged(true)
        XCTAssertFalse(m.showsGuidedAccessTip)
        m.guidedAccessChanged(false)
        XCTAssertTrue(m.showsGuidedAccessTip)
        m.dismissGuidedAccessTip()
        XCTAssertFalse(m.showsGuidedAccessTip)
        XCTAssertTrue(h.store.guidedAccessTipDismissed, "remembered for the next launch")
    }

    // MARK: - the notice's rules

    func testTheNoticePostsOnlyForARunningRoom() {
        let h = KioskHarness()
        XCTAssertTrue(RoomModeNotices.shouldPost(.leftForeground, active: true, running: true, inBackground: true))
        XCTAssertFalse(RoomModeNotices.shouldPost(.leftForeground, active: true, running: false, inBackground: true))
        XCTAssertFalse(RoomModeNotices.shouldPost(.leftForeground, active: false, running: true, inBackground: true))
        XCTAssertFalse(RoomModeNotices.shouldPost(.tooHot, active: true, running: true, inBackground: false))
        XCTAssertTrue(RoomModeNotices.shouldPost(.tooHot, active: true, running: true, inBackground: true))
        XCTAssertEqual("Domovoi Room · Den is not listening", RoomModeNotices.title(roomLabel: "Den"))
    }
}
