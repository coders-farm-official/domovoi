import Foundation
import XCTest
@testable import DomovoiKit

/// The room phone's battery between the minute's health frames (Android's
/// `BatteryReporterTest`; domovoi/room_battery.py warns at 20 % and 10 %):
/// the charger going on or off sends at once, a 5 % move at most once per
/// 15 s, a new session at once, and anything smaller waits for the minute.
/// Also iOS's battery -> the core's object (ROOMMODE-MAP §3.10).
final class RoomBatteryReporterTests: XCTestCase {

    private func b(_ pct: Int?, charging: Bool? = false, onPower: Bool? = false, plugged: String? = nil) -> RoomBattery {
        RoomBattery(percent: pct, charging: charging, onPower: onPower, tempC: 30, plugged: plugged)
    }

    func testANewSessionSendsAtOnce() {
        let r = RoomBatteryReporter()
        XCTAssertTrue(r.onChange(b(64), nowMS: 0))
        r.sent(b(64), nowMS: 0)
        XCTAssertFalse(r.onChange(b(63), nowMS: 1_000))
        r.reset()
        XCTAssertTrue(r.onChange(b(63), nowMS: 2_000), "reset: the next change sends")
    }

    func testFivePercentSendsButLessWaitsForTheMinute() {
        let r = RoomBatteryReporter()
        r.sent(b(40), nowMS: 0)
        for p in stride(from: 39, through: 36, by: -1) {
            XCTAssertFalse(r.onChange(b(p), nowMS: Int64(60_000 * (40 - p))), "\(p)%")
        }
        XCTAssertTrue(r.onChange(b(35), nowMS: 300_000))
        r.sent(b(35), nowMS: 300_000)
        XCTAssertFalse(r.onChange(b(34), nowMS: 310_000))
        XCTAssertTrue(r.onChange(b(40), nowMS: 400_000))
    }

    func testAFiveStepInsideTheGapWaits() {
        let r = RoomBatteryReporter()
        r.sent(b(30), nowMS: 0)
        XCTAssertFalse(r.onChange(b(25), nowMS: RoomBatteryReporter.defaultMinGapMS - 1))
        XCTAssertTrue(r.onChange(b(25), nowMS: RoomBatteryReporter.defaultMinGapMS))
    }

    func testTheChargerGoingOnOrOffSendsAtOnce() {
        let r = RoomBatteryReporter()
        r.sent(b(18), nowMS: 0)
        XCTAssertTrue(r.onChange(b(18, charging: true, onPower: true), nowMS: 100), "plugged in")
        r.sent(b(18, charging: true, onPower: true), nowMS: 100)
        XCTAssertFalse(r.onChange(b(19, charging: true, onPower: true), nowMS: 200))
        XCTAssertFalse(r.onChange(b(19, charging: false, onPower: true), nowMS: 300), "held full on the charger is still on it")
        XCTAssertTrue(r.onChange(b(19, charging: false, onPower: false), nowMS: 400), "unplugged")
    }

    func testLowAndOffTheChargerEveryPercentCountsAndTheGapSaysWhen() {
        let r = RoomBatteryReporter()
        r.sent(b(9), nowMS: 0)
        XCTAssertEqual(12_000, r.decide(b(8), nowMS: 3_000))
        XCTAssertEqual(0, r.decide(b(8), nowMS: 15_000))
        r.sent(b(8), nowMS: 15_000)
        XCTAssertNil(r.decide(b(8), nowMS: 40_000))
        XCTAssertEqual(0, r.decide(b(4), nowMS: 40_000))
        r.sent(b(4, charging: true, onPower: true), nowMS: 50_000)
        XCTAssertNil(r.decide(b(6, charging: true, onPower: true), nowMS: 80_000))
        r.sent(b(30), nowMS: 100_000)
        XCTAssertNil(r.decide(b(27), nowMS: 200_000))
    }

    func testTheMinutesFrameMovesTheStep() {
        let r = RoomBatteryReporter()
        r.sent(b(50), nowMS: 0)
        r.sent(b(47), nowMS: 60_000)
        XCTAssertFalse(r.onChange(b(45), nowMS: 90_000))
        XCTAssertTrue(r.onChange(b(42), nowMS: 120_000))
    }

    func testNothingKnownSendsNothing() {
        XCTAssertFalse(RoomBatteryReporter().onChange(RoomBattery(percent: nil, charging: nil, onPower: nil), nowMS: 0))
    }

    func testIOSsBatteryBecomesTheCoresObject() {
        // UIDevice.BatteryState: 0 unknown, 1 unplugged, 2 charging, 3 full.
        XCTAssertEqual(RoomBattery(percent: 15, charging: false, onPower: false), RoomBatteryReporter.battery(level: 0.15, stateRaw: 1))
        XCTAssertEqual(RoomBattery(percent: 64, charging: true, onPower: true), RoomBatteryReporter.battery(level: 0.64, stateRaw: 2))
        let full = RoomBatteryReporter.battery(level: 1.0, stateRaw: 3)
        XCTAssertEqual(RoomBattery(percent: 100, charging: false, onPower: true), full)
        XCTAssertEqual(true, full.onCharger)
        let unknown = RoomBatteryReporter.battery(level: -1, stateRaw: 0)
        XCTAssertEqual(RoomBattery(percent: nil, charging: nil, onPower: nil), unknown)
        XCTAssertNil(unknown.onCharger)
        XCTAssertNil(RoomBatteryReporter.battery(level: 0.5, stateRaw: 2).plugged, "iOS never says which charger")
        XCTAssertNil(RoomBatteryReporter.battery(level: 0.5, stateRaw: 2).tempC)
    }

    func testTheFrameSaysWhichChargerWhenAPhoneCan() {
        var f = RoomHealthSample.Facts(
            seq: 1, ts: 1, rssKB: nil, cpuPct: nil, tempC: nil, uptimeS: 1, procUptimeS: 1, coreOK: true,
            micExpected: true, micFPS: 33.3, idle: true, wsUp: true, wsDownForS: 0, reconnects: 0,
            wifiRxMbits: nil, wifiSSID: nil, wifiSignalDBM: nil, batteryPercent: 15, batteryCharging: true,
            onPower: true, micBlocked: false, wakeLoopAgeS: nil, threads: nil, plugged: "usb"
        )
        XCTAssertEqual(.string("usb"), RoomHealthSample.build(f)["battery"]?["plugged"])
        XCTAssertEqual(.int(15), RoomHealthSample.build(f)["battery"]?["percent"])
        f.plugged = nil
        XCTAssertNil(RoomHealthSample.build(f)["battery"]?["plugged"])
    }
}
