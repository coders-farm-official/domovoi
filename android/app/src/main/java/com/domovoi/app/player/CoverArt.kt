package com.domovoi.app.player

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.Interceptor
import okhttp3.Response
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
 * A track's cover can change inside its file (V058: Domovoi writes the Cover
 * Art Archive's front cover into it, and Undo puts the original back), with
 * the URL unchanged. The server's answer carries a strong ETag built from
 * the file's path, mtime and size, but also `max-age=86400`, which let
 * Coil's disk cache show the old picture for a day without asking. So the
 * phone keeps the picture and ETag yet asks again every time it draws a
 * cover from disk ([revalidate]: a 304 costs no picture bytes), and a
 * return to the screen starts a new [epoch] so covers kept in memory are
 * asked about too, and a remembered "no cover" is forgotten.
 *
 * Pure Kotlin: the URL rules, the miss memo, the revalidation and the
 * epoch are pinned by CoverArtTest.
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

    private val coverRoute = Regex("^/api/music/library/[0-9]+/cover$")

    /** Whether [encodedPath] is a library track's cover route. */
    fun isCoverRoute(encodedPath: String): Boolean = coverRoute.matches(encodedPath)

    /**
     * On Coil's client: a cover answer (a 200 or a 304) is kept but marked
     * `no-cache`, so the disk copy is used only after the server confirms
     * its ETag still matches. Every other answer passes untouched.
     */
    val revalidate: Interceptor = Interceptor { chain ->
        val response: Response = chain.proceed(chain.request())
        if (!isCoverRoute(chain.request().url.encodedPath)) {
            response
        } else {
            response.newBuilder()
                .removeHeader("Expires")
                .header("Cache-Control", "no-cache")
                .build()
        }
    }

    private val _epoch = MutableStateFlow(0)

    /** Bumped when the app comes back to the screen: covers drawn after it
     *  are asked about again ([memoryKey]). */
    val epoch: StateFlow<Int> = _epoch

    @Volatile private var away = false

    /** The app left the screen (not a rotation). */
    fun appBackgrounded() {
        away = true
    }

    /** The app is on screen again: after a spell away, a new epoch, and
     *  every remembered "no cover" is asked about again (a song can have
     *  gained one meanwhile). A first start or a rotation changes nothing. */
    fun appForegrounded() {
        if (!away) return
        away = false
        missing.clear()
        _epoch.value = _epoch.value + 1
    }

    /** Coil's memory-cache key for [model] in [epoch]: a new epoch misses
     *  memory, so the picture comes from disk, which [revalidate] checks. */
    fun memoryKey(model: String, epoch: Int): String = "$model#cover-epoch=$epoch"
}
