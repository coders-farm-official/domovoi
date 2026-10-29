import Foundation
import Synchronization

/// ``StateSocketTransport`` over `URLSessionWebSocketTask`.
///
/// Android gets four things from OkHttp that this has to write out, because
/// `URLSession` does not offer them: the ping, the open/close callbacks, the
/// receive loop, and the upgrade's HTTP status on a failure. Each is below,
/// with what it is standing in for.
///
/// **What is NOT here, and that is the point of the split:** every decision.
/// Whether to connect, what the address is, what the request carries, whether
/// a failure earns a retry or a park — all of it is ``StateSocket`` and
/// ``socketNext(after:backoff:)``, which `swift test` drives against a
/// transport of its own. This file is the part a test cannot see, so it is
/// kept to the part that has nothing to decide.
public final class URLSessionStateSocketTransport: StateSocketTransport {

    private let session: URLSession
    private let pingMs: Int64

    /// - Parameters:
    ///   - session: the app's one session, so the socket inherits its
    ///     configuration. Android's upgrade goes through the app's one
    ///     `OkHttpClient` for the same reason (map §1.2).
    ///   - pingMs: ``stateSocketPingMs``.
    public init(session: URLSession, pingMs: Int64 = stateSocketPingMs) {
        self.session = session
        self.pingMs = pingMs
    }

    public func open(_ request: URLRequest, reporting events: StateSocketEvents) -> any StateSocketLink {
        let task = session.webSocketTask(with: request)
        // Chosen rather than inherited — see ``stateSocketMaximumFrameBytes``.
        // The platform's own default is this same 1 MiB today; setting it
        // means a platform that changes its default does not change this app,
        // and it is the bound ``StateEvent/parse(_:)`` also applies itself.
        task.maximumMessageSize = stateSocketMaximumFrameBytes
        let link = Link(task: task, events: events)
        task.resume()
        link.run(pingMs: pingMs)
        return link
    }

    /// One open socket, its receive loop and its ping.
    ///
    /// `@unchecked Sendable` with the reason, as the house rule asks: the one
    /// mutable field is the `Mutex`, `task` and `events` are `let`, and
    /// `URLSessionWebSocketTask` is documented thread-safe for `send`,
    /// `receive`, `sendPing` and `cancel`.
    final class Link: StateSocketLink, @unchecked Sendable {

        private struct State {
            var opened = false
            var finished = false
            var pump: Task<Void, Never>?
        }

        private let task: URLSessionWebSocketTask
        private let events: StateSocketEvents
        private let state = Mutex(State())

        init(task: URLSessionWebSocketTask, events: StateSocketEvents) {
            self.task = task
            self.events = events
        }

        func send(_ text: String) {
            task.send(.string(text)) { [weak self] error in
                // A send that fails is a dead socket. Android never sees this
                // case separately — OkHttp enqueues the frame and reports the
                // failure through `onFailure` — so it is reported the same way
                // here rather than silently dropped.
                if error != nil { self?.fail(nil, nil) }
            }
        }

        func cancel() {
            let pump = state.withLock { current -> Task<Void, Never>? in
                current.finished = true
                let pump = current.pump
                current.pump = nil
                return pump
            }
            pump?.cancel()
            task.cancel(with: .goingAway, reason: nil)
        }

        /// The receive loop and the ping, as one task.
        ///
        /// **Why the loop is the open callback too.** `URLSession` has a
        /// delegate method for "the socket opened"
        /// (`urlSession(_:webSocketTask:didOpenWithProtocol:)`), and using it
        /// would mean an `NSObject` delegate on the app's shared session —
        /// which already has ``RedirectDelegate`` per request and no shared
        /// delegate at all. The first successful `receive()` or the first
        /// successful ping is the same fact: the upgrade was accepted and
        /// frames are flowing. So the ping is sent first, and its success is
        /// what reports `opened`.
        ///
        /// Android's `onOpen` fires on the upgrade's `101`, strictly before
        /// any frame. The difference is one round trip of latency on the live
        /// dot, and nothing behaves differently: the subscribe-all frame goes
        /// out on `opened` either way, and the server treats "no frame" as
        /// subscribed to everything anyway (`web/backend/main.py:704`).
        func run(pingMs: Int64) {
            let pump = Task<Void, Never> { [weak self] in
                await self?.pump(pingMs: pingMs)
            }
            let stale = state.withLock { current -> Task<Void, Never>? in
                guard !current.finished else { return pump }
                current.pump = pump
                return nil
            }
            stale?.cancel()
        }

