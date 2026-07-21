package com.domovoi.app.ui.screens.news

import kotlinx.serialization.Serializable

// Mirrors the rows served by /api/people + /api/news/* (web/static/news.jsx).

@Serializable
internal data class NewsPerson(
    val id: Long = 0,
    val name: String? = null,
    val last_seen_at: String? = null,
)

@Serializable
internal data class NewsTopic(
    val id: Long = 0,
    val kind: String? = null,
    val topic: String? = null,
    val feed_count: Int = 0,
)

@Serializable
internal data class NewsFeedRow(
    val id: Long = 0,
    val title: String? = null,
    val source: String? = null,
    val url: String? = null,
    val discovered_via: String? = null,
    val scope: String? = null,
    val valid: Boolean = false,
)

@Serializable
internal data class NewsItemRow(
    val id: Long = 0,
    val title: String? = null,
    val summary: String? = null,
    val url: String? = null,
    val topic: String? = null,
    val source: String? = null,
    val published_at: String? = null,
    val fetched_at: String? = null,
    val favorited: Boolean = false,
    val read_at: String? = null,
)

@Serializable
internal data class NewsBriefing(
    val briefing: String? = null,
    val generated_at: String? = null,
)
