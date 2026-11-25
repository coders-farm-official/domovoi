import Foundation
import XCTest
@testable import DomovoiKit

/// Rooms and the devices in them (1.5.0, rooms slice M), the iPhone's side of
/// Android 267850b2: the room code's `in=`, the hello's `satellite_id` and
/// `requested_room`, `ready.room` and the `room` frame, `satellite_id_mismatch`,
/// and the household words ("This phone is in the Kitchen", never
/// "satellite"). Android's new cases in `RoomEnrolmentTest`,
/// `RoomProtocolTest` and `RoomRulesTest`, case for case.
final class RoomInARoomTests: XCTestCase {

    private static let repo: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private func enrolFixture() throws -> [String: Any] {
        let data = try Data(contentsOf: Self.repo.appendingPathComponent("docs/uml/spare-device-enrol-fixture.json"))
        return try XCTUnwrap(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    /// The fixture's "basic" payload up to its `&room=`.
    private func base() throws -> String {
        let valid = try XCTUnwrap(try enrolFixture()["valid"] as? [[String: Any]])
        let basic = try XCTUnwrap(valid.first { $0["name"] as? String == "basic" })
        let payload = try XCTUnwrap(basic["payload"] as? String)
        return try XCTUnwrap(payload.components(separatedBy: "&room=").first)
    }

    private func ok(_ payload: String, file: StaticString = #filePath, line: UInt = #line) -> RoomEnrolment? {
        guard let e = RoomEnrolmentParser.parse(payload).enrolment else {
            XCTFail("\(payload) refused: \(RoomEnrolmentParser.parse(payload))", file: file, line: line)
            return nil
        }
        return e
    }

    // MARK: - `in=` (RoomEnrolmentTest)

    func testNoValidCaseOfTheContractAsksForARoom() throws {
        for c in try XCTUnwrap(try enrolFixture()["valid"] as? [[String: Any]]) {
            let e = ok(c["payload"] as? String ?? "")
            XCTAssertNil(e?.inRoom)
            XCTAssertNil(e?.inRoomName)
        }
    }

    /// What the dashboard's "Add a satellite to the Kitchen" makes: the
    /// device's own name in `room`, the room it joins in `in`.
    func testInNamesTheRoomThePhoneJoins() throws {
        let base = try base()
        let e = try XCTUnwrap(ok("\(base)&room=Kitchen%202&in=kitchen"))
        XCTAssertEqual("Kitchen 2", e.roomLabel)
        XCTAssertEqual("kitchen_2", e.roomID)
        XCTAssertEqual("kitchen", e.inRoom)
        XCTAssertEqual("Kitchen", e.inRoomName)
        // In a join link too, and in any order.
        let link = try XCTUnwrap(ok(
            "\(e.coreURL)/join#" + base.replacingOccurrences(of: "domovoi-room:1?", with: "domovoi-room:1?in=kitchen&")
                + "&room=Kitchen%202"
        ))
        XCTAssertEqual(e, link)
    }

    /// A room id as domovoi/room_ids.py `is_room_id` takes one from outside;
    /// anything else is ignored like an unknown key.
    func testAnInThatIsNotARoomIdIsIgnored() throws {
        let base = try base()
        for good in ["kitchen", "room_2nd", "2nd-bedroom", "a", String(repeating: "k", count: 32)] {
            XCTAssertEqual(good, ok("\(base)&room=Kitchen%202&in=\(good)")?.inRoom)
        }
        for bad in ["", "Kitchen", "_kitchen", "-kitchen", String(repeating: "k", count: 33), "kit%20chen", "kit.chen", "k%C3%BCche"] {
            let e = ok("\(base)&room=Kitchen%202&in=\(bad)")
            XCTAssertNil(e?.inRoom, bad)
            XCTAssertNil(e?.inRoomName, bad)
            XCTAssertEqual("kitchen_2", e?.roomID)
        }
        // Given twice is refused, like any other key.
        XCTAssertEqual(.malformed, RoomEnrolmentParser.parse("\(base)&room=Den&in=den&in=kitchen").refusal?.reason)
    }

    func testTheRoomsNameIsTheDevicesNameWithoutItsNumber() {
        XCTAssertEqual("Kitchen", RoomEnrolmentParser.invitedRoomName("Kitchen 2"))
        XCTAssertEqual("Kids' Room", RoomEnrolmentParser.invitedRoomName("Kids' Room 12"))
        XCTAssertEqual("Kitchen phone", RoomEnrolmentParser.invitedRoomName("Kitchen phone"))
        // Only a number: nothing left to name a room by, so it stays.
        XCTAssertEqual("2", RoomEnrolmentParser.invitedRoomName("2"))
        XCTAssertEqual("Room2", RoomEnrolmentParser.invitedRoomName("Room2"))
    }

    // MARK: - the store keeps the room asked for until approved

    func testTheStoreAsksForItsRoomUntilApprovedAndKeepsTheRoomsName() throws {
        let store = RoomStore.inMemory()
        let joining = RoomEnrolment(
            coreURL: "http://192.168.1.20:6370", fingerprint: "SHA256:" + String(repeating: "A", count: 43),
            roomID: "kitchen_2", roomLabel: "Kitchen 2", inRoom: "kitchen", inRoomName: "Kitchen"
        )
        XCTAssertTrue(store.enrol(joining))
        XCTAssertEqual("kitchen", store.requestedRoom)
        XCTAssertEqual("kitchen", store.enrolment?.inRoom)
        XCTAssertEqual("Kitchen", store.roomName)
        store.clearRequestedRoom()
        XCTAssertNil(store.requestedRoom)
        XCTAssertEqual("kitchen", store.roomNameRoom, "kept with the room it names")
        store.noteRoom(RoomPlace(roomID: "den", name: "Den", shared: false))
        store.noteRoom(RoomPlace(roomID: "den", name: "  ", shared: false))
        store.noteRoom(RoomPlace(roomID: "den", name: nil, shared: false))
        XCTAssertEqual("Den", store.roomName)
        XCTAssertEqual("den", store.roomNameRoom)
        // No name for another room overwrites nothing either.
        store.noteRoom(RoomPlace(roomID: "hall", name: nil, shared: false))
        XCTAssertEqual("Den", store.roomName)
        XCTAssertEqual("den", store.roomNameRoom)
        // A new room starts afresh: no room asked for, and only the name the
        // code gives the room it makes, kept with that room.
        let porch = RoomEnrolment(coreURL: joining.coreURL, fingerprint: joining.fingerprint, roomID: "porch", roomLabel: "Porch")
        XCTAssertTrue(store.enrol(porch))
        XCTAssertNil(store.requestedRoom)
        XCTAssertEqual("Porch", store.roomName)
        XCTAssertEqual("porch", store.roomNameRoom)
    }

    func testNoNameNeverOverwritesTheKeptOne() {
        let kitchen = RoomPlace(roomID: "kitchen", name: "Kitchen", shared: false)
        XCTAssertTrue(RoomNameKeeping.nameToKeep(kitchen, keptName: nil, keptFor: nil).map { $0 == ("kitchen", "Kitchen") } ?? false)
        XCTAssertTrue(RoomNameKeeping.nameToKeep(RoomPlace(roomID: "den", name: "Den", shared: false), keptName: "Kitchen", keptFor: "kitchen")
            .map { $0 == ("den", "Den") } ?? false)
        XCTAssertNil(RoomNameKeeping.nameToKeep(kitchen, keptName: "Kitchen", keptFor: "kitchen"))
        XCTAssertTrue(RoomNameKeeping.nameToKeep(kitchen, keptName: "Kitchen", keptFor: nil).map { $0 == ("kitchen", "Kitchen") } ?? false)
        XCTAssertNil(RoomNameKeeping.nameToKeep(RoomPlace(roomID: "kitchen", name: nil, shared: false), keptName: "Kitchen", keptFor: "kitchen"))
        XCTAssertNil(RoomNameKeeping.nameToKeep(RoomPlace(roomID: "den", name: nil, shared: false), keptName: "Kitchen", keptFor: "kitchen"))
        XCTAssertNil(RoomNameKeeping.nameToKeep(RoomPlace(roomID: "kitchen", name: " ", shared: false), keptName: "Kitchen", keptFor: "kitchen"))
    }

    /// The in-memory store keeps the name exactly as the real one does.
    func testTheInMemoryStoreKeepsNamesLikeTheRealOne() {
        let joining = RoomEnrolment(
            coreURL: "http://192.168.1.20:6370", fingerprint: "SHA256:" + String(repeating: "A", count: 43),
            roomID: "kitchen_2", roomLabel: "Kitchen 2", inRoom: "kitchen", inRoomName: "Kitchen"
        )
        let memory = InMemoryRoomStore(enrolment: joining)
        XCTAssertEqual("Kitchen", memory.roomName)
        XCTAssertEqual("kitchen", memory.roomNameRoom)
        memory.noteRoom(RoomPlace(roomID: "den", name: nil, shared: false))
        XCTAssertEqual("Kitchen", memory.roomName)
        memory.noteRoom(RoomPlace(roomID: "den", name: "Den", shared: false))
        XCTAssertEqual("Den", memory.roomName)
        XCTAssertEqual("den", memory.roomNameRoom)
    }

    // MARK: - rooms (1.5.0): satellite-fixtures.json `rooms`

    /// One room-id rule for an id from outside (`in=`, `requested_room`,
    /// `ready.room.room_id`): the core's `in_room_rule`.
    func testTheRoomIdRuleIsTheCoresInRoomRule() throws {
        let rooms = try XCTUnwrap(try RoomTestKit.json("docs/uml/satellite-fixtures.json")["rooms"])
        XCTAssertEqual(#"^[a-z0-9][a-z0-9_-]{0,31}\Z"#, rooms["in_room_rule"]?.stringValue)
        guard case let .array(validJSON)? = rooms["in_room_valid"], case let .array(invalidJSON)? = rooms["in_room_invalid"] else {
            return XCTFail("the fixture's lists")
        }
        let valid = validJSON.compactMap(\.stringValue)
        let invalid = invalidJSON.compactMap(\.stringValue)
        XCTAssertFalse(valid.isEmpty)
        XCTAssertEqual(invalidJSON.count, invalid.count)
        for id in valid {
            XCTAssertTrue(RoomEnrolmentParser.isRoomID(id), id)
            XCTAssertEqual(.string(id), hello(id)["requested_room"], id)
            let frame = RoomJSON.object(["type": .string("room"), "room": .object(["room_id": .string(id), "name": .string("Den")])])
            XCTAssertEqual(.room(RoomPlace(roomID: id, name: "Den", shared: false)), RoomProtocol.parse(frame.serialized), id)
        }
        for id in invalid {
            XCTAssertFalse(RoomEnrolmentParser.isRoomID(id), id)
            XCTAssertNil(hello(id)["requested_room"], id)
            let frame = RoomJSON.object(["type": .string("room"), "room": .object(["room_id": .string(id), "name": .string("Den")])])
            XCTAssertEqual(.room(nil), RoomProtocol.parse(frame.serialized), id)
        }
    }

    /// The core could not read the room's row: `name` is null, never the id.
    func testAnUnnamedRoomHasNoName() throws {
        let rooms = try XCTUnwrap(try RoomTestKit.json("docs/uml/satellite-fixtures.json")["rooms"])
        let ready = RoomJSON.object([
            "type": .string("ready"), "satellite_id": .string("kitchen_2"), "room_id": .string("kitchen"),
            "room": try XCTUnwrap(rooms["ready_room_unnamed"]),
        ])
        guard case let .ready(_, _, _, _, _, place) = RoomProtocol.parse(ready.serialized) else { return XCTFail() }
        XCTAssertEqual(RoomPlace(roomID: "kitchen", name: nil, shared: false, claim: false), place)
        XCTAssertEqual(
            .room(RoomPlace(roomID: "den", name: nil, shared: false, claim: false)),
            RoomProtocol.parse(try XCTUnwrap(rooms["room_move_unnamed"]).serialized)
        )
        XCTAssertEqual(
            .room(RoomPlace(roomID: "den", name: "Den", shared: false, claim: false)),
            RoomProtocol.parse(try XCTUnwrap(rooms["room_move"]).serialized)
        )
    }

    func testAnUnnamedRoomKeepsTheLastNameForTheSameRoomOnly() {
        let unnamed = RoomPlace(roomID: "kitchen", name: nil, shared: false)
        XCTAssertEqual("Kitchen", RoomStatusText.roomName(status(place: unnamed), kept: "Kitchen", keptFor: "kitchen", label: "Kitchen 2"))
        let den = RoomPlace(roomID: "den", name: nil, shared: false)
        XCTAssertNil(RoomStatusText.roomName(status(place: den), kept: "Kitchen", keptFor: "kitchen", label: "Kitchen 2"))
        XCTAssertNil(RoomStatusText.roomName(status(place: unnamed), kept: "Kitchen", keptFor: nil, label: "Kitchen 2"))
        XCTAssertNil(RoomStatusText.roomName(status(place: unnamed), kept: nil, keptFor: nil, label: "Kitchen 2"))
        XCTAssertEqual("This phone is in this room", RoomStatusText.whereLine(status(place: den), roomName: nil, approved: true))
        XCTAssertEqual(
            "This phone is in this room, with other devices",
            RoomStatusText.whereLine(status(place: RoomPlace(roomID: "den", name: nil, shared: true)), roomName: nil, approved: true)
        )
        XCTAssertEqual(
            "This phone will join this room",
            RoomStatusText.whereLine(status(link: .awaitingApproval(code: "305117", removed: false)), roomName: nil, approved: false)
        )
        XCTAssertEqual("This room", RoomStatusText.title(nil))
        XCTAssertEqual("Kitchen", RoomStatusText.title("Kitchen"))
        XCTAssertEqual("this room", RoomStatusText.theRoom(nil))
        XCTAssertEqual("this room", RoomStatusText.theRoom(" "))
    }

    // MARK: - the protocol (RoomProtocolTest)

    private func hello(_ requested: String?) -> RoomProtocol.Frame {
        RoomProtocol.hello(
            roomID: "kitchen_2", wakeWord: "hey_jarvis", pairingToken: "t", approvalCode: "305117",
            echoCanceller: true, requestedRoom: requested
        )
    }

    func testTheHelloNamesThePhoneAndTheRoomItAskedFor() {
        let h = hello("kitchen")
        XCTAssertEqual(.string("kitchen_2"), h["room_id"])
        XCTAssertEqual(.string("kitchen_2"), h["satellite_id"])
        XCTAssertEqual(.string("kitchen"), h["requested_room"])
        XCTAssertTrue(RoomProtocol.streamURL(coreURL: "http://192.168.1.20:6370", roomID: "kitchen_2").hasSuffix("/kitchen_2"))
        // Approved (no room asked for), or a value that is not a room id: no key.
        XCTAssertNil(hello(nil)["requested_room"])
        XCTAssertNil(hello("Kitchen")["requested_room"])
        XCTAssertEqual(.string("2nd-floor"), hello("2nd-floor")["requested_room"])
    }

    func testReadyNamesThePhoneAndItsRoom() throws {
        let fixtures = try RoomTestKit.json("docs/uml/satellite-fixtures.json")
        let ready = try XCTUnwrap(fixtures["devices"]?["android_room"]?["ready"])
        guard case let .ready(_, _, _, _, satellite, room) = RoomProtocol.parse(ready.serialized) else {
            return XCTFail("the fixture's ready did not parse")
        }
        XCTAssertEqual("nursery", satellite)
        XCTAssertEqual(RoomPlace(roomID: "nursery", name: "nursery", shared: false, claim: false), room)
        // A room other devices share, with its name and wake claims: the
        // claim flag is kept, the phone does not claim yet.
        let turns = try XCTUnwrap(fixtures["room_turns"])
        let shared = RoomJSON.object([
            "type": .string("ready"), "satellite_id": .string("kitchen_2"), "room_id": .string("kitchen"),
            "room": try XCTUnwrap(turns["ready_room"]),
        ])
        guard case let .ready(_, _, _, _, id, place) = RoomProtocol.parse(shared.serialized) else { return XCTFail() }
        XCTAssertEqual(RoomPlace(roomID: "kitchen", name: "Kitchen", shared: true, claim: true), place)
        XCTAssertEqual("kitchen_2", id)
        XCTAssertFalse(RoomProtocol.supports(echoCanceller: true).contains(turns["hello_supports_adds"]?.stringValue ?? "wake_claim"))
        // An older core says neither.
        XCTAssertEqual(
            .ready(features: [], roomID: "kitchen", botName: nil, roomVoice: nil),
            RoomProtocol.parse(#"{"type":"ready","room_id":"kitchen","features":[]}"#)
        )
    }

    /// An admin moved the phone: it carries on, connected, in the new room.
    func testTheRoomFrameIsTheNewRoom() {
        XCTAssertEqual(
            .room(RoomPlace(roomID: "den", name: "Den", shared: true, claim: true)),
            RoomProtocol.parse(#"{"type":"room","room":{"room_id":"den","name":"Den","shared":true,"claim":true}}"#)
        )
        // `claim` is optional; a blank, null or missing name is no name,
        // never the id.
        for name in [#""name":" ","#, #""name":null,"#, ""] {
            XCTAssertEqual(
                .room(RoomPlace(roomID: "den", name: nil, shared: false)),
                RoomProtocol.parse(#"{"type":"room","room":{"room_id":"den","# + name + #""shared":false}}"#),
                name
            )
        }
        // No room, or no room id: nothing to carry on in.
        XCTAssertEqual(.room(nil), RoomProtocol.parse(#"{"type":"room"}"#))
        XCTAssertEqual(.room(nil), RoomProtocol.parse(#"{"type":"room","room":{"name":"Den"}}"#))
        XCTAssertEqual(.room(nil), RoomProtocol.parse(#"{"type":"room","room":{"room_id":"Den Room"}}"#))
    }

    func testAMismatchedIdIsARefusal() {
        guard case let .error(reason, _, code) = RoomProtocol.parse(
            #"{"type":"error","reason":"satellite_id_mismatch","message":"the hello's satellite_id is not the one in the path"}"#
        ) else { return XCTFail() }
        let v = RoomSessionRules.verdict(reason: reason, code: code, current: "305117")
        XCTAssertEqual(.rejected(reason: RoomSessionRules.mismatchText), v.link)
        XCTAssertEqual("satellite_id_mismatch", v.outcome)
        XCTAssertEqual(RoomSessionRules.refusedRetryMS, RoomSessionRules.retryAfterMS("satellite_id_mismatch", backoff: 1_000))
        XCTAssertFalse(RoomSessionRules.mismatchText.lowercased().contains("satellite"), "household words")
    }

    // MARK: - household words (RoomRulesTest)

    private let kitchen = RoomPlace(roomID: "kitchen", name: "Kitchen", shared: false)

    private func status(link: RoomLink = .ready, place: RoomPlace? = nil) -> RoomStatus {
        RoomStatus(link: link, wakeReady: true, place: place)
    }

    func testTheScreenSaysTheRoomsNameNeverItsId() {
        // The server's name wins, then the kept one, then the room code's.
        XCTAssertEqual("Kitchen", RoomStatusText.roomName(status(place: kitchen), kept: "Den", keptFor: "den", label: "Kitchen 2"))
        XCTAssertEqual("Den", RoomStatusText.roomName(status(), kept: "Den", keptFor: "den", label: "Kitchen 2"))
        XCTAssertEqual("Kitchen 2", RoomStatusText.roomName(nil, kept: nil, keptFor: nil, label: "Kitchen 2"))
        XCTAssertEqual("Kitchen 2", RoomStatusText.roomName(nil, kept: " ", keptFor: nil, label: "Kitchen 2"))
        XCTAssertNil(RoomStatusText.roomName(nil, kept: nil, keptFor: nil, label: nil))
    }

    func testThisPhoneIsInTheKitchen() {
        XCTAssertEqual("This phone is in the Kitchen", RoomStatusText.whereLine(status(place: kitchen), roomName: "Kitchen", approved: true))
        let shared = RoomPlace(roomID: "kitchen", name: "Kitchen", shared: true)
        XCTAssertEqual(
            "This phone is in the Kitchen, with other devices",
            RoomStatusText.whereLine(status(place: shared), roomName: "Kitchen", approved: true)
        )
        // Not yet approved: where it is going (the room code's `in=`).
        let parked = status(link: .awaitingApproval(code: "305117", removed: false))
        XCTAssertEqual("This phone will join the Kitchen", RoomStatusText.whereLine(parked, roomName: "Kitchen", approved: false))
        XCTAssertEqual("This phone is in the Kitchen", RoomStatusText.whereLine(nil, roomName: "Kitchen", approved: true))
        XCTAssertEqual("This phone will join the Kitchen.", RoomStatusText.joinLine("Kitchen"))
        XCTAssertEqual("Join the Kitchen", RoomStatusText.joinButton("Kitchen"))
        // A name that already reads as one keeps its own words.
        XCTAssertEqual("The Den", RoomStatusText.theRoom("The Den"))
        XCTAssertEqual("Alex's room", RoomStatusText.theRoom("Alex's room"))
        XCTAssertEqual("the Kids' Room", RoomStatusText.theRoom("Kids' Room"))
        XCTAssertEqual("the Theatre", RoomStatusText.theRoom("Theatre"))
    }

    func testRoomModeIsOfferedAsThisPhoneInARoom() {
        XCTAssertEqual("Use this phone in a room", RoomStatusText.entryTitle)
        XCTAssertEqual(RoomStatusText.entryTitle, RoomModeStrings.entryTitle)
        XCTAssertEqual(RoomStatusText.entryTitle, RoomModeStrings.explainerTitle)
        XCTAssertEqual("this phone", RoomModeStrings.entrySection)
        let waiting = RoomStatusText.detail(status(link: .awaitingApproval(code: "305117", removed: false)), running: true, wakeWord: "hey_jarvis")
        XCTAssertTrue(waiting?.contains("approve this phone") == true, waiting ?? "")
        let removed = RoomStatusText.detail(status(link: .awaitingApproval(code: "305117", removed: true)), running: true, wakeWord: "hey_jarvis")
        XCTAssertTrue(removed?.hasPrefix("This phone was removed") == true, removed ?? "")
    }

    /// The setup screen with an `in=` code: "This phone will join the
    /// Kitchen", the field is the phone's own name, the button "Join the
    /// Kitchen", and what it enrols keeps the room it asks for.
    @MainActor
    func testTheRoomCodeScreenSaysWhichRoomThePhoneJoins() throws {
        let base = try base()
        let plain = try XCTUnwrap(ok("\(base)&room=Kitchen"))
        let joining = try XCTUnwrap(ok("\(base)&room=Kitchen%202&in=kitchen"))
        XCTAssertNil(plain.inRoomName)
        XCTAssertEqual("Kitchen", joining.inRoomName)
        XCTAssertEqual("This phone will join the Kitchen.", joining.inRoomName.map(RoomStatusText.joinLine))
    }

    /// The phone never says "satellite" (the dashboard's admin pages do): no
    /// string literal with a space in it in room mode's sources, the app's or
    /// the package's, names one. Protocol names (`satellite_id`,
    /// `satellite-sounds`) are words, not sentences. Android's
    /// `roomModeNeverSaysSatellite`.
    func testRoomModeNeverSaysSatellite() throws {
        let folders = ["ios/Packages/DomovoiKit/Sources/DomovoiKit/RoomMode", "ios/Domovoi/RoomMode"]
        let literal = try NSRegularExpression(pattern: #""((?:[^"\\]|\\.)*)""#)
        let interpolation = try NSRegularExpression(pattern: #"\\\([^)]*\)"#)
        var said: [String] = []
        var files = 0
        for folder in folders {
            let dir = Self.repo.appendingPathComponent(folder)
            let names = try FileManager.default.contentsOfDirectory(atPath: dir.path).filter { $0.hasSuffix(".swift") }
            for name in names {
                files += 1
                let text = try String(contentsOf: dir.appendingPathComponent(name), encoding: .utf8)
                for raw in text.components(separatedBy: "\n") {
                    let code = raw.trimmingCharacters(in: .whitespaces)
                    if code.hasPrefix("//") || code.hasPrefix("*") || code.hasPrefix("/*") { continue }
                    let line = code.components(separatedBy: " // ").first ?? code
                    let range = NSRange(line.startIndex..., in: line)
                    for m in literal.matches(in: line, range: range) {
                        guard let r = Range(m.range(at: 1), in: line) else { continue }
                        let inner = String(line[r])
                        // An interpolation's code (`\(id)`) is not words.
                        let words = interpolation.stringByReplacingMatches(
                            in: inner, range: NSRange(inner.startIndex..., in: inner), withTemplate: ""
                        )
                        if words.contains(" ") && words.lowercased().contains("satellite") { said.append("\(name): \(words)") }
                    }
                }
            }
        }
        XCTAssertGreaterThan(files, 20, "the scan is not reading room mode's sources")
        XCTAssertEqual([], said)
    }
}
