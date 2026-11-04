import Foundation
import XCTest
@testable import DomovoiKit

/// The swipe feed's pure rules, and the details sheet's rows.
///
/// These are the cases `android/.../FeedModelsTest.kt` covers — the only part
/// of Videos that has a test over there — plus the ones it does not: a width
/// of zero, a duration that is not known, a resume exactly on the floor, and
/// the two formatters.
final class VideoFeedRulesTests: XCTestCase {

    // MARK: - where a page starts

    /// Only the video the viewer tapped resumes, and only once: swiping away
    /// and back starts it from the beginning, which is the owner's decision
    /// of 2026-10-08.
    func testOnlyTheTappedPageResumesAndOnlyTheFirstTime() {
        XCTAssertEqual(
            90_000,
            FeedRules.startPositionMs(page: 4, startIndex: 4, resumeSec: 90, resumeUsed: false)
        )
        // Another page, same resume: from the beginning.
        XCTAssertEqual(
            0,
            FeedRules.startPositionMs(page: 5, startIndex: 4, resumeSec: 90, resumeUsed: false)
        )
        // The tapped page, come back to: from the beginning.
        XCTAssertEqual(
            0,
            FeedRules.startPositionMs(page: 4, startIndex: 4, resumeSec: 90, resumeUsed: true)
        )
    }

    /// A resume of five seconds or less never really started. The boundary is
    /// `> 5`, not `>= 5`, and both sides of it are asserted because an
    /// off-by-one here is a video that jumps four seconds in for no reason.
    func testAResumeOnTheFloorStartsFromTheBeginning() {
        func start(_ seconds: Int64) -> Int64 {
            FeedRules.startPositionMs(page: 0, startIndex: 0, resumeSec: seconds, resumeUsed: false)
        }

        XCTAssertEqual(0, start(0))
        XCTAssertEqual(0, start(5))
        XCTAssertEqual(6000, start(6))
        XCTAssertEqual(0, start(-10))
    }

    // MARK: - dropping a video from the queue

    func testARemovalBeforeTheCurrentPageShiftsItLeft() {
        XCTAssertEqual(2, FeedRules.indexAfterRemoval(current: 3, removed: 0, newSize: 9))
        XCTAssertEqual(2, FeedRules.indexAfterRemoval(current: 3, removed: 2, newSize: 9))
    }

    func testARemovalAfterTheCurrentPageLeavesItAlone() {
        XCTAssertEqual(3, FeedRules.indexAfterRemoval(current: 3, removed: 4, newSize: 9))
    }

    /// Removing the video being watched lands on the one that slid into its
    /// place — or on the new last one, when it was the last.
    func testRemovingTheCurrentPageLandsOnWhatSlidIntoIt() {
        XCTAssertEqual(3, FeedRules.indexAfterRemoval(current: 3, removed: 3, newSize: 9))
        XCTAssertEqual(2, FeedRules.indexAfterRemoval(current: 3, removed: 3, newSize: 3))
    }

    func testAnEmptiedQueueIsPageZeroRatherThanMinusOne() {
        XCTAssertEqual(0, FeedRules.indexAfterRemoval(current: 0, removed: 0, newSize: 0))
    }

    // MARK: - the screen timeout

    /// While it means to play — a stall to buffer included — the screen stays
    /// awake. Paused, failed or idle, it times out as usual.
    func testTheScreenIsHeldOnlyWhileItMeansToPlay() {
        XCTAssertTrue(FeedRules.keepsScreenOn(wantsToPlay: true, state: .ready, failed: false))
        XCTAssertTrue(FeedRules.keepsScreenOn(wantsToPlay: true, state: .buffering, failed: false))

        XCTAssertFalse(FeedRules.keepsScreenOn(wantsToPlay: false, state: .ready, failed: false))
        XCTAssertFalse(FeedRules.keepsScreenOn(wantsToPlay: true, state: .idle, failed: false))
        XCTAssertFalse(FeedRules.keepsScreenOn(wantsToPlay: true, state: .ended, failed: false))
        // A video that will not decode must not hold the screen awake
        // forever on a phone in somebody's pocket.
        XCTAssertFalse(FeedRules.keepsScreenOn(wantsToPlay: true, state: .ready, failed: true))
    }

