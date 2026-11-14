package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/*
 * Playlists made from the music files (V058): an .m3u / .m3u8 file, or the
 * songs' own PLAYLIST tags. The files own them, so the server refuses every
 * change with 409 (rename, delete, add, remove, reorder) and the row says
 * `"imported": true`. The web's PlaylistDrawer and LibraryAddDrawer
 * (music.jsx) are the reference; the words below are theirs.
 *
 * Pure Kotlin: PlaylistReadOnlyTest pins every rule.
 */

/** The drawer's eyebrow for one (the web's, where a household playlist says "playlist"). */
internal const val IMPORTED_EYEBROW = "from your music files"

/** The drawer's note under the buttons (the web's words). */
internal const val IMPORTED_NOTE =
    "This playlist comes from your music files (a playlist file or the songs' own tags), " +
        "so it changes when they do. Copy it to change it here."

/** The button that makes an editable copy (the web's words). */
internal const val COPY_TO_NEW_PLAYLIST = "Copy to a new playlist"

/**
 * What a 409 on a read-only playlist says when the server sent no reason
 * of its own: the server's READ_ONLY_MESSAGE
 * (domovoi/library_playlists_import.py), word for word.
 */
internal const val READ_ONLY_TEXT =
    "This playlist comes from your music files, so it changes when they do. " +
        "Copy it to a new playlist to change it in Domovoi."

/** Renamed, reordered, trimmed or deleted here: neither Favorites nor a playlist from the files. */
internal val Playlist.editable: Boolean get() = !isVirtual && !imported

/** The add-to-playlist sheet's list: Favorites (a favourite toggle) and the
 *  household's playlists, never one made from the files (the web's
 *  LibraryAddDrawer). */
internal fun addTargets(all: List<Playlist>): List<Playlist> = all.filter { !it.imported }

/** The Library tab's bulk-add picker: real household playlists only (the
 *  web's LibraryTab `realPlaylists`). */
internal fun bulkAddTargets(all: List<Playlist>): List<Playlist> = all.filter { it.editable }

private fun detailOf(e: ApiException): String? = runCatching {
    ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail") as? JsonPrimitive)
        ?.takeIf { it.isString }?.content
}.getOrNull()?.trim()?.takeIf { it.isNotEmpty() }

/** A single add refused because the song is already there (409 "track N is
 *  already in this playlist"): a duplicate, not a failure. */
internal fun isAlreadyInPlaylist(e: Throwable): Boolean =
    e is ApiException && e.status == 409 &&
        detailOf(e)?.contains("already in this playlist", ignoreCase = true) == true

/**
 * Plain words for a 409 from a playlist write — chiefly a playlist the files
 * own, refused while this phone's copy of the list still showed it as
 * editable (the flag was stale): the server's own reason, else
 * [READ_ONLY_TEXT]. Null for anything that isn't a 409, which the caller
 * reports as it always has.
 */
internal fun playlistConflictText(e: Throwable): String? {
    if (e !is ApiException || e.status != 409) return null
    return detailOf(e) ?: READ_ONLY_TEXT
}

/** How one song of a bulk add went wrong. */
internal sealed interface BulkAddFailure {
    /** Already in the playlist: counted, not reported. */
    data object Duplicate : BulkAddFailure
    /** The playlist refuses adds (made from the files): stop and say why. */
    data class Refused(val text: String) : BulkAddFailure
    /** Anything else: reported, and the next song is tried. */
    data class Other(val error: Throwable) : BulkAddFailure
}

/** Sorts a bulk add's failure. Every 409 used to count as "already in", so a
 *  playlist that refuses all adds reported each song as a duplicate. */
internal fun bulkAddFailure(e: Throwable): BulkAddFailure = when {
    isAlreadyInPlaylist(e) -> BulkAddFailure.Duplicate
    else -> playlistConflictText(e)?.let { BulkAddFailure.Refused(it) } ?: BulkAddFailure.Other(e)
}

/** `POST /api/playlists/{id}/copy`: a new household playlist with the same
 *  songs in the same order, named "<name> (copy)" by the server (201). */
internal suspend fun copyPlaylist(api: ApiClient, playlistId: Long): Playlist =
    api.post("/api/playlists/$playlistId/copy").decode<Playlist>()

/** The toast once the copy exists (the web's words). */
internal fun copiedText(copy: Playlist): String = "copied to “${copy.name}”"
