import Foundation
import XCTest
@testable import DomovoiKit

/// The swipe feed's decisions: what a settle tells the player to do, what a
/// resume save is spent on, and what dropping a video from the session queue
/// leaves behind.
///
/// Nothing on Android tests this: `VideoFeed.kt` keeps the same state in
/// composition locals and effects, so "a swipe that bounces back must not
/// rebuffer" is specified by nothing over there. It is specified here.
@MainActor
final class VideoFeedModelTests: XCTestCase {

    // MARK: - the rig

    /// Every resume save the model asked for, in order.
    private struct Save: Equatable {
        let key: String
        let positionSec: Int64
        let durationSec: Int64?
        let ended: Bool
    }

    private final class Log {
        var saves: [Save] = []
        var loadMoreCalls = 0
    }

    private func videos(_ count: Int) -> [FeedVideo] {
        (0..<count).map { index in
            FeedVideo(
                key: "aa:v\(index).mp4",
                title: "v\(index).mp4",
                streamPath: "/api/videos/stream?library_id=aa&path=v\(index).mp4",
                sizeBytes: Int64(index) * 1024,
                location: "Movies/v\(index).mp4"
            )
        }
    }

    private func rig(
        count: Int = 10,
        startIndex: Int = 0,
        resumeSec: Int64 = 0,
        wantsMore: Bool = true
    ) -> (VideoFeedModel, Log) {
        let log = Log()
        // Each piece named and typed, for ``VideoFeedView/build()``'s reason:
        // written as one call with `loadMore` as a ternary over two closures,
        // the type checker answers "failed to produce diagnostic" — it could
        // not even describe what it did not like. This is the same code with
        // one decision at a time.
        let save: @MainActor (FeedVideo, Int64, Int64?, Bool) -> Void = {
            video, positionSec, durationSec, ended in
            log.saves.append(
                Save(key: video.key, positionSec: positionSec, durationSec: durationSec, ended: ended)
            )
        }
        var more: (@MainActor () -> Void)?
        if wantsMore {
            more = { log.loadMoreCalls += 1 }
        }
        let model = VideoFeedModel(
            videos: videos(count),
            startIndex: startIndex,
            resumeSec: resumeSec,
            persist: save,
            loadMore: more
        )
        return (model, log)
    }

    private func loadedKey(_ action: FeedAction) -> String? {
        guard case .load(let video, _) = action else { return nil }
        return video.key
    }

    private func startMs(_ action: FeedAction) -> Int64? {
        guard case .load(_, let startMs) = action else { return nil }
        return startMs
    }

    // MARK: - what it opens on

    func testItOpensOnThePageTheViewerTapped() {
        let (model, _) = rig(count: 10, startIndex: 4)

        XCTAssertEqual(4, model.current)
        XCTAssertEqual("v4.mp4", model.title)
        XCTAssertEqual("5 / 10", model.counter)
        XCTAssertFalse(model.isEmpty)
    }

    /// A start index past the end is a list that changed under the tap. It is
    /// clamped rather than trapped.
    func testAStartIndexPastTheEndIsClamped() {
        let (far, _) = rig(count: 3, startIndex: 99)
        let (negative, _) = rig(count: 3, startIndex: -4)

        XCTAssertEqual(2, far.current)
        XCTAssertEqual(0, negative.current)
    }

    /// An empty queue has no page at all, and asking for its page is `nil`
    /// rather than a trap — the state a feed is in for one frame after the
    /// last video is dropped.
    func testAnEmptyQueueShowsNothing() {
        let (model, _) = rig(count: 0)

        XCTAssertTrue(model.isEmpty)
        XCTAssertNil(model.showing)
        XCTAssertEqual("", model.title)
        XCTAssertNil(model.closingKey)
        XCTAssertEqual("0 / 0", model.counter)
    }

    // MARK: - the pager settled

