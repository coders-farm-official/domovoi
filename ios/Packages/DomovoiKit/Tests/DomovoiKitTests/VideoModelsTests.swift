import Foundation
import XCTest
@testable import DomovoiKit

/// Videos' wire shapes, the grid's arithmetic and every route it asks for.
///
/// Android tests none of this: `VideosScreen.kt` is a composable and its
/// `LibraryTotal`, `videoKey` and `videoIdentityQuery` are file-private
/// helpers with no JVM test over them. The grouping in particular **must** be
/// specified here rather than ported on trust, because the Kotlin relies on
/// `groupBy` returning a `LinkedHashMap` and the obvious Swift translation
/// has no order at all.
final class VideoModelsTests: XCTestCase {

    private func decode<T: Decodable>(_ json: String, as type: T.Type = T.self) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - what the wire can say

    /// Every field absent. A `/list` row from a server that answered with an
    /// empty object is a row with no identity — and it decodes rather than
    /// throwing, because one unreadable row must not take the page with it.
    func testAnEmptyObjectDecodesToAnEmptyRow() throws {
        let row: VideoRow = try decode("{}")

        XCTAssertEqual("", row.libraryID)
        XCTAssertNil(row.libraryLabel)
        XCTAssertEqual("", row.rel)
        XCTAssertEqual("", row.name)
        XCTAssertNil(row.size)
        XCTAssertNil(row.mtime)
        XCTAssertNil(row.positionSec)
        XCTAssertNil(row.durationSec)
        XCTAssertNil(row.title)
        XCTAssertEqual(":", row.id)
        XCTAssertEqual(VideoStrings.unnamedVideo, row.displayTitle)
    }

    func testAFullRowDecodesEverySnakeCaseField() throws {
        let row: VideoRow = try decode("""
        {"library_id":"core:videos","library_label":"Movies","rel":"clips/holiday.mp4",
         "name":"holiday.mp4","size":734003200,"mtime":1762000000.5,
         "position_sec":72,"duration_sec":2910,"title":"Holiday 2026"}
        """)

        XCTAssertEqual("core:videos", row.libraryID)
        XCTAssertEqual("Movies", row.libraryLabel)
        XCTAssertEqual("clips/holiday.mp4", row.rel)
        XCTAssertEqual("holiday.mp4", row.name)
        XCTAssertEqual(734_003_200, row.size)
        XCTAssertEqual(1_762_000_000.5, row.mtime)
        XCTAssertEqual(72, row.positionSec)
        XCTAssertEqual(2910, row.durationSec)
        XCTAssertEqual("Holiday 2026", row.title)
    }

    /// The id splits from the **left** at the first colon, because the
    /// server's own library ids contain one.
    func testTheIdIsLibraryThenPathAndTheLibraryMayHoldAColon() {
        let row = VideoRow(libraryID: "core:videos", rel: "a/b.mp4")

        XCTAssertEqual("core:videos:a/b.mp4", row.id)
    }

    func testTheTitleWinsOverTheFilename() {
        let row = VideoRow(rel: "clips/x.mp4", name: "x.mp4", title: "Holiday 2026")

        XCTAssertEqual("Holiday 2026", row.displayTitle)
    }

    /// A `title` of spaces is not a title. A resume row saved under a blank
    /// one would otherwise draw a strip of empty labels.
    func testABlankTitleFallsThroughToTheFilename() {
        let row = VideoRow(rel: "clips/x.mp4", name: "x.mp4", title: "   ")

        XCTAssertEqual("x.mp4", row.displayTitle)
    }

    func testWithNoNameTheRelativePathIsTheName() {
        let row = VideoRow(rel: "clips/x.mp4", name: "")

        XCTAssertEqual("clips/x.mp4", row.displayTitle)
    }

    func testTheLocationPrefersTheLibrarysOwnNameAndFallsBackToItsId() {
        let named = VideoRow(libraryID: "core:videos", libraryLabel: "Movies", rel: "a/b.mp4")
        let bare = VideoRow(libraryID: "core:videos", rel: "a/b.mp4")

        XCTAssertEqual("Movies/a/b.mp4", named.location)
        XCTAssertEqual("core:videos/a/b.mp4", bare.location)
    }

    // MARK: - how far through it is

    func testAWatchedFractionNeedsAUsableDuration() {
        XCTAssertNil(VideoRow(positionSec: 30, durationSec: nil).watchedFraction)
        XCTAssertNil(VideoRow(positionSec: 30, durationSec: 0).watchedFraction)
        XCTAssertNil(VideoRow(positionSec: 30, durationSec: -10).watchedFraction)
    }

