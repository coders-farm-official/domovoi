import Foundation
import Synchronization

// ===========================================================================
// Private chats (1.5.0, design-notes PRIVATE-CHATS.md §2.5–§2.7;
// docs/SECURITY_PRIVACY.md "Private chats"). `ChatPrivacy.kt`, rule for rule.
// ===========================================================================
//
// A home-server chat is private to the phone or browser that started it
// unless it is shared with the household. The server decides who sees what,
// by this phone's device key (``DeviceKeyStore``), and says so in every
// thread's access fields; the app shows what those say and hides what they
// deny. The words are §2.5's, as written (the dashboard's `chat.jsx` uses the
// same ones).

public enum ChatPrivacyStrings {
    /// A new chat's chip and header.
    public static let privateLine = "Private · only this device and the house's admins can see it"
    /// The switch, and its helper line.
    public static let shareSwitch = "Share with household"
    public static let shareHelp = "Everyone's phones and browsers in the house can read and add to it."
    /// Turning sharing off asks first.
    public static let unshareAsk =
        "Stop sharing? Other phones and browsers won't see this chat any more, including anything they wrote in it."
    public static let unshareTitle = "stop sharing"
    public static let unshareConfirm = "stop sharing"
    /// A shared chat's chip.
    public static let sharedLine = "Shared with the household"
    /// On a shared screen: no switch, and this chip.
    public static let sharedScreenLine = "Shared · chats on this screen are shared with the household"
    /// The admin's list filter.
    public static let scopeMine = "My chats"
    public static let scopeAll = "All chats in the house"
    /// Phones only, once: the fully private place, where this build has one.
    public static let trulyPrivateNote = "Want it truly private? Chat with Domovoi on this phone: it never leaves the phone."
    /// Save to Documents from a private chat asks first.
    public static let savePrivateAsk = "Saves a copy to Documents, where the household can see it."
    public static let savePrivateTitle = "save to Documents"
    public static let savePrivateConfirm = "save"
    /// An open chat the server stopped showing this phone: said, and closed.
    public static let goneLine = "This chat isn't shared with this device any more."
    /// The switch's toasts (the dashboard's).
    public static let sharedToast = "shared with the household"
    public static let unsharedToast = "private to this device again"

    /// An admin reading another device's private chat.
    public static func adminBanner(_ deviceName: String?) -> String {
        let name = deviceName?.trimmingCharacters(in: .whitespacesAndNewlines)
        let who = (name?.isEmpty == false ? name : nil) ?? "another device"
        return "You're reading \(who)'s private chat as an admin. You can share it or delete it, not write in it."
    }

    /// Delete: a private chat says only that it can't be undone; a shared one
    /// (and an older server's, which is the household's) says it goes for
    /// everyone; another device's shared chat has no delete at all.
    public static func deleteText(_ title: String, shared: Bool?) -> String {
        shared == false
            ? "Delete \"\(title)\"? This can't be undone."
            : ChatStrings.deleteText(title)
    }
}

/// The 409s a create (or an unshare) answers with a `code`.
public enum ChatRefusal: Equatable, Sendable {
    case sharedScreen
    case keyMissing

    public static func of(_ error: DomovoiError) -> ChatRefusal? {
        guard let failure = error.failure, failure.status == 409 else { return nil }
        switch ChatPrivacyRules.code(of: failure.body) {
        case "shared_screen": return .sharedScreen
        case "device_key_missing": return .keyMissing
        default: return nil
        }
    }
}

public enum ChatPrivacyRules {

    /// The switch shows only to the owner or an admin, never on a shared screen.
    public static func showsShareSwitch(_ thread: ChatThread, sharedScreen: Bool) -> Bool {
        !sharedScreen && thread.canShare == true
    }

    /// The chip for an open chat whose server sent the access fields.
    public static func line(shared: Bool, sharedScreen: Bool) -> String {
        if sharedScreen { return ChatPrivacyStrings.sharedScreenLine }
        return shared ? ChatPrivacyStrings.sharedLine : ChatPrivacyStrings.privateLine
    }

