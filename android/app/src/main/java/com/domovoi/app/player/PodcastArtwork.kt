package com.domovoi.app.player

/**
 * Podcast artwork on the phone (fix B11). The server fetches each show's
 * artwork itself and serves it — `/api/podcasts/subscriptions/{id}/artwork`
 * for a subscription, `/api/podcasts/discover/artwork/{key}` for a search
 * result — so the `artwork` field the API returns is a server path or null,
 * never the publisher's URL. The phone loads only such a path, from the
 * active server; anything else (an absolute http(s) URL from an older
 * server, a protocol-relative `//host/…`) draws the placeholder, so the
 * phone never contacts a publisher on its own.
 *
 * Pure Kotlin: pinned by PodcastArtworkTest.
 */
object PodcastArtwork {

    /** The server path to load for a podcast artwork value, or null. */
    fun path(url: String?): String? {
        val p = url?.trim() ?: return null
        return p.takeIf { it.startsWith("/api/") }
    }

    /** What to hand an image loader: the path resolved against the active
     *  server ([absolute] — ApiClient.absolute), or null for the placeholder. */
    fun model(url: String?, absolute: (String) -> String): String? = path(url)?.let(absolute)
}
