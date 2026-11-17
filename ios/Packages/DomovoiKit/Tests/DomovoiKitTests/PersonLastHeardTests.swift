import Foundation
import XCTest
@testable import DomovoiKit

/// "last heard in", the left tile of a person's profile header (owner
/// decision, 1.3.0; the web People page's `heardIn`, 838da8b6): the room of
/// their newest conversation row, "the phone" for a phone's chat mic, "chat"
/// for a turn with no room, and "—" before they have said anything.
///
/// The strings are spelt out rather than read from ``PeopleStrings``, so a
/// change to the wording fails here.
final class PersonLastHeardTests: XCTestCase {

    private func turn(_ id: Int, room: String?, at: String? = "2026-11-15T10:00:00Z") -> PersonTurn {
        PersonTurn(id: id, at: at, roomID: room)
    }

    // MARK: - where one turn was heard

    func testARoomIsItsName() {
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: "kitchen")), "kitchen")
    }

    /// Dashes and underscores read as spaces, as the web's `heardIn` does.
    func testARoomIdReadsWithSpaces() {
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: "living_room")), "living room")
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: "guest-bed__room")), "guest bed room")
    }

    /// A phone's chat mic logs as room `phone-<device>`.
    func testAPhoneRoomIsThePhone() {
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: "phone-3f9a")), "the phone")
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: " phone-3f9a ")), "the phone", "trimmed first")
        // Only the prefix counts: a room merely called something with "phone" in it is a room.
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: "phonebooth")), "phonebooth")
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: "my-phone-nook")), "my phone nook")
    }

    /// A turn with no room came from chat: nil, empty or blank.
    func testNoRoomIsChat() {
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: nil)), "chat")
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: "")), "chat")
        XCTAssertEqual(PersonLastHeard.place(turn(1, room: "   ")), "chat")
    }

    /// Never heard: an em dash, U+2014.
    func testNeverHeardIsADash() {
        XCTAssertEqual(PersonLastHeard.place(nil), "\u{2014}")
        XCTAssertEqual(PersonLastHeard.place(of: []), "\u{2014}")
    }

    // MARK: - which turn is the newest

    /// The server answers newest first; the first row is the one.
    func testTheNewestRowWinsInTheServersOrder() {
        let turns = [
            turn(3, room: "phone-abc", at: "2026-11-15T10:05:00Z"),
            turn(2, room: "kitchen", at: "2026-11-15T10:00:00Z"),
            turn(1, room: nil, at: "2026-11-14T09:00:00Z"),
        ]
        XCTAssertEqual(PersonLastHeard.newest(turns)?.id, 3)
        XCTAssertEqual(PersonLastHeard.place(of: turns), "the phone")
    }

    /// Read by time, so an answer in another order still names the newest
    /// turn's room, never the first row's.
    func testTheNewestRowWinsWhateverTheOrder() {
        let turns = [
            turn(1, room: "study", at: "2026-11-14T09:00:00Z"),
            turn(3, room: nil, at: "2026-11-15T10:05:00Z"),
            turn(2, room: "kitchen", at: "2026-11-15T10:00:00Z"),
        ]
        XCTAssertEqual(PersonLastHeard.newest(turns)?.id, 3)
        XCTAssertEqual(PersonLastHeard.place(of: turns), "chat")
    }

    /// Times with different offsets are compared as instants, not as text.
    func testOffsetsAreComparedAsInstants() {
        let turns = [
            turn(1, room: "study", at: "2026-11-15T10:00:00Z"),
            // 06:30 at -05:00 is 11:30Z: the newer of the two.
            turn(2, room: "porch", at: "2026-11-15T06:30:00-05:00"),
        ]
        XCTAssertEqual(PersonLastHeard.place(of: turns), "porch")
    }

    /// A tie goes to the earlier row, which the server put first.
    func testATieKeepsTheServersFirstRow() {
        let turns = [
            turn(1, room: "kitchen", at: "2026-11-15T10:00:00Z"),
            turn(2, room: "study", at: "2026-11-15T10:00:00Z"),
        ]
        XCTAssertEqual(PersonLastHeard.place(of: turns), "kitchen")
    }

    /// Only rows with a readable time compete; with none readable, the
    /// server's first row wins.
    func testRowsWithoutATimeFallBackToTheServersOrder() {
        XCTAssertEqual(
            PersonLastHeard.place(of: [
                turn(1, room: "attic", at: nil),
                turn(2, room: "kitchen", at: "2026-11-15T10:00:00Z"),
                turn(3, room: "study", at: "not a time"),
            ]),
            "kitchen"
        )
        XCTAssertEqual(
            PersonLastHeard.place(of: [turn(1, room: "attic", at: nil), turn(2, room: "study", at: nil)]),
            "attic"
        )
    }
}
