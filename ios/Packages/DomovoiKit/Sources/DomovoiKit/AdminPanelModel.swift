import Foundation
import Observation

/// Settings → Household → Admin on this phone (1.5.0): what it says.
///
/// The port of `AdminPanel.kt`'s strings (bd5c4b5e). Where Android says
/// "fingerprint or face", these say Face ID or Touch ID, which is what an
/// iPhone has; everything else is the Kotlin's words. The section's own label
/// and sub are ``SettingsSection``'s, held to the Kotlin by
/// `ShellStringsTests`.
public enum AdminStrings {

    public static let title = "Admin on this phone"

    /// `ADMIN_PANEL_SUB`.
    public static let sub =
        "Sign in with the admin password to do admin jobs from this phone. The password isn't kept: "
        + "the sign-in lasts an hour with no admin change, or the rest of the day behind Face ID or Touch ID."

    /// `ADMIN_JOBS`: the jobs that work from the phone so far (design §1's
    /// list, one area at a time); everything else stays on the dashboard.
    public static let jobs =
        "As admin on this phone you can approve or turn away a room device asking to join, and restart, upgrade "
        + "or change the settings of a room (Settings → Rooms), "
        + "answer friend requests, remove, block or unblock friend houses, and save a copy of or delete a conversation "
        + "(Friend Houses → friends & connections), "
        + "acknowledge or clear Health incidents (Settings → Health), "
        + "and install an update or restart the Domovoi server (Settings → About & updates). "
        + "Everything else is on the dashboard for now."

    /// `ADMIN_SHARED`.
    public static let shared =
        "This is a shared screen, so it never holds admin. Sign in on your own phone."

    /// `ADMIN_NO_BIOMETRICS`, the iPhone's words.
    public static let noBiometrics =
        "Keeping admin for the day needs Face ID or Touch ID set up on this phone. "
        + "Without it, the sign-in lasts an hour."

    public static let connectFirst = "Connect this phone to your Domovoi first."
    public static let passwordLabel = "admin password"
    public static let keepToggle = "Keep admin on this phone today (Face ID)"
    public static let signIn = "sign in"
    public static let signingIn = "signing in…"
    public static let keepToday = "keep admin today"
    public static let goOn = "go on as admin"
    public static let signOut = "sign out"

    /// The top bar's badge, and what VoiceOver says for it (Android's
    /// `onClickLabel`).
    public static let badge = "admin"
    public static let badgeAccessibility = "admin on this phone"

    /// Signed in, but the day could not be kept.
    public static func notKeptForTheDay(_ reason: String) -> String {
        "Signed in for the hour; not kept for the day: \(reason)"
    }

    /// `adminStateLine`: the line under the title, what admin this phone
    /// holds now. The clock is the app's (``homeClock(ms:timeZone:)``,
    /// `7:00pm`).
    public static func stateLine(_ state: AdminSession.State, timeZone: TimeZone = .current) -> String {
        func clock(_ ms: Int64) -> String { homeClock(ms: ms, timeZone: timeZone) }
        switch state {
        case .signedOut:
            return "Not signed in as admin."
        case .signedIn(_, _, let kept?):
            return "Signed in as admin, kept on this phone until \(clock(kept))."
        case .signedIn:
            return "Signed in as admin. It ends after an hour with no admin change."
        case .kept(_, let until):
            return "Admin is kept on this phone until \(clock(until)). Face ID goes on when you need it."
        }
    }

    /// `adminLabel`: what the dashboard's session list shows for this phone.
    public static func label(model: String) -> String {
        String("phone: \(model.isEmpty ? "iPhone" : model)".prefix(AdminSession.labelMax))
    }
}

/// The badge in the top bar (design §1): filled while signed in, muted while
/// kept today, absent when signed out or on a shared screen.
public enum AdminBadge: Equatable, Sendable {
    case filled
    case muted

    public static func of(_ state: AdminSession.State, sharedScreen: Bool) -> AdminBadge? {
        if sharedScreen { return nil }
        switch state {
        case .signedOut: return nil
        case .signedIn: return .filled
        case .kept: return .muted
        }
    }
}

/// What the screens read and do about admin on this phone: the session's
/// state, observable, and the panel's four actions with the words they end
/// in. One for the shell (``ShellModel/admin``), so the badge and the panel
/// read the same value.
///
/// It is the session's one change sink (``AdminSession/onChange(_:)``). A
/// change may arrive on any thread; it hops to the main actor and re-reads
/// the session there, so a late hop can never write an older state over a
/// newer one.
@MainActor
@Observable
public final class AdminModel {

    public let session: AdminSession

    public private(set) var state: AdminSession.State

    /// An action is running: the panel's buttons wait.
    public private(set) var busy = false

    /// What the last action said, when it said anything (a refusal).
    public private(set) var message: String?

    public init(session: AdminSession) {
        self.session = session
        state = session.state
        session.onChange { [weak self] _ in
            Task { @MainActor in self?.reread() }
        }
    }

    public var canKeepToday: Bool { session.canKeepToday }

    public func badge(sharedScreen: Bool) -> AdminBadge? { AdminBadge.of(state, sharedScreen: sharedScreen) }

    /// The server may have changed: the state is recomputed for it.
    public func refresh() {
        session.refresh()
        reread()
    }

    private func reread() { state = session.state }

    /// Sign in with `password`, and keep admin for the day when `keep` and
    /// the phone can. A day that could not be kept still leaves the hour.
    public func signIn(password: String, keep: Bool, label: String) async {
        let session = session
        let canKeep = canKeepToday
        await perform {
            switch await session.signIn(password: password, label: label) {
            case .refused(let why):
                return why
            case .ok:
                guard keep, canKeep else { return nil }
                if case .refused(let why) = await session.keepToday(label: label) {
                    return AdminStrings.notKeptForTheDay(why)
                }
                return nil
            }
        }
    }

    public func keepToday(label: String) async {
        let session = session
        await perform {
            if case .refused(let why) = await session.keepToday(label: label) { return why }
            return nil
        }
    }

    /// "go on as admin": Face ID opens today's grant.
    public func resume() async {
        let session = session
        await perform {
            if case .refused(let why) = await session.resume() { return why }
            return nil
        }
    }

    public func signOut() async {
        let session = session
        await perform {
            await session.signOut()
            return nil
        }
    }

    private func perform(_ work: () async -> String?) async {
        busy = true
        message = nil
        message = await work()
        busy = false
        reread()
    }
}
