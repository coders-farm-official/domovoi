package com.domovoi.app.player

import java.util.concurrent.ConcurrentHashMap

/**
 * Library cover art: `GET /api/music/library/{id}/cover` on the web backend
 * (web/backend/api/music.py `track_cover`) answers with the picture built
 * into the track's file, else its album folder's cover / folder / front /
 * album image, read on request — and 404 when there is neither. The route
 * is open, like the library listing, but the app fetches it through its one
 * OkHttpClient anyway (Coil and the media session's bitmap loader both), so
 * the cleartext policy and the device token apply as for every other call.
 *
 * Pure Kotlin: the URL rules and the miss memo are pinned by CoverArtTest.
 */
object CoverArt {

    /** The server-relative path of a library track's cover. */
    fun libraryPath(trackId: Long): String = "/api/music/library/$trackId/cover"

    /**
     * What to hand an image loader for a cover path: a server path resolved
     * against the active server ([absolute] — ApiClient.absolute); an
     * on-device content:// URI or a full http(s) URL as it is; null for none.
     * (Prefixing a content:// URI with the server, as the mini player and
     * the queue sheet used to, turned a phone song's art into a dead URL.)
     */
    fun model(coverPath: String?, absolute: (String) -> String): String? = when {
        coverPath.isNullOrBlank() -> null
        coverPath.startsWith("/") -> absolute(coverPath)
        else -> coverPath
    }

    // Covers the server said it doesn't have, this run. Coil keeps no record
    // of a failure, so without this every row scrolled back into view would
    // ask for the same missing picture again.
    private val missing: MutableSet<String> = ConcurrentHashMap.newKeySet()

    /** Whether [url] already came back "no cover" this run. */
    fun isMissing(url: String): Boolean = url in missing

    /**
     * A load of [url] failed with HTTP [status] (null: no answer at all).
     * Only a 404 — "this track has no cover" — is remembered; a network
     * blip or a server error is tried again next time the cover is drawn.
     */
    fun noteFailure(url: String, status: Int?) {
        if (status == 404) missing += url
    }

    /** Forget every remembered miss (tests). */
    fun forgetMisses() = missing.clear()
}
