import Foundation

/// The room queue, kept quick with a long queue in it.
///
/// `RoomQueueWindow.kt` (Android 6d14aa91; the web's Room queue tab,
/// music.jsx `QueueTab`, merge 715a9681): a whole playlist can be a room's
/// queue (~1,500 songs) since 2026-11-12. Three things, plain values so a
/// package test can hold each:
///
/// - which read is **this** room's queue — an answer for another room or
///   server is never shown, and adding waits for this room's own answer;
/// - when a `music.now_playing.changed` is worth a re-read — the event
///   carries every room's now-playing, and only **this** room's state or
///   song counts;
/// - which rows are drawn: 100 at a time, more as the list scrolls, the
///   songs already played folded away deep into a queue.

/// A room queue read, with the server and room it was asked of
/// ([[read-after-await-wrong-key]]).
public struct RoomQueueRead: Equatable, Sendable {
    public let server: String
    public let room: String
    public let queue: RoomQueue

    public init(server: String, room: String, queue: RoomQueue) {
        self.server = server
        self.room = room
        self.queue = queue
    }
}

public enum RoomQueueWindow {

    /// The queue to show for `room` on `server`: the read when it was asked
    /// of them and the server says it is that room's; else nil (still
    /// reading). The read for a room just picked is in flight while the last
    /// room's answer is still held — that answer is not this room's queue.
    public static func queue(_ read: RoomQueueRead?, server: String, room: String?) -> RoomQueue? {
        guard let read, let room, read.server == server, read.room == room else { return nil }
        let said = read.queue.roomID
        return said.isEmpty || said == room ? read.queue : nil
    }

    /// The search that adds to the room is drawn only where adding works: the
    /// queue answered for this room and says this caller may change it
    /// (`editable`: the household, or a guest on a playing room, and not
    /// blocked). Otherwise the queue's `blocked_reason` is said instead.
    public static func canAdd(answered: Bool, queue: RoomQueue) -> Bool {
        answered && queue.editable
    }

    // MARK: - re-reading on now-playing

    /// What makes a room's queue worth re-reading: its state and its song,
    /// from the event's per-room value (realtime.py `_snapshot_now_playing`:
    /// `{state, song, stream_url}`). The web's `roomPlaySig`.
    public static func playSig(_ value: Any?) -> String {
        guard let object = value as? [String: Any] else { return "" }
        func text(_ any: Any?) -> String { (any as? String) ?? "" }
        let songSig: String
        if let song = object["song"] as? [String: Any] {
            songSig = "\(text(song["file"]))|\(text(song["title"]))"
        } else {
            songSig = ""
        }
        return "\(text(object["state"]))|\(songSig)"
    }

    /// The event frame's `data`, the object the server publishes under
    /// `{"type": …, "data": …}` (realtime.py:391), or nil.
    public static func eventData(_ event: StateEvent) -> Any? {
        guard let bytes = event.frame.data(using: .utf8),
              let frame = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] else { return nil }
        return frame["data"]
    }

    // MARK: - drawing a long queue a page at a time

    /// The rows are drawn this many at a time (the web's `QUEUE_ROWS_STEP`).
    public static let rowsStep = 100

    /// Songs played before the one playing that stay drawn above it (the
    /// web's `QUEUE_FOLD_AFTER`).
    public static let foldAfter = 20

    /// "show N played songs": the songs folded away above the window.
    public static func showEarlierLabel(_ count: Int) -> String {
        "show \(count) played song\(count == 1 ? "" : "s")"
    }

    /// "show more (N more)": the rows past the window.
    public static func showMoreLabel(_ count: Int) -> String { "show more (\(count) more)" }

    /// The lines drawn for `items`: at most `shown` entries from the window's
    /// start, the Up next head where it belongs, and a line for what is left
    /// either side.
    ///
    /// Deep into a long queue (``foldAfter`` songs played) the window starts
    /// two songs before the one playing, unless `earlier` was asked for.
    /// Every ``RoomQueueLine/entry(_:)`` keeps its index in the **whole**
    /// queue, so up/down, a jump, remove and the Up next head work the same
    /// on rows past the first page. The head goes before the first pick
    /// still to come when that pick is drawn, else after the list.
    public static func lines(_ queue: RoomQueue, items: [QueueItem], shown: Int, earlier: Bool) -> [RoomQueueLine] {
        let current = items.firstIndex(where: \.playing) ?? -1
        let from = (earlier || current < foldAfter) ? 0 : current - 2
        let to = min(items.count, from + max(shown, 1))
        let firstPick = queue.sessionID == nil ? -1 : (RoomUpNextRules.rows(items).first ?? -1)
        var out: [RoomQueueLine] = []
        out.reserveCapacity(max(0, to - from) + 3)
        if from > 0 { out.append(.showEarlier(from)) }
        var headDrawn = false
        if from < to {
            for index in from..<to {
                if index == firstPick {
                    out.append(.upNextHead)
                    headDrawn = true
                }
                out.append(.entry(index))
            }
        }
        if to < items.count { out.append(.showMore(items.count - to)) }
        if queue.sessionID != nil, !headDrawn { out.append(.upNextHead) }
        return out
    }
}

/// One line of the room queue's list.
public enum RoomQueueLine: Equatable, Hashable, Sendable {
    case upNextHead
    /// The entry at this index of the **whole** queue.
    case entry(Int)
    /// The played songs folded away above the drawn rows.
    case showEarlier(Int)
    /// The rows past the drawn ones.
    case showMore(Int)
}

/// Decides, per `music.now_playing.changed`, whether the room queue on show
/// reads its room again (`RoomPlayWatch`).
///
/// The event's `data` is `{room_id: {state, song, …}}` for every room: a
/// song changing in another room no longer re-reads (and re-draws) this
/// room's whole queue. An event without the rooms re-reads, as before. The
/// first event seen for a room and server re-reads too: what the room was
/// doing before it is not known here, and a change missed would leave a
/// stale queue on screen.
public struct RoomPlayWatch: Sendable {
    private var at: String?
    private var seen: String?

    public init() {}

    /// Forget what was seen (a room was picked).
    public mutating func reset() {
        at = nil
        seen = nil
    }

    public mutating func shouldReread(server: String, room: String?, data: Any?) -> Bool {
        guard let room else { return false }
        guard let all = data as? [String: Any] else { return true }
        let key = "\(server)\u{1F}\(room)"
        if at != key {
            at = key
            seen = nil
        }
        let now = RoomQueueWindow.playSig(all[room])
        if now == seen { return false }
        seen = now
        return true
    }
}
