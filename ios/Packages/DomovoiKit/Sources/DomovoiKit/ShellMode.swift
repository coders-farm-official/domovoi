import Foundation
import Synchronization

/// Which shell the app draws: the on-device one (the phone's own music,
/// videos and files; no Domovoi needed) or the full server workspace.
///
/// A port of `android/.../ui/shell/ShellMode.kt`. The screens are part 2's;
/// this file is the decision, and the decision is the part that has been got
/// wrong twice on Android and fixed twice from bug reports.
public enum ShellMode: Equatable, Sendable {
    case local
    case workspace
}

/// A saved server is not the same as a reachable one. Take the phone out of
/// the house and the workspace would spin on every panel until each request
/// timed out; instead, once the server has not answered for
/// ``unreachableGraceMs``, the app drops back to local media, and comes back
/// to the workspace the moment it answers again.
///
/// A pairing refusal is the exception: the server answered, it just wants the
/// household token, so the workspace stays up to show the pairing screen.
///
/// `preferLocal` is the person's own choice in the connection dialog ("this
/// phone"): it holds even at home with the server answering, until they pick
/// the server again. Choosing the server keeps the automatic fallback above.
///
/// **The order of the `if`s is the specification**, and two of them exist
/// because of bug reports:
///
///  - `preferLocal` is asked **before** `pairingRequired`, because the
///    pairing screen is a workspace screen and someone who chose the phone
///    did not ask to see it
///    (`ShellModeTest.choosingThePhoneBeatsAPairingRefusal`);
///  - `pairingRequired || tokenTrouble` is asked **before** `unreachable`,
///    because a held or refused token makes every request fail, so the
///    reachability probe reads the server as out of reach and would bounce
///    the person to local media with no explanation
///    (`TokenTroubleTest.theCardKeepsTheWorkspaceUpEvenIfTheProbesFail`).
///
/// - Parameters:
///   - identityChanged: the server proved a different key than the one
///     pinned (a reinstall, a new machine, or someone else's server at this
///     address): nothing is sent to it, and the local shell's banner asks
///     first (``identityChangedText(address:canTrustAgain:)``).
///   - tokenTrouble: the server refused this phone's token, or asked it to
///     wait (``tokenTrouble(serverURL:paired:rejected:waitMs:identityChanged:)``):
///     the workspace stays up to show the one card.
public func shellMode(
    serverURL: String,
    unreachable: Bool,
    pairingRequired: Bool,
    preferLocal: Bool = false,
    identityChanged: Bool = false,
    tokenTrouble: Bool = false
) -> ShellMode {
    if ServerCredentials.isBlank(serverURL) { return .local }
    if preferLocal { return .local }
    if identityChanged { return .local }
    if pairingRequired || tokenTrouble { return .workspace }
    if unreachable { return .local }
    return .workspace
}

// MARK: - the whole state machine, which Android spreads over two files

/// Why the local shell is showing — which is **not** which banner it draws.
///
/// Android's `shellMode` answers `Local` and throws the reason away; its
/// `OfflineShell` then re-derives a banner from the `ServerChoice` it is
/// handed separately (`AppShell.kt:632-652`, read 2026-10-29). Keeping the
/// reason here costs nothing and makes the seven leaves nameable, but it is
/// deliberately *not* the banner selector, because two of the banner's cases
/// are not inputs to `shellMode` at all:
///
///  - `Unproven` (something answered and proved nothing) and `Unavailable`
///    (the web backend answered, its core did not) set Android's `notOurs`
///    and `coreDown`, which go to `serverChoice` but **not** to `shellMode`.
///    They reach local media through ``outOfReach`` instead, because with the
///    token held every request fails and the reachability probe times out.
///    So ``outOfReach`` covers three banners, not one.
///
/// The banner is ``localBanner(for:canTrustAgain:)``, which takes a
/// ``ServerChoice``. This enum says which `if` in ``shellMode(serverURL:unreachable:pairingRequired:preferLocal:identityChanged:tokenTrouble:)``
/// fired, and nothing more.
public enum LocalReason: Equatable, Sendable {
    /// No server saved. Nothing is wrong and nothing opens by itself: a
    /// fresh install lands on its own music with a `connect` pill.
    case noServer

