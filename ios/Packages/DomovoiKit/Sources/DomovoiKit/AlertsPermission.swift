import Foundation

// ---------------------------------------------------------------------------
// "Can this phone say so at all?" — the one permission the alerts need, the
// row on Home's timers card that says when they cannot, and what the person is
// told before iOS asks.
//
// Ported from `android/.../alerts/ExactAlarms.kt:74-98` and
// `ui/screens/home/HomeSections.kt:351-407` (read at `898b3af2`), **minus the
// exact-alarm half of both**:
//
// * `MAY_RING_LATE`, `timerAlertHint`'s `canExact` branch, `exactHintDismissed`
//   and everything in `ExactAlarms.kt` are not ported. An iPhone has no
//   exact-alarm grant: a scheduled notification is delivered on time, and a
//   row saying "timers may ring a few minutes late" would describe nothing
//   while its `allow alarms` link opened nothing (MAP §5.1).
// * The three "can I post?" checks collapse to one. Android has a channel per
//   kind, each silenceable on its own; iOS has one app-level authorization and
//   `UNNotificationCategory` is not a user-visible control, so a Settings row
//   offering "timers only" would control nothing (DECISIONS §8).
//
// So this file is the notifications-off case alone — which is the case Android
// shows first anyway, because nothing rings at all while it holds
// (`HomeSections.kt:400`).
// ---------------------------------------------------------------------------

/// What `UNUserNotificationCenter` says about this app, as the three answers
/// that change what is drawn.
///
/// `UNAuthorizationStatus` has five cases; `.ephemeral` (an App Clip) is
/// folded into ``authorized`` because an App Clip of this app does not exist
/// and, if one did, it could post. The distinction that matters here is not
/// "how was it granted" but **"is there anything left to ask?"**: iOS allows
/// exactly one system prompt per install, so ``notDetermined`` and ``denied``
/// need different controls.
public enum AlertsPermissionState: String, Equatable, Sendable, CaseIterable {

    /// Nobody has been asked yet. The system prompt is still available —
    /// once.
    case notDetermined

    /// The person said no, or notifications were turned off later. Only
    /// Settings can change it.
    case denied

    /// Notifications may be posted.
    case authorized

    /// Granted quietly (`.provisional`): notifications are delivered to the
    /// notification centre without a prompt. Posting works, so nothing is
    /// drawn — and nothing is *asked* either, which is the point of the state.
    case provisional

    /// Whether a notification posted now would be delivered — the one
    /// `canPost()` the three Android sinks collapse into.
    public var canPost: Bool { self == .authorized || self == .provisional }
}

/// What Home's timers card says about this phone's alerts, at most one line —
/// `TimerAlertHint` (`ExactAlarms.kt:77`), with `MAY_RING_LATE` gone.
public enum AlertsHint: String, Equatable, Sendable {

    /// Nothing to say: alerts post.
    case none

    /// Notifications are off for the app: nothing arrives here at all.
    case alertsOff
}

/// `timerAlertHint` (`ExactAlarms.kt:90`) — whether to say anything, and the
/// order it is said in.
///
/// The dismissal is read **after** the status, not before: a person who said
/// `not now` and later turned notifications on is not told anything, and a
/// person who said `not now` and still has them off is not nagged. Reversing
/// the two would hide the row for an install that has nothing to hide.
public func alertsHint(canPost: Bool, dismissed: Bool) -> AlertsHint {
    guard !canPost else { return .none }
    return dismissed ? .none : .alertsOff
}

/// What the fix control on the hint row does, which is **not the same control
/// in both states** (MAP §4.4).
public enum AlertsFix: String, Equatable, Sendable {

    /// `turn on` → the explain card, then the system prompt.
    case explainThenAsk

    /// `open Settings` → `UIApplication.openSettingsURLString`, the only place
    /// iOS lets an app send someone. There is no second system prompt.
    case openSettings
}

/// The whole of the row's and the card's decisions, as one value — so that a
/// view asks this and draws, rather than branching on a `UNAuthorizationStatus`
/// in a `body`.
///
/// ### Why the ask is here and not at launch
///
/// The item's scope forbids asking at launch, and the self-explaining moment
/// is Android's own: the timers card draws this row only when there are timers
/// to be alerted about (`HomeSections.kt:385`, `timersShown`). So the person
/// meets the ask on a card that is on screen **because the house has a running
/// timer**, after one sentence saying what will arrive
/// (``AlertsStrings/askBody``).
public struct AlertsPermissionAsk: Equatable, Sendable {

    public let state: AlertsPermissionState

    public init(state: AlertsPermissionState) {
        self.state = state
    }

    /// The hint row's sentence, or nil when nothing is drawn.
    public func line(dismissed: Bool) -> String? {
        alertsHint(canPost: state.canPost, dismissed: dismissed) == .alertsOff
            ? AlertsStrings.notificationsOff
            : nil
    }

    /// The fix link's words, or nil when nothing is drawn.
    public func fixLabel(dismissed: Bool) -> String? {
        guard line(dismissed: dismissed) != nil else { return nil }
        return fix == .openSettings ? AlertsStrings.openSettings : AlertsStrings.turnOn
    }

    /// Which control the fix link is.
    public var fix: AlertsFix { state == .denied ? .openSettings : .explainThenAsk }

    /// Whether tapping the fix link should put the explain card on screen.
    /// **False once the answer is `.denied`**: the card's button says
    /// `ask iOS`, and iOS would not ask, so the card would be a promise the
    /// app cannot keep. The link goes straight to Settings instead.
    public var showsExplainCard: Bool { fix == .explainThenAsk }
}
