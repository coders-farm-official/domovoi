import Foundation

/*
 * One place per book on the phone — the owner decision of 2026-10-20, ported
 * whole from `audiobooks/BookPositions.kt:13-26`: streaming and an offline
 * copy read and write the same position, it is kept on the phone, and it goes
 * up to the server's slot for this phone (`POST
 * /api/audiobooks/{id}/position`) whenever the book's server is the connected
 * one. Rooms keep their own places; the "continue from the kitchen" offer
 * reads those (``BookContinueRules``, `AudiobookContinue.swift`).
 *
 * The server's slot for this phone is written by this phone only, so it can
 * move without the phone — a reinstall, cleared data, or another install
 * sharing the device id. ``BookPositionRules`` decides which place stands:
 * the phone's when only the phone moved, the server's when only the server
 * did, and the newer of the two when both did.
 */

/// Which server a place belongs to. Android's `player/ServerScope`
/// (`ServerScope.kt:26`), which iOS has no equivalent of.
///
/// The identity when both sides know one — a house keeps its pinned key
/// across a DHCP lease — else the address. **No server is never the same
/// server**, which is what stops every place on a phone with no server
/// configured from being treated as one house's.
public struct BookServerKey: Codable, Sendable, Equatable {

    public let address: String?
    public let fingerprint: String?

    public init(address: String?, fingerprint: String?) {
        self.address = address
        self.fingerprint = fingerprint
    }

    /// The app is connected to no server.
    public static let none = BookServerKey(address: nil, fingerprint: nil)

    /// The saved server with the identity pinned for it — `ServerScope.of`
    /// (`ServerScope.kt:44`), down to using the **pin key** as the address so
    /// two spellings of one house are one key.
    public static func of(serverURL: String?, pin: ServerIdentity.Pin?) -> BookServerKey {
        let url = serverURL.flatMap { ServerCredentials.isBlank($0) ? nil : $0 }
        var mark = pin?.fingerprint
        if let held = mark, ServerCredentials.isBlank(held) { mark = nil }
        return BookServerKey(address: IdentityGate.pinKey(url), fingerprint: mark)
    }

    /// `ServerScope.isSameServer` (`:30`).
    public func isSame(_ other: BookServerKey) -> Bool {
        if let mine = fingerprint, let theirs = other.fingerprint { return mine == theirs }
        guard let address else { return false }
        return address == other.address
    }
}

/// The phone's place in one book. `BookPositions.kt:30`.
///
/// ### Still no `personId` on the **phone's** record, and now on purpose
///
/// Android's record carries the listener (`BookPositions.kt:34`), and as of
/// D11 iOS does store one (``PlayerListenerStore``) — but it is **not** in
/// here, and that is a decision rather than an omission.
///
/// The phone keeps *one place per book*, which is the owner's decision of
/// 2026-10-20: a streamed book and a saved copy share it. Keying it by
/// listener as well would make "where am I in this book" depend on who
/// Settings last said was listening, so picking a person would lose the place
/// on the phone. What the listener decides is which **server row** the place
/// goes to and comes from, and that is where it is sent:
/// ``ApiBookPositionRemote`` reads it per request, exactly as Android's
/// `ApiPositionRemote` reads `prefs.listenerPersonId.value` at each call
/// (`ApiPositionRemote.kt:27`, `:40`, `:60`).
///
/// The consequence is written down rather than hidden: a phone whose listener
/// changes keeps its own place and begins reading and writing a different
/// server row. `AudiobookListenerTests` is what says so.
public struct LocalBookPosition: Codable, Sendable, Equatable {

    public let bookID: Int64
    public let serverAddress: String?
    public let serverFingerprint: String?
    public let positionSec: Double
    public let speed: Double

    /// The **phone's** clock, in milliseconds, when the place was last set
    /// here. Compared against the server's `updated_at` only in the one
    /// branch where both moved, and `BookPositions.kt:81-84` says why that is
    /// good enough on a home network.
    public let updatedAtMs: Int64

    /// Set here since the server last had it.
    public let dirty: Bool

    /// The server's `updated_at` for this slot when the two last agreed.
    public let serverStamp: String?

    public init(
        bookID: Int64,
        serverAddress: String? = nil,
        serverFingerprint: String? = nil,
        positionSec: Double,
        speed: Double = 1,
        updatedAtMs: Int64,
        dirty: Bool = true,
        serverStamp: String? = nil
    ) {
        self.bookID = bookID
        self.serverAddress = serverAddress
        self.serverFingerprint = serverFingerprint
        self.positionSec = positionSec
        self.speed = speed
        self.updatedAtMs = updatedAtMs
        self.dirty = dirty
        self.serverStamp = serverStamp
    }