    func testAWatchedFractionIsClampedAtBothEnds() throws {
        let half = try XCTUnwrap(VideoRow(positionSec: 50, durationSec: 100).watchedFraction)
        XCTAssertEqual(0.5, half, accuracy: 0.0001)

        // A position past the end — a resume row saved by a player that
        // reported one — is a full bar, not a bar overflowing its track.
        let over = try XCTUnwrap(VideoRow(positionSec: 500, durationSec: 100).watchedFraction)
        XCTAssertEqual(1, over, accuracy: 0.0001)

        let under = try XCTUnwrap(VideoRow(positionSec: -20, durationSec: 100).watchedFraction)
        XCTAssertEqual(0, under, accuracy: 0.0001)
    }

    func testTheResumeLineDropsTheLengthItDoesNotKnow() {
        XCTAssertEqual("1:12 / 48:30", VideoRow(positionSec: 72, durationSec: 2910).resumeLine)
        XCTAssertEqual("1:12", VideoRow(positionSec: 72, durationSec: nil).resumeLine)
        XCTAssertEqual("1:12", VideoRow(positionSec: 72, durationSec: 0).resumeLine)
        XCTAssertEqual("0:00", VideoRow(positionSec: nil, durationSec: nil).resumeLine)
    }

    // MARK: - the per-library totals

    func testTheTotalsDecodeFromTheListAnswer() throws {
        let totals: VideoLibraryTotals = try decode("""
        {"videos":[],"total":0,
         "libraries":[{"library_id":"core:videos","total":12},
                      {"library_id":"usb","total":3}]}
        """)

        XCTAssertEqual(["core:videos": 12, "usb": 3], totals.byLibrary)
    }

    /// ``Page`` tolerates a bare array, so this must too: the fetch decodes
    /// the **same body** as both, and a throw here would be a throw on a page
    /// of rows that read perfectly well.
    func testABareArrayIsNoTotalsRatherThanAnError() throws {
        let totals: VideoLibraryTotals = try decode("[{\"rel\":\"a.mp4\"}]")

        XCTAssertEqual([:], totals.byLibrary)
    }

    func testATotalWithNoLibraryIdIsDroppedAndAMissingTotalIsZero() throws {
        let totals: VideoLibraryTotals = try decode("""
        {"libraries":[{"total":9},{"library_id":"","total":4},{"library_id":"usb"}]}
        """)

        XCTAssertEqual(["usb": 0], totals.byLibrary)
    }

    // MARK: - grouping, in order

    private func row(_ library: String, _ rel: String, label: String? = nil) -> VideoRow {
        VideoRow(libraryID: library, libraryLabel: label, rel: rel, name: rel)
    }

    /// **The order is the test.** `Dictionary(grouping:by:)` would pass every
    /// other assertion in this file and reshuffle the sections between two
    /// reads of the same list.
    func testGroupingKeepsTheOrderTheRowsArrivedIn() {
        let grouped = VideoGrid.grouped([
            row("zz", "z1.mp4"),
            row("aa", "a1.mp4"),
            row("mm", "m1.mp4"),
        ])

        XCTAssertEqual(["zz", "aa", "mm"], grouped.map(\.libraryID))
    }

    /// A library that reappears later in the list joins the group it already
    /// has rather than opening a second section with the same heading.
    func testALibraryThatComesBackJoinsItsOwnGroup() {
        let grouped = VideoGrid.grouped([
            row("aa", "a1.mp4"),
            row("bb", "b1.mp4"),
            row("aa", "a2.mp4"),
        ])

        XCTAssertEqual(["aa", "bb"], grouped.map(\.libraryID))
        let first = grouped.first { $0.libraryID == "aa" }
        XCTAssertEqual(["a1.mp4", "a2.mp4"], first?.videos.map(\.rel))
    }

    func testGroupingNothingIsNoGroups() {
        XCTAssertTrue(VideoGrid.grouped([]).isEmpty)
    }

    // MARK: - the grid's entries

    func testTheRecentStripIsTheFirstEntryWhenItIsShown() {
        let grouped = VideoGrid.grouped([row("aa", "a1.mp4")])

        let with = VideoGrid.entries(showRecent: true, grouped: grouped)
        let without = VideoGrid.entries(showRecent: false, grouped: grouped)

        XCTAssertEqual("recent", with.first?.id)
        XCTAssertEqual("hdr:aa", without.first?.id)
        XCTAssertEqual(with.count, without.count + 1)
    }

