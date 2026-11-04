import Foundation

// ---------------------------------------------------------------------------
// The timer alerts' pure rules: what a notification says, which fires of a
// batch are worth posting, and what the phone remembers so that the two paths
// (a fire pushed over the socket, and a notification scheduled ahead of time)
// post one timer once between them.
//
// Ported from `android/.../alerts/FireModels.kt:94-183`,
// `AlertStore.kt:41-148` and `AlertEngine.kt:43-61` (read at `898b3af2`).
// Everything here is a function of its arguments, which is how the engine's
// decisions get tested without a notification centre, a clock or a server.
// ---------------------------------------------------------------------------

// MARK: - the windows and the caps

/// `ALERTED_KEEP_MS` (`AlertStore.kt:29`) — an entry in the posted book older
/// than a day is forgotten.
public let alertsAlertedKeepMs: Int64 = 24 * 60 * 60 * 1000

/// `ALERTED_MAX` (`:32`) — and the book never holds more than this many.
public let alertsAlertedMax = 500

/// `FIRST_RUN_WINDOW_MS` (`:35`) — on a server's first catch-up only fires
/// younger than two minutes post. A phone meeting a house for the first time
/// is not handed its whole afternoon.
public let alertsFirstRunWindowMs: Int64 = 2 * 60 * 1000

/// `CATCH_UP_WINDOW_MS` (`:38`) — and on every later one, half an hour.
public let alertsCatchUpWindowMs: Int64 = 30 * 60 * 1000

/// `AlertsApi.FIRES_PAGE` (`AlertEngine.kt:45`).
public let alertsFiresPage = 50

/// `CATCH_UP_PAGES` (`AlertEngine.kt:61`) — a catch-up reads at most five
/// pages, so a phone that was away for a week does not read a ledger for ever.
public let alertsCatchUpPages = 5

// MARK: - the reads, as paths

/// The web backend's fire reads and the socket's event names, as the tests pin
/// them — `AlertsApi` (`AlertEngine.kt:43`), `HelpDeliveriesApi`
/// (`HelpDeliveries.kt:36`) and `IncidentsApi` (`IncidentEngine.kt:49`).
public enum AlertsPaths {

    /// Newest first with no `sinceID` (a first run); **ascending** after it,
    /// which is the route's own rule (`satellites.py:1239-1243`).
    public static func firesSince(_ sinceID: Int64?, limit: Int = alertsFiresPage) -> String {
        guard let sinceID else { return "/api/timers/fires?limit=\(limit)" }
        return "/api/timers/fires?since_id=\(sinceID)&limit=\(limit)"
    }

    /// The newest fire the server has: a catch-up that found nothing past the
    /// remembered id checks the server is not **behind** it.
    public static let newestFire = "/api/timers/fires?limit=1"

    public static let timers = "/api/timers"

    public static let fires = "/api/timers/fires"

    /// `timer_fires.changed` carries a **list** of fires, last hour, newest
    /// first, at most 20, unmasked (`realtime.py:953-963`).
    public static let firesEvent = "timer_fires.changed"

    /// `timers.changed` carries a list of timer rows and **no `server_now`**
    /// (`realtime.py:901-927`), which is why the mirror re-reads
    /// `GET /api/timers` rather than arming from the push.
    public static let timersEvent = "timers.changed"

    public static let helpDeliveries = "/api/help/deliveries"

    public static let helpEvent = "help_deliveries.changed"

    public static let incidents = "/api/containers/incidents"

    public static let containersEvent = "containers.changed"
}

// MARK: - words

/// `kindOf` (`FireModels.kt:136`).
public func alertsKind(reminder: Bool) -> String { reminder ? "reminder" : "timer" }

/// The contract's kind rule: a reminder iff it has a message, and the server
/// sends both spellings of that answer (`FireModels.kt:57`).
public func alertsIsReminder(_ fire: HomeFire) -> Bool {
    fire.isReminder || fire.kind == "reminder"
}

