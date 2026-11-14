import XCTest
@testable import DomovoiKit

/// V059 on the phone: track and queue rows carry `artists`, `cover_v` and
/// `cover_url`; a versioned cover is shown from what is held without asking
/// and a new version is fetched fresh; `library.cover.changed` moves the
/// covers a row, a room card and the lock screen draw; credits read
/// "with B, C" in the web's words.
///
/// Android `CoverVersionTest.kt` / `CreditsAndCoverFieldsTest.kt`
/// (wf/android-libfu) and the web's `components.jsx`.
@MainActor
final class MusicCoverVersionTests: XCTestCase {

    static let repositoryRoot: URL = {
        var url = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { url = url.deletingLastPathComponent() }
        return url
    }()

    private let server = "http://10.0.0.42:6369"

    override func setUp() {
        super.setUp()
        MusicCoverVersions.shared.forget()
        PlayerArtwork.forgetMisses()
    }

    override func tearDown() {
        MusicCoverVersions.shared.forget()
        PlayerArtwork.forgetMisses()
        super.tearDown()
    }

    nonisolated static let picture = Data([0x89, 0x50, 0x4E, 0x47] + Array(repeating: 0x20, count: 200))

    private func settleUntil(
        _ done: @MainActor () -> Bool,
        _ what: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<settleYieldLimit {
            if done() { return }
            await Task.yield()
        }
        XCTFail("\(what) never settled in \(settleYieldLimit) yields", file: file, line: line)
    }

    private func frame(_ tracks: String) -> StateEvent {
        StateEvent(
            type: MusicCoverVersions.event,
            frame: #"{"type":"library.cover.changed","data":{"tracks":\#(tracks)}}"#
        )
    }

    // MARK: - the fields

