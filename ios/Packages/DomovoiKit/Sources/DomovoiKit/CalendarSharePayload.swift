import CryptoKit
import Foundation

// ---------------------------------------------------------------------------
// "Share this phone's calendars with the house" (1.3.0): what goes in the one
// call that sends them, `PUT /api/calendar/phones/{device_id}` — the port of
// `android/.../calendarshare/SharePayload.kt` (merge `bf05916b`), **with the
// review's corrections** (coordinator, 2026-11-14), which Android is taking
// on `wf/android-cal-fixes`:
//
//  * no `account_name` is sent (an iPhone's account title is often an email
//    address), and a calendar whose own title looks like one is sent as
//    `Main calendar` (correction 5);
//  * an event the person declined is left out, and a location's web links
//    are taken out — one that was only a link reads `online meeting`
//    (correction 9);
//  * an event marked private is sent as `busy` with no place (correction 4)
//    — but EventKit gives an app **no** private flag, so on an iPhone
//    ``PhoneEventRow/isPrivate`` is always false and the disclosure says so;
//  * `phone_name` is cut to the server's 80 characters (correction 10);
//  * the body has a fingerprint without the window, so an unchanged snapshot
//    is not sent again (correction 8).
//
// And the server's V057 contract (main 8769500d, `docs/API_REFERENCE.md`):
// each event carries its `ical_uid` (EventKit's
// `calendarItemExternalIdentifier`) so one event two phones share is listed
// once, a private one goes as `busy: true` with **no** title or place, and the
// body carries a `generation` that only grows, so an upload that lands after
// the DELETE is refused (410) rather than stored.
//
// Pure: the app's EventKit reader turns `EKCalendar` / `EKEvent` into
// ``PhoneCalendarInfo`` / ``PhoneEventRow``, and every rule lives here, where
// the package tests reach it. Minimum data: a title, a time, a place, the
// calendar's name and colour. Never a description, a guest, an alarm or a
// URL.
// ---------------------------------------------------------------------------

/// One calendar on the phone (`EKCalendar`).
public struct PhoneCalendarInfo: Equatable, Sendable {
    /// `calendarIdentifier`.
    public let id: String
    /// `title`.
    public let name: String
    /// `source.title` — iCloud, Gmail, an Exchange address. **Shown on this
    /// phone only** (the panel's second line), never sent.
    public let accountName: String?
    /// `#rrggbb`, from `cgColor`.
    public let colorHex: String?

    public init(id: String, name: String, accountName: String? = nil, colorHex: String? = nil) {
        self.id = id
        self.name = name
        self.accountName = accountName
        self.colorHex = colorHex
    }
}

/// One occurrence of an event (`EKEventStore.events(matching:)` expands a
/// repeating event into one row per occurrence).
public struct PhoneEventRow: Equatable, Hashable, Sendable {
    /// `eventIdentifier` (shared by every occurrence of a repeating event).
    public let eventID: String
    public let calendarID: String
    /// Epoch ms. For an all-day event, **a UTC midnight** of its date — the
    /// shape Android's provider keeps and the server reads by date
    /// (``CalendarSharePayload/allDayBoundsMs(startMs:endMs:timeZone:)``).
    public let beginMs: Int64
    public let endMs: Int64?
    public let allDay: Bool
    public let title: String?
    public let location: String?
    /// `status == .canceled`: never sent.
    public let cancelled: Bool
    /// The person said no to it (their own attendee is `.declined`): never
    /// sent.
    public let declined: Bool
    /// Marked private by its calendar. **EventKit has no such property**, so
    /// the iPhone reader always says false; the rule is here so the body
    /// matches Android's, which can read it.
    public let isPrivate: Bool
    /// The event's iCalendar UID (`calendarItemExternalIdentifier`), shared by
    /// every copy of one event: two phones sharing one family calendar are
    /// listed once (V057).
    public let icalUID: String?

