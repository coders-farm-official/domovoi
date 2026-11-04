import Foundation
import XCTest
@testable import DomovoiKit

/// Playing an episode on this phone, and the place the house kept — the half
/// of D46 that `PodcastsModel` owns.
///
/// **This file exists because review 1 of the 2026-11-03 batch found the whole
/// path uncovered.** `canPlay`, `play`, `resume`, `startOver`, `cancelResume`
/// and ``PodcastResume`` are about a hundred lines of new asynchronous state —
/// including the `houseEpoch` read-after-await guard and the `resuming` state
/// that `serverChanged()` must drop — and nothing named any of them:
/// `grep -rn "resuming\|PodcastResume" Tests DomovoiUITests` was empty, while
/// two comments (`StubHouseUITests+Podcasts.swift` and the unit's DECISIONS)
/// said `PodcastsModelTests` owned the arithmetic. It does not; that file was
/// never touched. A comment claiming coverage is worse than no coverage,
/// because it stops the next reader looking.
///
/// The five things a comment cannot carry:
///
/// 1. **A place worth asking about asks**, and one that is not **plays**. The
///    boundary is `pos > 5` (`PodcastsScreen.kt:226`), not `pos > 0`: the
///    model had `> 0` until this review, which is a dialog in front of
///    somebody who pressed play and pressed stop.
/// 2. **`resume` plays from the place and `startOver` plays from zero**, both
///    keeping the speed — that is how this person listens, not where they
///    were.
/// 3. **The house's answer lands under the house it was asked of.** The epoch
///    is taken before the `await` and checked after it, and this file switches
///    the house **inside** the round trip, which is the only arrangement that
///    fails against the broken code ([[read-after-await-wrong-key]]).
/// 4. **A move drops the open question.** `resuming` carries the *old* house's
///    episode id, and `resume` would hand the new house's player an id it has
///    never heard of.
/// 5. **A refusal refuses.** No player, or an episode the house has no file
///    for, starts nothing and leaves no dialog behind.
@MainActor
final class PodcastPlayTests: XCTestCase {

