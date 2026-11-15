import XCTest
@testable import DomovoiKit

/// Groups (V060) on the phone: what is parsed off the wire, what a room card
/// offers, what a queue says, and who is shown the controls at all.
///
/// The words are the web's (`home.jsx` ~:595-700, `music.jsx` ~:1796,
/// `satellites.jsx` ~:2483); the rules are the brief's: household only, a
/// 404 hides the feature silently, "leave" only for a member room, the
/// server's 400/409 sentence as it is.
final class MusicGroupsTests: XCTestCase {

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    private let harvest = HomeSong(title: "Harvest Moon", artist: "Neil Young", durationSec: 302)

    private func room(
        _ id: String,
        online: Bool = true,
        state: String? = nil,
        playing: Bool = false,
        group: NowPlayingGroup? = nil
    ) -> HomeRoom {
        let np: HomeNowPlaying?
        if playing {
            np = HomeNowPlaying(state: state ?? "play", song: harvest, elapsedSec: 10, group: group)
        } else if group != nil || state != nil {
            np = HomeNowPlaying(state: state ?? "stop", group: group)
        } else {
            np = nil
        }
        return HomeRoom(roomID: id, status: online ? "online" : "offline", nowPlaying: np)
    }

    private let upstairs = NowPlayingGroup(
        id: "sq-1", name: "Upstairs", hostRoom: "kitchen",
        rooms: ["kitchen", "garage", "office"], groupID: 1, everywhere: false
    )

    private func failure(_ status: Int, _ detail: String) -> DomovoiError {
        let body = "{\"detail\":\"\(detail)\"}"
        let f = ApiFailure(status: status, message: "\(status): \(body)", body: body)
        switch status {
        case 401, 403: return .refused(f)
        case 500...599: return .serverTrouble(f)
        default: return .httpError(f)
        }
    }

    /// The body as text: the bodies are written with sorted keys.
    private func text(_ data: Data) -> String {
        String(decoding: data, as: UTF8.self)
    }

    // MARK: - parsing: a room's now-playing

    func testARoomInAGroupCarriesItsGroupHostFirst() throws {
        let json = """
        {"room_id":"garage","status":"online","now_playing":{"state":"play",
         "song":{"title":"Harvest Moon","artist":"Neil Young"},"elapsed_sec":12,
         "group":{"id":"sq-1","name":"Upstairs","host_room":"kitchen",
                  "rooms":["kitchen","garage","office"],"group_id":1,"everywhere":false}}}
        """
        let garage = try decode(HomeRoom.self, json)
        let group = try XCTUnwrap(garage.nowPlaying?.group)
        XCTAssertEqual(group.id, "sq-1")
        XCTAssertEqual(group.name, "Upstairs")
        XCTAssertEqual(group.hostRoom, "kitchen")
        XCTAssertEqual(group.rooms, ["kitchen", "garage", "office"], "the host stays first, as the server sent it")
        XCTAssertEqual(group.groupID, 1)
        XCTAssertFalse(group.everywhere)
        // The member's song is the host's on the wire, and is not touched.
        XCTAssertEqual(garage.nowPlaying?.song?.title, "Harvest Moon")
    }