    public init(
        eventID: String, calendarID: String, beginMs: Int64, endMs: Int64?, allDay: Bool,
        title: String?, location: String?, cancelled: Bool = false, declined: Bool = false,
        isPrivate: Bool = false, icalUID: String? = nil
    ) {
        self.eventID = eventID
        self.calendarID = calendarID
        self.beginMs = beginMs
        self.endMs = endMs
        self.allDay = allDay
        self.title = title
        self.location = location
        self.cancelled = cancelled
        self.declined = declined
        self.isPrivate = isPrivate
        self.icalUID = icalUID
    }
}

/// The window sent: `startMs` to `endMs`.
public struct ShareWindow: Equatable, Sendable {
    public let startMs: Int64
    public let endMs: Int64
    public init(startMs: Int64, endMs: Int64) {
        self.startMs = startMs
        self.endMs = endMs
    }
}

/// The body, as the server's `PhoneCalendarSnapshot` (`schemas.py`).
public struct ShareSnapshotBody: Equatable, Sendable, Encodable {

    public struct Event: Equatable, Sendable, Encodable {
        public let uid: String
        /// Nil for a busy event: its title is not sent.
        public let title: String?
        public let startsAtMs: Int64
        public let endsAtMs: Int64?
        public let allDay: Bool
        /// Nil for a busy event: its place is not sent.
        public let location: String?
        public let icalUID: String?
        public let busy: Bool

        enum CodingKeys: String, CodingKey {
            case uid, title, location, busy
            case startsAtMs = "starts_at_ms"
            case endsAtMs = "ends_at_ms"
            case allDay = "all_day"
            case icalUID = "ical_uid"
        }

        // Nulls written out, as Android's `put(…, null)` does; `ical_uid`
        // only when there is one.
        public func encode(to encoder: any Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(uid, forKey: .uid)
            try c.encode(title, forKey: .title)
            try c.encode(startsAtMs, forKey: .startsAtMs)
            try c.encode(endsAtMs, forKey: .endsAtMs)
            try c.encode(allDay, forKey: .allDay)
            try c.encode(location, forKey: .location)
            try c.encodeIfPresent(icalUID, forKey: .icalUID)
            try c.encode(busy, forKey: .busy)
        }
    }

    public struct Calendar: Equatable, Sendable, Encodable {
        public let calendarID: String
        public let name: String
        public let color: String?
        public let events: [Event]

        enum CodingKeys: String, CodingKey {
            case name, color, events
            case calendarID = "calendar_id"
        }

        public func encode(to encoder: any Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(calendarID, forKey: .calendarID)
            try c.encode(name, forKey: .name)
            try c.encode(color, forKey: .color)
            try c.encode(events, forKey: .events)
        }
    }

    /// What the house records this phone as (`SharePayload.PLATFORM`; the
    /// server accepts `android` or `ios`).
    public static let platform = "ios"

    public let phoneName: String?
    public let windowStartMs: Int64
    public let windowEndMs: Int64
    /// V057: a number that only grows on this phone — the epoch ms of the
    /// read, or one past the last one sent
    /// (``CalendarShareRules/nextGeneration(_:nowMs:)``). Nil leaves it out.
    public var generation: Int64?
    public let calendars: [Calendar]

    public init(
        phoneName: String?, windowStartMs: Int64, windowEndMs: Int64,
        generation: Int64? = nil, calendars: [Calendar]
    ) {
        self.phoneName = phoneName
        self.windowStartMs = windowStartMs
        self.windowEndMs = windowEndMs
        self.generation = generation
        self.calendars = calendars
    }

    enum CodingKeys: String, CodingKey {
        case calendars, generation, platform
        case phoneName = "phone_name"
        case windowStartMs = "window_start_ms"
        case windowEndMs = "window_end_ms"
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Self.platform, forKey: .platform)
        try c.encodeIfPresent(phoneName, forKey: .phoneName)
        try c.encode(windowStartMs, forKey: .windowStartMs)
        try c.encode(windowEndMs, forKey: .windowEndMs)
        try c.encodeIfPresent(generation, forKey: .generation)
        try c.encode(calendars, forKey: .calendars)
    }
}

/// A body and what had to be left out to keep under the server's caps.
public struct BuiltSharePayload: Equatable, Sendable {
    public let body: ShareSnapshotBody
    public let calendars: Int
    public let events: Int
    /// Events left out (past the caps); 0 almost always.
    public let droppedEvents: Int
    /// Picked calendars left out (past ``CalendarSharePayload/maxCalendars``).
    public let droppedCalendars: Int
}

