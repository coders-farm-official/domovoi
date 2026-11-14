import XCTest
@testable import DomovoiKit

/// One owner of the microphone at a time, and the three refusals.
///
/// `MicArbiterTest` on Android. The refusals are the point: Android silences
/// all but one recorder without telling the others, so a room would go deaf
/// while a drop-in ran and say nothing about it. A sentence a screen can show
/// is what replaces that silence, and a typo in one of them is a person told
/// nothing.
final class PlayerMicArbiterTests: XCTestCase {

    private var arbiter = PlayerMicOwnership()

    override func setUp() {
        super.setUp()
        arbiter = PlayerMicOwnership()
    }

    func testTheFourLabelsAreTheAndroidWords() {
        XCTAssertEqual(PlayerMicOwner.roomMode.label, "room mode")
        XCTAssertEqual(PlayerMicOwner.dropIn.label, "a drop-in call")
        XCTAssertEqual(PlayerMicOwner.chatMic.label, "the chat mic")
        XCTAssertEqual(PlayerMicOwner.assistant.label, "a phone chat")
    }

    func testOneOwnerAtATime() {
        XCTAssertNil(arbiter.holder())
        XCTAssertNil(arbiter.refusal(.chatMic), "nothing holds it, so there is nothing to refuse")

        let lease = arbiter.tryAcquire(.chatMic)
        XCTAssertNotNil(lease)
        XCTAssertEqual(arbiter.holder(), .chatMic)
        XCTAssertNil(arbiter.tryAcquire(.dropIn), "a second user is refused")

        lease?.release()
        XCTAssertNil(arbiter.holder())
        XCTAssertNotNil(arbiter.tryAcquire(.dropIn), "and can have it once the first lets go")
    }

    func testTheThreeRefusalSentences() {
        _ = arbiter.tryAcquire(.roomMode)
        XCTAssertEqual(
            arbiter.refusal(.chatMic),
            "This phone is a room right now: room mode has the microphone."
        )
        XCTAssertEqual(
            arbiter.refusal(.roomMode),
            "This phone is a room right now: room mode has the microphone.",
            "room mode's own sentence wins over the already-in-use one"
        )

        let other = PlayerMicOwnership()
        _ = other.tryAcquire(.dropIn)
        XCTAssertEqual(other.refusal(.dropIn), "The microphone is already in use by a drop-in call.")
        XCTAssertEqual(other.refusal(.chatMic), "The microphone is in use by a drop-in call.")
    }

    func testRoomModeIsExclusiveUntilItLetsGo() {
        let room = arbiter.tryAcquire(.roomMode)
        XCTAssertTrue(arbiter.roomModeActive)
        XCTAssertNil(arbiter.tryAcquire(.assistant))
        room?.release()
        XCTAssertFalse(arbiter.roomModeActive, "the flag clears with the lease, not a tick later")
        XCTAssertNotNil(arbiter.tryAcquire(.assistant))
    }

    func testRoomModeDoesNotStartWhileSomethingElseHoldsTheMic() {
        _ = arbiter.tryAcquire(.chatMic)
        XCTAssertNil(arbiter.tryAcquire(.roomMode))
        XCTAssertFalse(arbiter.roomModeActive)
    }

    func testAReleaseIsIdempotent() {
        let lease = arbiter.tryAcquire(.chatMic)
        lease?.release()
        lease?.release()
        XCTAssertNil(arbiter.holder())
        XCTAssertNotNil(arbiter.tryAcquire(.dropIn))
        lease?.release()
        XCTAssertEqual(
            arbiter.holder(), .dropIn,
            "a stale lease does not take the mic from whoever holds it now — the token is what makes that true"
        )
    }

    func testAcquireOrThrowCarriesTheRefusalsWords() throws {
        _ = arbiter.tryAcquire(.roomMode)
        XCTAssertThrowsError(try arbiter.acquireOrThrow(.chatMic)) { error in
            XCTAssertEqual(
                (error as? PlayerMicRefused)?.note,
                "This phone is a room right now: room mode has the microphone."
            )
        }
        let free = PlayerMicOwnership()
        XCTAssertNoThrow(try free.acquireOrThrow(.chatMic))
    }

    func testTheHooksAreCalledOnTakeAndOnGiveBack() {
        let taken = Shared<[String]>([])
        let freed = Shared(0)
        arbiter.whenTaken(
            { owner in taken.mutate { $0.append(owner.rawValue) } },
            whenFreed: { freed.mutate { $0 += 1 } }
        )
        let lease = arbiter.tryAcquire(.dropIn)
        XCTAssertEqual(taken.current, ["dropIn"])
        XCTAssertEqual(freed.current, 0)
        lease?.release()
        XCTAssertEqual(freed.current, 1)
        lease?.release()
        XCTAssertEqual(freed.current, 1, "a second release calls nothing")

        _ = arbiter.tryAcquire(.chatMic)
        XCTAssertNil(arbiter.tryAcquire(.assistant))
        XCTAssertEqual(taken.current, ["dropIn", "chatMic"], "a refused acquire is not a take")
    }

    // MARK: - through the model

