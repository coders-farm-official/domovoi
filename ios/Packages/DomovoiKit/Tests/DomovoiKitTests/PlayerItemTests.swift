import XCTest
@testable import DomovoiKit

/// The queue item's five factories, and the artwork rules they lean on.
///
/// Each factory **pins a path**, and the path is the whole contract with the
/// server: `/api/music/library/12/audio` is what `AVPlayer` opens, so a typo
/// here is a queue that plays nothing and no other test would catch it.
/// `PlayItemTest` and `CoverArtTest` on Android pin the same ones.
final class PlayerItemTests: XCTestCase {

    override func setUp() {
        super.setUp()
        PlayerArtwork.forgetMisses()
    }

    override func tearDown() {
        PlayerArtwork.forgetMisses()
        super.tearDown()
    }

    // MARK: - the factories

    func testATrackPinsItsAudioAndCoverPaths() {
        let item = PlayerItem.fromTrack(id: 12, title: "Transmission", artist: "Joy Division", album: "Substance", durationSec: 210)
        XCTAssertEqual(item.uid, "lib-12")
        XCTAssertEqual(item.kind, .library)
        XCTAssertEqual(item.src, "/api/music/library/12/audio")
        XCTAssertEqual(item.coverPath, "/api/music/library/12/cover")
        XCTAssertEqual(item.durationSec, 210)
        XCTAssertTrue(item.seekable)
        XCTAssertFalse(item.isSpoken)
        XCTAssertTrue(item.chapters.isEmpty, "a library track gets no chapter list (MAP §5.7)")
        XCTAssertTrue(item.parts.isEmpty)
    }

    func testAStationIsNotSeekableAndItsArtistLineIsLiveRadio() {
        let item = PlayerItem.fromStation(id: 4, name: "Hollow Earth Radio")
        XCTAssertEqual(item.uid, "radio-4")
        XCTAssertEqual(item.src, "/api/plugins/radio/stations/4/stream")
        XCTAssertEqual(item.artist, "live radio", "PlayItem.kt:60, word for word")
        XCTAssertFalse(item.seekable, "a live stream has no place to seek to")
        XCTAssertNil(item.coverPath)
        XCTAssertFalse(item.isSpoken)
    }

    func testAnEpisodeKeepsOnlyAServerArtworkPath() {
        let kept = PlayerItem.fromEpisode(
            id: 9, title: "The Lost Coast", show: "Nightwaves",
            artwork: "/api/podcasts/subscriptions/3/artwork"
        )
        XCTAssertEqual(kept.src, "/api/podcasts/episodes/9/audio")
        XCTAssertEqual(kept.coverPath, "/api/podcasts/subscriptions/3/artwork")
        XCTAssertTrue(kept.isSpoken)

        // A publisher's own URL is dropped, so the phone never contacts one.
        let dropped = PlayerItem.fromEpisode(
            id: 9, title: "The Lost Coast", artwork: "https://publisher.example/cover.jpg"
        )
        XCTAssertNil(dropped.coverPath, "PodcastArtwork.kt:20 — only /api/ paths load")
        let protocolRelative = PlayerItem.fromEpisode(id: 9, title: "x", artwork: "//publisher.example/c.jpg")
        XCTAssertNil(protocolRelative.coverPath)
    }

    func testAPhoneSongCarriesItsOwnURIAndNoServerPath() {
        let item = PlayerItem.fromDeviceAudio(
            id: 77, title: "Pocketful", uri: "file:///var/music/pocketful.m4a",
            artURI: "file:///var/music/art.jpg"
        )
        XCTAssertEqual(item.kind, .device)
        XCTAssertEqual(item.src, "file:///var/music/pocketful.m4a")
        XCTAssertEqual(item.coverPath, "file:///var/music/art.jpg")
        XCTAssertFalse(item.isSpoken)
    }

    func testASingleFileBookKeepsNoPartsAndAFolderBookStartsOnItsFirst() {
        let single = PlayerItem.fromBook(id: 3, title: "One File", durationSec: 900)
        XCTAssertEqual(single.src, "/api/audiobooks/3/audio")
        XCTAssertTrue(single.parts.isEmpty)

        // One part is not a folder book: `parts` is kept only above one
        // (`PlayItem.kt:105`), so the timeline stays trivial.
        let one = PlayerItem.fromBook(
            id: 3, title: "One File",
            parts: [PlayerMediaPart(src: "/api/audiobooks/3/audio?file=a.mp3", durationSec: 900)]
        )
        XCTAssertEqual(one.src, "/api/audiobooks/3/audio?file=a.mp3", "src is the first part's")
        XCTAssertTrue(one.parts.isEmpty, "one part is not a folder book")

        let folder = PlayerSeed.folderBook
        XCTAssertEqual(folder.parts.count, 3)
        XCTAssertEqual(folder.src, "/api/audiobooks/2/audio?file=01.mp3")
        XCTAssertTrue(folder.isSpoken)
    }

    // MARK: - the artwork rules

    func testACoverPathResolvesButAPhoneURIDoesNot() {
        let absolute: (String) -> String = { "http://10.0.0.42:6369" + $0 }
        XCTAssertEqual(
            PlayerArtwork.model(coverPath: "/api/music/library/12/cover", absolute: absolute),
            "http://10.0.0.42:6369/api/music/library/12/cover"
        )
        // Prefixing a phone song's own URI with the server turned its art into
        // a dead URL on Android (`CoverArt.kt:30`).
        XCTAssertEqual(
            PlayerArtwork.model(coverPath: "file:///var/music/art.jpg", absolute: absolute),
            "file:///var/music/art.jpg"
        )
        XCTAssertNil(PlayerArtwork.model(coverPath: nil, absolute: absolute))
        XCTAssertNil(PlayerArtwork.model(coverPath: "  ", absolute: absolute))
    }