/// `fireTitle` (`FireModels.kt:99`) — `Timer done · kitchen`,
/// `Reminder · office`, `… · no room`.
///
/// All a shared screen ever shows, and — on iOS, where there is no public
/// version of a notification — all a locked screen shows when the person's own
/// *Show Previews* is anything but "always" (DECISIONS §6).
public func alertsFireTitle(kind: String?, roomID: String?) -> String {
    let room: String
    if let roomID, !roomID.isEmpty {
        room = roomID
    } else {
        room = AlertsStrings.noRoom
    }
    let prefix = kind == "reminder" ? AlertsStrings.reminderTitlePrefix : AlertsStrings.timerTitlePrefix
    return prefix + room
}

/// `timerNounFromSpan` (`FireModels.kt:106`) — what an unlabelled timer is
/// called, from how long it ran: `45s timer`, `10 min timer`, and `timer` when
/// that is not known.
public func alertsTimerNoun(createdAt: String?, dueAt: String?) -> String {
    guard let at = isoMs(dueAt), let created = isoMs(createdAt) else { return AlertsStrings.timerNoun }
    let total = Double(at - created) / 1000.0
    guard total > 0 else { return AlertsStrings.timerNoun }
    if total < 90 {
        return "\(Int64(total.rounded()))" + AlertsStrings.secondsTimerSuffix
    }
    return "\(Int64((total / 60).rounded()))" + AlertsStrings.minutesTimerSuffix
}

/// `alertBody` (`FireModels.kt:122`) — the notification's text line.
///
/// **A reminder's words are household speech**: never on a shared screen, and
/// absent when the server masked them, in which case there is nothing to say
/// beyond the title. A timer's label (`pasta`) is low-risk and always shows,
/// which is the same judgement Home's card makes.
public func alertsBody(
    reminder: Bool,
    label: String?,
    message: String?,
    masked: Bool,
    createdAt: String?,
    dueAt: String?,
    shared: Bool
) -> String? {
    if reminder {
        if shared || masked { return nil }
        if let message, !message.isEmpty { return message }
        return AlertsStrings.reminderNoun
    }
    if let label, !label.isEmpty { return label }
    return alertsTimerNoun(createdAt: createdAt, dueAt: dueAt)
}

/// `fireBody` (`FireModels.kt:134`).
public func alertsFireBody(_ fire: HomeFire, shared: Bool) -> String? {
    alertsBody(
        reminder: alertsIsReminder(fire),
        label: fire.label,
        message: fire.message,
        masked: fire.masked,
        createdAt: fire.createdAt,
        dueAt: fire.dueAt,
        shared: shared
    )
}

/// One notification about a timer, before ``AlertsNotifier`` builds it —
/// `AlertContent` (`FireModels.kt:155`).
///
/// ### Two differences from the Kotlin, both platform
///
/// 1. **There is no `publicTitle`.** Android posts a second, redacted
///    notification as the lock screen's version; iOS has one title and one
///    body and no API to supply an alternative (DECISIONS §6). What is written
///    here is what a locked screen may show, which is why ``body`` is nil
///    wherever Android's `text` was.
/// 2. **``subtitle`` is only ever the server's own summary** (`heard in
///    garage`), which carries no speech. Android's other two subtitles —
///    `going off now`, `couldn't reach Domovoi to confirm` — are written by
///    code that runs as the alarm rings, and nothing of this app runs then
///    (DECISIONS §3), so a scheduled alert has no subtitle at all.
public struct AlertsContent: Equatable, Sendable {

    /// The **timer's** id, not the fire's: it is the notification's slot, so
    /// that the pushed fire and the scheduled alert land in the same one
    /// (`TimerNotifier.kt:119`).
    public let timerID: Int64
    public let title: String
    public let subtitle: String?
    public let body: String?

    /// When it went off, on **this phone's** clock, for the notification's
    /// timestamp. Nil when the server did not say.
    public let whenMs: Int64?

    public init(timerID: Int64, title: String, subtitle: String? = nil, body: String? = nil, whenMs: Int64? = nil) {
        self.timerID = timerID
        self.title = title
        self.subtitle = subtitle
        self.body = body
        self.whenMs = whenMs
    }
}

