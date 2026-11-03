import XCTest
@testable import DomovoiKit

/// The queue and the transport, with the engine recording what it was handed.
///
/// `PlayerController` has no JVM test on Android — it holds an `ExoPlayer`. The
/// seam (``PlayerAudioEngine``) is what makes these cases possible at all, and
/// what they are really for is the thing neither the model nor the engine can
/// show alone: that the two stayed **in step** through a move, a remove and a
/// jump, each of which `AVQueuePlayer` cannot do and this port rebuilds.
@MainActor
final class PlayerModelQueueTests: XCTestCase {

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        PlayerHouse().install()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        super.tearDown()
    }

    private let three = [PlayerSeed.transmission, PlayerSeed.atmosphere, PlayerSeed.decades]

    /// What the engine should be holding for a model at `index`.
    private func expected(_ items: [PlayerItem], from index: Int) -> [String] {
        items[index...].map { "http://10.0.0.42:6369" + $0.src }
    }

    // MARK: - play here

    func testPlayHandsTheEngineTheQueueFromThePlayHeadOn() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 1)
        XCTAssertEqual(h.model.queue.count, 3)
        XCTAssertEqual(h.model.index, 1)
        XCTAssertEqual(h.engine.lastSetItems, expected(three, from: 1))
        XCTAssertEqual(h.engine.playCount, 1)
        XCTAssertTrue(h.model.isPlaying)
    }

    func testPlayActivatesTheSessionOnTheFirstPlayAndNotBefore() {
        let h = PlayerHarness()
        XCTAssertNil(h.session.active, "an app that activates a playback session at launch stops their podcast")
        h.model.play([PlayerSeed.transmission])
        XCTAssertEqual(h.session.active, .music)
        XCTAssertEqual(h.session.activations, [.music])
    }

    func testAPodcastActivatesTheSessionInSpokenAudio() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        XCTAssertEqual(h.session.active, .spoken, "Apple's own guidance, and what makes AirPlay 2 behave")
    }

    func testAnEmptyListIsNotAPlay() {
        let h = PlayerHarness()
        XCTAssertNil(h.model.play([]))
        XCTAssertTrue(h.model.queue.isEmpty)
        XCTAssertNil(h.session.active)
    }

    func testALongListIsCutToAWindowAroundTheTappedRow() {
        let h = PlayerHarness()
        let many = (1...700).map { PlayerItem.fromTrack(id: Int64($0), title: "t\($0)") }
        h.model.play(many, startAt: 600)
        XCTAssertEqual(h.model.queue.count, 500)
        XCTAssertEqual(h.model.current?.title, "t601", "the row that was tapped")
    }

    func testPlayStartsAtAResumePositionAndASpeed() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode], resumeSec: 912, speed: 1.5)
        XCTAssertEqual(h.engine.lastOffset, 912)
        XCTAssertEqual(h.model.speed, 1.5)
        XCTAssertEqual(h.engine.lastRate, 1.5, "both rate and defaultRate, which is the engine's job")
    }

    // MARK: - a play here while casting

    func testAPlayHereWhileCastingPausesTheRoomAndDoesNotSendItTheList() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        XCTAssertEqual(h.model.target, .room("office"))

        let before = PlayerHouse.paths.count
        let left = h.model.play([PlayerSeed.episode])

        XCTAssertEqual(left, "office", "so a consumer can say paused office")
        XCTAssertEqual(h.model.target, .thisPhone)
        XCTAssertNil(h.model.roomReading, "the room is no longer watched")
        XCTAssertEqual(h.model.queue.map(\.uid), ["pod-9"])
        await h.settle(until: { PlayerHouse.paths.count > before }, "the room's pause")
        let after = Array(PlayerHouse.paths.dropFirst(before))
        XCTAssertTrue(
            after.contains("/api/music/pause/office"),
            "a room that is left is PAUSED — a person's pause holds against every automatic restart"
        )
        XCTAssertFalse(
            after.contains("/api/music/stop/office"),
            "never stopped: stop throws the room's place away and tears down its stream"
        )
        XCTAssertFalse(
            after.contains("/api/music/play-tracks"),
            "a play here is not a cast: the episode is not sent to the room"
        )
    }

    func testAJumpIsRefusedWhileCasting() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        h.engine.forget()
        h.model.jump(to: 2)
        XCTAssertTrue(
            h.engine.calls.isEmpty,
            "a jump while casting is a re-cast, never local playback under a room that is still playing"
        )
    }

    // MARK: - enqueue and play next

    func testEnqueueOnAnEmptyQueueIsAPlay() {
        let h = PlayerHarness()
        h.model.enqueue([PlayerSeed.transmission])
        XCTAssertTrue(h.model.isPlaying)
        XCTAssertEqual(h.model.queue.count, 1)
    }

    func testEnqueueAppendsAndTheEngineHoldsTheSameTail() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        h.model.enqueue([PlayerSeed.atmosphere, PlayerSeed.decades])
        XCTAssertEqual(h.model.queue.map(\.uid), ["lib-11", "lib-12", "lib-13"])
        XCTAssertEqual(h.engine.lastSetItems, expected(three, from: 0))
    }

    func testPlayNextInsertsRightAfterTheCurrentRow() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission, PlayerSeed.decades])
        h.model.playNext([PlayerSeed.atmosphere])
        XCTAssertEqual(h.model.queue.map(\.uid), ["lib-11", "lib-12", "lib-13"])
        XCTAssertEqual(h.model.index, 0, "the play head does not move")
        XCTAssertEqual(h.engine.lastSetItems, expected(three, from: 0))
    }

    // MARK: - remove and move keep the model and the engine in step

    func testRemovingARowBeforeThePlayHeadMovesTheIndexWithIt() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 2)
        h.model.remove(at: 0)
        XCTAssertEqual(h.model.index, 1)
        XCTAssertEqual(h.model.current?.uid, "lib-13", "the same song is still current")
        XCTAssertEqual(
            h.engine.lastSetItems,
            ["http://10.0.0.42:6369" + PlayerSeed.decades.src],
            "and the engine holds the same tail"
        )
    }

    func testRemovingTheCurrentRowStartsTheNextOneFromItsTop() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 1)
        h.engine.currentTimeSec = 40
        h.model.remove(at: 1)
        XCTAssertEqual(h.model.current?.uid, "lib-13")
        XCTAssertEqual(h.engine.lastOffset, 0, "a different song does not start 40 s in")
    }

    func testRemovingTheLastRowLeavesThePlayHeadInTheQueue() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 2)
        h.model.remove(at: 2)
        XCTAssertEqual(h.model.index, 1)
        XCTAssertEqual(h.model.queue.count, 2)
    }

    func testRemovingOutOfRangeChangesNothing() {
        let h = PlayerHarness()
        h.model.play(three)
        h.engine.forget()
        h.model.remove(at: 9)
        h.model.remove(at: -1)
        XCTAssertEqual(h.model.queue.count, 3)
        XCTAssertTrue(h.engine.calls.isEmpty)
    }

    func testMovingTheCurrentRowTakesThePlayHeadWithIt() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 0)
        h.model.move(from: 0, to: 2)
        XCTAssertEqual(h.model.queue.map(\.uid), ["lib-12", "lib-13", "lib-11"])
        XCTAssertEqual(h.model.index, 2)
        XCTAssertEqual(h.model.current?.uid, "lib-11", "the song playing is still the song playing")
        XCTAssertEqual(
            h.engine.lastSetItems, ["http://10.0.0.42:6369" + PlayerSeed.transmission.src]
        )
    }

    func testMovingARowPastThePlayHeadShiftsTheIndex() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 1)
        h.model.move(from: 0, to: 2)
        XCTAssertEqual(h.model.queue.map(\.uid), ["lib-12", "lib-13", "lib-11"])
        XCTAssertEqual(h.model.current?.uid, "lib-12", "Atmosphere was playing and still is")
        XCTAssertEqual(h.model.index, 0)
    }

    func testMovingARowFromAfterThePlayHeadToBeforeItShiftsTheIndexForward() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 1)
        h.model.move(from: 2, to: 0)
        XCTAssertEqual(h.model.queue.map(\.uid), ["lib-13", "lib-11", "lib-12"])
        XCTAssertEqual(h.model.current?.uid, "lib-12")
        XCTAssertEqual(h.model.index, 2)
    }

    func testAMoveToWhereItAlreadyIsDoesNothing() {
        let h = PlayerHarness()
        h.model.play(three)
        h.engine.forget()
        h.model.move(from: 1, to: 1)
        h.model.move(from: 9, to: 0)
        XCTAssertTrue(h.engine.calls.isEmpty)
    }

    // MARK: - clear, and room mode

    func testClearFlushesThePlaceAndGivesTheSessionBack() async {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        h.engine.currentTimeSec = 300
        h.model.clear()
        XCTAssertTrue(h.model.queue.isEmpty)
        XCTAssertEqual(h.model.positionSec, 0)
        XCTAssertEqual(h.session.deactivations, 1, "another app can have the audio back")
        await h.settle(
            until: { PlayerHouse.paths.contains("/api/podcasts/positions/9") },
            "the place being flushed before the queue went"
        )
    }

    func testRoomModeLeavesTheRoomPlayingAndOnlyStopsWatchingIt() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        let before = PlayerHouse.paths.count
        h.model.detachForRoomMode()
        XCTAssertEqual(h.model.target, .thisPhone)
        XCTAssertTrue(h.model.queue.isEmpty)
        await Task.yield()
        let after = Array(PlayerHouse.paths.dropFirst(before))
        XCTAssertFalse(
            after.contains("/api/music/pause/office"),
            "the room plays on: this phone becoming a room is not a reason to silence another one"
        )
    }

    // MARK: - transport

    func testPauseAndResumeOnThisPhone() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        h.model.pause()
        XCTAssertFalse(h.model.isPlaying)
        XCTAssertEqual(h.engine.pauseCount, 1)
        h.engine.status = .readyToPlay
        h.model.resume()
        XCTAssertTrue(h.model.isPlaying)
    }

    func testResumeAfterTheEngineWentIdleSetsTheItemsAgainBeforePlaying() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        h.engine.currentTimeSec = 90
        h.model.pause()
        // The `AVPlayer` reached the end of its items, or failed: a bare
        // play() would do nothing (`resumeLocal`, `PlayerController.kt:416`).
        h.engine.status = .idle
        h.engine.forget()
        h.model.resume()
        XCTAssertTrue(
            h.engine.calls.contains { if case .setItems = $0 { return true } else { return false } },
            "the items are re-set before play"
        )
        XCTAssertEqual(h.engine.lastOffset, 90, "and from where it was, not from the top")
    }

    func testToggleFollowsWhetherThePhoneIsPlaying() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        h.model.toggle()
        XCTAssertFalse(h.model.isPlaying)
        h.engine.status = .readyToPlay
        h.model.toggle()
        XCTAssertTrue(h.model.isPlaying)
    }

    func testNextAndPreviousMoveThroughTheQueue() {
        let h = PlayerHarness()
        h.model.play(three)
        h.model.next()
        XCTAssertEqual(h.model.index, 1)
        XCTAssertEqual(h.engine.lastSetItems, expected(three, from: 1))
        h.engine.currentTimeSec = 0
        h.model.previous()
        XCTAssertEqual(h.model.index, 0, "under 3 s in, previous steps back")
    }

    func testPreviousPastThreeSecondsRestartsTheTrack() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 1)
        h.engine.currentTimeSec = 4
        h.model.tickForTests()
        h.model.previous()
        XCTAssertEqual(h.model.index, 1, "the same track")
        XCTAssertEqual(h.engine.seeks.last, 0)
    }

    func testNextAtTheEndOfTheQueueDoesNothing() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 2)
        h.engine.forget()
        h.model.next()
        XCTAssertEqual(h.model.index, 2)
        XCTAssertTrue(h.engine.calls.isEmpty)
        XCTAssertFalse(h.model.canNext, "and the button that would have done it is not live")
    }

    func testSeekingIsRefusedForALiveStation() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.station])
        XCTAssertFalse(h.model.seekable)
        h.engine.forget()
        h.model.seek(to: 30)
        XCTAssertTrue(h.engine.seeks.isEmpty, "a live stream has no place to seek to")
    }

    func testSeekingIsRefusedWhileCasting() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        XCTAssertFalse(h.model.seekable, "there is no route to seek a room")
        h.engine.forget()
        h.model.seek(to: 30)
        XCTAssertTrue(h.engine.seeks.isEmpty)
    }

    func testSeekingByAmountNeverGoesNegative() {
        let h = PlayerHarness()
        h.model.play(three)
        h.engine.currentTimeSec = 5
        h.model.tickForTests()
        h.model.seek(by: -15)
        XCTAssertEqual(h.engine.seeks.last, 0)
    }

    // MARK: - speed

    func testASpeedTheSourceCannotDoIsNotOfferedAndNotAccepted() {
        let h = PlayerHarness()
        h.engine.canPlayFastForward = false
        h.engine.canPlaySlowForward = false
        h.model.play([PlayerSeed.episode])
        XCTAssertEqual(h.model.speedOptions, [1.0], "a 2× button on a stream that cannot do 2× would lie")
        h.model.setSpeed(2.0)
        XCTAssertEqual(h.model.speed, 1.0)
    }

    func testTheSpeedOptionsComeFromTheEnginesAnswer() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        XCTAssertTrue(h.model.speedOptions.contains(2.0))
        XCTAssertTrue(h.model.speedOptions.contains(0.75))
        h.model.setSpeed(1.5)
        XCTAssertEqual(h.model.speed, 1.5)
        XCTAssertEqual(h.engine.lastRate, 1.5)
    }

    func testNoSpeedIsOfferedWhileCasting() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        XCTAssertTrue(h.model.speedOptions.isEmpty, "there is no rate route for a room")
        h.model.setSpeed(1.5)
        XCTAssertEqual(h.model.speed, 1.0)
    }

    // MARK: - chapters, and a folder book

    func testAChapterJumpSeeksToItsStart() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        XCTAssertEqual(h.model.chapters.count, 3)
        h.model.jump(toChapter: 1)
        XCTAssertEqual(h.engine.seeks.last, 600)
        XCTAssertEqual(h.model.currentChapter, 1)
        h.model.jump(toChapter: 9)
        XCTAssertEqual(h.engine.seeks.last, 600, "out of range is refused rather than clamped to the last")
    }

    func testALibraryTrackHasNoChapterList() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        XCTAssertTrue(h.model.chapters.isEmpty, "so a chapter button is not drawn (MAP §5.7)")
        XCTAssertNil(h.model.currentChapter)
    }

    func testAFolderBookPlaysOnePartAtATimeAndReportsOnePosition() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.folderBook])
        XCTAssertEqual(
            h.engine.lastSetItems,
            PlayerSeed.folderBook.parts.map { "http://10.0.0.42:6369" + $0.src },
            "the engine holds the book's parts, not the queue's tail"
        )
        XCTAssertTrue(h.model.bookTimelineIsWhole)

        // 50 s into part 1 is 150 s into the book.
        h.engine.currentTimeSec = 50
        h.model.itemFinishedForTests()  // part 0 ended, part 1 begins
        h.engine.currentTimeSec = 50
        h.model.tickForTests()
        XCTAssertEqual(h.model.positionSec, 150, "one position from the book's start")
        XCTAssertEqual(h.model.durationSec, 300)
        XCTAssertEqual(h.model.currentChapter, 1, "which is chapter \"The Long Road\"")
    }

    func testSeekingAFolderBookAcrossAPartRebuildsFromThatPart() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.folderBook])
        h.engine.forget()
        h.model.seek(to: 260)  // 10 s into part 2
        XCTAssertEqual(
            h.engine.lastSetItems,
            ["http://10.0.0.42:6369/api/audiobooks/2/audio?file=03.mp3"]
        )
        XCTAssertEqual(h.engine.lastOffset, 10)
        XCTAssertEqual(h.model.positionSec, 260)
    }

    func testSeekingInsideThePartDoesNotRebuild() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.folderBook])
        h.engine.forget()
        h.model.seek(to: 40)
        XCTAssertEqual(h.engine.seeks, [40])
        XCTAssertFalse(h.engine.calls.contains { if case .setItems = $0 { return true } else { return false } })
    }

    func testABookWithAnUnknownPartLengthGetsNoWholeBookPosition() {
        let h = PlayerHarness()
        let gappy = PlayerItem.fromBook(
            id: 5, title: "Unmeasured",
            parts: [
                PlayerMediaPart(src: "/api/audiobooks/5/audio?file=a.mp3", durationSec: 100),
                PlayerMediaPart(src: "/api/audiobooks/5/audio?file=b.mp3", durationSec: nil),
            ]
        )
        h.model.play([gappy])
        XCTAssertFalse(h.model.bookTimelineIsWhole, "so a consumer draws no whole-book scrubber (MAP §5.6)")
        h.engine.currentTimeSec = 30
        h.model.tickForTests()
        XCTAssertEqual(h.model.positionSec, 30, "the part's own position, which is all it can honestly mean")
    }

    func testTheLastPartFinishingMovesToTheNextQueueItem() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.folderBook, PlayerSeed.transmission])
        h.model.itemFinishedForTests()
        h.model.itemFinishedForTests()
        XCTAssertEqual(h.model.index, 0, "still the book, now on its third part")
        h.model.itemFinishedForTests()
        XCTAssertEqual(h.model.index, 1)
        XCTAssertEqual(h.model.current?.uid, "lib-11")
    }

    // MARK: - the lock screen

    func testTheLockScreenIsPublishedWithTheQueueAndClearedWithIt() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        XCTAssertEqual(h.nowPlaying.latest?.title, "Transmission")
        XCTAssertTrue(h.nowPlaying.enabled.contains(.togglePlayPause))
        h.model.clear()
        XCTAssertNil(h.nowPlaying.latest)
        XCTAssertTrue(h.nowPlaying.enabled.isEmpty, "nothing loaded, nothing offered")
    }

    func testTheLockScreenNeverCarriesALyricLine() {
        // By construction: `PlayerNowPlayingInfo` has no field for one
        // (`Lyrics.kt:38-42` — no log, no notification, no media session).
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        let mirror = Mirror(reflecting: h.nowPlaying.latest!)
        XCTAssertFalse(
            mirror.children.contains { ($0.label ?? "").lowercased().contains("lyric") }
        )
    }

    func testARemoteCommandDrivesTheModel() {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        h.nowPlaying.handler?(.pause, nil)
        XCTAssertFalse(h.model.isPlaying)
        h.engine.status = .readyToPlay
        h.nowPlaying.handler?(.play, nil)
        XCTAssertTrue(h.model.isPlaying)
        h.nowPlaying.handler?(.changePlaybackPosition, 77)
        XCTAssertEqual(h.engine.seeks.last, 77)
        h.engine.currentTimeSec = 77
        h.model.tickForTests()
        h.nowPlaying.handler?(.skipBackward, nil)
        XCTAssertEqual(h.engine.seeks.last, 62, "15 s back")
    }
}
