import Foundation

// ---------------------------------------------------------------------------
// The mirror: every running timer and reminder on the active server gets a
// **local notification scheduled on this phone** at the moment it is due, so
// the phone says so even when the app is not running and the socket is down.
//
// Ported from `android/.../alerts/TimerAlarmMirror.kt:26-119` (read at
// `898b3af2`), where the same book arms `AlarmManager` alarms.
//
// ### What iOS does instead of an alarm, and what that costs
//
// Android's alarm wakes *code*, which asks the server what really happened
// before it posts (`TimerAlarmReceiver.kt`). A scheduled
// `UNNotificationRequest` wakes nothing: the words are written here, at
// schedule time, and iOS shows them. So the mirror's job is narrower and its
// book is the same — which timers have a notification pending, and what it
// says — because the book is still what tells a later sync what to withdraw.
//
// `useExact`, `AlarmSink`, `AndroidAlarms`, `MIRROR_MAX`'s exact/inexact
// branch and the boot re-arm are **not ported**: a pending notification
// request is delivered on time and survives a reboot, so there is nothing to
// grant and nothing to re-arm (MAP §4.2).
// ---------------------------------------------------------------------------

/// `MIRROR_MAX` (`TimerAlarmMirror.kt:27`) — the mirror never schedules more
/// than the fifty soonest.
///
/// Android's reason is politeness to `AlarmManager`; here there is a hard
/// platform number behind it as well. **iOS keeps the 64 soonest pending
/// requests per app and silently drops the rest**, and the help and incident
/// notifications share that budget — which is why
/// ``alertsDesiredAlarms(_:receivedAtMs:)`` returns the soonest *in due-time
/// order*: if anything is dropped it has to be the far-off ones.
public let alertsMirrorMax = 50

/// `MIRROR_MIN_LEAD_MS` (`:31`) — a timer due within a second of the server's
/// clock is not worth scheduling: the server is about to fire it and the live
/// path will say so.
public let alertsMirrorMinLeadMs: Int64 = 1_000

/// `MIRROR_MOVE_MS` (`:34`) — a trigger that moved by more than this is
/// re-scheduled.
public let alertsMirrorMoveMs: Int64 = 1_000

/// One scheduled notification, and what it says when it arrives —
/// `MirrorAlarm` (`TimerAlarmMirror.kt:37`).
///
/// **This is the one book that holds a reminder's own words**, which is why it
/// is stored in a file excluded from the backup rather than in `UserDefaults`
/// (``AlertsFileMirrorStore``, DECISIONS §9). Android's equivalent is
/// app-private in a store the app opts out of backups for
/// (`AlertStore.kt:21-22`).
public struct AlertsMirrorAlarm: Equatable, Sendable, Codable {

    public let timerID: Int64

    /// `"timer"` or `"reminder"` — ``alertsKind(reminder:)``.
    public let kind: String?
    public let label: String?
    public let message: String?
    public let roomID: String?
    public let createdAt: String?
    public let expiresAt: String?

    /// When it arrives, on **this phone's** clock: the server's due time,
    /// corrected by the offset (``alertsTriggerAtMs(expiresAtMs:serverNowMs:receivedAtMs:)``).
    public let triggerAtMs: Int64
    public let masked: Bool

    public init(
        timerID: Int64,
        kind: String? = nil,
        label: String? = nil,
        message: String? = nil,
        roomID: String? = nil,
        createdAt: String? = nil,
        expiresAt: String? = nil,
        triggerAtMs: Int64 = 0,
        masked: Bool = false
    ) {
        self.timerID = timerID
        self.kind = kind
        self.label = label
        self.message = message
        self.roomID = roomID
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.triggerAtMs = triggerAtMs
        self.masked = masked
    }
}

/// The stored mirror: whose notifications these are, and what is pending —
/// `MirrorBook` (`TimerAlarmMirror.kt:54`).
///
/// The server key is in the book rather than in a key per server, because a
/// switch has to disarm the **old** server's pending requests, and that needs
/// the one list that is actually scheduled, not a list per household.
public struct AlertsMirrorBook: Equatable, Sendable, Codable {

    public let serverKey: String?
    public let alarms: [AlertsMirrorAlarm]

