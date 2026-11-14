import Foundation

/// The Music page's rooms strip, folded away until asked for (owner
/// 2026-11-11, mobile hand-off §2): a simple page by default.
///
/// Android `RoomsToggle.kt` (d19efd9c), whose words are the web's
/// (`web/static/music.jsx`, the rooms toggle): `Show rooms`,
/// `Show rooms (2 playing)`, `Hide rooms`.
public enum MusicRoomsToggle {

    /// The link's words (`RoomsToggle.kt:28-32`). The count is the one the
    /// now-playing read **already on screen** gives — no request is made for
    /// it — and it is left off when nothing is playing.
    public static func label(open: Bool, playingCount: Int) -> String {
        if open { return MusicRoomsStrings.hide }
        if playingCount > 0 { return MusicRoomsStrings.showPlaying(playingCount) }
        return MusicRoomsStrings.show
    }

    /// What VoiceOver says after the label: the link is a button that is
    /// expanded or collapsed (`RoomsToggle.kt:77`'s `stateDescription`).
    public static func accessibilityValue(open: Bool) -> String {
        open ? MusicRoomsStrings.expanded : MusicRoomsStrings.collapsed
    }

    /// What the strip's place on the page holds once it is open.
    public enum Section: Equatable, Sendable {
        /// Folded away: nothing below the link.
        case hidden
        /// The first now-playing read has not answered (`MusicScreen.kt:428`).
        case loading
        /// The read failed and there is nothing to show from before it — said,
        /// with a retry, rather than drawn as an empty house
        /// (`MusicScreen.kt:430-437`, new in d19efd9c).
        case unavailable(message: String)
        /// The rooms the read gave — none, or some. None draws nothing, as
        /// ``MusicNowPlayingStrip`` always has: an empty list cannot tell an
        /// unprovisioned house from one whose satellites have not answered.
        case rooms
    }

    /// The strip's state, from the screen's own now-playing read.
    ///
    /// `MusicScreen.kt:425-438`: loading while there is no data and the read
    /// is in flight; **unavailable** when the list is empty and the read
    /// failed; otherwise the strip. A failed refresh over rooms already shown
    /// keeps them (the read keeps its data on error, ``ApiRead/isStale``).
    public static func section(
        open: Bool,
        hasData: Bool,
        loading: Bool,
        rooms: [NowPlayingRoom],
        error: String?
    ) -> Section {
        guard open else { return .hidden }
        if !hasData && loading { return .loading }
        if rooms.isEmpty, let error { return .unavailable(message: MusicRoomsStrings.unavailableMessage(error)) }
        return .rooms
    }
}

/// Whether the rooms strip is open, kept on this phone.
///
/// The web keeps it per browser under `domovoi.music.roomsOpen`; Android in
/// `SharedPreferences("music_ui")`. Here `UserDefaults`, under the web's key.
/// **Closed until someone opens it** — for everyone, not only new installs.
public struct MusicRoomsOpenStore {

    public static let key = "domovoi.music.roomsOpen"

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        #if DEBUG
        // `-domovoi-music-rooms-forget`: a UI case that has to start from a
        // phone that never chose (``Self/key`` absent) forgets the choice,
        // **once** per process — so the case can still open the strip, leave
        // the screen and come back within the same launch. DEBUG only, like
        // the other test launch arguments (``AuditStrip``).
        if !Self.forgotThisLaunch,
           ProcessInfo.processInfo.arguments.contains("-domovoi-music-rooms-forget") {
            Self.forgotThisLaunch = true
            defaults.removeObject(forKey: Self.key)
        }
        #endif
    }

    #if DEBUG
    /// `nonisolated(unsafe)`: read and written only on the main thread — a
    /// view's initialiser, and the package's `@MainActor` tests.
    nonisolated(unsafe) private static var forgotThisLaunch = false
    #endif

    /// `bool(forKey:)` once the key exists, so a value given on the launch
    /// line (`-domovoi.music.roomsOpen YES`, a string in the argument domain)
    /// reads the same as one this app wrote.
    public var open: Bool {
        get { defaults.object(forKey: Self.key) == nil ? false : defaults.bool(forKey: Self.key) }
        nonmutating set { defaults.set(newValue, forKey: Self.key) }
    }
}

/// The toggle's words. The web's (`music.jsx`) and Android's
/// (`RoomsToggle.kt`), letter for letter.
public enum MusicRoomsStrings {
    public static let show = "Show rooms"
    public static let hide = "Hide rooms"
    public static func showPlaying(_ count: Int) -> String { "Show rooms (\(count) playing)" }
    /// `stateDescription` (`RoomsToggle.kt:77`).
    public static let expanded = "expanded"
    public static let collapsed = "collapsed"
    /// `ErrorState(title:)` and its message (`MusicScreen.kt:432-436`).
    public static let unavailableTitle = "rooms unavailable"
    public static func unavailableMessage(_ error: String) -> String { "couldn't load now-playing: \(error)" }
    /// `ErrorState`'s button (`Kit.kt:296`).
    public static let retry = "retry"
}
