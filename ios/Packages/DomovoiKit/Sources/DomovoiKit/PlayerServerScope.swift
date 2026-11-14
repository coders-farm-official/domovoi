import Foundation

/// Which server the phone's queue came from, and what a move to another one
/// leaves of it.
///
/// `android/.../player/ServerScope.kt:1-78`. Found on the owner's phone,
/// 2026-10-12: he played music from one Domovoi, connected the app to a fresh
/// one at another address, and the mini player still showed the first one's
/// song.
///
/// A library track, a station, an episode or a book is a **server path**
/// (`/api/music/library/12/audio`): id 12 on another server is another song,
/// or nothing. So what the phone queued from a server belongs to that server —
/// by its pinned **identity**, not its address, because the same house at a
/// new address is still the same house — and a move to a different server
/// clears it. The phone's own songs (``PlayerKind/device``) and books saved on
/// this phone (``PlayerItem/savedKey``) belong to no server and stay: their
/// files are here.
public struct PlayerServerScope: Equatable, Sendable {
    public let address: String?
    public let fingerprint: String?

    public init(address: String? = nil, fingerprint: String? = nil) {
        self.address = address
        self.fingerprint = fingerprint
    }

    /// The app is connected to no server (it was forgotten).
    public static let none = PlayerServerScope()

    /// Same server: the same identity when **both** are known, whatever the
    /// address; else the same address. No server is never the same
    /// (`ServerScope.kt:28`).
    public func isSameServer(as other: PlayerServerScope) -> Bool {
        if let fingerprint, let theirs = other.fingerprint { return fingerprint == theirs }
        guard let address else { return false }
        return address == other.address
    }

    /// This server, as `other` — the same server — now knows it: a new
    /// address, an identity pinned since (`ServerScope.kt:34`).
    public func merge(_ other: PlayerServerScope) -> PlayerServerScope {
        PlayerServerScope(
            address: other.address ?? address,
            fingerprint: other.fingerprint ?? fingerprint
        )
    }

    /// The saved server `serverURL` with the identity pinned for it
    /// (`ServerScope.kt:44`). The address is ``IdentityGate/pinKey(_:)-7l1jg``,
    /// the key the pins are themselves keyed by, so two spellings of one
    /// address are one server here too.
    public static func of(serverURL: String?, pin: ServerIdentity.Pin?) -> PlayerServerScope {
        let address = IdentityGate.pinKey(serverURL?.isEmpty == true ? nil : serverURL)
        let fingerprint = pin?.fingerprint
        return PlayerServerScope(
            address: address,
            fingerprint: (fingerprint?.isEmpty ?? true) ? nil : fingerprint
        )
    }
}

/// What a move to a different server leaves of the queue: the phone's own
/// songs, in order, and where the play head goes among them
/// (`ServerScope.kt:52-65`).
public struct PlayerServerSwitchPlan: Equatable, Sendable {
    public let kept: [PlayerItem]
    public let keptIndex: Int
    /// The old server's entries, by queue position, **highest first** — the
    /// order they can be taken out of a player in without the later indices
    /// shifting underneath.
    public let removed: [Int]
    /// The entry playing now was the old server's: playback stops.
    public let currentGoes: Bool

    public init(kept: [PlayerItem], keptIndex: Int, removed: [Int], currentGoes: Bool) {
        self.kept = kept
        self.keptIndex = keptIndex
        self.removed = removed
        self.currentGoes = currentGoes
    }
}

/// `ServerScope.kt:67-78` (`ServerSwitch`).
public enum PlayerServerSwitch {
    /// Media that came from a server, by path; everything but the phone's own.
    public static func fromServer(_ item: PlayerItem) -> Bool {
        item.kind != .device && item.savedKey == nil
    }

    public static func plan(queue: [PlayerItem], index: Int) -> PlayerServerSwitchPlan {
        let kept = queue.filter { !fromServer($0) }
        let removed = queue.indices.filter { fromServer(queue[$0]) }.sorted(by: >)
        let current = (index >= 0 && index < queue.count) ? queue[index] : nil
        let currentGoes = current.map(fromServer) ?? true
        // The phone's songs before the play head: the current one's place
        // among them, or — when the current one goes — the next one's.
        let head = min(max(index, 0), queue.count)
        let before = queue[0..<head].filter { !fromServer($0) }.count
        let keptIndex = kept.isEmpty ? 0 : min(before, kept.count - 1)
        return PlayerServerSwitchPlan(
            kept: kept, keptIndex: keptIndex, removed: removed, currentGoes: currentGoes
        )
    }
}
