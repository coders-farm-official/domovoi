import Foundation
import Synchronization

// ---------------------------------------------------------------------------
// Calendar sharing's state, one entry per server — the port of
// `android/.../calendarshare/ShareState.kt` (merge `bf05916b`) with the
// review's corrections (coordinator, 2026-11-14):
//
//  * revoked access — the iPhone's calendar permission taken back in
//    Settings, or the house refusing this phone — turns sharing **off**,
//    with a notice, and the house's copy is deleted where it still can be
//    (correction 7, and correction 2's revoked token);
//  * a ticked calendar that is no longer on the phone loses its tick and the
//    panel says `<name> is no longer on this phone` (correction 7);
//  * an unchanged snapshot is not sent again, except once a day so the
//    rolling window moves (correction 8).
// ---------------------------------------------------------------------------

/// Calendar sharing for one server (by normalised address): a server this
/// phone is paired with does not get the calendars just because another one
/// does (`ShareEntry`).
///
/// `pendingDelete`: sharing was turned off and the server has not yet
/// confirmed it deleted its copy. It stays true until a DELETE answers, so
/// the house's copy never stays behind silently.
public struct CalendarShareEntry: Equatable, Sendable, Codable {
    public var enabled: Bool = false
    /// `EKCalendar.calendarIdentifier` of the ticked calendars.
    public var calendarIDs: [String] = []
    /// The ticked calendars' titles when they were ticked, so a calendar
    /// that has gone can still be named.
    public var calendarNames: [String: String] = [:]
    public var pendingDelete: Bool = false
    /// When the server last took a send (epoch ms).
    public var lastSentAtMs: Int64?
    /// What the last call got: a plain sentence; nil when it went.
    public var lastError: String?
    /// Not before this (a 429's Retry-After), epoch ms.
    public var notBeforeMs: Int64?
    /// Events left out of the last send to stay under the server's caps.
    public var lastDropped: Int = 0
    /// ``CalendarSharePayload/fingerprint(_:)`` of the last snapshot sent.
    public var lastFingerprint: String?
    /// Something the person should know that is not a failure: sharing was
    /// turned off for them, or a calendar went. Cleared when they act.
    public var notice: String?

    public init(
        enabled: Bool = false, calendarIDs: [String] = [], calendarNames: [String: String] = [:],
        pendingDelete: Bool = false, lastSentAtMs: Int64? = nil, lastError: String? = nil,
        notBeforeMs: Int64? = nil, lastDropped: Int = 0, lastFingerprint: String? = nil,
        notice: String? = nil
    ) {
        self.enabled = enabled
        self.calendarIDs = calendarIDs
        self.calendarNames = calendarNames
        self.pendingDelete = pendingDelete
        self.lastSentAtMs = lastSentAtMs
        self.lastError = lastError
        self.notBeforeMs = notBeforeMs
        self.lastDropped = lastDropped
        self.lastFingerprint = lastFingerprint
        self.notice = notice
    }

    // Every key optional on the way in, so an entry an older build wrote
    // still decodes.
    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? false
        calendarIDs = try c.decodeIfPresent([String].self, forKey: .calendarIDs) ?? []
        calendarNames = try c.decodeIfPresent([String: String].self, forKey: .calendarNames) ?? [:]
        pendingDelete = try c.decodeIfPresent(Bool.self, forKey: .pendingDelete) ?? false
        lastSentAtMs = try c.decodeIfPresent(Int64.self, forKey: .lastSentAtMs)
        lastError = try c.decodeIfPresent(String.self, forKey: .lastError)
        notBeforeMs = try c.decodeIfPresent(Int64.self, forKey: .notBeforeMs)
        lastDropped = try c.decodeIfPresent(Int.self, forKey: .lastDropped) ?? 0
        lastFingerprint = try c.decodeIfPresent(String.self, forKey: .lastFingerprint)
        notice = try c.decodeIfPresent(String.self, forKey: .notice)
    }
}

/// What a pass does for a server now (`ShareAction`).
public enum CalendarShareAction: Equatable, Sendable {
    case put, delete, nothing
}

public enum CalendarShareRules {

    /// A day: an unchanged snapshot is still sent this often, so the window
    /// moves (correction 8).
    public static let resendMs: Int64 = 86_400_000

    /// Sharing on → send the set (once something is ticked, or to empty a
    /// copy sent before); off with a copy to remove → delete it.
    public static func action(_ e: CalendarShareEntry?) -> CalendarShareAction {
        guard let e else { return .nothing }
        if e.enabled { return (!e.calendarIDs.isEmpty || e.lastSentAtMs != nil) ? .put : .nothing }
        return e.pendingDelete ? .delete : .nothing
    }