    // MARK: - the memo through the model

    /// A different house forgets the misses; the same house at a new address
    /// does not.
    ///
    /// `PlayerModel.dropServerMedia` calls ``PlayerArtwork/forgetMisses()``
    /// (`PlayerModel.swift:1055`) and **nothing asserted it**: that line could
    /// be deleted and this whole suite stayed green, which is the
    /// seam-with-no-caller shape this track has been bitten by before. It
    /// matters because the memo is keyed by ABSOLUTE url and two houses both
    /// have a track 11 — so the first house's 404 would keep the second
    /// house's real cover behind the placeholder for the rest of the run.
    ///
    /// Both halves are here on purpose. Forgetting on every reconnect is the
    /// opposite bug and just as invisible: every missing cover in the library
    /// re-asked each time the shell found the house at a new address.
    @MainActor
    func testAHouseChangeForgetsTheMissesAndAReconnectDoesNot() {
        StubURLProtocol.reset()
        PlayerHouse().install()
        defer { StubURLProtocol.reset() }
        let url = "http://10.0.0.42:6369/api/music/library/11/cover"

        let reconnected = PlayerHarness()
        reconnected.model.serverChanged(
            to: PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "AA")
        )
        PlayerArtwork.noteFailure(url, status: 404)
        XCTAssertTrue(PlayerArtwork.isMissing(url))
        reconnected.model.serverChanged(
            to: PlayerServerScope(address: "http://192.168.0.9:6369", fingerprint: "AA")
        )
        XCTAssertTrue(
            PlayerArtwork.isMissing(url),
            "the same house at a new address still has no cover for track 11"
        )

        let moved = PlayerHarness()
        moved.model.serverChanged(
            to: PlayerServerScope(address: "http://10.0.0.42:6369", fingerprint: "AA")
        )
        moved.model.serverChanged(
            to: PlayerServerScope(address: "http://10.0.0.99:6369", fingerprint: "BB")
        )
        XCTAssertFalse(
            PlayerArtwork.isMissing(url),
            "a different house's track 11 is a different track, and its cover was never asked for"
        )
    }

    func testOnlyA404IsRememberedAsAMissingCover() {
        let url = "http://10.0.0.42:6369/api/music/library/9404/cover"
        XCTAssertFalse(PlayerArtwork.isMissing(url))

        PlayerArtwork.noteFailure(url, status: 500)
        XCTAssertFalse(PlayerArtwork.isMissing(url), "a server error is tried again; the house may be restarting")
        PlayerArtwork.noteFailure(url, status: nil)
        XCTAssertFalse(PlayerArtwork.isMissing(url), "no answer at all is tried again")

        PlayerArtwork.noteFailure(url, status: 404)
        XCTAssertTrue(PlayerArtwork.isMissing(url), "404 is this track has no cover")

        PlayerArtwork.forgetMisses()
        XCTAssertFalse(PlayerArtwork.isMissing(url))
    }

    // MARK: - the target, and a room reading

    func testATargetSaysWhichRoomOrNone() {
        XCTAssertNil(PlayerTarget.thisPhone.roomID)
        XCTAssertFalse(PlayerTarget.thisPhone.isCasting)
        XCTAssertEqual(PlayerTarget.room("office").roomID, "office")
        XCTAssertTrue(PlayerTarget.room("office").isCasting)
    }

    func testARoomReadingTakesThePhonesClockNotTheServers() {
        let row = NowPlayingRoom(
            roomID: "office", state: "play",
            song: NowPlayingSong(title: "Atmosphere", artist: "Joy Division", durationSec: 390),
            elapsedSec: 42, trackID: 12
        )
        let reading = PlayerRoomReading(row, readAtMs: 123_456)
        XCTAssertEqual(reading.roomID, "office")
        XCTAssertEqual(reading.title, "Atmosphere")
        XCTAssertEqual(reading.elapsedSec, 42)
        XCTAssertEqual(reading.trackID, 12)
        XCTAssertTrue(reading.isPlaying)
        XCTAssertEqual(
            reading.readAtMs, 123_456,
            "the lyrics anchor is the phone's receipt clock; the two clocks are not the same clock"
        )
    }

    func testARoomRowWithNoStateReadsAsStopped() {
        let reading = PlayerRoomReading(NowPlayingRoom(roomID: "den"), readAtMs: 1)
        XCTAssertEqual(reading.state, "stop")
        XCTAssertFalse(reading.isPlaying)
        XCTAssertEqual(reading.elapsedSec, 0)
    }

    // MARK: - the server's own JSON

    func testAChapterDecodesTheServersFieldNames() throws {
        let json = Data(#"{"title":"The Lighthouse","start_sec":600.5}"#.utf8)
        let chapter = try JSONDecoder().decode(PlayerChapter.self, from: json)
        XCTAssertEqual(chapter.title, "The Lighthouse")
        XCTAssertEqual(chapter.startSec, 600.5)
    }

    func testAChapterWithNeitherFieldDecodesRatherThanThrowing() throws {
        // Every field is optional-with-a-default for the reason
        // `NowPlayingSong` is: one missing key must not lose a whole book's
        // chapter list.
        let chapter = try JSONDecoder().decode(PlayerChapter.self, from: Data("{}".utf8))
        XCTAssertEqual(chapter.title, "")
        XCTAssertEqual(chapter.startSec, 0)
    }
}
