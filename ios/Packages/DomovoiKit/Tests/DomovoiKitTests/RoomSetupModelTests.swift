import Foundation
import XCTest
@testable import DomovoiKit

@MainActor
final class FakeRoomPermissions: RoomSetupPermissions {
    var micGranted = false
    var micAnswer = true
    var cameraAvailable = true
    var cameraGranted = false
    var cameraAnswer = true
    var asked: [String] = []

    func requestMic(_ done: @escaping @MainActor (Bool) -> Void) {
        asked.append("mic")
        if micAnswer { micGranted = true }
        done(micAnswer)
    }
    func requestNotifications() { asked.append("notifications") }
    func requestCamera(_ done: @escaping @MainActor (Bool) -> Void) {
        asked.append("camera")
        if cameraAnswer { cameraGranted = true }
        done(cameraAnswer)
    }
}

/// Setup, one decision a screen (`RoomSetupActivity.kt`): the explainer
/// is read to the end first, the microphone is asked for in context, and
/// nothing becomes a room until the last button.
@MainActor
final class RoomSetupModelTests: XCTestCase {

    static let payload =
        "domovoi-room:1?core=http%3A%2F%2F192.168.1.20%3A6370&fp=SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA&room=Kitchen"

    private var begun: [RoomEnrolment] = []

    private func model(
        payload: String? = nil,
        lock: FixedRoomLockFacts = FixedRoomLockFacts(),
        permissions: FakeRoomPermissions = FakeRoomPermissions(),
        refusal: String? = nil,
        keeps: Bool = true
    ) -> RoomSetupModel {
        RoomSetupModel(
            payload: payload,
            lock: lock,
            permissions: permissions,
            micRefusal: { refusal },
            onBegin: { [unowned self] e in
                self.begun.append(e)
                return keeps
            }
        )
    }

    func testStartWorksOnlyOnceTheExplainerWasReadToTheEnd() {
        let m = model()
        m.start()
        XCTAssertEqual(.explain, m.step, "Start room mode before the end of the explainer")
        m.readToTheEnd = true
        m.start()
        XCTAssertEqual(.microphone, m.step)
    }

    func testTheMicrophoneStepIsSkippedWhenItIsAlreadyAllowed() {
        let p = FakeRoomPermissions()
        p.micGranted = true
        let m = model(permissions: p)
        m.readToTheEnd = true
        m.start()
        XCTAssertEqual(.code, m.step)
        XCTAssertEqual([], p.asked, "nothing asked for that was already given")
    }

    func testAllowAsksForTheMicAndTheNoticeAndARefusalSaysWhy() {
        let p = FakeRoomPermissions()
        p.micAnswer = false
        let m = model(permissions: p)
        m.readToTheEnd = true
        m.start()
        m.allowMic()
        XCTAssertEqual(["notifications", "mic"], p.asked)
        XCTAssertTrue(m.micDenied)
        XCTAssertEqual(.microphone, m.step)
        p.micAnswer = true
        m.allowMic()
        XCTAssertFalse(m.micDenied)
        XCTAssertEqual(.code, m.step)
    }

    func testAHouseholdPhoneWithoutAPasscodeIsHeldAfterTheExplainer() {
        let m = model(lock: FixedRoomLockFacts(holdsHouseholdToken: true, deviceSecure: false))
        XCTAssertFalse(m.lockOK)
        let ok = model(lock: FixedRoomLockFacts(holdsHouseholdToken: true, deviceSecure: true))
        XCTAssertTrue(ok.lockOK)
    }

    func testAPastedCodeShowsTheServerTheKeyAndTheRoom() throws {
        let m = model()
        m.setText("  " + Self.payload + "\n")
        let e = try XCTUnwrap(m.enrolment)
        XCTAssertNil(m.refusal)
        XCTAssertEqual("Server: 192.168.1.20:6370", RoomModeStrings.server(e.coreURL))
        XCTAssertEqual("Server key: SHA256:4gq+pNW/lnGij…", RoomModeStrings.serverKey(e.fingerprint))
        XCTAssertEqual("Kitchen", m.shownName)
        XCTAssertEqual("kitchen", m.roomID)
        XCTAssertTrue(m.canBecome)
        XCTAssertTrue(begun.isEmpty, "a paste only fills the form")
    }

    func testAWrongCodeSaysWhy() {
        let m = model()
        m.setText("domovoi-room:2?core=x")
        XCTAssertNil(m.enrolment)
        XCTAssertEqual(true, m.refusal?.contains("version 2"))
        m.setText("hello")
        XCTAssertEqual(true, m.refusal?.contains("not a Domovoi room code"))
        XCTAssertFalse(m.canBecome)
    }

    func testARenamedRoomIsHeldToTheRulesAndBecomesThatRoom() throws {
        let m = model(payload: Self.payload)
        m.editedName = "!!!"
        XCTAssertNotNil(m.nameRefusal)
        XCTAssertFalse(m.canBecome)
        m.editedName = "  Back Porch "
        XCTAssertNil(m.nameRefusal)
        XCTAssertTrue(m.become())
        let e = try XCTUnwrap(begun.first)
        XCTAssertEqual("back_porch", e.roomID)
        XCTAssertEqual("Back Porch", e.roomLabel)
        XCTAssertEqual("http://192.168.1.20:6370", e.coreURL)
    }

    func testAMicSomeoneElseHoldsStopsTheLastButton() {
        let m = model(payload: Self.payload, refusal: "A drop-in call has the microphone.")
        XCTAssertFalse(m.become())
        XCTAssertEqual("A drop-in call has the microphone. Finish that first.", m.problem)
        XCTAssertTrue(begun.isEmpty)
    }

    func testAKeyTheKeychainWouldNotKeepIsSaid() {
        let m = model(payload: Self.payload, keeps: false)
        XCTAssertFalse(m.become())
        XCTAssertEqual(RoomModeStrings.keyNotKept, m.problem)
    }

    func testANewTextForgetsATypedName() {
        let m = model(payload: Self.payload)
        m.editedName = "Den"
        m.setText(Self.payload.replacingOccurrences(of: "Kitchen", with: "Hall"))
        XCTAssertNil(m.editedName)
        XCTAssertEqual("Hall", m.shownName)
    }

    func testTheScannerIsOfferedAndOpensOnlyWithACamera() {
        let p = FakeRoomPermissions()
        let m = model(permissions: p)
        m.scan()
        XCTAssertTrue(m.scanning)
        m.scanned(Self.payload)
        XCTAssertFalse(m.scanning)
        XCTAssertEqual("kitchen", m.roomID)

        let none = FakeRoomPermissions()
        none.cameraAvailable = false
        let n = model(permissions: none)
        n.scan()
        XCTAssertFalse(n.scanning)
        XCTAssertEqual(RoomModeStrings.noCamera, n.problem)

        let refused = FakeRoomPermissions()
        refused.cameraAnswer = false
        let r = model(permissions: refused)
        r.scan()
        XCTAssertFalse(r.scanning)
        XCTAssertEqual(RoomModeStrings.noCamera, r.problem)
    }
}
