import Foundation
import Synchronization

// MARK: - what the socket carries

/// One frame off the state socket: `{"type": …, "data": …}`.
///
/// Android's `WsEvent(type, payload)` (`net/StateBus.kt:21`), with one
/// difference that is a fidelity decision rather than a preference: the
/// payload is kept as **the frame's own text**, not as a re-parsed tree. The
/// one push whose payload is consumed (`satellites.wifi.changed`, map §1.3)
/// decodes it, and decoding the original bytes cannot lose a field the way a
/// parse-and-re-encode round trip can.
public struct StateEvent: Equatable, Sendable {

    /// The `type` field, which every consumer filters on.
    public let type: String

    /// The whole frame, as it arrived.
    public let frame: String

    public init(type: String, frame: String) {
        self.type = type
        self.frame = frame
    }

    /// The frame, or nil for one the listener drops.
    ///
    /// Android drops three kinds, all inside one `runCatching`
    /// (`StateBus.kt:98-105`): text that is not JSON, JSON that is not an
    /// object, and an object with no `type`. So does this.
    ///
    /// **One divergence, stated because it is a divergence and not because it
    /// matters:** Kotlin's `jsonPrimitive.content` would render a numeric
    /// `type` as its digits, where this requires a string and drops the
    /// frame. Every `type` the server sends is a string
    /// (`web/backend/main.py` pushes `{"type": "<name>", …}`), so no frame
    /// behaves differently; `testAFrameWhoseTypeIsANumberIsDropped` is the
    /// record of which way it goes.
    /// ### The two bounds on a frame, and which one Foundation already gives
    ///
    /// **Size** is this app's: ``stateSocketMaximumFrameBytes``, checked here
    /// before `JSONSerialization` is handed anything, and set on the task as
    /// well so an over-size frame is usually refused by the transport first.
    ///
    /// **Nesting depth** is Foundation's, and it is enough. Measured
    /// 2026-10-29 on a cooperative-pool thread, whose stack is far smaller
    /// than the main thread's and is where a real frame is parsed:
    /// `JSONSerialization` parses **512** levels of nesting and **drops 513**,
    /// and it drops rather than crashing at every depth up to 600 000
    /// (a 1.2 MB frame) — see `testADeeplyNestedFrameIsDroppedRatherThanCrashing`.
    /// So there is no depth cap here: there is nothing left to cap.
    public static func parse(_ text: String) -> StateEvent? {
        // Counted before any allocation of a parse tree, and on the UTF-8
        // view rather than `text.count`, because the maximum is a number of
        // bytes on a wire and not a number of characters.
        guard text.utf8.count <= stateSocketMaximumFrameBytes else { return nil }
        guard let bytes = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let type = object["type"] as? String
        else { return nil }
        return StateEvent(type: type, frame: text)
    }

    private struct Envelope<Payload: Decodable>: Decodable {
        let data: Payload?
    }

    /// The frame's `data`, decoded, or nil when it is absent or does not fit.
    public func data<Payload: Decodable>(as payload: Payload.Type) -> Payload? {
        guard let bytes = frame.data(using: .utf8) else { return nil }
        return (try? JSONDecoder().decode(Envelope<Payload>.self, from: bytes))?.data
    }
}

/// The subscribe-all frame, sent on open as a text frame.
///
/// The literal `{"subscribe": []}` (`StateBus.kt:95`). An empty list means
/// every channel — `web/backend/main.py:704`, "Empty list / no frame =
/// subscribed to all channels" — and nothing in either app ever sends a
/// narrower one: consumers filter by `type` client-side.
///
/// It carries **no token**. The server accepts a `device_token` field here and
/// ignores it, "kept only for older clients", because a frame arrives only
/// after the socket has been accepted, which is too late to refuse it (map
/// §8.1). The credential is on the upgrade, and only there.
public let stateSocketSubscribeAll = #"{"subscribe": []}"#

/// The path the state socket is opened on.
public let stateSocketPath = "/ws/state"

/// How often the socket pings, from `ApiClient.kt:150`'s
/// `pingInterval(15, TimeUnit.SECONDS)`, whose comment says exactly why:
/// "Walking out of Wi-Fi range often leaves the state socket half-open rather
/// than closed: no FIN arrives, so without a ping it would read 'live'
/// indefinitely and the shell would never fall back to local media."
///
/// `URLSessionWebSocketTask` has no automatic ping, so
/// ``URLSessionStateSocketTransport`` sends them itself.
public let stateSocketPingMs: Int64 = 15_000