    @MainActor
    func testTakingTheMicPausesThePlayerAndReleasingItGivesTheSessionBack() async {
        StubURLProtocol.reset()
        PlayerHouse().install()
        PlayerMicArbiter.shared.reset()
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        XCTAssertTrue(h.model.isPlaying)
        XCTAssertEqual(h.session.active, .spoken, "a podcast activates the session in spokenAudio")

        let lease = PlayerMicArbiter.tryAcquire(.dropIn)
        XCTAssertNotNil(lease)
        await h.settle(until: { !h.model.isPlaying }, "the player pausing for the microphone")
        XCTAssertFalse(
            h.model.isPlaying,
            "room mode and a drop-in call are not things to talk over, so the honest behaviour is a pause"
        )

        let activations = h.session.activations.count
        // Settled on the COUNT, not on `categories.last == .spoken`: that was
        // already true from `play()`, so the case read the session before the
        // release's task had run and passed against the broken code (measured
        // on the negative control, 2026-11-02).
        let categories = h.session.categories.count
        lease?.release()
        await h.settle(until: { h.session.categories.count > categories }, "the category coming back")
        XCTAssertEqual(h.session.categories.last, .spoken)
        XCTAssertFalse(
            h.model.isPlaying,
            "the session comes back; playback does not — starting the podcast up as the call ends is nobody's ask"
        )
        // The CATEGORY comes back, not the activation. Playback is not
        // resumed, so activating a `.playback` session here would take the
        // process-wide session off whatever picked the audio up during the
        // call and then play nothing — `PlayerAudioSession`'s own rule, one
        // layer along. The next `play()` activates it.
        XCTAssertEqual(
            h.session.activations.count, activations,
            "a silent player does not claim the session"
        )
        PlayerMicArbiter.shared.reset()
        StubURLProtocol.reset()
    }

    // MARK: - the system taking the audio away

    @MainActor
    func testAnInterruptionPausesAndOnlyResumesWhenTheSystemSaysSo() {
        StubURLProtocol.reset()
        PlayerHouse().install()
        let h = PlayerHarness()
        h.model.play([PlayerSeed.episode])
        XCTAssertTrue(h.model.isPlaying)

        h.model.reactForTests(PlayerInterruption.interruption(began: true, shouldResume: false))
        XCTAssertFalse(h.model.isPlaying, "a phone call is an interruption, not a lock screen that lies")

        h.model.reactForTests(PlayerInterruption.interruption(began: false, shouldResume: false))
        XCTAssertFalse(h.model.isPlaying, "ended without `shouldResume`: music out of a pocket otherwise")

        h.engine.status = .readyToPlay
        h.model.reactForTests(PlayerInterruption.interruption(began: false, shouldResume: true))
        XCTAssertTrue(h.model.isPlaying)
        StubURLProtocol.reset()
    }

    @MainActor
    func testTheHeadphonesComingOutPausesAndTheirGoingInDoesNot() {
        StubURLProtocol.reset()
        PlayerHouse().install()
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])

        h.model.reactForTests(PlayerInterruption.routeChange(oldDeviceUnavailable: false))
        XCTAssertTrue(h.model.isPlaying, "a new device arriving is not a reason to stop: a jump scare otherwise")

        h.model.reactForTests(PlayerInterruption.routeChange(oldDeviceUnavailable: true))
        XCTAssertFalse(h.model.isPlaying, "Apple's own 'audio becoming noisy'")
        StubURLProtocol.reset()
    }

    @MainActor
    func testAMediaServicesResetSetsTheSourcesAgainRatherThanLeavingADeadPlayButton() {
        StubURLProtocol.reset()
        PlayerHouse().install()
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        h.engine.currentTimeSec = 48
        h.model.tickForTests()
        h.engine.forget()

        h.model.reactForTests(PlayerInterruption.mediaServicesReset, mediaServicesReset: true)
        XCTAssertEqual(
            h.engine.lastSetItems, ["http://10.0.0.42:6369/api/music/library/11/audio"],
            "every AVFoundation object under us was stale"
        )
        XCTAssertEqual(h.engine.lastOffset, 48, "and from where it was, not from the top")
        XCTAssertTrue(h.model.isPlaying, "it was playing, so it plays")
        StubURLProtocol.reset()
    }

    @MainActor
    func testAnInterruptionWhileCastingLeavesTheRoomAlone() async {
        StubURLProtocol.reset()
        PlayerHouse().install()
        let h = PlayerHarness()
        h.model.play([PlayerSeed.transmission])
        _ = await h.model.cast(to: "office")
        XCTAssertEqual(h.model.target, .room("office"))
        h.model.reactForTests(.pause)
        for _ in 0..<50 { await Task.yield() }

        // `pause()` while casting sends the ROOM a pause — so a phone call
        // here would stop the music in the office. The phone is already silent
        // (the cast paused it); nothing here is ours to pause. Asserted on the
        // room's own route rather than on a request count, because the room
        // poll keeps asking for now-playing while this runs.
        XCTAssertFalse(
            PlayerHouse.paths.contains("/api/music/pause/office"),
            "the room's audio is not ours to stop because this phone rang"
        )
        StubURLProtocol.reset()
    }
}
