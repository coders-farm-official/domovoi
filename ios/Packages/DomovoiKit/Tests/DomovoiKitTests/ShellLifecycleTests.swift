import XCTest
@testable import DomovoiKit

/// The lifecycle: when the server is asked, when the asking **stops**, and
/// what that costs when it does not (``reachabilityAction(serverURL:connected:phase:)``,
/// ``ReachabilityDriver``, ``RegisterCadence``).
///
/// This file is the discharge of the hazard part 1 could only write down.
/// `ReachabilityTests` covers the tracker and the loop; this covers the thing
/// that starts and cancels the loop, which is the half Android gets from
/// `repeatOnLifecycle(STARTED)` and which did not exist here until now.
final class ShellLifecycleTests: XCTestCase {

    private let server = "http://10.0.0.42:6369"

    // MARK: - the decision

    /// Android asks its two questions before it consults the lifecycle at
    /// all, and the order is ported with them.
    func testNoServerOrALiveSocketMeansNothingIsOwedWhateverThePhaseIs() {
        for phase in [ShellPhase.active, .inactive, .background] {
            XCTAssertEqual(.inReach, reachabilityAction(serverURL: "", connected: false, phase: phase))
            XCTAssertEqual(.inReach, reachabilityAction(serverURL: "   ", connected: false, phase: phase))
            XCTAssertEqual(.inReach, reachabilityAction(serverURL: server, connected: true, phase: phase))
        }
    }

    /// On screen with a saved server and the socket down: ask. Off screen:
    /// stop, and keep what is owed.
    func testTheServerIsAskedOnlyWhileTheAppIsOnScreen() {
        XCTAssertEqual(.ask, reachabilityAction(serverURL: server, connected: false, phase: .active))
        XCTAssertEqual(.stopCounting, reachabilityAction(serverURL: server, connected: false, phase: .inactive))
        XCTAssertEqual(.stopCounting, reachabilityAction(serverURL: server, connected: false, phase: .background))
    }

    // MARK: - the driver

    /// A loop driven by a fake clock, a fake probe and a `wait` that parks.
    ///
    /// The same trick as `ReachabilityTests.Rig` and Android's: the stretch
    /// ends deterministically because the loop's `wait` sets `parked` and then
    /// sleeps for an hour, and the test only goes on once `parked` is set.
    /// Nothing here depends on how two tasks interleave.
    private final class Rig {
        let clock = Shared<Int64>(0)
        let probes = Shared<Int>(0)
        let verdicts = Shared<[Bool]>([])
        let parked = Shared<Bool>(false)
        let driver: ReachabilityDriver

        /// - Parameter answers: whether the probe at this index answers.
        init(graceMs: Int64 = unreachableGraceMs, answers: @Sendable @escaping (Int) -> Bool) {
            let clock = self.clock
            let probes = self.probes
            let verdicts = self.verdicts
            let parked = self.parked
            driver = ReachabilityDriver(
                graceMs: graceMs,
                probe: {
                    clock.advance(300)
                    let index = probes.mutate { (n: inout Int) -> Int in
                        let was = n
                        n += 1
                        return was
                    }
                    return answers(index)
                },
                clock: { clock.current },
                wait: { _ in
                    parked.set(true)
                    try await Task.sleep(for: .seconds(3600))
                },
                publish: { verdict in verdicts.mutate { $0.append(verdict) } }
            )
        }

        /// Wait until the loop has probed and is sitting in its `wait`.
        func settle() async {
            while !parked.current { await Task.yield() }
            parked.set(false)
        }
    }