/// How long an attempt may be neither open nor ended before it stops counting
/// as a connection.
///
/// **This number is one answer to two questions, and that is deliberate.**
///
///  1. ``URLSessionStateSocketTransport`` ends a link that has not reported
///     `opened` within it. A server that completes the `101` and then answers
///     nothing — a proxy, a captive-portal box in front of the house — is
///     otherwise a socket with no bound on it at all, because
///     `URLSessionWebSocketTask` has no post-handshake idle timeout this app
///     may rely on (`timeoutIntervalForRequest` is documented as the
///     handshake's, and whether it also acts as an idle timeout is an
///     undocumented implementation detail that differs across OS versions).
///  2. ``StateSocket/wake()`` restarts such an attempt. That is the half that
///     does not depend on the transport ever calling back at all, which is
///     worth more than the timeout: the timeout fixes the transport this app
///     ships, and this fixes every transport.
///
/// 10 s beside a 15 s ping cadence: long enough that a slow LAN handshake is
/// not a failure, short enough that the person waiting on the live dot is not.
/// It is also the line that keeps ``StateSocket/wake()`` from thrashing a
/// socket that is legitimately still connecting — `wake()` is called on
/// **every** reachability verdict, so that line has to exist and be measured
/// (`testWakingASocketThatIsStillConnectingDoesNotThrashIt`).
public let stateSocketConnectBudgetMs: Int64 = 10_000

/// The largest frame this app will take off the socket.
///
/// The same 1 MiB `URLSessionWebSocketTask` defaults to, **chosen rather than
/// inherited**, and set explicitly on the task so that a platform whose
/// default changes does not change this app. ``StateEvent/parse(_:)`` rejects
/// an over-size frame before `JSONSerialization` sees it, so a transport that
/// does not enforce it is bounded too.
///
/// Android has no equivalent — OkHttp sets no maximum — so **a frame iOS
/// rejects, Android would parse**. Nothing the server pushes today comes near
/// it: the largest is `satellites.wifi.changed`, which carries one small
/// object per room. If a house ever could exceed it, the answer is a smaller
/// push on the server side rather than a bigger number here, and that is an
/// owner decision; `I1D1-FIX-REPORT.md` records it as one.
public let stateSocketMaximumFrameBytes = 1 << 20

/// The socket's address for a saved server: the base with its scheme swapped
/// and ``stateSocketPath`` on the end.
///
/// `StateBus.wsUrl()` (`:81`) does this with two `replaceFirst` calls, which
/// rewrite anywhere in the string; this one rewrites only a prefix. The two
/// agree for every address ``ServerURL`` can produce — a base is
/// `scheme://host[:port]` with no path — and a prefix rewrite cannot turn a
/// query or a path that happens to contain `http://` into a second scheme.
public func stateSocketURL(base: String) -> String? {
    guard !ServerCredentials.isBlank(base) else { return nil }
    if base.hasPrefix("http://") {
        return "ws://" + base.dropFirst("http://".count) + stateSocketPath
    }
    if base.hasPrefix("https://") {
        return "wss://" + base.dropFirst("https://".count) + stateSocketPath
    }
    return base + stateSocketPath
}

// MARK: - the reconnect rule, as a decision

/// Why the socket is **not** connected and is not counting down to anything.
///
/// Each of these is a question whose answer cannot change by itself, so a
/// timer is the wrong thing to wait on (map §8.3): the socket parks and waits
/// to be told the state changed. ``StateSocket/wake()`` is that telling.
public enum SocketPause: Equatable, Sendable {

    /// No server is saved. Only a server switch changes that.
    ///
    /// Android spins here instead, waking every ≤15 s to find there is still
    /// no address (`StateBus.kt:88`, and map §9's first inference). A fresh
    /// install with no server really does enter that state, because
    /// `DomovoiApplication.onCreate` starts the bus unconditionally outside
    /// room mode.
    case noServer

    /// The address cannot host a socket at all: it does not parse, or
    /// ``CleartextPolicy`` refuses it. A property of the address, so it is
    /// decided once — ``DomovoiClient/webSocketUpgrade(_:)`` returns nil.
    case addressUnusable

    /// ``SocketUpgrade/mint()`` said "not now": the server asked this phone to
    /// wait, or the identity gate held the token. **The request was never
    /// built**, so nothing left the phone.
    case notNow
}