    func testAHeaderTakesTheLibrarysLabelAndFallsBackToItsId() throws {
        let grouped = VideoGrid.grouped([
            row("aa", "a1.mp4", label: "Movies"),
            row("bb", "b1.mp4"),
        ])

        let entries = VideoGrid.entries(showRecent: false, grouped: grouped)
        let headers = entries.compactMap { entry -> (String, String, Int)? in
            guard case .header(let id, let label, let count) = entry else { return nil }
            return (id, label, count)
        }

        XCTAssertEqual(2, headers.count)
        let first = try XCTUnwrap(headers.first)
        XCTAssertEqual("aa", first.0)
        XCTAssertEqual("Movies", first.1)
        let second = try XCTUnwrap(headers.last)
        XCTAssertEqual("bb", second.0)
        XCTAssertEqual("bb", second.1)
    }

    /// The count is the **server's** total, so a library with twelve videos
    /// reads "12" over the two tiles that have loaded so far. A server that
    /// sent no total falls back to what is loaded, which is the only honest
    /// number then.
    func testTheHeaderCountIsTheServersTotalWhenThereIsOne() throws {
        let grouped = VideoGrid.grouped([row("aa", "a1.mp4"), row("aa", "a2.mp4")])

        let told = VideoGrid.entries(showRecent: false, grouped: grouped, totals: ["aa": 12])
        let untold = VideoGrid.entries(showRecent: false, grouped: grouped)

        func count(_ entries: [VideoGridEntry]) throws -> Int {
            let header = try XCTUnwrap(entries.first { if case .header = $0 { return true } else { return false } })
            guard case .header(_, _, let count) = header else { return -1 }
            return count
        }

        XCTAssertEqual(12, try count(told))
        XCTAssertEqual(2, try count(untold))
    }

    /// A heading over nothing reads as a failed section.
    func testALibraryWithNoVideosGetsNoHeader() {
        let entries = VideoGrid.entries(
            showRecent: false,
            grouped: [(libraryID: "aa", videos: []), (libraryID: "bb", videos: [row("bb", "b1.mp4")])]
        )

        XCTAssertEqual(["hdr:bb", "bb:b1.mp4"], entries.map(\.id))
    }

    func testOnlyAHeaderIsFullWidthAndOnlyATileHasAVideo() {
        let entries = VideoGrid.entries(
            showRecent: true,
            grouped: VideoGrid.grouped([row("aa", "a1.mp4")])
        )

        XCTAssertEqual([true, true, false], entries.map(\.isFullWidth))
        XCTAssertEqual(["aa:a1.mp4"], entries.compactMap(\.video).map(\.id))
    }

    // MARK: - the entries read as sections

    /// The tile index counts **tiles**, across every library and ignoring the
    /// recent strip — which is what makes "load more when the last tile
    /// appears" fire once at the end of the grid rather than once per
    /// library.
    func testTileIndicesRunThroughTheWholeGridNotPerSection() throws {
        let entries = VideoGrid.entries(
            showRecent: true,
            grouped: VideoGrid.grouped([
                row("aa", "a1.mp4"),
                row("aa", "a2.mp4"),
                row("bb", "b1.mp4"),
            ])
        )

        let sections = VideoGrid.sections(entries)

        XCTAssertEqual(["aa", "bb"], sections.map(\.libraryID))
        let first = try XCTUnwrap(sections.first)
        XCTAssertEqual([0, 1], first.tiles.map(\.gridIndex))
        let second = try XCTUnwrap(sections.last)
        XCTAssertEqual([2], second.tiles.map(\.gridIndex))
    }

    /// The sections are a **reading of the entries**, so the two cannot
    /// drift: every tile in the flat list is in exactly one section, in the
    /// same order.
    func testEverySectionsTilesAreTheEntriesTilesInOrder() {
        let entries = VideoGrid.entries(
            showRecent: true,
            grouped: VideoGrid.grouped([
                row("aa", "a1.mp4"),
                row("bb", "b1.mp4"),
                row("bb", "b2.mp4"),
            ])
        )

        let flat = VideoGrid.sections(entries).flatMap(\.tiles).map(\.video.id)

        XCTAssertEqual(entries.compactMap(\.video).map(\.id), flat)
    }

    /// A tile with no header above it cannot come out of `entries`. It is
    /// handled anyway — and the point of the test is that it does not trap,
    /// because a crash in a grid is the worst possible answer to an
    /// impossible input.
    func testATileWithNoHeaderMakesItsOwnSectionRatherThanTrapping() throws {
        let sections = VideoGrid.sections([.tile(row("aa", "a1.mp4", label: "Movies"))])

        let only = try XCTUnwrap(sections.first)
        XCTAssertEqual(1, sections.count)
        XCTAssertEqual("aa", only.libraryID)
        XCTAssertEqual("Movies", only.label)
        XCTAssertEqual(1, only.count)
        XCTAssertEqual([0], only.tiles.map(\.gridIndex))
    }

