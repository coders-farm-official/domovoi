package com.domovoi.app.player

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
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
 * A server from V059 on numbers each track's cover (`cover_v`, moved on
 * every change) and hands out `cover_url` with it as `?v=`. A versioned
 * cover is kept and shown without asking (a change is a new URL), and the
 * live `library.cover.changed` event names the tracks whose version moved
 * ([versions]), so a row read earlier, the player and the media session
 * all move to the new picture. The ETag check above stays for a cover with
 * no version (an older server).
 *
 * Pure Kotlin: the URL rules, the miss memo, the revalidation, the epoch
 * and the versions are pinned by CoverArtTest and CoverVersionTest.
 */
object CoverArt {

    /**
     * The server-relative path of a library track's cover. With a version
     * (V059: the row's `cover_v`, or a newer one `library.cover.changed`
     * named, [versions]) it is the server's own `cover_url`,
     * `/api/music/library/{id}/cover?v={cover_v}` — the web's
     * libraryCoverUrl (components.jsx): the larger of the two, and `?v=`
     * whenever the row carried a version or a newer one is known. Without
     * either (an older server, a row that never had one) it is the bare
     * route, checked by its ETag ([revalidate], [epoch]).
     */
    fun libraryPath(trackId: Long, coverV: Int? = null): String {
        val known = knownVersion(trackId)
        val base = "/api/music/library/$trackId/cover"
        if (coverV == null && known == null) return base
        return "$base?v=${maxOf(coverV ?: 0, known ?: 0)}"
    }

    // ── The cover's version (V059) ──────────────────────────────────────

    /** The live event that names tracks whose cover just changed. */
    const val COVER_CHANGED_EVENT = "library.cover.changed"

    /**
     * The newest cover versions [COVER_CHANGED_EVENT] told this run, for
     * [server] (the server URL the event's socket was opened to): track id
     * to `cover_v`. Another server's track ids mean other songs, so a switch
     * starts it empty.
     */
    data class Versions(val server: String = "", val byTrack: Map<Long, Int> = emptyMap())

    private val _versions = MutableStateFlow(Versions())

    /** What every cover drawn now is held against ([withKnownVersion]). */
    val versions: StateFlow<Versions> = _versions

    /** The newest known version of [trackId]'s cover, or null. */
    fun knownVersion(trackId: Long): Int? = _versions.value.byTrack[trackId]

    /**
     * `{"tracks": [{"id", "cover_v", "cover_url"}, ...]}` (the event's
     * `data`, web/backend/realtime.py `_snapshot_library_covers`) as track
     * id to `cover_v`; anything malformed is left out.
     */
    fun parseCoverChanged(data: JsonElement?): Map<Long, Int> {
        val tracks = (data as? JsonObject)?.get("tracks") as? JsonArray ?: return emptyMap()
        val out = LinkedHashMap<Long, Int>()
        for (t in tracks) {
            val o = t as? JsonObject ?: continue
            val id = (o["id"] as? JsonPrimitive)?.longOrNull ?: continue
            val v = (o["cover_v"] as? JsonPrimitive)?.intOrNull ?: continue
            out[id] = maxOf(v, out[id] ?: 0)
        }
        return out
    }

    /**
     * A [COVER_CHANGED_EVENT] from the socket opened to [eventServer], heard
     * while the app talks to [activeServer]: an event from a server the app
     * has left is dropped, and a switch forgets the old server's versions.
     * Returns the tracks whose known version moved up; a version already
     * known (the event repeats the 50 newest every time) changes nothing.
     */
    @Synchronized
    fun onCoverChanged(eventServer: String, activeServer: String, data: JsonElement?): Set<Long> {
        var now = _versions.value
        if (now.server != activeServer) now = Versions(activeServer)
        if (eventServer != activeServer) {
            _versions.value = now
            return emptySet()
        }
        val moved = LinkedHashSet<Long>()
        val next = HashMap(now.byTrack)
        for ((id, v) in parseCoverChanged(data)) {
            val had = next[id]
            if (had == null || v > had) {
                next[id] = v
                moved += id
            }
        }
        _versions.value = if (moved.isEmpty()) now else now.copy(byTrack = next)
        return moved
    }

    /** The app now talks to [server]: another server's versions are forgotten. */
    @Synchronized
    fun serverIs(server: String) {
        if (_versions.value.server != server) _versions.value = Versions(server)
    }

    /** Forget every known version (tests). */
    fun forgetVersions() {
        _versions.value = Versions()
    }

    private val versionedCover = Regex("^(.*/api/music/library/([0-9]+)/cover)(?:\\?v=([0-9]+))?$")

    /**
     * [model] (an absolute cover URL or a server path) with the newest
     * version in [known], when that is newer than the one it carries — so a
     * row read before a cover changed, or the player's item, draws the new
     * picture. Only a cover of [known]'s server is touched (or a bare
     * server path); everything else comes back as it is.
     */
    fun withKnownVersion(model: String?, known: Versions): String? {
        if (model == null || known.byTrack.isEmpty()) return model
        if (!model.startsWith("/") && (known.server.isBlank() || !model.startsWith(known.server))) return model
        val m = versionedCover.matchEntire(model) ?: return model
        val id = m.groupValues[2].toLongOrNull() ?: return model
        val v = known.byTrack[id] ?: return model
        val had = m.groupValues[3].toIntOrNull()
        return if (had != null && had >= v) model else "${m.groupValues[1]}?v=$v"
    }

    /**
     * A phone-queue [item] with its cover moved to the newest known version,
     * or null when it is not a library song or its cover is already that.
     * The player swaps it in, so the notification and the lock screen load
     * the new picture (Media3's bitmap loader keeps the last one by URI).
     */
    fun refreshedItem(item: PlayItem, known: Versions = _versions.value): PlayItem? {
        if (item.kind != PlayKind.Library) return null
        val path = item.coverPath ?: return null
        val next = withKnownVersion(path, known)
        return if (next == null || next == path) null else item.copy(coverPath = next)
    }

    /** Whether [encodedQuery] (a URL's query) carries a cover version. */
    fun isVersioned(encodedQuery: String?): Boolean =
        encodedQuery?.split('&')?.any { it.startsWith("v=") } == true

    /** Whether [model] is a versioned cover URL ([libraryPath] with `?v=`). */
    fun isVersionedModel(model: String): Boolean =
        versionedCover.matchEntire(model)?.groupValues?.get(3)?.isNotEmpty() == true

    /** The same cover route whatever its version: the track part of [a] and [b]. */
    fun sameCover(a: String, b: String): Boolean =
        a.substringBefore('#').substringBefore('?') == b.substringBefore('#').substringBefore('?')

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
     * its ETag still matches. A versioned cover (`?v=`, V059) is left as the
     * server sent it: a new picture comes with a new version, so the kept
     * one is shown without asking. Every other answer passes untouched.
     */
    val revalidate: Interceptor = Interceptor { chain ->
        val response: Response = chain.proceed(chain.request())
        val url = chain.request().url
        if (!isCoverRoute(url.encodedPath) || isVersioned(url.encodedQuery)) {
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
     *  memory, so the picture comes from disk, which [revalidate] checks.
     *  A versioned cover is its URL alone: the version is the picture, so a
     *  known one is drawn from memory whatever the epoch, and a new version
     *  is a new key. */
    fun memoryKey(model: String, epoch: Int): String =
        if (isVersionedModel(model)) model else "$model#cover-epoch=$epoch"
}
