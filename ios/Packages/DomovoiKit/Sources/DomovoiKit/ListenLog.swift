import Foundation

/// What the phone's player is doing, as listening history needs to see it:
/// the entry in front, whether it is making sound, whether that is HERE (not
/// a cast), and the engine's own length for it when known.
public struct ListenSnapshot: Equatable, Sendable {
    public let item: PlayerItem?
    public let playing: Bool
    public let local: Bool
    public let durationSec: Double?

    public init(item: PlayerItem?, playing: Bool, local: Bool, durationSec: Double? = nil) {
        self.item = item
        self.playing = playing
        self.local = local
        self.durationSec = durationSec
    }
}

/// The player's hooks for listening history — the whole of what
/// ``PlayerModel`` knows about it (`PlayerModel.listens`).
///
/// `observe` is called with the player's state after every change it
/// publishes; the other three carry the one thing a snapshot cannot: WHY the
/// song in front changed. Android's `PlayerController` makes the same calls
/// (`transition` / `playing` from ExoPlayer's listener, `skip` from
/// next/jump/remove, `ended` from `STATE_ENDED`, `stopped` from a new play,
/// casting and clearing).
@MainActor
public protocol PlayerListenHooks: AnyObject {
    func observe(_ snapshot: ListenSnapshot)
    /// The song in front was skipped: next, a queue row, or removing it.
    func skip()
    /// The song in front ran to its end.
    func ended()
    /// Stopped: a new play, the queue cleared.
    func stopped()
}

/// Listening history from the phone's own player (V051, Up next P1; the
/// web's ListenLog, web/static/player.jsx). `ListenLog`
/// (`android/.../listens/ListenLog.kt`, a1e611b6). Each LIBRARY song the
/// phone plays is one row on its server, written to the ``ListenOutbox``
/// when it ends and sent by the ``ListenSender``:
///
///  * logged: library tracks (``PlayerKind/library``, a track id of the
///    server's library) played HERE. Never while casting (the room logs its
///    own), never in room mode, and never a podcast, an audiobook, radio or a
///    song stored on the phone;
///  * whose: the "listening as" pick for that server when there is one
///    (`listener_person_id`, or `as_household` for "household" on a claimed
///    phone), else the server decides (whose the phone is, unless it is a
///    shared screen). A shared screen sends no pick;
///  * sent: a few seconds after a song ends, when the network comes back,
///    when the app goes to the background, and after each registration.
@MainActor
public final class ListenLog: PlayerListenHooks {

    public static let flushDelayMs: Int64 = 5_000

    private let outbox: ListenOutbox
    private var tracker: ListenTracker
    private let sender: ListenSender?
    /// The active server (normalised), "" for none.
    private let server: () -> String
    private let deviceID: () -> String
    /// The raw "listening as" pick for a server (``PlayerListenerStore/pick(for:)``).
    private let pickFor: (String) -> String?
    private let sharedAt: (String) -> Bool
    /// Whether the phone is in room mode (no household calls at all).
    private let roomMode: () -> Bool
    private let newID: () -> String
    /// How the outbox is written: off the main actor in the app, in place in
    /// the tests.
    private let io: (@escaping @Sendable () -> Void) -> Void
    private let sleep: @Sendable (Int64) async -> Void

    /// False once a server said the household keeps no listening history
    /// (`recording: false`): nothing more is logged until the app starts
    /// again.
    public private(set) var recording = true

    /// The entry the last snapshot showed, to tell a change of song from a
    /// change of state.
    private var lastKey: String?
    private var flushTask: Task<Void, Never>?

    public init(
        outbox: ListenOutbox,
        tracker: ListenTracker,
        sender: ListenSender?,
        server: @escaping () -> String,
        deviceID: @escaping () -> String,
        pickFor: @escaping (String) -> String?,
        sharedAt: @escaping (String) -> Bool,
        roomMode: @escaping () -> Bool = { false },
        newID: @escaping () -> String = { UUID().uuidString },
        io: @escaping (@escaping @Sendable () -> Void) -> Void = { work in
            Task.detached(priority: .utility) { work() }
        },
        sleep: @Sendable @escaping (Int64) async -> Void = { ms in
            try? await Task.sleep(for: .milliseconds(ms))
        }
    ) {
        self.outbox = outbox
        self.tracker = tracker
        self.sender = sender
        self.server = server
        self.deviceID = deviceID
        self.pickFor = pickFor
        self.sharedAt = sharedAt
        self.roomMode = roomMode
        self.newID = newID
        self.io = io
        self.sleep = sleep
    }

    private func eligible(_ item: PlayerItem?, local: Bool) -> Bool {
        guard recording, local, !roomMode(), let item else { return false }
        return item.kind == .library && item.itemID > 0 && !server().isEmpty
    }

    private static func key(_ item: PlayerItem?) -> String? { item?.uid }

    // MARK: - the hooks

    public func observe(_ s: ListenSnapshot) {
        let key = Self.key(s.item)
        if key != lastKey || !s.local {
            // Another song in front (or none), or the sound went to a room:
            // what was open is over. Why, if a hook said so, was recorded
            // there; anything else is "stopped".
            lastKey = key
            close(natural: false)
            if eligible(s.item, local: s.local), let item = s.item {
                tracker.begin(trackID: item.itemID, server: server(), durationSec: item.durationSec, playing: s.playing)
            }
        } else if s.playing, tracker.openTrack == nil, eligible(s.item, local: s.local), let item = s.item {
            // The same song starting again with nothing followed: played on
            // after it ended, back from a cast, after a stop.
            tracker.begin(trackID: item.itemID, server: server(), durationSec: item.durationSec, playing: true)
        }
        if let d = s.durationSec, d > 0, let item = s.item, tracker.openTrack == item.itemID {
            tracker.duration(d)
        }
        tracker.playing(s.playing && s.local)
    }

    /// Closed now, as skipped: the next snapshot shows the new song.
    public func skip() {
        tracker.skip()
        close(natural: false)
    }

    public func ended() { close(natural: true) }

    public func stopped() { close(natural: false) }

    private func close(natural: Bool) {
        guard let row = tracker.close(natural: natural) else { return }
        let out = toOutbox(row)
        let outbox = self.outbox
        io { outbox.add(out) }
        flushSoon()
    }

    func toOutbox(_ row: ListenRow) -> OutboxRow {
        let shared = sharedAt(row.server)
        let pick = shared ? nil : pickFor(row.server)
        return OutboxRow(
            id: newID(),
            server: row.server,
            trackID: row.trackID,
            deviceID: deviceID(),
            startedAt: OutboxRow.isoUTC(epochMs: row.startedAtMs),
            playedSec: row.playedSec,
            durationSec: row.durationSec,
            outcome: row.outcome,
            listenerPersonID: pick.flatMap { $0 == ListenerPicks.household ? nil : Int64($0) },
            asHousehold: pick == ListenerPicks.household
        )
    }

    // MARK: - sending

    /// Send what waits in a few seconds (debounced: songs that end close
    /// together go in one batch).
    public func flushSoon(delayMs: Int64 = ListenLog.flushDelayMs) {
        guard let sender else { return }
        flushTask?.cancel()
        let sleep = self.sleep
        flushTask = Task {
            if delayMs > 0 { await sleep(delayMs) }
            guard !Task.isCancelled else { return }
            await sender.flush()
        }
    }

    /// Send what waits now (the app going to the background).
    public func flushNow() { flushSoon(delayMs: 0) }

    /// A server said it keeps no listening history.
    public func recordingOff() {
        recording = false
        _ = tracker.close(natural: false)
    }
}
