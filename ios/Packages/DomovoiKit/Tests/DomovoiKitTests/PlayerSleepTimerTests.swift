import XCTest
@testable import DomovoiKit

/// The sleep timer's arithmetic, and the Android race that is not ported.
final class PlayerSleepTimerTests: XCTestCase {

    func testMinutesBecomeSeconds() {
        XCTAssertEqual(PlayerSleepTimer.seconds(forMinutes: 30), 1800)
        XCTAssertEqual(PlayerSleepTimer.seconds(forMinutes: 0), 0)
        XCTAssertEqual(PlayerSleepTimer.seconds(forMinutes: -5), 0, "a negative timer is no timer")
    }

    func testEndOfTrackIsSeededFromWhatIsLeftOfIt() {
        XCTAssertEqual(
            PlayerSleepTimer.secondsForEndOfTrack(durationSec: 390, positionSec: 42), 348
        )
    }

    func testEndOfTrackIsAtLeastOneSecond() {
        // A track that has already finished still pauses through the countdown
        // rather than never firing (`PlayerController.kt:547`).
        XCTAssertEqual(PlayerSleepTimer.secondsForEndOfTrack(durationSec: 10, positionSec: 10), 1)
        XCTAssertEqual(PlayerSleepTimer.secondsForEndOfTrack(durationSec: 10, positionSec: 99), 1)
        XCTAssertEqual(PlayerSleepTimer.secondsForEndOfTrack(durationSec: 0, positionSec: 0), 1)
        XCTAssertEqual(
            PlayerSleepTimer.secondsForEndOfTrack(durationSec: .infinity, positionSec: 0), 1,
            "a live stream has no end of track to seed from"
        )
    }

    func testTheCountdownPausesAtZeroExactlyOnce() {
        var remaining: Int? = 3
        for expected in [2, 1] {
            let step = PlayerSleepTimer.tick(remaining: remaining)
            XCTAssertFalse(step.pause)
            XCTAssertEqual(step.remaining, expected)
            remaining = step.remaining
        }
        let last = PlayerSleepTimer.tick(remaining: remaining)
        XCTAssertTrue(last.pause)
        XCTAssertNil(last.remaining, "the value is cleared at the same step, so nothing pauses twice")

        let after = PlayerSleepTimer.tick(remaining: last.remaining)
        XCTAssertFalse(after.pause, "a cancelled or finished timer does nothing")
        XCTAssertNil(after.remaining)
    }

    func testATimerSeededAtZeroPausesOnItsFirstTick() {
        let step = PlayerSleepTimer.tick(remaining: 0)
        XCTAssertTrue(step.pause)
        XCTAssertNil(step.remaining)
    }

    func testANilRemainingIsNotATimerAtAll() {
        let step = PlayerSleepTimer.tick(remaining: nil)
        XCTAssertFalse(step.pause)
        XCTAssertNil(step.remaining)
    }

    // MARK: - through the model

    @MainActor
    func testTheModelsEndOfTrackTimerCountsDownAndPausesOnce() async {
        StubURLProtocol.reset()
        let house = PlayerHouse()
        house.install()
        let h = PlayerHarness()
        h.engine.durationSec = 10
        h.model.play([PlayerSeed.transmission])
        h.engine.currentTimeSec = 7
        h.engine.status = .playing
        h.model.sleepAtEndOfTrack()
        XCTAssertEqual(h.model.sleepRemainingSec, 3, "10 s long, 7 s in")

        XCTAssertFalse(h.model.sleepTickForTests())
        XCTAssertFalse(h.model.sleepTickForTests())
        XCTAssertTrue(h.model.sleepTickForTests(), "the third tick reaches zero")
        XCTAssertNil(h.model.sleepRemainingSec)
        XCTAssertFalse(h.model.isPlaying, "and it pauses")

        XCTAssertFalse(h.model.sleepTickForTests(), "a finished timer does not pause again")
        StubURLProtocol.reset()
    }

    @MainActor
    func testCancellingTheTimerClearsItWithoutPausing() async {
        StubURLProtocol.reset()
        PlayerHouse().install()
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        h.model.sleep(afterMinutes: 1)
        XCTAssertEqual(h.model.sleepRemainingSec, 60)
        h.model.cancelSleep()
        XCTAssertNil(h.model.sleepRemainingSec)
        XCTAssertTrue(h.model.isPlaying, "cancel is not pause")
        StubURLProtocol.reset()
    }
}