    public var server: BookServerKey {
        BookServerKey(address: serverAddress, fingerprint: serverFingerprint)
    }

    /// `BookPositions.kt:46`.
    public func isFor(bookID: Int64, server: BookServerKey) -> Bool {
        self.bookID == bookID && self.server.isSame(server)
    }

    /// The same record with the two sync flags moved — Kotlin's `copy(...)`.
    public func settled(dirty: Bool, serverStamp: String?) -> LocalBookPosition {
        LocalBookPosition(
            bookID: bookID,
            serverAddress: serverAddress,
            serverFingerprint: serverFingerprint,
            positionSec: positionSec,
            speed: speed,
            updatedAtMs: updatedAtMs,
            dirty: dirty,
            serverStamp: serverStamp
        )
    }

    private enum CodingKeys: String, CodingKey {
        case bookID = "bookId"
        case serverAddress, serverFingerprint, positionSec, speed, updatedAtMs, dirty, serverStamp
    }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        bookID = try values.decodeIfPresent(Int64.self, forKey: .bookID) ?? 0
        serverAddress = try values.decodeIfPresent(String.self, forKey: .serverAddress)
        serverFingerprint = try values.decodeIfPresent(String.self, forKey: .serverFingerprint)
        positionSec = try values.decodeIfPresent(Double.self, forKey: .positionSec) ?? 0
        speed = try values.decodeIfPresent(Double.self, forKey: .speed) ?? 1
        updatedAtMs = try values.decodeIfPresent(Int64.self, forKey: .updatedAtMs) ?? 0
        dirty = try values.decodeIfPresent(Bool.self, forKey: .dirty) ?? true
        serverStamp = try values.decodeIfPresent(String.self, forKey: .serverStamp)
    }
}

/// Which place stands after comparing the two. `BookPositions.kt:54`.
public struct BookPositionResolution: Sendable, Equatable {
    public let positionSec: Double
    public let speed: Double

    /// The phone's place is newer: send it to the server.
    public let upload: Bool

    /// The server's place is newer: the phone takes it.
    public let adopted: Bool

    public init(positionSec: Double, speed: Double, upload: Bool, adopted: Bool) {
        self.positionSec = positionSec
        self.speed = speed
        self.upload = upload
        self.adopted = adopted
    }
}

/// `PositionRules` (`BookPositions.kt:63`), pure and ported whole.
public enum BookPositionRules {

    /// Which of the two places stands. Nil when **neither** side has one.
    ///
    /// "The server has a row" is `server != nil && server.updatedAt != nil`,
    /// which is exactly why the route's answer for a book nobody has started
    /// — `{"position_sec": 0, "speed": 1.0}` with no `updated_at` at all —
    /// must keep that field absent all the way through
    /// ``ServerBookPosition``.
    public static func resolve(
        local: LocalBookPosition?,
        server: ServerBookPosition?
    ) -> BookPositionResolution? {
        let serverHas = server?.hasRow ?? false
        guard let local else {
            guard serverHas, let server else { return nil }
            return BookPositionResolution(
                positionSec: server.positionSec,
                speed: server.speed,
                upload: false,
                adopted: true
            )
        }
        guard serverHas, let server else {
            return BookPositionResolution(
                positionSec: local.positionSec,
                speed: local.speed,
                upload: local.positionSec > 0,
                adopted: false
            )
        }
        let serverMoved = server.updatedAt != local.serverStamp
        let phoneWins: Bool
        if !serverMoved {
            phoneWins = true
        } else if !local.dirty {
            phoneWins = false
        } else {
            // Both moved: the newer one. The two clocks are the phone's and
            // the server's; on a home network they agree well enough for
            // "which did I touch last" (`BookPositions.kt:81-84`).
            let serverMs = isoMs(server.updatedAt) ?? Int64.min
            phoneWins = local.updatedAtMs >= serverMs
        }
        if phoneWins {
            return BookPositionResolution(
                positionSec: local.positionSec,
                speed: local.speed,
                upload: local.dirty,
                adopted: false
            )
        }
        return BookPositionResolution(
            positionSec: server.positionSec,
            speed: server.speed,
            upload: false,
            adopted: true
        )
    }
}

/// The phone's places, in one small JSON blob. `PositionStore`
/// (`BookPositions.kt:96`).
///
/// It is a value over a ``SavedState`` rather than a class over a `File`
/// because that is the seam this package already has for "something kept
/// between launches", and it is what lets the cap and the ordering be driven
/// by a test with no disk. The app's conformer is
/// ``BookPositionFile``, which is the Kotlin's file — in Application
/// Support, written through a temporary and renamed over the old one.
public struct BookPositionStore: Sendable {