    // MARK: - seeking

    func testASeekIsClampedToTheVideo() {
        XCTAssertEqual(0, FeedRules.seekTarget(-5000, durationMs: 60_000))
        XCTAssertEqual(30_000, FeedRules.seekTarget(30_000, durationMs: 60_000))
        XCTAssertEqual(60_000, FeedRules.seekTarget(90_000, durationMs: 60_000))
    }

    /// A stream whose length is not known yet clamps at zero only — the
    /// alternative is clamping every seek to zero, which would pin a live
    /// stream's scrubber to the start.
    func testASeekInAVideoOfUnknownLengthIsClampedAtZeroOnly() {
        XCTAssertEqual(90_000, FeedRules.seekTarget(90_000, durationMs: nil))
        XCTAssertEqual(90_000, FeedRules.seekTarget(90_000, durationMs: 0))
        XCTAssertEqual(0, FeedRules.seekTarget(-1, durationMs: nil))
    }

    // MARK: - asking for another page

    func testThePageAskForMoreThreeFromTheEnd() {
        XCTAssertFalse(FeedRules.wantsAnotherPage(current: 5, loaded: 10))
        XCTAssertFalse(FeedRules.wantsAnotherPage(current: 6, loaded: 10))
        XCTAssertTrue(FeedRules.wantsAnotherPage(current: 7, loaded: 10))
        XCTAssertTrue(FeedRules.wantsAnotherPage(current: 9, loaded: 10))
    }

    /// An empty queue asks for nothing. That is the state a feed is in for
    /// one frame after the last video is removed, and `current >= -3` is true
    /// of every page there is.
    func testAnEmptyQueueAsksForNothing() {
        XCTAssertFalse(FeedRules.wantsAnotherPage(current: 0, loaded: 0))
    }

    // MARK: - where a double tap landed

    func testTheEdgeThirdsSkipAndTheMiddleDoesNot() {
        XCTAssertEqual(.back, FeedRules.doubleTapZone(x: 10, width: 390))
        XCTAssertEqual(.middle, FeedRules.doubleTapZone(x: 195, width: 390))
        XCTAssertEqual(.forward, FeedRules.doubleTapZone(x: 380, width: 390))
    }

    /// The boundaries belong to the middle, which is the safer side: a tap
    /// one point inside the edge third skipping is fine, a tap in the centre
    /// skipping is not.
    func testTheThirdsBoundariesBelongToTheMiddle() {
        XCTAssertEqual(.middle, FeedRules.doubleTapZone(x: 130, width: 390))
        XCTAssertEqual(.middle, FeedRules.doubleTapZone(x: 260, width: 390))
        XCTAssertEqual(.back, FeedRules.doubleTapZone(x: 129.9, width: 390))
        XCTAssertEqual(.forward, FeedRules.doubleTapZone(x: 260.1, width: 390))
    }

    /// A width of zero is what a view reports mid-layout. Skipping on a tap
    /// whose position means nothing is the worse guess.
    func testATapInAViewOfNoWidthReadsAsTheMiddle() {
        XCTAssertEqual(.middle, FeedRules.doubleTapZone(x: 0, width: 0))
        XCTAssertEqual(.middle, FeedRules.doubleTapZone(x: 50, width: 0))
        XCTAssertEqual(.middle, FeedRules.doubleTapZone(x: 50, width: -390))
    }

    // MARK: - leaving the app

    /// The thing that goes wrong is subtle: the first notification after
    /// subscribing is the arrival, and a resume that trusted it would start a
    /// video nobody asked for.
    func testComingBackResumesOnlyWhatWasPlayingAndOnlyOnce() {
        let pause = BackgroundPause()

        // Arriving before anything left: nothing to resume.
        XCTAssertFalse(pause.onReturn())

        XCTAssertTrue(pause.onLeave(playing: true))
        XCTAssertTrue(pause.onReturn())
        // Spent. A second return — a notification that fires twice — must not
        // start it again.
        XCTAssertFalse(pause.onReturn())
    }

