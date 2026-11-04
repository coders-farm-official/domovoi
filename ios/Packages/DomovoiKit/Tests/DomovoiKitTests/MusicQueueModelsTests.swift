import XCTest
@testable import DomovoiKit

/// The room queue's wire shapes, and the three permission questions a screen
/// asks before it draws a control.
///
/// None of this is tested on Android: `RoomQueue` there is a `data class`
/// with no `guest` field at all, and the `editable` it does have is read
/// inside a `Composable`. The permissions are the part worth pinning — they
/// are the difference between a control that works and a control that answers
/// with a 403.
final class MusicQueueModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type = T.self) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - what the wire can say

    /// Every field absent. The defaults are the **narrow** reading, which is
    /// the opposite of the server's own defaults and deliberately so.
    func testAnEmptyObjectIsAQueueThisPhoneMayOnlyWatch() throws {
        let queue: RoomQueue = try decode("{}")

        XCTAssertEqual("", queue.roomID)
        XCTAssertTrue(queue.items.isEmpty)
        XCTAssertNil(queue.currentSongID)
        // The server's pydantic default is `editable: True`; an unreadable
        // answer gets `false` here, because a client that could not read the
        // permission knows nothing about it.
        XCTAssertFalse(queue.editable)
        XCTAssertTrue(queue.guest)
        XCTAssertNil(queue.blockedReason)
        XCTAssertFalse(queue.mayClear)
        XCTAssertFalse(queue.mayMove)
    }

    func testAFullAnswerDecodes() throws {
        let queue: RoomQueue = try decode("""
        {"room_id":"living room","current_song_id":7,"editable":true,"guest":false,
         "items":[{"song_id":7,"pos":0,"file":"/srv/a.mp3","title":"Hey Jude",
                   "artist":"The Beatles","album":"1","duration_sec":431,
                   "added_by":"Kamron's iPhone","added_by_device_id":"ios-1",
                   "added_at":"2026-10-30T12:00:00Z","playing":true}]}
        """)

        XCTAssertEqual("living room", queue.roomID)
        XCTAssertEqual(7, queue.currentSongID)
        XCTAssertTrue(queue.editable)
        XCTAssertFalse(queue.guest)
        let item = try XCTUnwrap(queue.items.first)
        XCTAssertEqual(7, item.songID)
        XCTAssertEqual(0, item.pos)
        XCTAssertEqual("Hey Jude", item.title)
        XCTAssertEqual("The Beatles", item.artist)
        XCTAssertEqual("1", item.album)
        XCTAssertEqual(431, item.durationSec)
        XCTAssertEqual("Kamron's iPhone", item.addedBy)
        XCTAssertEqual("ios-1", item.addedByDeviceID)
        XCTAssertEqual("2026-10-30T12:00:00Z", item.addedAt)
        XCTAssertTrue(item.playing)
    }

    /// `added_at` is typed `Any` on the server. A number there must cost the
    /// timestamp and **nothing else** — not the row, and not the queue.
    func testATimestampThatIsNotAStringDoesNotCostTheRow() throws {
        let queue: RoomQueue = try decode("""
        {"editable":true,"guest":false,
         "items":[{"song_id":3,"title":"Come Together","added_at":1762000000}]}
        """)

        let item = try XCTUnwrap(queue.items.first)
        XCTAssertNil(item.addedAt)
        XCTAssertEqual("Come Together", item.title)
        XCTAssertEqual(1, queue.items.count)
    }

    /// A row whose `song_id` is a string, though, is a row this app cannot
    /// address — so it throws, and the throw is the honest answer rather than
    /// a silent `songID: 0` that a remove would then send.
    func testARowWithAnUnreadableSongIDThrows() {
        XCTAssertThrowsError(
            try decode(#"{"items":[{"song_id":"seven"}]}"#, as: RoomQueue.self)
        )
    }

    // MARK: - what a row says

    func testARowPrefersItsTagOverItsPath() {
        XCTAssertEqual(
            "Hey Jude",
            QueueItem(file: "/srv/music/01 hey jude.mp3", title: "Hey Jude").displayTitle
        )
    }

    /// The basename, off **either** separator — where Android shows the whole
    /// stored path. Kamron's server is Windows.
    func testAnUntaggedRowShowsTheFilenameAndNotTheWholeWindowsPath() {
        XCTAssertEqual(
            "song.mp3",
            QueueItem(file: #"C:\Users\Kamron\Music\Artist\song.mp3"#).displayTitle
        )
        XCTAssertEqual("song.mp3", QueueItem(file: "/srv/music/artist/song.mp3").displayTitle)
    }

    func testARowWithNothingToGoOnIsCalledATrack() {
        XCTAssertEqual(MusicStrings.unnamedTrack, QueueItem().displayTitle)
        XCTAssertEqual(MusicStrings.unnamedTrack, QueueItem(file: "/", title: "   ").displayTitle)
    }

    func testTheSecondLineHasEveryPartThatExists() {
        XCTAssertEqual(
            "The Beatles · 7:11 · added by Kamron's iPhone",
            QueueItem(
                artist: "The Beatles",
                durationSec: 431,
                addedBy: "Kamron's iPhone"
            ).subtitle
        )
    }

    /// And only the parts that exist. An entry with no record of who queued
    /// it says nothing about who queued it.
    func testTheSecondLineLeavesOutWhatTheServerDidNotSay() {
        XCTAssertEqual(MusicStrings.unknownArtist, QueueItem().subtitle)
        XCTAssertEqual("The Beatles", QueueItem(artist: "The Beatles").subtitle)
        XCTAssertEqual(
            "The Beatles · 3:04",
            QueueItem(artist: "The Beatles", durationSec: 184).subtitle
        )
        // A blank name is no name: a trimmed-empty `added_by` must not become
        // "added by ".
        XCTAssertEqual(
            "The Beatles",
            QueueItem(artist: "The Beatles", addedBy: "   ").subtitle
        )
        XCTAssertEqual(
            MusicStrings.unknownArtist,
            QueueItem(artist: "  ").subtitle
        )
    }

    // MARK: - the three permissions

    func testAHouseholdPhoneMayDoEverything() {
        let playing = QueueItem(songID: 1, playing: true)
        let next = QueueItem(songID: 2)
        let queue = RoomQueue(items: [playing, next], editable: true, guest: false)

        XCTAssertTrue(queue.mayClear)
        XCTAssertTrue(queue.mayMove)
        XCTAssertTrue(queue.mayRemove(playing))
        XCTAssertTrue(queue.mayRemove(next))
    }

    /// A guest may reorder and may take out an entry that is not playing, and
    /// may do neither of the other two — which is what the server enforces,
    /// so it is what the screen offers.
    func testAGuestMayNotClearAndMayNotRemoveWhatIsPlaying() {
        let playing = QueueItem(songID: 1, playing: true)
        let next = QueueItem(songID: 2)
        let queue = RoomQueue(items: [playing, next], editable: true, guest: true)

        XCTAssertFalse(queue.mayClear, "clear is household-only on the server")
        XCTAssertTrue(queue.mayMove)
        XCTAssertFalse(queue.mayRemove(playing), "the core refuses a guest this one")
        XCTAssertTrue(queue.mayRemove(next))
    }

    /// `editable: false` is the end of it, whoever is asking.
    func testABlockedDeviceMayDoNothingAtAll() {
        let next = QueueItem(songID: 2)
        let queue = RoomQueue(items: [next], editable: false, guest: false)

        XCTAssertFalse(queue.mayClear)
        XCTAssertFalse(queue.mayMove)
        XCTAssertFalse(queue.mayRemove(next))
    }

    // MARK: - the notice

    func testTheWatchOnlyNoticeIsTheServersSentenceAndThenWhatItMeans() {
        let queue = RoomQueue(
            editable: false,
            blockedReason: "Kamron's iPad isn't allowed to edit the queue in garage"
        )

        XCTAssertEqual(
            "Kamron's iPad isn't allowed to edit the queue in garage. "
                + "You can watch the queue but not change it.",
            queue.watchOnlyNote
        )
    }

    /// No notice over a queue that can be edited, even when a reason came
    /// with it, and none when the server gave no reason: this app does not
    /// write that sentence itself.
    func testThereIsNoNoticeWithoutBothHalves() {
        XCTAssertNil(RoomQueue(editable: true, blockedReason: "stale").watchOnlyNote)
        XCTAssertNil(RoomQueue(editable: false, blockedReason: nil).watchOnlyNote)
        XCTAssertNil(RoomQueue(editable: false, blockedReason: "   ").watchOnlyNote)
    }

    // MARK: - the paths

    /// A room name is a person's word, so every path escapes it. `'` and a
    /// space are the two that turn up in a real house.
    func testEveryQueuePathEscapesTheRoomName() {
        XCTAssertEqual(
            "/api/music/queue/Kids%27%20room?device_id=ios-ab12",
            MusicQueuePath.read(room: "Kids' room", deviceID: "ios-ab12")
        )
        XCTAssertEqual(
            "/api/music/queue/Kids%27%20room/remove",
            MusicQueuePath.remove(room: "Kids' room")
        )
        XCTAssertEqual(
            "/api/music/queue/Kids%27%20room/move",
            MusicQueuePath.move(room: "Kids' room")
        )
        XCTAssertEqual(
            "/api/music/queue/Kids%27%20room/clear",
            MusicQueuePath.clear(room: "Kids' room")
        )
    }

    /// Including the slash, which `.urlPathAllowed` would have let through —
    /// a room called `a/b` must not address a path this app did not write.
    func testASlashInARoomNameDoesNotBecomeAPathSegment() {
        XCTAssertEqual(
            "/api/music/queue/a%2Fb/clear",
            MusicQueuePath.clear(room: "a/b")
        )
        XCTAssertEqual(
            "/api/music/pause/a%2Fb",
            MusicQueuePath.transport(.pause, room: "a/b")
        )
    }

    /// The device id is escaped too. It is this app's own `ios-` plus hex, so
    /// nothing in it needs escaping today — and the day something does, the
    /// id must not be able to add a query parameter of its own.
    func testTheDeviceIDIsEscapedInTheQueryToo() {
        XCTAssertEqual(
            "/api/music/queue/den?device_id=ios%26admin%3D1",
            MusicQueuePath.read(room: "den", deviceID: "ios&admin=1")
        )
    }

    func testEachTransportActionHasItsOwnPathAndItsOwnWord() {
        XCTAssertEqual(
            [
                "/api/music/previous/den",
                "/api/music/pause/den",
                "/api/music/resume/den",
                "/api/music/skip/den",
                "/api/music/stop/den",
            ],
            MusicTransportAction.allCases.map { MusicQueuePath.transport($0, room: "den") }
        )
        XCTAssertEqual(
            ["previous", "pause", "resume", "skip", "stop"],
            MusicTransportAction.allCases.map(\.label)
        )
    }

    func testFavouritingWhatIsPlayingAddressesTheRoom() {
        XCTAssertEqual(
            "/api/music/now-playing/living%20room/favorite",
            MusicQueuePath.favoriteNowPlaying(room: "living room")
        )
    }

    // MARK: - the bodies

    /// `song_ids` is a **list** on the wire even for one entry, because the
    /// server's `QueueRemoveRequest` takes a list.
    func testARemoveSendsAListOfOne() {
        XCTAssertEqual(
            #"{"song_ids":[7],"device_id":"ios-ab12"}"#,
            String(decoding: MusicQueueModel.json(songIDs: [7], deviceID: "ios-ab12"), as: UTF8.self)
        )
    }

    func testAMoveSendsTheIDAndThePosition() {
        XCTAssertEqual(
            #"{"song_id":7,"to_position":2,"device_id":"ios-ab12"}"#,
            String(
                decoding: MusicQueueModel.json(songID: 7, to: 2, deviceID: "ios-ab12"),
                as: UTF8.self
            )
        )
    }

    func testAClearSendsOnlyTheDevice() {
        XCTAssertEqual(
            #"{"device_id":"ios-ab12"}"#,
            String(decoding: MusicQueueModel.json(deviceID: "ios-ab12"), as: UTF8.self)
        )
    }
}