    /// A phone that has listened to a thousand books keeps the last 500
    /// places (`BookPositions.kt:123`).
    public static let maximum = 500

    private let place: any SavedState

    public init(place: any SavedState) {
        self.place = place
    }

    /// Every place held, newest first.
    ///
    /// A blob that cannot be decoded reads as **no places**, which is
    /// Kotlin's `runCatching { … }.getOrDefault(emptyList())`: a corrupt file
    /// loses the places and nothing else, where a throw here would take a
    /// book's play with it.
    public func all() -> [LocalBookPosition] {
        guard let blob = place.read(), !blob.isEmpty else { return [] }
        return (try? JSONDecoder().decode([LocalBookPosition].self, from: blob)) ?? []
    }

    public func get(bookID: Int64, server: BookServerKey) -> LocalBookPosition? {
        all().first { $0.isFor(bookID: bookID, server: server) }
    }

    /// `p` in place of whatever was held for the same book and server.
    public func put(_ p: LocalBookPosition) {
        let kept = all().filter { !$0.isFor(bookID: p.bookID, server: p.server) }
        let next = ([p] + kept)
            .sorted { $0.updatedAtMs > $1.updatedAtMs }
            .prefix(Self.maximum)
        guard let blob = try? JSONEncoder().encode(Array(next)) else { return }
        place.write(blob)
    }
}

/// The app's ``BookPositionStore`` backing: one JSON file in Application
/// Support, replaced atomically.
///
/// `Data.write(to:options:.atomic)` is the Kotlin's write-a-`.tmp`-and-rename
/// (`BookPositions.kt:114-119`) — Foundation writes a temporary beside the
/// file and renames it over, so a phone that dies mid-write has the old
/// places rather than half of the new ones.
///
/// `@unchecked Sendable` with its reason, as the house rule asks: the one
/// stored property is a `URL`, which is a value, and every read and write
/// goes through `FileManager`, which Apple documents as thread-safe for this
/// use. Nothing is cached in memory, so there is no state to race.
public struct BookPositionFile: SavedState, @unchecked Sendable {

    public let url: URL

    public init(url: URL) {
        self.url = url
    }

    /// `audiobook-places.json` in Application Support, which is where a
    /// phone's own records belong: not Documents (the person does not manage
    /// this file) and not Caches (the system may delete it, and a lost place
    /// is the one thing this feature is for).
    public static func inApplicationSupport(
        named: String = "audiobook-places.json"
    ) -> BookPositionFile? {
        guard let root = try? FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ) else { return nil }
        return BookPositionFile(url: root.appendingPathComponent(named))
    }

    public func read() -> Data? {
        try? Data(contentsOf: url)
    }

    public func write(_ blob: Data) {
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try? blob.write(to: url, options: .atomic)
    }
}

/// The places in memory, for a phone with no Application Support directory
/// and for a test.
///
/// `@unchecked Sendable` with its reason: the one stored property is read and
/// written only inside the lock, and neither method is async, so the lock is
/// never held across a suspension.
public final class BookPositionMemory: SavedState, @unchecked Sendable {

    private let lock = NSLock()
    private var blob: Data?

    public init(_ blob: Data? = nil) {
        self.blob = blob
    }

    public func read() -> Data? { lock.withLock { blob } }

    public func write(_ next: Data) { lock.withLock { blob = next } }
}

/// The connected server's slot for this phone, and this listener's places
/// elsewhere. `PositionRemote` (`BookPositions.kt:128`) plus the `others`
/// read Android keeps on `ApiPositionRemote`'s companion.
///
/// `others` is on the protocol and not a free function so one fake answers
/// everything ``BookStartPlanner`` asks.
public protocol BookPositionRemote: Sendable {

    /// The server this remote talks to **now**.
    var server: BookServerKey { get }

    func get(_ bookID: Int64) async throws -> ServerBookPosition

    func put(_ bookID: Int64, positionSec: Double, speed: Double) async throws

    /// This listener's places on other devices and rooms, newest first.
    func others(_ bookID: Int64) async throws -> [OtherBookPosition]
}

/// The real remote, over the app's own client. `ApiPositionRemote.kt:20`.
///
/// **It cannot be built without a device id.** Both routes require
/// `device_id` (`audiobooks.py:538-545`, `:599-604`), and an empty one is a
/// *different device* to the server rather than an absent one: a place
/// written under it would be stranded and a `/positions` read under it would
/// answer this phone's own rows. So a phone that has not introduced itself
/// gets **no remote at all** — the local place still works, and the cross
/// device offer is the only thing lost (DECISIONS §3).
public struct ApiBookPositionRemote: BookPositionRemote {

