import Foundation

/// Playlists made from the music files (V058): an `.m3u` / `.m3u8` file, or
/// the songs' own PLAYLIST tags. The files own them, so the server refuses
/// every change with **409** (rename, delete, add, remove, reorder) and the
/// row says `"imported": true` (`web/backend/api/playlists.py`
/// `_refuse_if_imported`).
///
/// `PlaylistReadOnly.kt` (Android c57e255a) as a type. The words are the
/// web's (`music.jsx` PlaylistDrawer / LibraryAddDrawer) and the server's
/// (`domovoi/library_playlists_import.py` READ_ONLY_MESSAGE), and
/// `MusicPlaylistReadOnlyTests` reads both files so they cannot drift.
public enum MusicPlaylistReadOnly {

    /// The sheet's eyebrow word for one (where a household playlist says
    /// "playlist"): `PlaylistReadOnly.kt` IMPORTED_EYEBROW.
    public static let eyebrowWord = "from your music files"

    /// The note under the sheet's buttons: IMPORTED_NOTE.
    public static let note =
        "This playlist comes from your music files (a playlist file or the songs' own tags), "
        + "so it changes when they do. Copy it to change it here."

    /// The button that makes an editable copy: COPY_TO_NEW_PLAYLIST.
    public static let copyButton = "Copy to a new playlist"

    /// What a 409 on a playlist write says when the server sent no reason of
    /// its own: the server's READ_ONLY_MESSAGE, word for word.
    public static let readOnlyText =
        "This playlist comes from your music files, so it changes when they do. "
        + "Copy it to a new playlist to change it in Domovoi."

    /// The words a 409 for a song already in the playlist carries
    /// (`playlists.py` "track N is already in this playlist").
    static let alreadyInMarker = "already in this playlist"

    /// The sheet's title: `virtual · #0`, `from your music files · #9`,
    /// `playlist · #4` (`PlaylistDrawer.kt` DrawerHeader).
    public static func eyebrow(for playlist: Playlist) -> String {
        let word: String
        if playlist.isVirtual {
            word = "virtual"
        } else if playlist.imported {
            word = eyebrowWord
        } else {
            word = "playlist"
        }
        return "\(word) · #\(playlist.id)"
    }

    /// The toast once the copy exists (the web's words).
    public static func copiedText(_ copy: Playlist) -> String {
        "copied to \u{201C}\(copy.name)\u{201D}"
    }

    /// What a failed copy says.
    public static func copyFailed(_ detail: String) -> String {
        "copy playlist failed: \(detail)"
    }

    // MARK: - who may be written to

    /// The add-to-playlist sheet's list: Favorites (the favourite toggle)
    /// and the household's playlists, never one made from the files (the
    /// web's LibraryAddDrawer, `PlaylistReadOnly.kt` addTargets).
    public static func addTargets(_ all: [Playlist]) -> [Playlist] {
        all.filter { !$0.imported }
    }

    /// A picker of playlists to add **many** songs to: household playlists
    /// only, never Favorites or one from the files (`bulkAddTargets`, the
    /// web's LibraryTab `realPlaylists`).
    public static func bulkAddTargets(_ all: [Playlist]) -> [Playlist] {
        all.filter { (playlist: Playlist) -> Bool in playlist.editable }
    }

    // MARK: - a 409, in words

    /// The server's own `detail`, when the refusal carried one with words.
    static func detail(_ error: DomovoiError) -> String? {
        guard let failure = error.failure else { return nil }
        return serverDetail(in: failure.body)
    }

    /// A single add refused because the song is already there (409 "track N
    /// is already in this playlist"): a duplicate, not a failure.
    public static func isAlreadyInPlaylist(_ error: DomovoiError) -> Bool {
        guard error.failure?.status == 409, let said = detail(error) else { return false }
        return said.range(of: alreadyInMarker, options: .caseInsensitive) != nil
    }

    /// Plain words for a 409 from a playlist write: chiefly a playlist the
    /// files own, refused while this phone's copy of the list still showed it
    /// as editable (the flag was stale). The server's own reason, else
    /// ``readOnlyText``. Nil for anything that is not a 409, which the caller
    /// reports as it always has.
    public static func conflictText(_ error: DomovoiError) -> String? {
        guard error.failure?.status == 409 else { return nil }
        return detail(error) ?? readOnlyText
    }

    /// How one song of a bulk add went wrong (`BulkAddFailure`).
    public enum BulkAddFailure: Equatable, Sendable {
        /// Already in the playlist: counted, not reported.
        case duplicate
        /// The playlist refuses adds (made from the files): stop and say why.
        case refused(String)
        /// Anything else: reported, and the next song is tried.
        case other(String)
    }

    /// Sorts a bulk add's failure. Every 409 used to count as "already in",
    /// so a playlist that refuses all adds reported each song as a duplicate.
    public static func bulkAddFailure(_ error: DomovoiError) -> BulkAddFailure {
        if isAlreadyInPlaylist(error) { return .duplicate }
        if let text = conflictText(error) { return .refused(text) }
        return .other(error.message)
    }

    // MARK: - the copy

    /// `POST /api/playlists/{id}/copy`: a new household playlist with the
    /// same songs in the same order, named "<name> (copy)" by the server
    /// (201 with the ``Playlist``).
    public static func copy(client: DomovoiClient, playlistID: Int64) async throws(DomovoiError) -> Playlist {
        try await client.post(MusicPlaylistPath.copy(playlistID), as: Playlist.self)
    }
}

extension MusicPlaylistPath {

    /// `POST /api/playlists/{id}/copy` → **201** with the new ``Playlist``
    /// (V058; `playlists.py` copy_playlist). 400 for Favorites.
    public static func copy(_ id: Int64) -> String { "/api/playlists/\(id)/copy" }
}

extension Playlist {

    /// Renamed, reordered, trimmed of a song or deleted here: neither
    /// Favorites nor a playlist from the files (`PlaylistReadOnly.kt`
    /// `editable`). Taking a song out is the one write Favorites has too
    /// (it unfavourites), which is ``MusicPlaylistDetailModel/mayRemove``.
    public var editable: Bool { !isVirtual && !imported }
}