    func testTheRecentStripIsNotASection() {
        XCTAssertTrue(VideoGrid.sections([.recent]).isEmpty)
    }

    // MARK: - the viewing order, and finding a video again

    func testTheFeedOrderIsEveryTileAndNothingElse() {
        let entries = VideoGrid.entries(
            showRecent: true,
            grouped: VideoGrid.grouped([row("aa", "a1.mp4"), row("bb", "b1.mp4")])
        )

        XCTAssertEqual(["aa:a1.mp4", "bb:b1.mp4"], VideoGrid.feedOrder(entries).map(\.id))
    }

    func testAVideoNoLongerInTheGridIsNotFoundRatherThanFoundAtMinusOne() {
        let entries = VideoGrid.entries(
            showRecent: true,
            grouped: VideoGrid.grouped([row("aa", "a1.mp4")])
        )

        XCTAssertEqual(0, VideoGrid.index(of: "recent", in: entries))
        XCTAssertEqual(1, VideoGrid.index(of: "hdr:aa", in: entries))
        XCTAssertEqual(2, VideoGrid.index(of: "aa:a1.mp4", in: entries))
        XCTAssertNil(VideoGrid.index(of: "aa:gone.mp4", in: entries))
    }

    // MARK: - the routes

    func testTheListAsksForTheLibrarySortAndLeavesABlankFilterOut() {
        XCTAssertEqual("/api/videos/list?sort=library", VideoPath.list())
        XCTAssertEqual("/api/videos/list?sort=library", VideoPath.list(filter: "   "))
        XCTAssertEqual("/api/videos/list?sort=library&q=the%20sea", VideoPath.list(filter: "  the sea  "))
    }

    /// A library id with a colon, a path with a space, a `&` and a Windows
    /// backslash — all of which the house actually serves, and every one of
    /// which silently reads the wrong video if it reaches the query raw.
    func testEveryRouteEscapesTheIdentityPair() {
        let row = VideoRow(libraryID: "core:videos", rel: "a b&c\\d.mp4")
        let identity = "library_id=core%3Avideos&path=a%20b%26c%5Cd.mp4"

        XCTAssertEqual("/api/videos/stream?\(identity)", VideoPath.stream(row))
        XCTAssertEqual("/api/videos/stream?\(identity)&download=1", VideoPath.stream(row, download: true))
        XCTAssertEqual("/api/videos/poster?\(identity)", VideoPath.poster(row))
        XCTAssertEqual(
            "/api/videos/position?\(identity)&device_id=ios-1",
            VideoPath.position(row, deviceID: "ios-1")
        )
    }

    /// `person_id=` empty is a different key from absent, so an unsigned-in
    /// phone sends no person at all.
    func testTheWatcherSendsNoPersonWhenNobodyIsSignedIn() {
        XCTAssertEqual("device_id=ios%201", VideoPath.watcher(deviceID: "ios 1"))
        XCTAssertEqual("device_id=ios-1", VideoPath.watcher(deviceID: "ios-1", personID: ""))
        XCTAssertEqual("device_id=ios-1&person_id=4", VideoPath.watcher(deviceID: "ios-1", personID: "4"))
        XCTAssertEqual(
            "/api/videos/recent?device_id=ios-1&person_id=4",
            VideoPath.recent(deviceID: "ios-1", personID: "4")
        )
    }

    // MARK: - the resume save's body

