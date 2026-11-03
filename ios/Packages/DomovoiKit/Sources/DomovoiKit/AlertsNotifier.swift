import Foundation
import UserNotifications

// ---------------------------------------------------------------------------
// The notification side: the three Android notifiers (`TimerNotifier.kt`,
// `HelpNotifier.kt`, `IncidentNotifier.kt`, read at `898b3af2`) as one value
// type plus one seam.
//
// ``AlertsRequest`` is what a notification *is*, as data — which is what makes
// the engines testable: a package test asserts the title, the body, the slot
// and the delay, and nothing in the suite touches
// `UNUserNotificationCenter.current()` (which has no bundle to belong to under
// `swift test` and would trap).
//
// ### What iOS has instead of Android's four knobs
//
// | Android | here |
// |---|---|
// | `notify(tag, id, …)` — a slot per server and kind | one `identifier` string, built by ``alertsTimerIdentifier(serverKey:timerID:)`` and its two siblings. Re-adding the same identifier replaces a pending notification and updates a delivered one, which is the whole dedupe mechanism. |
// | `setGroup(GROUP)` | ``AlertsRequest/threadIdentifier`` |
// | `setPublicVersion(…)` + `VISIBILITY_PRIVATE` | nothing. iOS has one title and one body (DECISIONS §6); what is written is what a locked screen may show. |
// | `setWhen(content.whenMs)` | **nothing, deliberately.** A delivered notification is stamped with its delivery time and `UNNotificationContent` has no field for another one, so a fire that went off forty seconds ago reads as "now". Faking it is not available, and the alternative — putting the time in the body — would spend the one line a reminder's words need. |
// | channel importance | one app-level authorization. `.timeSensitive` would need the `com.apple.developer.usernotifications.time-sensitive` entitlement, which means an entitlements file, which means editing `project.yml` — forbidden to this item (NIGHT-RULES §2), so the level is left at `.active`. |
// ---------------------------------------------------------------------------

/// The slot a timer's notification posts under — `alertTag` + the timer id as
/// the notification id (`FireModels.kt:196`, `TimerNotifier.kt:119`).
///
/// The **timer's** id, not the fire's, so that the fire pushed over the socket
/// and the notification scheduled ahead of time land in the same slot and one
/// replaces the other.
public func alertsTimerIdentifier(serverKey: String, timerID: Int64) -> String {
    "timer_fire:\(serverKey):\(timerID)"
}

/// `helpTag` (`HelpNotifier.kt:35`).
public func alertsHelpIdentifier(serverKey: String, deliveryID: Int64) -> String {
    "help_delivery:\(serverKey):\(deliveryID)"
}

/// `incidentTag` (`ContainerIncidents.kt:392`).
public func alertsIncidentIdentifier(serverKey: String, incidentID: Int64) -> String {
    "container_incident:\(serverKey):\(incidentID)"
}

/// What a notification tap carries, and the only keys ``AlertsCenter`` reads
/// out of a `userInfo` — the port of Android's four intent extras
/// (`TimerNotifier.kt:20`, `HelpNotifier.kt:25-26`, `TimerAlarmMirror.kt:166`).
public enum AlertsPayloadKey {
    public static let route = "domovoi.route"
    public static let serverKey = "domovoi.server_key"
    public static let chatThread = "domovoi.chat_thread"
    public static let chatTitle = "domovoi.chat_title"
}

/// One notification, as data.
///
/// `userInfo` is `[String: String]` rather than `[AnyHashable: Any]`: it keeps
/// this value `Equatable` and `Sendable`, and the thread id — the one number in
/// it — is written as digits and read back with `Int64(_:)`, which is what the
/// tap path has to do anyway after a round trip through a plist.
public struct AlertsRequest: Equatable, Sendable {

    public let identifier: String
    public let title: String
    public let subtitle: String?
    public let body: String?

    /// The port of `setGroup` — one of ``AlertsRequest/timersThread``,
    /// ``helpThread`` or ``incidentsThread``.
    public let threadIdentifier: String
    public let userInfo: [String: String]

    /// Seconds from now, or nil to deliver at once.
    ///
    /// **Never zero or negative**: `UNTimeIntervalNotificationTrigger` throws
    /// on a non-positive interval, so a timer already due is an immediate post
    /// (``AlertsNotifier/scheduled(_:shared:serverKey:nowMs:)``).
    public let afterSeconds: Double?

