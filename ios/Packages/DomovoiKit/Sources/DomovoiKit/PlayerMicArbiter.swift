import Foundation
import Synchronization

/// Who holds the phone's microphone: ONE owner at a time, for the four things
/// in the app that open it.
///
/// `android/.../audio/MicArbiter.kt:1-94`, ported with one addition iOS forces
/// (below). Android lets several recorders open the mic and then silences all
/// but one without telling the others: a room would go deaf while a drop-in
/// ran and say nothing about it. So every user asks here first, and a refusal
/// is a sentence the screen can show.
///
/// Room mode is exclusive for as long as it is **on** — muted, between turns or
/// waiting for the network included: it is the phone's whole job then. The
/// others take the mic one at a time while room mode is off, and room mode does
/// not start while one of them holds it.
///
/// ## The addition: taking the mic pauses the player
///
/// An owner here does not merely record, it needs `AVAudioSession` in
/// `.playAndRecord`, which ducks or stops playback. So this is also where "the
/// player gives the session back" lives: ``PlayerModel`` plugs itself in
/// through ``PlayerMicArbiter/whenTaken(_:)`` and pauses on acquire, and the
/// lease's release puts the session back to `.playback`. Room mode and a
/// drop-in call are not things to talk over, so pausing is the honest
/// behaviour rather than a mysteriously quiet player.
///
/// ## What is not, and why there is no control for it
///
/// **No `deinit`-based release.** A lease that frees itself when nobody holds
/// it is a lease that frees itself at the collector's convenience, which on a
/// microphone means the mic coming free a second or a minute after the call
/// ended — or not at all under a retain cycle. ``PlayerMicLease/release()`` is
/// explicit and idempotent, as Android's `close()` is.
public enum PlayerMicOwner: String, Equatable, Sendable, CaseIterable {
    case roomMode
    case dropIn
    case chatMic
    case assistant

    /// `MicArbiter.kt:30-33`, word for word — these appear inside a refusal.
    public var label: String {
        switch self {
        case .roomMode: return "room mode"
        case .dropIn: return "a drop-in call"
        case .chatMic: return "the chat mic"
        case .assistant: return "a phone chat"
        }
    }
}

/// A held microphone; ``release()`` gives it back, and is idempotent.
public final class PlayerMicLease: Sendable {
    public let owner: PlayerMicOwner
    private let arbiter: PlayerMicOwnership
    private let token: Int64

    init(owner: PlayerMicOwner, arbiter: PlayerMicOwnership, token: Int64) {
        self.owner = owner
        self.arbiter = arbiter
        self.token = token
    }

    /// Hand the microphone back. Calling it twice does nothing the second
    /// time, and a lease that has already been superseded does not steal the
    /// release from whoever holds the mic now — the token is what makes that
    /// true rather than identity, which is why it exists.
    public func release() {
        arbiter.release(token: token)
    }
}

/// `MicArbiter.kt:25-87` (`MicOwnership`). Process-wide through
/// ``PlayerMicArbiter``.
public final class PlayerMicOwnership: Sendable {

    private struct State {
        var owner: PlayerMicOwner?
        var token: Int64 = 0
        var next: Int64 = 1
        var roomMode = false
    }

    private let state = Mutex(State())

    /// What to do when the mic is taken and when it comes back — the player's
    /// pause and the audio session's category. Set once, by whoever owns the
    /// player; nil until then, and then the arbiter is Android's exactly.
    private struct Hooks: Sendable {
        var taken: (@Sendable (PlayerMicOwner) -> Void)?
        var freed: (@Sendable () -> Void)?
    }

    private let hooks = Mutex(Hooks())

    public init() {}

    /// Called with the new owner whenever the mic is taken, and with nothing
    /// when it is given back. ``PlayerModel`` installs the pause here.
    public func whenTaken(
        _ taken: @escaping @Sendable (PlayerMicOwner) -> Void,
        whenFreed freed: @escaping @Sendable () -> Void
    ) {
        hooks.withLock { $0 = Hooks(taken: taken, freed: freed) }
    }

    /// True while room mode holds the mic (the other users are off).
    public var roomModeActive: Bool { state.withLock { $0.roomMode } }

    /// The current holder, if any.
    public func holder() -> PlayerMicOwner? { state.withLock { $0.owner } }

    /// The mic for `owner`, or nil with ``refusal(_:)`` saying why.
    public func tryAcquire(_ owner: PlayerMicOwner) -> PlayerMicLease? {
        let token: Int64? = state.withLock {
            guard $0.owner == nil else { return nil }
            $0.owner = owner
            $0.token = $0.next
            $0.next += 1
            if owner == .roomMode { $0.roomMode = true }
            return $0.token
        }
        guard let token else { return nil }
        // Outside the lock: the hook pauses a player on the main actor, and a
        // hook that ran under this mutex would be a lock held across whatever
        // it chooses to do.
        hooks.withLock { $0.taken }?(owner)
        return PlayerMicLease(owner: owner, arbiter: self, token: token)
    }

    func release(token: Int64) {
        let freed: Bool = state.withLock {
            guard $0.owner != nil, $0.token == token else { return false }
            $0.owner = nil
            $0.roomMode = false
            return true
        }
        guard freed else { return }
        hooks.withLock { $0.freed }?()
    }

    /// Words for a refused `owner`; nil when nothing holds the mic
    /// (`MicArbiter.kt:64`).
    public func refusal(_ owner: PlayerMicOwner) -> String? {
        guard let current = holder() else { return nil }
        if current == .roomMode { return PlayerStrings.micHeldByRoomMode }
        if current == owner { return PlayerStrings.micAlreadyYours(owner.label) }
        return PlayerStrings.micHeldBy(current.label)
    }

    /// ``tryAcquire(_:)`` or a throw carrying ``refusal(_:)``'s words, for the
    /// capture paths that already report mic failures that way
    /// (`MicArbiter.kt:83`).
    public func acquireOrThrow(_ owner: PlayerMicOwner) throws -> PlayerMicLease {
        if let lease = tryAcquire(owner) { return lease }
        throw PlayerMicRefused(note: refusal(owner) ?? PlayerStrings.micInUse)
    }

    /// Forget the holder and the hooks (tests only).
    func reset() {
        state.withLock { $0 = State() }
        hooks.withLock { $0 = Hooks() }
    }
}

/// `MicArbiter.kt:83`'s `IOException`, typed.
public struct PlayerMicRefused: Error, Equatable, Sendable {
    public let note: String

    public init(note: String) { self.note = note }
}

/// The app's one ``PlayerMicOwnership`` (`MicArbiter.kt:90`).
public enum PlayerMicArbiter {
    public static let shared = PlayerMicOwnership()

    public static func tryAcquire(_ owner: PlayerMicOwner) -> PlayerMicLease? {
        shared.tryAcquire(owner)
    }

    public static func acquireOrThrow(_ owner: PlayerMicOwner) throws -> PlayerMicLease {
        try shared.acquireOrThrow(owner)
    }

    public static func refusal(_ owner: PlayerMicOwner) -> String? { shared.refusal(owner) }

    public static var roomModeActive: Bool { shared.roomModeActive }
}
