import Foundation
import XCTest
@testable import DomovoiKit

/// ``PlayerModelMusicEngine``: the app's one player seen through the player
/// tab's vocabulary (D46/D49).
///
/// Every member forwards, so what is worth stating is the **three maps** and
/// the one new member — not that `next()` calls `next()`. The player itself is
/// driven through ``PlayerHarness``'s fakes, so these are decisions rather
/// than audio.
@MainActor
final class MusicEngineAdapterTests: XCTestCase {

    private func rig() -> (PlayerHarness, PlayerModelMusicEngine) {
        let harness = PlayerHarness()
        return (harness, PlayerModelMusicEngine(harness.model))
    }

    // MARK: - the queue map

    /// A library track, both ways round: the entry the tab draws carries the
    /// player's own uid, its id as `trackID`, and the same words.
    func testALibraryTrackReadsAsTheTabsOwnEntry() {
        let (harness, engine) = rig()
        harness.model.play([
            PlayerItem.fromTrack(
                id: 12, title: "transmission", artist: "Joy Division",
                album: "Substance", durationSec: 211
            ),
        ])

        XCTAssertEqual(engine.queue.count, 1)
        let entry = engine.queue[0]
        XCTAssertEqual(entry.uid, "lib-12")
        XCTAssertEqual(entry.kind, .library)
        XCTAssertEqual(entry.trackID, 12)
        XCTAssertEqual(entry.title, "transmission")
        XCTAssertEqual(entry.artist, "Joy Division")
        XCTAssertEqual(entry.album, "Substance")
        XCTAssertEqual(entry.src, "/api/music/library/12/audio")
        XCTAssertEqual(entry.durationSec, 211)
        XCTAssertTrue(entry.seekable)
        // The cast planner reads this, and only a library entry may go.
        XCTAssertTrue(entry.kind.castable)
        XCTAssertEqual(entry.coverTrackID, 12)
    }

    /// All five kinds map across, and they are the five the planner counts.
    /// A `rawValue` round trip would have had a failure branch whose only
    /// honest filling would be a kind the planner then mis-counts.
    func testEveryKindHasATwin() {
        XCTAssertEqual(PlayerModelMusicEngine.kind(.library), .library)
        XCTAssertEqual(PlayerModelMusicEngine.kind(.radio), .radio)
        XCTAssertEqual(PlayerModelMusicEngine.kind(.podcast), .podcast)
        XCTAssertEqual(PlayerModelMusicEngine.kind(.audiobook), .audiobook)
        XCTAssertEqual(PlayerModelMusicEngine.kind(.device), .device)
        XCTAssertEqual(
            PlayerKind.allCases.count, MusicPlayKind.allCases.count,
            "a kind added to one enum and not the other is a queue entry the tab cannot draw"
        )
    }

    /// A live station is **not seekable**, and that has to survive the map:
    /// the tab's scrubber is drawn from this flag, and a scrubber over a live
    /// stream is a control that cannot do what it says.
    func testALiveStationStaysUnseekable() {
        let (harness, engine) = rig()
        harness.model.play([PlayerItem.fromStation(id: 3, name: "FIP")])
        XCTAssertEqual(engine.queue[0].kind, .radio)
        XCTAssertFalse(engine.queue[0].seekable)
        XCTAssertFalse(engine.queue[0].kind.castable, "a room plays library ids, not a stream")
    }

    /// An audiobook's chapters reach the tab, because
    /// ``MusicPlayerMath/currentChapterIndex(_:positionSec:)`` is what draws
    /// the chapter row from them.
    func testChaptersSurviveTheMap() {
        let (harness, engine) = rig()
        harness.model.play([
            PlayerItem.fromBook(
                id: 7, title: "Dune",
                chapters: [
                    PlayerChapter(title: "one", startSec: 0),
                    PlayerChapter(title: "two", startSec: 900),
                ]
            ),
        ])
        XCTAssertEqual(engine.queue[0].chapters.map(\.title), ["one", "two"])
        XCTAssertEqual(engine.queue[0].chapters.map(\.startSec), [0, 900])
    }

    // MARK: - the target map

    func testTheTargetReadsAcross() {
        let (harness, engine) = rig()
        XCTAssertEqual(engine.target, .local)
        XCTAssertNil(engine.target.roomID)
        _ = harness
    }

    // MARK: - what the tab reads

    /// `speed` widens `Float` to `Double` and `setSpeed` narrows it back. The
    /// values the tab offers are quarter and half steps, every one exact in
    /// binary, so the round trip is not approximate — which is the claim this
    /// case exists to pin.
    func testTheSpeedRoundTripIsExact() {
        let (_, engine) = rig()
        for wanted in [0.75, 1.0, 1.25, 1.5, 2.0] as [Double] {
            engine.setSpeed(wanted)
            XCTAssertEqual(engine.speed, wanted, "speed \(wanted) came back as \(engine.speed)")
        }
    }

    /// Nothing queued is nothing drawn, and the tab asks three separate
    /// questions about it.
    func testAnEmptyPlayerIsAnEmptyTab() {
        let (_, engine) = rig()
        XCTAssertTrue(engine.queue.isEmpty)
        XCTAssertFalse(engine.isPlaying)
        XCTAssertNil(engine.remote)
        XCTAssertNil(engine.sleepRemainingSec)
    }