    /// **The test the whole file is for**, and the replacement for part 1's
    /// standing negative one.
    ///
    /// Two minutes in the background with the server fine: the loop is
    /// cancelled on the way out, the suspension is not measured, and the
    /// stale first probe on the way back — a request on a torn-down
    /// connection — costs nothing. The twin of Android's
    /// `ReachabilityTest.twoMinutesInTheBackgroundWithTheServerFineStays
    /// OnTheWorkspace`, with the lifecycle wiring in it rather than assumed.
    ///
    /// Watched failing by deleting the `.stopCounting` branch's cancel and
    /// pause from ``ReachabilityDriver/update(serverURL:connected:phase:)``:
    /// the verdicts become `[false, true]` and `unreachable` goes true, which
    /// is exactly what the negative test used to pin.
    func testTwoMinutesInTheBackgroundCostsNothing() async {
        // The server answers the first time (it is healthy and always was)
        // and fails on the first probe after the thaw.
        let rig = Rig(answers: { $0 == 0 })
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        // Two: adopting a server tells the shell it is reachable (a fresh
        // grace, not last server's verdict), and then the probe answers.
        XCTAssertEqual([false, false], rig.verdicts.current)

        // Off screen. The process then freezes for two minutes.
        rig.driver.update(serverURL: server, connected: false, phase: .background)
        XCTAssertFalse(rig.driver.isAsking)
        rig.clock.advance(120_000)

        // Back on screen: a fresh probe runs first, and it fails.
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        XCTAssertEqual([false, false, false], rig.verdicts.current, "two minutes away is not evidence")
        XCTAssertFalse(rig.driver.unreachable)
        XCTAssertEqual(2, rig.probes.current, "one probe on the way back, before any verdict")
    }

    /// The stopwatch stops **in the call that changes the phase**, not when
    /// the cancelled task gets round to noticing.
    ///
    /// `Task.cancel()` returns at once and the loop's own `defer` runs later,
    /// with a later timestamp; by then the clock has moved. So the pause is
    /// synchronous, and this is how that is visible: after the phase change,
    /// a missed probe charged with a wall-clock time two minutes on is
    /// ignored, because nothing is being timed.
    func testTheStopwatchStopsWhenThePhaseChangesNotWhenTheTaskNoticed() async {
        let rig = Rig(answers: { _ in false })
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        rig.driver.update(serverURL: server, connected: false, phase: .background)
        rig.clock.advance(120_000)
        // A result that lands while the app is away is no evidence either
        // way — and this one could not even be charged, because the
        // stopwatch is already stopped.
        rig.driver.reach.missed(rig.clock.current)
        XCTAssertFalse(rig.driver.unreachable)
    }

    /// `.inactive` stops the asking too, and the seconds already spent are
    /// **kept**: a short interruption delays the fallback, it does not undo
    /// it. The grace is 10 s here, so 9 s on screen plus 2 s after the
    /// interruption is still a fallback.
    func testAnInterruptionKeepsWhatIsOwedRatherThanClearingIt() async {
        let rig = Rig(answers: { _ in false })
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        // 300 ms of probe; push the stretch to 9 s of asking.
        rig.clock.advance(8_700)
        rig.driver.update(serverURL: server, connected: false, phase: .inactive)
        XCTAssertFalse(rig.driver.unreachable)

        // Ten minutes in the app switcher, then back.
        rig.clock.advance(600_000)
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        XCTAssertFalse(rig.driver.unreachable, "9.3 s on screen, whatever the clock says")
        rig.clock.advance(1_000)
        rig.driver.reach.missed(rig.clock.current)
        XCTAssertTrue(rig.driver.unreachable, "and the 9 s already owed still count")
    }

    /// **A cancelled loop's `defer` must not close a stretch it did not
    /// open.** The driver pauses synchronously and starts the next loop at
    /// once; the cancelled task's `defer` runs some time after that, so by
    /// then the tracker is asking again. Unticketed, that late `pause` closes
    /// the new stretch, every later ``Reachability/missed(_:)`` returns
    /// early, and a server that really is gone never falls back.
    ///
    /// Found by `testAnInterruptionKeepsWhatIsOwedRatherThanClearingIt`
    /// failing on the Mac, which is why the ticket exists; this is the same
    /// thing without the race, so it cannot pass by luck.
    func testALatePauseFromACancelledLoopIsIgnored() {
        let reach = Reachability(graceMs: 10_000)
        let first = reach.resume(0)
        reach.pause(9_000, stretch: first)       // the driver, on the way out
        let second = reach.resume(600_000)       // the next loop, on the way in
        XCTAssertNotEqual(first, second)
        reach.pause(600_000, stretch: first)     // the cancelled loop's `defer`
        reach.missed(601_300)
        XCTAssertTrue(reach.unreachable, "9 s owed plus 1.3 s is past a 10 s grace")
        // And the driver's own untokened call still closes whatever is open.
        let third = Reachability(graceMs: 10_000)
        third.resume(0)
        third.pause(5_000)
        third.missed(600_000)
        XCTAssertFalse(third.unreachable, "nothing is being timed")
    }

