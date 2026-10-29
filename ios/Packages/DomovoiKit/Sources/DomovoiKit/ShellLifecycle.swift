import Foundation
import Synchronization

/// The scene phase, as a value this package can be given and a test can drive.
///
/// SwiftUI's `ScenePhase` is the real input; this is the same three cases
/// without the dependency, so the decision below is testable without a scene.
public enum ShellPhase: Equatable, Sendable {
    /// On screen and taking input. The only phase in which the server is
    /// asked.
    case active
    /// Visible but not taking input: the app switcher, Control Centre, a
    /// system alert. **Treated as off screen here** — see
    /// ``reachabilityAction(serverURL:connected:phase:)``.
    case inactive
    /// Off screen, and about to be suspended.
    case background
}

/// What the reachability loop should be doing.
public enum ReachabilityAction: Equatable, Sendable {
    /// Ask the server every ``reachProbeEveryMs``: there is a saved server,
    /// the live socket is down, and the app is on screen.
    case ask
    /// Stop asking and **keep what is owed**: the app left the screen. The
    /// foreground seconds already spent unanswered still count towards the
    /// grace when it comes back; the time away does not.
    case stopCounting
    /// Stop asking and **clear what is owed**: there is no server to ask, or
    /// the live socket is up, which is better evidence than any probe.
    case inReach
}

/// Whether the loop runs, in one function.
///
/// A port of the `LaunchedEffect(serverUrl, connected, shellLifecycle)` at
/// `android/.../ui/shell/AppShell.kt:130-148` (read 2026-10-29), which asks
/// its two questions in this order and then hands the rest to
/// `repeatOnLifecycle(Lifecycle.State.STARTED)`:
///
/// ```kotlin
/// if (serverUrl.isBlank() || connected) { reach.connected(); unreachable = false; return@LaunchedEffect }
/// shellLifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) { askWhileOnScreen(…) }
/// ```
///
/// **The order is ported, not just the conditions**: "no server, or the socket
/// is up" is answered before the lifecycle is consulted, so a backgrounded app
/// with the socket up owes nothing rather than owing a paused stretch.
///
/// ### Why `.inactive` counts as off screen
///
/// Android's `STARTED` means *visible*, which covers a dialog or the app
/// switcher. The nearest iOS reading would be "`.active` or `.inactive`". This
/// takes the stricter one, and the asymmetry is the reason:
///
///  - stopping **too eagerly** delays a true fallback by the length of the
///    interruption, because ``Reachability/pause(_:)`` keeps the seconds
///    already spent and the next stretch adds to them. Nothing is lost and
///    nothing is wrong on screen.
///  - stopping **too late** charges a suspension against a ten-second grace
///    and drops a person sitting beside a healthy server to local media.
///
/// One of those is a delay and the other is the bug this whole file is about.
/// `.inactive` is also the phase an app passes through on its way to
/// `.background`, so stopping there stops strictly before any suspension can
/// begin.
public func reachabilityAction(
    serverURL: String,
    connected: Bool,
    phase: ShellPhase
) -> ReachabilityAction {
    if ServerCredentials.isBlank(serverURL) || connected { return .inReach }
    return phase == .active ? .ask : .stopCounting
}

/// Runs ``askWhileOnScreen(reach:probe:clock:wait:publish:)`` while the app is
/// on screen, and **cancels it when the app leaves** — which is the whole
/// point of the type.
///
/// ### The hazard this discharges
///
/// ``askWhileOnScreen(reach:probe:clock:wait:publish:)`` calls
/// ``Reachability/resume(_:)`` on itself, so the stopwatch starts the moment
/// any caller starts the loop, and ``Reachability`` has no clock of its own —
/// it believes the wall clock it is handed. A suspended process's `Task` is
/// not cancelled, merely not running, so a loop nobody cancels **measures the
/// suspension**: two minutes in the background, one stale probe on the way
/// back, and ``Reachability/missed(_:)`` charges all two minutes against a ten
/// second grace. Android prevents it with `repeatOnLifecycle(STARTED)`;
/// this type is that line of lifecycle.
///
/// ### Cancellation alone would not be enough, and this is the subtle part
///
/// `Task.cancel()` returns at once; the cancelled task's `defer`, which calls
/// ``Reachability/pause(_:)``, runs only when that task is next scheduled. The
/// app is still alive at `.background`, so in practice it runs immediately —
/// but "in practice" is how the first version of this bug shipped, and the
/// timestamp it would pause with is the wall clock at that later moment. So
/// this type **pauses the stopwatch itself, synchronously, in the same call
/// that cancels**, and the task's own `defer` is then a no-op
/// (``Reachability/pause(_:)`` does nothing when nothing is being timed).
/// `testTheStopwatchStopsWhenThePhaseChangesNotWhenTheTaskNoticed` is that
/// claim.
///
/// ### Per server
///
/// Android holds the tracker in `remember(serverUrl)` and the verdict in
/// `remember(serverUrl) { mutableStateOf(false) }` (`AppShell.kt:127-128`), so
/// a server switch gets a fresh grace rather than inheriting the last
/// server's verdict. ``update(serverURL:connected:phase:)`` replaces the
/// tracker when the address changes, for the same reason.
public final class ReachabilityDriver: Sendable {

    private struct State {
        var serverURL: String?
        var task: Task<Void, Never>?
        var reach: Reachability
    }

    private let state: Mutex<State>
    private let graceMs: Int64
    private let probe: @Sendable () async -> Bool
    private let clock: @Sendable () -> Int64
    private let wait: @Sendable (Int64) async throws -> Void
    private let publish: @Sendable (Bool) -> Void