/// What one attempt to connect, or one live socket, just did.
public enum SocketStep: Equatable, Sendable {
    case noServer
    case addressUnusable
    /// ``SocketUpgrade/mint()`` returned nil.
    case notNow
    /// The upgrade succeeded.
    case opened
    /// A close or a transport failure — the only kind of thing the backoff is
    /// for.
    case lost
}

/// What the socket does next.
public enum SocketNext: Equatable, Sendable {
    case park(SocketPause)
    case live
    case retry(afterMs: Int64)
}

/// The 1 s → ×1.6 → 15 s ladder, as a value.
///
/// `StateBus.scheduleReconnect` (`:118-126`) sleeps the *current* delay and
/// multiplies afterwards, so the first retry really is at one second, and
/// `onOpen` resets it (`:93`). Both are copied, including the truncation:
/// Kotlin's `(delayMs * 1.6).toLong()` and Swift's `Int64(Double(d) * 1.6)`
/// both truncate towards zero, so the ladder is
/// **1000, 1600, 2560, 4096, 6553, 10484, 15000** and then 15000 for ever.
///
/// `ANDROID-HOME-MAP.md` §1.4 writes the sixth rung as 10485. It is 10484:
/// 6553 × 1.6 is 10484.8 in double arithmetic and `toLong()` truncates.
/// `testTheBackoffLadderIsTheKotlinLadder` is the ladder.
public struct SocketBackoff: Equatable, Sendable {

    public static let firstMs: Int64 = 1_000
    public static let capMs: Int64 = 15_000
    public static let factor = 1.6

    public private(set) var delayMs: Int64 = SocketBackoff.firstMs

    public init() {}

    /// The delay to wait now, advancing the ladder for next time.
    public mutating func take() -> Int64 {
        let now = delayMs
        delayMs = min(Int64(Double(delayMs) * Self.factor), Self.capMs)
        return now
    }

    public mutating func reset() {
        delayMs = Self.firstMs
    }
}

/// **The rule this batch exists to get right.**
///
/// The backoff applies to real transport failures and to nothing else. A nil
/// ``SocketUpgrade/mint()`` is not a failed connection: it is this app
/// declining to send a request, over a question — a wait, a held identity —
/// whose answer cannot change while a timer runs. Retrying it on the ladder
/// would ask the same question every 15 s for ever, which is Android's
/// behaviour and is tolerable there only because the identity gate caches
/// refusals (map §8.3, and `IdentityGate.holds`).
///
/// So: three of the five steps park, one goes live and resets the ladder, and
/// exactly one takes a rung off it.
public func socketNext(after step: SocketStep, backoff: inout SocketBackoff) -> SocketNext {
    switch step {
    case .noServer:
        return .park(.noServer)
    case .addressUnusable:
        return .park(.addressUnusable)
    case .notNow:
        return .park(.notNow)
    case .opened:
        backoff.reset()
        return .live
    case .lost:
        return .retry(afterMs: backoff.take())
    }
}

// MARK: - the transport

/// A socket that is open, as the little the socket needs of it.
public protocol StateSocketLink: Sendable {
    func send(_ text: String)
    func cancel()
}

/// What a transport tells the socket. Every closure may be called from any
/// thread, and a stale link's calls are ignored by generation inside
/// ``StateSocket``.
public struct StateSocketEvents: Sendable {

    /// The upgrade succeeded. **It carries the link**, rather than leaving the
    /// socket to find the one it stored, because a transport may answer
    /// synchronously: the test one does, and then `open` has not returned and
    /// there is nothing stored yet. Handing the link to the callback is what
    /// makes the subscribe-all frame independent of that ordering —
    /// `testASynchronousOpenStillSubscribes` is the order that used to lose
    /// it.
    public let opened: @Sendable (any StateSocketLink) -> Void

    public let text: @Sendable (String) -> Void

    /// A failure. `status` is the upgrade's HTTP status when there was a
    /// response, nil when the request never got an answer (which is also what
    /// Android sees for its two `throw`s — map §8.2). `body` is whatever text
    /// there is to read, which on this path is **nothing**: see
    /// ``StateSocket``'s note on the pairing screen.
    public let failed: @Sendable (Int?, String?) -> Void

    public let closed: @Sendable () -> Void

    public init(
        opened: @Sendable @escaping (any StateSocketLink) -> Void,
        text: @Sendable @escaping (String) -> Void,
        failed: @Sendable @escaping (Int?, String?) -> Void,
        closed: @Sendable @escaping () -> Void
    ) {
        self.opened = opened
        self.text = text
        self.failed = failed
        self.closed = closed
    }
}

