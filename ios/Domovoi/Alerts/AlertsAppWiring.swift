import DomovoiKit
import SwiftUI
import UIKit
import UserNotifications

/// The alerts' process-wide half: the three engines, the notification
/// delegate, and the one model Home's hint row reads.
///
/// A port of the three `DomovoiApplication.onCreate` lines
/// (`android/.../alerts/TimerAlerts.kt:97`, `HelpAlerts.kt:53`,
/// `ContainerIncidents.kt:64`) plus `MainActivity`'s notification-tap route
/// (`MainActivity.kt:131-141`).
///
/// ### Why this type exists at all
///
/// Everything here could be four lines in ``DomovoiApp/init()``. It is a type
/// instead so that the hook into that shared file is **two** lines — one
/// property and one call — which was the whole reason the mapper left the hook
/// unapplied (DECISIONS §2: "twenty branches editing two shared files is how a
/// train loses a branch to a conflict"). The round-1 Simulator train then
/// skipped five of this item's seven cases for exactly that absence, each with
/// `UNMEASURED` in its message, so the hook had to land somewhere; this file is
/// the smallest place it can land.
///
/// ### What is not here, and why there is no control for it
///
///  * **Room mode's `pause()`/`resume()`.** ``AlertsCenter/start()`` reads
///    ``HouseholdLock/roomModeActive`` once, so a phone that *launches* as a
///    room schedules nothing — which is Android's own `onCreate` guard. The
///    two mid-session transitions belong to whoever lands room mode and are
///    written out in DECISIONS §2.5; calling them from here would need an
///    observer on a shared type this item may not edit.
///  * **A catch-up on socket reconnect.** DECISIONS §2.3. Without it the port
///    catches up at start, on a server switch, and on the first frame after a
///    quiet gap — never on a bare reconnect. No wording anywhere claims
///    otherwise.
@MainActor
final class AlertsAppWiring {

    /// The three engines. Started here, once, and never stopped: the process
    /// ending is the only thing that ends them, as on Android.
    let centre: AlertsCenter

    /// What Home's hint row and the explain card read. It outlives every row
    /// and every card, which is the house rule a lazy row holding a `Task`
    /// breaks (NIGHT-RULES §3).
    let hint: AlertsHintModel

    /// Held, not merely assigned: `UNUserNotificationCenter.delegate` is
    /// **weak**, so a delegate owned by the initialiser would be gone by the
    /// first tap.
    private let tapDelegate: AlertsTapDelegate

    init(container: AppContainer) {
        let centre = AlertsCenter(container: container)
        self.centre = centre
        hint = AlertsHintModel(centre: centre) {
            // `.denied` is the only state whose fix link is `open Settings`
            // (``AlertsPermissionAsk/showsExplainCard``), and iOS will not
            // prompt a second time, so this is the app's own Settings page
            // and not a re-ask.
            guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
            UIApplication.shared.open(url)
        }
        tapDelegate = AlertsTapDelegate(centre: centre)
        UNUserNotificationCenter.current().delegate = tapDelegate
        // After the delegate, so a notification posted by the catch-up that
        // `start()` kicks off cannot be delivered with nobody to present it.
        centre.start()
    }
}

/// The notification delegate: present a fire that lands while the app is
/// open, and route a tap.
///
/// ### `willPresent` is not optional on iOS, whatever Android's shape suggests
///
/// iOS puts a local notification delivered while the app is in the
/// **foreground** straight into Notification Centre, silently, unless this
/// method asks for a banner. The default — and the behaviour with no delegate
/// at all — swallows the app's own alert. Android's notification arrives
/// whatever the app is doing (`AlertEngine.kt:101`), so returning `.banner`
/// here is what makes the two platforms agree rather than a preference.
/// DECISIONS §2's hook 4, second half.
///
/// ### Isolation
///
/// `nonisolated` on both methods on purpose. Whether
/// `UNUserNotificationCenterDelegate`'s requirements are `@MainActor` in the
/// SDK is the one question a package build cannot answer (DECISIONS §2), and a
/// `nonisolated` witness satisfies the requirement either way — it is callable
/// from anywhere, so it is never the more restrictive of the two. Neither body
/// needs the main actor: ``AlertsCenter`` is `Sendable` and
/// ``AlertsCenter/handleTap(_:)`` is a synchronous call on it.
final class AlertsTapDelegate: NSObject, UNUserNotificationCenterDelegate, @unchecked Sendable {

    /// `@unchecked` because `NSObject` cannot be checked, not because
    /// anything here is unsafe: the one stored property is a `let` of a
    /// `Sendable` type.
    private let centre: AlertsCenter

    init(centre: AlertsCenter) {
        self.centre = centre
        super.init()
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        // Which of the three routes, and whether a help thread belongs to
        // this house, are both ``AlertsCenter/handleTap(_:)``'s and are
        // package-tested; this is the line that reaches them.
        centre.handleTap(response.notification.request.content.userInfo)
    }
}

// MARK: - how the row finds the model

private struct AlertsHintModelKey: EnvironmentKey {
    static var defaultValue: AlertsHintModel? { nil }
}

extension EnvironmentValues {

    /// The one ``AlertsHintModel``, put in by ``DomovoiApp`` and read by
    /// Home's timers card.
    ///
    /// The environment rather than a parameter, and that is the smaller
    /// change: the row's place is inside `HomeTimersView`, whose only caller
    /// is `HomeView`, whose only caller is `RouteScreen`'s `home` slot —
    /// threading a parameter down would edit three shared files instead of
    /// one, and two of them belong to other items tonight.
    var alertsHintModel: AlertsHintModel? {
        get { self[AlertsHintModelKey.self] }
        set { self[AlertsHintModelKey.self] = newValue }
    }
}
