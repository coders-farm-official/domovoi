import XCTest
@testable import DomovoiKit

/// The stats tiles, the track sheet's metadata table, the two delete
/// questions, the tab labels and `fmtBigDur`.
///
/// All of it arithmetic over a server's answer, and all of it specified by
/// nothing on Android: `StatsGrid` and `MetaRow` are composables, and the
/// awkward cases — a library of zero, durations never indexed, one
/// `added_via` bucket, a sweep that finished between two queries — are
/// `?:`-expressions in the middle of a layout.
final class MusicStatsAndSheetTests: XCTestCase {

    // MARK: - fmtBigDur

    func testABigDurationDropsTheSeconds() {
        XCTAssertEqual("0m", fmtBigDur(0))
        // Whole seconds first, then the division: 30 s is 0m, not a rounded
        // 1m. Android's `fmtBigDur` does the same, and this expectation was
        // mine rather than the function's.
        XCTAssertEqual("0m", fmtBigDur(30))
        XCTAssertEqual("0m", fmtBigDur(59))
        XCTAssertEqual("1m", fmtBigDur(60))
        XCTAssertEqual("1m", fmtBigDur(119))
        XCTAssertEqual("47m", fmtBigDur(47 * 60))
        XCTAssertEqual("1h 0m", fmtBigDur(3_600))
        XCTAssertEqual("2h 14m", fmtBigDur(2 * 3_600 + 14 * 60))
    }

    /// Nil, negative and non-finite are all an em dash. Android's version
    /// guards none of the three: `fmtBigDur(Double.NaN)` over there renders
    /// `-2147483648h`, because `roundToInt()` on a NaN is `Int.MIN_VALUE`.
    func testABigDurationThatIsNotANumberIsAnEmDash() {
        XCTAssertEqual("—", fmtBigDur(nil))
        XCTAssertEqual("—", fmtBigDur(-1))
        XCTAssertEqual("—", fmtBigDur(.nan))
        XCTAssertEqual("—", fmtBigDur(.infinity))
    }

    // MARK: - the tiles

    private let full = LibraryStats(
        totalTracks: 5_232,
        totalDurationSec: 1_329_000,
        byAddedVia: ["manual": 4_000, "voice": 1_200, "plugin": 32],
        enrichedCount: 412
    )

    func testTheFourTilesReadAFullLibrary() {
        let tiles = full.tiles

        XCTAssertEqual(4, tiles.count)
        XCTAssertEqual("total tracks", tiles[0].label)
        XCTAssertEqual("5232", tiles[0].value)
        XCTAssertEqual("across all sources", tiles[0].sub)

        XCTAssertEqual("total duration", tiles[1].label)
        XCTAssertEqual("369h 10m", tiles[1].value)
        XCTAssertEqual("22150 minutes", tiles[1].sub)

        // The biggest bucket by count, with the rest beneath it — and the
        // vocabulary is the server's, so nothing here knows what "plugin" is.
        XCTAssertEqual("added via manual", tiles[2].label)
        XCTAssertEqual("4000", tiles[2].value)
        XCTAssertEqual("1200 voice · 32 plugin", tiles[2].sub)

        XCTAssertEqual("enriched", tiles[3].label)
        XCTAssertEqual("412 / 5232", tiles[3].value)
        XCTAssertEqual("4820 pending", tiles[3].sub)
    }

    /// A library of zero is four tiles that say so, not four that look broken:
    /// `—` where a ratio would be, and no "pending" line at all.
    func testAnEmptyLibrarySaysNothingItCannotSay() {
        let tiles = LibraryStats().tiles

        XCTAssertEqual("0", tiles[0].value)
        XCTAssertEqual("—", tiles[1].value)
        XCTAssertEqual("no durations indexed", tiles[1].sub)
        XCTAssertEqual("added via —", tiles[2].label)
        XCTAssertEqual("0", tiles[2].value)
        XCTAssertNil(tiles[2].sub)
        XCTAssertEqual("—", tiles[3].value)
        XCTAssertNil(tiles[3].sub)
    }

    /// One bucket has nothing to list under it.
    func testASingleAddedViaBucketHasNoSecondLine() {
        let tiles = LibraryStats(totalTracks: 3, byAddedVia: ["manual": 3]).tiles

        XCTAssertEqual("added via manual", tiles[2].label)
        XCTAssertEqual("3", tiles[2].value)
        XCTAssertNil(tiles[2].sub)
    }