/// How a socket is actually opened. The app passes
/// ``URLSessionStateSocketTransport``; a test passes its own.
public protocol StateSocketTransport: Sendable {
    func open(_ request: URLRequest, reporting events: StateSocketEvents) -> any StateSocketLink
}

// MARK: - the socket

/// The app's one state socket: `/ws/state`, subscribe-all, reconnecting.
///
/// A port of `net/StateBus.kt` (130 lines, read at `6ff79c0e`). It belongs on
/// ``AppContainer`` and lives for the process, because Android's does and
/// because eight consumers read it — Home's live dot and its re-reads, the
/// capability manifest, the device register, four alert engines and every
/// paged list (map §1.7). It is **not** owned by a view.
///
/// ### Live and reachable are two facts, and this type only knows one
///
/// ``StateSocket`` publishes whether the socket is up. That is not whether
/// the server is reachable, and the two must not be joined: an unpaired
/// phone's upgrade is refused `403` by a server standing right next to it, so
/// its socket is permanently down while ``DomovoiClient/answers()`` — which
/// counts **any** HTTP status — says the server is plainly there (map §1.6,
/// `ApiClient.kt:266-274`). ``reachabilityAction(serverURL:connected:phase:)``
/// consumes `connected` only as a short-circuit, never as evidence of the
/// opposite.
///
/// The mirror of it is this type's own hazard: a frozen app's `connected`
/// stays true against a dead socket, because no callback fires while the
/// process is suspended. So ``sceneReturned()`` exists, and
/// ``ShellModel/scenePhaseChanged(to:sendRegister:)`` calls it: on coming
/// back the flag is not believed, the link is dropped and the socket re-mints
/// and reconnects. `testASceneReturnDoesNotBelieveTheConnectedFlag` is that.
///
/// ### The pairing screen, and what this call cannot do
///
/// `StateBus.onFailure` passes the upgrade's status and OkHttp's `Response`
/// **reason phrase** to `notePossiblePairingRefusal`, and its comment claims
/// "a refused upgrade is the only way this socket hears 'pair first'". Map
/// §11.6 shows that chain is broken on Android, because the server's wording
/// ("household device token or admin session required") is in the WebSocket
/// close reason, not the HTTP status line, and
/// ``isDeviceTokenRefusal(status:body:)`` needs the wording.
///
/// **On iOS the chain is broken by construction, not by accident.** A failed
/// `URLSessionWebSocketTask` upgrade gives the task's `HTTPURLResponse` and no
/// body at all; `HTTPURLResponse` has no wire reason phrase to read
/// (`localizedString(forStatusCode:)` is generated locally from the number).
/// So this socket calls ``DomovoiClient/notePossiblePairingRefusal(status:body:)``
/// with a nil body, and **that call cannot raise the pairing screen**.
/// `testARefusedUpgradeDoesNotRaiseThePairingScreen` asserts exactly that, so
/// the fact is pinned rather than argued. It is not a gap this batch closed:
/// the pairing screen is raised by `/api/satellites`' tracked 401 instead
/// (map §11.1), and whether the upgrade should raise it **by status alone**
/// is an owner decision this batch did not take, listed in `I1D1-REPORT.md`.
public final class StateSocket: Sendable {

    private struct State {
        var started = false
        /// The base the running socket is for, so a switch is noticed.
        var base = ""
        var link: (any StateSocketLink)?
        var retry: Task<Void, Never>?
        var backoff = SocketBackoff()
        /// Why it is parked, or nil when it is not.
        var paused: SocketPause?
        /// Which attempt is current. A stale link's callbacks carry an older
        /// number and are ignored — the generation guard
        /// ``ReachabilityDriver`` needs a reservation ticket for, for the same
        /// reason: this type is `public` and `Sendable` and its callbacks
        /// arrive from the transport's own threads.
        var attempt: Int64 = 0
        var connected = false
        /// When the current attempt was claimed, on ``clock``'s scale. The
        /// measurement behind ``stateSocketConnectBudgetMs``: an attempt that
        /// has neither opened nor ended is a connection only while it is
        /// younger than that.
        var attemptStartedMs: Int64 = 0
    }

    private let state: Mutex<State>

    private let base: @Sendable () -> String
    private let upgrade: @Sendable (String) -> SocketUpgrade?
    private let transport: any StateSocketTransport
    private let noteRefusal: @Sendable (Int, String?) -> Void
    private let clock: @Sendable () -> Int64
    private let wait: @Sendable (Int64) async throws -> Void
    private let publish: @Sendable (Bool) -> Void
    private let event: @Sendable (StateEvent) -> Void

