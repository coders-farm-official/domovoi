import Foundation
import XCTest
@testable import DomovoiKit

/// The lyrics panel's **follow** state machine: the pause a drag makes, the
/// four-second resume after it, and every path that cancels that resume.
///
/// Kamron's answer 35, 2026-11-03: `resume following on the 4-second timer`.
/// Android pauses on `DragInteraction.Start` and starts a fresh
/// `delay(FOLLOW_PAUSE_MS)` on `Stop`/`Cancel`
/// (`LyricsPanel.kt:468-485`, `:118`); none of that is specified anywhere on
/// that side, because it is a `LaunchedEffect` inside a composable.
///
/// **Its own file, and named after this unit**, rather than more cases in
/// `MusicMoreModelTests`: that file's lyrics section belongs to the player
/// unit's branch in the same batch, and two branches adding cases to one
/// `final class` is a merge conflict a train pays for.
///
/// ### How a timer that must not fire is tested
///
/// Four of these cases are about a resume being **cancelled**, and "it did
/// not happen" is the hardest thing in this file to assert honestly: a
/// resume still parked and a resume cancelled look identical from outside.
/// So the parked wait is **releasable**. A cancel case cancels the resume,
/// then releases the park, then waits real time — and if the cancel had not
/// taken, the task would wake, find itself uncancelled, and turn following
/// on. Each one also arranges for `following` to be **false** at that moment,
/// so the stale resume has something to change.
@MainActor
final class ScreensLyricsFollowTests: XCTestCase {

    private let server = "http://10.0.0.77:6369"

    override func setUp() {
        super.setUp()
        StubURLProtocol.reset()
    }

    override func tearDown() {
        StubURLProtocol.reset()
        super.tearDown()
    }

    // MARK: - the rig

    private struct Rig {
        let model: MusicRoomLyricsModel

        /// Every duration the model asked to wait for that was the resume's,
        /// in order. The republish loop's 100 ms waits are not in here.
        let resumeWaits: Shared<[Int64]>

        /// Lets a parked resume finish, so a cancel case can prove the task
        /// is gone rather than merely asleep.
        let releaseResume: Shared<Bool>

        /// Set when a parked resume ran its bound out — neither released nor
        /// cancelled. Asserted false, for `ParkedRequest/ranOut`'s reason: a
        /// wait that outlasts its bound turns the case it serves into one
        /// that cannot fail.
        let ranOut: Shared<Bool>

        /// Let a parked resume finish, if it is still alive to finish.
        ///
        /// Named rather than `releaseResume.set(true)` at six call sites,
        /// because what it means at each of them is "now prove the task is
        /// gone".
        func letTheResumeFinish() {
            releaseResume.set(true)
        }
    }