    /// The live socket coming up is better evidence than any probe: the loop
    /// stops and what was owed is **cleared**, so the verdict goes back to
    /// reachable at once.
    func testTheSocketComingUpClearsEverything() async {
        let rig = Rig(answers: { _ in false })
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        rig.clock.advance(20_000)
        rig.driver.reach.missed(rig.clock.current)
        XCTAssertTrue(rig.driver.unreachable)

        rig.driver.update(serverURL: server, connected: true, phase: .active)
        XCTAssertFalse(rig.driver.isAsking)
        XCTAssertFalse(rig.driver.unreachable)
        XCTAssertEqual(false, rig.verdicts.current.last, "and the shell is told")
    }

    /// A server switch gets a fresh grace rather than inheriting the last
    /// server's verdict — Android's `remember(serverUrl)`.
    func testASwitchGetsAFreshGraceAndNotTheLastServersVerdict() async {
        let rig = Rig(answers: { _ in false })
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        rig.clock.advance(20_000)
        rig.driver.reach.missed(rig.clock.current)
        XCTAssertTrue(rig.driver.unreachable)

        rig.driver.update(serverURL: "http://10.0.0.7:6369", connected: false, phase: .active)
        XCTAssertFalse(rig.driver.unreachable, "the old server's silence says nothing about this one")
        XCTAssertEqual(false, rig.verdicts.current.last)
        await rig.settle()
        XCTAssertFalse(rig.driver.unreachable)
    }

    /// Being told the same thing twice does not start a second loop.
    func testStartingAnAlreadyRunningLoopDoesNothing() async {
        let rig = Rig(answers: { _ in true })
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        let probes = rig.probes.current
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        XCTAssertTrue(rig.driver.isAsking)
        XCTAssertEqual(probes, rig.probes.current, "no second loop, so no second probe")
    }

    /// `stop()` is the shell going away: nothing is asked afterwards.
    func testStopEndsIt() async {
        let rig = Rig(answers: { _ in true })
        rig.driver.update(serverURL: server, connected: false, phase: .active)
        await rig.settle()
        rig.driver.stop()
        XCTAssertFalse(rig.driver.isAsking)
    }

    // MARK: - the device register's cadence (map §9.2)

    /// Coming back to the app repeatedly re-asks at most once every 15 s,
    /// and the on-screen poll at most once every two minutes. The gap is
    /// Android's `lastAsked`, which lives outside its `repeatOnLifecycle`
    /// block for exactly this reason.
    func testComingBackRepeatedlyDoesNotHammerTheRegister() {
        let cadence = RegisterCadence()
        XCTAssertNil(cadence.lastAskedAt)
        XCTAssertTrue(cadence.askNow(now: 1_000, reason: .changed, connected: true, paired: true))
        XCTAssertEqual(1_000, cadence.lastAskedAt)
        XCTAssertFalse(cadence.askNow(now: 5_000, reason: .changed, connected: true, paired: true))
        XCTAssertFalse(cadence.askNow(now: 15_999, reason: .changed, connected: true, paired: true))
        XCTAssertTrue(cadence.askNow(now: 16_000, reason: .changed, connected: true, paired: true))
        // The poll's own cadence is the longer one.
        XCTAssertFalse(cadence.askNow(now: 100_000, reason: .onScreenTick, connected: true, paired: true))
        XCTAssertTrue(cadence.askNow(now: 136_000, reason: .onScreenTick, connected: true, paired: true))
    }

    /// An install that is neither connected nor paired is never asked for:
    /// its registration would only be refused
    /// (``DeviceRegistry/canRegister(connected:paired:)``).
    func testAnUnpairedPhoneWithNoSocketIsNeverRegistered() {
        let cadence = RegisterCadence()
        XCTAssertFalse(cadence.askNow(now: 1_000, reason: .changed, connected: false, paired: false))
        XCTAssertNil(cadence.lastAskedAt, "a refusal is not an ask, so it does not start the gap")
        // Paired but with no socket **is** asked: that is how a freshly
        // paired kitchen tablet learns it is a shared screen.
        XCTAssertTrue(cadence.askNow(now: 1_000, reason: .changed, connected: false, paired: true))
    }
}
