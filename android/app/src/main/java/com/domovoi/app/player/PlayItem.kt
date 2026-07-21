package com.domovoi.app.player

import kotlinx.serialization.Serializable

enum class PlayKind { Library, Radio, Podcast, Audiobook }

@Serializable
data class Chapter(val title: String, val startSec: Double)

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
) {
    companion object {
        fun fromTrack(id: Long, title: String, artist: String?, album: String?, durationSec: Double?) =
            PlayItem(
                uid = "lib-$id", kind = PlayKind.Library, id = id,
                title = title, artist = artist, album = album,
                src = "/api/music/library/$id/audio",
                coverPath = "/api/music/library/$id/cover",
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

        fun fromEpisode(id: Long, title: String, show: String?, durationSec: Double?, artwork: String?, chapters: List<Chapter>) =
            PlayItem(
                uid = "pod-$id", kind = PlayKind.Podcast, id = id,
                title = title, artist = show,
                src = "/api/podcasts/episodes/$id/audio",
                coverPath = artwork,
                durationSec = durationSec, chapters = chapters,
            )

        fun fromBook(id: Long, title: String, author: String?, durationSec: Double?, artwork: String?, chapters: List<Chapter>) =
            PlayItem(
                uid = "book-$id", kind = PlayKind.Audiobook, id = id,
                title = title, artist = author,
                src = "/api/audiobooks/$id/audio",
                coverPath = artwork,
                durationSec = durationSec, chapters = chapters,
            )
    }

    val isSpoken: Boolean get() = kind == PlayKind.Podcast || kind == PlayKind.Audiobook
}