    func testTheFirstSettleLoadsTheVideoAtItsResume() throws {
        let (model, log) = rig(count: 10, startIndex: 4, resumeSec: 90)

        let action = model.settled(on: 4)

        XCTAssertEqual("aa:v4.mp4", loadedKey(action))
        XCTAssertEqual(90_000, startMs(action))
        XCTAssertEqual("aa:v4.mp4", try XCTUnwrap(model.closingKey))
        // Nothing was left, so nothing was saved.
        XCTAssertTrue(log.saves.isEmpty)
    }

    /// A swipe that bounces back to where it started answers `.play`, which
    /// is the one answer that costs no rebuffer — and it saves nothing,
    /// because nothing was left.
    func testSettlingOnThePageAlreadyHeldOnlyPlays() {
        let (model, log) = rig()

        model.settled(on: 0)
        let again = model.settled(on: 0, leavingAtSec: 30, leavingDurationSec: 100)

        XCTAssertEqual(.play, again)
        XCTAssertTrue(log.saves.isEmpty)
    }

    /// Swiping on saves where the page being **left** had got to, before the
    /// player moves.
    func testASwipeSavesTheOutgoingPageFirst() throws {
        let (model, log) = rig()

        model.settled(on: 0)
        let action = model.settled(on: 1, leavingAtSec: 42, leavingDurationSec: 2910)

        XCTAssertEqual("aa:v1.mp4", loadedKey(action))
        let save = try XCTUnwrap(log.saves.first)
        XCTAssertEqual(1, log.saves.count)
        XCTAssertEqual("aa:v0.mp4", save.key)
        XCTAssertEqual(42, save.positionSec)
        XCTAssertEqual(2910, save.durationSec)
        XCTAssertFalse(save.ended)
    }

    /// Only the tapped page resumes. The next one starts at the beginning
    /// even though the model still holds the resume it opened with.
    func testTheNextPageStartsFromTheBeginning() {
        let (model, _) = rig(count: 10, startIndex: 4, resumeSec: 90)

        model.settled(on: 4)
        let next = model.settled(on: 5)

        XCTAssertEqual(0, startMs(next))
    }

    /// Coming back to the tapped page starts it from the beginning too: the
    /// resume is spent by the first settle, whichever page that was.
    func testTheResumeIsSpentByTheFirstSettle() {
        let (model, _) = rig(count: 10, startIndex: 4, resumeSec: 90)

        model.settled(on: 4)
        model.settled(on: 5)
        let back = model.settled(on: 4)

        XCTAssertEqual(0, startMs(back))
    }

    func testASettleOffTheEndOfTheQueueDoesNothingAndMovesNothing() {
        let (model, log) = rig(count: 3)

        model.settled(on: 1)
        let action = model.settled(on: 9)

        XCTAssertEqual(.nothing, action)
        XCTAssertEqual(1, model.current)
        XCTAssertTrue(log.saves.isEmpty)
    }

    /// A page that failed, then was swiped away from and back to, is not
    /// still failed: the message belongs to the attempt, not the video.
    func testASettleClearsTheFailureAndTheRefusalOfThePageBefore() throws {
        let (model, _) = rig()

        model.settled(on: 0)
        model.failed("aa:v0.mp4")
        model.refused(.noServer)
        XCTAssertTrue(model.hasFailed(try XCTUnwrap(model.showing)))
        XCTAssertNotNil(model.refusal)

        model.settled(on: 1)

        XCTAssertFalse(model.hasFailed(try XCTUnwrap(model.showing)))
        XCTAssertNil(model.refusal)
    }

    // MARK: - asking the list behind it for more

    func testItAsksForAnotherPageOnlyNearTheEnd() {
        let (model, log) = rig(count: 10)

        model.settled(on: 5)
        XCTAssertEqual(0, log.loadMoreCalls)

        model.settled(on: 7)
        XCTAssertEqual(1, log.loadMoreCalls)
    }

    /// A feed opened from the recently-played strip has no next page, and
    /// `nil` here is what stops it asking for one.
    func testAFeedWithNothingBehindItNeverAsks() {
        let (model, log) = rig(count: 10, wantsMore: false)

        model.settled(on: 9)

        XCTAssertEqual(0, log.loadMoreCalls)
    }

