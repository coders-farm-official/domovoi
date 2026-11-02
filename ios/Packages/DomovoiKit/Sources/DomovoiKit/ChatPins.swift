import Foundation
import Synchronization

/// The chats pinned on **this phone**, and where they rest between launches.
///
/// Android keeps them in `Prefs` (`chatPins`, `setChatPinned`), keyed
/// `server:<address>#<id>` so two houses never share them. The owner's
/// decision, 2026-10-20: *one person's pins do not move the household's list*
/// — so a pin is not a `PATCH`, there is no route for it, and it is the one
/// piece of Chat's state that is not the house's.
///
/// ### Why this is a type and not a `UserDefaults` call in the model
///
/// ``SavedShellStore`` is the only other thing in this module that persists
/// anything, and it goes through ``SavedState`` so that a test can hand one
/// store's bytes to a second store and watch them come back. This uses **that
/// same seam** — a second key, `UserDefaultsSavedState.chatPinsKey`, and no
/// second mechanism. A model reaching `UserDefaults.standard` directly would be
/// a third place that knows where saved state lives, and the first one a test
/// could not drive.
///
/// `@unchecked Sendable` with the reason, as the house rule asks: the one
/// stored mutable value is behind `Mutex`, nothing is held across a suspension
/// because no method here is `async`, and ``SavedState`` is itself `Sendable`.
public final class ChatPinBook: @unchecked Sendable {

    /// The saved shape: one key, so a later field is additive.
    ///
    /// Every field is optional on the way in, for the reason
    /// ``TrustBook/init(from:)`` gives at length — a blob written by a build
    /// that did not have a field must not throw and lose the pins.
    struct Saved: Codable, Sendable {
        var pins: Set<String> = []

        enum CodingKeys: String, CodingKey { case pins }

        init(pins: Set<String> = []) { self.pins = pins }

        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            pins = try values.decodeIfPresent(Set<String>.self, forKey: .pins) ?? []
        }
    }

    private let state: Mutex<Set<String>>
    private let place: any SavedState

    public init(place: any SavedState = UserDefaultsSavedState(key: UserDefaultsSavedState.chatPinsKey)) {
        self.place = place
        var loaded: Set<String> = []
        if let blob = place.read(), let saved = try? JSONDecoder().decode(Saved.self, from: blob) {
            loaded = saved.pins
        }
        // A blob this build cannot read at all starts blank. Losing a pin is a
        // chat that is not at the top of a list; crashing on launch for ever is
        // a phone whose owner cannot clear it.
        state = Mutex(loaded)
    }

    /// Every key pinned, across every house this phone knows.
    public var all: Set<String> { state.withLock { $0 } }

    // There is no `isPinned(_ key:)` here. It was the natural API and it had
    // no non-test caller: ``ChatModel/isPinned(_:)`` asks the set it already
    // holds (``all``, taken at init and returned by ``set(_:pinned:)``), which
    // is what lets `rows` be a computed property the view can read without
    // touching a lock. A seam with tests and no reader is a bug on this
    // project, so the honest resolution was to delete one of the two — and the
    // one with a reader stayed.

    /// Pin or unpin `key`, and write the whole set out.
    ///
    /// Returns the set as it now is, so a caller does not have to read it back
    /// — the read-after-write that [[read-after-await-wrong-key]] is the
    /// awaiting cousin of.
    @discardableResult
    public func set(_ key: String, pinned: Bool) -> Set<String> {
        let next = state.withLock { pins -> Set<String> in
            if pinned { pins.insert(key) } else { pins.remove(key) }
            return pins
        }
        save(next)
        return next
    }

    private func save(_ pins: Set<String>) {
        guard let blob = try? JSONEncoder().encode(Saved(pins: pins)) else { return }
        place.write(blob)
    }
}
