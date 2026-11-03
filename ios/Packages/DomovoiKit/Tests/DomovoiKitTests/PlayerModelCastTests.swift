import XCTest
@testable import DomovoiKit

/// Casting, from the model: what is sent, in what order, and what a second
/// pick or a play here does to one already on its way.
///
/// Every case here is a bug from 2026-10-01. `office` then `den` from this
/// phone started **both** rooms, office playing on with nothing watching or
/// pausing it; `office` then "this device" said "playing on this device" and
/// then cast to office anyway; and a cast cut off mid-POST left a room playing
/// that nothing watched.
@MainActor
final class PlayerModelCastTests: XCTestCase {

    private var house = PlayerHouse()

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        house = PlayerHouse()
        house.install()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        super.tearDown()
    }

    private let three = [PlayerSeed.transmission, PlayerSeed.atmosphere, PlayerSeed.decades]

    // MARK: - a cast that goes

    func testACastSendsTheLibraryIdsFromThePlayHeadAndTakesTheTarget() async {
        let h = PlayerHarness()
        h.model.play(three, startAt: 1)
        h.engine.currentTimeSec = 30
        h.model.tickForTests()

        let outcome = await h.model.cast(to: "office")

        XCTAssertEqual(outcome.note, "casting to office")
        XCTAssertTrue(outcome.changedTarget)
        XCTAssertEqual(h.model.target, .room("office"))
        XCTAssertTrue(PlayerHouse.paths.contains("/api/music/play-tracks"))
        XCTAssertEqual(PlayerHouse.methods(for: "/api/music/play-tracks"), ["POST"])
        XCTAssertFalse(h.model.isPlaying, "the phone is silenced once the room has taken the queue")
        XCTAssertEqual(h.engine.pauseCount, 1)
    }

    func testTheCastPlanFollowsThePhonesPosition() {
        let h = PlayerHarness()
        h.model.play(three, startAt: 1)
        h.engine.currentTimeSec = 30
        h.model.tickForTests()
        let plan = h.model.castPlan()
        XCTAssertEqual(plan.trackIDs, [12, 13])
        XCTAssertEqual(plan.startSec, 30)
    }

    func testACastThatCanSendNothingLeavesTheTargetAloneAndSaysWhy() async {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode, PlayerSeed.phoneSong])
        let before = PlayerHouse.paths.count

        let outcome = await h.model.cast(to: "office")

        XCTAssertEqual(
            outcome.note,
            "nothing from here on can play in a room: songs saved on this phone "
            + "stay on the phone, and only library songs can be cast."
        )
        XCTAssertFalse(outcome.changedTarget)
        XCTAssertEqual(
            h.model.target, .thisPhone,
            "the player never reads casting for a room that was sent nothing"
        )
        XCTAssertTrue(h.model.isPlaying, "and playback here is untouched")
        XCTAssertEqual(PlayerHouse.paths.count, before, "nothing was sent at all")
        XCTAssertEqual(h.model.trouble, outcome.note)
    }

    func testAFailedCastChangesNeitherTheTargetNorPlayback() async {
        let h = PlayerHarness()
        h.model.play(three)
        house.writeStatus.set(502)

        let outcome = await h.model.cast(to: "office")

        XCTAssertEqual(
            outcome.note,
            "couldn't cast to office: its music player on the domovoi server isn't answering "
            + "(try again in a minute; if it keeps failing, restart the domovoi)"
        )
        XCTAssertEqual(h.model.target, .thisPhone)
        XCTAssertTrue(h.model.isPlaying, "the phone is silenced only once the room has taken the queue")
        XCTAssertEqual(h.engine.pauseCount, 0)
    }

    func testACastFromAPausedPhoneStartsTheRoomPausedThere() async {
        let h = PlayerHarness()
        h.model.play(three)
        h.model.pause()

        let outcome = await h.model.cast(to: "office")

        XCTAssertEqual(
            outcome.note, "casting to office, paused",
            "Spotify Connect's hand-off: the room starts the way the player it took over from was"
        )
    }

    // MARK: - room to room

    func testARoomToRoomCastStartsFromWhereTheFirstRoomHadGotTo() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        // office is playing Atmosphere, which is queue row 1.
        house.nowPlaying.set("""
        [{"room_id":"office","state":"play","elapsed_sec":88,"favorited":false,"track_id":12,\
        "song":{"title":"Atmosphere","artist":"Joy Division","duration_sec":390}},\
        {"room_id":"den","state":"stop"}]
        """)

        let outcome = await h.model.cast(to: "den")

        XCTAssertEqual(h.model.target, .room("den"))
        XCTAssertEqual(
            outcome.note, "casting to den · paused office",
            "the new room is told first; the old one is silenced only once it has taken the queue"
        )
        if case .toRoom(let plan, _, _, _, _) = outcome {
            XCTAssertEqual(plan.trackIDs, [12, 13], "from Atmosphere on, which is where office had got to")
            XCTAssertEqual(plan.startSec, 88, "at the room's elapsed time, not the phone's stale one")
        } else {
            XCTFail("expected a cast to den, got \(outcome)")
        }
    }

    func testARoomThatCannotBePausedIsSaidSoRatherThanAssumedQuiet() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        // The cast to den succeeds; the pause of office comes back ok: false,
        // which since 2026-10-01 is a no.
        house.writeBody.set(#"{"ok":false}"#)

        let outcome = await h.model.cast(to: "den")

        XCTAssertEqual(
            outcome.note, "casting to den · couldn't pause office, it may still be playing"
        )
    }

    // MARK: - coming back

    func testComingBackPicksUpWhereTheRoomHadGotToAndPlaysOnlyIfItWasPlaying() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        house.nowPlaying.set("""
        [{"room_id":"office","state":"play","elapsed_sec":120,"favorited":false,"track_id":13,\
        "song":{"title":"Decades","artist":"Joy Division","duration_sec":400}}]
        """)

        let outcome = await h.model.cast(to: nil)

        XCTAssertEqual(h.model.target, .thisPhone)
        XCTAssertNil(h.model.roomReading)
        XCTAssertEqual(h.model.index, 2, "the row office was on")
        XCTAssertEqual(h.model.positionSec, 120)
        XCTAssertEqual(outcome.note, "playing on this device · paused office")
        XCTAssertTrue(h.model.isPlaying)
    }

    func testComingBackFromAStoppedRoomDoesNotStartThePhone() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        house.nowPlaying.set(#"[{"room_id":"office","state":"stop"}]"#)

        let outcome = await h.model.cast(to: nil)

        XCTAssertFalse(h.model.isPlaying)
        XCTAssertEqual(outcome.note, "back on this device, paused · office wasn't playing")
    }

    func testComingBackWhenAlreadyHereSaysOnlyWhatIsTrue() async {
        let h = PlayerHarness()
        h.model.play(three)
        let outcome = await h.model.cast(to: nil)
        XCTAssertEqual(outcome.note, "playing on this device")
        XCTAssertEqual(h.model.target, .thisPhone)
    }

    // MARK: - a tapped queue row while casting

    func testATappedRowWhileCastingRestartsTheRoomThere() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")

        let outcome = await h.model.cast(fromQueueRow: 2)

        XCTAssertEqual(outcome.note, "casting to office")
        if case .toRoom(let plan, _, _, _, _) = outcome {
            XCTAssertEqual(plan.trackIDs, [13])
            XCTAssertEqual(plan.startSec, 0, "a tapped row starts from its top")
        } else {
            XCTFail("expected a cast, got \(outcome)")
        }
    }

    func testATappedRowThatCannotBeCastIsRefusedByName() async {
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission, PlayerSeed.phoneSong])
        _ = await h.model.cast(to: "office")

        let outcome = await h.model.cast(fromQueueRow: 1)

        XCTAssertEqual(
            outcome.note, "\"Pocketful\" is saved on this phone only; a room can't play it."
        )
        XCTAssertEqual(h.model.target, .room("office"), "and the room is left where it was")
    }

    func testATappedRowWhileNotCastingIsRefused() async {
        let h = PlayerHarness()
        h.model.play(three)
        let outcome = await h.model.cast(fromQueueRow: 1)
        XCTAssertEqual(outcome.note, "not casting to a room")
        XCTAssertFalse(outcome.changedTarget)
    }

    func testARowThatWentWhileTheTapWasInFlightIsSaidSo() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        let outcome = await h.model.cast(fromQueueRow: 9)
        XCTAssertEqual(outcome.note, "that queue entry is gone")
    }

    // MARK: - the lock

    func testTwoPicksAreSerialisedAndTheSecondStartsFromWhereTheFirstLeftThings() async {
        let h = PlayerHarness()
        h.model.play(three)

        // Both picked before either has finished, which is what the cast menu
        // closing on a tap makes easy.
        async let first = h.model.cast(to: "office")
        async let second = h.model.cast(to: "den")
        let outcomes = await [first, second]

        XCTAssertEqual(
            h.model.target, .room("den"),
            "the second pick waits for the first and starts from where it left things"
        )
        XCTAssertEqual(outcomes[0].note, "casting to office")
        XCTAssertEqual(
            outcomes[1].note, "casting to den · paused office",
            "office was the target by the time den was picked, so office is paused — "
            + "not left playing with nothing watching it"
        )
    }

    // MARK: - a play here mid-cast

    func testAPlayHereWhileACastIsOnItsWayWinsAndTheRoomIsPausedAgain() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")

        // A play here lands between this cast being picked and its POST
        // returning. The generation counter is read when the cast is picked and
        // again at every step that waits (Android's `playHereGen`).
        async let moving = h.model.cast(to: "den")
        h.model.play([PlayerSeed.episode])
        let outcome = await moving

        XCTAssertEqual(
            h.model.target, .thisPhone,
            "the play here won: the phone plays what was picked there"
        )
        XCTAssertEqual(h.model.queue.map(\.uid), ["pod-9"])
        switch outcome {
        case .superseded(let room, _, _):
            XCTAssertEqual(room, "den")
        case .refused:
            // The cast was picked after the play here had already bumped the
            // generation, so it never left. Either answer is honest; what is
            // not allowed is a cast that lands and takes the target back.
            break
        default:
            XCTFail("a play here must beat a cast still on its way, got \(outcome)")
        }
    }

    // MARK: - the room poll

    func testTheRoomPollFillsInTheReadingAndTheLockScreenFollowsIt() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        await h.settle(until: { h.model.roomReading != nil }, "the first room reading")

        XCTAssertEqual(h.model.roomReading?.title, "Atmosphere")
        XCTAssertEqual(h.model.roomReading?.elapsedSec, 42)
        XCTAssertEqual(
            h.model.roomReading?.readAtMs, 10_000,
            "the phone's own clock, which is the anchor the lyrics follow"
        )
        XCTAssertEqual(
            h.nowPlaying.latest?.title, "Atmosphere",
            "the lock screen shows the room's track, not the phone's Transmission"
        )
        XCTAssertEqual(h.nowPlaying.latest?.artist, "Joy Division · in office")
        XCTAssertFalse(h.nowPlaying.enabled.contains(.changePlaybackPosition))
    }

    func testAReadingForARoomThisPhoneHasLeftIsDropped() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        await h.settle(until: { h.model.roomReading != nil }, "the first room reading")
        // Read after await, with the room captured before it: the house now
        // answers only for den, so a poll that lands after the phone comes home
        // must not be shown.
        _ = await h.model.cast(to: nil)
        XCTAssertNil(h.model.roomReading)
        for _ in 0..<50 { await Task.yield() }
        XCTAssertNil(
            h.model.roomReading,
            "a poll that lands after the cast moved on is dropped, not shown"
        )
    }

    // MARK: - the transport while casting

    func testEveryTransportControlActsOnTheRoomWhileCasting() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        await h.settle(until: { h.model.roomReading != nil }, "the first room reading")
        h.engine.forget()

        h.model.next()
        await h.settle(until: { PlayerHouse.paths.contains("/api/music/skip/office") }, "skip")
        h.model.previous()
        await h.settle(until: { PlayerHouse.paths.contains("/api/music/previous/office") }, "previous")
        h.model.stop()
        await h.settle(until: { PlayerHouse.paths.contains("/api/music/stop/office") }, "stop")

        XCTAssertTrue(
            h.engine.calls.isEmpty,
            "the phone's own player is never the one moved while a room is playing the queue"
        )
        XCTAssertFalse(h.model.queue.isEmpty, "a stop sent to the room does not clear the phone's queue")
    }

    func testToggleWhileCastingSendsPauseForAPlayingRoomAndResumeForAStoppedOne() async {
        let h = PlayerHarness()
        h.model.play(three)
        _ = await h.model.cast(to: "office")
        await h.settle(until: { h.model.roomReading?.isPlaying == true }, "a playing room")
        h.model.toggle()
        await h.settle(until: { PlayerHouse.paths.contains("/api/music/pause/office") }, "pause")

        house.nowPlaying.set(#"[{"room_id":"office","state":"pause","elapsed_sec":42}]"#)
        await h.settle(until: { h.model.roomReading?.isPlaying == false }, "a paused room")
        h.model.toggle()
        await h.settle(until: { PlayerHouse.paths.contains("/api/music/resume/office") }, "resume")
    }
}