    private let houseA = "http://10.0.0.42:6369"
    private let houseB = "http://10.0.0.77:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
    }

    /// **Leave nothing running.** Every case here builds a ``PlayerModel``,
    /// which registers with ``PlayerMicArbiter`` and the notification centre,
    /// and most leave a position read or a resume Task in the air. XCTest runs
    /// classes in one process, one after another, so work this file leaves
    /// behind would otherwise run *during somebody else's cases* — on the same
    /// main actor, against the dispatcher they installed, and into the
    /// `StubURLProtocol.recorded` they assert on. This is hygiene, the same
    /// `setUp`/`tearDown` pair `PlayerModelCastTests` has had all along.
    ///
    /// **It is not the fix for the flake that prompted it, and this comment
    /// said it was.** The suite fails intermittently in
    /// `AudiobooksModelTests.testOpeningABookTakesTheServersPlaceWhenOnlyItHasOne`,
    /// and the obvious story — work leaked from the player's new classes into
    /// the ones that run next alphabetically — was wrong. Measured 2026-11-04
    /// on the Mac, under the slot: that case run **alone**, `--filter
    /// AudiobooksModelTests` with nothing else in the process, failed 4 of 8
    /// runs. Twelve whole-suite runs alternating between this branch and the
    /// pre-review commit failed 2 of 6 on each. The cause is in its own rig —
    /// `sleep: { _ in }` into ``BookTimeout/bounded(ms:sleep:)``, which races
    /// `work()` against an *instant* timeout in a task group, so the loser is
    /// whichever child the scheduler happens to run second. It is logged for
    /// the unit that owns that file (`DECISIONS.md` §7.6).
    override func tearDown() async throws {
        for _ in 0..<settleYieldLimit { await Task.yield() }
        StubURLProtocol.reset()
        PlayerMicArbiter.shared.reset()
        try await super.tearDown()
    }

    // MARK: - the rig

    /// A `PodcastsModel` with a real `PlayerModel` over fake audio.
    ///
    /// The engine, the session and the Now Playing slot are `PlayerFakes.swift`'s,
    /// as every other player case uses — so what is asserted is the queue the
    /// player was actually handed, not a spy's record of a call. `wait` is a
    /// no-op: nothing here is about the search debounce.
    private struct Rig {
        let podcasts: PodcastsModel
        let player: PlayerModel
        let engine: FakePlayerEngine
        let store: InMemoryShellStore
        let house: PlayerHouse
    }

    private func rig(deviceID: String = "phone-1", withPlayer: Bool = true) -> Rig {
        let store = InMemoryShellStore(serverURL: houseA, token: "household-abc")
        store.trustServer(houseA, identity: TestIdentity.real.pin)
        store.trustServer(houseB, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let engine = FakePlayerEngine()
        let player = PlayerModel(
            client: container.client,
            deviceID: deviceID,
            engine: engine,
            session: RecordingPlayerAudioSession(),
            nowPlaying: RecordingNowPlayingPublisher(),
            clock: PlayerTestClock(nowMs: 10_000)
        )
        let house = PlayerHouse()
        house.install()
        let podcasts = PodcastsModel(in: container, wait: { _ in })
        if withPlayer { podcasts.player = player }
        return Rig(
            podcasts: podcasts, player: player, engine: engine,
            store: store, house: house
        )
    }

    /// `The Lost Coast`, episode 9 — the house has the audio.
    ///
    /// The seed discriminates: the title appears in no other fixture in this
    /// package, so an assertion that the queue holds it cannot pass over some
    /// other item the player had lying about.
    private func episode(
        id: Int64 = 9,
        title: String = "The Lost Coast",
        hasFile: Bool = true
    ) -> PodcastEpisode {
        PodcastEpisode(id: id, title: title, durationSec: 3600, hasFile: hasFile)
    }

    /// Yield until `done`, rather than sleeping.
    private func settle(
        until done: @MainActor () -> Bool,
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

    // MARK: - 1. ask, or play

    /// A place past five seconds **asks**, and asks about the right episode.
    func testAPlaceWorthAskingAboutAsksRatherThanPlaying() async {
        let kit = rig()
        kit.house.podcastPosition.set(#"{"position_sec":3671,"speed":1.25}"#)

        kit.podcasts.play(episode())
        await settle(until: { kit.podcasts.resuming != nil }, "the resume prompt")

        let asked = kit.podcasts.resuming
        XCTAssertEqual("The Lost Coast", asked?.title)
        XCTAssertEqual(3671, asked?.positionSec)
        XCTAssertEqual(1.25, asked?.speed)
        XCTAssertEqual("You were at 1:01:11.", asked?.said)
        XCTAssertTrue(
            kit.engine.lastSetItems.isEmpty,
            "the episode started playing while the person was still being asked"
        )
        XCTAssertTrue(kit.player.queue.isEmpty, "and it was queued, too")
    }

    /// A place of five seconds or less **plays**, from the top, at the saved
    /// speed and with no dialog.
    ///
    /// `pos > 5` strictly (`PodcastsScreen.kt:226`). Both sides of the
    /// boundary, in one case, because the bug this pins is an off-by-one in a
    /// comparison and a case that tried one side would pass against `>=`.
    func testAPlaceOfFiveSecondsOrLessJustPlays() async {
        for place in [0.0, 0.4, 5.0] {
            StubURLProtocol.reset()
            let kit = rig()
            kit.house.podcastPosition.set(#"{"position_sec":\#(place),"speed":1.5}"#)

            kit.podcasts.play(episode())
            await settle(until: { !kit.player.queue.isEmpty }, "the episode at \(place)s")

            XCTAssertNil(
                kit.podcasts.resuming,
                "a place of \(place)s put a resume dialog in front of somebody who "
                    + "pressed play and pressed stop"
            )
            XCTAssertEqual(["The Lost Coast"], kit.player.queue.map(\.title))
            XCTAssertEqual(0, kit.engine.lastOffset, "it did not start at the top")
            XCTAssertEqual(1.5, kit.player.speed, "the saved speed was dropped")
        }
    }

    /// Five-and-a-bit seconds asks. The other side of the same boundary.
    func testJustOverFiveSecondsAsks() async {
        let kit = rig()
        kit.house.podcastPosition.set(#"{"position_sec":5.5,"speed":1.0}"#)

        kit.podcasts.play(episode())
        await settle(until: { kit.podcasts.resuming != nil }, "the resume prompt")

        XCTAssertEqual(5.5, kit.podcasts.resuming?.positionSec)
    }

    // MARK: - 2. what each of the three buttons does

    /// `resume` plays from the place; the dialog goes.
    func testResumePlaysFromThePlaceAndClosesTheDialog() async {
        let kit = rig()
        kit.house.podcastPosition.set(#"{"position_sec":1800,"speed":1.25}"#)
        kit.podcasts.play(episode())
        await settle(until: { kit.podcasts.resuming != nil }, "the resume prompt")

        kit.podcasts.resume()

        XCTAssertNil(kit.podcasts.resuming, "the dialog is still up over a playing episode")
        XCTAssertEqual(["The Lost Coast"], kit.player.queue.map(\.title))
        XCTAssertEqual(1800, kit.engine.lastOffset, "it did not resume at the place")
        XCTAssertEqual(1.25, kit.player.speed)
    }

    /// `start over` plays from zero and **keeps the speed**: that is how this
    /// person listens, not where they were.
    func testStartOverPlaysFromZeroAndKeepsTheSpeed() async {
        let kit = rig()
        kit.house.podcastPosition.set(#"{"position_sec":1800,"speed":1.25}"#)
        kit.podcasts.play(episode())
        await settle(until: { kit.podcasts.resuming != nil }, "the resume prompt")

        kit.podcasts.startOver()

        XCTAssertNil(kit.podcasts.resuming)
        XCTAssertEqual(0, kit.engine.lastOffset, "start over did not start over")
        XCTAssertEqual(
            1.25, kit.player.speed,
            "start over reset the speed as well as the place"
        )
    }

    /// Dismissing the dialog plays **nothing** (`BookUi.kt:167`'s
    /// `onDismissRequest`).
    func testDismissingTheDialogPlaysNothing() async {
        let kit = rig()
        kit.house.podcastPosition.set(#"{"position_sec":1800,"speed":1.0}"#)
        kit.podcasts.play(episode())
        await settle(until: { kit.podcasts.resuming != nil }, "the resume prompt")

        kit.podcasts.cancelResume()

        XCTAssertNil(kit.podcasts.resuming)
        XCTAssertTrue(kit.player.queue.isEmpty, "a dismissed dialog started the episode")
        XCTAssertTrue(kit.engine.lastSetItems.isEmpty)
    }

    // MARK: - 3. the house the answer belongs to

    /// A place that lands **after the app has moved house** is dropped.
    ///
    /// The whole mechanism, exercised the only way that discriminates: the
    /// position request is **parked inside the dispatcher**, the house is
    /// moved while it is parked, and then it is released. A case that moved
    /// the house before or after the round trip would pass against a model
    /// that had no guard at all.
    ///
    /// Why it matters: an episode id is the **house's** id. House A's place
    /// for episode 9 applied to house B's episode 9 is somebody else's
    /// programme opened in the middle — [[read-after-await-wrong-key]], the
    /// top defect class on this track.
    func testAPlaceThatLandsAfterAMoveIsDropped() async {
        let kit = rig()
        kit.house.podcastPosition.set(#"{"position_sec":3671,"speed":1.25}"#)
        kit.house.holdPosition.set(true)

        kit.podcasts.play(episode())
        await settle(
            until: { kit.house.positionArrivals.current > 0 },
            "the position read reaching the house"
        )

        // Inside the round trip: the app is now pointed at another house.
        _ = kit.store.setServerURL(self.houseB)
        kit.podcasts.serverChanged()
        kit.house.release()

        // Nothing to wait *for*, so the wait is for the request to be done and
        // then an assertion that nothing came of it.
        for _ in 0..<settleYieldLimit { await Task.yield() }
        XCTAssertNil(
            kit.podcasts.resuming,
            "house A's place for episode 9 became a resume prompt on house B — "
                + "somebody else's programme, opened in the middle"
        )
        XCTAssertTrue(
            kit.player.queue.isEmpty,
            "house A's episode was queued on house B"
        )
    }

    /// A move **drops an open dialog**, before anything else.
    ///
    /// `resuming` holds the old house's queue item, so `resume` would hand the
    /// new house's player an episode id it has never heard of.
    func testAMoveDropsAnOpenResumeDialog() async {
        let kit = rig()
        kit.house.podcastPosition.set(#"{"position_sec":3671,"speed":1.0}"#)
        kit.podcasts.play(episode())
        await settle(until: { kit.podcasts.resuming != nil }, "the resume prompt")

        kit.podcasts.serverChanged()

        XCTAssertNil(
            kit.podcasts.resuming,
            "the dialog survived a house move, and its `resume` would play the "
                + "old house's episode id against the new house"
        )
        // And the choice is gone, not just hidden: a `resume` after the move
        // must do nothing at all.
        kit.podcasts.resume()
        XCTAssertTrue(kit.player.queue.isEmpty, "resume after a move played something")
    }

    // MARK: - 4. what is refused, and what it leaves behind

    /// No player offers nothing and plays nothing.
    ///
    /// ``ShellModel/player`` is nil on a build with no audio engine (review 2
    /// corrected the "phone with no device id" this said), and `PodcastsView`
    /// hands nil over. The row draws no play control at all, so this is the
    /// model half of the same rule: a tap that reached the model anyway does
    /// nothing, and asks nothing.
    func testWithNoPlayerNothingIsOfferedAndNothingPlays() async {
        let kit = rig(withPlayer: false)
        kit.house.podcastPosition.set(#"{"position_sec":3671,"speed":1.0}"#)

        XCTAssertFalse(kit.podcasts.canPlay(episode()), "a model with no player offered play")
        kit.podcasts.play(episode())
        for _ in 0..<settleYieldLimit { await Task.yield() }

        XCTAssertNil(kit.podcasts.resuming)
        XCTAssertEqual(
            0, kit.house.positionArrivals.current,
            "a phone that cannot play still asked the house where it had got to"
        )
    }

    /// An episode the house has no file for is refused, with a player.
    ///
    /// `has_file` is the house's half and the player is the phone's, and they
    /// are different reasons — ``PodcastsModel/canPlay(_:)`` needs both.
    /// Android disables the button for the first (`PodcastsEpisodes.kt:223`);
    /// here it is absent.
    func testAnEpisodeWithNoFileIsRefusedEvenWithAPlayer() async {
        let kit = rig()

        XCTAssertFalse(kit.podcasts.canPlay(episode(hasFile: false)))
        XCTAssertTrue(
            kit.podcasts.canPlay(episode()),
            "the discriminating half: the same rig DOES offer an episode with a file"
        )

        kit.podcasts.play(episode(hasFile: false))
        for _ in 0..<settleYieldLimit { await Task.yield() }

        XCTAssertNil(kit.podcasts.resuming)
        XCTAssertEqual(
            0, kit.house.positionArrivals.current,
            "an episode with no audio was looked up anyway"
        )
    }

    /// `resume` and `startOver` with no dialog open do nothing.
    ///
    /// Both are reachable from a button on a sheet that can be redrawn between
    /// the tap and the call, and a `resuming` of nil is the state after a
    /// house move — where playing *anything* would be the bug the move exists
    /// to prevent.
    func testResumeAndStartOverDoNothingWithNothingAsked() {
        let kit = rig()

        kit.podcasts.resume()
        kit.podcasts.startOver()

        XCTAssertTrue(kit.player.queue.isEmpty)
        XCTAssertTrue(kit.engine.lastSetItems.isEmpty)
    }

    // MARK: - 5. what the queue entry carries

    /// The queue entry is the episode's, with the **show's stored artwork**
    /// and never the publisher's own URL.
    ///
    /// `PlayItem.fromEpisode` (`PlayItem.kt:68`) through
    /// ``PodcastArt/path(for:)``: a feed's `artwork` is an address on somebody
    /// else's server, and this phone does not contact one.
    func testTheQueueEntryCarriesTheShowAndTheHousesArtwork() async {
        let kit = rig()
        kit.house.podcastPosition.set(#"{"position_sec":0,"speed":1.0}"#)
        kit.podcasts.play(episode())
        await settle(until: { !kit.player.queue.isEmpty }, "the queue")

        let entry = kit.player.queue.first
        XCTAssertEqual(.podcast, entry?.kind)
        XCTAssertEqual(9, entry?.itemID)
        XCTAssertEqual("The Lost Coast", entry?.title)
        XCTAssertEqual(3600, entry?.durationSec)
        XCTAssertFalse(
            entry?.src.hasPrefix("http") ?? true,
            "the entry plays an address off this house: \(entry?.src ?? "nil")"
        )
    }
}