    /// Turned on: nothing is waiting to be deleted any more (the next send
    /// replaces the house's copy whole), and any old notice is read.
    public static func turnOn(_ e: CalendarShareEntry?) -> CalendarShareEntry {
        var next = e ?? CalendarShareEntry()
        next.enabled = true
        next.pendingDelete = false
        next.lastError = nil
        next.notice = nil
        return next
    }

    /// Turned off: the house's copy must go.
    public static func turnOff(_ e: CalendarShareEntry?) -> CalendarShareEntry {
        var next = e ?? CalendarShareEntry()
        next.enabled = false
        next.pendingDelete = true
        next.lastError = nil
        return next
    }

    /// Ticked or unticked one calendar. Nothing is ticked until the person
    /// ticks it: turning sharing on sends nothing by itself.
    public static func toggle(_ e: CalendarShareEntry?, id: String, name: String, on: Bool) -> CalendarShareEntry {
        var next = e ?? CalendarShareEntry()
        var ids = next.calendarIDs.filter { $0 != id }
        if on {
            ids.append(id)
            next.calendarNames[id] = name
        } else {
            next.calendarNames[id] = nil
        }
        next.calendarIDs = Array(Set(ids)).sorted()
        next.notice = nil
        return next
    }

    /// Ticked calendars that are no longer on the phone lose their tick, and
    /// the notice names them (correction 7). Unchanged when none went.
    public static func dropVanished(_ e: CalendarShareEntry, onPhone: Set<String>) -> CalendarShareEntry {
        let gone = e.calendarIDs.filter { !onPhone.contains($0) }
        guard !gone.isEmpty else { return e }
        var next = e
        next.calendarIDs = e.calendarIDs.filter { onPhone.contains($0) }
        let names = gone.map { e.calendarNames[$0] ?? CalendarShareStrings.aCalendar }
        for id in gone { next.calendarNames[id] = nil }
        next.notice = CalendarShareStrings.noLongerOnPhone(names)
        return next
    }

    /// Whether a snapshot is worth sending: it differs from the last one
    /// sent, or a day has passed since (correction 8).
    public static func needsSend(_ e: CalendarShareEntry, fingerprint: String, nowMs: Int64) -> Bool {
        guard let sent = e.lastSentAtMs, e.lastFingerprint == fingerprint else { return true }
        return nowMs - sent >= resendMs
    }

    /// A send went: when, how many events were left out, and what it was.
    public static func sent(
        _ e: CalendarShareEntry, nowMs: Int64, dropped: Int, fingerprint: String
    ) -> CalendarShareEntry {
        var next = e
        next.lastSentAtMs = nowMs
        next.lastError = nil
        next.notBeforeMs = nil
        next.lastDropped = dropped
        next.lastFingerprint = fingerprint
        return next
    }

    /// The delete went (or there was nothing to delete).
    public static func deleted(_ e: CalendarShareEntry) -> CalendarShareEntry {
        var next = e
        next.pendingDelete = false
        next.lastError = nil
        next.notBeforeMs = nil
        next.lastSentAtMs = nil
        next.lastDropped = 0
        next.lastFingerprint = nil
        return next
    }

    /// A call failed: `error` for the settings line, and a 429's wait kept as
    /// a "not before". The pending delete stays set, so it is tried again.
    public static func failed(
        _ e: CalendarShareEntry, error: String, nowMs: Int64, retryAfterMs: Int64? = nil
    ) -> CalendarShareEntry {
        var next = e
        next.lastError = error
        if let retryAfterMs { next.notBeforeMs = nowMs + retryAfterMs }
        return next
    }

    /// The phone's calendar permission was taken back (correction 7):
    /// sharing is off, and the house's copy is deleted — the delete needs no
    /// calendar access.
    public static func permissionGone(_ e: CalendarShareEntry) -> CalendarShareEntry {
        var next = turnOff(e)
        next.notice = CalendarShareStrings.permissionGoneNotice
        return next
    }

    /// The house refused this phone (401/403: its token was revoked or it was
    /// unpaired there). Nothing more can be sent **or deleted** with that
    /// token, so sharing is off and the notice says who can remove the copy
    /// (correction 2).
    public static func refused(_ e: CalendarShareEntry) -> CalendarShareEntry {
        var next = e
        next.enabled = false
        next.pendingDelete = false
        next.lastError = nil
        next.notBeforeMs = nil
        next.notice = CalendarShareStrings.refusedNotice
        return next
    }