    /// Two buckets of equal size must not reshuffle between reads. A Swift
    /// `Dictionary`'s iteration order is not stable between runs, so "the
    /// biggest bucket" would otherwise be a coin toss — the tile would change
    /// its own label on a refresh that changed nothing.
    func testEqualBucketsAreBrokenByNameSoTheTileDoesNotMoveOnItsOwn() {
        let stats = LibraryStats(totalTracks: 4, byAddedVia: ["voice": 2, "manual": 2])

        for _ in 0..<20 {
            let tiles = stats.tiles
            XCTAssertEqual("added via manual", tiles[2].label)
            XCTAssertEqual("2", tiles[2].value)
            XCTAssertEqual("2 voice", tiles[2].sub)
        }
    }

    /// A duration of zero is not a duration. The server sends `0` for a
    /// library whose files were never probed, and `0m` beside "0 minutes"
    /// reads as a measurement.
    func testAZeroDurationIsTreatedAsNoDuration() {
        let tiles = LibraryStats(totalTracks: 9, totalDurationSec: 0).tiles

        XCTAssertEqual("—", tiles[1].value)
        XCTAssertEqual("no durations indexed", tiles[1].sub)
    }

    /// The two numbers come from different queries, so a sweep that finished
    /// between them can report more enriched rows than rows. Android's
    /// subtraction would say "-3 pending".
    func testMoreEnrichedThanTotalDoesNotProduceANegativePending() {
        let tiles = LibraryStats(totalTracks: 10, enrichedCount: 13).tiles

        XCTAssertEqual("13 / 10", tiles[3].value)
        XCTAssertEqual("0 pending", tiles[3].sub)
    }

    // MARK: - the metadata table

    private let track = LibraryTrack(
        id: 42,
        title: "Hey Jude",
        artist: "The Beatles",
        album: "1",
        durationSec: 431,
        source: "spotify",
        sourceID: "spot:7",
        addedAt: "2026-10-31T11:00:00Z",
        addedVia: "voice",
        enrichedAt: "2026-10-31T11:30:00Z",
        filePath: "/srv/music/hey jude.mp3"
    )

    /// `2026-10-31T12:00:00Z`, so the track's two timestamps are an hour and
    /// half an hour old.
    private let noon = Int64(1_793_448_000_000)

    func testTheSheetsTableReadsEveryFieldTheServerSent() {
        let rows = track.metaRows(nowMs: noon)

        XCTAssertEqual(
            ["duration", "source", "source id", "added", "added via", "enriched", "path"],
            rows.map(\.label)
        )
        XCTAssertEqual("7:11", rows[0].value)
        XCTAssertEqual("spotify", rows[1].value)
        XCTAssertEqual(.pill(brand: true), rows[1].style)
        XCTAssertEqual("spot:7", rows[2].value)
        XCTAssertEqual("1h ago", rows[3].value)
        XCTAssertEqual("voice", rows[4].value)
        XCTAssertEqual("30m ago", rows[5].value)
        XCTAssertEqual("/srv/music/hey jude.mp3", rows[6].value)
    }

    /// A row the library found on disk: **an em dash under `source`, never
    /// the word "manual"**, which is what the row below it already says.
    func testATrackWithNoProviderShowsADashAndNotTheWordManual() {
        let found = LibraryTrack(id: 1, addedVia: "manual")
        let rows = found.metaRows(nowMs: noon)

        XCTAssertEqual("—", rows[1].value)
        XCTAssertEqual(.mono, rows[1].style, "nothing to put in a pill")
        XCTAssertEqual("manual", rows[4].value)
        // Only `voice` gets the brand tone: the one route that is a person
        // talking to the house rather than a file appearing.
        XCTAssertEqual(.pill(brand: false), rows[4].style)
    }

    /// Everything missing is an em dash, and the two timestamps are an em
    /// dash each rather than "just now".
    func testATrackWithNothingOnItIsAllEmDashes() {
        let rows = LibraryTrack(id: 1).metaRows(nowMs: noon)

        XCTAssertEqual("—", rows[0].value, "no duration")
        XCTAssertEqual("—", rows[1].value)
        XCTAssertEqual("—", rows[2].value)
        XCTAssertEqual("—", rows[3].value, "never read a missing added_at as now")
        XCTAssertEqual("—", rows[4].value)
        XCTAssertEqual("—", rows[5].value)
        XCTAssertEqual("—", rows[6].value)
    }

    /// A whitespace-only `source` is no source — the same trim
    /// ``LibraryTrack/sourcePill`` applies, so the pill and this row cannot
    /// disagree.
    func testABlankSourceIsNoSource() {
        let rows = LibraryTrack(id: 1, source: "   ").metaRows(nowMs: noon)

        XCTAssertEqual("—", rows[1].value)
    }

    // MARK: - the two delete questions

    /// Removing a row and deleting a file are different acts, so they are
    /// different sentences — and the one that keeps the file says what brings
    /// it back.
    func testTheLibraryOnlyDeleteSaysTheFileStays() {
        let asked = MusicModel.deleteQuestion(track, alsoFile: false)

        XCTAssertEqual(
            "Remove \"Hey Jude\" from the library? "
                + "The file stays on disk; a rescan will re-add it.",
            asked
        )
    }

