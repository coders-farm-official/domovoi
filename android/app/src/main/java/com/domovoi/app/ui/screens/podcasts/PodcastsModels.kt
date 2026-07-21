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
    val episode_count: Int = 0,
    val downloaded_count: Int = 0,
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
)

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