        /// **Nothing below here waits on the first ping.** It used to: `pump`
        /// was `await ping()` and then, *afterwards*, the ping loop and the
        /// receive loop. `ping()` is `withCheckedContinuation` around
        /// `sendPing`, with no deadline and no cancellation handler, so a
        /// server that completed the `101` and then answered nothing
        /// suspended this function for ever — and the 15 s ping loop that
        /// exists precisely to notice a half-open socket was never started,
        /// because it was downstream of the thing it was meant to catch.
        ///
        /// Now the first ping is just the pinger's first iteration, the
        /// receive loop starts immediately, and a **watchdog** ends the link
        /// if nothing has reported `opened` within
        /// ``stateSocketConnectBudgetMs``.
        ///
        /// It is a watchdog and deliberately **not** a race: a task group
        /// around `ping()` would have to await that child before returning,
        /// and `ping()`'s continuation is not cancellation-aware, so the
        /// group would hang in exactly the case the bound exists for.
        ///
        /// One consequence, stated because the report got it wrong the other
        /// way round: a refused upgrade can now end through `receive()`
        /// throwing as well as through the failed ping, so
        /// ``endedFromTask()`` sometimes does read the upgrade's real status.
        /// Which of the two wins is a race, and `claimFinish()` means exactly
        /// one does. Nothing downstream depends on it — a `403` on `/ws/state`
        /// still cannot raise the pairing screen, because
        /// ``DomovoiClient/notePossiblePairingRefusal(status:body:)`` needs
        /// the server's wording and an `HTTPURLResponse` carries none. That
        /// was map §11.6's real reason all along; it used to also be true by
        /// accident, because the status was never read at all.
        private func pump(pingMs: Int64) async {
            let watchdog = Task { [weak self] in
                do {
                    try await Task.sleep(for: .milliseconds(stateSocketConnectBudgetMs))
                } catch { return }
                self?.failIfNeverOpened()
            }
            defer { watchdog.cancel() }
            // The ping's own loop, beside the receive loop: `receive()` blocks
            // until a frame arrives, and a half-open socket sends none, so the
            // thing that notices has to be elsewhere.
            let pinger = Task { [weak self] in
                await self?.ping()
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .milliseconds(pingMs)) } catch { return }
                    guard !Task.isCancelled else { return }
                    await self?.ping()
                }
            }
            defer { pinger.cancel() }
            while !Task.isCancelled {
                do {
                    let message = try await task.receive()
                    reportOpenedOnce()
                    // Written as an `if` rather than an exhaustive `switch`
                    // with `@unknown default`, which warns: `Message` is a
                    // Swift enum and the compiler knows both cases. Binary
                    // frames fall into the `else` and are ignored, as Android
                    // ignores them — it does not override
                    // `onMessage(ByteString)` at all (map §1.3) — because the
                    // server sends JSON text.
                    if case .string(let text) = message { events.text(text) }
                } catch {
                    // A cancelled socket is this app closing it, not a
                    // failure: `cancel()` has already said so.
                    if state.withLock(\.finished) { return }
                    endedFromTask()
                    return
                }
            }
        }

        private func ping() async {
            let ok: Bool = await withCheckedContinuation { continuation in
                task.sendPing { error in continuation.resume(returning: error == nil) }
            }
            if state.withLock(\.finished) { return }
            if ok {
                reportOpenedOnce()
            } else {
                fail(nil, nil)
            }
        }

        /// The connect watchdog's verdict: an upgrade the server accepted and
        /// then said nothing on is a failure, not a connection.
        ///
        /// Reported as `failed(nil, nil)`, which is the same shape as every
        /// other answerless outcome, so ``StateSocket`` puts it on the
        /// backoff ladder — which is right: unlike a held token, this *is* a
        /// transport failure and retrying it is the thing to do.
        private func failIfNeverOpened() {
            let never = state.withLock { current in !current.finished && !current.opened }
            guard never else { return }
            fail(nil, nil)
        }

        /// `opened` exactly once per link, whichever of the ping and the first
        /// frame arrives first.
        private func reportOpenedOnce() {
            let first = state.withLock { current -> Bool in
                guard !current.finished, !current.opened else { return false }
                current.opened = true
                return true
            }
            if first { events.opened(self) }
        }

        /// The receive loop ended: say which of Android's two ways it was.
        ///
        /// OkHttp has `onClosed` (the peer closed, handshake complete) and
        /// `onFailure(…, response: Response?)` (everything else, with the
        /// upgrade's response when there was one), and `StateBus` treats them
        /// differently in exactly one respect: only the failure path notes a
        /// possible pairing refusal (`StateBus.kt:106-111`). `URLSession`
        /// collapses both into a thrown `receive()`, and the way to tell them
        /// apart is `closeCode`, which is `.invalid` until a close handshake
        /// completes.
        ///
        /// On the failure path the status is `task.response`'s, and **there is
        /// no body and no wire reason phrase to read** — see ``StateSocket``'s
        /// note, which is where the consequence for the pairing screen is
        /// written down.
        private func endedFromTask() {
            if task.closeCode != .invalid {
                guard claimFinish() else { return }
                tearDown()
                events.closed()
                return
            }
            fail((task.response as? HTTPURLResponse)?.statusCode, nil)
        }

        private func fail(_ status: Int?, _ body: String?) {
            guard claimFinish() else { return }
            tearDown()
            events.failed(status, body)
        }

        /// Put the real socket down, on the link's own initiative.
        ///
        /// ``cancel()`` is ``StateSocket`` telling this link to go; this is
        /// the link having decided for itself, and before it existed the two
        /// were not symmetrical: `endedFromTask()` and `fail()` set `finished`
        /// and reported, and **nothing cancelled the task or the pinger**. The
        /// only thing that actually tore the socket down was `StateSocket`
        /// dropping the link (`advance`'s `carry.drop?.cancel()`), which it
        /// does today — but that was a second undocumented cross-component
        /// dependency, and the watchdog above adds a path where this link
        /// decides to end while the socket still believes in it.
        private func tearDown() {
            let pump = state.withLock { current -> Task<Void, Never>? in
                let pump = current.pump
                current.pump = nil
                return pump
            }
            pump?.cancel()
            task.cancel(with: .goingAway, reason: nil)
        }

        /// Whether this caller is the first to end the link. One end per
        /// link, so a ping failing at the same moment as the receive loop
        /// cannot report two.
        private func claimFinish() -> Bool {
            state.withLock { current -> Bool in
                guard !current.finished else { return false }
                current.finished = true
                return true
            }
        }
    }
}