    // MARK: - playTracks, the one member the conformance added

    /// The queue is the tracks, in order, and the index is the one asked for.
    func testPlayTracksQueuesThemInOrder() {
        let (harness, engine) = rig()
        engine.playTracks(
            [
                LibraryTrack(id: 1, title: "transmission"),
                LibraryTrack(id: 2, title: "atmosphere"),
                LibraryTrack(id: 3, title: "decades"),
            ],
            startAt: 1
        )
        XCTAssertEqual(harness.model.queue.map(\.itemID), [1, 2, 3])
        XCTAssertEqual(harness.model.index, 1)
        XCTAssertEqual(harness.model.current?.title, "atmosphere")
    }

    /// A track with no title at all still gets one: `displayTitle` falls back
    /// to the file name and then to a word, and an entry titled `""` is a row
    /// with nothing in it.
    func testATrackWithNoTitleIsStillNamed() {
        let (harness, engine) = rig()
        engine.playTracks([LibraryTrack(id: 5, filePath: "/music/odd.flac")], startAt: 0)
        XCTAssertFalse(harness.model.queue[0].title.isEmpty)
    }

    /// An empty list is the player's own early return: nothing plays, and the
    /// queue that was there is **left alone** rather than replaced by nothing.
    func testAnEmptyListLeavesTheQueueAlone() {
        let (harness, engine) = rig()
        engine.playTracks([LibraryTrack(id: 1, title: "transmission")], startAt: 0)
        engine.playTracks([], startAt: 0)
        XCTAssertEqual(harness.model.queue.map(\.itemID), [1])
    }

    // MARK: - the model over the adapter

    /// ``MusicLocalQueueModel`` is what the sheet calls, and with an engine
    /// registered it plays and says so.
    func testTheQueueModelPlaysThroughTheAdapterAndSaysWhatStarted() {
        let (harness, engine) = rig()
        let model = MusicLocalQueueModel(deviceID: "phone-1")
        model.use(engine)

        XCTAssertTrue(model.mayPlayHere)
        XCTAssertTrue(model.playHere(
            [LibraryTrack(id: 1, title: "transmission"), LibraryTrack(id: 2, title: "atmosphere")],
            startAt: 1
        ))
        XCTAssertEqual(harness.model.index, 1)
        XCTAssertEqual(model.takeToast(), "playing \"atmosphere\" on this device")
        XCTAssertNil(model.takeToast(), "the toast is taken once")
        XCTAssertEqual(model.queue.map(\.trackID), [1, 2])
        XCTAssertEqual(model.current?.title, "atmosphere")
    }

    /// An index past the end is clamped, not refused: a playlist can be
    /// re-read between the tap and the call, and starting at the top beats not
    /// starting.
    func testAnIndexPastTheEndStartsAtTheLastTrack() {
        let (harness, engine) = rig()
        let model = MusicLocalQueueModel(deviceID: "phone-1")
        model.use(engine)
        XCTAssertTrue(model.playHere([LibraryTrack(id: 1, title: "transmission")], startAt: 9))
        XCTAssertEqual(harness.model.index, 0)
        XCTAssertEqual(model.takeToast(), "playing \"transmission\" on this device")
    }

    /// An empty playlist is refused and says nothing — which is why the sheet
    /// does not draw the button for one at all.
    func testAnEmptyPlaylistIsRefusedSilently() {
        let (_, engine) = rig()
        let model = MusicLocalQueueModel(deviceID: "phone-1")
        model.use(engine)
        XCTAssertFalse(model.playHere([]))
        XCTAssertNil(model.takeToast())
    }

    /// ``MusicLocalQueueModel/use(_:)`` **owns** the adapter, which is the
    /// I003 fix.
    ///
    /// `MusicView`'s `.task` is `player?.use(PlayerModelMusicEngine(made))` —
    /// an adapter built on that line over the shell's player and held by
    /// nothing else. While the seam was `weak` it was released at once,
    /// ``MusicLocalQueueModel/hasEngine`` stayed false, and the player tab
    /// and both `play on this device` buttons were never drawn (train I003,
    /// four cases). The harness holds the player and nothing holds the
    /// adapter, which is exactly the call site.
    func testTheSeamKeepsAnEngineNothingElseHolds() {
        let harness = PlayerHarness()
        let model = MusicLocalQueueModel(deviceID: "phone-1")

        autoreleasepool {
            model.use(PlayerModelMusicEngine(harness.model))
        }
        XCTAssertTrue(
            model.hasEngine,
            "the adapter was let go of the moment it was registered, so this "
                + "phone offers no player tab and no play on this device"
        )
        XCTAssertTrue(MusicTab.shown(hasPlayer: model.hasEngine).contains(.player))
    }