    /// The person chose "this phone" in the connection dialog. Sticky across
    /// restarts; "choosing 'this phone' with the server right there is not an
    /// outage", so the banner says nothing.
    case choseThisPhone

    /// The server proved a different key than the one pinned: an
    /// `IdentityVerdict.mismatch`. The one identity state `shellMode` is told
    /// about.
    case identityChanged

    /// The reachability probe gave up. Also where `Unproven` and
    /// `Unavailable` land, for the reason on this enum.
    case outOfReach
}

/// The **whole** state machine: `shellMode` plus the two early returns in
/// Android's `ShellContent` (`AppShell.kt:269-290`) that complete it.
///
/// Seven leaves. Android's four `Local` branches, and the three ways its
/// `Workspace` branch is then drawn:
///
/// | leaf | Android |
/// |---|---|
/// | `.localMedia(.noServer)` | `shellMode` branch 1 (map §2.2) |
/// | `.localMedia(.choseThisPhone)` | branch 2 (§2.3) |
/// | `.localMedia(.identityChanged)` | branch 3 (§2.5) |
/// | `.localMedia(.outOfReach)` | branch 5 (§2.4, and §2.5/§2.6 via the probe) |
/// | `.tokenTroubleCard` | `if (trouble != null)` (§2.8) |
/// | `.pairing` | `if (pairingRequired && !pairingDismissed)` (§2.7) |
/// | `.workspace` | the rest (§2.9) |
///
/// Room mode (§2.10) is **not** a leaf of this on either platform: it is a
/// separate activity on Android and will be a separate scene here, decided
/// before the container is built at all.
public enum ShellScreen: Equatable, Sendable {
    case localMedia(LocalReason)
    case tokenTroubleCard(TokenTrouble)
    case pairing
    case workspace
}

/// The seven-leaf decision, in the order the Android code decides it.
///
/// This calls ``shellMode(serverURL:unreachable:pairingRequired:preferLocal:identityChanged:tokenTrouble:)``
/// rather than repeating its `if`s, so there is exactly one copy of the
/// ordering that two bug reports paid for.
///
/// - Parameters:
///   - trouble: the token card to show, if any. Android derives it with
///     `tokenTrouble(…)` in `AppShell` and passes it down; the same value is
///     what makes `shellMode`'s `tokenTrouble` flag true, so it is passed
///     once here and used for both.
///   - pairingDismissed: the person tapped `not now`. Android holds this in
///     `remember(deviceToken)` — it resets on any token change and is **not**
///     saved across process death, so the pairing screen comes back
///     (map §1.5). Part 2 owns that lifetime; this function only reads it.
public func shellScreen(
    serverURL: String,
    unreachable: Bool,
    pairingRequired: Bool,
    preferLocal: Bool = false,
    identityChanged: Bool = false,
    trouble: TokenTrouble? = nil,
    pairingDismissed: Bool = false
) -> ShellScreen {
    let mode = shellMode(
        serverURL: serverURL,
        unreachable: unreachable,
        pairingRequired: pairingRequired,
        preferLocal: preferLocal,
        identityChanged: identityChanged,
        tokenTrouble: trouble != nil
    )
    if mode == .local {
        // The same four questions `shellMode` just asked, in the same order,
        // only to name which one answered. Re-asked rather than returned
        // from `shellMode` so that function stays a character-for-character
        // port of the Kotlin and its test file can be ported case for case.
        if ServerCredentials.isBlank(serverURL) { return .localMedia(.noServer) }
        if preferLocal { return .localMedia(.choseThisPhone) }
        if identityChanged { return .localMedia(.identityChanged) }
        return .localMedia(.outOfReach)
    }
    // `ShellContent`'s two early returns, in its order: the token card
    // first. It has to be first — a refused token and a pairing refusal are
    // both live at once (a refused token is why the server refuses the
    // request), and the card is the one that says what happened.
    if let trouble { return .tokenTroubleCard(trouble) }
    if pairingRequired && !pairingDismissed { return .pairing }
    return .workspace
}