    public init(serverKey: String? = nil, alarms: [AlertsMirrorAlarm] = []) {
        self.serverKey = serverKey
        self.alarms = alarms
    }

    /// The book with one timer's row dropped — `forget` (`AlertEngine.kt:356`).
    public func without(timerID: Int64) -> AlertsMirrorBook {
        AlertsMirrorBook(serverKey: serverKey, alarms: alarms.filter { $0.timerID != timerID })
    }
}

/// `triggerAtMs` (`TimerAlarmMirror.kt:71`) — when a timer due at
/// `expiresAtMs` on the server's clock arrives on this phone.
///
/// The server's clock read `serverNowMs` when this phone's read landed at
/// `receivedAtMs`, so **a phone three minutes slow rings three minutes
/// "early" by its own clock — which is on time.**
public func alertsTriggerAtMs(expiresAtMs: Int64, serverNowMs: Int64, receivedAtMs: Int64) -> Int64 {
    expiresAtMs - (serverNowMs - receivedAtMs)
}

/// `desiredAlarms` (`TimerAlarmMirror.kt:79`) — what a fresh
/// `GET /api/timers` asks the mirror to hold: both kinds (the owner decision
/// that plain timers follow the reminders' rule), only timers still more than
/// ``alertsMirrorMinLeadMs`` away on the **server's** clock, the
/// ``alertsMirrorMax`` soonest, **in due-time order**.
public func alertsDesiredAlarms(_ list: AlertsTimerList, receivedAtMs: Int64) -> [AlertsMirrorAlarm] {
    let serverNow = isoMs(list.serverNow) ?? receivedAtMs
    return list.timers
        .compactMap { timer -> (AlertsTimer, Int64)? in
            guard let at = isoMs(timer.expiresAt) else { return nil }
            return (timer, at)
        }
        .filter { $0.1 > serverNow + alertsMirrorMinLeadMs }
        .sorted { $0.1 < $1.1 }
        .prefix(alertsMirrorMax)
        .map { timer, at in
            AlertsMirrorAlarm(
                timerID: timer.id,
                kind: alertsKind(reminder: timer.isReminder),
                label: timer.label,
                message: timer.message,
                roomID: timer.roomID,
                createdAt: timer.createdAt,
                expiresAt: timer.expiresAt,
                triggerAtMs: alertsTriggerAtMs(expiresAtMs: at, serverNowMs: serverNow, receivedAtMs: receivedAtMs),
                masked: timer.masked
            )
        }
}

/// What a sync changes — `Reconcile` (`TimerAlarmMirror.kt:105`).
public struct AlertsReconcile: Equatable, Sendable {

    /// To schedule: new, or moved by more than ``alertsMirrorMoveMs``. A moved
    /// one goes back under the **same identifier**, which is how iOS replaces
    /// a pending request rather than adding a second notification for one
    /// timer.
    public let toSchedule: [AlertsMirrorAlarm]

    /// Timer ids whose pending request is withdrawn: it fired, or somebody
    /// cancelled the timer from any client.
    public let toCancel: [Int64]

    public init(toSchedule: [AlertsMirrorAlarm], toCancel: [Int64]) {
        self.toSchedule = toSchedule
        self.toCancel = toCancel
    }
}

/// `reconcile` (`TimerAlarmMirror.kt:110`).
public func alertsReconcile(desired: [AlertsMirrorAlarm], current: [AlertsMirrorAlarm]) -> AlertsReconcile {
    let want = Dictionary(desired.map { ($0.timerID, $0) }, uniquingKeysWith: { _, last in last })
    let have = Dictionary(current.map { ($0.timerID, $0) }, uniquingKeysWith: { _, last in last })
    // In `current`'s own order, so a test can say which went and in what
    // order, rather than reading a dictionary's.
    let toCancel = current.map(\.timerID).filter { want[$0] == nil }
    let toSchedule = desired.filter { alarm in
        guard let mine = have[alarm.timerID] else { return true }
        return abs(mine.triggerAtMs - alarm.triggerAtMs) > alertsMirrorMoveMs
    }
    return AlertsReconcile(toSchedule: toSchedule, toCancel: toCancel)
}
