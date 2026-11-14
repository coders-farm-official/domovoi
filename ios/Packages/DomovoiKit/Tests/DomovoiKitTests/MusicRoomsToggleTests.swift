import Foundation
import XCTest
@testable import DomovoiKit

/// The Music page's rooms strip is folded away behind a text link (owner
/// 2026-11-11, hand-off §2; Android `RoomsToggleTest.kt`, d19efd9c): closed
/// until opened, the choice kept on this phone, the link worded as the web
/// words it, and announced as a button that is expanded or collapsed.
@MainActor
final class MusicRoomsToggleTests: XCTestCase {

    /// A `UserDefaults` of this case's own, so no case reads another's choice
    /// and none touches the app's.
    private var suite = ""
    private var defaults = UserDefaults()

    override func setUp() {
        super.setUp()
        suite = "music-rooms-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suite) ?? .standard
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    // MARK: - the store

    func testRoomsStartFoldedAwayForEveryone() {
        XCTAssertNil(defaults.object(forKey: MusicRoomsOpenStore.key), "a phone that never chose")
        XCTAssertFalse(MusicRoomsOpenStore(defaults: defaults).open, "the strip starts closed")
    }

    func testTheChoiceIsReadBackFromThePhone() {
        MusicRoomsOpenStore(defaults: defaults).open = true
        // A new screen — a new store over the same storage — reads it back.
        XCTAssertTrue(MusicRoomsOpenStore(defaults: defaults).open)
        MusicRoomsOpenStore(defaults: defaults).open = false
        XCTAssertFalse(MusicRoomsOpenStore(defaults: defaults).open)
        XCTAssertEqual(
            defaults.object(forKey: "domovoi.music.roomsOpen") as? Bool, false,
            "kept under the web's own key, so the two are the same setting by name"
        )
    }

    /// A value given on the launch line arrives as a **string** in the
    /// argument domain; the UI cases open the strip that way.
    func testAStringYesReadsAsOpen() {
        defaults.set("YES", forKey: MusicRoomsOpenStore.key)
        XCTAssertTrue(MusicRoomsOpenStore(defaults: defaults).open)
    }

    // MARK: - the words

    func testTheLinkIsWordedAsTheWebWordsIt() {
        XCTAssertEqual(MusicRoomsToggle.label(open: false, playingCount: 0), "Show rooms")
        XCTAssertEqual(MusicRoomsToggle.label(open: false, playingCount: 2), "Show rooms (2 playing)")
        XCTAssertEqual(MusicRoomsToggle.label(open: false, playingCount: 1), "Show rooms (1 playing)")
        XCTAssertEqual(MusicRoomsToggle.label(open: true, playingCount: 0), "Hide rooms")
        XCTAssertEqual(MusicRoomsToggle.label(open: true, playingCount: 3), "Hide rooms")
    }

    func testTheLinkSaysWhetherTheRoomsAreShown() {
        XCTAssertEqual(MusicRoomsToggle.accessibilityValue(open: false), "collapsed")
        XCTAssertEqual(MusicRoomsToggle.accessibilityValue(open: true), "expanded")
    }

    /// The count is the rooms the now-playing read already on screen says
    /// are playing — rows as the core sends them, a bare array.
    func testThePlayingCountIsTheRoomsTheReadSaysArePlaying() throws {
        let rows = try JSONDecoder().decode([NowPlayingRoom].self, from: Data("""
        [{"room_id":"kitchen","state":"play","elapsed_sec":12,"song":{"title":"A","artist":"B","duration_sec":200}},
         {"room_id":"office","state":"pause","elapsed_sec":3,"song":{"title":"C","artist":"D","duration_sec":100}},
         {"room_id":"den","state":"play","elapsed_sec":1,"song":{"title":"E","artist":"F","duration_sec":90}},
         {"room_id":"hall","state":"stop"}]
        """.utf8))
        XCTAssertEqual(MusicPage.playingCount(rows), 2)
        XCTAssertEqual(
            MusicRoomsToggle.label(open: false, playingCount: MusicPage.playingCount(rows)),
            "Show rooms (2 playing)"
        )
    }

    // MARK: - what is under the link

    private let kitchen = NowPlayingRoom(roomID: "kitchen", state: "play")

    func testShutShowsNothingWhateverTheReadSays() {
        XCTAssertEqual(
            MusicRoomsToggle.section(open: false, hasData: false, loading: true, rooms: [], error: nil),
            .hidden
        )
        XCTAssertEqual(
            MusicRoomsToggle.section(open: false, hasData: false, loading: false, rooms: [], error: "502"),
            .hidden,
            "the rooms-unavailable card shows only when the strip is open"
        )
        XCTAssertEqual(
            MusicRoomsToggle.section(open: false, hasData: true, loading: false, rooms: [kitchen], error: nil),
            .hidden
        )
    }

    func testOpenItSaysLoadingThenTheRooms() {
        XCTAssertEqual(
            MusicRoomsToggle.section(open: true, hasData: false, loading: true, rooms: [], error: nil),
            .loading
        )
        XCTAssertEqual(
            MusicRoomsToggle.section(open: true, hasData: true, loading: false, rooms: [kitchen], error: nil),
            .rooms
        )
    }

    /// A failed read is said, not drawn as a house with no rooms
    /// (`MusicScreen.kt:430`); a failed refresh over rooms already read keeps
    /// them.
    func testAFailedReadIsSaidWithItsReason() {
        XCTAssertEqual(
            MusicRoomsToggle.section(open: true, hasData: false, loading: false, rooms: [], error: "the server is not answering"),
            .unavailable(message: "couldn't load now-playing: the server is not answering")
        )
        XCTAssertEqual(
            MusicRoomsToggle.section(open: true, hasData: true, loading: false, rooms: [kitchen], error: "timed out"),
            .rooms
        )
        XCTAssertEqual(MusicRoomsStrings.unavailableTitle, "rooms unavailable")
        XCTAssertEqual(MusicRoomsStrings.retry, "retry")
    }
}