    public let server: BookServerKey

    private let client: DomovoiClient
    private let deviceID: String

    /// Who is listening, asked **at each request** rather than captured when
    /// this was built.
    ///
    /// Android's shape, and for its reason: `prefs.listenerPersonId.value` is
    /// read inside `query()` and inside `put` (`ApiPositionRemote.kt:27`,
    /// `:40`), so a person picked in Settings takes effect on the next save
    /// without anything being rebuilt. A captured `String?` would have made
    /// the picker a control that worked only after a relaunch.
    private let listener: @Sendable () -> String?

    /// - Parameter listener: ``PlayerListenerStore/personID``, as a closure.
    ///   Left out, every request is the unidentified listener's slot — which
    ///   is what a phone that has never opened the picker wants.
    public init?(
        client: DomovoiClient,
        deviceID: String?,
        server: BookServerKey,
        listener: @Sendable @escaping () -> String? = { nil }
    ) {
        guard let deviceID, !ServerCredentials.isBlank(deviceID) else { return nil }
        self.client = client
        self.deviceID = deviceID
        self.server = server
        self.listener = listener
    }

    public func get(_ bookID: Int64) async throws -> ServerBookPosition {
        try await client.get(
            AudiobookPath.position(bookID, deviceID: deviceID, personID: listener()),
            as: ServerBookPosition.self
        )
    }

    public func put(_ bookID: Int64, positionSec: Double, speed: Double) async throws {
        try await client.send(
            method: "POST",
            path: AudiobookPath.savePosition(bookID),
            body: AudiobookPath.saveBody(
                deviceID: deviceID,
                positionSec: positionSec,
                speed: speed,
                personID: listener()
            )
        )
    }

    public func others(_ bookID: Int64) async throws -> [OtherBookPosition] {
        try await client.get(
            AudiobookPath.positions(bookID, deviceID: deviceID, personID: listener()),
            as: OtherBookPositions.self
        ).positions
    }
}