public enum CalendarSharePayload {

    /// The server's caps (413 past them).
    public static let maxCalendars = 25
    public static let maxEventsPerCalendar = 5_000
    public static let maxEventsTotal = 20_000
    public static let maxTitle = 200
    public static let maxLocation = 200
    /// `PhoneCalendarSnapshot.phone_name`'s `max_length` (correction 10).
    public static let maxPhoneName = 80
    public static let maxBodyBytes: Int64 = 8 * 1024 * 1024
    /// What the phone keeps under ``maxBodyBytes``, with room to spare.
    public static let bodyBudgetBytes: Int64 = maxBodyBytes * 9 / 10

    public static let dayMs: Int64 = 86_400_000
    public static let daysBack: Int64 = 30
    public static let daysAhead: Int64 = 180

    /// What a calendar whose title looks like an email address is sent as.
    public static let mainCalendar = "Main calendar"
    /// What a location that was only a web link is sent as.
    public static let onlineMeeting = "online meeting"
    /// A private event's title.
    public static let busy = "busy"

    /// 30 days back to 180 days ahead of `nowMs` (`SharePayload.window`).
    public static func window(nowMs: Int64) -> ShareWindow {
        ShareWindow(startMs: nowMs - daysBack * dayMs, endMs: nowMs + daysAhead * dayMs)
    }

    /// `"<eventIdentifier>:<begin>"`: one per occurrence of a repeating event.
    public static func uid(_ row: PhoneEventRow) -> String { "\(row.eventID):\(row.beginMs)" }

    /// `s` trimmed and cut to `max` UTF-16 units — Kotlin's `take`, the unit
    /// Android counts in — never through a surrogate pair; blank is nil.
    public static func clip(_ s: String?, _ max: Int) -> String? {
        guard let t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        if t.utf16.count <= max { return t }
        var out = String.UnicodeScalarView()
        var used = 0
        for scalar in t.unicodeScalars {
            let width = scalar.utf16.count
            if used + width > max { break }
            out.append(scalar)
            used += width
        }
        return String(out)
    }

    /// Whether a calendar's title is someone's email address (Google and
    /// Exchange name the main calendar after the account).
    public static func looksLikeEmail(_ s: String) -> Bool {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.contains(where: { $0.isWhitespace }) else { return false }
        let halves = t.split(separator: "@", omittingEmptySubsequences: false)
        guard halves.count == 2, !halves[0].isEmpty else { return false }
        let domain = halves[1]
        guard let dot = domain.firstIndex(of: "."), dot != domain.startIndex,
              domain.index(after: dot) != domain.endIndex
        else { return false }
        return true
    }

    /// The name sent for a calendar: its title, clipped; `Main calendar` when
    /// the title is an email address; `Calendar` when it has none.
    public static func sentName(_ calendar: PhoneCalendarInfo) -> String {
        guard let name = clip(calendar.name, maxTitle) else { return "Calendar" }
        return looksLikeEmail(name) ? mainCalendar : name
    }

    /// A location with its web links taken out (correction 9). One that was
    /// nothing but a link is `online meeting`; blank is nil.
    public static func sentLocation(_ s: String?) -> String? {
        guard let t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        var hadLink = false
        var kept: [Substring] = []
        for word in t.split(whereSeparator: { $0.isWhitespace }) {
            if isLink(word) {
                hadLink = true
            } else {
                kept.append(word)
            }
        }
        // What a link leaves behind: "Room 4 -", "Zoom: ", "(…)".
        var text: String = kept.joined(separator: " ")
        let edge = CharacterSet(charactersIn: " -–—:;,|/()[]<>")
        text = text.trimmingCharacters(in: edge)
        if text.isEmpty { return hadLink ? onlineMeeting : nil }
        return clip(text, maxLocation)
    }

