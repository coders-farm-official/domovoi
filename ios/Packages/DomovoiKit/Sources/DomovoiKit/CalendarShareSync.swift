import Foundation

/// One pass of calendar sharing for one server — the port of
/// `android/.../calendarshare/ShareSync.kt` (merge `bf05916b`), with the
/// review's corrections (coordinator, 2026-11-14):
///
///  * **Serialised per server, and the state re-read after every call**
///    (correction 3). Passes for one server run one after another, never
///    side by side, and a running upload is never cancelled; when a PUT
///    comes back and the entry now says off (it was switched off while the
///    PUT was out), the DELETE goes next — so the DELETE always lands after
///    the PUT it undoes.
///  * Calendar access taken back → off, and the copy deleted (correction 7).
///  * A ticked calendar gone from the phone → its tick dropped, and named.
///  * The house refusing this phone (401/403) → off, no retry (correction 2).
///  * An unchanged snapshot is skipped, except once a day (correction 8).
///
/// **Every write is keyed by the server the pass is for** — the `server`
/// argument, never "the active server" read after an await — and the two
/// calls are made `onlyTo` it by the caller's closures, so a pass for house A
/// that finishes after a switch to house B neither sends to B nor records
/// into B's entry (RULES: read-after-await).
///
/// No EventKit and no UIKit: the phone's calendars and the two calls come in
/// as closures, so the package tests drive it whole.
public actor CalendarShareSync {

    public enum Outcome: Equatable, Sendable {
        case done
        /// Try again later by itself: out of reach, busy, or asked to wait
        /// (with how long, when the server said).
        case retry(afterMs: Int64?)
    }

    public typealias Calendars = @Sendable () -> [PhoneCalendarInfo]?
    public typealias Events = @Sendable (ShareWindow, Set<String>) -> [PhoneEventRow]?
    public typealias Put = @Sendable (_ server: String, _ body: Data) async throws -> Void
    public typealias Delete = @Sendable (_ server: String) async throws -> Void

    private let store: CalendarShareStore
    private let calendars: Calendars
    private let events: Events
    private let put: Put
    private let delete: Delete
    private let phoneName: @Sendable () -> String?
    /// Whether this phone may send a snapshot to `server` now (not a shared
    /// screen, not a room). A delete is always allowed.
    private let maySend: @Sendable (String) -> Bool
    private let now: @Sendable () -> Int64

    /// The last pass queued per server; the next one waits for it.
    private var tails: [String: Task<Outcome, Never>] = [:]

    public init(
        store: CalendarShareStore,
        calendars: @escaping Calendars,
        events: @escaping Events,
        put: @escaping Put,
        delete: @escaping Delete,
        phoneName: @escaping @Sendable () -> String?,
        maySend: @escaping @Sendable (String) -> Bool = { _ in true },
        now: @escaping @Sendable () -> Int64 = { Int64((Date().timeIntervalSince1970 * 1000).rounded()) }
    ) {
        self.store = store
        self.calendars = calendars
        self.events = events
        self.put = put
        self.delete = delete
        self.phoneName = phoneName
        self.maySend = maySend
        self.now = now
    }

    /// Run a pass for `server` after any pass already queued for it.
    public func run(server: String) async -> Outcome {
        let key = ServerCredentials.normalize(server)
        let before = tails[key]
        let task = Task { [weak self] () -> Outcome in
            _ = await before?.value
            guard let self else { return .done }
            return await self.pass(key)
        }
        tails[key] = task
        return await task.value
    }

    /// Actions until the entry is settled: a PUT whose entry was switched off
    /// meanwhile is followed by the DELETE; a permission lost mid-send turns
    /// into the DELETE. Bounded, so a flapping entry cannot spin.
    private func pass(_ server: String) async -> Outcome {
        var last: Outcome = .done
        for _ in 0..<4 {
            let entry = store.get(server)
            let action = CalendarShareRules.action(entry)
            if action == .nothing { return .done }
            let wait = CalendarShareRules.waitMs(entry, nowMs: now())
            if wait > 0 { return .retry(afterMs: wait) }
            switch action {
            case .put: last = await send(server)
            case .delete: last = await remove(server)
            case .nothing: return .done
            }
            if case .retry = last { return last }
            // Re-read: anything still to do (switched off while the PUT was
            // out, or the permission went) goes now, in order.
            let after = CalendarShareRules.action(store.get(server))
            if after == .nothing { return .done }
            if after == .put, action == .put { return .done }
        }
        return last
    }

    private func send(_ server: String) async -> Outcome {
        guard maySend(server) else { return .done }
        let t = now()
        guard let onPhone = calendars() else {
            store.update(server) { CalendarShareRules.permissionGone($0 ?? CalendarShareEntry()) }
            return .done
        }
        let phoneIDs = Set(onPhone.map(\.id))
        let entry = store.update(server) { CalendarShareRules.dropVanished($0 ?? CalendarShareEntry(), onPhone: phoneIDs) }
        guard CalendarShareRules.action(entry) == .put else { return .done }
        let ids = Set(entry.calendarIDs).intersection(phoneIDs)
        let window = CalendarSharePayload.window(nowMs: t)
        guard let rows = events(window, ids) else {
            store.update(server) { CalendarShareRules.permissionGone($0 ?? CalendarShareEntry()) }
            return .done
        }
        let built = CalendarSharePayload.buildWithinSize(
            phoneName: phoneName(), window: window, calendars: onPhone, rows: rows, picked: ids, nowMs: t
        )
        let fingerprint = CalendarSharePayload.fingerprint(built.body)
        guard CalendarShareRules.needsSend(entry, fingerprint: fingerprint, nowMs: t) else { return .done }
        do {
            try await put(server, CalendarSharePayload.encode(built.body))
            store.update(server) {
                CalendarShareRules.sent($0 ?? CalendarShareEntry(), nowMs: self.now(), dropped: built.droppedEvents,
                                        fingerprint: fingerprint)
            }
            return .done
        } catch {
            return failed(server, error, at: t, deleting: false)
        }
    }

    private func remove(_ server: String) async -> Outcome {
        let t = now()
        do {
            try await delete(server)
            store.update(server) { CalendarShareRules.deleted($0 ?? CalendarShareEntry()) }
            return .done
        } catch {
            if Self.status(error) == 404 {
                // A server with no such route never had a copy to keep.
                store.update(server) { CalendarShareRules.deleted($0 ?? CalendarShareEntry()) }
                return .done
            }
            return failed(server, error, at: t, deleting: true)
        }
    }

    private func failed(_ server: String, _ error: any Error, at t: Int64, deleting: Bool) -> Outcome {
        let status = Self.status(error)
        if status == 401 || status == 403 {
            store.update(server) { CalendarShareRules.refused($0 ?? CalendarShareEntry()) }
            return .done
        }
        let wait = Self.retryAfterMs(error)
        store.update(server) {
            CalendarShareRules.failed($0 ?? CalendarShareEntry(), error: CalendarShareRules.errorText(status),
                                      nowMs: t, retryAfterMs: wait)
        }
        guard let status else { return .retry(afterMs: nil) }
        // A pending delete is always tried again; a refused send waits for
        // the next change or the next open.
        if deleting || CalendarShareRules.retries(status) { return .retry(afterMs: wait) }
        return .done
    }

    /// The HTTP status a failed call carried; nil for one that got no answer.
    static func status(_ error: any Error) -> Int? {
        (error as? DomovoiError)?.failure?.status
    }

    static func retryAfterMs(_ error: any Error) -> Int64? {
        if case .rateLimited(_, let waitMs)? = error as? DomovoiError { return waitMs }
        return nil
    }
}