// MARK: - the connection dialog's server side

/// Whether the connection dialog's server side can be picked right now, and
/// if not, why: it is greyed out with the reason rather than hidden.
public enum ServerChoice: Equatable, Sendable {
    /// No server saved yet: the picker below the switch is how to add one.
    case noServer

    /// Saved, but nothing has answered for the grace period.
    case unreachable(label: String)

    /// Something answers at the address but failed the identity proof
    /// (A6-03): an `IdentityVerdict.mismatch` **or** `.unproven`.
    case notOurs(label: String)

    /// The web backend answers but its core does not, so the proof could not
    /// be taken and the token is held until it can (`IdentityVerdict.unavailable`):
    /// starting, updating or busy — not an impostor, never a reason to forget
    /// it.
    case coreDown(label: String)

    /// Answering; `needsPairing` when it wants the household token first.
    case available(label: String, needsPairing: Bool = false)

    public var isEnabled: Bool {
        if case .available = self { return true }
        return false
    }

    /// The one-line status under the server side of the switch.
    public var reason: String {
        switch self {
        case .noServer: return "no server yet · pick one below"
        case .unreachable: return "can't reach it right now"
        case .notOurs: return "looks like a different Domovoi, or it was reinstalled"
        case .coreDown: return "up, but its core isn't answering yet"
        case .available(_, let needsPairing):
            return needsPairing ? "available · needs the household token" : "available"
        }
    }

    /// Name for the server side of the switch.
    public var title: String {
        switch self {
        case .noServer: return "your Domovoi"
        case .unreachable(let label), .notOurs(let label), .coreDown(let label): return label
        case .available(let label, _): return label
        }
    }

    /// The label where there is one, so the chrome can tell "no server" from
    /// "a server called this". Android asks `choice == NoServer` at each site;
    /// one accessor is the same question asked once.
    public var label: String? {
        if case .noServer = self { return nil }
        return title
    }
}

public func serverChoice(
    serverURL: String,
    label: String,
    unreachable: Bool,
    notOurs: Bool,
    pairingRequired: Bool,
    coreDown: Bool = false
) -> ServerChoice {
    if ServerCredentials.isBlank(serverURL) { return .noServer }
    // If the inputs disagree the impostor reading wins: it is the one that
    // must never be softened (`ShellModeTest.anIdentityFailureBeatsACoreDownReading`).
    if notOurs { return .notOurs(label: label) }
    // With the token held, every request fails and the server reads as
    // unreachable; the identity verdict says why, so it is named first
    // (`ShellModeTest.aServerWhoseCoreIsDownIsGreyedWithItsOwnReasonNotUnreachable`).
    if coreDown { return .coreDown(label: label) }
    if unreachable { return .unreachable(label: label) }
    return .available(label: label, needsPairing: pairingRequired)
}

// MARK: - reachability

/// How long the server may go without answering before it counts as
/// unreachable. Long enough for a couple of failed probes, so a blip at home
/// does not bounce the person out of the screen they are on.
public let unreachableGraceMs: Int64 = 10_000

/// How often the server is asked while the live socket is down.
public let reachProbeEveryMs: Int64 = 4_000