    func testLeavingPausedMeansComingBackStaysPaused() {
        let pause = BackgroundPause()

        XCTAssertFalse(pause.onLeave(playing: false))
        XCTAssertFalse(pause.onReturn())
    }

    /// Leave playing, come back, leave paused: the answer is the **last**
    /// leave, not a flag that stuck on.
    func testTheAnswerIsTheLastLeave() {
        let pause = BackgroundPause()

        pause.onLeave(playing: true)
        XCTAssertTrue(pause.onReturn())
        pause.onLeave(playing: false)
        XCTAssertFalse(pause.onReturn())
    }

    // MARK: - a library row becomes a feed entry

    func testAFeedEntryCarriesAPathAndNeverAUrl() {
        let row = VideoRow(
            libraryID: "core:videos",
            libraryLabel: "Movies",
            rel: "a b.mp4",
            name: "a b.mp4",
            size: 1024,
            mtime: 1_762_000_000
        )

        let video = FeedVideo(row)

        XCTAssertEqual("core:videos:a b.mp4", video.key)
        XCTAssertEqual("a b.mp4", video.title)
        XCTAssertEqual(VideoPath.stream(row), video.streamPath)
        XCTAssertEqual(VideoPath.poster(row), video.posterPath)
        XCTAssertEqual(1024, video.sizeBytes)
        XCTAssertEqual(1_762_000_000, video.modifiedEpochSec)
        XCTAssertEqual("Movies/a b.mp4", video.location)
        // The path is relative and holds no token. Both halves matter: a
        // stored absolute URL would go stale when the phone changes house,
        // and a stored token-bearing one would leak the token into whatever
        // held it.
        XCTAssertTrue(video.streamPath.hasPrefix("/api/videos/stream?"))
        XCTAssertFalse(video.streamPath.contains("device_token"))
        XCTAssertFalse(video.streamPath.contains("http"))
    }

    /// A server video is never shareable, because sharing its URL would share
    /// the household token.
    func testAServerVideoIsNotShareable() {
        XCTAssertFalse(FeedVideo(VideoRow(libraryID: "aa", rel: "a.mp4")).shareable)
    }

    // MARK: - the details sheet

    private let video = FeedVideo(
        key: "aa:a.mp4",
        title: "a.mp4",
        streamPath: "/api/videos/stream?library_id=aa&path=a.mp4",
        sizeBytes: 734_003_200,
        modifiedEpochSec: 1_762_000_000,
        location: "Movies/a.mp4"
    )

    private func rows(_ facts: FeedStreamFacts?) -> [String: String] {
        var out: [String: String] = [:]
        for row in video.detailRows(facts: facts, timeZone: TimeZone(identifier: "UTC") ?? .current) {
            out[row.label] = row.value
        }
        return out
    }

    /// Size, duration and resolution are drawn **always**, saying `unknown`
    /// when they are not known: a video with no duration is worth saying out
    /// loud. The rest are left out.
    func testTheThreeAlwaysDrawnRowsSayUnknownRatherThanVanishing() {
        let said = rows(nil)

        XCTAssertEqual("700.0 MB", said[VideoStrings.metaSize])
        XCTAssertEqual(VideoStrings.unknown, said[VideoStrings.metaDuration])
        XCTAssertEqual(VideoStrings.unknown, said[VideoStrings.metaResolution])
        XCTAssertNil(said[VideoStrings.metaBitrate])
        XCTAssertNil(said[VideoStrings.metaFrameRate])
        XCTAssertNil(said[VideoStrings.metaCodec])
    }

    func testAMeasuredStreamFillsEveryRow() {
        let said = rows(
            FeedStreamFacts(
                durationSec: 2910,
                width: 1920,
                height: 1080,
                bitsPerSecond: 2_048_000,
                frameRate: 29.97,
                codec: "avc1"
            )
        )

        XCTAssertEqual("48:30", said[VideoStrings.metaDuration])
        XCTAssertEqual("1920 × 1080", said[VideoStrings.metaResolution])
        XCTAssertEqual("2048 kbps", said[VideoStrings.metaBitrate])
        XCTAssertEqual("30 fps", said[VideoStrings.metaFrameRate])
        XCTAssertEqual("avc1", said[VideoStrings.metaCodec])
        XCTAssertEqual("Movies/a.mp4", said[VideoStrings.metaLocation])
        XCTAssertEqual("2025-11-01 12:26", said[VideoStrings.metaModified])
    }

