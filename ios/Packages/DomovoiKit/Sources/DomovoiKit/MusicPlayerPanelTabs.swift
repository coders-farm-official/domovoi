import Foundation

/// The full player's panel tabs: Lyrics · Queue · Hide.
///
/// `android/.../ui/shell/player/PlayerSheetTabs.kt` (3cdd261b), the web's
/// `PlayerSheet`. **Android draws them in the mini player's sheet; this app
/// has no mini player**, and its Music `player` tab is the counterpart — the
/// one surface on the phone that is "the full player": transport, the words
/// and the queue. So the tabs are there (``MusicPlayerTabView``), in place of
/// the tab's fold-open lyrics section and the queue under it.
///
/// The web has an Equalizer tab too; the phone has no equalizer, so it has
/// none — the same call Android made ([[no-misleading-controls]]).
public enum MusicPlayerPanelTab: String, CaseIterable, Sendable {
    case lyrics
    case queue
    case hide

    public var label: String {
        switch self {
        case .lyrics: return "lyrics"
        case .queue: return "queue"
        case .hide: return "hide"
        }
    }

    /// The tab's accessibility identifier, so a UI case can reach it without
    /// colliding with the lyrics panel's own words or the queue's header.
    public var identifier: String { "music-player-panel-\(rawValue)" }
}

/// The tab picked last, kept for this run of the app — Android's
/// `SheetTabMemory`; the web keeps it for the browser tab's session.
@MainActor
public enum MusicPlayerPanelMemory {
    public static var picked: MusicPlayerPanelTab?
}

/// `PlayerSheetTabs.kt`'s rules, pure.
public enum MusicPlayerPanel {

    /// The tabs offered: Lyrics only for a song that can have them — a library
    /// track whose words this viewer may read.
    public static func tabs(lyricsTab: Bool) -> [MusicPlayerPanelTab] {
        lyricsTab ? [.lyrics, .queue, .hide] : [.queue, .hide]
    }

    /// Which panel shows (the web's `playerSheetTab`, minus the Equalizer).
    ///
    /// - `here`: the tab picked for THIS song. It stays — even Lyrics on a
    ///   song with none found: the person asked to look.
    /// - else the session's `picked` while it still applies: Hide and Queue
    ///   always do, Lyrics while the song has some (or they are still
    ///   loading);
    /// - else the default: Lyrics when the song has them, otherwise the Queue.
    public static func choose(
        here: MusicPlayerPanelTab?,
        picked: MusicPlayerPanelTab?,
        lyricsTab: Bool,
        lyricsFound: Bool,
        lyricsLoading: Bool
    ) -> MusicPlayerPanelTab {
        func usable(_ tab: MusicPlayerPanelTab) -> Bool { tab != .lyrics || lyricsTab }
        if let here, usable(here) { return here }
        let withLyrics = lyricsTab && (lyricsFound || lyricsLoading)
        if picked == .hide || picked == .queue, let picked { return picked }
        if withLyrics { return .lyrics }
        return .queue
    }

    /// Whether an answer has words to show: timed lines or plain text
    /// (`lyricsFound`). Instrumental, "still looking" and "none" are not.
    public static func lyricsFound(_ state: MusicLyricsState?) -> Bool {
        switch state {
        case .some(.timed), .some(.plain): return true
        default: return false
        }
    }

    /// The song a tab was picked for: the room's track while casting, else
    /// this phone's entry (`PlayerQueueSheet.kt:114`).
    public static func songKey(castingTo: String?, lyricsTrackID: Int64?, currentUID: String?) -> String {
        if castingTo != nil {
            return "room:\(lyricsTrackID.map(String.init) ?? ""):\(currentUID ?? "")"
        }
        return currentUID ?? ""
    }
}
