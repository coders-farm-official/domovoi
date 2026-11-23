import Foundation
import Observation

/// The newest cover versions the server has announced this run (V059).
///
/// A server from V059 on numbers each track's cover (`cover_v`, moved on
/// every change) on its track and queue rows, and hands out the cover path
/// with it as `?v=`. The live `library.cover.changed` event carries the 50
/// most recent changes, `{"tracks": [{"id", "cover_v", "cover_url"}]}`
/// (`web/backend/realtime.py` `_snapshot_library_covers`), repeated whole
/// each time. This remembers the highest version heard per track, so that
/// a row read **before** the change, a room card (`/now-playing` carries no
/// version), the player and the lock screen all move to the new picture —
/// the web's `coverVersions` and Android's `CoverArt.versions`.
///
/// Keyed by the server it was heard from: another server's track ids are
/// other songs, so a change of server starts it empty.
@MainActor
@Observable
public final class MusicCoverVersions {

    /// The live event that names tracks whose cover just changed.
    public static let event = "library.cover.changed"

    /// The one the app uses.
    public static let shared = MusicCoverVersions()

    public private(set) var server = ""
    public private(set) var byTrack: [Int64: Int] = [:]

    public init() {}

    /// The newest version this run has heard for `trackID`, or nil.
    public func known(_ trackID: Int64) -> Int? { byTrack[trackID] }

    /// The version to draw `trackID`'s cover at: the larger of the row's own
    /// (`cover_v`, nil from an older server) and the newest heard, or nil
    /// when there is neither — then the cover is the bare route, checked by
    /// its ETag (the web's `libraryCoverUrl`).
    public func version(_ trackID: Int64, row: Int?) -> Int? {
        let heard = byTrack[trackID]
        guard row != nil || heard != nil else { return nil }
        return max(row ?? 0, heard ?? 0)
    }

    /// The app now talks to `address`: another server's versions are gone.
    public func serverIs(_ address: String) {
        guard address != server else { return }
        server = address
        byTrack = [:]
    }

    /// Forget every version heard: the app is leaving this server.
    public func forget() {
        server = ""
        byTrack = [:]
    }

    /// A `library.cover.changed` frame heard while the app talks to
    /// `server`. Returns the tracks whose known version moved up; a version
    /// already known (the event repeats the 50 newest every time) changes
    /// nothing, and so does any other frame.
    @discardableResult
    public func received(_ frame: StateEvent, server address: String) -> Set<Int64> {
        guard frame.type == Self.event else { return [] }
        serverIs(address)
        var moved: Set<Int64> = []
        var next = byTrack
        for (id, version) in Self.parse(frame.frame) {
            if let had = next[id], had >= version { continue }
            next[id] = version
            moved.insert(id)
        }
        if !moved.isEmpty { byTrack = next }
        return moved
    }

    /// The frame's `data.tracks` as track id to `cover_v`; anything
    /// malformed is left out.
    nonisolated static func parse(_ frame: String) -> [Int64: Int] {
        guard let bytes = frame.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
              let data = object["data"] as? [String: Any],
              let tracks = data["tracks"] as? [Any] else { return [:] }
        var out: [Int64: Int] = [:]
        for entry in tracks {
            guard let row = entry as? [String: Any],
                  let id = (row["id"] as? NSNumber)?.int64Value,
                  let version = (row["cover_v"] as? NSNumber)?.intValue else { continue }
            out[id] = max(version, out[id] ?? 0)
        }
        return out
    }

    /// `path` (a library cover path, bare or versioned) at the newest heard
    /// version when that is newer than the one it carries; anything else
    /// comes back as it is. For the player's item, whose path was built when
    /// it was queued.
    public func versioned(_ path: String?) -> String? {
        guard let path, let parsed = PlayerArtwork.coverPathPieces(path),
              let heard = byTrack[parsed.trackID] else { return path }
        if let had = parsed.version, had >= heard { return path }
        return PlayerArtwork.libraryPath(trackID: parsed.trackID, coverV: heard, size: parsed.size)
    }
}
