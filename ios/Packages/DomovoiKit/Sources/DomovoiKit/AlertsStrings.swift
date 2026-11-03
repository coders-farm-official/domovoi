import Foundation

/// Every word the alerts say — in a notification, or in the one row Home's
/// timers card draws about them — transcribed from the Kotlin with the file and
/// line each came from, the shape ``ChatStrings`` and ``ShellStrings`` have.
///
/// `AlertsStringsTests` reads the Kotlin off disk at test time and compares
/// **whole strings**, so a middle dot typed as a bullet or a clause dropped off
/// an end fails the suite rather than passing a containment check.
///
/// ### What is not here, and why there is no control for it
///
/// * **`Timers may ring a few minutes late on this phone`** and the
///   `allow alarms` link (`HomeSections.kt:404`, `ExactAlarms.kt:36-99`).
///   There is no exact-alarm grant on an iPhone, so the sentence would
///   describe nothing and the link would open nothing.
/// * **`couldn't reach Domovoi to confirm` and `going off now`**
///   (`FireModels.kt:144-147`). Both are written by code that runs *at the
///   moment the alarm rings*, and nothing of this app runs when a local
///   notification fires. A pre-scheduled alert carries no second line at all
///   rather than a line claiming the server was, or was not, reached
///   (DECISIONS §3).
/// * **The three channel names and descriptions** (`TimerNotifier.kt:43`,
///   `HelpNotifier.kt:43`, `IncidentNotifier.kt:40`). iOS has no notification
///   channels; nothing would show them.
/// * **`Domovoi sent you help steps`** (`HelpNotifier.kt:96`), which is
///   Android's *public* version of a help notification. iOS has no public
///   version (``alertsHelpTitle(_:)`` and the body below are what a locked
///   screen shows, under the person's own Show Previews setting), and help
///   never posts on a shared screen at all (`HelpDeliveries.kt:99-105`), so
///   the sentence has no surface to appear on.
///
/// Four sentences here have **no Android counterpart** and are marked
/// **iOS-only**, with the reason for each in
/// `design-notes/ios/overnight/alerts/IOS-ONLY-STRINGS.md`.
public enum AlertsStrings {

    // MARK: - a timer or reminder going off (`FireModels.kt:94-134`)

    /// `fireTitle` (`FireModels.kt:99-102`) — the two halves either side of
    /// the room. A reminder's title is all a shared screen ever shows.
    public static let reminderTitlePrefix = "Reminder · "
    public static let timerTitlePrefix = "Timer done · "

    /// `fireTitle`'s room for a timer set with none — the app, or dashboard
    /// chat (`:100`).
    public static let noRoom = "no room"

    /// `timerNounFromSpan` (`:106-112`) — what an unlabelled timer is called.
    /// `40s timer`, `3 min timer`, and `timer` when the span is unknown.
    public static let timerNoun = "timer"
    public static let secondsTimerSuffix = "s timer"
    public static let minutesTimerSuffix = " min timer"

    /// `alertBody` (`:131`) — a reminder whose words are empty. Not the same
    /// case as a masked one, which has no body at all.
    public static let reminderNoun = "reminder"

    // MARK: - help steps (`HelpDeliveries.kt:79-86`, `HelpNotifier.kt:97`)

    /// `helpTitle` (`HelpDeliveries.kt:82`) — a delivery whose thread has no
    /// title.
    public static let helpUntitled = "Help from Domovoi"

    /// `helpTitle` (`:85`) — prepended when the row's own title lacks it, so
    /// every help notification reads the way the assistant plan names it.
    public static let helpTitlePrefix = "Help: "

    /// `HelpNotifier.kt:97` — the body, which is the whole of what a help
    /// notification says beyond its title. The steps themselves stay in the
    /// thread.
    public static let helpBody = "Tap to open the steps in Chat."

    // MARK: - container incidents (`ContainerIncidents.kt:360-384`)

    /// `incidentTitle` (`:374-376`).
    public static let incidentTamperTitle = "Signs of tampering"
    public static let incidentOutdatedTitle = "A container needs recreating"
    public static let incidentFailedTitle = "A container failed"

    /// `INCIDENT_PUBLIC_TITLE` (`:360`). On Android this is the lock screen's
    /// version; here it is what a **shared screen** says, and it is the whole
    /// notification — no body (`:383`).
    public static let incidentSharedTitle = "Domovoi needs an admin's attention"

    /// `kindWords` (`:315-328`) — a `kind` in a few words, for the body of an
    /// incident whose summary the server did not compute. A kind this does not
    /// know reads with its underscores as spaces, which is the Kotlin's `else`.
    public static func incidentKindWords(_ kind: String?) -> String {
        guard let kind else { return "problem" }
        switch kind {
        case "missing": return "missing"
        case "stopped": return "stopped"
        case "restarting": return "restarting"
        case "unhealthy": return "unhealthy"
        case "oneshot_failed": return "failed"
        case "daemon": return "docker down"
        case "image": return "different image"
        case "unknown": return "unknown container"
        case "config": return "risky settings"
        case "outdated": return "older image"
        default: return kind.replacingOccurrences(of: "_", with: " ")
        }
    }

    /// `incidentContent` (`:384`) — the separator between a container's label
    /// and its kind, when the server sent no summary.
    public static let incidentBodySeparator = " · "

    // MARK: - Home's timers card (`HomeSections.kt:366`, `:400`)

    /// `HomeSections.kt:400` — the row, when notifications are off for this
    /// app. Android's wording exactly, and true on an iPhone for the same
    /// reason: nothing rings here at all.
    public static let notificationsOff = "Timer alerts are off on this phone"

    /// `HomeSections.kt:400` — the fix link while the person has not been
    /// asked yet.
    public static let turnOn = "turn on"

    /// `HomeSections.kt:366` — and the explain card's second button.
    public static let notNow = "not now"

    // MARK: - the permission ask (**iOS-only**, IOS-ONLY-STRINGS.md)

    /// **iOS-only.** Android never explains: on 13+ the `POST_NOTIFICATIONS`
    /// prompt follows the `turn on` link directly. iOS gives one chance at the
    /// system prompt for the life of the install, so the ask is reached
    /// through one sentence that says what will ring.
    public static let askTitle = "Let Domovoi tell you when a timer goes off"

    /// **iOS-only.** What will arrive, where from, and — the question a person
    /// actually has — what will not.
    public static let askBody = """
        A timer or reminder set anywhere in your house will show on this \
        phone, even when the app is closed. Nothing else is sent to it.
        """

    /// **iOS-only.** The button opens the **system** prompt, so it says so
    /// rather than claiming to switch something on.
    public static let askConfirm = "ask iOS"

    /// The explain card's second button. The same word as the row's
    /// ``notNow`` — Android's own (`HomeSections.kt:366`) — under its own
    /// name because the two answers are not the same answer: the row's is
    /// remembered for the install, the card's only closes the card
    /// (DECISIONS.md #11). Named so that changing one never silently
    /// changes the other.
    public static let askCancel = notNow

    /// **iOS-only.** `UNAuthorizationStatus.denied` is a state Android's row
    /// cannot reach. iOS allows exactly one ask, so a second `turn on` would
    /// do nothing; this is literally what the control does
    /// (`UIApplication.openSettingsURLString`).
    public static let openSettings = "open Settings"
}
