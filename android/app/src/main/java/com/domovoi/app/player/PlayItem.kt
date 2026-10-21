package com.domovoi.app.player

import kotlinx.serialization.Serializable

enum class PlayKind { Library, Radio, Podcast, Audiobook, Device }

@Serializable
data class Chapter(val title: String, val startSec: Double)

/**
 * One file of a book that is several (a folder book: one file per chapter).
 * [src] is a server path or a file:// URI, like [PlayItem.src]. The player
 * joins the parts into ONE item ([BookMediaSources]), so the book has one
 * position, one seek bar and its chapters across the files.
 */
data class MediaPart(val src: String, val durationSec: Double?)

/**
 * Generic queue item — mirrors the web player's item shape (player.jsx):
 * library tracks, live radio, podcast episodes and audiobooks all flow
 * through the one queue. `src`/`coverPath` are server-relative paths.
 */
data class PlayItem(
    val uid: String,
    val kind: PlayKind,
    val id: Long,                    // track/station/episode/book id
    val title: String,
    val artist: String? = null,
    val album: String? = null,
    val src: String,                 // e.g. /api/music/library/12/audio
    val coverPath: String? = null,   // e.g. /api/music/library/12/cover
    val durationSec: Double? = null,
    val seekable: Boolean = true,
    val chapters: List<Chapter> = emptyList(),
    /** A book of several files: every file in order ([src] is the first). */
    val parts: List<MediaPart> = emptyList(),
    /** A book saved on this phone (audiobooks/BookStore.kt): its folder.
     *  Its files belong to no server, so it plays on whatever the app is
     *  connected to, or nothing. */
    val savedKey: String? = null,
    /** The server a saved book came from: where its place goes. */
    val origin: ServerScope? = null,
) {
    companion object {
        fun fromTrack(id: Long, title: String, artist: String?, album: String?, durationSec: Double?) =
            PlayItem(
                uid = "lib-$id", kind = PlayKind.Library, id = id,
                title = title, artist = artist, album = album,
                src = "/api/music/library/$id/audio",
                coverPath = CoverArt.libraryPath(id),
                durationSec = durationSec,
            )

        // Radio streams come from the radio plugin's router (design §9.1) —
        // this item kind is only reachable when the "stations" capability
        // is present, i.e. the plugin is installed.
        fun fromStation(id: Long, name: String) =
            PlayItem(
                uid = "radio-$id", kind = PlayKind.Radio, id = id,
                title = name, artist = "live radio",
                src = "/api/plugins/radio/stations/$id/stream",
                seekable = false,
            )

        // `artwork` is the server path of the show's stored artwork; a
        // remote URL is dropped (PodcastArtwork), so the media session never
        // fetches a publisher's image.
        fun fromEpisode(id: Long, title: String, show: String?, durationSec: Double?, artwork: String?, chapters: List<Chapter>) =
            PlayItem(
                uid = "pod-$id", kind = PlayKind.Podcast, id = id,
                title = title, artist = show,
                src = "/api/podcasts/episodes/$id/audio",
                coverPath = PodcastArtwork.path(artwork),
                durationSec = durationSec, chapters = chapters,
            )

        // On-device media (offline/local mode): `src`/`coverPath` are
        // content:// URIs from MediaStore, played directly — no server.
        fun fromDeviceAudio(id: Long, title: String, artist: String?, album: String?, durationSec: Double?, uri: String, artUri: String?) =
            PlayItem(
                uid = "dev-$id", kind = PlayKind.Device, id = id,
                title = title, artist = artist, album = album,
                src = uri, coverPath = artUri,
                durationSec = durationSec,
            )

        /** A book streamed from the server. A folder book passes its
         *  [parts] (`/api/audiobooks/{id}/audio?file=...` each). */
        fun fromBook(
            id: Long,
            title: String,
            author: String?,
            durationSec: Double?,
            artwork: String?,
            chapters: List<Chapter>,
            parts: List<MediaPart> = emptyList(),
        ) =
            PlayItem(
                uid = "book-$id", kind = PlayKind.Audiobook, id = id,
                title = title, artist = author,
                src = parts.firstOrNull()?.src ?: "/api/audiobooks/$id/audio",
                coverPath = artwork,
                durationSec = durationSec, chapters = chapters,
                parts = parts.takeIf { it.size > 1 }.orEmpty(),
            )
    }

    val isSpoken: Boolean get() = kind == PlayKind.Podcast || kind == PlayKind.Audiobook
}