/// Whether the saved server counts as out of reach: it has gone unanswered
/// for `graceMs` of time the app spent ON SCREEN asking it.
///
/// A port of `Reachability` in `android/.../ui/shell/ShellMode.kt`.
///
/// The time in the background does not count. Android freezes a cached app
/// (the system folder picker in front of it is enough): its live socket
/// misses a ping, and the first request after the thaw can fail on a stale
/// connection. Measured by the wall clock the grace was long over by then,
/// and one failed probe dropped a phone sitting next to a healthy server to
/// local media. So the asking pauses when the app leaves the screen
/// (``pause(_:)``) and starts again with a fresh probe when it comes back
/// (``resume(_:)``); only foreground stretches add up, and a result that
/// lands while the app is away is no evidence either way.
///
/// **iOS has the same hazard in a different shape**, which is why the rule is
/// ported even though the lifecycle API is not: a suspended app's
/// `URLSession` connections are torn down or go stale, and the first request
/// after a resume can fail for that reason alone.
///
/// ### What this type is owed is CANCELLATION, and ``ReachabilityDriver`` owes it
///
/// ``askWhileOnScreen(reach:probe:clock:wait:publish:)`` calls ``resume(_:)``
/// **on itself** and takes ``pause(_:)`` in its own `defer`. So the moment
/// any caller starts that loop the stopwatch is running, and **this type
/// cannot stop it**: a suspended process's `Task` is not cancelled, it is
/// merely not running, and this type has no clock of its own — it believes
/// the one it is handed, which is the wall clock.
///
/// An uncancelled loop therefore **measures the suspension**. Two minutes in
/// the background with the loop still live, the first probe after the thaw
/// failing on a stale connection, and ``missed(_:)`` charges the whole two
/// minutes against a 10 s grace: a person sitting beside a healthy server is
/// dropped to local media.
///
/// **Who stops it, as of I1-C part 2:** ``ReachabilityDriver``, which is the
/// only thing in this package that starts the loop. It cancels the `Task`
/// when the scene leaves `.active` and — the part that is not obvious — calls
/// ``pause(_:)`` **synchronously in the same call**, because `Task.cancel()`
/// returns at once and the loop's own `defer` would run later, with a later
/// timestamp, charging the gap it was meant to avoid. The `defer`'s `pause`
/// is then a no-op (``pause(_:)`` guards on whether a stretch is open), which
/// is why both can stand.
///
/// So a caller's obligation is now one of two things: use
/// ``ReachabilityDriver``, or, if something ever drives
/// ``askWhileOnScreen(reach:probe:clock:wait:publish:)`` directly, cancel its
/// `Task` and pause this tracker together whenever the app leaves the screen.
/// The cost of not doing so is asserted, not described, in
/// `ReachabilityTests.testTheSuspensionCostsNothingNowThatTheDriverCancelsThe`
/// `Loop`, whose first half still drives a raw uncancelled loop; the wired
/// behaviour is `ShellLifecycleTests`.
///
/// An earlier version of this comment said the failure mode of the missing
/// wiring was "the app never falls back, not it falls back wrongly"; that was
/// true of a part 1 where nothing ran the loop, and false of the part 2 it
/// was written for. A review caught it. The sentence "the grace is never
/// counted" was only ever true while nothing had started the loop at all.
///
/// What it does not change: out of the house, on screen, the server not
/// answering, the app still falls back after `graceMs`; once out of reach it
/// stays so until an answer — a spell in the background never brings the
/// workspace back by itself.
public final class Reachability: Sendable {

    private struct State {
        var unreachable = false
        /// Foreground time spent unanswered before the current stretch.
        var spentMs: Int64 = 0
        /// When the current foreground stretch of asking began (or last got
        /// an answer); nil while not asking.
        var askingSince: Int64?
        /// The last ticket ``resume(_:)`` handed out, counted from 1. Every
        /// call gets its own, whether or not it opened anything.
        var lastTicket: Int64 = 0
        /// The tickets of every loop that is **currently asking**. The
        /// stretch is open while this is non-empty and closes when the last
        /// of them leaves. See ``pause(_:stretch:)`` for why it is a set and
        /// not one number.
        var openers: Set<Int64> = []
    }

    private let state = Mutex(State())
    private let graceMs: Int64

    public init(graceMs: Int64 = unreachableGraceMs) {
        self.graceMs = graceMs
    }

    public var unreachable: Bool { state.withLock { $0.unreachable } }

    /// Whether a stretch is open — whether anything is being timed at all.
    ///
    /// Internal, and only a test reads it: it is the exact condition
    /// ``missed(_:)`` guards on, so it is how a test can see the failure in
    /// which the stopwatch is closed **under a live loop** — every later
    /// `missed` returns early and a server that really is gone never falls
    /// back. See `ShellLifecycleTests.testPhaseChurnNeverClosesTheStopwatch`
    /// `UnderALiveLoop`.
    var isTiming: Bool { state.withLock { $0.askingSince != nil } }

