import XCTest
@testable import DomovoiKit

/// When the app drops to local media because the saved server is out of reach
/// (``Reachability``, ``askWhileOnScreen(reach:probe:clock:wait:publish:)``).
///
/// A port of `android/.../test/ui/shell/ReachabilityTest.kt` case for case.
/// The grace counts only time the app spent on screen asking: a phone that
/// sat 30 s behind the system folder picker — frozen, its socket missing a
/// ping, the first request after the thaw failing on a stale connection —
/// used to come back to local media next to a healthy server, because the
/// wall clock said the grace was long over. The real fallback (on screen, out
/// of the house) still happens.
///
/// iOS has the same hazard in a different shape — a suspended app's
/// connections are torn down or go stale — which is why the rule is ported
/// even though `repeatOnLifecycle` is not. Here "the app left the screen" is
/// a cancelled `Task`.
final class ReachabilityTests: XCTestCase {

    // MARK: - the tracker

    /// `onScreenWithTheServerGoneItFallsBackAfterTheGrace`
    func testOnScreenWithTheServerGoneItFallsBackAfterTheGrace() {
        let r = Reachability(graceMs: 10_000)
        r.resume(0)
        r.missed(5_000)
        XCTAssertFalse(r.unreachable)
        r.missed(9_999)
        XCTAssertFalse(r.unreachable, "not a moment before the grace")
        r.missed(10_000)
        XCTAssertTrue(r.unreachable)
    }

    /// `timeInTheBackgroundIsNotCountedButTimeOnScreenIs`
    func testTimeInTheBackgroundIsNotCountedButTimeOnScreenIs() {
        let r = Reachability(graceMs: 10_000)
        r.resume(0)
        r.missed(6_000)
        r.pause(6_000) // six seconds of asking, then the app left the screen
        r.resume(606_000) // ten minutes later
        r.missed(606_500)
        XCTAssertFalse(r.unreachable, "6.5 s on screen, whatever the clock says")
        r.missed(610_000)
        XCTAssertTrue(r.unreachable, "10 s on screen")
    }

    /// `anAnswerLandingWhileTheAppIsAwayCountsForNothing`
    func testAnAnswerLandingWhileTheAppIsAwayCountsForNothing() {
        let r = Reachability(graceMs: 10_000)
        r.resume(0)
        r.pause(1_000)
        r.missed(200_000)
        XCTAssertFalse(r.unreachable)
    }

    /// `outOfReachStaysSoThroughABackgroundSpellUntilTheServerAnswers`
    func testOutOfReachStaysSoThroughABackgroundSpellUntilTheServerAnswers() {
        let r = Reachability(graceMs: 10_000)
        r.resume(0)
        r.missed(12_000)
        XCTAssertTrue(r.unreachable)
        r.pause(13_000)
        r.resume(200_000)
        XCTAssertTrue(r.unreachable, "coming back is not an answer")
        r.missed(200_500)
        XCTAssertTrue(r.unreachable)
        r.answered(204_500)
        XCTAssertFalse(r.unreachable)
    }

    /// `anAnswerOrTheSocketStartsTheGraceOver`
    func testAnAnswerOrTheSocketStartsTheGraceOver() {
        let r = Reachability(graceMs: 10_000)
        r.resume(0)
        r.missed(9_000)
        r.answered(9_500)
        r.missed(15_000)
        XCTAssertFalse(r.unreachable, "5.5 s since the answer")
        r.missed(19_500)
        XCTAssertTrue(r.unreachable)
        r.connected()
        XCTAssertFalse(r.unreachable)
        r.resume(30_000)
        r.missed(35_000)
        XCTAssertFalse(r.unreachable, "the socket wiped what was owed")
    }

    // MARK: - the loop, which part 2 runs while the app is on screen