/// `fireContent` (`FireModels.kt:164`) — a fire the server recorded.
///
/// `serverOffsetMs` is the server's clock minus this phone's, so the
/// notification's time is in the phone's own terms.
public func alertsFireContent(_ fire: HomeFire, shared: Bool, serverOffsetMs: Int64) -> AlertsContent {
    AlertsContent(
        timerID: fire.timerID,
        title: alertsFireTitle(kind: alertsKind(reminder: alertsIsReminder(fire)), roomID: fire.roomID),
        subtitle: shared ? nil : (fire.summary?.isEmpty == false ? fire.summary : nil),
        body: shared ? nil : alertsFireBody(fire, shared: false),
        whenMs: isoMs(fire.firedAt).map { $0 - serverOffsetMs }
    )
}

/// `alarmContent` (`FireModels.kt:177`) — the words written **when the
/// notification is scheduled**, for a timer that has not gone off yet.
///
/// No subtitle, for the reason ``AlertsContent`` gives: there is nothing to
/// say about a fire nobody has confirmed, and a line saying the server was (or
/// was not) reached would be a claim nothing measured.
public func alertsAlarmContent(_ alarm: AlertsMirrorAlarm, shared: Bool) -> AlertsContent {
    AlertsContent(
        timerID: alarm.timerID,
        title: alertsFireTitle(kind: alarm.kind, roomID: alarm.roomID),
        subtitle: nil,
        body: shared ? nil : alertsBody(
            reminder: alarm.kind == "reminder",
            label: alarm.label,
            message: alarm.message,
            masked: alarm.masked,
            createdAt: alarm.createdAt,
            dueAt: alarm.expiresAt,
            shared: false
        ),
        whenMs: alarm.triggerAtMs
    )
}

// MARK: - the posted book

/// `markInBook` (`AlertStore.kt:69`) — record that `timerID` on `serverKey`
/// was posted at `nowMs`.
///
/// Answers the next book (pruned of malformed entries and of anything older
/// than ``alertsAlertedKeepMs``, at most ``alertsAlertedMax``) and whether
/// **this call** added it. False means some path already posted this timer, so
/// this one must not: it is the whole of the "one timer, one notification"
/// rule, and both the socket's fire and a scheduled alert's arrival ask it.
public func alertsMarkInBook(
    _ entries: [String],
    serverKey: String,
    timerID: Int64,
    nowMs: Int64
) -> (entries: [String], added: Bool) {
    let kept = entries.filter { entry in
        // Exactly `<serverKey>|<timerId>|<epochMs>`: the Kotlin's two tests
        // (a readable time, and two separators) in one, so an entry written by
        // some other version of this code is dropped rather than trusted.
        let parts = entry.split(separator: "|", omittingEmptySubsequences: false)
        guard parts.count == 3, let at = Int64(parts[2]) else { return false }
        return nowMs - at < alertsAlertedKeepMs
    }
    let prefix = "\(serverKey)|\(timerID)|"
    if kept.contains(where: { $0.hasPrefix(prefix) }) { return (kept, false) }
    return (Array((kept + ["\(prefix)\(nowMs)"]).suffix(alertsAlertedMax)), true)
}

/// Whether the book already holds `timerID` for this server — `wasAlerted`
/// (`AlertStore.kt:181`).
public func alertsWasAlerted(_ entries: [String], serverKey: String, timerID: Int64) -> Bool {
    entries.contains { $0.hasPrefix("\(serverKey)|\(timerID)|") }
}

/// `forgetServer` (`AlertStore.kt:135`) — the book without this server's
/// entries. Its timer ids started again with its fire history, so an old entry
/// would hush a new timer.
public func alertsForgetServer(_ entries: [String], serverKey: String) -> [String] {
    entries.filter { !$0.hasPrefix("\(serverKey)|") }
}

// MARK: - where this phone is in a server's history