    /// - Parameter parkTheResume: `true` holds the resume's wait open until
    ///   it is cancelled or ``Rig/releaseResume`` is set, which is how a case
    ///   proves a cancel; `false` lets it return at once, which is how a case
    ///   proves the resume fires.
    private func rig(parkTheResume: Bool) -> Rig {
        // The room read answers nothing. This file is about the follow flag,
        // and a panel with no document still pauses and resumes — the view's
        // `onScrollPhaseChange` does not ask what is in the list.
        StubURLProtocol.dispatch { _ in .json(#"{"detail":"no lyrics here"}"#, status: 404) }
        let store = InMemoryShellStore(serverURL: server, token: "household-abc")
        store.trustServer(server, identity: TestIdentity.real.pin)
        let container = AppContainer(
            store: store,
            vault: TokenVault(items: InMemoryKeychain()),
            lock: HouseholdLock(),
            clock: { 0 },
            configuration: StubURLProtocol.session(timeout: 1)
        )
        let waits = Shared<[Int64]>([])
        let release = Shared<Bool>(false)
        let ranOut = Shared<Bool>(false)
        let model = MusicRoomLyricsModel(
            room: "living room",
            store: MusicLyricsStore(container: container, clock: { 0 }),
            nudges: MusicLyricsNudgeStore(place: SavedStateBox()),
            clock: { 0 },
            wait: { ms in
                guard ms == MusicRoomLyricsModel.followResumeMs else {
                    // The republish loop. Parked for the test's life: this
                    // file asserts nothing about the position.
                    try await Task.sleep(for: .seconds(3_600))
                    return
                }
                waits.mutate { $0.append(ms) }
                guard parkTheResume else { return }
                // **Bounded**, like every wait in this suite, and `Task.sleep`
                // is what throws when the task is cancelled — the mechanism
                // these cases are about.
                for _ in 0..<500 {
                    if release.current { return }
                    try await Task.sleep(for: .milliseconds(2))
                }
                ranOut.set(true)
            }
        )
        return Rig(model: model, resumeWaits: waits, releaseResume: release, ranOut: ranOut)
    }

    /// Real time, so a task woken from a 2 ms sleep has certainly run.
    ///
    /// `Task.yield()` alone is not enough here: two thousand yields can
    /// finish in well under a millisecond, so a released park would not have
    /// woken yet and "it did not fire" would be a race the test won rather
    /// than a measurement.
    private func letTheResumeWakeIfItLives() async {
        for _ in 0..<50 {
            try? await Task.sleep(for: .milliseconds(2))
            await Task.yield()
        }
    }

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

    // MARK: - the number itself

    /// `FOLLOW_PAUSE_MS = 4_000L` (`LyricsPanel.kt:118`), to the millisecond.
    ///
    /// Pinned on its own because every other case in this file asks the model
    /// what its own constant is, and would pass just as happily with a
    /// four-minute timer.
    func testTheResumeIsAndroidsFourSeconds() {
        XCTAssertEqual(4_000, MusicRoomLyricsModel.followResumeMs)
    }

    // MARK: - the drag

    /// A finger on the list pauses following and starts **no** timer.
    ///
    /// This is the half that keeps the panel from being yanked out from under
    /// someone: Android starts its `delay` on `Stop`, not on `Start`
    /// (`LyricsPanel.kt:476-483`), so a drag held for a minute is a minute
    /// with nothing counting down.
    func testADragPausesFollowingAndStartsNothingWhileTheFingerIsDown() async {
        let rig = rig(parkTheResume: true)
        rig.model.start()
        defer { rig.model.stop() }
        XCTAssertTrue(rig.model.following, "a panel follows until something stops it")

        rig.model.handPaused()
        XCTAssertFalse(rig.model.following)
        XCTAssertEqual([], rig.resumeWaits.current, "a pause must not start the resume")

        // A second `.interacting` inside one gesture, which is what
        // `onScrollPhaseChange` can deliver: still nothing counting down.
        rig.model.handPaused()
        await letTheResumeWakeIfItLives()
        XCTAssertFalse(rig.model.following, "the list came back with a finger still on it")
        XCTAssertEqual([], rig.resumeWaits.current)
    }

    /// The drag ending starts exactly one four-second resume, and following
    /// comes back by itself when it fires.
    func testTheDragEndingStartsOneFourSecondResumeThatComesBackByItself() async {
        let rig = rig(parkTheResume: false)
        rig.model.start()
        defer { rig.model.stop() }

        rig.model.handPaused()
        rig.model.handReleased()
        await settleUntil({ rig.model.following }, "the four-second resume")
        XCTAssertEqual(
            [MusicRoomLyricsModel.followResumeMs], rig.resumeWaits.current,
            "one resume, and it waited the four seconds Android waits"
        )
    }

    /// **A phase change with no drag behind it starts nothing.**
    ///
    /// The panel scrolls itself (`scrollTo` on the active line), and that
    /// produces `.animating` and then `.idle` — which the view reports as a
    /// release, because `.interacting` is the only phase that means a finger.
    /// A model that took every such phase for a drag ending would start a
    /// timer on its own scrolling, and then turn following on from a scroll
    /// that happened because following was already on.
    func testAReleaseWithNoDragBeforeItStartsNothing() async {
        let rig = rig(parkTheResume: false)
        rig.model.start()
        defer { rig.model.stop() }

        rig.model.handReleased()
        rig.model.handReleased()
        await letTheResumeWakeIfItLives()
        XCTAssertEqual([], rig.resumeWaits.current, "the panel's own scroll started a timer")
        XCTAssertTrue(rig.model.following, "and it was on the whole time")

        // And after a real drag has been served, the trailing phases of one
        // flick — `.decelerating`, then `.idle` — do not start a second one.
        rig.model.handPaused()
        rig.model.handReleased()
        rig.model.handReleased()
        await settleUntil({ rig.model.following }, "the resume")
        XCTAssertEqual(
            1, rig.resumeWaits.current.count,
            "one flick is one resume, however many phases it reports"
        )
    }

    // MARK: - everything that cancels it

    /// A finger back down inside the four seconds cancels the resume, so the
    /// list is not pulled away mid-drag — `resume?.cancel()` on
    /// `DragInteraction.Start` (`LyricsPanel.kt:473`).
    func testAFingerBackDownInsideTheFourSecondsCancelsTheResume() async {
        let rig = rig(parkTheResume: true)
        rig.model.start()
        defer { rig.model.stop() }

        rig.model.handPaused()
        rig.model.handReleased()
        await settleUntil({ !rig.resumeWaits.current.isEmpty }, "the resume starting")

        // The finger is back down, and following is off again. Releasing the
        // park now is what tells a cancelled resume from a sleeping one: an
        // uncancelled task would wake and turn following on with a finger on
        // the list, which is the yank.
        rig.model.handPaused()
        rig.letTheResumeFinish()
        await letTheResumeWakeIfItLives()
        XCTAssertFalse(
            rig.model.following,
            "the first drag's resume fired while a second finger was on the list"
        )
        XCTAssertFalse(rig.ranOut.current, "the parked resume ran its bound out")

        // The second drag gets its own resume when it ends: cancelling one is
        // not switching the feature off.
        rig.model.handReleased()
        await settleUntil({ rig.resumeWaits.current.count == 2 }, "the second resume")
    }

    /// The panel going away takes the resume with it: a `Task` the model owns
    /// is cancelled in ``MusicRoomLyricsModel/stop()`` with the other two.
    func testThePanelGoingAwayCancelsThePendingResume() async {
        let rig = rig(parkTheResume: true)
        rig.model.start()
        rig.model.handPaused()
        rig.model.handReleased()
        await settleUntil({ !rig.resumeWaits.current.isEmpty }, "the resume starting")

        rig.model.stop()
        rig.letTheResumeFinish()
        await letTheResumeWakeIfItLives()
        XCTAssertFalse(rig.model.following, "a panel that is gone published into itself")
        XCTAssertFalse(rig.ranOut.current)
    }

    /// Moving house cancels it: the drag that paused following was on the old
    /// house's words.
    func testMovingHouseCancelsThePendingResume() async {
        let rig = rig(parkTheResume: true)
        rig.model.start()
        defer { rig.model.stop() }
        rig.model.handPaused()
        rig.model.handReleased()
        await settleUntil({ !rig.resumeWaits.current.isEmpty }, "the resume starting")

        rig.model.serverChanged()
        XCTAssertTrue(rig.model.following, "a new house's words follow from the top")
        // Pause again, so the stale resume has something to change, then let
        // it wake.
        rig.model.handPaused()
        rig.letTheResumeFinish()
        await letTheResumeWakeIfItLives()
        XCTAssertFalse(
            rig.model.following,
            "the old house's resume was still running and turned following on"
        )
        XCTAssertFalse(rig.ranOut.current)
    }

    /// A new song cancels it, for the same reason and by the same two lines.
    func testANewSongCancelsThePendingResume() async {
        let rig = rig(parkTheResume: true)
        rig.model.start()
        defer { rig.model.stop() }
        rig.model.handPaused()
        rig.model.handReleased()
        await settleUntil({ !rig.resumeWaits.current.isEmpty }, "the resume starting")

        rig.model.offer(trackID: 99)
        XCTAssertTrue(rig.model.following, "a new song's words follow from the top")
        rig.model.handPaused()
        rig.letTheResumeFinish()
        await letTheResumeWakeIfItLives()
        XCTAssertFalse(rig.model.following, "the old song's resume was still running")
        XCTAssertFalse(rig.ranOut.current)
    }

    /// **The chip is kept** (Android ships it too, `:604`), it works at once,
    /// and it cancels the timer rather than racing it.
    func testTheFollowChipComesBackAtOnceAndCancelsTheTimer() async {
        let rig = rig(parkTheResume: true)
        rig.model.start()
        defer { rig.model.stop() }

        rig.model.handPaused()
        rig.model.handReleased()
        await settleUntil({ !rig.resumeWaits.current.isEmpty }, "the resume starting")

        rig.model.follow()
        XCTAssertTrue(rig.model.following, "the chip waits for nothing")

        // A fresh drag right after the chip. The resume the chip cancelled
        // must not come back and undo it.
        rig.model.handPaused()
        rig.letTheResumeFinish()
        await letTheResumeWakeIfItLives()
        XCTAssertFalse(
            rig.model.following,
            "the resume the chip cancelled fired anyway, on top of a new drag"
        )
        XCTAssertEqual(
            1, rig.resumeWaits.current.count, "the chip started a resume of its own"
        )
        XCTAssertFalse(rig.ranOut.current)
    }

    /// The old name still pauses, so a caller written against it does not
    /// quietly lose the pause.
    func testScrolledByHandIsStillThePause() {
        let rig = rig(parkTheResume: true)
        rig.model.start()
        defer { rig.model.stop() }
        rig.model.scrolledByHand()
        XCTAssertFalse(rig.model.following)
        XCTAssertEqual([], rig.resumeWaits.current)
    }
}