/// The phone's places and their sync. `PositionSync` (`BookPositions.kt:142`).
///
/// An `actor` rather than a class with a mutex: every entry point is already
/// `async`, and the store's read-modify-write must not interleave with
/// another book's. **Where it differs from the Kotlin:** Android's single
/// `Mutex` serialises every push in the app, while an actor releases its turn
/// at each `await`, so two pushes for *different* books can be in flight at
/// once. The guard that matters is per-book and unchanged — the record's
/// `updatedAtMs` is captured before the request and re-read after it — and
/// ``pushInFlight`` keeps two pushes for the **same** book from racing, which
/// is all the Kotlin's lock was protecting.
public actor BookPositionSync {

    /// How long opening a book waits for the server's slot
    /// (`READ_TIMEOUT_MS`, `BookPositions.kt:211`). A server out of reach
    /// must not hold up play.
    public static let readTimeoutMs: Int64 = 3_000

    private let store: BookPositionStore
    private let remote: @Sendable () -> (any BookPositionRemote)?
    private let clock: @Sendable () -> Int64
    private let sleep: @Sendable (Int64) async throws -> Void

    /// Books with a push in flight.
    private var pushInFlight: Set<Int64> = []

    /// - Parameters:
    ///   - remote: answers for whichever server the app is connected to, nil
    ///     for none. A book's place goes **only** to the server the book came
    ///     from (``remoteFor(_:)``).
    ///   - clock: the phone's wall clock in milliseconds. Wall and not
    ///     monotonic, because the value is compared against the server's own
    ///     `updated_at` in one branch of ``BookPositionRules``.
    public init(
        store: BookPositionStore,
        remote: @Sendable @escaping () -> (any BookPositionRemote)?,
        clock: @Sendable @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        sleep: @Sendable @escaping (Int64) async throws -> Void = {
            try await Task.sleep(for: .milliseconds($0))
        }
    ) {
        self.store = store
        self.remote = remote
        self.clock = clock
        self.sleep = sleep
    }

    private func remoteFor(_ server: BookServerKey) -> (any BookPositionRemote)? {
        guard let there = remote(), there.server.isSame(server) else { return nil }
        return there
    }

    /// The phone's place, as it stands here. `BookPositions.kt:153`.
    public func local(bookID: Int64, server: BookServerKey) -> LocalBookPosition? {
        store.get(bookID: bookID, server: server)
    }

    /// Set the place here; it goes up on the next ``push(bookID:server:)``.
    /// `BookPositions.kt:156`.
    ///
    /// The previous record's `serverStamp` is kept, because what the server
    /// last held has not changed just because the phone moved.
    public func record(bookID: Int64, server: BookServerKey, positionSec: Double, speed: Double) {
        let before = store.get(bookID: bookID, server: server)
        store.put(
            LocalBookPosition(
                bookID: bookID,
                serverAddress: server.address ?? before?.serverAddress,
                serverFingerprint: server.fingerprint ?? before?.serverFingerprint,
                positionSec: max(0, positionSec.isFinite ? positionSec : 0),
                speed: speed,
                updatedAtMs: clock(),
                dirty: true,
                serverStamp: before?.serverStamp
            )
        )
    }

    /// Where `bookID` starts: the phone's place and the server's, compared
    /// (``BookPositionRules``) when the book's server is connected, else the
    /// phone's alone. `BookPositions.kt:172`.
    ///
    /// The winner is kept here and sent up when it is the phone's. **Never
    /// throws**: a server that cannot be read leaves the phone's place
    /// standing, which is the whole point of keeping one.
    public func open(bookID: Int64, server: BookServerKey) async -> (positionSec: Double, speed: Double) {
        let local = store.get(bookID: bookID, server: server)
        let mine = (local?.positionSec ?? 0, local?.speed ?? 1)
        guard let there = remoteFor(server) else { return mine }
        guard let theirs = await BookTimeout.bounded(
            ms: Self.readTimeoutMs,
            sleep: sleep,
            { try await there.get(bookID) }
        ) else { return mine }
        guard let decided = BookPositionRules.resolve(local: local, server: theirs) else { return (0, 1) }
        if decided.adopted {
            store.put(
                LocalBookPosition(
                    bookID: bookID,
                    serverAddress: server.address,
                    serverFingerprint: server.fingerprint,
                    positionSec: decided.positionSec,
                    speed: decided.speed,
                    updatedAtMs: clock(),
                    dirty: false,
                    serverStamp: theirs.updatedAt
                )
            )
        } else if decided.upload {
            _ = await push(bookID: bookID, server: server)
        }
        return (decided.positionSec, decided.speed)
    }

    /// Send the phone's place for `bookID` when it is newer and the book's
    /// server is connected. False when nothing went — offline, or nothing
    /// new. `BookPositions.kt:194`.
    ///
    /// ### The in-flight guard
    ///
    /// After the POST the slot is re-read for its new `updated_at`, and
    /// `dirty` is cleared **only if nothing newer was recorded while the
    /// request was on its way** — `now.updatedAtMs == p.updatedAtMs`. The key
    /// is captured before the first `await` and compared after the last, which
    /// is the read-after-await rule this track lost three bugs to: a place
    /// recorded during the round trip would otherwise be marked clean and
    /// never sent.
    @discardableResult
    public func push(bookID: Int64, server: BookServerKey) async -> Bool {
        guard !pushInFlight.contains(bookID) else { return false }
        guard let p = store.get(bookID: bookID, server: server), p.dirty else { return false }
        guard let there = remoteFor(p.server) else { return false }
        pushInFlight.insert(bookID)
        defer { pushInFlight.remove(bookID) }
        do {
            try await there.put(bookID, positionSec: p.positionSec, speed: p.speed)
        } catch {
            return false
        }
        // What the server now holds for the slot, so the next compare knows
        // it has not moved since. A failed re-read leaves the stamp nil,
        // which makes the next compare see a server that moved — the safe
        // direction, and what the Kotlin's `getOrNull()` does.
        let stamp = try? await there.get(bookID).updatedAt
        guard let now = store.get(bookID: bookID, server: server), now.updatedAtMs == p.updatedAtMs else {
            return true
        }
        store.put(now.settled(dirty: false, serverStamp: stamp))
        return true
    }

    /// Every place set here while away, for books of `server`, on reconnect.
    /// Returns how many went. `BookPositions.kt:217`.
    @discardableResult
    public func pushAll(_ server: BookServerKey) async -> Int {
        guard remoteFor(server) != nil else { return 0 }
        let mine = store.all().filter { $0.dirty && $0.server.isSame(server) }
        var sent = 0
        for p in mine {
            // Sequentially, as the Kotlin's single mutex made it: twenty
            // places going up at once on a reconnect is twenty requests at
            // once to a server that has just come back.
            if await push(bookID: p.bookID, server: p.server) { sent += 1 }
        }
        return sent
    }

}
