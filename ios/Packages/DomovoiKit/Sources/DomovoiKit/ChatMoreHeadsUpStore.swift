import Foundation
import Synchronization

/// This phone's heads-up switch and the kinds it has silenced.
///
/// `HeadsUpPrefs` (`assistant/docs/HeadsUpStore.kt`), as a send and a pick
/// carry it (`headsUpBody`, `ChatDocsApi.kt:105`).
public struct ChatMoreHeadsUpPrefs: Equatable, Sendable, Codable {
    /// Heads-ups off altogether.
    public var off: Bool
    /// The kinds silenced on this phone (`rewrite`, `scanned`, …).
    public var skip: Set<String>

    public init(off: Bool = false, skip: Set<String> = []) {
        self.off = off
        self.skip = skip
    }

    enum CodingKeys: String, CodingKey { case off, skip }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        off = try values.decodeIfPresent(Bool.self, forKey: .off) ?? false
        skip = try values.decodeIfPresent(Set<String>.self, forKey: .skip) ?? []
    }
}

/// Where that switch rests between launches.
///
/// **There is no route that stores it.** `HeadsUpBody`
/// (`chat_documents.py:567`) rides on a send or a pick and the server keeps
/// nothing, so this is the device's own preference — the one piece of the
/// heads-up feature that is not the household's, exactly as a chat pin is
/// (``ChatPinBook``).
///
/// It goes through ``SavedState`` for the reason ``ChatPinBook`` gives at
/// length: a model reaching `UserDefaults.standard` would be a third place
/// that knows where saved state lives, and the first one a test could not
/// drive.
///
/// `@unchecked Sendable` with the reason the house rule asks for: the one
/// stored mutable value is behind `Mutex`, no method here is `async` so
/// nothing is held across a suspension, and ``SavedState`` is itself
/// `Sendable`.
public final class ChatMoreHeadsUpStore: @unchecked Sendable {

    private let state: Mutex<ChatMoreHeadsUpPrefs>
    private let place: any SavedState

    public init(place: any SavedState = UserDefaultsSavedState(key: UserDefaultsSavedState.chatHeadsUpKey)) {
        self.place = place
        var loaded = ChatMoreHeadsUpPrefs()
        if let blob = place.read(),
           let saved = try? JSONDecoder().decode(ChatMoreHeadsUpPrefs.self, from: blob) {
            loaded = saved
        }
        // A blob this build cannot read starts blank, as the pins do: losing a
        // silenced kind is one more heads-up card, and refusing to launch is a
        // phone nobody can clear.
        state = Mutex(loaded)
    }

    public var prefs: ChatMoreHeadsUpPrefs { state.withLock { $0 } }

    /// Whether this phone has silenced `kind` — which is what the card draws
    /// in place of its "never again" button (`ChatDocsUi.kt:414`).
    public func silenced(_ kind: String) -> Bool {
        state.withLock { $0.off || $0.skip.contains(kind) }
    }

    /// "don't warn me about this again": the kind goes into `skip`, and the
    /// **next** pick carries it (`ChatDocsApi.kt:105`).
    @discardableResult
    public func silence(_ kind: String) -> ChatMoreHeadsUpPrefs {
        let next = state.withLock { prefs -> ChatMoreHeadsUpPrefs in
            if !kind.isEmpty { prefs.skip.insert(kind) }
            return prefs
        }
        save(next)
        return next
    }

    /// Heads-ups off, or back on, altogether (Settings → Chat on Android;
    /// nothing in this app draws it yet, and the field is sent either way).
    @discardableResult
    public func setOff(_ off: Bool) -> ChatMoreHeadsUpPrefs {
        let next = state.withLock { prefs -> ChatMoreHeadsUpPrefs in
            prefs.off = off
            return prefs
        }
        save(next)
        return next
    }

    private func save(_ prefs: ChatMoreHeadsUpPrefs) {
        guard let blob = try? JSONEncoder().encode(prefs) else { return }
        place.write(blob)
    }
}

/// The version of each draft this phone has already looked at, which is what
/// decides whether the canvas opens with its changes on (`DraftSeen`,
/// `DraftUi.kt:168`).
///
/// Keyed as Android keys it — `server:<address>#<id>` (`ServerDrafts.kt:47`) —
/// so two houses with a draft 7 apiece never share a mark.
public final class ChatMoreDraftSeenStore: @unchecked Sendable {

    private struct Saved: Codable, Sendable {
        var seen: [String: Int] = [:]

        enum CodingKeys: String, CodingKey { case seen }

        init(seen: [String: Int] = [:]) { self.seen = seen }

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            seen = try values.decodeIfPresent([String: Int].self, forKey: .seen) ?? [:]
        }
    }

    private let state: Mutex<[String: Int]>
    private let place: any SavedState

    public init(place: any SavedState = UserDefaultsSavedState(key: UserDefaultsSavedState.chatDraftSeenKey)) {
        self.place = place
        var loaded: [String: Int] = [:]
        if let blob = place.read(), let saved = try? JSONDecoder().decode(Saved.self, from: blob) {
            loaded = saved.seen
        }
        state = Mutex(loaded)
    }

    /// The draft's key, as Android spells it (`ServerDrafts.kt:47`).
    public static func key(server: String, draft: Int64) -> String {
        "server:\(server)#\(draft)"
    }

    public func seen(_ key: String) -> Int { state.withLock { $0[key] ?? 0 } }

    /// Mark a version looked at. Never goes backwards, as `DraftSeen.mark`
    /// does not (`DraftUi.kt:171`).
    public func mark(_ key: String, _ version: Int) {
        let next = state.withLock { seen -> [String: Int]? in
            guard version > (seen[key] ?? 0) else { return nil }
            seen[key] = version
            return seen
        }
        guard let next, let blob = try? JSONEncoder().encode(Saved(seen: next)) else { return }
        place.write(blob)
    }
}

extension UserDefaultsSavedState {

    /// This phone's heads-up switch (``ChatMoreHeadsUpStore``).
    ///
    /// A key of its own under the same prefix, for the reason
    /// ``UserDefaultsSavedState/chatPinsKey`` gives: silencing a heads-up kind
    /// has nothing to do with the server, the trust or the pins, and one write
    /// that carried all four is how a half-applied write comes to trust a
    /// server there is no row for.
    public static let chatHeadsUpKey = "domovoi.chat.headsup"

    /// Which draft version this phone has seen (``ChatMoreDraftSeenStore``).
    public static let chatDraftSeenKey = "domovoi.chat.draftseen"
}