    func testNoGroupAndANullGroupAreBothNoGroup() throws {
        let absent = try decode(HomeRoom.self, #"{"room_id":"kitchen","now_playing":{"state":"play"}}"#)
        let null = try decode(HomeRoom.self, #"{"room_id":"kitchen","now_playing":{"state":"play","group":null}}"#)
        XCTAssertNil(absent.nowPlaying?.group)
        XCTAssertNil(null.nowPlaying?.group)
    }

    /// A group that will not decode is no group — never a rooms read lost.
    func testABrokenGroupDoesNotThrowTheRoomAway() throws {
        let odd = try decode(HomeRoom.self, #"{"room_id":"kitchen","status":"online","now_playing":{"state":"play","group":"nope"}}"#)
        XCTAssertEqual(odd.roomID, "kitchen")
        XCTAssertEqual(odd.nowPlaying?.state, "play")
        XCTAssertNil(odd.nowPlaying?.group)
    }

    func testEverywhereAndAnUnnamedGroupParse() throws {
        let everywhere = try decode(NowPlayingGroup.self, """
        {"id":"sq-2","name":"Everywhere","host_room":"kitchen","rooms":["kitchen","study"],"group_id":null,"everywhere":true}
        """)
        XCTAssertTrue(everywhere.everywhere)
        XCTAssertNil(everywhere.groupID)
        XCTAssertEqual(everywhere.name, "Everywhere")

        let byHand = try decode(NowPlayingGroup.self, #"{"id":7,"host_room":"kitchen","rooms":["kitchen","garage"]}"#)
        XCTAssertEqual(byHand.id, "7", "a numeric id is kept as its digits")
        XCTAssertNil(byHand.name)
        XCTAssertFalse(byHand.everywhere, "absent everywhere is false")
    }

    // MARK: - parsing: a room's queue

    func testAQueueCarriesItsGroupOrNone() throws {
        let shared = try decode(RoomQueue.self, """
        {"room_id":"garage","items":[],"editable":true,"guest":false,
         "group":{"id":"sq-1","name":null,"host_room":"kitchen","rooms":["kitchen","garage"],"group_id":null,"everywhere":false}}
        """)
        XCTAssertEqual(shared.group?.hostRoom, "kitchen")
        XCTAssertEqual(shared.group?.rooms, ["kitchen", "garage"])

        let alone = try decode(RoomQueue.self, #"{"room_id":"garage","items":[],"editable":true,"guest":false,"group":null}"#)
        XCTAssertNil(alone.group)
        let older = try decode(RoomQueue.self, #"{"room_id":"garage","items":[],"editable":true,"guest":false}"#)
        XCTAssertNil(older.group)
    }

    // MARK: - parsing: the list

    func testTheListSkipsARowItCannotRead() throws {
        let list = try decode(MusicGroupsList.self, """
        {"groups":[{"id":1,"name":"Upstairs","rooms":["garage","office"]},{"name":"no id"}],
         "shared":[{"id":"sq-1","name":"Upstairs","host_room":"kitchen","rooms":["kitchen","garage","office"],
                    "group_id":1,"everywhere":false}]}
        """)
        XCTAssertEqual(list.groups, [MusicGroup(id: 1, name: "Upstairs", rooms: ["garage", "office"])])
        XCTAssertEqual(list.shared.first?.rooms.first, "kitchen")

        let empty = try decode(MusicGroupsList.self, "{}")
        XCTAssertEqual(empty, MusicGroupsList())
    }

    // MARK: - the chip

    func testTheChipNamesTheGroupAndTheOtherRooms() {
        let kitchen = room("kitchen", playing: true, group: upstairs)
        XCTAssertEqual(groupChipText(kitchen), "Upstairs · with garage, office")
        let garage = room("garage", playing: true, group: upstairs)
        XCTAssertEqual(groupChipText(garage), "Upstairs · with kitchen, office", "the host is named first")
    }

    func testAnUnnamedGroupSaysOnlyWhoItPlaysWith() {
        let pair = NowPlayingGroup(id: "sq-3", hostRoom: "kitchen", rooms: ["kitchen", "garage"])
        XCTAssertEqual(groupChipText(room("garage", playing: true, group: pair)), "with kitchen")
    }

    func testNoChipOfflineOrAlone() {
        XCTAssertNil(groupChipText(room("garage", online: false, playing: false, group: upstairs)))
        XCTAssertNil(groupChipText(room("kitchen", playing: true)))
    }

    func testTheGroupChipIsTheLastChipAndIsSpoken() {
        let kitchen = room("kitchen", playing: true, group: upstairs)
        XCTAssertEqual(roomChips(kitchen, timerLeftSec: 45).last, .group("Upstairs · with garage, office"))
        XCTAssertEqual(roomChips(kitchen, timerLeftSec: nil).map(\.tone), [.idle])
        XCTAssertTrue(
            spokenRoom(kitchen, stale: false, elapsedSec: 10, nowMs: 0, timerLeftSec: nil)
                .hasSuffix(", Upstairs · with garage, office")
        )
    }

    // MARK: - what a room card offers

    func testAPlayingRoomOffersTheGroupEverywhereAndEachOtherRoom() {
        let kitchen = room("kitchen", playing: true)
        let house = [kitchen, room("garage"), room("office"), room("study", online: false)]
        let named = [MusicGroup(id: 1, name: "Upstairs", rooms: ["garage", "office"])]
        let choices = groupChoices(kitchen, rooms: house, groups: named)
        XCTAssertEqual(choices.map(\.label), [
            "play in Upstairs too", "play everywhere", "play in garage too", "play in office too",
        ])
        XCTAssertEqual(choices.map(\.id), ["group:1", "everywhere", "share:garage", "share:office"])
        XCTAssertEqual(choices.first(where: { $0.id == "everywhere" })?.hint, "every connected room hears it")
        XCTAssertFalse(choices.contains { $0.label.contains("study") }, "an offline room is never offered")
        XCTAssertFalse(choices.contains { $0.isLeave }, "a room in no group has nothing to leave")
    }

    func testAQuietRoomGetsOnlyTheJoins() {
        let garage = room("garage")
        let house = [garage, room("kitchen", playing: true), room("office")]
        let choices = groupChoices(garage, rooms: house, groups: [MusicGroup(id: 1, name: "Upstairs", rooms: ["garage", "office"])])
        XCTAssertEqual(choices.map(\.label), ["join kitchen"])
        XCTAssertEqual(choices.first?.hint, "hear what plays in kitchen")
        XCTAssertEqual(choices.first?.path, "shared/join")
    }

    func testAQuietLonelyRoomIsOfferedNothing() {
        let garage = room("garage")
        XCTAssertEqual(groupChoices(garage, rooms: [garage, room("office")], groups: []), [])
    }

    func testOnlyAMemberIsOfferedLeaveAndRoomsAlreadyInTheGroupAreNotOffered() {
        let kitchen = room("kitchen", playing: true, group: upstairs)
        let house = [kitchen, room("garage", playing: true, group: upstairs),
                     room("office", playing: true, group: upstairs), room("study")]
        let named = [MusicGroup(id: 1, name: "Upstairs", rooms: ["garage", "office"])]
        let choices = groupChoices(kitchen, rooms: house, groups: named)
        XCTAssertEqual(choices.map(\.label), ["play everywhere", "play in study too", "leave the group"])
        XCTAssertEqual(choices.last?.hint, "this room stops, the others play on")
        XCTAssertEqual(choices.last?.path, "shared/leave")
    }

    func testEverythingAlreadyInTheGroupLeavesOnlyLeave() {
        let kitchen = room("kitchen", playing: true, group: upstairs)
        let house = [kitchen, room("garage", playing: true, group: upstairs), room("office", playing: true, group: upstairs)]
        XCTAssertEqual(groupChoices(kitchen, rooms: house, groups: nil).map(\.label), ["leave the group"])
    }

    func testAPausedRoomShares() {
        let kitchen = room("kitchen", state: "pause", playing: true)
        XCTAssertEqual(groupChoices(kitchen, rooms: [kitchen, room("garage")], groups: nil).map(\.id),
                       ["everywhere", "share:garage"])
    }

    // MARK: - the bodies the web sends

    func testEachChoiceSendsTheWebsBody() {
        let named = GroupChoice(room: "kitchen", kind: .group(1), label: "play in Upstairs too")
        XCTAssertEqual(text(named.body), #"{"group_id":1,"host_room":"kitchen"}"#)
        XCTAssertEqual(named.path, "shared")
        let everywhere = GroupChoice(room: "kitchen", kind: .everywhere, label: "play everywhere")
        XCTAssertEqual(text(everywhere.body), #"{"everywhere":true,"host_room":"kitchen"}"#)
        let share = GroupChoice(room: "kitchen", kind: .share("garage"), label: "play in garage too")
        XCTAssertEqual(text(share.body), #"{"host_room":"kitchen","rooms":["garage"]}"#)
        XCTAssertEqual(share.touched, ["kitchen", "garage"], "both rooms are busy while it is out")
        let join = GroupChoice(room: "garage", kind: .join("kitchen"), label: "join kitchen")
        XCTAssertEqual(text(join.body), #"{"host_room":"kitchen","room_id":"garage"}"#)
        let leave = GroupChoice(room: "garage", kind: .leave, label: "leave the group")
        XCTAssertEqual(text(leave.body), #"{"room_id":"garage"}"#)
        XCTAssertEqual(text(MusicGroupsApi.groupBody(name: "Upstairs", rooms: ["garage", "office"])),
                       #"{"name":"Upstairs","rooms":["garage","office"]}"#)
        XCTAssertEqual(text(MusicGroupsApi.leaveBody(room: "garage")), #"{"room_id":"garage"}"#)
    }

    func testWhatAChoiceSaysWhenItWorked() {
        XCTAssertEqual(GroupChoice(room: "kitchen", kind: .share("garage"), label: "play in garage too").doneText,
                       "play in garage too · done")
        XCTAssertEqual(GroupChoice(room: "garage", kind: .leave, label: "leave the group").doneText,
                       "garage left the group")
    }

    // MARK: - the queue's line

    func testTheQueueLineCountsTheOtherRooms() {
        XCTAssertEqual(groupQueueLine(upstairs, room: "garage"),
                       "Upstairs · shared with 2 other rooms (kitchen, office)")
        let pair = NowPlayingGroup(id: "sq-3", hostRoom: "kitchen", rooms: ["kitchen", "garage"])
        XCTAssertEqual(groupQueueLine(pair, room: "kitchen"), "shared with 1 other room (garage)")
    }

    // MARK: - the 400 and 409 sentences

    func testA409SaysTheServersSentence() {
        XCTAssertEqual(groupFailureText(GroupStrings.shareVerb, failure(409, "nothing is playing in kitchen")),
                       "share failed: nothing is playing in kitchen")
        XCTAssertEqual(groupFailureText(GroupStrings.leaveVerb, failure(404, "that room isn't in a group")),
                       "leave the group failed: that room isn't in a group")
    }

    func testA400OnSaveIsTheSentenceAsItIs() {
        let said = "A group's name can't use words like station, radio or playlist, or saying it would play something else."
        XCTAssertEqual(groupSaveFailureText(failure(400, said)), said)
        XCTAssertEqual(groupSaveFailureText(failure(500, "boom")), "save the group failed: boom")
    }

    func testAnUnreachableServerIsTheAppsOwnWords() {
        XCTAssertEqual(groupFailureText(GroupStrings.shareVerb, .unreachable("timed out")), "share failed (offline?)")
    }

    // MARK: - who sees the controls

    func testAGuestNeverSeesThemAndAsksNothing() {
        let read = MusicGroupsRead(server: "http://10.0.0.5:6370", answer: .list(MusicGroupsList()))
        XCTAssertEqual(groupsAccess(paired: false, read: read, server: "http://10.0.0.5:6370", refused: false), .hidden)
    }

    func testAHouseholdPhoneSeesThemOnceTheListAnswers() {
        let server = "http://10.0.0.5:6370"
        XCTAssertEqual(groupsAccess(paired: true, read: nil, server: server, refused: false), .unknown)
        XCTAssertFalse(GroupsAccess.unknown.showsControls, "nothing is drawn before the answer")
        let read = MusicGroupsRead(server: server, answer: .list(MusicGroupsList()))
        XCTAssertEqual(groupsAccess(paired: true, read: read, server: server, refused: false), .household)
        XCTAssertTrue(GroupsAccess.household.showsControls)
    }

    func testAnOlderServerAndARefusalHideThemSilently() {
        let server = "http://10.0.0.5:6370"
        let older = MusicGroupsRead(server: server, answer: .notOnThisServer)
        XCTAssertEqual(groupsAccess(paired: true, read: older, server: server, refused: false), .hidden)
        let refused = MusicGroupsRead(server: server, answer: .notHousehold)
        XCTAssertEqual(groupsAccess(paired: true, read: refused, server: server, refused: false), .hidden)
        let fine = MusicGroupsRead(server: server, answer: .list(MusicGroupsList()))
        XCTAssertEqual(groupsAccess(paired: true, read: fine, server: server, refused: true), .hidden,
                       "a write refused since hides them too")
    }

    /// House A's answer is never drawn on house B.
    func testAnotherServersAnswerIsNotThisOnes() {
        let a = MusicGroupsRead(server: "http://10.0.0.5:6370", answer: .list(MusicGroupsList(groups: [
            MusicGroup(id: 1, name: "Upstairs", rooms: ["garage", "office"]),
        ])))
        XCTAssertEqual(groupsAccess(paired: true, read: a, server: "http://10.0.0.9:6370", refused: false), .unknown)
        XCTAssertNil(groupsList(a, server: "http://10.0.0.9:6370"))
        XCTAssertEqual(groupsList(a, server: "http://10.0.0.5:6370")?.groups.count, 1)
    }

    func testWhichFailuresAreRefusals() {
        XCTAssertTrue(groupsRefused(failure(403, "this device is not the household's")))
        XCTAssertTrue(groupsRefused(failure(401, "pair this device")))
        XCTAssertFalse(groupsRefused(failure(409, "nothing is playing in kitchen")))
        XCTAssertFalse(groupsRefused(failure(404, "Not Found")))
    }

    // MARK: - the Settings form

    func testTheFormNeedsANameAndTwoRooms() {
        XCTAssertFalse(groupFormReady(name: "  ", rooms: ["garage", "office"], busy: false))
        XCTAssertFalse(groupFormReady(name: "Upstairs", rooms: ["garage"], busy: false))
        XCTAssertFalse(groupFormReady(name: "Upstairs", rooms: ["garage", "office"], busy: true))
        XCTAssertTrue(groupFormReady(name: " Upstairs ", rooms: ["garage", "office"], busy: false))
    }

    func testTheFormOffersTheHouseAndAnyRoomTheGroupStillHolds() {
        let group = MusicGroup(id: 1, name: "Upstairs", rooms: ["attic", "garage"])
        XCTAssertEqual(groupFormRooms(house: ["office", "garage", "kitchen"], group: group),
                       ["attic", "garage", "kitchen", "office"])
        XCTAssertEqual(groupFormRooms(house: ["office", "garage"], group: nil), ["garage", "office"])
    }

    func testTheCardsWordsAreTheWebs() {
        XCTAssertEqual(GroupStrings.title, "Groups")
        XCTAssertEqual(GroupStrings.newGroup, "new group")
        XCTAssertEqual(GroupStrings.deleteTitle("Upstairs"), "Delete the group Upstairs?")
        XCTAssertEqual(GroupStrings.deleteBody, "Music playing in its rooms keeps playing.")
        XCTAssertEqual(GroupStrings.made("Upstairs"), "group Upstairs made")
        XCTAssertTrue(GroupStrings.help.contains("\u{201C}Everywhere\u{201D} needs no group."))
        XCTAssertFalse(GroupStrings.help.contains("station"), "groups are never called stations")
        XCTAssertEqual(GroupStrings.menuLabel("kitchen"), "play kitchen\u{2019}s music in other rooms, or join another room")
    }
}