    /// The app is on screen with the live socket down: asking starts (or
    /// starts again).
    ///
    /// Answers **a ticket of this caller's own**, for a caller that will pause
    /// the stretch it opened and no other — see ``pause(_:stretch:)``. Asking
    /// again while already asking changes nothing about the stopwatch, and the
    /// ticket it answers is a fresh one that **opened nothing**, so pausing
    /// with it does nothing.
    ///
    /// That last sentence is the fix for a review finding, and the shape it
    /// replaced is worth recording because its own comment argued it was
    /// safe. This used to increment only when it opened a stretch and
    /// otherwise answer *the stretch in progress*, so two callers whose
    /// `resume` calls overlapped were handed **the same number** — and a
    /// number that names the stretch rather than its caller cannot tell the
    /// opener from a bystander. `pause`'s "a stale one is ignored" was true
    /// and beside the point: a bystander's token was not stale, it was
    /// identical, and its late `pause` closed the live loop's stretch.
    /// `ShellLifecycleTests.testPhaseChurnNeverClosesTheStopwatchUnderALive`
    /// `Loop` is that race, and it fails on the old shape — 2 of 200 cycles,
    /// measured.
    ///
    /// **A ticket that named only the opener was not enough either**, and
    /// that is worth recording too, because it is what the fix was first
    /// written as. In the churn case the late pauser *is* the opener: the
    /// cancelled loop resumed first and opened the stretch, the live loop
    /// found one open and so opened nothing, and the cancelled loop's `defer`
    /// then closed the stretch the live loop was running under — which the
    /// churn test still caught, 1 of 200, with that shape in place. So the
    /// stretch is not owned by one loop: it is open while **any** loop is
    /// asking, which is what ``State/openers`` holds.
    @discardableResult
    public func resume(_ now: Int64) -> Int64 {
        state.withLock {
            $0.lastTicket += 1
            if $0.askingSince == nil { $0.askingSince = now }
            $0.openers.insert($0.lastTicket)
            return $0.lastTicket
        }
    }

    /// The app left the screen (or the socket came back): asking stops, the
    /// stretch so far is kept.
    ///
    /// - Parameter stretch: the ticket ``resume(_:)`` answered **this**
    ///   caller. The stretch is closed only when that ticket is the one that
    ///   opened it, and that is not tidiness:
    ///   ``askWhileOnScreen(reach:probe:clock:wait:publish:)`` pauses in a
    ///   `defer`, which runs **after** its task is cancelled — by which time
    ///   ``ReachabilityDriver`` may already have paused this tracker (it does
    ///   so synchronously, with the right timestamp) and started a new loop
    ///   that resumed it. Without the ticket that late `defer` closes the new
    ///   stretch, the next ``missed(_:)`` finds nothing open and returns, and
    ///   a server that really is gone never falls back. Watched happening:
    ///   `ShellLifecycleTests.testAnInterruptionKeepsWhatIsOwedRatherThan`
    ///   `ClearingIt` fails without this.
    ///
    ///   With a ticket this means **"this loop has stopped asking"**, and the
    ///   stretch closes only once nothing else is asking either. One number
    ///   naming the opener is not enough: in the churn case the loop that
    ///   leaves is the one that opened the stretch, and the live loop joined
    ///   an already-open one, so "only the opener may close it" closes it
    ///   under the live loop. A ticket that is not in ``State/openers`` —
    ///   a loop that never asked, or asked and has already left — does
    ///   nothing at all.
    ///
    ///   Omitted (``ReachabilityDriver``'s own call, and ``stop()``'s), it
    ///   means **"everybody is off the screen now"**: every opener is
    ///   dropped and the stretch closes with this timestamp, which is the
    ///   authoritative one. That is why the driver's call has no ticket.
    public func pause(_ now: Int64, stretch: Int64? = nil) {
        state.withLock {
            if let stretch {
                guard $0.openers.remove(stretch) != nil else { return }
                guard $0.openers.isEmpty else { return }
            } else {
                $0.openers.removeAll()
            }
            if let since = $0.askingSince { $0.spentMs += max(0, now - since) }
            $0.askingSince = nil
        }
    }

