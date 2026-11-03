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

    /// Where this store's bytes live.
    ///
    /// A key of its own under the app's prefix, as ``ChatPinBook``'s is, and
    /// for the same reason its own doc comment gives: a tap on a nudge chip
    /// must not rewrite the shell's blob, because a half-applied write there
    /// is how an app comes to trust a server there is no row for.
    public static let defaultsKey = "domovoi.music.lyricsNudge"

    /// Every room with an offset. A room not in here is at zero.
    public private(set) var offsets: [String: Int64]

    private let place: any SavedState

    /// - Parameter place: ``UserDefaultsSavedState`` in the app, a
    ///   ``SavedStateBox`` in a test. The same seam ``ChatPinBook`` uses —
    ///   a model reaching `UserDefaults.standard` itself is both a second
    ///   mechanism and a capture the compiler refuses in a `@Sendable`
    ///   closure, `UserDefaults` not being `Sendable`.
    public init(
        place: any SavedState = UserDefaultsSavedState(
            key: MusicLyricsNudgeStore.defaultsKey
        )
    ) {
        self.place = place
        self.offsets = MusicLyricsNudge.decode(
            place.read().flatMap { String(data: $0, encoding: .utf8) }
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
        place.write(Data(MusicLyricsNudge.encode(offsets).utf8))
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