    public init(
        identifier: String,
        title: String,
        subtitle: String? = nil,
        body: String? = nil,
        threadIdentifier: String,
        userInfo: [String: String] = [:],
        afterSeconds: Double? = nil
    ) {
        self.identifier = identifier
        self.title = title
        self.subtitle = subtitle
        self.body = body
        self.threadIdentifier = threadIdentifier
        self.userInfo = userInfo
        self.afterSeconds = afterSeconds
    }

    /// `TimerNotifier.GROUP` (`TimerNotifier.kt:45`).
    public static let timersThread = "timer_fires"
    public static let helpThread = "help"
    public static let incidentsThread = "container_incidents"
}

/// Builds the three kinds of request. Pure, so every word and every slot is a
/// package test rather than something only a phone can show.
public enum AlertsNotifier {

    /// A fire the server recorded: posted now, because it already happened.
    public static func fire(_ content: AlertsContent, serverKey: String) -> AlertsRequest {
        AlertsRequest(
            identifier: alertsTimerIdentifier(serverKey: serverKey, timerID: content.timerID),
            title: content.title,
            subtitle: content.subtitle,
            body: content.body,
            threadIdentifier: AlertsRequest.timersThread,
            // A timer alert opens Home, as Android's does
            // (`TimerNotifier.kt:79`).
            userInfo: [AlertsPayloadKey.route: "home", AlertsPayloadKey.serverKey: serverKey]
        )
    }

    /// A timer that has not gone off yet: the mirror's row, delivered when it
    /// is due.
    ///
    /// `nowMs` is this phone's clock, and the trigger is the difference — so a
    /// row whose trigger has already passed (the app was asleep through it, or
    /// the clock moved) posts at once rather than being thrown away. That is
    /// the nearest thing to Android's `setExactAndAllowWhileIdle` on a time in
    /// the past, which fires immediately.
    public static func scheduled(
        _ alarm: AlertsMirrorAlarm,
        shared: Bool,
        serverKey: String,
        nowMs: Int64
    ) -> AlertsRequest {
        let content = alertsAlarmContent(alarm, shared: shared)
        let after = Double(alarm.triggerAtMs - nowMs) / 1000.0
        return AlertsRequest(
            identifier: alertsTimerIdentifier(serverKey: serverKey, timerID: alarm.timerID),
            title: content.title,
            subtitle: nil,
            body: content.body,
            threadIdentifier: AlertsRequest.timersThread,
            userInfo: [AlertsPayloadKey.route: "home", AlertsPayloadKey.serverKey: serverKey],
            afterSeconds: after > 0 ? after : nil
        )
    }

    /// Help steps: the title is the thread's, the body says where the steps
    /// are, and the tap carries the thread so Chat can open it —
    /// `HelpNotifier.openIntent` (`HelpNotifier.kt:64`).
    public static func help(_ row: AlertsHelpDelivery, serverKey: String) -> AlertsRequest {
        let title = alertsHelpTitle(row)
        return AlertsRequest(
            identifier: alertsHelpIdentifier(serverKey: serverKey, deliveryID: row.id),
            title: title,
            subtitle: nil,
            body: AlertsStrings.helpBody,
            threadIdentifier: AlertsRequest.helpThread,
            userInfo: [
                AlertsPayloadKey.route: "chat",
                AlertsPayloadKey.serverKey: serverKey,
                AlertsPayloadKey.chatThread: String(row.threadID),
                AlertsPayloadKey.chatTitle: title,
            ]
        )
    }

    /// A container incident: the tap opens the Containers screen, as Android's
    /// does (`IncidentNotifier.kt:95`).
    public static func incident(_ content: AlertsIncidentContent, serverKey: String) -> AlertsRequest {
        AlertsRequest(
            identifier: alertsIncidentIdentifier(serverKey: serverKey, incidentID: content.incidentID),
            title: content.title,
            subtitle: nil,
            body: content.body,
            threadIdentifier: AlertsRequest.incidentsThread,
            userInfo: [AlertsPayloadKey.route: "containers", AlertsPayloadKey.serverKey: serverKey]
        )
    }
}