    /// The server answered: in reach, and the grace starts over.
    public func answered(_ now: Int64) {
        state.withLock {
            $0.spentMs = 0
            if $0.askingSince != nil { $0.askingSince = now }
            $0.unreachable = false
        }
    }

    /// The live socket is up: nothing to ask, nothing owed.
    public func connected() {
        state.withLock {
            $0.spentMs = 0
            $0.askingSince = nil
            $0.openers.removeAll()
            $0.unreachable = false
        }
    }

    /// A probe went unanswered. Ignored unless the app is on screen asking.
    public func missed(_ now: Int64) {
        state.withLock {
            guard let since = $0.askingSince else { return }
            if $0.spentMs + max(0, now - since) >= graceMs { $0.unreachable = true }
        }
    }
}

/// Ask the server every ``reachProbeEveryMs`` while the app is on screen,
/// publishing the verdict after each answer.
///
/// A port of `askWhileOnScreen`. Android runs it under
/// `repeatOnLifecycle(STARTED)`; here the caller runs it in a `Task` and
/// cancels that task when the app leaves the screen, which is the same shape:
/// leaving cancels it (and ``Reachability`` stops counting, in the `defer`),
/// coming back starts it again with a fresh probe first.
///
/// **That cancellation is not optional.** This function takes
/// ``Reachability/resume(_:)`` on itself, so a caller that starts the loop and
/// does not cancel it has a stopwatch running across every suspension — see
/// the note on ``Reachability`` for what that does to the person.
///
/// **A probe still out when the app left is dropped with it.** On Android
/// that is automatic: cancelling the coroutine abandons the suspended
/// `probe()` call. Swift cancellation is cooperative, so an `await probe()`
/// can *return* after the task was cancelled, and its answer would be
/// published as evidence about a stretch the app was not on screen for. The
/// `Task.isCancelled` check after the probe is what drops it, and
/// `ReachabilityTests.testTwoMinutesInTheBackgroundWithTheServerFineStaysOnTheWorkspace`
/// fails without it.
///
/// **A task cancelled before it ran does not start the stopwatch either.**
/// That is the `Task.isCancelled` check on the *first* line, and it is a
/// different hazard from the one after the probe: a cancelled Swift task still
/// runs its body, so a loop the driver cancelled in the same main-actor turn
/// it started it would otherwise call ``Reachability/resume(_:)`` alongside
/// the live loop's.
///
/// `probe` is not throwing, which matches Android: its probe is
/// `api.answers()`, which returns false rather than throwing
/// (``DomovoiClient/answers()``).
public func askWhileOnScreen(
    reach: Reachability,
    probe: @Sendable () async -> Bool,
    clock: @Sendable () -> Int64,
    wait: @Sendable (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) },
    publish: @Sendable (Bool) -> Void
) async {
    // A task that was cancelled before it ran an instruction must not start
    // the stopwatch at all. Swift runs a cancelled task's body, so
    // `.active → .background → .active` inside one main-actor turn — a launch
    // straight into the background, a flick through the app switcher — gets
    // here on a task the driver has already cancelled, and without this guard
    // its `resume` races the live loop's. See
    // `ShellLifecycleTests.testPhaseChurnNeverClosesTheStopwatchUnderALiveLoop`:
    // the stretch ticket makes that race harmless, and this makes it not
    // happen.
    if Task.isCancelled { return }
    let stretch = reach.resume(clock())
    defer { reach.pause(clock(), stretch: stretch) }
    while !Task.isCancelled {
        let answered = await probe()
        // See the note on the type: this is the dropped straddling probe.
        if Task.isCancelled { return }
        let now = clock()
        if answered { reach.answered(now) } else { reach.missed(now) }
        publish(reach.unreachable)
        do { try await wait(reachProbeEveryMs) } catch { return }
    }
}
