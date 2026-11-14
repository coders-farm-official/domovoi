import Foundation
import XCTest
@testable import DomovoiKit

/// The room phone's wake gate against the Pi's (satellite/wake_gate.py and
/// satellite/tests/test_wake_gate.py, the same cases), as Android's
/// `WakeGateTest.kt` holds it (220085d8): patience, the floor a run is
/// counted against, the cooldown after a turn that went wrong, and the
/// defaults a phone wakes with when nothing is set.
final class WakeGateTests: XCTestCase {

    private func feedAll(_ gate: WakeGate, _ scores: [Float], from: Int64 = 0) -> [Bool] {
        scores.enumerated().map { i, s in gate.feed(s, now: from + Int64(i) * 80) }
    }

    func testTheDefaultsWakeOnTheFirstChunkOverTheThresholdAsBefore() {
        let g = WakeGate(threshold: 0.5)
        XCTAssertEqual(1, g.patience)
        XCTAssertEqual(2.0, g.cooldownSec)
        XCTAssertTrue(g.feed(0.51, now: 1_000))
        XCTAssertEqual(0.51, g.lastPeak)
        // At the threshold is not over it.
        XCTAssertFalse(g.feed(0.5, now: 1_080))
    }

    func testWithPatienceTwoOneChunkIsNotAWakeAndTwoInARowAre() {
        let g = WakeGate(threshold: 0.5, patience: 2, cooldownSec: 0)
        XCTAssertFalse(g.feed(0.99, now: 1_000))
        XCTAssertFalse(g.feed(0.1, now: 1_080)) // the dip ends the run
        XCTAssertFalse(g.feed(0.7, now: 1_160))
        XCTAssertTrue(g.feed(0.93, now: 1_240))
        XCTAssertEqual(0.93, g.lastPeak)
        XCTAssertEqual(2, g.lastRun)
        // The run starts again after a wake.
        XCTAssertFalse(g.feed(0.9, now: 1_320))
    }

    func testPatienceThreeNeedsThreeInARowAndADipStartsAgain() {
        let g = WakeGate(threshold: 0.5, patience: 3, cooldownSec: 0)
        XCTAssertEqual([false, false, false, false, false, true], feedAll(g, [0.9, 0.9, 0.2, 0.9, 0.9, 0.9]))
        XCTAssertEqual(3, g.lastRun)
    }

    func testAFarFieldWakeAtARaisedThresholdStillWakesAndALoneBlipDoesNot() {
        // Threshold 0.8: the run counts against max(0.3, 0.8 x 0.6) = 0.48.
        let g = WakeGate(threshold: 0.8, patience: 2, cooldownSec: 0)
        XCTAssertEqual(0.48, g.floor, accuracy: 1e-6)
        XCTAssertEqual([false, false, true, false], feedAll(g, [0.1, 0.6, 0.85, 0.6]))
        XCTAssertEqual(0.85, g.lastPeak)
        XCTAssertEqual([false, true], feedAll(WakeGate(threshold: 0.8, patience: 2, cooldownSec: 0), [0.85, 0.6]))
        XCTAssertFalse(feedAll(WakeGate(threshold: 0.8, patience: 2, cooldownSec: 0), [0.1, 0.85, 0.15, 0.1]).contains(true))
        // Over the floor for ever, never reaching the threshold: no wake.
        XCTAssertFalse(feedAll(WakeGate(threshold: 0.8, patience: 2, cooldownSec: 0), Array(repeating: 0.7, count: 10)).contains(true))
    }

    func testTheFloorNeverSitsAboveTheThreshold() {
        XCTAssertEqual(0.3, WakeGate.floorFor(0.5))
        XCTAssertEqual(0.2, WakeGate.floorFor(0.2))
        XCTAssertEqual(0.6, WakeGate.floorFor(1.0), accuracy: 1e-6)
    }

    func testAResetBreaksTheRun() {
        let g = WakeGate(threshold: 0.5, patience: 2, cooldownSec: 0)
        _ = g.feed(0.9, now: 1_000)
        g.reset()
        XCTAssertFalse(g.feed(0.9, now: 1_160))
        XCTAssertTrue(g.feed(0.9, now: 1_240))
    }

    func testNothingWakesInTheCooldownAfterATurnThatWentWrong() {
        let g = WakeGate(threshold: 0.5) // the defaults: 1 chunk, 2 s
        g.quietFor(now: 10_000)
        XCTAssertTrue(g.inCooldown(now: 11_999))
        for t: Int64 in [10_000, 10_100, 11_000, 11_999] {
            XCTAssertFalse(g.feed(0.98, now: t), "woke at \(t)")
        }
        XCTAssertFalse(g.inCooldown(now: 12_000))
        XCTAssertTrue(g.feed(0.9, now: 12_000))
    }

