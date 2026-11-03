import Foundation
import Synchronization

/// Where a queue item's picture comes from, and what is never fetched.
///
/// `android/.../player/CoverArt.kt:1-53` and `player/PodcastArtwork.kt:1-26`,
/// merged into one file because they are one rule read from two routes.
///
/// `GET /api/music/library/{id}/cover` (`web/backend/api/music.py:1680-1724`)
/// answers with the picture built into the track's file, else its album
/// folder's cover image, and **404 when there is neither**. The route is
/// **open** — its own docstring says why: "an `<img>` tag and the phone's lock
/// screen can't send a credential" — so unlike `VideoStream.swift` nothing
/// here puts a token in a query string.
///
/// ## What is not, and why there is no control for it
///
/// A podcast's artwork is loaded **only** from a `/api/…` path. The server
/// fetches each show's artwork itself and serves it, so the `artwork` field
/// the API returns is a server path or nil, never the publisher's URL; an
/// absolute `http(s)` URL from an older server, or a protocol-relative
/// `//host/…`, draws the placeholder (`PodcastArtwork.kt:20`). The phone never
/// contacts a publisher on its own, and there is no setting to let it.
public enum PlayerArtwork {

    /// The server-relative path of a library track's cover.
    public static func libraryPath(trackID: Int64) -> String {
        "/api/music/library/\(trackID)/cover"
    }

    /// What to hand an image loader for a cover path: a server path resolved
    /// against the active server; an on-device file URL or a full `http(s)`
    /// URL as it is; nil for none.
    ///
    /// `CoverArt.kt:30`. Prefixing a phone song's own URI with the server —
    /// which the Android mini player and queue sheet both used to do — turns
    /// its art into a dead URL, so the leading `/` is the test.
    public static func model(coverPath: String?, absolute: (String) -> String) -> String? {
        guard let coverPath, !coverPath.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return coverPath.hasPrefix("/") ? absolute(coverPath) : coverPath
    }

    /// The server path to load for a podcast artwork value, or nil
    /// (`PodcastArtwork.kt:19`).
    public static func podcastPath(_ url: String?) -> String? {
        guard let trimmed = url?.trimmingCharacters(in: .whitespaces) else { return nil }
        return trimmed.hasPrefix("/api/") ? trimmed : nil
    }

    /// `PodcastArtwork.kt:25`.
    public static func podcastModel(_ url: String?, absolute: (String) -> String) -> String? {
        podcastPath(url).map(absolute)
    }

    // MARK: - the miss memo

    /// Covers the server said it does not have, this run.
    ///
    /// An image loader keeps no record of a failure, so without this every row
    /// scrolled back into view asks for the same missing picture again
    /// (`CoverArt.kt:38`). A `Mutex` rather than a concurrent set because this
    /// is read from the main actor by rows and written from whatever task the
    /// load failed on, and `AppContainer.swift:3` already imports
    /// `Synchronization` for exactly this shape.
    private static let misses = Mutex<Set<String>>([])

    /// Whether `url` already came back "no cover" this run.
    public static func isMissing(_ url: String) -> Bool {
        misses.withLock { $0.contains(url) }
    }

    /// A load of `url` failed with HTTP `status` (nil: no answer at all).
    ///
    /// **Only a 404** — "this track has no cover" — is remembered; a network
    /// blip or a server error is tried again next time the cover is drawn
    /// (`CoverArt.kt:50`). Remembering a 500 would hide a cover for the rest
    /// of the run because the house was restarting.
    public static func noteFailure(_ url: String, status: Int?) {
        guard status == 404 else { return }
        misses.withLock { $0.insert(url) }
    }

    /// Forget every remembered miss (tests, and a server change).
    public static func forgetMisses() {
        misses.withLock { $0.removeAll() }
    }
}
