import Foundation

/// The tabs Music actually has.
///
/// `MusicScreen.kt:353` lists five — `library`, `player`, `room queue`,
/// `playlists`, `stats` — and this is all five **cases**, of which four are
/// ever offered: the phone's own `player` needs an audio engine, and nothing
/// in this app conforms to ``MusicMorePlayerEngine`` yet. A tab bar is a
/// promise that each tab has something behind it, so the bar iterates
/// ``shown(hasPlayer:)`` rather than ``allCases`` and the enum is where that
/// is recorded rather than an `if tab == 1` somewhere in a view.
///
/// **Never `allCases` in a view.** That is how the `player` tab would come
/// back: `CaseIterable` is kept because the tests enumerate it, and a bar
/// built from it would offer a tab that draws nothing
/// ([[no-misleading-controls]]).
/// Declared in Android's own tab order (`MusicScreen.kt:353`), so that
/// ``shown(hasPlayer:)`` is a filter rather than a second ordering to keep in
/// step.
public enum MusicTab: String, CaseIterable, Sendable, Identifiable {
    case library
    /// This phone's own playback. Offered only with an engine registered.
    case player
    case queue
    case playlists
    case stats

    public var id: String { rawValue }

    /// The tabs to draw, in Android's order (`MusicScreen.kt:353`).
    ///
    /// - Parameter hasPlayer: ``MusicLocalQueueModel/hasEngine``. With no
    ///   engine `player` is **not in the list at all** — not greyed, not
    ///   empty — because a transport row over no engine is a control that
    ///   cannot do what it says.
    public static func shown(hasPlayer: Bool) -> [MusicTab] {
        allCases.filter { $0 != .player || hasPlayer }
    }

    /// The word on the tab, with its count where there is one to show.
    ///
    /// - Parameters:
    ///   - libraryTotal: `stats.total_tracks`, or nil while that read has not
    ///     answered — in which case the tab is just `library`, **not**
    ///     `library · 0`.
    ///   - queueCount: how many entries the open room's queue holds, or nil
    ///     when no queue has answered.
    ///   - playlistCount: how many playlists the house reported, or nil while
    ///     that read is in flight. ``MusicPlaylistsModel/count`` is nil rather
    ///     than zero for exactly this reason.
    ///
    /// Android appends the library count the same way (`:354`) and leaves the
    /// queue tab bare. The count is on the queue tab here because this app's
    /// queue tab is where a person goes to find out whether anything is
    /// queued, and the answer being on the tab saves the trip.
    public func label(
        libraryTotal: Int? = nil,
        queueCount: Int? = nil,
        playlistCount: Int? = nil
    ) -> String {
        switch self {
        case .library:
            guard let libraryTotal else { return MusicStrings.tabLibrary }
            return "\(MusicStrings.tabLibrary) · \(libraryTotal)"
        case .playlists:
            // Zero IS shown here, unlike the queue's: a house with no
            // playlists is a fact worth reading off the bar, and the tab's
            // own empty state says what to do about it.
            guard let playlistCount else { return MusicMoreStrings.tabPlaylists }
            return "\(MusicMoreStrings.tabPlaylists) · \(playlistCount)"
        case .queue:
            guard let queueCount, queueCount > 0 else { return MusicStrings.tabQueue }
            return "\(MusicStrings.tabQueue) · \(queueCount)"
        case .stats:
            return MusicStrings.tabStats
        case .player:
            return MusicMoreStrings.tabPlayer
        }
    }
}