    /// How long to wait before the next call (0: go).
    public static func waitMs(_ e: CalendarShareEntry?, nowMs: Int64) -> Int64 {
        guard let nb = e?.notBeforeMs else { return 0 }
        return max(0, nb - nowMs)
    }

    /// Whether this server's entry may still have a copy on the server — so
    /// forgetting the server must send the delete first (correction 2).
    public static func mayHaveCopy(_ e: CalendarShareEntry?) -> Bool {
        guard let e else { return false }
        return e.pendingDelete || e.lastSentAtMs != nil || (e.enabled && !e.calendarIDs.isEmpty)
    }

    /// Plain words for a failed call, for the settings line
    /// (`ShareRules.errorText`).
    public static func errorText(_ status: Int?) -> String {
        guard let status else { return "couldn't reach your Domovoi; trying again later" }
        switch status {
        case 401, 403: return "your Domovoi refused this phone; check it is still paired"
        case 404: return "your Domovoi is too old to show phone calendars; update it"
        case 413: return "too many events to send; pick fewer calendars"
        case 429: return "sent a moment ago; trying again shortly"
        default:
            return status >= 500
                ? "your Domovoi had a problem; trying again later"
                : "your Domovoi turned it down (\(status))"
        }
    }

    /// Worth trying again by itself: busy, asked to wait, or out of reach.
    public static func retries(_ status: Int) -> Bool { status == 408 || status == 429 || status >= 500 }
}

/// The per-server entries, one JSON file in the app's own storage, kept out
/// of backups, replaced atomically (`ShareStore`). Thread safe.
public final class CalendarShareStore: Sendable {

    private struct State {
        var entries: [String: CalendarShareEntry]
        var onChange: (@Sendable () -> Void)?
    }

    private let url: URL?
    private let state: Mutex<State>

    /// `url` nil keeps everything in memory (a test's store).
    public init(url: URL?) {
        self.url = url
        var loaded: [String: CalendarShareEntry] = [:]
        if let url, let data = try? Data(contentsOf: url),
           let decoded = try? JSONDecoder().decode([String: CalendarShareEntry].self, from: data) {
            loaded = decoded
        }
        state = Mutex(State(entries: loaded, onChange: nil))
    }

    /// The app's file: `Application Support/Domovoi/calendar-share.json`.
    public static func forApp() -> CalendarShareStore {
        let base = (try? FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        )) ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return CalendarShareStore(url: base.appendingPathComponent("Domovoi/calendar-share.json"))
    }

    static func key(_ server: String) -> String { ServerCredentials.normalize(server) }

    public func get(_ server: String) -> CalendarShareEntry? {
        state.withLock { $0.entries[Self.key(server)] }
    }

    public func all() -> [String: CalendarShareEntry] { state.withLock { $0.entries } }

    /// Called after every change, from whichever thread made it.
    public func onChange(_ sink: @escaping @Sendable () -> Void) {
        state.withLock { $0.onChange = sink }
    }

    /// Change `server`'s entry (`f` gets nil when there is none).
    @discardableResult
    public func update(_ server: String, _ f: (CalendarShareEntry?) -> CalendarShareEntry) -> CalendarShareEntry {
        let k = Self.key(server)
        let (next, snapshot, sink) = state.withLock { (s: inout State) -> (CalendarShareEntry, [String: CalendarShareEntry], (@Sendable () -> Void)?) in
            let next = f(s.entries[k])
            s.entries[k] = next
            return (next, s.entries, s.onChange)
        }
        save(snapshot)
        sink?()
        return next
    }

    /// Drop `server`'s entry (the phone forgot that server).
    public func forget(_ server: String) {
        let k = Self.key(server)
        let (snapshot, sink, had) = state.withLock { (s: inout State) -> ([String: CalendarShareEntry], (@Sendable () -> Void)?, Bool) in
            let had = s.entries.removeValue(forKey: k) != nil
            return (s.entries, s.onChange, had)
        }
        guard had else { return }
        save(snapshot)
        sink?()
    }

    private func save(_ entries: [String: CalendarShareEntry]) {
        guard let url else { return }
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        guard let data = try? JSONEncoder().encode(entries) else { return }
        try? data.write(to: url, options: .atomic)
        // The calendars' names are the household's: not in the iCloud backup.
        // Set after every write, because an atomic write replaces the file.
        var excluded = URLResourceValues()
        excluded.isExcludedFromBackup = true
        var target = url
        try? target.setResourceValues(excluded)
    }
}