    /// The counterpart of the Kotlin `Rig`.
    ///
    /// Android's rig drives the loop with a `wait` that fakes the clock and
    /// ends the stretch by suspending for ever; the same trick works here,
    /// with a cancelled `Task` for ON_STOP.
    private final class Rig {
        let clock = Shared<Int64>(0)
        let reach = Reachability(graceMs: unreachableGraceMs)
        let verdicts = Shared<[Bool]>([])
        let probes = Shared<Int>(0)
        /// On screen for `count` probes answering `answers` (each probe takes
        /// `probeMs` of clock); then the app leaves.
        ///
        /// The stretch ends deterministically: the `count`-th `wait` sets
        /// `parked` and then sleeps for an hour, and the test's side only
        /// cancels once `parked` is set. Nothing here depends on how the
        /// cooperative pool interleaves two tasks.
        func onScreen(_ count: Int, answers: @Sendable @escaping (Int) -> Bool, probeMs: Int64 = 300) async {
            let clock = self.clock
            let verdicts = self.verdicts
            let probes = self.probes
            let reach = self.reach
            let remaining = Shared<Int>(count)
            let parked = Shared<Bool>(false)
            let task = Task {
                await askWhileOnScreen(
                    reach: reach,
                    probe: {
                        clock.advance(probeMs)
                        let index = probes.mutate { (n: inout Int) -> Int in
                            let was = n
                            n += 1
                            return was
                        }
                        return answers(index)
                    },
                    clock: { clock.current },
                    wait: { ms in
                        let left = remaining.mutate { (n: inout Int) -> Int in
                            n -= 1
                            return n
                        }
                        if left <= 0 {
                            // ON_STOP: the stretch ends here, with the loop
                            // waiting. Android's rig does the same with
                            // `awaitCancellation()`.
                            parked.set(true)
                            try await Task.sleep(for: .seconds(3600))
                        } else {
                            clock.advance(ms)
                        }
                    },
                    publish: { verdict in verdicts.mutate { $0.append(verdict) } }
                )
            }
            while !parked.current { await Task.yield() }
            task.cancel()
            await task.value
        }

        /// The app is suspended **without the loop being cancelled**: the
        /// process freezes while the loop sits in its `wait`, so the clock
        /// jumps forward with the stopwatch still running, and the loop
        /// carries straight on when it thaws.
        ///
        /// The first probe answers (the server is fine and always was); the
        /// one after the thaw fails, as a request on a torn-down connection
        /// does. Then the stretch parks, as `onScreen`'s does.
        func suspendedWithoutCancelling(suspensionMs: Int64) async {
            let clock = self.clock
            let verdicts = self.verdicts
            let probes = self.probes
            let reach = self.reach
            let parked = Shared<Bool>(false)
            let task = Task {
                await askWhileOnScreen(
                    reach: reach,
                    probe: {
                        clock.advance(300)
                        let index = probes.mutate { (n: inout Int) -> Int in
                            let was = n
                            n += 1
                            return was
                        }
                        // Probe 0: on screen, the server answers. Probe 1:
                        // the first one after the thaw, failing on a stale
                        // connection. The server was healthy throughout.
                        return index == 0
                    },
                    clock: { clock.current },
                    wait: { _ in
                        if probes.current == 1 {
                            // The process freezes HERE, for `suspensionMs`.
                            // Nothing cancelled the task, so nothing called
                            // `pause`.
                            clock.advance(suspensionMs)
                        } else {
                            parked.set(true)
                            try await Task.sleep(for: .seconds(3600))
                        }
                    },
                    publish: { verdict in verdicts.mutate { $0.append(verdict) } }
                )
            }
            while !parked.current { await Task.yield() }
            task.cancel()
            await task.value
        }

        /// On screen with a probe out when the app leaves: it never answers.
        func leavesMidProbe() async {
            let clock = self.clock
            let verdicts = self.verdicts
            let reach = self.reach
            let started = Shared<Bool>(false)
            let task = Task {
                await askWhileOnScreen(
                    reach: reach,
                    probe: {
                        started.set(true)
                        // A probe that outlives the trip: it returns only
                        // once the task has been cancelled, which is what a
                        // cooperatively-cancelled call does — and the answer
                        // it then gives must count for nothing.
                        while !Task.isCancelled { await Task.yield() }
                        return false
                    },
                    clock: { clock.current },
                    wait: { _ in },
                    publish: { verdict in verdicts.mutate { $0.append(verdict) } }
                )
            }
            while !started.current { await Task.yield() }
            task.cancel()
            await task.value
        }
    }

    /// `twoMinutesInTheBackgroundWithTheServerFineStaysOnTheWorkspace` — the
    /// test the whole design is for, and the one that fails if the straddling
    /// probe is not dropped.
    func testTwoMinutesInTheBackgroundWithTheServerFineStaysOnTheWorkspace() async {
        let rig = Rig()
        // The socket died while the app was away (the folder picker in front,
        // the process frozen); a probe was out when it left.
        await rig.leavesMidProbe()
        XCTAssertEqual([], rig.verdicts.current, "a probe that straddled the trip is no evidence either way")
        rig.clock.advance(120_000)
        // Back on screen: the first probe fails on a stale connection, the
        // next one gets through.
        await rig.onScreen(3, answers: { $0 != 0 })
        XCTAssertEqual([false, false, false], rig.verdicts.current)
        XCTAssertFalse(rig.reach.unreachable)
    }