    func testANewPageOfVideosJoinsTheEnd() {
        let (model, _) = rig(count: 3)

        model.grew(to: videos(5))

        XCTAssertEqual(5, model.queue.count)
        XCTAssertEqual(
            ["aa:v0.mp4", "aa:v1.mp4", "aa:v2.mp4", "aa:v3.mp4", "aa:v4.mp4"],
            model.queue.map(\.key)
        )
    }

    /// A video the viewer dropped is **not** added back when the list behind
    /// the feed reloads — which it does on any `files.changed`.
    func testAVideoTheViewerDroppedStaysDropped() {
        let (model, _) = rig(count: 3)

        model.settled(on: 0)
        model.remove("aa:v1.mp4")
        XCTAssertEqual(["aa:v0.mp4", "aa:v2.mp4"], model.queue.map(\.key))

        model.grew(to: videos(3))

        XCTAssertEqual(["aa:v0.mp4", "aa:v2.mp4"], model.queue.map(\.key))
    }

    // MARK: - what the player reported

    /// A loop saves the row at **zero** with `ended`, which is what "watched"
    /// means in the strip: the next tap starts it again rather than at the
    /// last second.
    func testALoopSavesTheRowAsWatched() throws {
        let (model, log) = rig()

        model.settled(on: 0)
        model.looped(durationSec: 2910)

        let save = try XCTUnwrap(log.saves.first)
        XCTAssertEqual("aa:v0.mp4", save.key)
        XCTAssertEqual(0, save.positionSec)
        XCTAssertEqual(2910, save.durationSec)
        XCTAssertTrue(save.ended)
    }

    func testTheFiveSecondTickSavesWhereItIs() throws {
        let (model, log) = rig()

        model.settled(on: 0)
        model.tick(positionSec: 35, durationSec: nil)

        let save = try XCTUnwrap(log.saves.first)
        XCTAssertEqual(35, save.positionSec)
        XCTAssertNil(save.durationSec)
        XCTAssertFalse(save.ended)
    }

    /// Nothing loaded, nothing to save. A tick from a stray timer after the
    /// queue emptied would otherwise write a row for a video that is gone.
    func testATickWithNothingLoadedSavesNothing() {
        let (model, log) = rig()

        model.tick(positionSec: 35, durationSec: 100)
        model.looped(durationSec: 100)

        XCTAssertTrue(log.saves.isEmpty)
    }

    /// The refusal is the whole sentence and the second line is empty — there
    /// is nothing true to put there.
    func testARefusalIsTheReasonAndNothingElse() throws {
        let (model, _) = rig()

        model.refused(.notPermitted(VideoStrings.pairToPlay))
        var refusal = try XCTUnwrap(model.refusal)
        XCTAssertEqual(VideoStrings.pairToPlay, refusal.title)
        XCTAssertEqual("", refusal.detail)

        model.refused(.unreachable("no route to 10.0.0.42"))
        refusal = try XCTUnwrap(model.refusal)
        XCTAssertEqual(
            VideoStrings.cannotPlayYet(DomovoiError.unreachable("no route to 10.0.0.42").message),
            refusal.title
        )
        XCTAssertEqual("", refusal.detail)
    }

    /// The failure is pinned to **one** key, so a video that will not decode
    /// does not put the message over the next one.
    func testOnlyTheVideoThatFailedIsMarkedFailed() throws {
        let (model, _) = rig()

        model.failed("aa:v0.mp4")

        let first = try XCTUnwrap(model.queue.first)
        let second = try XCTUnwrap(model.queue.dropFirst().first)
        XCTAssertTrue(model.hasFailed(first))
        XCTAssertFalse(model.hasFailed(second))
    }

    // MARK: - taps and drags