    /// The irreversible one says so, and shows the path it is about.
    func testTheFileDeleteSaysItCannotBeUndoneAndShowsThePath() {
        let asked = MusicModel.deleteQuestion(track, alsoFile: true)

        XCTAssertTrue(asked.contains("Permanently delete the file for \"Hey Jude\""), asked)
        XCTAssertTrue(asked.contains("can't be undone"), asked)
        XCTAssertTrue(asked.hasSuffix("\n\n/srv/music/hey jude.mp3"), asked)
    }

    /// With no path there is no path line — not an empty one, and not two
    /// trailing newlines over nothing.
    func testTheFileDeleteWithNoPathShowsNoPath() {
        let asked = MusicModel.deleteQuestion(LibraryTrack(id: 9, title: "x"), alsoFile: true)

        XCTAssertTrue(asked.hasSuffix("can't be undone."), asked)
    }

    /// An untitled track is named the way every other surface names it, which
    /// is the point of there being one ``LibraryTrack/displayTitle``.
    func testAnUntitledTrackIsNamedByItsFileInTheQuestionToo() {
        let asked = MusicModel.deleteQuestion(
            LibraryTrack(id: 9, filePath: #"C:\music\odd.flac"#),
            alsoFile: false
        )

        XCTAssertTrue(asked.contains("\"odd.flac\""), asked)
    }

    // MARK: - the tabs

    func testTheTabsAreTheThreeThatHaveSomethingBehindThem() {
        XCTAssertEqual([.library, .queue, .stats], MusicTab.allCases)
    }

    func testATabCarriesItsCountOnlyWhenThereIsOne() {
        XCTAssertEqual("library · 5232", MusicTab.library.label(libraryTotal: 5_232))
        // Nil is not zero: a library whose size has not been read yet is not
        // `library · 0`.
        XCTAssertEqual("library", MusicTab.library.label(libraryTotal: nil))
        XCTAssertEqual("library · 0", MusicTab.library.label(libraryTotal: 0))

        XCTAssertEqual("room queue · 3", MusicTab.queue.label(queueCount: 3))
        XCTAssertEqual("room queue", MusicTab.queue.label(queueCount: nil))
        // An empty queue's tab is bare: `room queue · 0` is a number that
        // tells you less than the word on its own.
        XCTAssertEqual("room queue", MusicTab.queue.label(queueCount: 0))

        XCTAssertEqual("stats", MusicTab.stats.label(libraryTotal: 5_232, queueCount: 3))
    }

    // MARK: - what enrich answers

    func testAStartedSweepSaysSo() {
        XCTAssertEqual("enrich started", EnrichReply(queued: true).text)
    }

    /// An older core sends no `queued` at all, and always meant "started".
    func testAnOlderCoreWithNoQueuedFieldStillReadsAsStarted() {
        XCTAssertEqual("enrich started", EnrichReply().text)
    }

    func testEachRefusalGetsItsOwnSentence() {
        XCTAssertEqual(
            "song recognition is turned off on the server",
            EnrichReply(queued: false, reason: "disabled").text
        )
        XCTAssertEqual(
            "no internet right now — try again when it's back",
            EnrichReply(queued: false, reason: "offline").text
        )
        XCTAssertEqual(
            "song recognition needs a free AcoustID key or the Shazam add-on",
            EnrichReply(queued: false, reason: "no_provider").text
        )
        XCTAssertEqual(
            "already identifying songs",
            EnrichReply(queued: false, reason: "running").text
        )
    }

    /// The reasons are an open set: a reason this app has never heard of is
    /// **said**, because "enrich didn't start" with nothing after it is a
    /// dead end for whoever has to work out why.
    func testAnUnknownReasonIsRepeatedRatherThanSwallowed() {
        XCTAssertEqual(
            "enrich didn't start (quota_exhausted)",
            EnrichReply(queued: false, reason: "quota_exhausted").text
        )
        XCTAssertEqual(
            "enrich didn't start (unknown reason)",
            EnrichReply(queued: false, reason: nil).text
        )
    }

    func testTheReplyDecodesFromTheWire() throws {
        let reply = try JSONDecoder().decode(
            EnrichReply.self,
            from: Data(#"{"queued":false,"reason":"offline"}"#.utf8)
        )

        XCTAssertEqual(false, reply.queued)
        XCTAssertEqual("offline", reply.reason)
        // And an answer with neither field, which is what a core that just
        // returns `{}` sends.
        let bare = try JSONDecoder().decode(EnrichReply.self, from: Data("{}".utf8))
        XCTAssertNil(bare.queued)
        XCTAssertNil(bare.reason)
    }
}
