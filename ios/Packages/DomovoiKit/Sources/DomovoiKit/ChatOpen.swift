import Foundation

/// Where a chat runs. Two places, which never blend (owner, 2026-10-21): the
/// model on this phone, and the home server's chat, which is the household's.
///
/// A port of `ChatPlaceKind` in
/// `android/.../ui/screens/chat/ChatPlace.kt:22-31`. Only the kind is here:
/// Chat's own words — the chips, the away lines, the composer hint — belong
/// with the Chat screen, which is a later batch's.
public enum ChatPlace: String, CaseIterable, Sendable {
    /// The model on this phone. Private to it, works with no server and no
    /// internet. **No iPhone has this engine yet** (the assistant is a later
    /// milestone), so nothing produces this case today.
    case phone
    /// The home server's `/api/chat`. Every paired device sees the same
    /// threads.
    case server

    /// The key a store keeps, and what a payload carries.
    public var key: String { rawValue }

    /// The kind a key names; nil for none, or one this build does not know
    /// (Kotlin's `ChatPlaceKind.of`).
    public static func of(_ key: String?) -> ChatPlace? {
        guard let key else { return nil }
        return ChatPlace(rawValue: key)
    }
}

/// A chat to open from outside Chat — "Ask about this document" in Files.
///
/// `ChatOpenRequest` (`ChatPlace.kt:34`), with one difference: Android's
/// `place` is a bare `String` that happens to hold a `ChatPlaceKind.key`, and
/// this is the kind itself. Two callers construct it on Android
/// (`FilesScreen.kt:325` and `:333`) and both pass a literal, so nothing is
/// lost by making the type say what the string had to mean.
public struct ChatOpenRequest: Equatable, Sendable {
    public let place: ChatPlace
    /// That place's own chat id.
    public let id: String

    public init(place: ChatPlace, id: String) {
        self.place = place
        self.id = id
    }
}

/// A home-server chat thread a help notification asked for: the thread, and
/// the title to show until the thread itself loads.
///
/// Android carries it as `Pair<Long, String>`
/// (`AppContainer.kt:155`, `pendingChatThread`). Named here because the rule
/// that guards it — ``AppContainer/requestChatThread(serverKey:thread:title:)``
/// — is the reason this payload exists at all, and a tuple would not carry a
/// doc comment.
public struct PendingChatThread: Equatable, Sendable {
    public let thread: Int64
    public let title: String

    public init(thread: Int64, title: String) {
        self.thread = thread
        self.title = title
    }

    /// What a help notification calls a thread it has no title for
    /// (`MainActivity.kt:138`, `?: "Help"`).
    public static let defaultTitle = "Help"
}