    func testTheSaveBodyIsTheWholeRowAndValidJson() throws {
        let row = VideoRow(libraryID: "core:videos", rel: "a\\b.mp4", name: "b.mp4")

        let body = VideoPath.saveBody(row, deviceID: "ios-1", positionSec: 42, durationSec: 2910)

        XCTAssertEqual(
            #"{"library_id":"core:videos","path":"a\\b.mp4","device_id":"ios-1","position_sec":42,"duration_sec":2910,"title":"b.mp4"}"#,
            String(decoding: body, as: UTF8.self)
        )
        // Hand-written JSON, so it is read back by a real parser rather than
        // only compared to a string this test also wrote.
        let parsed = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: body) as? [String: Any]
        )
        XCTAssertEqual("a\\b.mp4", parsed["path"] as? String)
        XCTAssertEqual(42, parsed["position_sec"] as? Int)
        XCTAssertNil(parsed["person_id"])
    }

    /// A duration of nil or zero is **left out**, not sent as zero: the
    /// server would take the zero and overwrite a good length with it.
    func testADurationTheyPlayerDoesNotKnowIsLeftOut() {
        let row = VideoRow(libraryID: "aa", rel: "a.mp4", name: "a.mp4")

        let unknown = String(decoding: VideoPath.saveBody(row, deviceID: "d", positionSec: 1, durationSec: nil), as: UTF8.self)
        let zero = String(decoding: VideoPath.saveBody(row, deviceID: "d", positionSec: 1, durationSec: 0), as: UTF8.self)

        XCTAssertFalse(unknown.contains("duration_sec"))
        XCTAssertFalse(zero.contains("duration_sec"))
    }

    /// A negative position means the player reported one. Sending it is how a
    /// resume row ends up unusable.
    func testANegativePositionIsClampedAtZero() {
        let row = VideoRow(libraryID: "aa", rel: "a.mp4", name: "a.mp4")

        let body = String(decoding: VideoPath.saveBody(row, deviceID: "d", positionSec: -5, durationSec: nil), as: UTF8.self)

        XCTAssertTrue(body.contains(#""position_sec":0"#), body)
    }

    /// The server types `person_id` as an integer, so one that is not a
    /// number is left out rather than sent as a string the row would refuse.
    func testThePersonIsSentOnlyWhenItIsANumber() {
        let row = VideoRow(libraryID: "aa", rel: "a.mp4", name: "a.mp4")

        func body(_ person: String?) -> String {
            String(
                decoding: VideoPath.saveBody(
                    row, deviceID: "d", personID: person, positionSec: 1, durationSec: nil
                ),
                as: UTF8.self
            )
        }

        XCTAssertTrue(body("4").contains(#""person_id":4"#))
        XCTAssertFalse(body("jordan").contains("person_id"))
        XCTAssertFalse(body("").contains("person_id"))
        XCTAssertFalse(body(nil).contains("person_id"))
    }

    func testTheSavedTitleIsWhatThePersonSawItCalled() {
        let row = VideoRow(libraryID: "aa", rel: "a.mp4", name: "a.mp4", title: "Holiday 2026")

        let body = String(decoding: VideoPath.saveBody(row, deviceID: "d", positionSec: 1, durationSec: nil), as: UTF8.self)

        XCTAssertTrue(body.contains(#""title":"Holiday 2026""#), body)
    }

    /// A filename can hold a quote, a newline or a control character, and a
    /// Windows path holds backslashes by definition.
    func testAQuotedStringEscapesWhatWouldBreakTheBody() throws {
        XCTAssertEqual(#""plain""#, VideoPath.quoted("plain"))
        XCTAssertEqual(#""a\"b""#, VideoPath.quoted("a\"b"))
        XCTAssertEqual(#""a\\b""#, VideoPath.quoted("a\\b"))
        XCTAssertEqual(#""a\nb""#, VideoPath.quoted("a\nb"))
        XCTAssertEqual(#""a\rb""#, VideoPath.quoted("a\rb"))
        XCTAssertEqual(#""a\tb""#, VideoPath.quoted("a\tb"))
        XCTAssertEqual(#""a\u0007b""#, VideoPath.quoted("a\u{07}b"))

        // And the whole point: it parses back to what went in.
        let round = try XCTUnwrap(
            try JSONSerialization.jsonObject(
                with: Data(("{\"k\":" + VideoPath.quoted("a\"b\\c\nd\u{01}") + "}").utf8)
            ) as? [String: Any]
        )
        XCTAssertEqual("a\"b\\c\nd\u{01}", round["k"] as? String)
    }

    // MARK: - bytes

    func testFmtBytesCrossesEveryUnitBoundary() {
        XCTAssertEqual("0 B", fmtBytes(0))
        XCTAssertEqual("1023 B", fmtBytes(1023))
        XCTAssertEqual("1 KB", fmtBytes(1024))
        XCTAssertEqual("1024 KB", fmtBytes(1024 * 1024 - 1))
        XCTAssertEqual("1.0 MB", fmtBytes(1024 * 1024))
        XCTAssertEqual("1.00 GB", fmtBytes(1024 * 1024 * 1024))
        XCTAssertEqual("700.0 MB", fmtBytes(734_003_200))
    }

    /// An em dash for nil **and for negative**, which is a deliberate
    /// divergence: Android's `fmtBytes` would print `-3 B`.
    func testFmtBytesWillNotPrintANegativeSize() {
        XCTAssertEqual("—", fmtBytes(nil))
        XCTAssertEqual("—", fmtBytes(-3))
        XCTAssertEqual("—", fmtBytes(Int64.min))
    }
}