    /// - Parameters:
    ///   - base: the saved server's address. Read fresh on every attempt,
    ///     never captured, because a server switch has to be noticed.
    ///   - upgrade: ``DomovoiClient/webSocketUpgrade(_:)``. Nil means the
    ///     address cannot host a socket; the ``SocketUpgrade`` it hands back
    ///     is asked again, with ``SocketUpgrade/mint()``, immediately before
    ///     **every** connect including every reconnect — which is that
    ///     method's stated obligation and the reason it is a value with a
    ///     `mint()` rather than a stored request.
    ///   - noteRefusal: ``DomovoiClient/notePossiblePairingRefusal(status:body:)``.
    ///   - clock: a monotonic millisecond clock, for
    ///     ``stateSocketConnectBudgetMs`` and nothing else. Monotonic rather
    ///     than wall-clock for the usual reason: the budget must not be
    ///     skipped or doubled by a clock correction, and the house pattern is
    ///     ``TokenHealth/monotonicMs``.
    ///   - publish: whether the socket is up, on every change and only on a
    ///     change (Android's `StateFlow` dedupes; so does this, because a
    ///     repeat would burn a device-register cadence slot).
    ///   - event: every frame that parses, with no replay and no buffer. An
    ///     event that arrives while nothing is listening is **dropped**, as
    ///     Android's no-replay `MutableSharedFlow` drops it, which is why
    ///     every consumer also has a catch-up read (map §1.1).
    public init(
        base: @Sendable @escaping () -> String,
        upgrade: @Sendable @escaping (String) -> SocketUpgrade?,
        transport: any StateSocketTransport,
        noteRefusal: @Sendable @escaping (Int, String?) -> Void = { _, _ in },
        clock: @Sendable @escaping () -> Int64 = TokenHealth.monotonicMs,
        wait: @Sendable @escaping (Int64) async throws -> Void = { try await Task.sleep(for: .milliseconds($0)) },
        publish: @Sendable @escaping (Bool) -> Void = { _ in },
        event: @Sendable @escaping (StateEvent) -> Void = { _ in }
    ) {
        self.base = base
        self.upgrade = upgrade
        self.transport = transport
        self.noteRefusal = noteRefusal
        self.clock = clock
        self.wait = wait
        self.publish = publish
        self.event = event
        self.state = Mutex(State())
    }

    // MARK: what a caller asks

    /// Whether the socket is up **as far as the callbacks go**. Not evidence
    /// about the server, and not to be believed across a suspension — see the
    /// note on this type.
    public var connected: Bool { state.withLock { $0.connected } }

    /// Why it is parked, or nil.
    public var paused: SocketPause? { state.withLock { $0.paused } }

    /// The ladder's current rung, for a test.
    var backoffMs: Int64 { state.withLock { $0.backoff.delayMs } }

    /// How many times the rule has been asked. The measurement behind
    /// "a scene return re-mints rather than believing the flag": the effect
    /// is otherwise invisible from outside when the answer has not changed.
    var attemptCount: Int64 { state.withLock { $0.attempt } }

    public var isStarted: Bool { state.withLock { $0.started } }

    /// Whether a reconnect is counting down.
    public var isWaitingToRetry: Bool { state.withLock { $0.retry != nil } }

    // MARK: the four inputs

    /// Open it, once. Idempotent, like `StateBus.start()`'s
    /// `if (started) return`.
    public func start() {
        let address = base()
        let go = state.withLock { current -> Bool in
            if current.started { return false }
            current.started = true
            current.base = address
            return true
        }
        guard go else { return }
        connect()
    }

    /// Close it and stop reconnecting.
    ///
    /// Android's only caller is room mode's setup activity
    /// (`RoomSetupActivity.kt:147`), and `stop()`'s own doc says why: "the
    /// household side is off while the phone is a room". **iOS has no room
    /// mode yet, so this has no caller in the app**, and that is a gap and
    /// not a design: the scene that reads ``HouseholdLock`` is a later
    /// batch's, and the batch that writes it has to call this.
    /// ``ShellModel/stop()`` deliberately does *not* — the socket outlives a
    /// shell — it drops its own subscription instead.
    ///
    /// Unlike Android's, this leaves nothing running: `StateBus.stop()`
    /// cancels the socket and the pending reconnect but not its `serverUrl`
    /// collector, so a later `start()` launches a second one (map §11.8).
    /// There is no collector here — ``serverChanged()`` is a call, not a
    /// subscription — so the shape that defect lives in does not exist.
    public func stop() {
        let (dropped, was) = state.withLock { current -> ((any StateSocketLink)?, Bool) in
            current.started = false
            current.retry?.cancel()
            current.retry = nil
            current.paused = nil
            let was = current.connected
            current.connected = false
            let link = current.link
            current.link = nil
            return (link, was)
        }
        dropped?.cancel()
        if was { publish(false) }
    }