/// The seam over `UNUserNotificationCenter`: the five things the engines ask of
/// it, and nothing else.
///
/// `isActive` (`AlertEngine.kt:38`) is ``deliveredIdentifiers()``, which is
/// what makes `refreshIfShowing` portable: the live path may update
/// `heard in garage` under a notification **already on screen**, and must not
/// re-post one the person has swiped away.
public protocol AlertsNotificationCentre: Sendable {

    /// What iOS says now. Reading it costs nothing and never prompts.
    func authorizationState() async -> AlertsPermissionState

    /// Put the **system** prompt on screen, once per install, and answer what
    /// came back. Only ever called from the explain card's own button
    /// (``AlertsPermissionAsk``).
    func requestAuthorization() async -> AlertsPermissionState

    /// Add, or replace one under the same identifier.
    func add(_ request: AlertsRequest) async

    func pendingIdentifiers() async -> [String]
    func deliveredIdentifiers() async -> [String]

    func removePending(_ identifiers: [String]) async
    func removeDelivered(_ identifiers: [String]) async
}

/// ``AlertsNotificationCentre`` over the real `UNUserNotificationCenter`.
///
/// **Never built in a package test.** `UNUserNotificationCenter.current()`
/// requires a bundle identifier, and `swift test` has none: the suite drives a
/// fake centre instead, and this type's job is to be the thin, obvious
/// translation that a train's UI cases exercise for real.
/// `@unchecked Sendable` with the reason spelt out, as the house rule asks:
/// `UNUserNotificationCenter` is a process-wide singleton whose own methods are
/// documented as callable from any thread, and nothing here adds state of its
/// own — the one stored property is the centre, set once in `init`.
public final class AlertsSystemNotificationCentre: AlertsNotificationCentre, @unchecked Sendable {

    private let centre: UNUserNotificationCenter

    public init(centre: UNUserNotificationCenter = .current()) {
        self.centre = centre
    }

    /// `.ephemeral` is folded into ``AlertsPermissionState/authorized`` —
    /// ``AlertsPermissionState`` says why — and an unknown future case reads as
    /// `.denied`, which is the safe way round: it draws the row and sends the
    /// person to Settings rather than claiming alerts work.
    public static func state(from status: UNAuthorizationStatus) -> AlertsPermissionState {
        switch status {
        case .notDetermined: return .notDetermined
        case .denied: return .denied
        case .authorized, .ephemeral: return .authorized
        case .provisional: return .provisional
        @unknown default: return .denied
        }
    }

    public func authorizationState() async -> AlertsPermissionState {
        Self.state(from: await centre.notificationSettings().authorizationStatus)
    }

    public func requestAuthorization() async -> AlertsPermissionState {
        // The answer is read back from the settings rather than from the
        // call's `Bool`: `false` also comes back for an error, and the three
        // states the row draws from are what the settings say.
        _ = try? await centre.requestAuthorization(options: [.alert, .sound, .badge])
        return await authorizationState()
    }

    public func add(_ request: AlertsRequest) async {
        let content = UNMutableNotificationContent()
        content.title = request.title
        if let subtitle = request.subtitle { content.subtitle = subtitle }
        if let body = request.body { content.body = body }
        content.threadIdentifier = request.threadIdentifier
        content.userInfo = request.userInfo
        content.sound = .default
        var trigger: UNNotificationTrigger?
        if let after = request.afterSeconds, after > 0 {
            trigger = UNTimeIntervalNotificationTrigger(timeInterval: after, repeats: false)
        }
        try? await centre.add(
            UNNotificationRequest(identifier: request.identifier, content: content, trigger: trigger)
        )
    }

    public func pendingIdentifiers() async -> [String] {
        await centre.pendingNotificationRequests().map(\.identifier)
    }

    public func deliveredIdentifiers() async -> [String] {
        await centre.deliveredNotifications().map(\.request.identifier)
    }

    public func removePending(_ identifiers: [String]) async {
        centre.removePendingNotificationRequests(withIdentifiers: identifiers)
    }

    public func removeDelivered(_ identifiers: [String]) async {
        centre.removeDeliveredNotifications(withIdentifiers: identifiers)
    }
}