    /// - Parameters:
    ///   - probe: one question to the server. Android's `api.answers()`,
    ///     which answers false rather than throwing.
    ///   - clock: a monotonic millisecond clock. Android passes
    ///     `SystemClock::elapsedRealtime`; ``TokenHealth/monotonicMs`` is the
    ///     counterpart here. **It keeps running while the device sleeps on
    ///     both platforms**, which is exactly why the loop has to be
    ///     cancelled rather than trusted to notice.
    ///   - publish: the verdict after each probe, for the shell to read.
    public init(
        graceMs: Int64 = unreachableGraceMs,
        probe: @Sendable @escaping () async -> Bool,
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        wait: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) },
        publish: @Sendable @escaping (Bool) -> Void
    ) {
        self.graceMs = graceMs
        self.probe = probe
        self.clock = clock
        self.wait = wait
        self.publish = publish
        self.state = Mutex(State(serverURL: nil, task: nil, reach: Reachability(graceMs: graceMs)))
    }

    /// The verdict right now.
    public var unreachable: Bool { state.withLock { $0.reach.unreachable } }

    /// Whether the loop is running.
    public var isAsking: Bool { state.withLock { $0.task != nil } }

    /// The loop's task, for a test that needs to await its unwinding.
    var task: Task<Void, Never>? { state.withLock { $0.task } }

    /// The tracker, for a test that needs to read what is owed.
    var reach: Reachability { state.withLock { $0.reach } }

    /// Tell the driver what the world looks like: the saved server, the live
    /// socket, and the scene phase. Call it on every change of any of the
    /// three — it is idempotent, and starting an already-running loop does
    /// nothing.
    public func update(serverURL: String, connected: Bool, phase: ShellPhase) {
        let now = clock()
        let action = reachabilityAction(serverURL: serverURL, connected: connected, phase: phase)

        // Everything that touches the state happens inside the lock; what has
        // to be told to the world is decided there and said outside it, so no
        // closure of someone else's runs while the lock is held.
        let decision = state.withLock { current -> Decision in
            var cancelled: Task<Void, Never>?
            var tellReachable = false
            if current.serverURL != serverURL {
                // A different server: the old one's verdict and the old one's
                // owed seconds are not evidence about this one.
                current.serverURL = serverURL
                cancelled = current.task
                current.task = nil
                current.reach = Reachability(graceMs: graceMs)
                tellReachable = true
            }
            switch action {
            case .inReach:
                if cancelled == nil { cancelled = current.task }
                current.task = nil
                current.reach.connected()
                tellReachable = true
            case .stopCounting:
                if cancelled == nil { cancelled = current.task }
                current.task = nil
                // The stopwatch stops HERE, with this timestamp. See the note
                // on the type: the cancelled task's own `defer` may not run
                // until later, and by then the clock has moved.
                current.reach.pause(now)
            case .ask:
                return Decision(
                    cancelled: cancelled,
                    tellReachable: tellReachable,
                    start: current.task == nil ? current.reach : nil
                )
            }
            return Decision(cancelled: cancelled, tellReachable: tellReachable, start: nil)
        }

        decision.cancelled?.cancel()
        if decision.tellReachable { publish(false) }
        guard let reach = decision.start else { return }

        // Started outside the lock, then stored. `askWhileOnScreen` resumes
        // the stopwatch itself, so the stretch begins when the loop does.
        let task = Task { [probe, clock, wait, publish] in
            await askWhileOnScreen(reach: reach, probe: probe, clock: clock, wait: wait, publish: publish)
        }
        // Another caller may have got here first, or the phase may have
        // changed back while this one was between the two locks; whichever
        // task is not wanted is cancelled rather than left running.
        let stale: Task<Void, Never>? = state.withLock { current in
            guard current.task == nil else { return task }
            current.task = task
            return nil
        }
        stale?.cancel()
    }

    /// What ``update(serverURL:connected:phase:)`` decided inside the lock and
    /// carries out after it.
    private struct Decision {
        let cancelled: Task<Void, Never>?
        let tellReachable: Bool
        /// The tracker to start a loop against, or nil for "do not start one".
        let start: Reachability?
    }

    /// Stop for good: the shell is going away. Keeps nothing.
    public func stop() {
        let cancelled = state.withLock { current -> Task<Void, Never>? in
            let task = current.task
            current.task = nil
            current.reach.pause(clock())
            return task
        }
        cancelled?.cancel()
    }
}

/// When the shell may introduce this install to the server again (map §9.2,
/// item 2).
///
/// ``DeviceRegistry/shouldRegister(now:lastAskedAt:reason:connected:paired:)``
/// is the rule and says in its own documentation that I1-C drives it from the
/// scene phase. This is the driving: the one piece of memory Android keeps as
/// `var lastAsked` outside its `repeatOnLifecycle` block
/// (`AppShell.kt:365-378`), so coming back to the app repeatedly re-asks at
/// most once every ``DeviceRegistry/reaskGapMs``.
///
/// Why it matters, in Android's words: nothing pushes an admin's
/// shared-screen change, and a wall tablet never leaves Home. Asking is an
/// upsert, so asking is cheap; asking on every return is not.
public final class RegisterCadence: Sendable {

    private let last = Mutex<Int64?>(nil)

    public init() {}

    /// When it last asked, or nil if it never has.
    public var lastAskedAt: Int64? { last.withLock { $0 } }

    /// Whether to ask now — **and, if so, records that it did**, so two
    /// callers in the same moment cannot both ask.
    public func askNow(
        now: Int64,
        reason: DeviceRegistry.ReaskReason,
        connected: Bool,
        paired: Bool
    ) -> Bool {
        last.withLock { lastAskedAt in
            let should = DeviceRegistry.shouldRegister(
                now: now,
                lastAskedAt: lastAskedAt,
                reason: reason,
                connected: connected,
                paired: paired
            )
            if should { lastAskedAt = now }
            return should
        }
    }
}