    func testADoubleTapAtTheEdgesSkipsAndFlashes() throws {
        let (model, _) = rig()

        let forward = model.doubleTapped(x: 380, width: 390, positionMs: 30_000, durationMs: 60_000)
        XCTAssertEqual(45_000, forward)
        var flash = try XCTUnwrap(model.seekFlash)
        XCTAssertTrue(flash.forward)
        XCTAssertEqual(1, flash.count)

        let back = model.doubleTapped(x: 10, width: 390, positionMs: 30_000, durationMs: 60_000)
        XCTAssertEqual(15_000, back)
        flash = try XCTUnwrap(model.seekFlash)
        XCTAssertFalse(flash.forward)
        // The count is what makes a second tap in the same direction restart
        // the indicator's fade instead of leaving it mid-way through the
        // first one's.
        XCTAssertEqual(2, flash.count)
    }

    func testADoubleTapInTheMiddleSeeksNowhereAndDoesNotFlash() {
        let (model, _) = rig()

        let target = model.doubleTapped(x: 195, width: 390, positionMs: 30_000, durationMs: 60_000)

        XCTAssertNil(target)
        XCTAssertNil(model.seekFlash)
    }

    func testASkipIsClampedToTheVideo() {
        let (model, _) = rig()

        XCTAssertEqual(0, model.doubleTapped(x: 10, width: 390, positionMs: 3000, durationMs: 60_000))
        XCTAssertEqual(
            60_000,
            model.doubleTapped(x: 380, width: 390, positionMs: 58_000, durationMs: 60_000)
        )
    }

    func testTheFlashFades() {
        let (model, _) = rig()

        _ = model.doubleTapped(x: 380, width: 390, positionMs: 0, durationMs: 60_000)
        model.flashFaded()

        XCTAssertNil(model.seekFlash)
    }

    func testADragAlongTheScrubberIsAFractionOfTheLength() {
        let (model, _) = rig()

        XCTAssertEqual(30_000, model.scrubbed(to: 0.5, durationMs: 60_000))
        XCTAssertEqual(0, model.scrubbed(to: -0.2, durationMs: 60_000))
        XCTAssertEqual(60_000, model.scrubbed(to: 1.4, durationMs: 60_000))
    }

    /// A video of unknown length has no scrubber to drag: a fraction of a
    /// length nobody knows is not a position.
    func testThereIsNothingToDragInAVideoOfUnknownLength() {
        let (model, _) = rig()

        XCTAssertNil(model.scrubbed(to: 0.5, durationMs: nil))
        XCTAssertNil(model.scrubbed(to: 0.5, durationMs: 0))
    }

    // MARK: - the sheets

    func testTheSheetsOpenAndClose() {
        let (model, _) = rig()

        model.open(.options)
        XCTAssertEqual(.options, model.sheet)
        model.open(.playlist)
        XCTAssertEqual(.playlist, model.sheet)
        model.closeSheet()
        XCTAssertNil(model.sheet)
    }

    /// Picking in the playlist sheet moves the **pager**, which then settles
    /// and drives the player. A model that loaded the video itself would
    /// leave the pager showing a different page from the one playing.
    func testPickingInThePlaylistMovesThePageAndClosesTheSheet() {
        let (model, log) = rig()

        model.settled(on: 0)
        model.open(.playlist)
        let picked = model.picked(7)

        XCTAssertEqual(7, picked)
        XCTAssertEqual(7, model.current)
        XCTAssertNil(model.sheet)
        // It did not load anything, so it did not save the page it left
        // either: the settle that follows does both.
        XCTAssertTrue(log.saves.isEmpty)
        XCTAssertEqual("aa:v7.mp4", model.closingKey)
    }

    func testPickingAVideoThatIsNotThereDoesNothing() {
        let (model, _) = rig(count: 3)

        model.open(.playlist)

        XCTAssertNil(model.picked(9))
        XCTAssertNil(model.picked(-1))
        XCTAssertEqual(0, model.current)
        // The sheet stays up, because nothing was picked.
        XCTAssertEqual(.playlist, model.sheet)
    }

    // MARK: - dropping a video

    func testDroppingAVideoThatIsNotThereIsNoChange() {
        let (model, _) = rig(count: 3)

        XCTAssertEqual(.notThere, model.remove("aa:gone.mp4"))
        XCTAssertEqual(3, model.queue.count)
    }