    func testARunStraddlingTheEndOfTheCooldownCountsOnlyItsOutsidePart() {
        let g = WakeGate(threshold: 0.5, patience: 2, cooldownSec: 2.0)
        g.quietFor(now: 10_000)
        XCTAssertFalse(g.feed(0.9, now: 11_920))
        XCTAssertFalse(g.feed(0.9, now: 12_000))
        XCTAssertTrue(g.feed(0.9, now: 12_080))
    }

    func testAfterAnOrdinaryTurnThereIsNoCooldown() {
        let g = WakeGate(threshold: 0.5, patience: 1, cooldownSec: 2.0)
        g.quietFor(now: 10_000, ms: 0)
        XCTAssertTrue(g.feed(0.9, now: 10_000))
    }

    func testAZeroCooldownSettingQuietsNothing() {
        let g = WakeGate(threshold: 0.5, patience: 1, cooldownSec: 0)
        g.quietFor(now: 10_000)
        XCTAssertTrue(g.feed(0.9, now: 10_000))
    }

    /// iOS-only: a monotonic clock near its end must not trap the listening
    /// thread (Kotlin wraps; Swift would crash on the overflow).
    func testACooldownNearTheEndOfTheClockDoesNotTrap() {
        let g = WakeGate(threshold: 0.5)
        g.quietFor(now: Int64.max - 10)
        XCTAssertTrue(g.inCooldown(now: Int64.max - 1))
        g.quietFor(now: 0, ms: -5)
        XCTAssertTrue(g.feed(0.9, now: 0), "a negative span is no span")
    }

    /// A NaN score is no score, never a wake.
    func testANaNScoreIsSilence() {
        let g = WakeGate(threshold: 0.5, patience: 1, cooldownSec: 0)
        XCTAssertFalse(g.feed(.nan, now: 0))
        XCTAssertNil(g.lastPeak)
    }

    func testTheSettingsFallBackToTheDefaults() {
        XCTAssertEqual(1, WakeGate.patienceDefault)
        XCTAssertEqual(2.0, WakeGate.cooldownSecDefault)
        XCTAssertEqual(1, WakeGate.patienceSetting(nil))
        XCTAssertEqual(3, WakeGate.patienceSetting(3))
        XCTAssertEqual(3, WakeGate.patienceSetting(3.0))
        XCTAssertEqual(3, WakeGate.patienceSetting(Float(3)))
        XCTAssertEqual(3, WakeGate.patienceSetting(Int64(3)))
        XCTAssertEqual(3, WakeGate.patienceSetting(" 3 "))
        XCTAssertEqual(1, WakeGate.patienceSetting(0))
        XCTAssertEqual(5, WakeGate.patienceSetting(9))
        XCTAssertEqual(5, WakeGate.patienceSetting(Int64.max))
        let bad: [Any] = ["x", true, false, 2.5, "3.0", Double.nan, [3]]
        for value in bad {
            XCTAssertEqual(1, WakeGate.patienceSetting(value), "\(value)")
        }
        XCTAssertEqual(2.0, WakeGate.cooldownSetting(nil))
        XCTAssertEqual(0.0, WakeGate.cooldownSetting(0))
        XCTAssertEqual(10.0, WakeGate.cooldownSetting(99))
        XCTAssertEqual(0.0, WakeGate.cooldownSetting(-1))
        XCTAssertEqual(1.5, WakeGate.cooldownSetting("1.5"))
        let badCooldowns: [Any] = ["x", true, Double.nan, Double.infinity, "NaN"]
        for value in badCooldowns {
            XCTAssertEqual(2.0, WakeGate.cooldownSetting(value), "\(value)")
        }
        // The constructor takes them through the same rules.
        XCTAssertEqual(5, WakeGate(threshold: 0.5, patience: 40).patience)
        XCTAssertEqual(10.0, WakeGate(threshold: 0.5, cooldownSec: 60).cooldownSec)
    }

    /// The core pushes `wake.*` as JSON: a JSON `true` is a CFBoolean and
    /// must be refused like Kotlin's `is Boolean`, while a JSON 1 is a number.
    func testJSONValuesAreTakenAsTheCoreSendsThem() throws {
        let data = Data(#"{"t": true, "one": 1, "two": 2.0, "half": 2.5, "cool": 4}"#.utf8)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(1, WakeGate.patienceSetting(object["t"]))
        XCTAssertEqual(2.0, WakeGate.cooldownSetting(object["t"]))
        XCTAssertEqual(1, WakeGate.patienceSetting(object["one"]))
        XCTAssertEqual(2, WakeGate.patienceSetting(object["two"]))
        XCTAssertEqual(1, WakeGate.patienceSetting(object["half"]))
        XCTAssertEqual(4.0, WakeGate.cooldownSetting(object["cool"]))
        XCTAssertEqual(1.0, WakeGate.cooldownSetting(object["one"]), "a JSON 1 is one second, not a boolean")
    }
}