    /// The saved server may have changed. Reconnects from scratch when it
    /// has, and does nothing when it has not.
    ///
    /// `StateBus.restart()` (`:73`) resets `delayMs` to 1000, so a switch gets
    /// a fresh first-second retry rather than inheriting the old server's
    /// backed-off delay (map §1.5). Same here.
    public func serverChanged() {
        let address = base()
        let changed = state.withLock { current -> Bool in
            guard current.started, current.base != address else { return false }
            current.base = address
            return true
        }
        guard changed else { return }
        restart()
    }

    /// Something that a parked socket was waiting for may have happened:
    /// the identity status, a wait running out, a token arriving, the network
    /// changing, the app coming back.
    ///
    /// This is the **only** thing that un-parks a ``SocketPause``, and it is
    /// cheap on purpose: when the socket is not parked it does nothing at
    /// all, and when it is, the one thing it does is ask
    /// ``SocketUpgrade/mint()`` again, which reads the identity gate's cache
    /// and the token health book and sends nothing. ``ShellModel/refresh()``
    /// calls it, which means the token-trouble card's once-a-second countdown
    /// calls it once a second while a wait is counting —
    /// `testAWaitingSocketSendsNothingHoweverOftenItIsWoken` is what says
    /// that costs the server nothing.
    ///
    /// ### It also rescues an attempt that never came back
    ///
    /// A park is not the only state nothing else gets out of. An upgrade that
    /// the server **accepts** and then answers nothing on leaves
    /// `started == true`, `paused == nil`, `retry == nil`,
    /// `connected == false` — a state `start()`, `serverChanged()` and the
    /// old `wake()` were all no-ops against, and which from outside is
    /// indistinguishable from "connecting". Only backgrounding the app and
    /// coming back recovered it.
    ///
    /// So the invariant is stated as the thing the socket actually wants:
    /// **an attempt that has neither opened nor ended, and has had
    /// ``stateSocketConnectBudgetMs`` to do one of them, is not a
    /// connection.** That removes the structural dependency on the transport
    /// ever calling back, which is worth more than
    /// ``URLSessionStateSocketTransport``'s own watchdog: the watchdog fixes
    /// the one transport this app ships, and this fixes every transport.
    ///
    /// **Where the line is, and why it has to be time and not state.**
    /// ``ShellModel`` calls this on every reachability verdict, so a rescue
    /// that fired on `paused == nil && !connected` alone would tear down and
    /// re-dial a socket that is legitimately still shaking hands, once per
    /// probe — worse than the bug. The budget is that line, and
    /// `testWakingASocketThatIsStillConnectingDoesNotThrashIt` is the
    /// measurement rather than the argument.
    ///
    /// A countdown to a retry is excluded too (`retry != nil`): the ladder
    /// owns the next attempt there, and a wake that jumped it would collapse
    /// the backoff into a spin.
    public func wake() {
        let now = clock()
        let go = state.withLock { current -> Bool in
            guard current.started else { return false }
            if current.paused != nil { return true }
            guard current.retry == nil, !current.connected else { return false }
            return now - current.attemptStartedMs >= stateSocketConnectBudgetMs
        }
        guard go else { return }
        connect()
    }

    /// The app came back on screen, so ``connected`` is not to be believed.
    ///
    /// The hazard is map §1.6's mirror image: a cached, frozen app misses
    /// every callback, so the flag it comes back holding is whatever was true
    /// before it was suspended. Android covers it with the 15 s ping plus an
    /// `ON_RESUME` re-read of everything; iOS drops the link and re-mints
    /// instead, which is strictly better evidence than either.
    ///
    /// A re-mint matters as much as the reconnect does: while the app was away
    /// the person may have started room mode, the token may have been
    /// refused, or the gate's proof may have aged out, and
    /// ``SocketUpgrade/mint()`` is the only thing that re-asks all three.
    public func sceneReturned() {
        guard state.withLock(\.started) else { return }
        restart()
    }

    // MARK: the loop

