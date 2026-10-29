import Foundation
import Observation

/// One read of the server, with its refresh policy — the port of
/// `net/ApiHooks.kt`'s `rememberApi` (73 lines, read at `6ff79c0e`).
///
/// Home has **no endpoint**. There is no `/api/home`; the screen is seven
/// independent reads composed client-side (`HomeScreen.kt:84`), and each one
/// answers, fails and refreshes on its own. This type is one of those reads.
/// Resist giving the screen a single `HomeState` that goes
/// loading/loaded/failed: it would lose the property that the rooms failing
/// does not blank the timers (map §10.1).
///
/// ### The five behaviours the Kotlin has that this copies
///
/// 1. **The server URL is an implicit key.** `rememberApi`'s
///    `LaunchedEffect(app.prefs.serverUrl.value, tick, *keys)` re-reads
///    everything on a switch. Here that is ``serverChanged()``, called by the
///    thing that knows the address changed.
/// 2. **`loading` is only true while there is no data yet**
///    (`loading = data == null`). A refresh of a section that already has
///    rows never shows a spinner.
/// 3. **A failure does not clear ``data``.** See the next heading; this is
///    the one that needed a decision rather than a transcription.
/// 4. **``refresh()`` cancels an in-flight fetch.** `tick++` re-keys the
///    effect, which cancels it, so two refreshes in quick succession mean one
///    request.
/// 5. **``answered`` is the screen's own predicate**, not the hook's:
///    `!loading && (data != null || error != null)` (`HomeScreen.kt:106`). It
///    separates "has not come back yet" from "came back empty", and it is why
///    sections can say they are checking rather than lying.
///
/// ### A failed read keeps its last value — ported, deliberately
///
/// Android never clears `data` on error, so "stale and silent" is the default
/// failure of every section that has succeeded once (map §10.4). **iOS keeps
/// that**, for one reason: a push-driven screen whose section blanks on a
/// momentary failure is worse than one that shows a reading a few seconds
/// old, and a `Result` would make the two indistinguishable to the view.
///
/// What iOS adds is that ``data`` and ``error`` are **both** readable at once
/// and ``isStale`` says so in one word, so a section *can* mark itself.
/// Nothing in this batch renders that mark: Home's header reads no section
/// data. The sections that will is listed as an open item in
/// `design-notes/ios/I1D1-REPORT.md` rather than described here as somebody
/// else's job.
@MainActor
@Observable
public final class ApiRead<Value: Sendable> {

    /// The last value that arrived, kept across a later failure.
    public private(set) var data: Value?

    /// True only while there has never been a value.
    public private(set) var loading = true

    /// The last failure's message, cleared by the next success.
    public private(set) var error: String?

    /// `HomeScreen.kt:106`'s `answered()`.
    public var answered: Bool { !loading && (data != nil || error != nil) }

    /// There is a value, and the newest attempt to replace it failed.
    public var isStale: Bool { data != nil && error != nil }

    /// How many requests this read has actually made, for a test that has to
    /// say "and nothing else was asked".
    public private(set) var fetches = 0

    private let fetch: @Sendable () async throws -> Value
    private let base: @Sendable () -> String
    private let eventTypes: Set<String>
    private var inFlight: Task<Void, Never>?
    private var lastBase: String?

    /// - Parameters:
    ///   - base: the saved server's address, the implicit key.
    ///   - eventTypes: the pushes that mean "re-read this". Every consumer
    ///     filters by `type` client-side, exactly as the web hooks do; nothing
    ///     ever sends a narrower subscription (map §1.2).
    ///   - fetch: the request. It must not swallow its own errors — the
    ///     message is what ``error`` holds.
    public init(
        base: @Sendable @escaping () -> String,
        eventTypes: Set<String> = [],
        fetch: @Sendable @escaping () async throws -> Value
    ) {
        self.base = base
        self.eventTypes = eventTypes
        self.fetch = fetch
    }

    /// Read it now, cancelling any read in flight.
    public func refresh() {
        let address = base()
        lastBase = address
        inFlight?.cancel()
        loading = data == nil
        fetches += 1
        inFlight = Task { @MainActor [weak self] in
            await self?.run()
        }
    }

    /// Re-read if the saved server is not the one the last read was for.
    ///
    /// Separate from ``refresh()`` because the Kotlin's key is the *value*,
    /// not the event: a `LaunchedEffect` keyed on a URL that has not changed
    /// does not re-run. A caller that shouts "the server changed" on every
    /// redraw would otherwise re-read seven endpoints every frame.
    public func serverChanged() {
        guard lastBase != base() else { return }
        refresh()
    }

    /// A push arrived. Re-reads when its type is one of ``eventTypes`` and
    /// does nothing otherwise.
    public func received(_ event: StateEvent) {
        guard eventTypes.contains(event.type) else { return }
        refresh()
    }

    /// Stop: the screen is going away. A read with a task still in flight
    /// would otherwise keep a request alive after its reader is gone, which
    /// is what `rememberApi`'s state dying with the composable does for free.
    public func stop() {
        inFlight?.cancel()
        inFlight = nil
    }

    private func run() async {
        do {
            let value = try await fetch()
            // A cancelled fetch must not publish: `refresh()` cancels the
            // task, but a `fetch` that ignores cancellation can still return.
            // Android gets this for nothing — `LaunchedEffect` re-keying
            // cancels the coroutine and `CancellationException` is rethrown
            // (`ApiHooks.kt:46`) — so the explicit check is the same rule.
            guard !Task.isCancelled else { return }
            data = value
            error = nil
        } catch is CancellationError {
            return
        } catch let trouble {
            guard !Task.isCancelled else { return }
            data = nil
            error = message(of: trouble)
        }
        loading = false
    }

    /// Kotlin's `e.message ?: "request failed"`.
    private func message(of error: any Error) -> String {
        if let domovoi = error as? DomovoiError { return domovoi.message }
        let text = (error as? LocalizedError)?.errorDescription ?? "\(error)"
        return text.isEmpty ? "request failed" : text
    }
}

/// React to pushes **without** re-reading — `ApiHooks.kt:67`'s
/// `OnStateEvents`, the `useStateEvents` analog.
///
/// One push on Home is consumed this way rather than as a nudge, and the
/// reason is in Android's own code: `satellites.wifi.changed` carries every
/// room's new link, and "every `/api/satellites` read opens an MPD connection
/// per room" while "every room sends one a minute"
/// (`HomeScreen.kt:151-153`). A six-room house would otherwise make six MPD
/// connections a minute for nothing. The payload's consumer is part 2's; this
/// is the subscription it needs.
///
/// It is a value and not a view modifier so the filtering is testable, which
/// is the whole reason `rememberApi` became a type here.
public struct StateEventFilter: Sendable {

    private let types: Set<String>
    private let onEvent: @Sendable (StateEvent) -> Void

    public init(_ types: Set<String>, onEvent: @Sendable @escaping (StateEvent) -> Void) {
        self.types = types
        self.onEvent = onEvent
    }

    public func received(_ event: StateEvent) {
        guard types.contains(event.type) else { return }
        onEvent(event)
    }
}