    /// Dropping one the viewer is not watching leaves the player alone.
    func testDroppingAnotherVideoLeavesThePlayerAlone() {
        let (model, log) = rig(count: 3)

        model.settled(on: 0)
        let outcome = model.remove("aa:v2.mp4", atSec: 30, durationSec: 100)

        XCTAssertEqual(.stillThere(.nothing), outcome)
        XCTAssertEqual(0, model.current)
        XCTAssertEqual("aa:v0.mp4", model.closingKey)
        XCTAssertTrue(log.saves.isEmpty)
    }

    func testDroppingAVideoBeforeTheCurrentOneShiftsThePageWithIt() {
        let (model, _) = rig(count: 5)

        model.settled(on: 3)
        let outcome = model.remove("aa:v0.mp4")

        XCTAssertEqual(.stillThere(.nothing), outcome)
        XCTAssertEqual(2, model.current)
        // The same video is still showing — which is the whole point of
        // shifting the index.
        XCTAssertEqual("aa:v3.mp4", model.closingKey)
    }

    /// **The subtle one.** Dropping the video being watched must tell the
    /// player to load the one that slid into its place. `loaded` is cleared
    /// before the re-settle, or the settle would see the removed video as
    /// still held and answer `.play` — to a player holding nothing.
    func testDroppingTheVideoBeingWatchedLoadsTheNextOne() throws {
        let (model, log) = rig(count: 5)

        model.settled(on: 2)
        let outcome = model.remove("aa:v2.mp4", atSec: 30, durationSec: 100)

        guard case .stillThere(let action) = outcome else {
            return XCTFail("expected the queue to still have videos in it, got \(outcome)")
        }
        XCTAssertEqual("aa:v3.mp4", loadedKey(action))
        XCTAssertEqual(0, startMs(action))
        XCTAssertEqual(2, model.current)
        XCTAssertEqual("aa:v3.mp4", model.closingKey)
        // The removed video's own resume row is **not** written: the viewer
        // dropped it, and `settled` has nothing outgoing to save because
        // `loaded` was cleared first.
        XCTAssertTrue(log.saves.isEmpty, "\(log.saves)")
    }

    /// Dropping the last video lands on the new last one.
    func testDroppingTheLastVideoLandsOnTheNewLast() throws {
        let (model, _) = rig(count: 3)

        model.settled(on: 2)
        let outcome = model.remove("aa:v2.mp4")

        guard case .stillThere(let action) = outcome else {
            return XCTFail("expected the queue to still have videos in it, got \(outcome)")
        }
        XCTAssertEqual("aa:v1.mp4", loadedKey(action))
        XCTAssertEqual(1, model.current)
    }

    /// Dropping the only video empties the feed: the sheet goes with it, and
    /// there is no key to centre the grid on, because the viewer ended on
    /// nothing.
    func testDroppingTheOnlyVideoEmptiesTheFeed() {
        let (model, _) = rig(count: 1)

        model.settled(on: 0)
        model.open(.playlist)
        let outcome = model.remove("aa:v0.mp4")

        XCTAssertEqual(.emptied, outcome)
        XCTAssertTrue(model.isEmpty)
        XCTAssertNil(model.sheet)
        XCTAssertNil(model.closingKey)
    }

    /// An emptied feed asks for no further page, even though every page index
    /// is within three of a queue of nothing.
    func testAnEmptiedFeedAsksForNoFurtherPage() {
        let (model, log) = rig(count: 1)

        model.settled(on: 0)
        let before = log.loadMoreCalls
        model.remove("aa:v0.mp4")

        XCTAssertEqual(before, log.loadMoreCalls)
    }

    // MARK: - the counter

    /// The counter is one-based and never reads past the end — a queue that
    /// shrank under a page would otherwise say "4 / 3".
    func testTheCounterNeverReadsPastTheEnd() {
        let (model, _) = rig(count: 3)

        model.settled(on: 2)
        XCTAssertEqual("3 / 3", model.counter)

        model.remove("aa:v0.mp4")
        XCTAssertEqual("2 / 2", model.counter)
    }
}
