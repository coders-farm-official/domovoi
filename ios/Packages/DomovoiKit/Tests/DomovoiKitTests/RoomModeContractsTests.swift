import Foundation
import XCTest
@testable import DomovoiKit

/// Room mode's shared contracts (RoomModeContracts.swift): the rules the
/// three units' stores and frames share, held here once so R2's engine and
/// R3's store cannot learn them differently.
final class RoomModeContractsTests: XCTestCase {

    /// `RoomStore.thresholdValue` (`RoomStore.kt:203-205`): 0-1 inclusive,
    /// anything else (or nothing) keeps the threshold the room has.
    func testAPushedThresholdIsTakenOnlyInsideZeroToOne() {
        XCTAssertEqual(0.0, RoomWakeSettings.thresholdValue(0.0))
        XCTAssertEqual(1.0, RoomWakeSettings.thresholdValue(1.0))
        XCTAssertEqual(0.62, RoomWakeSettings.thresholdValue(0.62))
        XCTAssertNil(RoomWakeSettings.thresholdValue(nil))
        XCTAssertNil(RoomWakeSettings.thresholdValue(1.01))
        XCTAssertNil(RoomWakeSettings.thresholdValue(-0.1))
        XCTAssertNil(RoomWakeSettings.thresholdValue(.nan))

        let store = InMemoryRoomStore()
        XCTAssertEqual("hey_jarvis", store.wakeWord)
        XCTAssertEqual(0.5, store.wakeThreshold)
        store.setWakeWord("hey_house", threshold: 0.7)
        XCTAssertEqual("hey_house", store.wakeWord)
        XCTAssertEqual(0.7, store.wakeThreshold)
        // A push without a usable threshold keeps it (the Pi's rule, 220085d8).
        store.setWakeWord("hey_jarvis", threshold: 7)
        XCTAssertEqual("hey_jarvis", store.wakeWord)
        XCTAssertEqual(0.7, store.wakeThreshold)
    }

    /// `RoomStore.setApprovalCode` and `RoomSessionRules`: 4-8 digits.
    func testOnlyAnApprovalCodeOfFourToEightDigitsIsKept() {
        let store = InMemoryRoomStore(approvalCode: "123456")
        store.setApprovalCode("12a456")
        XCTAssertEqual("123456", store.approvalCode)
        store.setApprovalCode("123")
        XCTAssertEqual("123456", store.approvalCode)
        store.setApprovalCode("98765432")
        XCTAssertEqual("98765432", store.approvalCode)
        store.renewApprovalCode()
        let code = store.approvalCode ?? ""
        XCTAssertEqual(6, code.count)
        XCTAssertTrue(RoomWakeSettings.isApprovalCode(code))
        XCTAssertFalse(RoomWakeSettings.isApprovalCode("１２３４"), "fullwidth digits are not ASCII digits")
    }

    /// `secrets.token_urlsafe(32)`: 43 URL-safe characters, never the same twice.
    func testAPairingKeyIsThirtyTwoUrlSafeBytes() {
        let a = RoomWakeSettings.newPairingKey()
        let b = RoomWakeSettings.newPairingKey()
        XCTAssertEqual(43, a.count)
        XCTAssertNotEqual(a, b)
        let allowed = Set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        XCTAssertTrue(a.allSatisfy { allowed.contains($0) }, a)
    }

    /// The frame JSON: keys in code-point order, whole numbers as integers,
    /// a Double as a float, Python's escaping.
    func testRoomJsonIsCompactAndOrdered() {
        let frame: RoomJSON = .object([
            "type": .string("utterance_start"),
            "utt": .int(3),
            "wake_score": .double(0.613),
            "cooldown": .double(2),
            "greeting_played": .bool(false),
            "last_voiced_frame": .null,
            "Z": .string("é\"\\\n\u{01}"),
        ])
        XCTAssertEqual(
            #"{"Z":"é\"\\\n\u0001","cooldown":2.0,"greeting_played":false,"last_voiced_frame":null,"#
                + #""type":"utterance_start","utt":3,"wake_score":0.613}"#,
            frame.serialized
        )
        XCTAssertEqual("[1,null]", RoomJSON.array([.int(1), .double(.infinity)]).serialized)
    }

    /// The feed never hands over exact zeros (which the room calls a blocked
    /// microphone), loops a recording, and reads nothing once stopped.
    func testTheFeedMicrophoneIsNeverSilentZerosAndStops() throws {
        let quiet = RoomFeedPCMSource(paced: false)
        var frame = [Int16](repeating: 0, count: RoomAudioFrame.samples)
        XCTAssertEqual(-1, quiet.read(into: &frame), "a source reads nothing before start")
        try quiet.start(call: false)
        XCTAssertEqual(RoomAudioFrame.samples, quiet.read(into: &frame))
        XCTAssertFalse(frame.contains(0))
        XCTAssertLessThanOrEqual(frame.map { abs(Int($0)) }.max() ?? 99, 3)
        quiet.stop()
        XCTAssertEqual(-1, quiet.read(into: &frame))

        let recorded = RoomFeedPCMSource(samples: [5, 6, 7], paced: false)
        try recorded.start(call: true)
        var four = [Int16](repeating: 0, count: 4)
        XCTAssertEqual(4, recorded.read(into: &four))
        XCTAssertEqual([5, 6, 7, 5], four)
    }

    /// `RoomDevice.Battery.onCharger` (`RoomDevice.kt:24-26`).
    func testOnChargerIsChargingOrHeldFull() {
        XCTAssertEqual(true, RoomBattery(percent: 100, charging: false, onPower: true).onCharger)
        XCTAssertEqual(true, RoomBattery(percent: 40, charging: true, onPower: nil).onCharger)
        XCTAssertEqual(false, RoomBattery(percent: 40, charging: false, onPower: false).onCharger)
        XCTAssertNil(RoomBattery(percent: 40, charging: nil, onPower: nil).onCharger)
    }
}
