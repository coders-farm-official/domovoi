import Foundation

/// A room's queue, through the web's room-queue routes
/// (`web/backend/api/music_queue.py`, API_REFERENCE §3.5a), and the room's
/// Up next session (§3.5f).
///
/// `RoomQueueApi.kt` (21b5ccfb, cb780e90): read, add (after the playing song,
/// or at the end before the room's Up next picks), move, remove, clear, play
/// one entry now (the room's real jump, MPD `playid`) and save the queue as a
/// playlist; and the session's autoplay switch and "less like this". **Each
/// is one request**: nothing here chains a second call on the first one's
/// answer.
public struct RoomQueueApi: Sendable {

    public static let whereNext = "next"
    public static let whereEnd = "end"

    private let client: DomovoiClient
    private let deviceID: String

    public init(client: DomovoiClient, deviceID: String) {
        self.client = client
        self.deviceID = deviceID
    }

    public func read(_ room: String) async throws(DomovoiError) -> RoomQueue {
        try await client.get(MusicQueuePath.read(room: room, deviceID: deviceID), as: RoomQueue.self)
    }

    /// Adds `trackIDs`: `where` `next` right after the song playing, `end`
    /// after the songs somebody asked for, before the room's Up next picks.
    /// The songids the room gave the new entries, in order.
    @discardableResult
    public func add(_ room: String, trackIDs: [Int64], where place: String = whereEnd) async throws(DomovoiError) -> [Int64] {
        let answer = try await client.post(
            MusicQueuePath.add(room: room),
            body: Self.addBody(trackIDs: trackIDs, deviceID: deviceID, where: place),
            as: QueueAddAnswer.self
        )
        return answer.songIDs
    }

    /// `trackIDs` right after the song the room is playing, in order. How
    /// many the room took.
    public func playNext(_ room: String, trackIDs: [Int64]) async throws(DomovoiError) -> Int {
        try await add(room, trackIDs: trackIDs, where: Self.whereNext).count
    }

    public func move(_ room: String, songID: Int64, to position: Int) async throws(DomovoiError) {
        try await client.send(
            method: "POST",
            path: MusicQueuePath.move(room: room),
            body: MusicQueueModel.json(songID: songID, to: position, deviceID: deviceID)
        )
    }

    public func remove(_ room: String, songIDs: [Int64]) async throws(DomovoiError) {
        try await client.send(
            method: "POST",
            path: MusicQueuePath.remove(room: room),
            body: MusicQueueModel.json(songIDs: songIDs, deviceID: deviceID)
        )
    }

    public func clear(_ room: String) async throws(DomovoiError) {
        try await client.send(
            method: "POST",
            path: MusicQueuePath.clear(room: room),
            body: MusicQueueModel.json(deviceID: deviceID)
        )
    }

    /// Plays the entry `songID` now: the room's own jump (`404` when it left
    /// the queue).
    public func play(_ room: String, songID: Int64) async throws(DomovoiError) {
        try await client.send(
            method: "POST",
            path: MusicQueuePath.play(room: room),
            body: Self.playBody(songID: songID, deviceID: deviceID)
        )
    }

    /// The room's queue, in order, as a new playlist named `name` (`409`
    /// taken, `422` nothing to save).
    public func savePlaylist(_ room: String, name: String) async throws(DomovoiError) -> RoomSaveResult {
        try await client.post(
            MusicQueuePath.savePlaylist(room: room),
            body: RoomPlayBodies.encode(["name": name, "device_id": deviceID]),
            as: RoomSaveResult.self
        )
    }

    /// The room's autoplay switch: its session, PATCHed.
    public func setAutoplay(session: String, on: Bool) async throws(DomovoiError) {
        _ = try await client.raw(
            method: "PATCH",
            path: MusicQueuePath.session(session),
            body: RoomPlayBodies.encode(["autoplay": on])
        )
    }

    /// "Less like this" on the pick `seq`: the seqs the session took back.
    public func lessLikeThis(session: String, seq: Int) async throws(DomovoiError) -> [Int] {
        let answer = try await client.post(
            MusicQueuePath.feedback(session: session),
            body: RoomPlayBodies.encode(["seq": seq, "kind": "less_like_this"]),
            as: QueueFeedbackAnswer.self
        )
        return answer.removed
    }

    // MARK: - the bodies

    /// `{"device_id":…,"track_ids":[…],"where":"next"}`.
    public static func addBody(trackIDs: [Int64], deviceID: String, where place: String) -> Data {
        RoomPlayBodies.encode(["track_ids": trackIDs, "device_id": deviceID, "where": place])
    }

    /// `{"device_id":…,"song_id":7}`.
    public static func playBody(songID: Int64, deviceID: String) -> Data {
        RoomPlayBodies.encode(["song_id": songID, "device_id": deviceID])
    }

    /// The playing entry's position, or nil when nothing is.
    public static func playingPos(_ queue: RoomQueue) -> Int? {
        guard let current = queue.currentSongID else { return nil }
        return queue.items.first { $0.songID == current }?.pos
    }
}

/// `queued: [{song_id, file}]` from an add's answer (`main.py`'s
/// `/v1/admin/music/queue/{room}/add`, bridged as it stands).
struct QueueAddAnswer: Decodable {
    let songIDs: [Int64]

    private enum CodingKeys: String, CodingKey { case queued }
    private struct Entry: Decodable {
        let songID: Int64?
        private enum CodingKeys: String, CodingKey { case songID = "song_id" }
        init(from decoder: any Decoder) throws {
            let values = try decoder.container(keyedBy: CodingKeys.self)
            songID = try? values.decodeIfPresent(Int64.self, forKey: .songID)
        }
    }

    init(from decoder: any Decoder) throws {
        let values = try? decoder.container(keyedBy: CodingKeys.self)
        let queued = (try? values?.decodeIfPresent([Entry].self, forKey: .queued)) ?? nil
        songIDs = (queued ?? []).compactMap(\.songID)
    }
}

/// `{removed: [seq]}` from a feedback's answer (`up_next.py:412`).
struct QueueFeedbackAnswer: Decodable {
    let removed: [Int]

    private enum CodingKeys: String, CodingKey { case removed }

    init(from decoder: any Decoder) throws {
        let values = try? decoder.container(keyedBy: CodingKeys.self)
        removed = ((try? values?.decodeIfPresent([Int].self, forKey: .removed)) ?? nil) ?? []
    }
}

/// The server's reason in words (the web's `apiErrorText`,
/// `RoomQueueApi.kt:126`): `detail` from the error body when it is a
/// sentence, `detail.error.message` for a coded one, else the message.
public func queueErrorText(_ error: DomovoiError) -> String {
    if let failure = error.failure, !failure.body.isEmpty,
       let data = failure.body.data(using: .utf8),
       let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
        if let said = object["detail"] as? String { return said }
        if let coded = object["detail"] as? [String: Any] {
            if let message = (coded["error"] as? [String: Any])?["message"] as? String { return message }
            if let flat = try? JSONSerialization.data(withJSONObject: coded, options: [.sortedKeys]),
               let text = String(data: flat, encoding: .utf8) {
                return text
            }
        }
    }
    let message = error.message.trimmingCharacters(in: .whitespacesAndNewlines)
    return message.isEmpty ? "request failed" : message
}