    static func isLink(_ word: Substring) -> Bool {
        let w = word.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "()[]<>,;\"'"))
        return w.hasPrefix("http://") || w.hasPrefix("https://") || w.hasPrefix("www.")
            || w.hasPrefix("meet.google.com/") || w.contains("zoom.us/") || w.contains("teams.microsoft.com/")
    }

    /// The phone's name, within the server's 80 characters.
    public static func sentPhoneName(_ s: String?) -> String? { clip(s, maxPhoneName) }

    /// An all-day occurrence's bounds as Android sends them: the **UTC
    /// midnights** of its first date and of the date after its last.
    ///
    /// EventKit gives an all-day event as the phone's local midnight to
    /// 23:59:59 of its last day; the server reads an all-day bound by its
    /// date in UTC (`calendar.py` `_all_day_bound`), so sending the local
    /// instant would put a holiday on the previous day for anyone east of
    /// Greenwich.
    public static func allDayBoundsMs(
        startMs: Int64, endMs: Int64?, timeZone: TimeZone
    ) -> (start: Int64, end: Int64?) {
        let firstDay = CalendarDay(ms: startMs, in: timeZone)
        let start = firstDay.startMs(in: utc)
        guard let endMs else { return (start, nil) }
        let lastDay = CalendarDay(ms: endMs, in: timeZone)
        // Ending exactly on a midnight: that date is already exclusive.
        let exclusive = lastDay.startMs(in: timeZone) == endMs ? lastDay : lastDay.adding(days: 1)
        let end = max(exclusive, firstDay.adding(days: 1)).startMs(in: utc)
        return (start, end)
    }

    static let utc = TimeZone(secondsFromGMT: 0) ?? .gmt

    /// The events to keep when there are more than `cap`: what is still to
    /// come or under way first (soonest first), then the past (most recent
    /// first). Returned in time order (`SharePayload.keep`).
    public static func keep(_ rows: [PhoneEventRow], cap: Int, nowMs: Int64) -> [PhoneEventRow] {
        let ordered = stableSorted(rows) { $0.beginMs < $1.beginMs }
        if rows.count <= cap { return ordered }
        let ahead = ordered.filter { ($0.endMs ?? $0.beginMs) >= nowMs }
        let past = ordered.filter { ($0.endMs ?? $0.beginMs) < nowMs }
        let chosen = Array(ahead.prefix(max(0, cap)))
        let rest = cap - chosen.count
        let olds: [PhoneEventRow] = rest > 0
            ? Array(stableSorted(past) { $0.beginMs > $1.beginMs }.prefix(rest))
            : []
        return stableSorted(chosen + olds) { $0.beginMs < $1.beginMs }
    }

    static func stableSorted(
        _ rows: [PhoneEventRow], by before: (PhoneEventRow, PhoneEventRow) -> Bool
    ) -> [PhoneEventRow] {
        rows.enumerated()
            .sorted { (a, b) -> Bool in
                if before(a.element, b.element) { return true }
                if before(b.element, a.element) { return false }
                return a.offset < b.offset
            }
            .map { $0.element }
    }

    /// The body for the calendars in `picked` (by id; one the phone no longer
    /// has is simply not sent, and the server deletes it). Cancelled and
    /// declined occurrences are left out, every text is clipped to the caps,
    /// and past the event caps the furthest-past events go first.
    public static func build(
        phoneName: String?,
        window: ShareWindow,
        calendars: [PhoneCalendarInfo],
        rows: [PhoneEventRow],
        picked: Set<String>,
        nowMs: Int64,
        maxTotal: Int = maxEventsTotal
    ) -> BuiltSharePayload {
        var seenCalendars = Set<String>()
        let pickedCals: [PhoneCalendarInfo] = calendars
            .filter { picked.contains($0.id) && seenCalendars.insert($0.id).inserted }
            .sorted { $0.id < $1.id }
        let sent = Array(pickedCals.prefix(maxCalendars))
        var seenRows = Set<String>()
        let live: [PhoneEventRow] = rows.filter { row in
            !row.cancelled && !row.declined && seenRows.insert(row.calendarID + "\u{1}" + uid(row)).inserted
        }
        var byCal: [String: [PhoneEventRow]] = [:]
        for row in live { byCal[row.calendarID, default: []].append(row) }
        var perCal: [String: [PhoneEventRow]] = [:]
        for c in sent { perCal[c.id] = keep(byCal[c.id] ?? [], cap: maxEventsPerCalendar, nowMs: nowMs) }
        let offered = sent.reduce(0) { $0 + (byCal[$1.id]?.count ?? 0) }
        let total = perCal.values.reduce(0) { $0 + $1.count }
        if total > maxTotal {
            // Over the house-wide cap: the same rule across all calendars.
            let all: [PhoneEventRow] = sent.flatMap { perCal[$0.id] ?? [] }
            let kept = Set(keep(all, cap: maxTotal, nowMs: nowMs))
            for c in sent { perCal[c.id] = (perCal[c.id] ?? []).filter { kept.contains($0) } }
        }
        let events = perCal.values.reduce(0) { $0 + $1.count }
        let body = ShareSnapshotBody(
            phoneName: sentPhoneName(phoneName),
            windowStartMs: window.startMs,
            windowEndMs: window.endMs,
            calendars: sent.map { c in
                ShareSnapshotBody.Calendar(
                    calendarID: c.id,
                    name: sentName(c),
                    color: c.colorHex,
                    events: (perCal[c.id] ?? []).map(event)
                )
            }
        )
        return BuiltSharePayload(
            body: body,
            calendars: sent.count,
            events: events,
            droppedEvents: offered - events,
            droppedCalendars: pickedCals.count - sent.count
        )
    }

    /// One event. A private one goes as `busy: true` with **no** title and
    /// no place (correction 4, V057): the server shows it as "Busy".
    static func event(_ r: PhoneEventRow) -> ShareSnapshotBody.Event {
        ShareSnapshotBody.Event(
            uid: uid(r),
            title: r.isPrivate ? nil : (clip(r.title, maxTitle) ?? ""),
            startsAtMs: r.beginMs,
            endsAtMs: r.endMs,
            allDay: r.allDay,
            location: r.isPrivate ? nil : sentLocation(r.location),
            icalUID: clip(r.icalUID, 300),
            busy: r.isPrivate
        )
    }

    /// The body as sent: UTF-8 JSON, keys sorted so the same snapshot is
    /// the same bytes.
    public static func encode(_ body: ShareSnapshotBody) -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return (try? encoder.encode(body)) ?? Data("{}".utf8)
    }

    public static func bytes(_ body: ShareSnapshotBody) -> Int64 { Int64(encode(body).count) }

    /// The snapshot's fingerprint **without the window or the generation**
    /// (correction 8): both move every call, and a snapshot that differs only
    /// in them is not worth sending again — until a day has passed
    /// (``CalendarShareRules/needsSend(_:fingerprint:nowMs:)``).
    public static func fingerprint(_ body: ShareSnapshotBody) -> String {
        let bare = ShareSnapshotBody(
            phoneName: body.phoneName, windowStartMs: 0, windowEndMs: 0, generation: nil,
            calendars: body.calendars
        )
        let digest = SHA256.hash(data: encode(bare))
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    /// ``build(phoneName:window:calendars:rows:picked:nowMs:maxTotal:)``,
    /// then fewer events until the body fits `budget` bytes.
    public static func buildWithinSize(
        phoneName: String?,
        window: ShareWindow,
        calendars: [PhoneCalendarInfo],
        rows: [PhoneEventRow],
        picked: Set<String>,
        nowMs: Int64,
        budget: Int64 = bodyBudgetBytes
    ) -> BuiltSharePayload {
        var cap = maxEventsTotal
        var out = build(phoneName: phoneName, window: window, calendars: calendars, rows: rows,
                        picked: picked, nowMs: nowMs, maxTotal: cap)
        var size = bytes(out.body)
        while size > budget && out.events > 0 {
            // Shrink in proportion, at least by one tenth each round.
            let ratio = Double(budget) / Double(size)
            cap = max(0, min(Int(Double(out.events) * ratio * 0.95), out.events * 9 / 10))
            out = build(phoneName: phoneName, window: window, calendars: calendars, rows: rows,
                        picked: picked, nowMs: nowMs, maxTotal: cap)
            size = bytes(out.body)
        }
        return out
    }
}
