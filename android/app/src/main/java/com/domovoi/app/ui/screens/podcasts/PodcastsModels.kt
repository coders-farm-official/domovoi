package com.domovoi.app.ui.screens.podcasts

import kotlinx.serialization.Serializable

// Mirrors the rows served by /api/podcasts/* (web/static/podcasts.jsx).
// Nullable + defaults everywhere — the backend evolves.

@Serializable
internal data class PodcastSubscription(
    val id: Long = 0,
    val title: String? = null,
    val feed_url: String? = null,
    val author: String? = null,
    val artwork: String? = null,
    val episode_count: Int? = null,
    val downloaded_count: Int = 0,
    val description: String? = null,      // feed HTML: shown as plain text only
    val last_polled_at: String? = null,
)

@Serializable
internal data class EpisodeChapterRow(
    val title: String? = null,
    val start_sec: Double = 0.0,
)

@Serializable
internal data class PodcastEpisode(
    val id: Long = 0,
    val title: String? = null,
    val duration_sec: Double? = null,
    val has_file: Boolean = false,
    val download_status: String? = null,
    val file_ext: String? = null,   // ".mp3" etc — names save-to-device files
    val chapters: List<EpisodeChapterRow> = emptyList(),
    val published_at: String? = null,
    val description: String? = null,      // feed HTML: shown as plain text only
    // The listening-as person's place (sent with device_id + person_id).
    // A row from a server older than 2026-11-12 has no such key, which is
    // not the same as null ("not started"): the default marks it absent.
    val position_sec: Double? = POSITION_NOT_SENT,
) {
    /** False for an older server's row (no position_sec key at all). */
    val positionKnown: Boolean get() = position_sec?.isNaN() != true

    companion object {
        const val POSITION_NOT_SENT: Double = Double.NaN
    }
}

@Serializable
internal data class DiscoverRow(
    val title: String? = null,
    val author: String? = null,
    val artwork: String? = null,
    val feed_url: String? = null,
)

@Serializable
internal data class PersonRow(
    val id: Long = 0,
    val name: String? = null,
)