    /// With an engine registered the player tab is in the bar. The same call
    /// with `false` is a phone whose ``ShellModel/player`` is nil.
    func testTheTabAppearsOnceAnEngineIsRegistered() {
        let (_, engine) = rig()
        let model = MusicLocalQueueModel(deviceID: "phone-1")
        XCTAssertFalse(MusicTab.shown(hasPlayer: model.hasEngine).contains(.player))
        model.use(engine)
        XCTAssertTrue(MusicTab.shown(hasPlayer: model.hasEngine).contains(.player))
    }

    // MARK: - the outcome map

    /// Branch for branch, including the refusal.
    ///
    /// All five are ``MusicCastOutcome``'s, the refusal included. It went
    /// back and forth: a fifth case on `MusicCastOutcome` until review 1 of
    /// the 2026-11-03 batch, then a `MusicMoreCastResult` wrapper beside the
    /// protocol — because `MusicCastPlan.swift` was not the player unit's
    /// file (BATCH-RULES §1) and a case cannot be added from outside — then
    /// ``MusicCastOutcome/refused(note:)`` on the integration, where it
    /// belonged all along. The alternative, in any shape, was reporting a
    /// POST that failed as "on this device": true about the audio, a lie
    /// about what was asked.
    func testEveryCastOutcomeHasATwinAndKeepsItsSentence() {
        let plan = PlayerCastPlan(
            trackIDs: [4, 7], startIndex: 0, startSec: 31, phoneOnly: 1, notInLibrary: 2
        )
        let sent = PlayerModelMusicEngine.outcome(
            .toRoom(plan: plan, room: "office", left: "kitchen", leftPaused: true, paused: false)
        )
        guard case .toRoom(let made, let room, let left, let leftPaused, let paused) = sent
        else {
            return XCTFail("a cast to a room must stay a cast to a room: \(sent)")
        }
        XCTAssertEqual(made.trackIDs, [4, 7])
        XCTAssertEqual(made.startSec, 31)
        XCTAssertEqual(made.phoneOnly, 1)
        XCTAssertEqual(made.notInLibrary, 2)
        XCTAssertEqual(room, "office")
        XCTAssertEqual(left, "kitchen")
        XCTAssertTrue(leftPaused)
        XCTAssertFalse(paused)

        if case .superseded(let room, let was, let undone) =
            PlayerModelMusicEngine.outcome(.superseded(room: "office", sent: true, undone: false)) {
            XCTAssertEqual(room, "office")
            XCTAssertTrue(was)
            XCTAssertFalse(undone)
        } else {
            XCTFail("superseded must stay superseded")
        }

        if case .here(let left, let playing, _, _, _) = PlayerModelMusicEngine.outcome(
            .here(left: "office", playing: true, leftPaused: true, leftWasPlaying: true)
        ) {
            XCTAssertEqual(left, "office")
            XCTAssertTrue(playing)
        } else {
            XCTFail("coming back here must stay coming back here")
        }

        // The sentence is the whole point, and `here(left: nil, …)` would have
        // replaced it with "on this device".
        let refused = PlayerModelMusicEngine.outcome(.refused(note: "office didn't answer"))
        XCTAssertEqual(refused.note, "office didn't answer")
        if case .refused = refused {} else { XCTFail("a refusal must stay a refusal: \(refused)") }
    }

    // MARK: - the model before an engine reaches it

    /// With **no engine registered** nothing is offered and nothing is
    /// claimed.
    ///
    /// The arm every Music screen passes through: ``MusicLocalQueueModel`` is
    /// built in ``MusicView``'s `.task` and handed an engine on the same
    /// appearance, so for one turn of the loop it has none — and on a shell
    /// whose ``ShellModel/player`` is nil (a build with no audio engine) it
    /// never gets one. `MusicMoreModelTests` has the rest of this arm and is
    /// not this unit's file to add to; what is here is the pair D46 added,
    /// because a `playHere` that returned true with nothing behind it would
    /// put a toast on screen for audio that never started.
    func testWithNoEngineNothingMayPlayHereAndNothingIsClaimed() {
        let model = MusicLocalQueueModel(deviceID: "phone-1")
        XCTAssertFalse(model.hasEngine)
        XCTAssertFalse(model.mayPlayHere, "no engine, so no `play on this device` either")
        XCTAssertFalse(
            model.playHere([LibraryTrack(id: 4, title: "transmission")]),
            "a play with no engine hands nothing over and says so"
        )
        XCTAssertNil(model.takeToast(), "and it does not claim to have started anything")
        XCTAssertTrue(model.queue.isEmpty)
    }

    /// A plan is the same five numbers under both names, and `castable` reads
    /// the same on each — the planner's gate, so a mismatch would offer a cast
    /// menu over a queue nothing in which can go.
    func testACastPlanIsTheSameFiveNumbers() {
        let mine = PlayerCastPlan(
            trackIDs: [], startIndex: -1, startSec: 0, phoneOnly: 3, notInLibrary: 0
        )
        let theirs = PlayerModelMusicEngine.plan(mine)
        XCTAssertEqual(theirs.trackIDs, [])
        XCTAssertEqual(theirs.startIndex, -1)
        XCTAssertEqual(theirs.phoneOnly, 3)
        XCTAssertEqual(mine.castable, theirs.castable)
        XCTAssertFalse(theirs.castable)
    }
}