    /// The defect: the V059 fields dropped, so no version and no credits.
    func testTrackRowsDecodeTheV059FieldsAndAnOlderServerMeansNone() throws {
        let row = try JSONDecoder().decode(LibraryTrack.self, from: Data(#"""
        {"id":12,"title":"Alpha","artist":"Nidra Vane","artists":["Nidra Vane","Orbis Quill","Tessaly Brook"],
         "cover_v":3,"cover_url":"/api/music/library/12/cover?v=3"}
        """#.utf8))
        XCTAssertEqual(["Nidra Vane", "Orbis Quill", "Tessaly Brook"], row.artists)
        XCTAssertEqual(3, row.coverV)
        XCTAssertEqual("/api/music/library/12/cover?v=3", row.coverURL)
        XCTAssertEqual(row, row.favorited(true).favorited(false), "the favourite toggle keeps every field")

        let old = try JSONDecoder().decode(LibraryTrack.self, from: Data(#"{"id":12,"title":"Alpha"}"#.utf8))
        XCTAssertEqual([], old.artists)
        XCTAssertNil(old.coverV)
        XCTAssertNil(old.coverURL)

        // One odd field must not throw the whole page away.
        let odd = try JSONDecoder().decode(
            LibraryTrack.self, from: Data(#"{"id":12,"artists":"Orbis","cover_v":"x"}"#.utf8)
        )
        XCTAssertEqual([], odd.artists)
        XCTAssertNil(odd.coverV)
    }

    func testQueueRowsDecodeTheV059Fields() throws {
        let rows = try JSONDecoder().decode([QueueItem].self, from: Data(#"""
        [{"song_id":7,"pos":0,"file":"a.flac","track_id":12,"artists":["B"],"cover_v":2,
          "cover_url":"/api/music/library/12/cover?v=2"},
         {"song_id":8,"pos":1,"file":"http://radio.example.com/x","artists":[],"cover_v":null,"cover_url":null}]
        """#.utf8))
        XCTAssertEqual([["B"], []], rows.map(\.artists))
        XCTAssertEqual([2, nil], rows.map(\.coverV))
        XCTAssertEqual(["/api/music/library/12/cover?v=2", nil], rows.map(\.coverURL))
    }

    // MARK: - credits

    /// The defect: credits shown in other words, or repeating the artist.
    func testCreditsAreTheWebsWords() throws {
        XCTAssertEqual("with Orbis Quill, Tessaly Brook", MusicCredits.with(
            artist: "Nidra Vane", artists: ["Nidra Vane", "Orbis Quill", "Tessaly Brook"]
        ))
        XCTAssertEqual("", MusicCredits.with(artist: "Main feat. Guest", artists: ["Main", "guest"]))
        XCTAssertEqual("", MusicCredits.with(artist: "A", artists: []))
        XCTAssertEqual("with B", MusicCredits.with(artist: nil, artists: ["  ", "B"]))
        XCTAssertEqual("A · with B, C", MusicCredits.artistLine("A", credits: "with B, C"))
        XCTAssertEqual("A", MusicCredits.artistLine("A", credits: ""))

        let jsx = try String(
            contentsOf: Self.repositoryRoot.appendingPathComponent("web/static/components.jsx"),
            encoding: .utf8
        )
        XCTAssertTrue(jsx.contains("`\(MusicCredits.lead)${extra.join('\(MusicCredits.joiner)')}`"))
        XCTAssertTrue(jsx.contains("!shown.includes(n.trim().toLowerCase())"))
    }

    // MARK: - the versions heard

    /// The defect: the event ignored, so a row read earlier, a room card and
    /// the lock screen kept the old picture.
    func testTheEventMovesOnlyNewerVersions() {
        let versions = MusicCoverVersions()
        let moved = versions.received(frame(#"[{"id":12,"cover_v":3,"cover_url":"x"},{"id":13,"cover_v":1}]"#), server: server)
        XCTAssertEqual([12, 13], moved)
        XCTAssertEqual(3, versions.known(12))
        // The event repeats the 50 newest every time: nothing new, nothing moved.
        XCTAssertEqual([], versions.received(frame(#"[{"id":12,"cover_v":3},{"id":13,"cover_v":1}]"#), server: server))
        XCTAssertEqual([12], versions.received(frame(#"[{"id":12,"cover_v":4},{"id":13,"cover_v":1}]"#), server: server))
        XCTAssertEqual([], versions.received(frame(#"[{"id":12,"cover_v":2}]"#), server: server), "never backwards")
        XCTAssertEqual(4, versions.known(12))
        XCTAssertEqual([], versions.received(StateEvent(type: "playlists.changed", frame: "{}"), server: server))
        XCTAssertEqual([], versions.received(frame(#"[{"id":"x"},{"cover_v":2},"junk"]"#), server: server))

        // Another server's track 12 is another song.
        versions.serverIs("http://10.0.0.77:6369")
        XCTAssertNil(versions.known(12))
    }

    /// The version drawn is the larger of the row's and the newest heard;
    /// neither means the bare route (an older server, checked by ETag).
    func testTheVersionDrawnIsTheLargerOfRowAndEvent() {
        let versions = MusicCoverVersions()
        XCTAssertNil(versions.version(12, row: nil))
        XCTAssertEqual(2, versions.version(12, row: 2))
        versions.received(frame(#"[{"id":12,"cover_v":5}]"#), server: server)
        XCTAssertEqual(5, versions.version(12, row: 2))
        XCTAssertEqual(5, versions.version(12, row: nil))
        XCTAssertEqual(7, versions.version(12, row: 7))
    }

    /// The player's item was queued with the path of its day: the lock
    /// screen asks for the newest version heard.
    func testThePlayersPathMovesToTheNewestVersion() {
        let versions = MusicCoverVersions()
        versions.received(frame(#"[{"id":12,"cover_v":5}]"#), server: server)
        XCTAssertEqual("/api/music/library/12/cover?v=5", versions.versioned("/api/music/library/12/cover"))
        XCTAssertEqual("/api/music/library/12/cover?v=5", versions.versioned("/api/music/library/12/cover?v=2"))
        XCTAssertEqual("/api/music/library/12/cover?v=9", versions.versioned("/api/music/library/12/cover?v=9"))
        XCTAssertEqual("/api/music/library/13/cover", versions.versioned("/api/music/library/13/cover"))
        XCTAssertEqual("/api/podcasts/art/4", versions.versioned("/api/podcasts/art/4"))
        XCTAssertNil(versions.versioned(nil))

        XCTAssertEqual(
            "/api/music/library/12/cover?v=3",
            PlayerItem.fromTrack(id: 12, title: "Alpha", coverV: 3).coverPath,
            "a song queued from a row asks for the version that row was read with"
        )
        XCTAssertEqual("/api/music/library/12/cover", PlayerItem.fromTrack(id: 12, title: "Alpha").coverPath)
    }

    // MARK: - the store

    /// The defect: a versioned cover asked about again after every return
    /// (it is that version's picture for good), or a new version served from
    /// the old one's bytes.
    func testAKnownVersionIsNotAskedAgainAndANewOneIsFetchedFresh() async {
        let asks = Shared<[(MusicCoverKey, String?)]>([])
        let renewal = MusicCoverRenewal()
        let covers = MusicCoverStore(renewal: renewal, revalidate: { key, etag in
            asks.mutate { $0.append((key, etag)) }
            return .picture(Self.picture, etag: "\"e\"")
        })
        let v2 = MusicCoverKey(trackID: 12, version: 2)
        covers.want(v2)
        await settleUntil({ !covers.isAsking(v2) }, "version 2")
        renewal.appReturned()
        covers.want(v2)
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(1, asks.current.count, "a known version was asked about again")

        let v3 = MusicCoverKey(trackID: 12, version: 3)
        covers.want(v3)
        await settleUntil({ !covers.isAsking(v3) }, "version 3")
        XCTAssertEqual(2, asks.current.count)
        XCTAssertEqual(v3, asks.current.last?.0)
        XCTAssertNil(asks.current.last?.1, "a new version is fetched fresh, not revalidated")
        XCTAssertEqual("/api/music/library/12/cover?v=3", asks.current.last?.0.path)
    }

    /// While a new version is fetched the old picture is what there is.
    func testTheNewestPictureHeldIsFoundForAnyVersion() async {
        let covers = MusicCoverStore(renewal: MusicCoverRenewal(), revalidate: { _, _ in
            .picture(Self.picture, etag: nil)
        })
        XCTAssertNil(covers.newestImage(trackID: 12))
        covers.want(MusicCoverKey(trackID: 12, version: 2))
        await settleUntil({ covers.newestImage(trackID: 12) != nil }, "version 2")
        XCTAssertEqual(.image(Self.picture), covers.newestImage(trackID: 12))
        XCTAssertNil(covers.newestImage(trackID: 13))
    }

    func testACoverPathIsReadBack() {
        XCTAssertEqual(12, PlayerArtwork.coverPathParts("/api/music/library/12/cover")?.trackID)
        XCTAssertNil(PlayerArtwork.coverPathParts("/api/music/library/12/cover")?.version)
        XCTAssertEqual(4, PlayerArtwork.coverPathParts("/api/music/library/12/cover?v=4")?.version)
        XCTAssertNil(PlayerArtwork.coverPathParts("/api/music/library/12/audio"))
        XCTAssertNil(PlayerArtwork.coverPathParts("/api/podcasts/art/4"))
    }
}
