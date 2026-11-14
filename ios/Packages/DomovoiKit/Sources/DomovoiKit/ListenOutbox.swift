import Foundation
import Synchronization

/// One listen waiting to go to `POST /api/music/listens`.
/// `OutboxRow` (`android/.../listens/ListenOutbox.kt`, a1e611b6).
///
/// `id` is this outbox's own (never sent): a flush deletes exactly the ids it
/// sent, so a song that ended while that flush was on its way stays.
/// `server` is the server the song came from (normalised address): a track
/// id means nothing on another server, so a row only ever goes to its own.
public struct OutboxRow: Equatable, Sendable, Codable {
    public let id: String
    public let server: String
    public let trackID: Int64
    public let deviceID: String
    public let startedAt: String
    public let playedSec: Int
    public let durationSec: Int?
    public let outcome: String
    public let listenerPersonID: Int64?
    public let asHousehold: Bool

    public init(
        id: String,
        server: String,
        trackID: Int64,
        deviceID: String,
        startedAt: String,
        playedSec: Int,
        durationSec: Int? = nil,
        outcome: String,
        listenerPersonID: Int64? = nil,
        asHousehold: Bool = false
    ) {
        self.id = id
        self.server = server
        self.trackID = trackID
        self.deviceID = deviceID
        self.startedAt = startedAt
        self.playedSec = playedSec
        self.durationSec = durationSec
        self.outcome = outcome
        self.listenerPersonID = listenerPersonID
        self.asHousehold = asHousehold
    }

    enum CodingKeys: String, CodingKey {
        case id, server, outcome
        case trackID = "track_id"
        case deviceID = "device_id"
        case startedAt = "started_at"
        case playedSec = "played_sec"
        case durationSec = "duration_sec"
        case listenerPersonID = "listener_person_id"
        case asHousehold = "as_household"
    }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        server = try c.decode(String.self, forKey: .server)
        trackID = try c.decode(Int64.self, forKey: .trackID)
        deviceID = try c.decode(String.self, forKey: .deviceID)
        startedAt = try c.decode(String.self, forKey: .startedAt)
        playedSec = try c.decode(Int.self, forKey: .playedSec)
        durationSec = try c.decodeIfPresent(Int.self, forKey: .durationSec)
        outcome = try c.decode(String.self, forKey: .outcome)
        listenerPersonID = try c.decodeIfPresent(Int64.self, forKey: .listenerPersonID)
        asHousehold = try c.decodeIfPresent(Bool.self, forKey: .asHousehold) ?? false
    }

    /// The row as the server takes it (API_REFERENCE §3.5e): never the
    /// outbox's own `id` or `server`; `listener_person_id` and
    /// `as_household` only when they say something.
    public var wire: Wire { Wire(row: self) }

    public struct Wire: Encodable {
        let row: OutboxRow

        enum CodingKeys: String, CodingKey {
            case trackID = "track_id"
            case deviceID = "device_id"
            case startedAt = "started_at"
            case playedSec = "played_sec"
            case durationSec = "duration_sec"
            case outcome
            case listenerPersonID = "listener_person_id"
            case asHousehold = "as_household"
            case player, origin
        }

        public func encode(to encoder: any Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(row.trackID, forKey: .trackID)
            try c.encode(row.deviceID, forKey: .deviceID)
            try c.encode(row.startedAt, forKey: .startedAt)
            try c.encode(row.playedSec, forKey: .playedSec)
            try c.encodeIfPresent(row.durationSec, forKey: .durationSec)
            try c.encode(row.outcome, forKey: .outcome)
            try c.encodeIfPresent(row.listenerPersonID, forKey: .listenerPersonID)
            if row.asHousehold { try c.encode(true, forKey: .asHousehold) }
            try c.encode(ListenOutbox.player, forKey: .player)
            try c.encode("explicit", forKey: .origin)
        }
    }

    /// `2026-11-12T18:03:11Z`: whole seconds, UTC.
    public static func isoUTC(epochMs: Int64) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = TimeZone(identifier: "UTC")
        let seconds = (epochMs >= 0 ? epochMs : epochMs - 999) / 1000
        return formatter.string(from: Date(timeIntervalSince1970: TimeInterval(seconds)))
    }
}

/// The listening-history outbox: the phone's listens wait here until the
/// server has taken them, across restarts and days offline (the server takes
/// rows up to 7 days late). `ListenOutbox` (`ListenOutbox.kt`).
///
/// Kept as ONE JSON file in the app's own storage, replaced atomically on
/// every change (`Data.write(options: .atomic)`, a temporary file renamed
/// over it): a reader sees the old list or the new one, never half of one,
/// and a process killed mid-write loses at most that write. At 500 short rows
/// the file is small (~100 KB at most) and read once per change.
///
/// At most ``max`` rows: the oldest go first. Thread safe (one lock).
public final class ListenOutbox: Sendable {

    /// The most rows kept (the oldest go first).
    public static let max = 500

    /// The most rows one POST carries (the server's MAX_BATCH).
    public static let batchSize = 200

    /// What the rows say played them (`player`: optional, the server reads
    /// the registered platform; Android sends "android").
    public static let player = "ios"

    private let file: URL
    private let lock = Mutex(())

    public init(file: URL) {
        self.file = file
    }

    /// The app's outbox: `Application Support/listens-outbox.json`, which is
    /// backed up with the app like any of its own data and never shared.
    public static func forApp() -> ListenOutbox {
        let base = (try? FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        )) ?? FileManager.default.temporaryDirectory
        return ListenOutbox(file: base.appendingPathComponent("listens-outbox.json"))
    }

    public func all() -> [OutboxRow] { lock.withLock { _ in load() } }

    /// Add `row` at the end, dropping the oldest past ``max``.
    public func add(_ row: OutboxRow) {
        lock.withLock { _ in save(Array((load() + [row]).suffix(Self.max))) }
    }

    /// The oldest `limit` rows waiting for `server`.
    public func batch(server: String, limit: Int = ListenOutbox.batchSize) -> [OutboxRow] {
        lock.withLock { _ in Array(load().filter { $0.server == server }.prefix(limit)) }
    }

    /// Delete exactly `ids` (a flush's own rows).
    public func remove(_ ids: Set<String>) {
        guard !ids.isEmpty else { return }
        lock.withLock { _ in
            let rows = load()
            let kept = rows.filter { !ids.contains($0.id) }
            if kept.count != rows.count { save(kept) }
        }
    }

    /// Forget every row for `server` (the phone forgot that server).
    public func forget(server: String) {
        lock.withLock { _ in
            let rows = load()
            let kept = rows.filter { $0.server != server }
            if kept.count != rows.count { save(kept) }
        }
    }

    private func load() -> [OutboxRow] {
        guard let data = try? Data(contentsOf: file) else { return [] }
        return (try? JSONDecoder().decode([OutboxRow].self, from: data)) ?? []
    }

    private func save(_ rows: [OutboxRow]) {
        try? FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        guard let data = try? JSONEncoder().encode(rows) else { return }
        try? data.write(to: file, options: .atomic)
    }
}
