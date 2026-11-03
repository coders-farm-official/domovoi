import Foundation
import Observation

/// Where a room's lyric timing offset is kept.
///
/// Android keeps it in `Prefs` under `lyrics_room_nudge`
/// (`player/Lyrics.kt:203`). On iOS it is a **per-viewer convenience** —
/// someone's correction for their own kitchen speaker's buffer, on this phone
/// — so `UserDefaults` is the right home for it and the wrong home for
/// anything else.
///
/// Every read is defaulted and nothing throws: `UserDefaults` can come back
/// empty, and a nudge nobody can read is a room at no offset, which is the
/// state everything else already handles.
///
/// The JSON encode and decode are ``MusicLyricsNudge``'s, not `Codable`'s,
/// because the clamping is the point: a value written by another client, or
/// by hand, cannot put a room ten seconds out.
@MainActor
@Observable
public final class MusicLyricsNudgeStore {

    /// The `UserDefaults` key. Android's prefs key, so the two phones' notes
    /// read the same.
    public static let defaultsKey = "lyrics_room_nudge"

    /// Every room with an offset. A room not in here is at zero.
    public private(set) var offsets: [String: Int64]

    private let read: @Sendable () -> String?
    private let write: @Sendable (String) -> Void

    /// For a test: the whole store over two closures, no `UserDefaults` at
    /// all.
    public init(
        read: @Sendable @escaping () -> String?,
        write: @Sendable @escaping (String) -> Void
    ) {
        self.read = read
        self.write = write
        self.offsets = MusicLyricsNudge.decode(read())
    }

    /// The store the app uses.
    public convenience init(defaults: UserDefaults = .standard) {
        let key = Self.defaultsKey
        self.init(
            read: { defaults.string(forKey: key) },
            write: { defaults.set($0, forKey: key) }
        )
    }

    /// `room`'s offset in milliseconds; zero for a room that has none.
    public func offset(_ room: String) -> Int64 { offsets[room] ?? 0 }

    /// The chip's label for `room` — `"timing"`, or `"timing +0.75 s"`.
    public func label(_ room: String) -> String {
        MusicLyricsNudge.label(offset(room))
    }

    /// Set `room` to `ms`, clamped. Zero removes the room rather than storing
    /// a zero, so the stored map is only the rooms somebody has corrected.
    public func set(_ room: String, ms: Int64) {
        guard !room.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        offsets = MusicLyricsNudge.with(offsets, room: room, ms: ms)
        write(MusicLyricsNudge.encode(offsets))
    }

    /// One tap of the `+¼ s` chip.
    public func later(_ room: String) {
        set(room, ms: offset(room) + MusicLyricsNudge.stepMs)
    }

    /// One tap of the `−¼ s` chip.
    public func earlier(_ room: String) {
        set(room, ms: offset(room) - MusicLyricsNudge.stepMs)
    }

    public func reset(_ room: String) { set(room, ms: 0) }
}
