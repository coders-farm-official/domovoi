import Foundation
import Observation

/// Settings → **About & updates**: the app version line, and where
/// "Ask Domovoi for help" goes.
///
/// Ports `data/DeveloperOptions.kt:120` and `ui/screens/settings/AskForHelp.kt`
/// with `ui/screens/chat/ChatPlace.kt:174-195`, read on 2026-11-02.
///
/// ### What is not, and why there is no control for it
///
///  - **Developer options and the seven-tap gesture** (`DeveloperOptions.kt:96-116`,
///    `DeveloperOptionsSection.kt:60-130`). Android's own rule is that they
///    do not exist where there is nothing under them — `available == false`
///    makes the version line plain text and no tap turns anything on
///    (`DeveloperOptions.kt:21-26`) — and the single tool Android puts under
///    them is the on-device assistant benchmark, which has no iOS
///    counterpart (`Route.swift:169-173`). So: a plain line, no gesture, no
///    section, no switch. ``systemSettingsAppVersionText(version:debugBuild:)``
///    is still ported, because the line is still drawn.
///  - **App updates** (`update/AppUpdates.kt:19-22` → `/api/devices/android-app`,
///    `web/backend/api/devices.py:404`). An App Store app does not fetch its
///    own build and that route serves an APK. No card, no "check for
///    updates", and **no App Store link** either — the app is not published,
///    so the link would open a page that does not exist.
///  - **A phone-chat branch for help.** Android's third place is a chat
///    answered by the manual on the phone (`AskForHelp.kt:82-85`); there is
///    no on-device engine on iOS, which ``RouteVisibility/phoneCanChat``
///    documents as permanently false today, so that branch is folded out
///    rather than drawn as a button that cannot start anything.

// MARK: - the version line (data/DeveloperOptions.kt:120)

/// `app version 1.0.0`, or `app version 1.0.0 (debug build)`.
///
/// A blank or missing version reads as ``SystemSettingsStrings/versionUnknown``,
/// which is Android's `?: "unknown"` and is also the shape a `Bundle` key
/// mistake takes — so a UI case asserting the line is not "unknown" is
/// asserting the read works.
public func systemSettingsAppVersionText(version: String?, debugBuild: Bool) -> String {
    let shown: String
    if let version, !version.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        shown = version
    } else {
        shown = SystemSettingsStrings.versionUnknown
    }
    return "app version \(shown)" + (debugBuild ? " (debug build)" : "")
}

// MARK: - where help goes (ChatPlace.kt:174-195)

/// Where "Ask Domovoi for help" goes.
///
/// Android's `HelpEntry` has three cases (`ChatPlace.kt:179`); its middle one
/// is a chat on the phone and this app has no engine to answer one, so there
/// are two here. The enum is not a transcription for that reason, and the
/// *rule* below is.
public enum SystemSettingsHelpEntry: Equatable, Sendable {
    /// A thread on the home server, titled ``systemSettingsHelpTitle``, which
    /// the server answers from Domovoi's help only.
    case serverChat
    /// The manual this app carries.
    case manual
}

/// `helpEntry(startablePlaces(…))` (`ChatPlace.kt:185-195`) with
/// `phoneEngine`/`phoneCapable` false throughout.
///
/// Unfolded, Android's two functions come to: the server's place is listed
/// when `!away && serverChatOn` (`ChatPlace.kt:169-172`), the phone's never
/// here, and `helpEntry` takes the server when it is listed, else the phone,
/// else the Manual screen. `shared` drops out as well — it gates **only** the
/// phone's place in the Kotlin — which is why it is not a parameter: a
/// shared screen with a chat-capable server starts the same help thread, as
/// on Android.
public func systemSettingsHelpEntry(connected: Bool, aiEnabled: Bool) -> SystemSettingsHelpEntry {
    connected && aiEnabled ? .serverChat : .manual
}

/// `HELP_APP_TITLE` (`ChatPlace.kt:177`), verbatim: the server answers a
/// thread titled `Help: …` from Domovoi's help only, so the prefix is load
/// bearing on the server side (`web/backend/.../help_delivery.py`, which
/// `chat.py:301` checks this exact prefix against).
public let systemSettingsHelpTitle = "Help: App"