    private func restart() {
        let dropped = state.withLock { current -> (any StateSocketLink)? in
            current.retry?.cancel()
            current.retry = nil
            current.backoff.reset()
            let link = current.link
            current.link = nil
            current.attempt += 1
            return link
        }
        dropped?.cancel()
        tell(connected: false)
        connect()
    }

    /// One attempt: read the address, ask the rule, open or park.
    ///
    /// Every closure the caller gave is run **outside** the lock, which is the
    /// invariant ``ReachabilityDriver/update(serverURL:connected:phase:)``
    /// states and the reason the attempt number is claimed in one acquisition
    /// and the link stored in another.
    private func connect() {
        let address = base()
        let startedAt = clock()
        let claimed = state.withLock { current -> (Int64, (any StateSocketLink)?)? in
            guard current.started else { return nil }
            current.retry?.cancel()
            current.retry = nil
            current.paused = nil
            current.base = address
            current.attempt += 1
            current.attemptStartedMs = startedAt
            let stale = current.link
            current.link = nil
            return (current.attempt, stale)
        }
        guard let (attempt, stale) = claimed else { return }
        stale?.cancel()

        guard let url = stateSocketURL(base: address) else {
            return advance(.noServer, attempt: attempt)
        }
        guard let upgrade = upgrade(url) else {
            return advance(.addressUnusable, attempt: attempt)
        }
        // Asked HERE, immediately before the task is made, on every attempt —
        // `DomovoiClient.webSocketUpgrade(_:)`'s stated obligation. Never
        // hoisted into a stored property: the rule it re-asks includes room
        // mode, and a request minted before the phone became a room would put
        // the household token on the wire from inside one.
        guard let request = upgrade.mint() else {
            return advance(.notNow, attempt: attempt)
        }

        let link = transport.open(request, reporting: StateSocketEvents(
            opened: { [weak self] link in self?.opened(link, attempt: attempt) },
            text: { [weak self] text in self?.arrived(text, attempt: attempt) },
            failed: { [weak self] status, body in self?.failed(status, body, attempt: attempt) },
            closed: { [weak self] in self?.advance(.lost, attempt: attempt) }
        ))
        // The attempt may already be stale — a synchronous transport can have
        // opened, subscribed and even closed before `open` returned. Storing
        // the link anyway would leave the socket holding one nothing cancels,
        // so the check is the same one every callback makes.
        let superseded = state.withLock { current -> Bool in
            guard current.started, current.attempt == attempt else { return true }
            // A park or a retry that has already run for this attempt cleared
            // the slot deliberately; putting the link back would resurrect a
            // socket the rule just declined.
            if current.paused != nil || current.retry != nil { return true }
            current.link = link
            return false
        }
        if superseded { link.cancel() }
    }

    /// The upgrade succeeded: publish, then subscribe to everything.
    ///
    /// Android's order (`StateBus.kt:92-96`): reset the ladder, set
    /// `connected`, send the frame. ``socketNext(after:backoff:)`` does the
    /// first, ``advance(_:attempt:)`` the second.
    private func opened(_ link: any StateSocketLink, attempt: Int64) {
        guard state.withLock({ $0.started && $0.attempt == attempt }) else { return }
        advance(.opened, attempt: attempt)
        link.send(stateSocketSubscribeAll)
    }

    private func arrived(_ text: String, attempt: Int64) {
        let mine = state.withLock { current in current.started && current.attempt == attempt }
        guard mine, let parsed = StateEvent.parse(text) else { return }
        event(parsed)
    }

    private func failed(_ status: Int?, _ body: String?, attempt: Int64) {
        // Android: `response?.let { api.notePossiblePairingRefusal(it.code, it.message) }`
        // (`StateBus.kt:107`). Nothing is said when the request never got an
        // answer, which is every held and throttled verdict (map §8.2), so a
        // withheld token cannot be mistaken for a refused one.
        if let status { noteRefusal(status, body) }
        advance(.lost, attempt: attempt)
    }