    /// **The hazard part 2 has to prevent, written as the thing that happens
    /// if it does not.**
    ///
    /// The test above is the one the design is for: the app leaves the screen,
    /// the loop is cancelled, two minutes pass, and the stale first probe on
    /// the way back costs nothing. This one is the same two minutes with the
    /// **loop left running** — which is what part 2 will have built if it
    /// starts `askWhileOnScreen` in a long-lived `Task` and does not cancel it
    /// when `scenePhase` leaves `.active`.
    ///
    /// The result is the bug, and it is asserted here rather than described in
    /// a comment: the server answered, was never asked again until the thaw,
    /// and one failed probe reports it out of reach — because
    /// ``askWhileOnScreen(reach:probe:clock:wait:publish:)`` takes
    /// ``Reachability/resume(_:)`` on itself, so the stopwatch ran for the
    /// whole suspension and `missed` charged all of it against a ten-second
    /// grace. Android's
    /// `ReachabilityTest.twoMinutesInTheBackgroundWithTheServerFineStaysOnTheWorkspace`
    /// exists to stop this reaching a person.
    ///
    /// **If this test ever fails, read it before changing it.** Two different
    /// changes make it red, and they want opposite answers:
    ///
    ///  - a batch that makes the tracker itself notice a suspension (a
    ///    monotonic clock that stops when the process does, say) has *fixed*
    ///    this: replace the assertion with the fixed behaviour and say so;
    ///  - a batch that merely moved the `resume`/`pause` calls around has
    ///    changed where the obligation lives, and the note on
    ///    ``Reachability`` has to move with it.
    ///
    /// What it cannot do is fail when part 2 forgets the cancellation — that
    /// would need part 2's lifecycle wiring, which does not exist yet. It
    /// pins the hazard so the cancellation cannot be removed as a tidy-up by
    /// someone who believes the stopwatch stops by itself.
    func testAnUncancelledLoopMeasuresTheSuspensionWhichPartTwoMustPrevent() async {
        let rig = Rig()
        await rig.suspendedWithoutCancelling(suspensionMs: 120_000)
        XCTAssertEqual(
            [false, true], rig.verdicts.current,
            "answered, then two minutes suspended, then one stale probe — and the verdict flips"
        )
        XCTAssertTrue(
            rig.reach.unreachable,
            "the whole suspension was charged against a 10 s grace: this is what the missing cancel costs"
        )
        XCTAssertEqual(2, rig.probes.current, "the server was asked twice and answered the first time")
    }

    /// `backOnScreenAFreshProbeRunsFirst`
    func testBackOnScreenAFreshProbeRunsFirst() async {
        let rig = Rig()
        await rig.onScreen(1, answers: { _ in false })
        rig.clock.advance(120_000)
        let before = rig.probes.current
        await rig.onScreen(1, answers: { _ in true })
        XCTAssertEqual(before + 1, rig.probes.current, "one probe on the way back, before any verdict")
        XCTAssertEqual(false, rig.verdicts.current.last)
    }

    /// `onScreenWithTheServerGoneTheLoopFallsBackAfterTheGrace`
    func testOnScreenWithTheServerGoneTheLoopFallsBackAfterTheGrace() async {
        let rig = Rig()
        // 300 ms probes every 4 s: answers at 0.3, 4.6, 8.9, 13.2 s.
        await rig.onScreen(5, answers: { _ in false })
        XCTAssertEqual([false, false, false, true, true], rig.verdicts.current)
        XCTAssertTrue(rig.reach.unreachable)
    }

    /// `anAnswerBringsTheWorkspaceBack`
    func testAnAnswerBringsTheWorkspaceBack() async {
        let rig = Rig()
        await rig.onScreen(6, answers: { $0 >= 5 })
        XCTAssertEqual([false, false, false, true, true, false], rig.verdicts.current)
    }

    /// The constants are Android's, and the loop uses them.
    func testTheNumbersAreAndroids() {
        XCTAssertEqual(10_000, unreachableGraceMs)
        XCTAssertEqual(4_000, reachProbeEveryMs)
    }
}
