package com.domovoi.app.ui.screens.music

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Shapes returned by the web backend — mirror web/static/music.jsx usage.
 *  Everything nullable-with-defaults: the backend evolves. */

@Serializable
data class NowPlayingSong(
    val title: String? = null,
    val artist: String? = null,
    val file: String? = null,
    @SerialName("duration_sec") val durationSec: Double? = null,
)

@Serializable
data class NowPlayingRoom(
    @SerialName("room_id") val roomId: String = "",
    val state: String? = null,
    val song: NowPlayingSong? = null,
    @SerialName("elapsed_sec") val elapsedSec: Double? = null,
    val favorited: Boolean = false,
    // Generic now-playing provenance (design §4.7/§10.2): `source` is the
    // registered source slug (library / playlist / a plugin's slug) and
    // `source_url` is an optional external link the source's matcher
    // supplied — rendered as a provider-agnostic "open externally" pill.
    val source: String? = null,
    @SerialName("source_url") val sourceUrl: String? = null,
)

@Serializable
data class LibraryTrack(
    val id: Long = 0,
    val title: String? = null,
    val artist: String? = null,
    val album: String? = null,
    @SerialName("duration_sec") val durationSec: Double? = null,
    val source: String? = null,
    @SerialName("source_id") val sourceId: String? = null,
    @SerialName("added_at") val addedAt: String? = null,
    @SerialName("added_via") val addedVia: String? = null,
    @SerialName("enriched_at") val enrichedAt: String? = null,
    @SerialName("file_path") val filePath: String? = null,
    val favorited: Boolean = false,
)

@Serializable
data class LibraryPage(
    val total: Int = 0,
    val items: List<LibraryTrack> = emptyList(),
)

@Serializable
data class LibraryStats(
    @SerialName("total_tracks") val totalTracks: Int = 0,
    @SerialName("total_duration_sec") val totalDurationSec: Double? = null,
    @SerialName("by_added_via") val byAddedVia: Map<String, Int> = emptyMap(),
    @SerialName("enriched_count") val enrichedCount: Int = 0,
)

@Serializable
data class Playlist(
    val id: Long = 0,
    val name: String = "",
    val description: String? = null,
    @SerialName("track_count") val trackCount: Int = 0,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("is_virtual") val isVirtual: Boolean = false,
    @SerialName("cover_emoji") val coverEmoji: String? = null,
    @SerialName("cover_color") val coverColor: String? = null,
)

@Serializable
data class NpFavoriteResponse(
    // Open vocabulary: "library" and "radio"-style kinds are core-known;
    // provider plugins report their own slug. Unknown kinds get generic copy.
    val kind: String? = null,
    @SerialName("already_favorited") val alreadyFavorited: Boolean = false,
    val title: String? = null,
    // Optional server-supplied toast copy — preferred when present.
    val message: String? = null,
)
