import Foundation

/// What the docked mini player draws: the bar above the phone bar (and above
/// the local shell's tabs) that every big player app has — `MiniPlayer.kt` on
/// Android, `MiniPlayer` on the web.
///
/// Before 2026-11-20 this app had no docked bar: Music's `player` tab was the
/// only place a phone's queue could be seen or stopped, so something playing
/// on the phone, or in a room it cast to, could be paused only from the Lock
/// Screen or by finding that tab (owner, 2026-11-20: "we also will need a
/// mini player").
///
/// A plain value over plain inputs, so the rules are a test's and not a
/// Simulator's. The view reads ``MusicLocalQueueModel`` — the same model
/// Music's `player` tab reads — so the bar and the tab cannot disagree about
/// what is playing.
public struct MiniPlayerFace: Equatable, Sendable {

    /// The title the bar shows: the room's while casting, this phone's
    /// current entry's otherwise, "—" when neither has one (Android's).
    public let title: String

    /// The artist under it, on this phone only. While casting the line under
    /// the title is the "casting to" pill instead (``castingTo``).
    public let artist: String?

    /// The room this phone is casting to, or nil.
    public let castingTo: String?

    /// Play or pause on the one toggle: the room's state while casting.
    public let isPlaying: Bool

    /// The hairline along the top, 0…1. Zero when the length is unknown —
    /// a radio stream, a room that has not said.
    public let progress: Double

    /// The library track whose cover is drawn, or nil. **None while
    /// casting**, for `MusicPlayerTabView`'s reason: the room reports a title,
    /// and a picture from this phone's queue would be of something else.
    public let coverTrackID: Int64?
}

/// The bar's face, or nil when there is nothing to show — no entry on this
/// phone and no room being cast to (`MiniPlayer.kt`: "Collapses to nothing
/// when the queue is empty and no room is being remote-controlled").
///
/// A `remote` reading **for another room** is not this cast's: a reading that
/// landed after the person moved the cast would otherwise put one room's song
/// under another room's name (the read-after-await class this app's iOS
/// track keeps finding). It is treated as no reading.
public func miniPlayerFace(
    current: MusicPlayerItem?,
    castingTo: String?,
    remote: MusicRemoteNowPlaying?,
    isPlaying: Bool,
    positionSec: Double
) -> MiniPlayerFace? {
    if let room = castingTo {
        let reading = remote?.roomID == room ? remote : nil
        let duration = reading?.durationSec ?? 0
        return MiniPlayerFace(
            title: nonEmpty(reading?.title) ?? MiniPlayerStrings.noTitle,
            artist: nil,
            castingTo: room,
            isPlaying: reading?.isPlaying ?? false,
            progress: fraction(reading?.elapsedSec ?? 0, of: duration),
            coverTrackID: nil
        )
    }
    guard let current else { return nil }
    return MiniPlayerFace(
        title: nonEmpty(current.title) ?? MiniPlayerStrings.noTitle,
        artist: nonEmpty(current.artist),
        castingTo: nil,
        isPlaying: isPlaying,
        progress: fraction(positionSec, of: current.durationSec ?? 0),
        coverTrackID: current.coverTrackID
    )
}

private func nonEmpty(_ s: String?) -> String? {
    guard let s, !s.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
    return s
}

private func fraction(_ at: Double, of length: Double) -> Double {
    guard length > 0, at.isFinite, length.isFinite else { return 0 }
    return min(max(at / length, 0), 1)
}

/// The mini player's words. The controls' own labels are the player tab's
/// (``MusicTransportAction``, ``stopAndCloseLabel(_:)``), so a person who
/// learns them in one place hears the same words in the other.
public enum MiniPlayerStrings {
    /// `MiniPlayer.kt`'s fallback title.
    public static let noTitle = "—"
    /// The bar's tap. iOS-only: Android's row has no label of its own.
    public static let openHint = "Opens the player"
    /// The sheet the bar opens. iOS-only: Android's sheet has no title.
    public static let sheetTitle = "Now playing"
    public static let done = "Done"
}