    /// Apply ``socketNext(after:backoff:)`` to this attempt, if it is still
    /// the current one.
    private func advance(_ step: SocketStep, attempt: Int64) {
        /// What was decided inside the lock and is carried out after it.
        /// Nothing injected and nothing of the transport's runs while the
        /// lock is held — the invariant ``ReachabilityDriver`` states, and the
        /// reason a link to cancel travels out of here rather than being
        /// cancelled where it was found.
        struct Carry {
            var drop: (any StateSocketLink)?
            var tell: Bool?
            var retryAfterMs: Int64?
        }
        let carry = state.withLock { current -> Carry in
            guard current.started, current.attempt == attempt else { return Carry() }
            var out = Carry()
            switch socketNext(after: step, backoff: &current.backoff) {
            case .live:
                current.paused = nil
                if !current.connected {
                    current.connected = true
                    out.tell = true
                }
            case .park(let why):
                current.paused = why
                out.drop = current.link
                current.link = nil
                if current.connected {
                    current.connected = false
                    out.tell = false
                }
            case .retry(let afterMs):
                current.paused = nil
                out.drop = current.link
                current.link = nil
                if current.connected {
                    current.connected = false
                    out.tell = false
                }
                out.retryAfterMs = afterMs
            }
            return out
        }
        carry.drop?.cancel()
        if let tell = carry.tell { publish(tell) }
        if let afterMs = carry.retryAfterMs { scheduleRetry(afterMs: afterMs, attempt: attempt) }
    }

    /// `scheduleReconnect` (`StateBus.kt:118`): sleep, then try again. The
    /// sleep is the ladder's current rung, already taken by
    /// ``socketNext(after:backoff:)``.
    private func scheduleRetry(afterMs: Int64, attempt: Int64) {
        let task = Task { [weak self, wait] in
            do { try await wait(afterMs) } catch { return }
            guard !Task.isCancelled else { return }
            self?.retryIfStillOurs(attempt: attempt)
        }
        let stale: Task<Void, Never>? = state.withLock { current in
            guard current.started, current.attempt == attempt else { return task }
            current.retry?.cancel()
            current.retry = task
            return nil
        }
        stale?.cancel()
    }

    private func retryIfStillOurs(attempt: Int64) {
        let mine = state.withLock { current -> Bool in
            guard current.started, current.attempt == attempt else { return false }
            current.retry = nil
            return true
        }
        guard mine else { return }
        connect()
    }

    /// Publish without consulting the rule — ``restart()``'s one use, which
    /// knows the link is gone because it dropped it.
    private func tell(connected: Bool) {
        let changed = state.withLock { current -> Bool in
            guard current.connected != connected else { return false }
            current.connected = connected
            return true
        }
        if changed { publish(connected) }
    }
}

/// The socket's two outputs, on their way to whoever is listening.
///
/// ``AppContainer`` builds the socket in `init`, where ``ShellModel`` does
/// not exist yet; the model connects afterwards. The same forward reference
/// ``DeferredSink`` and ``ShellModel/VerdictRelay`` exist for, resolved the
/// same way — and, like them, it **counts** what arrived before anything was
/// listening rather than pretending it cannot happen. It can: the container's
/// ``AppContainer/start()`` may open the socket before a shell is built, and
/// a connect that lands in that window is a connect whose capability refresh
/// and device register never happened.
///
/// ``missedConnections`` is the number to look at, and
/// `testAConnectBeforeAnybodyIsListeningIsCounted` is what says it is
/// counted. The app avoids the window by building the model before calling
/// `start()` (`DomovoiApp.init`), which is one line and is checkable by
/// reading it.
public final class SocketRelay: Sendable {

    private struct State {
        var connection: (@Sendable (Bool) -> Void)?
        var event: (@Sendable (StateEvent) -> Void)?
        var missedConnections = 0
        var missedEvents = 0
    }

    private let state = Mutex(State())

    public init() {}

    public var missedConnections: Int { state.withLock { $0.missedConnections } }
    public var missedEvents: Int { state.withLock { $0.missedEvents } }

    public func connect(
        connection: @Sendable @escaping (Bool) -> Void,
        event: @Sendable @escaping (StateEvent) -> Void
    ) {
        state.withLock {
            $0.connection = connection
            $0.event = event
        }
    }

    /// Drop the sinks: whoever was listening is going away, and a closure
    /// that reaches back into a dead shell is a leak with a callback.
    public func disconnect() {
        state.withLock {
            $0.connection = nil
            $0.event = nil
        }
    }

    func connectionChanged(_ connected: Bool) {
        let sink = state.withLock { current -> (@Sendable (Bool) -> Void)? in
            if current.connection == nil { current.missedConnections += 1 }
            return current.connection
        }
        sink?(connected)
    }

    func arrived(_ event: StateEvent) {
        let sink = state.withLock { current -> (@Sendable (StateEvent) -> Void)? in
            if current.event == nil { current.missedEvents += 1 }
            return current.event
        }
        sink?(event)
    }
}