    /// A new chat's chip before it exists on the server: private by default
    /// when this phone holds a key (`shareNew` when the switch was turned on
    /// first), always shared on a shared screen, and nil without a key (an
    /// older server, or no key yet: the household's, as before).
    public static func newChatLine(hasKey: Bool, sharedScreen: Bool, shareNew: Bool) -> String? {
        if sharedScreen { return ChatPrivacyStrings.sharedScreenLine }
        guard hasKey else { return nil }
        return shareNew ? ChatPrivacyStrings.sharedLine : ChatPrivacyStrings.privateLine
    }

    /// `POST /api/chat/threads`'s body. With a key, `shared` says what the
    /// chip says, so a private chat the server can't keep private fails
    /// loudly (409) instead of being made shared by surprise; on a shared
    /// screen it is shared; without a key the server's default stands.
    public static func createBody(hasKey: Bool, sharedScreen: Bool, shareNew: Bool) -> Data {
        if sharedScreen { return Data(#"{"shared":true}"#.utf8) }
        if hasKey { return Data((shareNew ? #"{"shared":true}"# : #"{"shared":false}"#).utf8) }
        return Data("{}".utf8)
    }

    /// `{"shared": …}` for `PATCH /threads/{id}`.
    public static func shareBody(_ shared: Bool) -> Data {
        Data((shared ? #"{"shared":true}"# : #"{"shared":false}"#).utf8)
    }

    /// The thread is not there for this phone: a 404 whose body is the
    /// server's "thread not found" (byte-identical for a missing chat and
    /// one this phone can't see). A child route's own 404 is not this.
    public static func isGone(status: Int, body: String?) -> Bool {
        status == 404 && jsonDetail(of: body) == "thread not found"
    }

    public static func isGone(_ error: DomovoiError) -> Bool {
        guard let failure = error.failure else { return false }
        return isGone(status: failure.status, body: failure.body)
    }

    static func code(of body: String?) -> String? {
        guard let body, let data = body.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return object["code"] as? String
    }

    /// The thread list's path: the chats or the archived ones, and an admin's
    /// "All chats in the house" (`scope=all`).
    public static func threadsPath(archived: Bool, all: Bool) -> String {
        var query: [String] = []
        if archived { query.append("archived=true") }
        if all { query.append("scope=all") }
        return "\(ChatPath.base)/threads" + (query.isEmpty ? "" : "?" + query.joined(separator: "&"))
    }

    /// Whether to offer the admin's "My chats" / "All chats in the house".
    /// The phone can't sign in as an admin on this branch (phone admin lives
    /// on its own branch), so the only evidence is the server's: a thread it
    /// marks `admin_view`, or an `owner_name` (sent to admins only). The
    /// admin sign-in work replaces this guess with what it knows.
    public static func offersScope(_ threads: [ChatThread], showingAll: Bool) -> Bool {
        showingAll || threads.contains { $0.adminView || !($0.ownerName ?? "").isEmpty }
    }
}

/// Whether the first-use note ("Want it truly private?…") was shown on this
/// phone. Once, ever. `UserDefaults` in the app; a box in a test.
public final class ChatPrivateNoteBook: Sendable {
    public static let key = "domovoi.chat.privateNoteSeen"

    private let place: any SavedState
    private let seenNow: Mutex<Bool>

    public init(place: any SavedState = SavedStateBox()) {
        self.place = place
        self.seenNow = Mutex(place.read() != nil)
    }

    public static func forApp() -> ChatPrivateNoteBook {
        ChatPrivateNoteBook(place: UserDefaultsSavedState(key: key))
    }

    public var seen: Bool { seenNow.withLock { $0 } }

    public func markSeen() {
        let first = seenNow.withLock { s -> Bool in
            defer { s = true }
            return !s
        }
        if first { place.write(Data("1".utf8)) }
    }
}

/// A thread list's scope, readable from the `@Sendable` fetch closures.
final class ChatScopeBox: Sendable {
    private let all = Mutex(false)
    var showingAll: Bool {
        get { all.withLock { $0 } }
        set { all.withLock { $0 = newValue } }
    }
}
