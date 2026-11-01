import Foundation

/// The tabs Music actually has.
///
/// `MusicScreen.kt:353` lists five — `library`, `player`, `room queue`,
/// `playlists`, `stats` — and this is **three of them**, because the other
/// two are not built: the player is this phone's own playback and the
/// playlists tab is its own batch. A tab bar is a promise that each tab has
/// something behind it, so the cases here are the tabs with something behind
/// them, and the enum is where that is recorded rather than a `if tab == 1`
/// somewhere in a view.
///
/// Three is also the number at which a tab bar starts earning its place. The
/// first Music batch had one tab's worth of screen and drew no bar at all,
/// which was right then and would be wrong now.
public enum MusicTab: String, CaseIterable, Sendable, Identifiable {
    case library
    case queue
    case stats

    public var id: String { rawValue }

    /// The word on the tab, with its count where there is one to show.
    ///
    /// - Parameters:
    ///   - libraryTotal: `stats.total_tracks`, or nil while that read has not
    ///     answered — in which case the tab is just `library`, **not**
    ///     `library · 0`.
    ///   - queueCount: how many entries the open room's queue holds, or nil
    ///     when no queue has answered.
    ///
    /// Android appends the library count the same way (`:354`) and leaves the
    /// queue tab bare. The count is on the queue tab here because this app's
    /// queue tab is where a person goes to find out whether anything is
    /// queued, and the answer being on the tab saves the trip.
    public func label(libraryTotal: Int? = nil, queueCount: Int? = nil) -> String {
        switch self {
        case .library:
            guard let libraryTotal else { return MusicStrings.tabLibrary }
            return "\(MusicStrings.tabLibrary) · \(libraryTotal)"
        case .queue:
            guard let queueCount, queueCount > 0 else { return MusicStrings.tabQueue }
            return "\(MusicStrings.tabQueue) · \(queueCount)"
        case .stats:
            return MusicStrings.tabStats
        }
    }
}
