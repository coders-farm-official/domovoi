import XCTest
@testable import DomovoiKit

/// A stop in a room of a group stops the whole group (owner, 2026-11-15), so
/// every stop button the phone shows for such a room names the group in the
/// web's words (`home.jsx` HomeStopTitle, `music.jsx` NPCard): "stop
/// Upstairs: kitchen, garage, office". A room alone keeps its plain stop.
/// There is no "stop just this room" button — the web has none; "leave the
/// group" is that stop — and the request stays the bare stop
/// (`HomeWritesTests` holds its path), the server's default scope being the
/// group.
final class GroupStopTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    private let upstairs = NowPlayingGroup(
        id: "sq-1", name: "Upstairs", hostRoom: "kitchen",
        rooms: ["kitchen", "garage", "office"], groupID: 1, everywhere: false
    )
    private let joined = NowPlayingGroup(id: "sq-2", name: nil, hostRoom: "kitchen", rooms: ["kitchen", "garage"])

    private func homeRoom(_ id: String, state: String = "play", group: NowPlayingGroup?) -> HomeRoom {
        let song = HomeSong(title: "Harvest Moon", artist: "Neil Young", durationSec: 302)
        return HomeRoom(
            roomID: id, status: "online",
            nowPlaying: HomeNowPlaying(state: state, song: song, elapsedSec: 10, group: group)
        )
    }

    // MARK: - the label

    func testANamedGroupsStopNamesItAndItsRooms() {
        XCTAssertEqual("stop Upstairs: kitchen, garage, office", groupStopLabel(upstairs, plain: "stop garage"))
        XCTAssertEqual("stop Upstairs: kitchen, garage, office", groupStopLabel(upstairs, plain: "stop"))
    }

    func testRoomsJoinedOneByOneAreTheGroup() {
        XCTAssertEqual("stop the group: kitchen, garage", groupStopLabel(joined, plain: "stop kitchen"))
        // An empty name is no name (the web's `g.name || 'the group'`).
        let blank = NowPlayingGroup(id: "sq-2", name: "", hostRoom: "kitchen", rooms: ["kitchen", "garage"])
        XCTAssertEqual("stop the group: kitchen, garage", groupStopLabel(blank, plain: "stop"))
    }

    func testEverywhereIsNamedAsTheServerNamesIt() {
        let all = NowPlayingGroup(id: "sq-3", name: "Everywhere", hostRoom: "porch", rooms: ["porch", "den"], everywhere: true)
        XCTAssertEqual("stop Everywhere: porch, den", groupStopLabel(all, plain: "stop porch"))
    }

    func testARoomAloneKeepsItsPlainStop() {
        XCTAssertEqual("stop kitchen", groupStopLabel(nil, plain: "stop kitchen"))
        XCTAssertEqual("stop", groupStopLabel(nil, plain: "stop"))
        // A group down to one room stops only this room.
        let one = NowPlayingGroup(id: "sq-2", name: "Upstairs", hostRoom: "kitchen", rooms: ["kitchen"])
        XCTAssertEqual("stop kitchen", groupStopLabel(one, plain: "stop kitchen"))
    }

    func testStopAndCloseNamesTheGroupItStops() {
        XCTAssertEqual("Stop and close", stopAndCloseLabel(nil))
        XCTAssertEqual("Stop and close · stop the group: kitchen, garage", stopAndCloseLabel(joined))
    }

    // MARK: - Home's row

    func testHomesStopNamesTheGroupAndPauseDoesNot() {
        let garage = homeRoom("garage", group: upstairs)
        XCTAssertEqual("stop Upstairs: kitchen, garage, office", transportLabel("stop", room: garage))
        XCTAssertEqual("pause garage", transportLabel("pause", room: garage))
        XCTAssertEqual("resume garage", transportLabel("resume", room: homeRoom("garage", state: "pause", group: upstairs)))
        XCTAssertEqual("stop shed", transportLabel("stop", room: homeRoom("shed", group: nil)))
    }

    // MARK: - which group the player's stop reaches

    func testThePlayersStopReachesTheCastRoomsGroupOnly() {
        let reading = MusicRemoteNowPlaying(roomID: "kitchen", state: "play", group: joined)
        XCTAssertEqual(joined, castStopGroup(target: .room("kitchen"), remote: reading))
        // Playing on the phone: its stop is its own.
        XCTAssertNil(castStopGroup(target: .local, remote: reading))
        // A reading of the room the cast left says nothing about this one.
        XCTAssertNil(castStopGroup(target: .room("garage"), remote: reading))
        XCTAssertNil(castStopGroup(target: .room("kitchen"), remote: nil))
        XCTAssertNil(castStopGroup(
            target: .room("kitchen"),
            remote: MusicRemoteNowPlaying(roomID: "kitchen", state: "play")
        ))
    }

    // MARK: - the wire

    func testTheMusicCardsAndTheCastReadingCarryTheGroup() throws {
        let rows = try decode([NowPlayingRoom].self, """
        [{"room_id":"garage","state":"play","group":{"id":"sq-1","name":"Upstairs","host_room":"kitchen",
          "rooms":["kitchen","garage"],"group_id":1,"everywhere":false}},
         {"room_id":"porch","state":"stop","group":null},
         {"room_id":"attic","state":"stop"},
         {"room_id":"den","state":"stop","group":"odd"}]
        """)
        XCTAssertEqual(4, rows.count)
        XCTAssertEqual("stop Upstairs: kitchen, garage", groupStopLabel(rows[0].group, plain: "stop"))
        XCTAssertNil(rows[1].group)
        XCTAssertNil(rows[2].group)
        // An odd group never throws the now-playing away.
        XCTAssertNil(rows[3].group)
        // The cast player's reading keeps it.
        XCTAssertEqual(rows[0].group, PlayerRoomReading(rows[0], readAtMs: 5).group)
    }
}