/// `historyRestarted` (`AlertStore.kt:99`) — whether this batch shows the
/// server's fire history started again: a fire at or below the remembered id
/// went off **after** the remembered one. Ids only grow within one history, so
/// this is a rebuilt or restored database on the same address.
public func alertsHistoryRestarted(fires: [HomeFire], seen: Int64?, seenAtMs: Int64?) -> Bool {
    guard let seen, let seenAtMs else { return false }
    return fires.contains { fire in
        guard fire.id <= seen, let at = isoMs(fire.firedAt) else { return false }
        return at > seenAtMs
    }
}

/// `historyBehind` (`AlertStore.kt:112`) — a catch-up past `seen` found
/// nothing: is the server's newest fire **behind** what this phone remembers?
///
/// `windowed` is the field that makes this answerable at all: an answer that
/// reached back only 600 seconds (`window_sec`, what a caller without a
/// household credential gets) says nothing about the history — only that
/// nothing went off lately. Flattened to "empty means empty", this rule would
/// make an unpaired phone start over on every quiet evening.
public func alertsHistoryBehind(newest: HomeFire?, seen: Int64, windowed: Bool = false) -> Bool {
    guard let newest else { return seen > 0 && !windowed }
    return newest.id < seen
}

/// What to post from a batch of fires, and the mark afterwards — `FirePlan`
/// (`AlertStore.kt:128`).
public struct AlertsFirePlan: Equatable, Sendable {

    public let post: [HomeFire]
    public let seen: Int64

    /// The `fired_at` of the fire ``seen`` names, when the batch carried it.
    public let seenAt: String?

    public init(post: [HomeFire], seen: Int64, seenAt: String? = nil) {
        self.post = post
        self.seen = seen
        self.seenAt = seenAt
    }
}

/// `planFires` (`AlertStore.kt:133`) — which of `fires` to post, given the
/// highest id already processed for this server (`seen`, nil on its first run)
/// and the **server's** clock.
///
/// * First run: nothing older than ``alertsFirstRunWindowMs`` posts — the mark
///   jumps to the newest fire older than that, and only the fires after it
///   that are younger post.
/// * Otherwise: every fire newer than `seen` and younger than `maxAgeMs`
///   posts; older ones only move the mark on.
///
/// A fire whose `fired_at` is unreadable **never** posts, and still moves the
/// mark: a row the phone cannot date is a row it cannot judge the age of, and
/// a notification is not the place to guess.
public func alertsPlanFires(
    fires: [HomeFire],
    seen: Int64?,
    serverNowMs: Int64,
    maxAgeMs: Int64
) -> AlertsFirePlan {
    func age(_ fire: HomeFire) -> Int64? { isoMs(fire.firedAt).map { serverNowMs - $0 } }
    let maxID = fires.map(\.id).max() ?? 0
    func firedAt(of id: Int64) -> String? { fires.first { $0.id == id }?.firedAt }

    guard let seen else {
        let base = fires.filter { fire in
            guard let age = age(fire) else { return true }
            return age > alertsFirstRunWindowMs
        }.map(\.id).max() ?? 0
        let post = fires.filter { fire in
            guard fire.id > base, let age = age(fire) else { return false }
            return age <= alertsFirstRunWindowMs
        }
        let next = max(base, maxID)
        return AlertsFirePlan(post: post.sorted { $0.id < $1.id }, seen: next, seenAt: firedAt(of: next))
    }
    let post = fires.filter { fire in
        guard fire.id > seen, let age = age(fire) else { return false }
        return age <= maxAgeMs
    }
    let next = max(seen, maxID)
    return AlertsFirePlan(post: post.sorted { $0.id < $1.id }, seen: next, seenAt: firedAt(of: next))
}

/// `serverOffsetMs` as the alerts keep it: the server's clock minus this
/// phone's, from the last read that said (`AlertEngine.kt:94`).
///
/// A phone three minutes slow is three minutes wrong about when a timer is
/// due, and the server is the clock a timer actually fires against.
public func alertsServerOffsetMs(serverNow: String?, receivedAtMs: Int64) -> Int64? {
    isoMs(serverNow).map { $0 - receivedAtMs }
}