/// The body of `POST /api/chat/threads`: `{"title":"Help: App"}`
/// (`AskForHelp.kt:73`, and `ThreadCreate` in `web/backend/api/chat.py:285`).
public func systemSettingsHelpThreadBody() -> Data {
    guard let body = try? JSONSerialization.data(
        withJSONObject: ["title": systemSettingsHelpTitle]
    ) else {
        // Unreachable: a one-key dictionary of strings always encodes.
        // Hand-rolled rather than trapping, because a help chat is not worth
        // a crash, and the literal is the same bytes.
        return Data(#"{"title":"Help: App"}"#.utf8)
    }
    return body
}

// MARK: - the help card's model

/// "Ask Domovoi for help": start the thread, or send the person to the
/// manual.
///
/// The shape ``FilesModel`` and ``ChatModel`` use — `@MainActor`,
/// `@Observable`, the request behind a `@Sendable` seam so a package test
/// drives it without a server.
///
/// ### The key is captured BEFORE the await
///
/// The top defect class on this track is a reply for server A landing under
/// server B. The thread id this `POST` answers means nothing outside the
/// household that minted it, and
/// ``AppContainer/requestChatThread(serverKey:thread:title:)`` refuses one
/// whose key is not the saved server's — a privacy rule, not tidiness: handed
/// over regardless, the id would be looked up on whichever server the phone
/// is on now and *that* house would answer with its own thread of that
/// number.
///
/// So ``askForHelp()`` reads the key once, before the round trip, and hands
/// that key to ``accept``. A server switch inside the round trip makes
/// `accept` answer false and the thread is dropped with
/// ``SystemSettingsStrings/helpServerChanged``.
///
/// **Android does not do this**: its card hands the thread straight to
/// `pendingChatThread` (`AskForHelp.kt:76-78`) with no key check, so a switch
/// mid-request opens a stranger's thread number. Reported in
/// `design-notes/ios/overnight/systemsettings/REPORT.md`.
@MainActor
@Observable
public final class SystemSettingsHelpModel {

    /// Whether the button is waiting on the server (Android's `starting`,
    /// `AskForHelp.kt:52`). The button is disabled while it is true, and a
    /// second tap is refused below as well — a disabled button is not a
    /// guarantee under VoiceOver's own activation.
    public private(set) var starting = false

    /// What to say when it did not work, from ``failureText(_:_:)`` with
    /// Android's own action word.
    public private(set) var error: String?

    /// Set when a thread was accepted: Chat should be opened, and Chat takes
    /// the pending thread on appear (`ChatView.swift:108`).
    public private(set) var shouldOpenChat = false

    private let serverKey: @MainActor () -> String?
    private let createThread: @Sendable () async throws(DomovoiError) -> ChatThread
    private let accept: @MainActor (String, ChatThread) -> Bool

    /// - Parameters:
    ///   - serverKey: ``AppContainer/serverKey(_:)`` of the saved address.
    ///     Read, not passed as a value, because the whole point is to read it
    ///     twice — once before the await and once through `accept` after it.
    ///   - createThread: `POST /api/chat/threads`, decoded as the server's
    ///     own thread row.
    ///   - accept: ``AppContainer/requestChatThread(serverKey:thread:title:)``,
    ///     which answers false when the captured key is no longer this
    ///     phone's server.
    public init(
        serverKey: @escaping @MainActor () -> String?,
        createThread: @escaping @Sendable () async throws(DomovoiError) -> ChatThread,
        accept: @escaping @MainActor (String, ChatThread) -> Bool
    ) {
        self.serverKey = serverKey
        self.createThread = createThread
        self.accept = accept
    }

    /// The button's tap, for ``SystemSettingsHelpEntry/serverChat``.
    ///
    /// The manual branch is navigation and belongs to the view: there is
    /// nothing to decide and nothing to await.
    public func askForHelp() async {
        guard !starting else { return }
        // BEFORE the await. See this type's doc comment.
        guard let key = serverKey() else {
            error = failureText(SystemSettingsStrings.helpAction, .noServer)
            return
        }
        starting = true
        error = nil
        defer { starting = false }
        do {
            let thread = try await createThread()
            guard accept(key, thread) else {
                error = SystemSettingsStrings.helpServerChanged
                return
            }
            shouldOpenChat = true
        } catch let failure {
            // Named, because an unnamed `catch` binds `error` and would
            // shadow the property this line is writing.
            error = failureText(SystemSettingsStrings.helpAction, failure)
        }
    }

    /// The view takes the navigation, which clears it, so a redraw does not
    /// push Chat twice (Android removes its intent extra for the same
    /// reason, `MainActivity.kt:140`).
    public func takeShouldOpenChat() -> Bool {
        let open = shouldOpenChat
        shouldOpenChat = false
        return open
    }
}