    /// A half-measured stream — one dimension and no other — says `unknown`
    /// rather than `1920 × 0`.
    func testHalfAResolutionIsUnknown() {
        XCTAssertEqual(
            VideoStrings.unknown,
            rows(FeedStreamFacts(width: 1920, height: 0))[VideoStrings.metaResolution]
        )
        XCTAssertEqual(
            VideoStrings.unknown,
            rows(FeedStreamFacts(width: 0, height: 1080))[VideoStrings.metaResolution]
        )
    }

    func testAZeroBitrateOrFrameRateIsLeftOutRatherThanDrawnAsZero() {
        let said = rows(FeedStreamFacts(bitsPerSecond: 0, frameRate: 0, codec: ""))

        XCTAssertNil(said[VideoStrings.metaBitrate])
        XCTAssertNil(said[VideoStrings.metaFrameRate])
        XCTAssertNil(said[VideoStrings.metaCodec])
    }

    /// A video with no `mtime` has no `modified` row at all — unlike the
    /// three above, a date nobody knows is not worth a line.
    func testAVideoWithNoModifiedTimeHasNoSuchRow() {
        let bare = FeedVideo(key: "k", title: "t", streamPath: "/p")

        let labels = bare.detailRows().map(\.label)

        XCTAssertFalse(labels.contains(VideoStrings.metaModified))
        XCTAssertFalse(labels.contains(VideoStrings.metaLocation))
        XCTAssertEqual(
            [VideoStrings.metaSize, VideoStrings.metaDuration, VideoStrings.metaResolution],
            labels
        )
    }

    /// Each row's label is its id, so two rows with the same label would
    /// collapse in a `ForEach`. Every label drawn must be distinct.
    func testEveryDetailRowHasItsOwnId() {
        let ids = video.detailRows(
            facts: FeedStreamFacts(
                durationSec: 10, width: 4, height: 2, bitsPerSecond: 1, frameRate: 1, codec: "x"
            )
        ).map(\.id)

        XCTAssertEqual(ids.count, Set(ids).count, "\(ids)")
    }

    // MARK: - the date

    /// The zone is the caller's, so a test can state one — and the locale is
    /// POSIX, so a phone set to Hindi does not render these digits in
    /// Devanagari numerals beside a monospaced size in Western ones.
    func testTheDateIsInTheZoneItWasGiven() throws {
        let utc = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let tokyo = try XCTUnwrap(TimeZone(identifier: "Asia/Tokyo"))

        XCTAssertEqual("2025-11-01 12:26", fmtEpochDate(1_762_000_000, timeZone: utc))
        XCTAssertEqual("2025-11-01 21:26", fmtEpochDate(1_762_000_000, timeZone: tokyo))
        XCTAssertEqual("1970-01-01 00:00", fmtEpochDate(0, timeZone: utc))
    }

    /// `Date(timeIntervalSince1970:)` with an infinity traps inside
    /// `DateFormatter`, so the guard is not decoration — it is the same trap
    /// `fmtBigDur` had, which took a whole test run with it.
    func testANonFiniteDateIsAnEmDashRatherThanACrash() {
        XCTAssertEqual("—", fmtEpochDate(.infinity))
        XCTAssertEqual("—", fmtEpochDate(-.infinity))
        XCTAssertEqual("—", fmtEpochDate(.nan))
    }

    // MARK: - the numbers the rules are made of

    /// The constants, pinned against the Kotlin. Not a tautology: each of
    /// these is a number Android chose and a reader here would otherwise have
    /// to take on trust.
    func testTheRulesNumbersMatchTheKotlin() {
        XCTAssertEqual(3, FeedRules.prefetch)
        XCTAssertEqual(15_000, FeedRules.seekStepMs)
        XCTAssertEqual(5000, FeedRules.saveEveryMs)
        XCTAssertEqual(5, FeedRules.resumeFloorSec)
    }
}
