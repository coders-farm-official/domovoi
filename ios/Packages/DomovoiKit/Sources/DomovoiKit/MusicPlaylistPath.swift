import Foundation

/// Every path the playlist routes live at, in one place.
///
/// ``MusicQueuePath``'s shape and its reason: a path written at a call site is
/// a path a test cannot state. The percent-encoding is
/// ``MusicQueuePath/escaped(_:)``, reused rather than redeclared — the
/// allowed set there is deliberately narrower than `.urlPathAllowed`, which
/// admits `/`, `;` and `&`.
///
/// Verified against `web/backend/api/playlists.py` and
/// `web/backend/api/music.py`, 2026-11-02.
public enum MusicPlaylistPath {

    /// The Favorites view's id, everywhere
    /// (`playlists.py` `FAVORITES_VIRTUAL_ID`).
    ///
    /// It is **not a playlist**: every write route refuses id 0, and the one
    /// for adding a track refuses it with a 400 whose detail names the route
    /// to use instead. See ``MusicAddToPlaylistModel/toggle(_:)``.
    public static let favoritesID: Int64 = 0

    /// `GET /api/playlists` → a **bare array** of ``Playlist``, Favorites
    /// pinned on top by the server (`playlists.py:82`). An open read.
    public static let all = "/api/playlists"

    /// `POST /api/playlists` → **201** with the created playlist.
    public static let create = "/api/playlists"

    /// `PATCH /api/playlists/{id}`, and `DELETE` for the whole playlist.
    public static func one(_ id: Int64) -> String { "/api/playlists/\(id)" }

    /// `GET /api/playlists/{id}/tracks` → a **bare array** of ``LibraryTrack``
    /// in playback order (`playlists.py:122`). An open read.
    public static func tracks(_ id: Int64) -> String { "/api/playlists/\(id)/tracks" }

    /// `POST /api/playlists/{id}/tracks` → **204**; **409** for a track
    /// already in it.
    public static func addTrack(_ id: Int64) -> String { "/api/playlists/\(id)/tracks" }

    /// `DELETE /api/playlists/{id}/tracks/{track_id}` → **204**.
    public static func removeTrack(_ id: Int64, trackID: Int64) -> String {
        "/api/playlists/\(id)/tracks/\(trackID)"
    }

    /// `PATCH /api/playlists/{id}/order` → **204**, and the submitted set
    /// **must equal** the playlist's current set (`playlists.py:318`).
    public static func order(_ id: Int64) -> String { "/api/playlists/\(id)/order" }

    /// `GET /api/music/library/{id}/playlists` → a **bare array** of
    /// ``Playlist``: Favorites (id 0, virtual) first when the row is
    /// favourited, then the real ones by lower-cased name
    /// (`music.py:341`). An open read.
    public static func memberships(trackID: Int64) -> String {
        "/api/music/library/\(trackID)/playlists"
    }

    /// `POST /api/music/play-playlist` (`music.py:1012`).
    public static let playPlaylist = "/api/music/play-playlist"

    /// `POST /api/music/play-tracks` — casting this phone's queue
    /// (`music.py:1726`).
    public static let playTracks = "/api/music/play-tracks"

    /// `PATCH /api/music/library/{id}` — the route Favorites goes through,
    /// because Favorites is not a playlist.
    public static func libraryRow(_ trackID: Int64) -> String {
        "/api/music/library/\(trackID)"
    }

    /// `GET /api/music/library/{track_id}/lyrics` (`lyrics.py:323`).
    /// Household tier: a bare request is a 401.
    public static func trackLyrics(_ trackID: Int64) -> String {
        "/api/music/library/\(trackID)/lyrics"
    }

    /// `GET /api/music/now-playing/{room_id}/lyrics` (`lyrics.py:339`) — the
    /// room's song, where it is, and that song's lyrics in one call. **404**
    /// for a room that is not provisioned.
    public static func roomLyrics(room: String) -> String {
        "/api/music/now-playing/\(MusicQueuePath.escaped(room))/lyrics"
    }
}
